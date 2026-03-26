import requests
import logging
from typing import Dict, List
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured, skipping notification")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def notify_trade_opened(position: Dict, signal: Dict):
    msg = (
        f"🟢 <b>TRADE OPENED</b>\n\n"
        f"📋 {position['question'][:80]}\n"
        f"📌 <b>Side:</b> {position['side']}\n"
        f"💰 <b>Entry:</b> {position['entry_price']:.1%}\n"
        f"💵 <b>Size:</b> ${position['gross_cost']:.2f} (fee: ${position['entry_fee']:.2f})\n"
        f"👥 <b>Consensus:</b> {signal['consensus_pct']:.0%} ({signal['total_votes']} wallets)\n"
        f"📈 <b>Net edge:</b> {signal['edge']:.1%}\n"
        f"🛑 <b>Stop-loss:</b> {position['stop_loss']:.1%}\n"
        f"✅ <b>Take-profit:</b> {position['take_profit']:.1%}"
    )
    send_message(msg)


def notify_trade_closed(position: Dict):
    emoji = "✅" if position["pnl"] > 0 else "❌"
    reason_emoji = {
        "STOP_LOSS": "🛑",
        "TAKE_PROFIT": "🎯",
        "RESOLVED_WIN": "🏆",
        "RESOLVED_LOSS": "💀",
        "MANUAL": "👋"
    }.get(position.get("close_reason", ""), "📋")

    msg = (
        f"{emoji} <b>TRADE CLOSED</b> {reason_emoji}\n\n"
        f"📋 {position['question'][:80]}\n"
        f"📌 <b>Side:</b> {position['side']}\n"
        f"💰 <b>Entry:</b> {position['entry_price']:.1%} → <b>Exit:</b> {position['exit_price']:.1%}\n"
        f"💵 <b>P&L:</b> ${position['pnl']:+.2f} ({position['pnl_pct']:+.1%})\n"
        f"💸 <b>Total fees:</b> ${position.get('total_fees', 0):.2f}\n"
        f"🏷 <b>Reason:</b> {position.get('close_reason', 'Unknown')}"
    )
    send_message(msg)


def notify_scan_complete(signals: List[Dict], opened: int, portfolio: Dict):
    signal_lines = ""
    for s in signals[:3]:
        signal_lines += f"  • {s['question'][:50]} → {s['dominant_side']} @ {s['dominant_price']:.1%} ({s['consensus_pct']:.0%}, {s['total_votes']} wallets)\n"
    if not signal_lines:
        signal_lines = "  No new signals this cycle\n"

    pnl_emoji = "📈" if portfolio["total_pnl"] >= 0 else "📉"

    msg = (
        f"🔍 <b>SCAN COMPLETE</b>\n\n"
        f"📊 <b>Signals:</b> {len(signals)} | <b>Opened:</b> {opened}\n\n"
        f"<b>Top signals:</b>\n{signal_lines}\n"
        f"{pnl_emoji} <b>Portfolio:</b> ${portfolio['total_value']:.2f} "
        f"({portfolio['total_pnl']:+.2f} | {portfolio['total_pnl_pct']:+.1%})\n"
        f"📂 <b>Open:</b> {portfolio['open_positions']} | "
        f"🏆 <b>Win rate:</b> {portfolio['win_rate']:.0%} ({portfolio['wins']}W/{portfolio['losses']}L)"
    )
    send_message(msg)


def notify_error(error: str):
    send_message(f"⚠️ <b>PolyFollow Error</b>\n\n{error[:500]}")


def notify_startup(portfolio: Dict):
    msg = (
        f"🚀 <b>PolyFollow v2 Started</b>\n\n"
        f"💰 <b>Bankroll:</b> ${portfolio['bankroll_free']:.2f}\n"
        f"📂 <b>Open positions:</b> {portfolio['open_positions']}\n"
        f"📊 <b>Total trades:</b> {portfolio['total_trades']}\n"
        f"🏆 <b>Win rate:</b> {portfolio['win_rate']:.0%}"
    )
    send_message(msg)
