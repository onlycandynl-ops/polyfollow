import logging
from typing import List, Dict, Optional
from datetime import datetime
from config import (
    CONSENSUS_THRESHOLD, MIN_PRICE, MAX_PRICE,
    MIN_LIQUIDITY, MIN_VOLUME
)

logger = logging.getLogger(__name__)


def calculate_edge(consensus_pct: float, market_price: float, dominant_side: str) -> float:
    """
    Calculate edge: how much the smart money consensus diverges from market price.
    Higher edge = stronger signal.
    """
    # Implied probability from consensus (weighted toward extremes)
    implied_prob = 0.5 + (consensus_pct - 0.5) * 1.5
    implied_prob = max(0.01, min(0.99, implied_prob))

    if dominant_side == "YES":
        edge = implied_prob - market_price
    else:
        edge = implied_prob - market_price

    return round(edge, 4)


def is_market_valid(signal: Dict) -> tuple[bool, str]:
    """Validate market quality. Returns (valid, reason)."""
    price = signal.get("dominant_price", 0)
    liquidity = signal.get("liquidity", 0)
    volume = signal.get("volume", 0)
    end_date = signal.get("end_date") or signal.get("endDate", "")

    if price < MIN_PRICE:
        return False, f"Price too low ({price:.1%})"
    if price > MAX_PRICE:
        return False, f"Price too high ({price:.1%})"
    if liquidity < MIN_LIQUIDITY:
        return False, f"Liquidity too low (${liquidity:.0f})"
    if volume < MIN_VOLUME:
        return False, f"Volume too low (${volume:.0f})"

    # Check market not ending too soon (< 1 day)
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00")).replace(tzinfo=None)
            hours_left = (end_dt - datetime.utcnow()).total_seconds() / 3600
            if hours_left < 24:
                return False, f"Market ending too soon ({hours_left:.0f}h)"
        except Exception:
            pass

    return True, "ok"


def filter_signals(consensus_signals: List[Dict]) -> List[Dict]:
    """
    Filter consensus signals down to actionable trades.
    Applies threshold, market validity, and edge requirements.
    """
    actionable = []
    rejected = {"low_consensus": 0, "invalid_market": 0, "low_edge": 0}

    for signal in consensus_signals:
        # 1. Consensus threshold
        if signal["consensus_pct"] < CONSENSUS_THRESHOLD:
            rejected["low_consensus"] += 1
            continue

        # 2. Market validity
        valid, reason = is_market_valid(signal)
        if not valid:
            rejected["invalid_market"] += 1
            logger.debug(f"Rejected '{signal['question'][:40]}': {reason}")
            continue

        # 3. Edge calculation
        edge = calculate_edge(
            signal["consensus_pct"],
            signal["dominant_price"],
            signal["dominant_side"]
        )

        if edge < 0.05:  # Minimum 5% edge
            rejected["low_edge"] += 1
            continue

        # Build actionable signal
        actionable.append({
            **signal,
            "edge": edge,
            "signal_strength": round(signal["consensus_pct"] * (1 + edge), 4),
            "generated_at": datetime.utcnow().isoformat()
        })

    # Sort by signal strength
    actionable.sort(key=lambda x: x["signal_strength"], reverse=True)

    logger.info(
        f"Signal filter: {len(actionable)} actionable | "
        f"Rejected: {rejected['low_consensus']} low consensus, "
        f"{rejected['invalid_market']} invalid market, "
        f"{rejected['low_edge']} low edge"
    )

    return actionable


def deduplicate_signals(
    new_signals: List[Dict],
    existing_positions: List[Dict]
) -> List[Dict]:
    """Remove signals for markets we already have a position in."""
    existing_market_ids = {p["market_id"] for p in existing_positions}
    fresh = [s for s in new_signals if s["market_id"] not in existing_market_ids]
    dupes = len(new_signals) - len(fresh)
    if dupes:
        logger.info(f"Removed {dupes} duplicate signals (already in positions)")
    return fresh


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with mock data
    mock_signals = [
        {
            "market_id": "test1",
            "question": "Will X happen by March?",
            "dominant_side": "YES",
            "dominant_price": 0.35,
            "consensus_pct": 0.75,
            "liquidity": 5000,
            "volume": 10000,
            "end_date": "2026-06-01T00:00:00Z",
            "total_votes": 22,
            "yes_count": 22,
            "no_count": 0,
            "wallet_details": []
        }
    ]

    signals = filter_signals(mock_signals)
    for s in signals:
        print(f"✅ {s['question'][:50]} | {s['dominant_side']} @ {s['dominant_price']:.1%} | Edge: {s['edge']:.1%} | Consensus: {s['consensus_pct']:.1%}")
