import requests
import time
import json
import logging
from typing import List, Dict, Set
from config import (
    GAMMA_API, DATA_API, MIN_LIQUIDITY, MIN_MARKET_VOLUME
)

logger = logging.getLogger(__name__)


def fetch_active_markets(limit: int = 500) -> List[Dict]:
    """
    Fetch active markets sorted by 24h volume (more relevant than total).
    Filters out negativeRisk markets (different settlement mechanics).
    """
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
                "order": "volume24hr",   # 24h volume = more relevant than all-time
                "ascending": "false"
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break

            for m in batch:
                # Skip negativeRisk markets — different settlement mechanics
                if m.get("negativeRisk"):
                    continue
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

    logger.info(f"Found {len(markets)} qualifying active markets (negativeRisk filtered)")
    return markets[:limit]


def fetch_market_holders(condition_id: str, limit: int = 20) -> List[Dict]:
    """
    Fetch top holders for a market using the /holders endpoint.
    This is the efficient approach: market-centric instead of wallet-centric.
    Returns list of {proxyWallet, outcomeIndex, amount, name}.
    """
    try:
        url = f"{DATA_API}/holders"
        params = {"market": condition_id, "limit": limit}
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        # API returns list of {token, holders:[...]}
        if not isinstance(data, list):
            return []

        holders = []
        for token_data in data:
            for h in token_data.get("holders", []):
                holders.append({
                    "proxyWallet": h.get("proxyWallet", ""),
                    "outcomeIndex": h.get("outcomeIndex", 0),
                    "amount": float(h.get("amount", 0) or 0),
                    "name": h.get("name", "")
                })
        return holders

    except Exception as e:
        logger.debug(f"Holders fetch failed for {condition_id[:12]}...: {e}")
        return []


def build_market_consensus(
    top_wallets: List[Dict],
    active_markets: List[Dict]
) -> List[Dict]:
    """
    Market-centric approach: for each market, fetch top holders
    and check overlap with our smart money set.

    This is ~10x faster than the wallet-centric approach because:
    - Old: 300 wallet API calls per cycle
    - New: 1 API call per market (batched, only for markets with enough liquidity)
    """
    from wallet_scorer import get_smart_money_set
    smart_money = get_smart_money_set(top_wallets)
    wallet_score_map = {w["address"]: w["score"] for w in top_wallets}

    logger.info(f"Scanning {len(active_markets)} markets for smart money consensus...")

    results = []

    for i, market in enumerate(active_markets):
        cid = market.get("conditionId", "")
        if not cid:
            continue

        holders = fetch_market_holders(cid, limit=20)
        if not holders:
            continue

        # Check which holders are in our smart money set
        yes_wallets = []
        no_wallets = []

        for h in holders:
            addr = h.get("proxyWallet", "")
            if addr not in smart_money:
                continue
            amount = h.get("amount", 0)
            if amount < 1:
                continue

            outcome_idx = h.get("outcomeIndex", 0)
            if outcome_idx == 0:
                yes_wallets.append({"address": addr, "amount": amount, "score": wallet_score_map.get(addr, 0)})
            else:
                no_wallets.append({"address": addr, "amount": amount, "score": wallet_score_map.get(addr, 0)})

        total_votes = len(yes_wallets) + len(no_wallets)
        if total_votes < 2:
            continue

        dominant_side = "YES" if len(yes_wallets) >= len(no_wallets) else "NO"
        dominant_count = max(len(yes_wallets), len(no_wallets))
        consensus_pct = dominant_count / total_votes

        # Get price from market data
        clob_tokens = market.get("clobTokenIds") or []
        if isinstance(clob_tokens, str):
            try:
                clob_tokens = json.loads(clob_tokens)
            except Exception:
                clob_tokens = []

        # Use market's outcome prices if available
        outcomes = market.get("outcomes") or []
        outcome_prices = market.get("outcomePrices") or []

        dominant_price = 0.0
        dominant_token_id = ""

        if dominant_side == "YES" and len(clob_tokens) > 0:
            dominant_token_id = clob_tokens[0]
            if outcome_prices and len(outcome_prices) > 0:
                try:
                    dominant_price = float(outcome_prices[0])
                except Exception:
                    pass
        elif dominant_side == "NO" and len(clob_tokens) > 1:
            dominant_token_id = clob_tokens[1]
            if outcome_prices and len(outcome_prices) > 1:
                try:
                    dominant_price = float(outcome_prices[1])
                except Exception:
                    pass

        # Fallback to lastTradePrice
        if dominant_price <= 0:
            last_price = float(market.get("lastTradePrice") or 0)
            dominant_price = last_price if dominant_side == "YES" else 1.0 - last_price

        results.append({
            "market_id": market.get("id", cid),
            "condition_id": cid,
            "question": market.get("question", "Unknown"),
            "category": market.get("category", ""),
            "end_date": market.get("endDate") or market.get("end_date", ""),
            "liquidity": float(market.get("liquidity") or 0),
            "volume": float(market.get("volume") or 0),
            "volume_24hr": float(market.get("volume24hr") or 0),
            "dominant_side": dominant_side,
            "dominant_price": dominant_price,
            "dominant_token_id": dominant_token_id,
            "clob_token_ids": clob_tokens,
            "yes_count": len(yes_wallets),
            "no_count": len(no_wallets),
            "total_votes": total_votes,
            "consensus_pct": round(consensus_pct, 4),
            "wallet_details": (yes_wallets if dominant_side == "YES" else no_wallets)[:5]
        })

        # Rate limit friendly
        time.sleep(0.05)

        if (i + 1) % 50 == 0:
            logger.info(f"  Scanned {i+1}/{len(active_markets)} markets, {len(results)} with consensus so far...")

    results.sort(key=lambda x: (x["total_votes"], x["consensus_pct"]), reverse=True)
    logger.info(f"Found consensus data for {len(results)} markets")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from wallet_scorer import get_top_wallets

    wallets = get_top_wallets()
    markets = fetch_active_markets(limit=100)
    consensus = build_market_consensus(wallets, markets)

    print(f"\nTop consensus signals:")
    for s in consensus[:10]:
        print(f"  [{s['total_votes']} wallets | {s['consensus_pct']:.0%}] {s['question'][:60]}")
        print(f"  → {s['dominant_side']} @ {s['dominant_price']:.1%} | vol24h=${s['volume_24hr']:,.0f}")
        print()
