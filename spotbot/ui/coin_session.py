"""Per-coin session: holds ExchangeManager, TradingEngine, ChartRenderer, workers."""

import json
import time

from PySide6.QtCore import QObject, Signal, Slot, QThread
from PySide6.QtWidgets import QWidget

from spotbot.constants import (
    CANDLE_LIMIT,
    FLOAT_EPS,
    NUMPY_AVAILABLE,
    PANDAS_AVAILABLE,
    REFRESH_MS,
    TIMEFRAME_MAP,
    TRADE_HISTORY_LIMIT,
)
from spotbot.exchange import ExchangeManager
from spotbot.trading import TradingEngine
from spotbot.transaction_logger import TransactionLogger
from spotbot.chart_renderer import ChartRenderer, FLIChartWorker
from spotbot.workers import (
    DataFetchWorker,
    IndicatorCalcWorker,
    ParallelPipeline,
    PairLoaderWorker,
    ProcessWorker,
    WebSocketWorker,
)


class CoinSession:
    """Per-pair state container for multi-coin tabs.

    Each tab owns one CoinSession.  The session holds a TradingEngine,
    candles, indicators, markers, and position info recovered from
    historical trades via FIFO lot matching.
    """

    def __init__(
        self,
        pair: str,
        timeframe: str,
        exchange_mgr,
        logger,
        investment_amount: float = 10.0,
        investment_mode: str = "FIXED",
    ):
        self.pair = pair
        self.timeframe = timeframe

        self.engine = TradingEngine(exchange_mgr, logger)
        self.engine.set_params(investment_amount, investment_mode, pair)

        self.candles: list = []  # [[ts,o,h,l,c,v], …]
        self.indicators: dict = {}
        self.markers: list = []  # [{ts, action, price}, …]

        # Position info — recovered from trades via FIFO lot matching
        self.entry_price: float | None = None
        self.entry_qty: float | None = None
        self.balance: float = 0.0

        self.halted: bool = False
        self.trading_enabled: bool = False  # user must toggle "Start Trading"
        self.pipeline = None  # CoinPipelineThread | ParallelPipeline | DataFetchWorker — set by MainWindow

    def seed_position(self, entry_price: float, qty: float):
        """Seed a recovered wallet position into the engine."""
        self.entry_price = entry_price
        self.entry_qty = qty
        self.engine.seed_wallet_position(entry_price, qty)

    def start_trading(self):
        """Enable live buy/sell execution."""
        self.trading_enabled = True
        self.engine.set_trading_enabled(True)

    def stop_trading(self):
        """Disable live buy/sell execution."""
        self.trading_enabled = False
        self.engine.set_trading_enabled(False)

    def halt(self):
        self.halted = True
        self.engine.halt()

    def resume(self):
        self.halted = False
        self.engine.resume()

    def update_balance(self, balance: float):
        self.balance = balance
        self.engine.update_balance(balance)


# ── Code Review 2.5: CoinPipelineThread deleted ──
# This class was never instantiated anywhere in the codebase (grep found only
# the class definition).  It also had a latent NameError — `run()` referenced
# `fli_data` (line 3365 in the old layout) without ever defining it, which
# would have crashed on every successful chart_ready emission.  Since
# ParallelPipeline (initial load) and DataFetchWorker (periodic refresh)
# cover the same responsibilities and are actually wired up, removing this
# dead code reduces confusion and the maintenance surface.
