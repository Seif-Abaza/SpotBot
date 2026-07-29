"""Background QThread workers for streaming, pair loading, and data pipelines."""

import asyncio
import json
import time

from PySide6.QtCore import QThread, Signal

from spotbot.constants import (
    CANDLE_LIMIT,
    CCXT_PRO_AVAILABLE,
    CCXT_AVAILABLE,
    FLOAT_EPS,
    NUMPY_AVAILABLE,
    PANDAS_AVAILABLE,
    TIMEFRAME_MAP,
    TRADE_HISTORY_LIMIT,
)
from spotbot.indicators import (
    _compute_fli_data,
    fli_compute_all_indicators,
    fli_ohlcv_to_df,
)
from spotbot.exchange import ExchangeManager
from spotbot.trading import TradingEngine
from spotbot.transaction_logger import TransactionLogger
from spotbot.chart_renderer import ChartRenderer


class WebSocketWorker(QThread):
    """QThread for ccxt.pro WebSocket real-time candle updates."""

    candle_update = Signal(list)  # [timestamp, o, h, l, c, v]
    ws_error = Signal(str)
    ws_status = Signal(str)

    def __init__(
        self, exchange_mgr: ExchangeManager, pair: str, timeframe: str, parent=None
    ):
        super().__init__(parent)
        self.exch_mgr = exchange_mgr
        self.pair = pair
        self.timeframe = timeframe
        self._running = True

    def run(self):
        if not CCXT_PRO_AVAILABLE:
            self.ws_status.emit("WebSocket unavailable — using polling")
            return

        self.ws_status.emit(f"WS connecting {self.pair}…")
        try:
            ws = self.exch_mgr.create_ws_exchange(
                self.exch_mgr.exchange_name, self.exch_mgr.is_demo
            )
            if ws is None:
                self.ws_status.emit("WS: exchange not supported")
                return

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            while self._running:
                try:
                    ohlcv = loop.run_until_complete(
                        ws.watch_ohlcv(self.pair, self.timeframe)
                    )
                    if ohlcv:
                        for candle in ohlcv:
                            self.candle_update.emit(candle)
                except Exception as e:
                    self.ws_error.emit(str(e))
                    time.sleep(5)

            loop.run_until_complete(ws.close())
            loop.close()

        except Exception as e:
            self.ws_error.emit(f"WS error: {e}")

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)


class PairLoaderWorker(QThread):
    """Loads spot pairs for selected exchange with progress updates."""

    pairs_loaded = Signal(list)
    load_progress = Signal(int)  # 0-100
    load_error = Signal(str)
    load_status = Signal(str)

    def __init__(self, exchange_mgr: ExchangeManager, exchange_name: str, parent=None):
        super().__init__(parent)
        self.exch_mgr = exchange_mgr
        self.exchange_name = exchange_name
        self._running = True

    def run(self):
        self.load_status.emit(f"Loading spot pairs for {self.exchange_name}…")

        try:
            pairs = self.exch_mgr.get_all_pairs(
                self.exchange_name, self.load_progress.emit
            )
            self.load_status.emit(f"Found {len(pairs)} spot pairs")
            self.pairs_loaded.emit(pairs)
            self.load_status.emit(f"{len(pairs)} pairs loaded ✅")

        except Exception as e:
            self.load_error.emit(str(e))
            self.load_progress.emit(0)

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)


class DataFetchWorker(QThread):
    data_fetched = Signal(dict)
    fetch_error = Signal(str)
    fetch_status = Signal(str)

    def __init__(self, exchange_mgr, pair, timeframe, parent=None):
        super().__init__(parent)
        self.exch_mgr = exchange_mgr
        self.pair = pair
        self.timeframe = timeframe

    def run(self):
        self.fetch_status.emit(f"Fetching {self.pair} @ {self.timeframe}…")
        try:
            candles = self.exch_mgr.fetch_ohlcv(self.pair, self.timeframe, CANDLE_LIMIT)
            balance = self.exch_mgr.fetch_wallet_coin("USDT")
            self.data_fetched.emit(
                {
                    "pair": self.pair,
                    "timeframe": self.timeframe,
                    "candles": candles,
                    "balance": balance,
                }
            )
            self.fetch_status.emit(
                f"{len(candles)} candles · Balance: {balance:.4f} USDT"
            )
        except Exception as e:
            self.fetch_error.emit(str(e))

    def stop(self):
        self.quit()
        self.wait(2000)


