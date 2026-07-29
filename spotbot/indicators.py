"""Indicator computation for SpotBot.

Two independent indicator systems:
  * FLI / SAI math (fli_compute_*) — chart display overlay only, never
    passed to TradingEngine.evaluate_signal.
  * IndicatorEngine — RSI / MACD / Bollinger / EMA / SMA used by the
    trading strategy.
"""
import math

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

from spotbot.constants import (
    FLOAT_EPS,
    FLI_BB_PERIOD, FLI_BB_DEV, FLI_USE_ATR, FLI_ATR_PERIOD,
    FLI_USE_CCI, FLI_CCI_LEN, FLI_CCI_LEVEL, FLI_CCI_BUFFER,
    FLI_USE_ADX, FLI_ADX_LEN, FLI_ADX_LEVEL,
    FLI_USE_OBV, FLI_OBV_SMA_LEN, FLI_MIN_SCORE,
)
def fli_compute_atr(df: "pd.DataFrame", period: int) -> "pd.Series":
    """Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def fli_compute_cci(df: "pd.DataFrame", period: int) -> "pd.Series":
    """Commodity Channel Index."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    sma = tp.rolling(window=period, min_periods=1).mean()
    mad = tp.rolling(window=period, min_periods=1).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    return (tp - sma) / (0.015 * mad.replace(0, np.nan))


def fli_compute_adx(df: "pd.DataFrame", period: int):
    """Average Directional Index. Returns (plus_di, minus_di, adx)."""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )

    atr_smooth = tr.rolling(window=period, min_periods=1).mean()
    plus_dm_smooth = plus_dm.rolling(window=period, min_periods=1).mean()
    minus_dm_smooth = minus_dm.rolling(window=period, min_periods=1).mean()

    plus_di = 100.0 * plus_dm_smooth / atr_smooth.replace(0, np.nan)
    minus_di = 100.0 * minus_dm_smooth / atr_smooth.replace(0, np.nan)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(window=period, min_periods=1).mean()

    return plus_di.fillna(0), minus_di.fillna(0), adx.fillna(0)


