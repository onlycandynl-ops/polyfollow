import requests
import json
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from config import (
    DATA_API, GAMMA_API, TOP_N_WALLETS, MIN_TRADES,
    MIN_ROI, SCORE_WINDOW_DAYS, WALLET_CACHE_FILE
)

logger = logging.getLogger(__name__)


def fetch_leaderboard(limit: int = 100) -> List[Dict]:
    """Fetch top traders from Polymarket leaderboard."""
    try:
        url = f"{DATA_API}/leaderboard"
        params = {"limit": limit, "offset": 0}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        logger.error(f"Failed to fetch leaderboard: {e}")
        return []


def fetch_wallet_trades(address: str, days: int = SCORE_WINDOW_DAYS) -> List[Dict]:
    """Fetch recent trades for a wallet address."""
    trades = []
    offset = 0
    limit = 100
    cutoff = datetime.utcnow() - timedelta(days=days)

    while True:
        try:
            url = f"{DATA_API}/trades"
            params = {"maker": address, "limit": limit, "offset": offset}
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break

            for trade in batch:
                ts = trade.get("timestamp") or trade.get("created_at", "")
                try:
                    trade_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                    if trade_dt < cutoff:
                        return trades
                except Exception:
                    pass
                trades.append(trade)

            if len(batch) < limit:
                break
            offset += limit
            time.sleep(0.1)  # Rate limit friendly

        except Exception as e:
            logger.error(f"Failed to fetch trades for {address}: {e}")
            break

    return trades


def score_wallet(address: str, trades: List[Dict]) -> Optional[Dict]:
    """
    Score a wallet based on recent performance.
    Returns None if wallet doesn't meet minimum criteria.
    """
    if len(trades) < MIN_TRADES:
        return None

    total_invested = 0.0
    total_returned = 0.0
    wins = 0
    losses = 0

    for trade in trades:
        side = trade.get("side", "").upper()
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        cost = size * price

        if side == "BUY":
            total_invested += cost
        elif side == "SELL":
            total_returned += cost

    if total_invested == 0:
        return None

    roi = (total_returned - total_invested) / total_invested
    win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.5

    if roi < MIN_ROI:
        return None

    # Composite score: ROI weighted with trade volume
    volume_factor = min(len(trades) / 100, 1.0)  # Normalize, cap at 100 trades
    score = roi * (0.7 + 0.3 * volume_factor)

    return {
        "address": address,
        "score": round(score, 4),
        "roi": round(roi, 4),
        "trade_count": len(trades),
        "total_invested": round(total_invested, 2),
        "win_rate": round(win_rate, 4),
        "updated_at": datetime.utcnow().isoformat()
    }


def get_top_wallets(force_refresh: bool = False) -> List[Dict]:
    """
    Get top N wallets, using cache if available and fresh.
    Falls back to leaderboard + scoring.
    """
    # Try cache first
    if not force_refresh:
        try:
            with open(WALLET_CACHE_FILE, "r") as f:
                cache = json.load(f)
            cached_at = datetime.fromisoformat(cache.get("cached_at", "2000-01-01"))
            if datetime.utcnow() - cached_at < timedelta(hours=24):
                logger.info(f"Using cached wallet list ({len(cache['wallets'])} wallets)")
                return cache["wallets"]
        except Exception:
            pass

    logger.info("Refreshing wallet scores from leaderboard...")
    leaderboard = fetch_leaderboard(limit=150)

    if not leaderboard:
        logger.warning("Empty leaderboard, returning empty wallet list")
        return []

    scored = []
    for entry in leaderboard[:80]:  # Score top 80, pick best 30
        address = entry.get("proxyWalletAddress") or entry.get("address") or entry.get("proxy_wallet")
        if not address:
            continue

        trades = fetch_wallet_trades(address)
        wallet_score = score_wallet(address, trades)
        if wallet_score:
            scored.append(wallet_score)
            logger.debug(f"Scored {address[:8]}... ROI={wallet_score['roi']:.2%}")
        time.sleep(0.2)

    # Sort by composite score, take top N
    scored.sort(key=lambda x: x["score"], reverse=True)
    top_wallets = scored[:TOP_N_WALLETS]

    # Cache results
    try:
        import os
        os.makedirs("data", exist_ok=True)
        with open(WALLET_CACHE_FILE, "w") as f:
            json.dump({
                "cached_at": datetime.utcnow().isoformat(),
                "wallets": top_wallets
            }, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to cache wallets: {e}")

    logger.info(f"Found {len(top_wallets)} qualifying wallets")
    return top_wallets


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    wallets = get_top_wallets(force_refresh=True)
    print(f"\nTop {len(wallets)} wallets:")
    for i, w in enumerate(wallets[:10], 1):
        print(f"  {i}. {w['address'][:10]}... ROI={w['roi']:.2%} Score={w['score']:.4f} Trades={w['trade_count']}")
