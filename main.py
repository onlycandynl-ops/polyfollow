import time
import logging
import schedule
from datetime import datetime
from config import SCAN_INTERVAL_MINUTES, WALLET_REFRESH_HOURS, MAX_TRADES_PER_CYCLE

from wallet_scorer import get_top_wallets
from scanner import fetch_active_markets, build_market_consensus
from signal_engine import filter_signals, deduplicate_signals
from paper_trader import (
    load_state, save_state, open_position,
    update_positions, get_portfolio_summary
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

_top_wallets = []
_wallets_loaded_at = None


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
    return _top_wallets


def run_cycle():
    logger.info("=" * 60)
    logger.info(f"Scan cycle at {datetime.now().isoformat()}")

    try:
        state = load_state()

        closed = update_positions(state)
        for pos in closed:
            notify_trade_closed(pos)
            logger.info(
                f"Closed: {pos['question'][:50]} | "
                f"P&L: ${pos['pnl']:+.2f} ({pos['pnl_pct']:+.1%}) | "
                f"Fees: ${pos.get('total_fees', 0):.2f} | "
                f"Reason: {pos['close_reason']}"
            )

        wallets = get_wallets()
        if not wallets:
            logger.warning("No qualifying wallets found, skipping scan")
            save_state(state)
            return

        markets = fetch_active_markets(limit=300)
        if not markets:
            logger.warning("No active markets found")
            save_state(state)
            return

        consensus = build_market_consensus(wallets, markets)
        signals = filter_signals(consensus)
        signals = deduplicate_signals(signals, state["positions"])

        opened_count = 0
        for signal in signals[:MAX_TRADES_PER_CYCLE]:
            success, msg = open_position(state, signal)
            if success:
                opened_count += 1
                notify_trade_opened(state["positions"][-1], signal)
                logger.info(f"Opened: {msg}")
            else:
                logger.info(f"Skipped: {msg}")

        save_state(state)

        portfolio = get_portfolio_summary(state)
        notify_scan_complete(signals, opened_count, portfolio)

        logger.info(
            f"Cycle complete. Portfolio: ${portfolio['total_value']:.2f} "
            f"({portfolio['total_pnl']:+.2f}) | "
            f"Open: {portfolio['open_positions']} | "
            f"Signals: {len(signals)}"
        )

    except Exception as e:
        logger.error(f"Cycle failed: {e}", exc_info=True)
        notify_error(str(e))


def main():
    logger.info("🚀 PolyFollow v2 starting up...")

    state = load_state()
    portfolio = get_portfolio_summary(state)
    notify_startup(portfolio)

    get_wallets(force=True)
    run_cycle()

    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_cycle)
    schedule.every().day.at("06:00").do(lambda: get_wallets(force=True))

    logger.info(f"Scheduled: scan every {SCAN_INTERVAL_MINUTES}min | wallet refresh every {WALLET_REFRESH_HOURS}h")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
