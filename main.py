"""
PolyFollow v3 — Real-time architecture

Three parallel systems:
  1. TradeMonitor (every 2 min) — detects smart money entries in the trade stream
  2. PriceWatcher (WebSocket)   — monitors open positions in real-time
  3. Hourly scan                — full market sweep via /holders endpoint
"""

import time
import logging
import schedule
import threading
from datetime import datetime
from config import SCAN_INTERVAL_MINUTES, WALLET_REFRESH_HOURS, MAX_TRADES_PER_CYCLE, TAKER_FEE

from wallet_scorer import get_top_wallets, get_smart_money_set
from scanner import fetch_active_markets, build_market_consensus
from signal_engine import filter_signals, deduplicate_signals
from trade_monitor import TradeMonitor
from price_watcher import PriceWatcher
from paper_trader import (
    load_state, save_state, open_position,
    close_position, get_portfolio_summary
)
from notifier import (
    notify_trade_opened, notify_trade_closed,
    notify_scan_complete, notify_error, notify_startup
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/polyfollow.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Global state
_state_lock = threading.Lock()
_top_wallets = []
_wallets_loaded_at = None
_trade_monitor: TradeMonitor = None
_price_watcher: PriceWatcher = None


# ─── Wallet Management ────────────────────────────────────────────────────────

def get_wallets(force: bool = False):
    global _top_wallets, _wallets_loaded_at
    now = datetime.now()
    stale = (
        _wallets_loaded_at is None or
        (now - _wallets_loaded_at).total_seconds() > WALLET_REFRESH_HOURS * 3600
    )
    if force or stale or not _top_wallets:
        logger.info("Refreshing top wallet list...")
        _top_wallets = get_top_wallets(force_refresh=True)
        _wallets_loaded_at = now

        # Update trade monitor with new wallet set
        if _trade_monitor:
            _trade_monitor.update_smart_money(
                get_smart_money_set(_top_wallets),
                {w["address"]: w["score"] for w in _top_wallets}
            )

    return _top_wallets


# ─── Signal Processing ────────────────────────────────────────────────────────

def process_signal(signal: Dict):
    """
    Handle a signal from either trade monitor or hourly scan.
    Thread-safe — can be called from any thread.
    """
    from signal_engine import is_market_valid, calculate_edge

    # Validate market
    valid, reason = is_market_valid(signal)
    if not valid:
        logger.debug(f"Signal rejected ({reason}): {signal['question'][:50]}")
        return

    # Calculate edge
    edge = calculate_edge(
        signal["consensus_pct"],
        signal["dominant_price"],
        signal["dominant_side"]
    )
    if edge < 0.03:
        logger.debug(f"Signal rejected (low edge {edge:.1%}): {signal['question'][:50]}")
        return

    signal["edge"] = edge
    signal["signal_strength"] = round(signal["consensus_pct"] * (1 + max(edge, 0)), 4)

    with _state_lock:
        state = load_state()

        # Check we don't already hold this market
        existing = {p.get("condition_id") or p["market_id"] for p in state["positions"]}
        if (signal.get("condition_id") or signal["market_id"]) in existing:
            logger.debug(f"Signal skipped (already in position): {signal['question'][:50]}")
            return

        success, msg = open_position(state, signal)
        if success:
            # Subscribe to price updates for this position
            pos = state["positions"][-1]
            token_id = pos.get("token_id", "")
            if token_id and _price_watcher:
                _price_watcher.subscribe(token_id, pos.get("condition_id", ""))

            save_state(state)
            notify_trade_opened(pos, signal)
            source = signal.get("source", "scan")
            logger.info(f"[{source.upper()}] {msg}")
        else:
            logger.info(f"Skipped: {msg}")


def _on_trade_monitor_signal(signal: Dict):
    """Callback from TradeMonitor — runs in monitor thread."""
    process_signal(signal)


# ─── Price Watcher Callback ───────────────────────────────────────────────────

def _on_price_update(token_id: str, price: float):
    """
    Called by PriceWatcher when price changes for a monitored token.
    Checks stop-loss and take-profit in real-time.
    """
    with _state_lock:
        state = load_state()
        changed = False

        for pos in state["positions"][:]:
            if pos.get("token_id") != token_id:
                continue

            pos["current_price"] = price

            # Fee-adjusted P&L
            exit_fee = pos["shares"] * price * TAKER_FEE
            net_value = pos["shares"] * price - exit_fee
            net_cost = pos.get("net_cost") or pos.get("cost") or 0
            pos["pnl"] = round(net_value - net_cost, 2)
            pos["pnl_pct"] = round((price - pos["entry_price"]) / pos["entry_price"], 4)

            close_reason = None
            if price >= 0.98:
                close_reason = "RESOLVED_WIN"
            elif price <= 0.02:
                close_reason = "RESOLVED_LOSS"
            elif price <= pos["stop_loss"]:
                close_reason = "STOP_LOSS"
            elif price >= pos["take_profit"]:
                close_reason = "TAKE_PROFIT"

            if close_reason:
                pos["current_price"] = price
                closed = close_position(state, pos, close_reason)
                notify_trade_closed(closed)

                # Unsubscribe from price updates
                if _price_watcher:
                    _price_watcher.unsubscribe(token_id)

                logger.info(
                    f"[REALTIME] Closed: {closed['question'][:50]} | "
                    f"P&L: ${closed['pnl']:+.2f} ({closed['pnl_pct']:+.1%}) | "
                    f"Reason: {close_reason}"
                )
                changed = True
                break

        if changed:
            save_state(state)


# ─── Hourly Scan ─────────────────────────────────────────────────────────────

def run_hourly_scan():
    """Full market sweep — runs every hour as a safety net."""
    logger.info("=" * 60)
    logger.info(f"Hourly scan at {datetime.now().isoformat()}")

    try:
        wallets = get_wallets()
        if not wallets:
            logger.warning("No wallets, skipping scan")
            return

        markets = fetch_active_markets(limit=300)
        if not markets:
            return

        consensus = build_market_consensus(wallets, markets)
        signals = filter_signals(consensus)

        with _state_lock:
            state = load_state()
            signals = deduplicate_signals(signals, state["positions"])

        opened_count = 0
        for signal in signals[:MAX_TRADES_PER_CYCLE]:
            signal["source"] = "hourly_scan"
            process_signal(signal)
            opened_count += 1

        with _state_lock:
            state = load_state()
            portfolio = get_portfolio_summary(state)

        notify_scan_complete(signals, opened_count, portfolio)
        logger.info(
            f"Hourly scan complete. Portfolio: ${portfolio['total_value']:.2f} "
            f"({portfolio['total_pnl']:+.2f}) | Open: {portfolio['open_positions']} | Signals: {len(signals)}"
        )

    except Exception as e:
        logger.error(f"Hourly scan failed: {e}", exc_info=True)
        notify_error(str(e))


# ─── Startup ─────────────────────────────────────────────────────────────────

def main():
    global _trade_monitor, _price_watcher

    logger.info("🚀 PolyFollow v3 starting up...")

    # Load wallets
    wallets = get_wallets(force=True)

    # Initial state
    state = load_state()
    portfolio = get_portfolio_summary(state)
    notify_startup(portfolio)

    # Start PriceWatcher (WebSocket)
    _price_watcher = PriceWatcher(on_price_update=_on_price_update)
    _price_watcher.start()

    # Subscribe to existing open positions
    for pos in state["positions"]:
        token_id = pos.get("token_id", "")
        if token_id:
            _price_watcher.subscribe(token_id, pos.get("condition_id", ""))
    logger.info(f"Subscribed to {len(state['positions'])} existing positions")

    # Start TradeMonitor (2-minute polling)
    _trade_monitor = TradeMonitor(
        smart_money_set=get_smart_money_set(wallets),
        wallet_score_map={w["address"]: w["score"] for w in wallets},
        on_signal=_on_trade_monitor_signal,
        consensus_threshold=0.60,
        min_votes=2
    )
    _trade_monitor.start()

    # Run initial hourly scan immediately
    run_hourly_scan()

    # Schedule recurring runs
    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_hourly_scan)
    schedule.every().day.at("06:00").do(lambda: get_wallets(force=True))

    logger.info(
        f"All systems running:\n"
        f"  • TradeMonitor: every 2 minutes\n"
        f"  • PriceWatcher: WebSocket (real-time)\n"
        f"  • Hourly scan: every {SCAN_INTERVAL_MINUTES} minutes"
    )

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
