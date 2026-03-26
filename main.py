import time
import logging
import schedule
from datetime import datetime
from config import SCAN_INTERVAL_MINUTES, WALLET_REFRESH_HOURS

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

# Global wallet cache
_top_wallets = []
_wallets_loaded_at = None


def get_wallets(force: bool = False):
    global _top_wallets, _wallets_loaded_at
    now = datetime.utcnow()
    stale = (
        _wallets_loaded_at is None or
        (now - _wallets_loaded_at).total_seconds() > WALLET_REFRESH_HOURS * 3600
    )
    if force or stale or not _top_wallets:
        logger.info("Refreshing top wallet list...")
        _top_wallets = get_top_wallets(force_refresh=force)
        _wallets_loaded_at = now
    return _top_wallets


def run_cycle():
    logger.info("=" * 60)
    logger.info(f"Starting scan cycle at {datetime.utcnow().isoformat()}")

    try:
        # 1. Load state
        state = load_state()

        # 2. Update existing positions (check stop-loss / take-profit)
        closed = update_positions(state)
        for pos in closed:
            notify_trade_closed(pos)
            logger.info(f"Closed: {pos['question'][:50]} | P&L: ${pos['pnl']:+.2f} ({pos['pnl_pct']:+.1%}) | Reason: {pos['close_reason']}")

        # 3. Get top wallets
        wallets = get_wallets()
        if not wallets:
            logger.warning("No qualifying wallets found, skipping scan")
            save_state(state)
            return

        # 4. Scan active markets
        markets = fetch_active_markets(limit=200)
        if not markets:
            logger.warning("No active markets found")
            save_state(state)
            return

        # 5. Build consensus
        consensus = build_market_consensus(wallets, markets)

        # 6. Filter to actionable signals
        signals = filter_signals(consensus)

        # 7. Remove duplicates (markets already in portfolio)
        signals = deduplicate_signals(signals, state["positions"])

        # 8. Execute paper trades (max 3 per cycle to avoid overconcentration)
        opened_count = 0
        for signal in signals[:3]:
            success, msg = open_position(state, signal)
            if success:
                opened_count += 1
                notify_trade_opened(state["positions"][-1], signal)
                logger.info(f"Opened: {msg}")
            else:
                logger.info(f"Skipped: {msg}")

        # 9. Save state
        save_state(state)

        # 10. Notify summary
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
    logger.info("🚀 PolyFollow starting up...")

    # Initial startup notification
    state = load_state()
    portfolio = get_portfolio_summary(state)
    notify_startup(portfolio)

    # Run immediately on start
    run_cycle()

    # Schedule recurring runs
    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_cycle)
    # Refresh wallets daily at 6am UTC
    schedule.every().day.at("06:00").do(lambda: get_wallets(force=True))

    logger.info(f"Scheduled: scan every {SCAN_INTERVAL_MINUTES} min | wallet refresh every {WALLET_REFRESH_HOURS}h")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
