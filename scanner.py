import requests
import time
import logging
from typing import List, Dict, Tuple
from config import (
    DATA_API, GAMMA_API, MIN_LIQUIDITY, MIN_VOLUME,
    MIN_PRICE, MAX_PRICE
)

logger = logging.getLogger(__name__)


def fetch_active_markets(limit: int = 200) -> List[Dict]:
    """Fetch active markets from Gamma API."""
    markets = []
    offset = 0

    while len(markets) < limit:
        try:
            url = f"{GAMMA_API}/markets"
            params = {
                "active": "true",
                "closed": "false",
                "limit": 100,
                "offset": offset,
                "order": "volume",
                "ascending": "false"
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            batch = resp.json()

            if not batch:
                break

            for m in batch:
                # Filter by liquidity and volume
                liquidity = float(m.get("liquidity", 0) or 0)
                volume = float(m.get("volume", 0) or 0)
                if liquidity >= MIN_LIQUIDITY and volume >= MIN_VOLUME:
                    markets.append(m)

            if len(batch) < 100:
                break
            offset += 100
            time.sleep(0.1)

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            break

    logger.info(f"Found {len(markets)} qualifying active markets")
    return markets[:limit]


def fetch_wallet_positions(address: str) -> List[Dict]:
    """Fetch current open positions for a wallet."""
    try:
        url = f"{DATA_API}/positions"
        params = {"user": address, "sizeThreshold": "0.01"}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        logger.error(f"Failed to fetch positions for {address[:8]}...: {e}")
        return []


def fetch_wallet_recent_trades(address: str, limit: int = 50) -> List[Dict]:
    """Fetch most recent trades for a wallet (for signal detection)."""
    try:
        url = f"{DATA_API}/trades"
        params = {"maker": address, "limit": limit}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Failed to fetch recent trades for {address[:8]}...: {e}")
        return []


def build_market_consensus(
    top_wallets: List[Dict],
    active_markets: List[Dict]
) -> List[Dict]:
    """
    For each active market, check how many top wallets hold a position
    and which direction (YES/NO).
    Returns list of markets with consensus data.
    """
    # Build market lookup by token IDs
    market_by_token = {}
    for m in active_markets:
        tokens = m.get("tokens") or m.get("clobTokenIds") or []
        if isinstance(tokens, str):
            try:
                import json
                tokens = json.loads(tokens)
            except Exception:
                tokens = []
        for token in tokens:
            token_id = token if isinstance(token, str) else token.get("token_id", "")
            if token_id:
                market_by_token[token_id] = m

    # Aggregate positions per market
    market_votes: Dict[str, Dict] = {}  # market_id -> {yes: count, no: count, wallets: []}

    logger.info(f"Scanning positions for {len(top_wallets)} wallets...")
    for wallet in top_wallets:
        address = wallet["address"]
        positions = fetch_wallet_positions(address)
        time.sleep(0.15)

        for pos in positions:
            token_id = pos.get("asset_id") or pos.get("token_id") or pos.get("tokenId", "")
            size = float(pos.get("size", 0) or pos.get("currentSize", 0) or 0)

            if size < 1.0:  # Skip dust positions
                continue

            market = market_by_token.get(token_id)
            if not market:
                continue

            market_id = market.get("id") or market.get("conditionId", "")
            outcome = pos.get("outcome", "").upper()

            if market_id not in market_votes:
                market_votes[market_id] = {
                    "market": market,
                    "yes_count": 0,
                    "no_count": 0,
                    "yes_size": 0.0,
                    "no_size": 0.0,
                    "wallet_details": []
                }

            if "YES" in outcome:
                market_votes[market_id]["yes_count"] += 1
                market_votes[market_id]["yes_size"] += size
            elif "NO" in outcome:
                market_votes[market_id]["no_count"] += 1
                market_votes[market_id]["no_size"] += size

            market_votes[market_id]["wallet_details"].append({
                "address": address[:10] + "...",
                "outcome": outcome,
                "size": round(size, 2),
                "score": wallet["score"]
            })

    # Compute consensus scores
    results = []
    total_wallets = len(top_wallets)

    for market_id, data in market_votes.items():
        yes = data["yes_count"]
        no = data["no_count"]
        total_votes = yes + no

        if total_votes < 2:  # Need at least 2 wallets to form signal
            continue

        yes_pct = yes / total_wallets
        no_pct = no / total_wallets
        dominant_pct = max(yes_pct, no_pct)
        dominant_side = "YES" if yes >= no else "NO"

        # Get market price for dominant side
        market = data["market"]
        tokens = market.get("tokens") or []
        best_price = None
        try:
            for token in tokens:
                if isinstance(token, dict):
                    outcome = token.get("outcome", "").upper()
                    if outcome == dominant_side:
                        best_price = float(token.get("price", 0) or 0)
        except Exception:
            pass

        if best_price is None:
            # Try top-level price fields
            if dominant_side == "YES":
                best_price = float(market.get("bestBid", 0) or market.get("lastTradePrice", 0) or 0)
            else:
                best_price = 1.0 - float(market.get("bestBid", 0) or market.get("lastTradePrice", 0) or 0)

        results.append({
            "market_id": market_id,
            "question": market.get("question", "Unknown"),
            "category": market.get("category", ""),
            "end_date": market.get("endDate") or market.get("end_date", ""),
            "liquidity": float(market.get("liquidity", 0) or 0),
            "volume": float(market.get("volume", 0) or 0),
            "dominant_side": dominant_side,
            "dominant_price": best_price,
            "yes_count": yes,
            "no_count": no,
            "total_votes": total_votes,
            "consensus_pct": round(dominant_pct, 4),
            "wallet_details": data["wallet_details"]
        })

    results.sort(key=lambda x: x["consensus_pct"], reverse=True)
    logger.info(f"Found consensus data for {len(results)} markets")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from wallet_scorer import get_top_wallets

    wallets = get_top_wallets()
    markets = fetch_active_markets(limit=100)
    consensus = build_market_consensus(wallets, markets)

    print(f"\nTop consensus signals:")
    for s in consensus[:5]:
        print(f"  {s['question'][:60]}")
        print(f"  → {s['dominant_side']} @ {s['dominant_price']:.1%} | Consensus: {s['consensus_pct']:.1%} ({s['total_votes']} wallets)")
        print()
