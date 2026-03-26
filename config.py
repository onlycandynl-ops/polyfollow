import os
from dotenv import load_dotenv

load_dotenv()

# === Telegram ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# === Polymarket API ===
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# === Wallet Scoring ===
TOP_N_WALLETS = 200
WALLET_CATEGORIES = ["POLITICS", "ECONOMICS", "CULTURE", "TECH", "FINANCE"]
MIN_PNL = 1000          # Min $1k PNL to qualify
MIN_VOLUME = 5000       # Min $5k volume to qualify
SCORE_WINDOW = "MONTH"  # Leaderboard window

# === Signal Engine ===
CONSENSUS_THRESHOLD = 0.60   # 60% of wallets in that market must agree
MIN_LIQUIDITY = 1000         # Min $1000 liquidity
MIN_MARKET_VOLUME = 5000     # Min $5000 volume
MIN_PRICE = 0.05             # Min 5%
MAX_PRICE = 0.95             # Max 95%
MIN_HOURS_LEFT = 24          # Market must have > 24h left
MAX_TRADES_PER_CYCLE = 5     # Max new trades per scan cycle

# === Paper Trading ===
PAPER_BANKROLL = 1000.0
TRADE_SIZE_PCT = 0.02        # 2% per trade
MAX_OPEN_POSITIONS = 15
STOP_LOSS_PCT = -0.50        # -50%
TAKE_PROFIT_PCT = 0.80       # +80%

# === Timing ===
SCAN_INTERVAL_MINUTES = 60
WALLET_REFRESH_HOURS = 24

# === Paths ===
DATA_DIR = "data"
PAPER_STATE_FILE = f"{DATA_DIR}/paper_state.json"
WALLET_CACHE_FILE = f"{DATA_DIR}/wallet_cache.json"
TRADE_LOG_FILE = f"{DATA_DIR}/trade_log.json"
