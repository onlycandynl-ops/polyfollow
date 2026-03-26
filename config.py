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
TOP_N_WALLETS = 30           # Track top N wallets
MIN_TRADES = 20              # Min trades to be considered
MIN_ROI = 0.05               # Min 5% ROI to be in top list
SCORE_WINDOW_DAYS = 30       # Rolling window for scoring

# === Signal Engine ===
CONSENSUS_THRESHOLD = 0.70   # 70% of top wallets must agree
MIN_LIQUIDITY = 1000         # Min $1000 liquidity in market
MIN_PRICE = 0.05             # Min 5% (avoid near-zero markets)
MAX_PRICE = 0.95             # Max 95% (avoid near-certain markets)
MIN_VOLUME = 5000            # Min $5000 volume

# === Paper Trading ===
PAPER_BANKROLL = 1000.0      # Starting paper bankroll in USDC
TRADE_SIZE_PCT = 0.02        # 2% of bankroll per trade
MAX_OPEN_POSITIONS = 10      # Max concurrent positions
STOP_LOSS_PCT = -0.50        # Stop loss at -50%
TAKE_PROFIT_PCT = 0.80       # Take profit at +80%

# === Timing ===
SCAN_INTERVAL_MINUTES = 60   # Scan every 60 minutes
WALLET_REFRESH_HOURS = 24    # Refresh wallet scores daily

# === Paths ===
DATA_DIR = "data"
PAPER_STATE_FILE = f"{DATA_DIR}/paper_state.json"
WALLET_CACHE_FILE = f"{DATA_DIR}/wallet_cache.json"
TRADE_LOG_FILE = f"{DATA_DIR}/trade_log.json"