def fli_compute_obv(df: "pd.DataFrame") -> "pd.Series":
    """On-Balance Volume."""
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def fli_compute_all_indicators(df: "pd.DataFrame", params: dict) -> "pd.DataFrame":
    """
    Computes, in order (identical logic to main.py, minus SL/TP1/TP2):
      1. Bollinger Bands            5. CCI confirmation
      2. BB signal                  6. ADX confirmation
      3. ATR                        7. OBV confirmation
      4. TrendLine + iTrend (FLI)   8. Score system + confirmed buy/sell signals
    """
    df = df.copy()
    n = len(df)
    if n < 2:
        return df

    bb_period = params.get("bb_period", FLI_BB_PERIOD)
    bb_dev = params.get("bb_dev", FLI_BB_DEV)
    df["sma"] = df["close"].rolling(window=bb_period, min_periods=1).mean()
    df["std"] = df["close"].rolling(window=bb_period, min_periods=1).std().fillna(0)
    df["bb_upper"] = df["sma"] + bb_dev * df["std"]
    df["bb_lower"] = df["sma"] - bb_dev * df["std"]

    df["bb_signal"] = np.where(
        df["close"] > df["bb_upper"], 1, np.where(df["close"] < df["bb_lower"], -1, 0)
    )

    use_atr = params.get("use_atr", FLI_USE_ATR)
    atr_period = params.get("atr_period", FLI_ATR_PERIOD)
    df["atr"] = (
        fli_compute_atr(df, atr_period) if use_atr else pd.Series(0.0, index=df.index)
    )

    trendline = np.zeros(n)
    trendline[0] = df["close"].iloc[0]
    for i in range(1, n):
        atr_val = df["atr"].iloc[i] if not np.isnan(df["atr"].iloc[i]) else 0.0
        sig = df["bb_signal"].iloc[i]
        if sig == 1:
            new_val = df["low"].iloc[i] - atr_val
            trendline[i] = max(trendline[i - 1], new_val)
        elif sig == -1:
            new_val = df["high"].iloc[i] + atr_val
            trendline[i] = min(trendline[i - 1], new_val)
        else:
            trendline[i] = trendline[i - 1]
    df["trendline"] = trendline

    tl_diff = np.diff(trendline, prepend=trendline[0])
    df["itrend"] = np.where(tl_diff > 0, 1, np.where(tl_diff < 0, -1, 0))
    it_arr = df["itrend"].values.astype(float)
    for i in range(1, n):
        if it_arr[i] == 0:
            it_arr[i] = it_arr[i - 1]
    df["itrend"] = it_arr.astype(int)

    prev_it = df["itrend"].shift(1).fillna(0).astype(int)
    df["raw_buy"] = (prev_it == -1) & (df["itrend"] == 1)
    df["raw_sell"] = (prev_it == 1) & (df["itrend"] == -1)

    use_cci = params.get("use_cci", FLI_USE_CCI)
    cci_len = params.get("cci_len", FLI_CCI_LEN)
    cci_level = params.get("cci_level", FLI_CCI_LEVEL)
    cci_buffer = params.get("cci_buffer", FLI_CCI_BUFFER)
    df["cci"] = (
        fli_compute_cci(df, cci_len) if use_cci else pd.Series(0.0, index=df.index)
    )

    use_adx = params.get("use_adx", FLI_USE_ADX)
    adx_len = params.get("adx_len", FLI_ADX_LEN)
    adx_level = params.get("adx_level", FLI_ADX_LEVEL)
    if use_adx:
        df["plus_di"], df["minus_di"], df["adx"] = fli_compute_adx(df, adx_len)
    else:
        df["plus_di"] = 0.0
        df["minus_di"] = 0.0
        df["adx"] = 999.0

    use_obv = params.get("use_obv", FLI_USE_OBV)
    obv_sma_len = params.get("obv_sma_len", FLI_OBV_SMA_LEN)
    df["obv"] = fli_compute_obv(df) if use_obv else pd.Series(0.0, index=df.index)
    df["obv_sma"] = df["obv"].rolling(window=obv_sma_len, min_periods=1).mean()

    cci_buy_ok = df["cci"] > (cci_level + cci_buffer)
    cci_sell_ok = df["cci"] < -(cci_level + cci_buffer)
    adx_ok = df["adx"] > adx_level
    obv_buy_ok = df["obv"] > df["obv_sma"]
    obv_sell_ok = df["obv"] < df["obv_sma"]

    df["score_buy"] = (
        (use_cci and cci_buy_ok).astype(int)
        + (use_adx and adx_ok).astype(int)
        + (use_obv and obv_buy_ok).astype(int)
    )
    df["score_sell"] = (
        (use_cci and cci_sell_ok).astype(int)
        + (use_adx and adx_ok).astype(int)
        + (use_obv and obv_sell_ok).astype(int)
    )

    min_score = params.get("min_score", FLI_MIN_SCORE)
    df["buy_signal"] = df["raw_buy"] & (df["score_buy"] >= min_score)
    df["sell_signal"] = df["raw_sell"] & (df["score_sell"] >= min_score)

    return df


