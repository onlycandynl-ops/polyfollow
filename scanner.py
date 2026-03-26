import requests
import time
import logging
from typing import List, Dict
from config import (
    GAMMA_API, DATA_API, MIN_LIQUIDITY, MIN_VOLUME,
    MIN_PRICE, MAX_PRICE
)

logger = logging.getLogger(__name__)


def fetch_active_markets(limit: int = 500) -> List[Dict]:
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
        return [p for p in data if float(p.get("curPrice") or 0) > 0]
    except Exception as e:
        logger.error(f"Failed to fetch positions for {address[:8]}...: {e}")
        return []


def build_market_consensus(top_wallets: List[Dict], active_markets: List[Dict]) -> List[Dict]:
    market_by_condition = {}
    for m in active_markets:
        cid = m.get("conditionId", "")
        if cid:
            market_by_condition[cid] = m

    logger.info(f"Scanning positions for {len(top_wallets)} wallets...")
    market_votes: Dict[str, Dict] = {}

    for wallet in top_wallets:
        address = wallet["address"]
        positions = fetch_wallet_positions(address)
        time.sleep(0.15)

        for pos in positions:
            cid = pos.get("conditionId", "")
            if not cid or cid not in market_by_condition:
                continue
            market = market_by_condition[cid]
            outcome = pos.get("outcome", "").upper()
            cur_price = float(pos.get("curPrice") or 0)
            size = float(pos.get("size") or 0)
            if cur_price <= 0 or size < 1:
                continue
            if cid not in market_votes:
                market_votes[cid] = {
                    "market": market,
                    "yes_count": 0,
                    "no_count": 0,
                    "yes_price": 0.0,
                    "no_price": 0.0,
                    "wallet_details": []
                }
            if "YES" in outcome:
                market_votes[cid]["yes_count"] += 1
                market_votes[cid]["yes_price"] = cur_price
            elif "NO" in outcome:
                market_votes[cid]["no_count"] += 1
                market_votes[cid]["no_price"] = cur_price
            market_votes[cid]["wallet_details"].append({
                "address": address[:10] + "...",
                "outcome": outcome,
                "size": round(size, 2),
                "cur_price": cur_price
            })

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
        market = data["market"]
        results.append({
            "market_id": market.get("id") or cid,
            "condition_id": cid,
            "question": market.get("question", "Unknown"),
            "category": market.get("category", ""),
            "end_date": market.get("endDate") or market.get("end_date", ""),
            "liquidity": float(market.get("liquidity") or 0),
            "volume": float(market.get("volume") or 0),
            "dominant_side": dominant_side,
            "dominant_price": dominant_price,
            "yes_count": yes,
            "no_count": no,
            "total_votes": total_votes,
            "consensus_pct": round(consensus_pct, 4),
            "wallet_details": data["wallet_details"]
        })

    results.sort(key=lambda x: (x["total_votes"], x["consensus_pct"]), reverse=True)
    logger.info(f"Found consensus data for {len(results)} markets")
    return results
