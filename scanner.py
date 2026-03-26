import requests
import time
import logging
from typing import List, Dict
from config import (
    GAMMA_API, DATA_API, MIN_LIQUIDITY, MIN_MARKET_VOLUME
)

logger = logging.getLogger(__name__)


def fetch_active_markets(limit: int = 500) -> List[Dict]:
    """Fetch active markets from Gamma API, indexed by conditionId."""
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
                liquidity = float(m.get("liquidity") or 0)
                volume = float(m.get("volume") or 0)
                if liquidity >= MIN_LIQUIDITY and volume >= MIN_MARKET_VOLUME:
                    markets.append(m)

            if len(batch) < 100:
                break
            offset += 100
            time.sleep(0.1)

        except Exception as e:
            logger.error(f"Failed to fetch markets at offset {offset}: {e}")
            break

    logger.info(f"Found {len(markets)} qualifying active markets")
    return markets[:limit]


def fetch_wallet_positions(address: str) -> List[Dict]:
    """
    Fetch active positions for a wallet.
    Only returns positions with curPrice > 0 (market still live).
    """
    try:
        url = f"{DATA_API}/positions"
        params = {
            "user": address,
            "sizeThreshold": "1",
            "redeemable": "false",
            "limit": 500
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []
        # Only positions in live markets (curPrice > 0)
        return [p for p in data if float(p.get("curPrice") or 0) > 0]
    except Exception as e:
        logger.error(f"Positions fetch failed for {address[:8]}...: {e}")
        return []


def build_market_consensus(top_wallets: List[Dict], active_markets: List[Dict]) -> List[Dict]:
    """
    For each active market, count how many top wallets hold a position
    and what direction. Returns markets sorted by total votes + consensus.
    """
    # Index by conditionId for O(1) lookup
    market_by_condition: Dict[str, Dict] = {}
    for m in active_markets:
        cid = m.get("conditionId", "")
        if cid:
            market_by_condition[cid] = m

    logger.info(f"Scanning positions for {len(top_wallets)} wallets...")

    market_votes: Dict[str, Dict] = {}

    for wallet in top_wallets:
        address = wallet["address"]
        positions = fetch_wallet_positions(address)
        time.sleep(0.1)

        for pos in positions:
            cid = pos.get("conditionId", "")
            if not cid or cid not in market_by_condition:
                continue

            outcome = pos.get("outcome", "").upper()
            cur_price = float(pos.get("curPrice") or 0)
            size = float(pos.get("size") or 0)

            if cur_price <= 0 or size < 1:
                continue
            if "YES" not in outcome and "NO" not in outcome:
                continue

            if cid not in market_votes:
                market_votes[cid] = {
                    "market": market_by_condition[cid],
                    "yes_count": 0,
                    "no_count": 0,
                    "yes_price": 0.0,
                    "no_price": 0.0,
                    "yes_token_id": "",
                    "no_token_id": "",
                    "wallet_details": []
                }

            if "YES" in outcome:
                market_votes[cid]["yes_count"] += 1
                market_votes[cid]["yes_price"] = cur_price
                market_votes[cid]["yes_token_id"] = pos.get("asset", "")
            else:
                market_votes[cid]["no_count"] += 1
                market_votes[cid]["no_price"] = cur_price
                market_votes[cid]["no_token_id"] = pos.get("asset", "")

            market_votes[cid]["wallet_details"].append({
                "address": address[:10] + "...",
                "outcome": outcome,
                "size": round(size, 2),
                "cur_price": cur_price,
                "score": round(wallet["score"], 4)
            })

    # Build results
    results = []
    for cid, data in market_votes.items():
        yes = data["yes_count"]
        no = data["no_count"]
        total_votes = yes + no

        if total_votes < 2:
            continue

        dominant_side = "YES" if yes >= no else "NO"
        consensus_pct = max(yes, no) / total_votes
        dominant_price = data["yes_price"] if dominant_side == "YES" else data["no_price"]
        dominant_token_id = data["yes_token_id"] if dominant_side == "YES" else data["no_token_id"]

        market = data["market"]

        # Extract clobTokenIds for price fetching
        clob_tokens = market.get("clobTokenIds") or []
        if isinstance(clob_tokens, str):
            try:
                import json
                clob_tokens = json.loads(clob_tokens)
            except Exception:
                clob_tokens = []

        results.append({
            "market_id": market.get("id", cid),
            "condition_id": cid,
            "question": market.get("question", "Unknown"),
            "category": market.get("category", ""),
            "end_date": market.get("endDate") or market.get("end_date", ""),
            "liquidity": float(market.get("liquidity") or 0),
            "volume": float(market.get("volume") or 0),
            "dominant_side": dominant_side,
            "dominant_price": dominant_price,
            "dominant_token_id": dominant_token_id,
            "clob_token_ids": clob_tokens,
            "yes_count": yes,
            "no_count": no,
            "total_votes": total_votes,
            "consensus_pct": round(consensus_pct, 4),
            "wallet_details": data["wallet_details"]
        })

    results.sort(key=lambda x: (x["total_votes"], x["consensus_pct"]), reverse=True)
    logger.info(f"Found consensus data for {len(results)} markets")
    return results
