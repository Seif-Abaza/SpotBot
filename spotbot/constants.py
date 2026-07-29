"""Configuration constants for SpotBot (Targov v3.0).

All runtime paths, trading parameters, and optional-dependency import guards
live here so every submodule can import from a single source of truth.
"""

from pathlib import Path
import os
import sys

# ── Optional dependency import guards ──────────────────────────────────
# These flags are checked throughout the codebase so the app degrades
# gracefully when an optional dependency is missing.
try:
    import ccxt

    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

try:
    import ccxt.pro as ccxtpro

    CCXT_PRO_AVAILABLE = True
except ImportError:
    CCXT_PRO_AVAILABLE = False

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    import trade_notifier

    TRADE_NOTIFIER_AVAILABLE = True
except ImportError:
    TRADE_NOTIFIER_AVAILABLE = False

CONFIG_DIR = Path.home() / ".targov_dashboard"
API_KEY_FILE = CONFIG_DIR / "api_keys.json"
PNL_LOG_FILE = CONFIG_DIR / "pnl_log.json"
CANDLE_LIMIT = 500
# Code Review (Additional #20): single source of truth for supported timeframes.
# Used by the radio buttons, the timeframe→seconds map, the polling-interval
# map, and any code that needs to enumerate valid timeframes.
TIMEFRAMES = ("3m", "5m", "15m", "30m", "1h", "4h", "1d")
TIMEFRAME_MAP = {
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
CHART_CDN_URL = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"
REFRESH_MS = 3000
QUOTE_ASSETS = frozenset({"USDT", "USD", "USDC", "BUSD", "DAI", "FDUSD", "TUSD"})
WALLET_MIN_NOTIONAL_USDT = 1.0
TRADE_HISTORY_LIMIT = 500

# ── Trading-strategy constants (Code Review 4.2: extract magic numbers) ──
RSI_BUY_THRESHOLD = 30  # initial RSI buy-signal threshold
RSI_SELL_THRESHOLD = 70  # initial RSI sell-signal threshold
RSI_BUY_CONFIRM = 35  # 1-candle confirmation threshold (relaxed)
RSI_SELL_CONFIRM = 65  # 1-candle confirmation threshold (relaxed)
MACD_BUY_CONFIRM_EPS = 0.0  # MACD line must stay above signal line to confirm
MACD_SELL_CONFIRM_EPS = 0.0  # MACD line must stay below signal line to confirm
FLOAT_EPS = 1e-12  # tiny epsilon for "quantity is effectively zero" checks

# ── Order execution constants (Code Review Low #12: extract magic numbers) ──
# Used by TradingEngine._execute_order for slippage/fee modeling in SIMULATOR
# mode and by the insufficient-balance fallback that clamps sell_qty to the
# wallet's free base-coin quantity.
DEFAULT_SLIPPAGE = 0.001  # 0.1% adverse slippage on simulated fills
DEFAULT_TAKER_FEE = 0.02  # 2% taker fee (used only as a sanity bound)
CONFIRM_MULTIPLIER = 1.0  # multiplier applied to confirmation thresholds

# ── Mock-candle gate (Code Review Medium #8) ──
# When CCXT is unavailable we always fall back to mock candles so the chart
# still renders.  When CCXT IS available, mocks are gated behind this env
# var so a production deployment can never accidentally trade against fake
# data.  Set APPV3_ALLOW_MOCKS=1 to re-enable them for local testing.
ALLOW_MOCK_CANDLES = (
    not CCXT_AVAILABLE  # auto-enable when there's no real exchange backend
    or os.environ.get("APPV3_ALLOW_MOCKS", "").lower() in ("1", "true", "yes")
)

# ── API-key encryption (Code Review 2.1: no plaintext credentials on disk) ──
# We use a machine-local passphrase derived from a fixed app salt + the user's
# home path.  This is NOT as strong as a real OS keychain (keyring package)
# but it prevents the trivial "cat api_keys.json" attack.  The file is also
# chmod 0o600'd on write.
API_KEY_PASSPHRASE = b"targov_dashboard_v3::api_keys::" + str(Path.home()).encode()


# ── FLI / SAI indicator defaults (ported 1:1 from main.py — chart display only,
#    NOT wired into TradingEngine.evaluate_signal, so Buy/Sell logic is untouched) ──
FLI_BB_PERIOD = 19
FLI_BB_DEV = 0.6
FLI_USE_ATR = True
FLI_ATR_PERIOD = 9
FLI_USE_CCI = True
FLI_CCI_LEN = 20
FLI_CCI_LEVEL = 100.0
FLI_CCI_BUFFER = 0.0
FLI_USE_ADX = True
FLI_ADX_LEN = 14
FLI_ADX_LEVEL = 20
FLI_USE_OBV = True
FLI_OBV_SMA_LEN = 15
FLI_MIN_SCORE = 1
