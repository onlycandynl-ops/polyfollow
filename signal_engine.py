import logging
from typing import List, Dict, Tuple
from datetime import datetime
from config import (
    CONSENSUS_THRESHOLD, MIN_PRICE, MAX_PRICE,
    MIN_LIQUIDITY, MIN_MARKET_VOLUME, MIN_HOURS_LEFT,
    TAKER_FEE
)

logger = logging.getLogger(__name__)


def calculate_edge(consensus_pct: float, market_price: float, dominant_side: str) -> float:
    """
    Edge = divergence between smart money implied probability and market price,
    AFTER accounting for taker fees on both entry and exit.

    Fees eat into edge: a 2% taker fee applied at entry and exit means
    you need >4% gross edge just to break even.
    """
    implied_prob = 0.5 + (consensus_pct - 0.5) * 1.5
    implied_prob = max(0.01, min(0.99, implied_prob))

    if dominant_side == "YES":
        gross_edge = implied_prob - market_price
    else:
        implied_no = 1.0 - implied_prob
        market_no = 1.0 - market_price
        gross_edge = implied_no - market_no

    # Subtract round-trip fee cost
    fee_cost = TAKER_FEE * 2
    net_edge = gross_edge - fee_cost

    return round(net_edge, 4)


def is_market_valid(signal: Dict) -> Tuple[bool, str]:
    """Validate market quality."""
    price = signal.get("dominant_price", 0)
    liquidity = signal.get("liquidity", 0)
    volume = signal.get("volume", 0)
    end_date = signal.get("end_date") or signal.get("endDate", "")

    if price <= 0:
        return False, "No price data"
    if price < MIN_PRICE:
        return False, f"Price too low ({price:.1%})"
    if price > MAX_PRICE:
        return False, f"Price too high ({price:.1%})"
    if liquidity < MIN_LIQUIDITY:
        return False, f"Liquidity too low (${liquidity:.0f})"
    if volume < MIN_MARKET_VOLUME:
        return False, f"Volume too low (${volume:.0f})"

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00")).replace(tzinfo=None)
            hours_left = (end_dt - datetime.now()).total_seconds() / 3600
            if hours_left < MIN_HOURS_LEFT:
                return False, f"Ending too soon ({hours_left:.0f}h)"
        except Exception:
            pass

    return True, "ok"


def filter_signals(consensus_signals: List[Dict]) -> List[Dict]:
    """Filter consensus signals to actionable trades."""
    actionable = []
    rejected = {"low_consensus": 0, "invalid_market": 0, "low_edge": 0}

    for signal in consensus_signals:
        if signal["consensus_pct"] < CONSENSUS_THRESHOLD:
            rejected["low_consensus"] += 1
            continue

        valid, reason = is_market_valid(signal)
        if not valid:
            rejected["invalid_market"] += 1
            logger.debug(f"Rejected '{signal['question'][:40]}': {reason}")
            continue

        edge = calculate_edge(
            signal["consensus_pct"],
            signal["dominant_price"],
            signal["dominant_side"]
        )

        # Minimum net edge after fees
        if edge < 0.03:
            rejected["low_edge"] += 1
            continue

        actionable.append({
            **signal,
            "edge": edge,
            "signal_strength": round(signal["consensus_pct"] * (1 + max(edge, 0)), 4),
            "generated_at": datetime.now().isoformat()
        })

    actionable.sort(key=lambda x: x["signal_strength"], reverse=True)

    logger.info(
        f"Signal filter: {len(actionable)} actionable | "
        f"Rejected: {rejected['low_consensus']} low consensus, "
        f"{rejected['invalid_market']} invalid market, "
        f"{rejected['low_edge']} low edge"
    )

    return actionable


def deduplicate_signals(new_signals: List[Dict], existing_positions: List[Dict]) -> List[Dict]:
    """Remove signals for markets we already hold."""
    existing = {p.get("condition_id") or p["market_id"] for p in existing_positions}
    fresh = [s for s in new_signals if (s.get("condition_id") or s["market_id"]) not in existing]
    dupes = len(new_signals) - len(fresh)
    if dupes:
        logger.info(f"Removed {dupes} duplicate signals")
    return fresh
