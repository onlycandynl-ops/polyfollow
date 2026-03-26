import json
import os
import requests
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from config import (
    PAPER_BANKROLL, TRADE_SIZE_PCT, MAX_OPEN_POSITIONS,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, PAPER_STATE_FILE,
    TRADE_LOG_FILE, CLOB_API, TAKER_FEE
)

logger = logging.getLogger(__name__)


def load_state() -> Dict:
    try:
        with open(PAPER_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {
            "bankroll": PAPER_BANKROLL,
            "positions": [],
            "closed_positions": [],
            "created_at": datetime.now().isoformat(),
            "total_trades": 0,
            "wins": 0,
            "losses": 0
        }


def save_state(state: Dict):
    os.makedirs("data", exist_ok=True)
    with open(PAPER_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_current_price(token_id: str, condition_id: str) -> Optional[float]:
    """
    Fetch live price via CLOB midpoint by token_id.
    Falls back to last trade price.
    """
    if token_id:
        try:
            resp = requests.get(f"{CLOB_API}/midpoint?token_id={token_id}", timeout=8)
            if resp.status_code == 200:
                mid = resp.json().get("mid")
                if mid is not None:
                    return float(mid)
        except Exception:
            pass

        try:
            resp = requests.get(f"{CLOB_API}/last-trade-price?token_id={token_id}", timeout=8)
            if resp.status_code == 200:
                price = resp.json().get("price")
                if price is not None:
                    return float(price)
        except Exception:
            pass

    # Fallback: check if market resolved via Gamma
    if condition_id:
        try:
            resp = requests.get(
                f"https://gamma-api.polymarket.com/markets?condition_ids={condition_id}",
                timeout=8
            )
            if resp.status_code == 200:
                markets = resp.json()
                if markets:
                    m = markets[0]
                    if not m.get("active") or m.get("closed"):
                        winner = (m.get("winnerOutcome") or "").upper()
                        token_outcome = m.get("outcomes", ["YES", "NO"])
                        # Check if this token won
                        if winner:
                            # outcomeIndex 0 = YES, 1 = NO
                            return 1.0 if "YES" in winner else 0.0
        except Exception:
            pass

    return None


def open_position(state: Dict, signal: Dict) -> Tuple[bool, str]:
    """Open a paper trade, deducting entry fee from cost."""
    if len(state["positions"]) >= MAX_OPEN_POSITIONS:
        return False, f"Max positions reached ({MAX_OPEN_POSITIONS})"

    gross_size = round(state["bankroll"] * TRADE_SIZE_PCT, 2)
    if gross_size < 1.0:
        return False, "Bankroll too low to trade"

    entry_price = signal["dominant_price"]
    if not entry_price or entry_price <= 0:
        return False, "Invalid entry price"

    # Entry fee reduces effective position size
    entry_fee = round(gross_size * TAKER_FEE, 4)
    net_cost = round(gross_size - entry_fee, 2)
    shares = round(net_cost / entry_price, 2)

    position = {
        "id": f"{signal.get('condition_id', signal['market_id'])}_{signal['dominant_side']}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "market_id": signal["market_id"],
        "condition_id": signal.get("condition_id", ""),
        "token_id": signal.get("dominant_token_id", ""),
        "question": signal["question"],
        "side": signal["dominant_side"],
        "entry_price": entry_price,
        "shares": shares,
        "gross_cost": gross_size,
        "entry_fee": entry_fee,
        "net_cost": net_cost,
        "stop_loss": round(entry_price * (1 + STOP_LOSS_PCT), 4),
        "take_profit": round(min(entry_price * (1 + TAKE_PROFIT_PCT), 0.95), 4),
        "consensus_pct": signal["consensus_pct"],
        "edge": signal["edge"],
        "wallet_count": signal["total_votes"],
        "opened_at": datetime.now().isoformat(),
        "current_price": entry_price,
        "pnl": 0.0,
        "pnl_pct": 0.0
    }

    state["positions"].append(position)
    state["bankroll"] = round(state["bankroll"] - gross_size, 2)
    state["total_trades"] += 1

    log_trade(position, "OPEN")
    return True, f"Opened {signal['dominant_side']} on '{signal['question'][:50]}' @ {entry_price:.1%} | Size: ${gross_size:.2f} (fee: ${entry_fee:.2f})"


def update_positions(state: Dict) -> List[Dict]:
    """Update positions, check stop-loss/take-profit/resolution."""
    closed_this_cycle = []

    for pos in state["positions"][:]:
        current_price = fetch_current_price(
            pos.get("token_id", ""),
            pos.get("condition_id", "")
        )
        if current_price is None:
            continue

        pos["current_price"] = current_price

        # Calculate P&L including exit fee
        exit_fee = round(pos["shares"] * current_price * TAKER_FEE, 4)
        gross_value = pos["shares"] * current_price
        net_value = gross_value - exit_fee
        pos["pnl"] = round(net_value - pos["net_cost"], 2)
        pos["pnl_pct"] = round((current_price - pos["entry_price"]) / pos["entry_price"], 4)

        close_reason = None
        if current_price >= 0.98:
            close_reason = "RESOLVED_WIN"
        elif current_price <= 0.02:
            close_reason = "RESOLVED_LOSS"
        elif current_price <= pos["stop_loss"]:
            close_reason = "STOP_LOSS"
        elif current_price >= pos["take_profit"]:
            close_reason = "TAKE_PROFIT"

        if close_reason:
            closed = close_position(state, pos, close_reason)
            closed_this_cycle.append(closed)

    return closed_this_cycle


def close_position(state: Dict, position: Dict, reason: str) -> Dict:
    """Close a position, deducting exit fee."""
    exit_price = position["current_price"]
    exit_fee = round(position["shares"] * exit_price * TAKER_FEE, 4)
    gross_exit_value = round(position["shares"] * exit_price, 2)
    net_exit_value = round(gross_exit_value - exit_fee, 2)
    pnl = round(net_exit_value - position["net_cost"], 2)
    pnl_pct = round((exit_price - position["entry_price"]) / position["entry_price"], 4)
    total_fees = round(position["entry_fee"] + exit_fee, 4)

    closed = {
        **position,
        "exit_price": exit_price,
        "exit_fee": exit_fee,
        "gross_exit_value": gross_exit_value,
        "net_exit_value": net_exit_value,
        "total_fees": total_fees,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "close_reason": reason,
        "closed_at": datetime.now().isoformat()
    }

    state["positions"].remove(position)
    state["closed_positions"].append(closed)
    state["bankroll"] = round(state["bankroll"] + net_exit_value, 2)

    if pnl > 0:
        state["wins"] += 1
    else:
        state["losses"] += 1

    log_trade(closed, "CLOSE")
    return closed


def get_portfolio_summary(state: Dict) -> Dict:
    open_value = sum(
        p["shares"] * p.get("current_price", p["entry_price"])
        for p in state["positions"]
    )
    total_value = state["bankroll"] + open_value
    total_pnl = round(total_value - PAPER_BANKROLL, 2)
    total_pnl_pct = round(total_pnl / PAPER_BANKROLL, 4)
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
    print(f"  Free bankroll:   ${summary['bankroll_free']:.2f}")
    print(f"  Open positions:  {summary['open_positions']} (${summary['open_positions_value']:.2f})")
    print(f"  Total value:     ${summary['total_value']:.2f}")
    print(f"  P&L:             ${summary['total_pnl']:.2f} ({summary['total_pnl_pct']:.1%})")
    print(f"  Win rate:        {summary['win_rate']:.1%} ({summary['wins']}W/{summary['losses']}L)")