class IndicatorCalcWorker(QThread):
    indicators_ready = Signal(dict)
    calc_error = Signal(str)
    calc_status = Signal(str)

    def __init__(self, candles, parent=None):
        super().__init__(parent)
        self.candles = candles
        # Code Review Low #15: cooperative shutdown flag.
        self._running = True

    def run(self):
        self.calc_status.emit("Computing indicators…")
        try:
            if not self._running:
                return
            result = IndicatorEngine.compute_all_indicators(self.candles)
            if not self._running:
                return
            self.indicators_ready.emit(result)
            self.calc_status.emit("Indicators computed ✅")
        except Exception as e:
            if self._running:
                self.calc_error.emit(str(e))

    def stop(self):
        # Code Review Low #15: cooperative flag + Qt thread termination.
        self._running = False
        self.quit()
        self.wait(3000)


class ProcessWorker(QThread):
    """Processes candles + indicators → chart HTML + trading signals + markers."""

    process_ready = Signal(dict)
    process_error = Signal(str)
    process_status = Signal(str)

    def __init__(
        self, candles, indicators, pair, timeframe, trading_result=None, parent=None
    ):
        super().__init__(parent)
        self.candles = candles
        self.indicators = indicators
        self.pair = pair
        self.timeframe = timeframe
        self.trading_result = trading_result or []

    def run(self):
        self.process_status.emit("Rendering chart…")
        try:
            markers = []
            for r in self.trading_result:
                marker = _normalize_trade_marker(r)
                if marker:
                    markers.append(marker)
            html = ChartRenderer.build_html(
                self.candles, self.indicators, self.pair, self.timeframe, markers
            )
            self.process_ready.emit(
                {
                    "chart_html": html,
                    "indicators": self.indicators,
                    "pair": self.pair,
                    "timeframe": self.timeframe,
                }
            )
            self.process_status.emit("Chart rendered ✅")
        except Exception as e:
            self.process_error.emit(str(e))

    def stop(self):
        self.quit()
        self.wait(3000)


