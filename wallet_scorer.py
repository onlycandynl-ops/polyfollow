import requests
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from config import (
    DATA_API, TOP_N_WALLETS, WALLET_CATEGORIES,
    MIN_PNL, MIN_VOLUME, WALLET_TIME_PERIOD, WALLET_CACHE_FILE
)

logger = logging.getLogger(__name__)


def fetch_leaderboard(category: str, limit: int = 50) -> List[Dict]:
    """Fetch top traders from Polymarket leaderboard by category."""
    try:
        url = f"{DATA_API}/v1/leaderboard"
        params = {
            "limit": limit,
            "offset": 0,
            "timePeriod": WALLET_TIME_PERIOD,
            "orderBy": "PNL",
            "category": category
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch leaderboard [{category}]: {e}")
        return []


def score_wallet(entry: Dict) -> Optional[Dict]:
    """Score a wallet based on leaderboard PNL and volume."""
    address = entry.get("proxyWallet", "")
    pnl = float(entry.get("pnl", 0) or 0)
    vol = float(entry.get("vol", 0) or 0)

    if not address or pnl < MIN_PNL or vol < MIN_VOLUME:
        return None

    roi = pnl / vol
    score = round(roi + (pnl / 10_000_000), 6)

    return {
        "address": address,
        "username": entry.get("userName", ""),
        "score": score,
        "roi": round(roi, 4),
        "pnl": round(pnl, 2),
        "volume": round(vol, 2),
        "updated_at": datetime.now().isoformat()
    }


def get_top_wallets(force_refresh: bool = False) -> List[Dict]:
    """Get top N wallets across all categories, cached 24h."""
    if not force_refresh:
        try:
            with open(WALLET_CACHE_FILE, "r") as f:
                cache = json.load(f)
            cached_at = datetime.fromisoformat(cache.get("cached_at", "2000-01-01"))
            if (datetime.now() - cached_at).total_seconds() < 86400:
                logger.info(f"Using cached wallet list ({len(cache['wallets'])} wallets)")
                return cache["wallets"]
        except Exception:
            pass

    logger.info(f"Refreshing wallets (categories: {WALLET_CATEGORIES}, period: {WALLET_TIME_PERIOD})...")

    wallet_map: Dict[str, Dict] = {}
    for category in WALLET_CATEGORIES:
        for entry in fetch_leaderboard(category, limit=50):
            wallet = score_wallet(entry)
            if not wallet:
                continue
            addr = wallet["address"]
            if addr not in wallet_map or wallet_map[addr]["score"] < wallet["score"]:
                wallet_map[addr] = wallet

    top = sorted(wallet_map.values(), key=lambda x: x["score"], reverse=True)[:TOP_N_WALLETS]

    try:
        import os
        os.makedirs("data", exist_ok=True)
        with open(WALLET_CACHE_FILE, "w") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "wallets": top}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to cache wallets: {e}")

    logger.info(f"Loaded {len(top)} qualifying wallets")
    return top


def get_smart_money_set(wallets: List[Dict]) -> Set[str]:
    """Return set of wallet addresses for fast O(1) lookup."""
    return {w["address"] for w in wallets}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    wallets = get_top_wallets(force_refresh=True)
    print(f"\nTop {len(wallets)} wallets:")
    for i, w in enumerate(wallets[:10], 1):
        print(f"  {i:2}. {(w['username'] or w['address'])[:25]:<25} ROI={w['roi']:.1%}  PNL=${w['pnl']:>12,.0f}")
