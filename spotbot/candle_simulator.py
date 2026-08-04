"""Realistic OHLCV candle simulator for testing indicators and strategies.

Generates candles that mimic real market behaviour — trends, mean reversion,
volatility clustering, volume spikes — so that FLI, MOM, Hull MA and every
other indicator reacts as it would on live data.

Only the data source is mocked; everything else (indicator computation,
trading-engine evaluation, chart rendering, backtest, PnL) runs on the
real code-paths unchanged.
"""

import random
import time
from typing import Optional

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False


class CandleSimulator:
    """Stateful OHLCV candle generator.

    Usage::

        sim = CandleSimulator(base_price=0.05, timeframe_sec=180)
        # Seed initial history
        candles = sim.generate_history(count=200)
        # Then call in a loop for each new candle
        candle = sim.next_candle()

    Each candle is a list: ``[timestamp_ms, open, high, low, close, volume]``
    — the exact format the rest of SpotBot expects.
    """

    def __init__(
        self,
        base_price: float = 0.05,
        timeframe_sec: int = 180,
        volatility: float = 0.015,
        trend_strength: float = 0.0003,
        mean_reversion: float = 0.0001,
        volume_base: float = 50_000.0,
        seed: Optional[int] = None,
    ):
        """
        Parameters
        ----------
        base_price : starting price for the first candle.
        timeframe_sec : candle duration in seconds (e.g. 180 for 3 m).
        volatility : base volatility (fraction of price). Real markets have
            0.5 %–5 % per candle depending on asset and timeframe.
        trend_strength : how strongly a new trend drifts the price.
        mean_reversion : pull toward a long-term moving average.
        volume_base : average volume units per candle.
        seed : optional RNG seed for reproducibility.
        """
        if seed is not None:
            random.seed(seed)
            if NUMPY_AVAILABLE:
                np.random.seed(seed)

        self.base_price = base_price
        self.timeframe_sec = timeframe_sec
        self.volatility = volatility
        self.trend_strength = trend_strength
        self.mean_reversion = mean_reversion
        self.volume_base = volume_base

        # Internal state
        self._price = base_price
        self._trend = 0.0          # current trend direction (±)
        self._trend_duration = 0    # bars remaining in current trend phase
        self._vol = volatility      # current local volatility
        self._timestamp = int(time.time() * 1000)
        # Align timestamp to the start of the current candle period
        self._timestamp = (self._timestamp // (timeframe_sec * 1000)) * (timeframe_sec * 1000)
        self._ma20 = base_price    # running 20-candle MA for mean reversion

        # History ring buffer for MA calculation
        self._close_history: list[float] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_history(self, count: int = 200) -> list:
        """Generate *count* historical candles and return them as a list.

        The simulator's internal state is advanced so that ``next_candle()``
        continues from where the history ends.
        """
        candles = []
        # Warm up silently (no candles returned) so the MA and trend
        # are already stabilised.
        for _ in range(60):
            self._step()
        for _ in range(count):
            candles.append(self._step())
        return candles

    def next_candle(self) -> list:
        """Generate and return the next candle."""
        return self._step()

    def set_speed_factor(self, factor: float):
        """Adjust the volatility / trend proportionally to simulate
        different market regimes. ``factor=1.0`` is normal;
        ``factor=3.0`` triples the action."""
        self.volatility = self.volatility * factor
        self.trend_strength = self.trend_strength * factor

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _step(self) -> list:
        """Generate one candle and advance internal state."""
        self._timestamp += self.timeframe_sec * 1000

        # ── Trend phase management ──
        # A "trend phase" lasts 10–80 candles. When it expires we randomly
        # pick a new trend direction and strength.
        if self._trend_duration <= 0:
            self._trend = random.gauss(0, self.trend_strength)
            self._trend = max(-self.trend_strength * 4,
                              min(self.trend_strength * 4, self._trend))
            self._trend_duration = random.randint(10, 80)

        self._trend_duration -= 1

        # ── Volatility clustering (GARCH-like) ──
        # Volatility tends to cluster: high-vol periods follow high-vol,
        # low-vol follow low-vol.
        vol_shock = random.gauss(0, 0.002)
        self._vol += 0.05 * (self.volatility - self._vol) + vol_shock
        self._vol = max(0.002, min(0.10, self._vol))

        # ── Random spike / crash events (rare, ~2 %) ──
        spike = 0.0
        if random.random() < 0.02:
            spike = random.gauss(0, self._vol * 3)

        # ── Price return for this candle ──
        noise = random.gauss(0, self._vol)
        mean_pull = -self.mean_reversion * (self._price - self._ma20) / max(self._ma20, 1e-12)
        ret = self._trend + noise + mean_pull + spike

        open_price = self._price
        close_price = open_price * (1.0 + ret)
        close_price = max(close_price, open_price * 0.5)  # floor clamp

        # Intra-candle wick: high/low extend beyond open–close range
        body_range = abs(close_price - open_price)
        wick_extra = random.uniform(0.1, 0.6) * body_range  # at least 10 % wick
        if close_price >= open_price:
            high = close_price + wick_extra
            low = open_price - wick_extra * random.uniform(0.3, 1.0)
        else:
            high = open_price + wick_extra * random.uniform(0.3, 1.0)
            low = close_price - wick_extra

        low = max(low, close_price * 0.5)

        # ── Volume ──
        # Volume spikes on large moves and trends
        abs_ret = abs(ret)
        vol_mult = 1.0 + abs_ret * 30 + abs(self._trend) * 50
        volume = self.volume_base * vol_mult * random.uniform(0.5, 1.5)
        volume = max(volume, 100)

        # ── Update internal state ──
        self._price = close_price
        self._close_history.append(close_price)
        if len(self._close_history) > 20:
            self._close_history.pop(0)
        self._ma20 = sum(self._close_history) / len(self._close_history)

        return [
            self._timestamp,
            round(open_price, 10),
            round(high, 10),
            round(low, 10),
            round(close_price, 10),
            round(volume, 4),
        ]

    def seed_from_candles(self, candles: list):
        """Seed the simulator state from existing real candles.

        The simulator will continue generating new candles from the last
        real candle's close price and timestamp, preserving the volatility
        and trend characteristics of the recent data.

        Parameters
        ----------
        candles : list of [ts, o, h, l, c, v]
            Existing OHLCV candle history (at least 2 candles recommended).
        """
        if not candles:
            return

        # Use last candle's close as current price
        last = candles[-1]
        self._price = float(last[4])  # close
        self.base_price = self._price
        self._timestamp = int(last[0])  # last candle timestamp

        # Build close history from available candles (up to 20 for MA)
        recent = candles[-20:]
        self._close_history = [float(c[4]) for c in recent]
        self._ma20 = sum(self._close_history) / len(self._close_history)

        # Estimate volatility from recent candles
        if len(recent) >= 2:
            returns = []
            for i in range(1, len(recent)):
                prev_close = float(recent[i - 1][4])
                curr_close = float(recent[i][4])
                if prev_close > 0:
                    returns.append(abs((curr_close - prev_close) / prev_close))
            if returns:
                self._vol = max(0.002, min(0.10, sum(returns) / len(returns) * 1.5))
                self.volatility = self._vol

        # Estimate trend from recent candles
        if len(recent) >= 5:
            first_close = float(recent[0][4])
            last_close = float(recent[-1][4])
            if first_close > 0:
                avg_move = (last_close - first_close) / first_close / len(recent)
                self._trend = max(-self.trend_strength * 4,
                                  min(self.trend_strength * 4, avg_move))
                self._trend_duration = random.randint(10, 40)

    def reset(self, base_price: Optional[float] = None):
        """Reset the simulator to initial state."""
        if base_price is not None:
            self.base_price = base_price
        self._price = self.base_price
        self._trend = 0.0
        self._trend_duration = 0
        self._vol = self.volatility
        self._ma20 = self.base_price
        self._close_history.clear()
        self._timestamp = int(time.time() * 1000)
        self._timestamp = (self._timestamp // (self.timeframe_sec * 1000)) * (self.timeframe_sec * 1000)


# ── Timeframe → seconds mapping ──
TF_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