def fli_ohlcv_to_df(candles: list) -> "pd.DataFrame":
    """Convert a ccxt-style OHLCV list ([ts, o, h, l, c, v], ts in ms or s)
    into the DataFrame shape fli_compute_all_indicators expects."""
    if not candles:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    rows = []
    for c in candles:
        if not isinstance(c, (list, tuple)) or len(c) < 5:
            continue
        ts = c[0]
        try:
            ts_val = float(ts)
        except (TypeError, ValueError):
            continue
        if ts_val <= 0:
            continue
        ts_sec = ts_val / 1000.0 if ts_val > 1e10 else ts_val
        rows.append(
            {
                "time": pd.Timestamp(ts_sec, unit="s", tz="UTC"),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]) if len(c) > 5 else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _compute_fli_data(candles):
    """Compute FLI indicator data dict from raw ccxt candles for chart overlay."""
    if not candles:
        return None
    try:
        df = fli_ohlcv_to_df(candles)
        if df.empty:
            return None
        df = fli_compute_all_indicators(df, {})
        last = df.iloc[-1]
        score = 0
        if last.get("buy_signal", False):
            score += 1
        if last.get("sell_signal", False):
            score += 1
        if last.get("bb_signal", False):
            score += 1
        if score >= FLI_MIN_SCORE:
            if last.get("buy_signal", False):
                signal = "BUY"
            elif last.get("sell_signal", False):
                signal = "SELL"
            else:
                signal = "WAIT"
        else:
            signal = "WAIT"
        fli_trend = int(last.get("itrend", 0))
        return {
            "bb_upper": df["bb_upper"].tolist(),
            "bb_lower": df["bb_lower"].tolist(),
            "trendline": df["trendline"].tolist(),
            "itrend": df["itrend"].tolist(),
            "signal": signal,
            "fli_trend": fli_trend,
            "score": score,
            "bb_upper_val": float(last.get("bb_upper", 0)),
            "bb_lower_val": float(last.get("bb_lower", 0)),
            "cci": float(last.get("cci", 0)),
            "adx": float(last.get("adx", 0)),
        }
    except Exception:
        return None



class TradeSignal(Exception):
    """Raised when the trading engine refuses to act on a signal."""