class ParallelPipeline(QThread):
    pipeline_done = Signal(dict)
    pipeline_error = Signal(str)
    pipeline_status = Signal(str)

    def __init__(self, exchange_mgr, pair, timeframe, trading_engine=None, parent=None):
        super().__init__(parent)
        self.exch_mgr = exchange_mgr
        self.pair = pair
        self.timeframe = timeframe
        self.trading_engine = trading_engine

    def run(self):
        self.pipeline_status.emit("Parallel pipeline…")
        fetch_r = {}
        calc_r = {}
        proc_r = {}
        f_err = None
        c_err = None
        p_err = None
        lock = threading.Lock()

        def _fetch():
            # Code Review 2.2: declare nonlocal so the outer scope's f_err
            # is mutated, not a new local.  Without this, pipeline_error
            # was effectively dead — errors only ever printed to console.
            nonlocal f_err
            try:
                c = self.exch_mgr.fetch_ohlcv(self.pair, self.timeframe, CANDLE_LIMIT)
                b = self.exch_mgr.fetch_wallet_coin("USDT")
                with lock:
                    fetch_r["candles"] = c
                    fetch_r["balance"] = b
                self.pipeline_status.emit("[Parallel] Fetch done")
            except Exception as e:
                f_err = str(e)
                print(f"Error {f_err}")

        def _calc():
            # Code Review 2.2: same fix for c_err.
            nonlocal c_err
            try:
                candles = []
                # Code Review 2.3: the original code had `break` followed
                # by `time.sleep(0.1)`, making the sleep unreachable.  The
                # loop spun through all 30 iterations with no delay when
                # data wasn't ready.  Move the sleep to the else branch
                # so we actually back off between polls.
                for _ in range(30):
                    with lock:
                        candles = fetch_r.get("candles", [])
                    if candles:
                        break
                    time.sleep(0.1)
                if not candles:
                    if ALLOW_MOCK_CANDLES:
                        candles = self.exch_mgr._mock_candles(
                            self.pair, self.timeframe, CANDLE_LIMIT
                        )
                    else:
                        # Code Review Medium #8: don't silently fall back to
                        # mock data in production.  Bubble the error up so
                        # the user sees "no candles" instead of fake trades.
                        c_err = "No candles fetched and APPV3_ALLOW_MOCKS not set"
                        return
                ind = IndicatorEngine.compute_all_indicators(candles)
                # ── Fix (duplicate PENDING marker): the pipeline NO LONGER
                #    calls TradingEngine.evaluate_signal.  Doing so here
                #    raced the main thread's _on_data_fetched path: both
                #    pushed pending/buy/sell markers, and the pipeline's
                #    2-candle loop (pending → confirm) left stale PENDING
                #    badges on the chart because the consume step ran
                #    inside the engine but the marker list still held
                #    the original pending entry.
                #
                #    The main thread (via _on_data_fetched →
                #    _add_pending_marker / _consume_pending_marker) is now
                #    the SOLE driver of PENDING/BUY/SELL markers.  The
                #    first trading decision happens on the next
                #    _refresh_pipelines tick (≤ refresh interval). ──
                with lock:
                    calc_r["indicators"] = ind
                    calc_r["candles"] = candles
                    calc_r["trading"] = []
                self.pipeline_status.emit("[Parallel] Calc done")
            except Exception as e:
                c_err = str(e)
                print(f"Error {c_err}")

        def _process():
            # Code Review 2.2: same fix for p_err.
            nonlocal p_err
            try:
                candles = []
                indicators = {}
                trading = []
                # Code Review 2.3: same busy-wait fix as _calc() — move
                # the sleep out from under the unreachable `break` path.
                for _ in range(60):
                    with lock:
                        candles = calc_r.get("candles", fetch_r.get("candles", []))
                        indicators = calc_r.get("indicators", {})
                        trading = calc_r.get("trading", [])
                    if candles and indicators:
                        break
                    time.sleep(0.2)
                # ── Fix: do NOT collect markers from the pipeline.  The
                #    engine state transitions (pending → confirm/reject)
                #    happen atomically inside evaluate_signal, but the
                #    marker list below would still contain the transient
                #    "pending" entry → duplicate PENDING badges on chart.
                #    Markers are managed solely by the main thread. ──
                html = ChartRenderer.build_html(
                    candles, indicators, self.pair, self.timeframe, []
                )
                fli_data = _compute_fli_data(candles)
                with lock:
                    proc_r["chart_html"] = html
                    proc_r["balance"] = fetch_r.get("balance", 0)
                    proc_r["candles"] = candles
                    proc_r["indicators"] = indicators
                    proc_r["fli_data"] = fli_data
                    proc_r["markers"] = []  # main thread owns markers now
                    proc_r["pair"] = self.pair
                    proc_r["timeframe"] = self.timeframe
                self.pipeline_status.emit("[Parallel] Process done")
            except Exception as e:
                p_err = str(e)
                print(f"Error {p_err}")

        t1 = threading.Thread(target=_fetch, daemon=True)
        t2 = threading.Thread(target=_calc, daemon=True)
        t3 = threading.Thread(target=_process, daemon=True)
        t1.start()
        t2.start()
        t3.start()
        t1.join(30)
        t2.join(30)
        t3.join(30)

        # Code Review Low #14: tag each error with the stage that produced
        # it so the user can see WHICH part of the pipeline failed
        # (previously the joined string was ambiguous — "fetch_ohlcv
        # timeout; list index out of range" didn't tell you which stage
        # raised the second error).
        stage_errors = []
        if f_err:
            stage_errors.append(f"[fetch] {f_err}")
        if c_err:
            stage_errors.append(f"[calc] {c_err}")
        if p_err:
            stage_errors.append(f"[process] {p_err}")
        if stage_errors:
            self.pipeline_error.emit("; ".join(stage_errors))
            return
        self.pipeline_done.emit(proc_r or fetch_r)
        self.pipeline_status.emit("Parallel pipeline ✅")

    def stop(self):
        self.quit()
        self.wait(3000)
