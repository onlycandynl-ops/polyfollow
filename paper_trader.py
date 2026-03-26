import json
import os
import requests
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from config import (
    PAPER_BANKROLL, TRADE_SIZE_PCT, MAX_OPEN_POSITIONS,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, PAPER_STATE_FILE,
    TRADE_LOG_FILE, DATA_API, CLOB_API
)

logger = logging.getLogger(__name__)


def load_state() -> Dict:
    """Load paper trading state from disk."""
    try:
        with open(PAPER_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {
            "bankroll": PAPER_BANKROLL,
            "positions": [],
            "closed_positions": [],
            "created_at": datetime.utcnow().isoformat(),
            "total_trades": 0,
            "wins": 0,
            "losses": 0
        }


def save_state(state: Dict):
    """Persist paper trading state to disk."""
    os.makedirs("data", exist_ok=True)
    with open(PAPER_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_current_price(market_id: str, side: str) -> Optional[float]:
    """Fetch live market price from Polymarket."""
    try:
        url = f"{CLOB_API}/markets/{market_id}"
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        tokens = data.get("tokens", [])
        for token in tokens:
            if isinstance(token, dict):
                outcome = token.get("outcome", "").upper()
                if outcome == side.upper():
                    price = token.get("price")
                    if price is not None:
                        return float(price)

        # Fallback to top-level fields
        if side.upper() == "YES":
            return float(data.get("bestBid") or data.get("lastTradePrice") or 0)
        else:
            bid = float(data.get("bestBid") or data.get("lastTradePrice") or 0)
            return 1.0 - bid if bid else None

    except Exception as e:
        logger.error(f"Failed to fetch price for {market_id}: {e}")
        return None


def open_position(state: Dict, signal: Dict) -> Tuple[bool, str]:
    """
    Open a new paper trade position.
    Returns (success, message).
    """
    if len(state["positions"]) >= MAX_OPEN_POSITIONS:
        return False, f"Max positions reached ({MAX_OPEN_POSITIONS})"

    trade_size = round(state["bankroll"] * TRADE_SIZE_PCT, 2)
    if trade_size < 1.0:
        return False, "Bankroll too low to trade"

    entry_price = signal["dominant_price"]
    if not entry_price or entry_price <= 0:
        return False, "Invalid entry price"

    shares = round(trade_size / entry_price, 2)

    position = {
        "id": f"{signal['market_id']}_{signal['dominant_side']}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "market_id": signal["market_id"],
        "question": signal["question"],
        "side": signal["dominant_side"],
        "entry_price": entry_price,
        "shares": shares,
        "cost": trade_size,
        "stop_loss": round(entry_price * (1 + STOP_LOSS_PCT), 4),
        "take_profit": round(entry_price * (1 + TAKE_PROFIT_PCT), 4),
        "consensus_pct": signal["consensus_pct"],
        "edge": signal["edge"],
        "wallet_count": signal["total_votes"],
        "opened_at": datetime.utcnow().isoformat(),
        "current_price": entry_price,
        "pnl": 0.0,
        "pnl_pct": 0.0
    }

    state["positions"].append(position)
    state["bankroll"] = round(state["bankroll"] - trade_size, 2)
    state["total_trades"] += 1

    log_trade(position, "OPEN")
    return True, f"Opened {signal['dominant_side']} on '{signal['question'][:50]}' @ {entry_price:.1%} | Size: ${trade_size:.2f}"


def update_positions(state: Dict) -> List[Dict]:
    """
    Update all open positions with current prices.
    Check stop-loss and take-profit triggers.
    Returns list of closed positions this cycle.
    """
    closed_this_cycle = []

    for pos in state["positions"][:]:
        current_price = fetch_current_price(pos["market_id"], pos["side"])
        if current_price is None:
            logger.warning(f"Could not fetch price for position {pos['id'][:20]}")
            continue

        pos["current_price"] = current_price
        current_value = pos["shares"] * current_price
        pos["pnl"] = round(current_value - pos["cost"], 2)
        pos["pnl_pct"] = round((current_price - pos["entry_price"]) / pos["entry_price"], 4)

        # Check exits
        close_reason = None
        if current_price <= pos["stop_loss"]:
            close_reason = "STOP_LOSS"
        elif current_price >= pos["take_profit"]:
            close_reason = "TAKE_PROFIT"

        if close_reason:
            closed = close_position(state, pos, close_reason)
            closed_this_cycle.append(closed)

    return closed_this_cycle


def close_position(state: Dict, position: Dict, reason: str) -> Dict:
    """Close a position and return funds to bankroll."""
    exit_price = position["current_price"]
    exit_value = round(position["shares"] * exit_price, 2)
    pnl = round(exit_value - position["cost"], 2)
    pnl_pct = round((exit_price - position["entry_price"]) / position["entry_price"], 4)

    closed = {
        **position,
        "exit_price": exit_price,
        "exit_value": exit_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "close_reason": reason,
        "closed_at": datetime.utcnow().isoformat()
    }

    state["positions"].remove(position)
    state["closed_positions"].append(closed)
    state["bankroll"] = round(state["bankroll"] + exit_value, 2)

    if pnl > 0:
        state["wins"] += 1
    else:
        state["losses"] += 1

    log_trade(closed, "CLOSE")
    return closed


def get_portfolio_summary(state: Dict) -> Dict:
    """Calculate portfolio stats."""
    open_value = sum(p["shares"] * p.get("current_price", p["entry_price"]) for p in state["positions"])
    total_value = state["bankroll"] + open_value
    starting = PAPER_BANKROLL
    total_pnl = round(total_value - starting, 2)
    total_pnl_pct = round(total_pnl / starting, 4)

    total = state["wins"] + state["losses"]
    win_rate = round(state["wins"] / total, 4) if total > 0 else 0

    return {
        "bankroll_free": round(state["bankroll"], 2),
        "open_positions_value": round(open_value, 2),
        "total_value": round(total_value, 2),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "total_trades": state["total_trades"],
        "wins": state["wins"],
        "losses": state["losses"],
        "win_rate": win_rate,
        "open_positions": len(state["positions"])
    }


def log_trade(trade: Dict, action: str):
    """Append trade to log file."""
    try:
        os.makedirs("data", exist_ok=True)
        logs = []
        try:
            with open(TRADE_LOG_FILE, "r") as f:
                logs = json.load(f)
        except Exception:
            pass
        logs.append({"action": action, **trade})
        with open(TRADE_LOG_FILE, "w") as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to log trade: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    state = load_state()
    summary = get_portfolio_summary(state)
    print(f"\n📊 Paper Trading Summary:")
    print(f"  Free bankroll:  ${summary['bankroll_free']:.2f}")
    print(f"  Open positions: {summary['open_positions']} (${summary['open_positions_value']:.2f})")
    print(f"  Total value:    ${summary['total_value']:.2f}")
    print(f"  P&L:            ${summary['total_pnl']:.2f} ({summary['total_pnl_pct']:.1%})")
    print(f"  Win rate:       {summary['win_rate']:.1%} ({summary['wins']}W/{summary['losses']}L)")