class IndicatorEngine:
    @staticmethod
    def sma(data, period):
        if NUMPY_AVAILABLE:
            arr = np.array(data, dtype=np.float64)
            if len(arr) < period:
                return []
            cs = np.cumsum(arr)
            return (
                (cs[period - 1 :] - np.concatenate(([0], cs[: len(arr) - period])))
                / period
            ).tolist()
        result = []
        for i in range(period - 1, len(data)):
            result.append(sum(data[i - period + 1 : i + 1]) / period)
        return result

    @staticmethod
    def ema(data, period):
        if len(data) < period:
            return []
        k = 2.0 / (period + 1)
        r = [sum(data[:period]) / period]
        for p in data[period:]:
            r.append(p * k + r[-1] * (1 - k))
        return r

    @staticmethod
    def rsi(closes, period=14):
        if len(closes) < period + 1:
            return []
        deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        result = []
        if avg_l == 0:
            result.append(100.0)
        else:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        for i in range(period, len(deltas)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            if avg_l == 0:
                result.append(100.0)
            else:
                result.append(100 - 100 / (1 + avg_g / avg_l))
        pad = [None] * (len(closes) - len(result))
        return pad + result

    @staticmethod
    def macd(closes, fast=12, slow=26, signal=9):
        ef = IndicatorEngine.ema(closes, fast)
        es = IndicatorEngine.ema(closes, slow)
        if not ef or not es:
            return {"macd_line": [], "signal_line": [], "histogram": []}
        d = len(ef) - len(es)
        ml = [ef[i + d] - es[i] for i in range(len(es))]
        if len(ml) < signal:
            return {"macd_line": [], "signal_line": [], "histogram": []}
        sl = IndicatorEngine.ema(ml, signal)
        sd2 = len(ml) - len(sl)
        hist = [ml[i + sd2] - sl[i] for i in range(len(sl))]
        p1 = [None] * (len(closes) - len(ml))
        p2 = [None] * (len(closes) - len(sl))
        p3 = [None] * (len(closes) - len(hist))
        return {"macd_line": p1 + ml, "signal_line": p2 + sl, "histogram": p3 + hist}

    @staticmethod
    def bollinger(closes, period=20, std_dev=2.0):
        if len(closes) < period:
            return {"upper": [], "middle": [], "lower": []}
        mid = IndicatorEngine.sma(closes, period)
        if NUMPY_AVAILABLE:
            arr = np.array(closes, dtype=np.float64)
            stds = [
                np.std(arr[i - period + 1 : i + 1]) for i in range(period - 1, len(arr))
            ]
        else:
            stds = []
            for i in range(period - 1, len(closes)):
                w = closes[i - period + 1 : i + 1]
                m = mid[i - period + 1]
                stds.append(math.sqrt(sum((x - m) ** 2 for x in w) / period))
        upper = [m + std_dev * s for m, s in zip(mid, stds)]
        lower = [m - std_dev * s for m, s in zip(mid, stds)]
        pad = [None] * (len(closes) - len(mid))
        return {"upper": pad + upper, "middle": pad + mid, "lower": pad + lower}

    @staticmethod
    def compute_all_indicators(candles):
        """Main entry — computes all indicators from OHLCV candle list."""
        closes = [c[4] for c in candles] if candles else []
        return {
            "rsi_14": IndicatorEngine.rsi(closes, 14),
            "macd": IndicatorEngine.macd(closes),
            "bollinger": IndicatorEngine.bollinger(closes),
            "ema_9": _pad(candles, IndicatorEngine.ema(closes, 9)),
            "ema_21": _pad(candles, IndicatorEngine.ema(closes, 21)),
            "sma_50": _pad(candles, IndicatorEngine.sma(closes, 50)),
            "candle_count": len(candles),
        }


def _pad(candles, data):
    return [None] * (len(candles) - len(data)) + data if data else [None] * len(candles)


def backtest_fli_signals(df: "pd.DataFrame") -> dict:
    """Run a simple backtest on FLI signals already computed in the DataFrame.

    Scans every row for ``buy_signal`` / ``sell_signal`` and simulates a
    long-only strategy (buy → sell → buy → …) to produce:

    * **markers**: list of ``{time, action, price}`` dicts ready for the
      chart renderer.
    * **stats**: ``total_trades``, ``wins``, ``losses``, ``win_rate``,
      ``total_pnl_pct`` — a quick summary the UI can display.

    ``time`` values are returned as **Unix seconds** (int) to match
    ``_to_chart_time`` expectations.
    """
    markers = []
    stats = {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
             "total_pnl_pct": 0.0}

    if df is None or df.empty or "buy_signal" not in df.columns:
        return {"markers": markers, "stats": stats}

    in_position = False
    entry_price = 0.0
    trade_pnl_list = []

    for i, row in df.iterrows():
        ts_raw = row.get("time")
        if ts_raw is None:
            ts_raw = row.get("timestamp")
        if ts_raw is None:
            continue

        # Convert timestamp to unix seconds int
        try:
            if isinstance(ts_raw, pd.Timestamp):
                ts_sec = int(ts_raw.timestamp())
            else:
                v = float(ts_raw)
                ts_sec = int(v / 1000.0) if v > 1e10 else int(v)
        except (TypeError, ValueError):
            continue

        price = float(row.get("close", 0))

        if not in_position and row.get("buy_signal", False):
            in_position = True
            entry_price = price
            markers.append({"time": ts_sec, "action": "bt_buy", "price": price})

        elif in_position and row.get("sell_signal", False):
            in_position = False
            pnl_pct = ((price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
            trade_pnl_list.append(pnl_pct)
            markers.append({"time": ts_sec, "action": "bt_sell", "price": price})

    # Compute stats
    stats["total_trades"] = len(trade_pnl_list)
    stats["wins"] = sum(1 for p in trade_pnl_list if p > 0)
    stats["losses"] = sum(1 for p in trade_pnl_list if p <= 0)
    stats["win_rate"] = (
        (stats["wins"] / stats["total_trades"] * 100.0)
        if stats["total_trades"] > 0
        else 0.0
    )
    stats["total_pnl_pct"] = sum(trade_pnl_list) if trade_pnl_list else 0.0

    return {"markers": markers, "stats": stats}
