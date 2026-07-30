"""Background QThread workers for streaming, pair loading, and data pipelines."""

import asyncio
import json
import threading
import time

from PySide6.QtCore import QThread, Signal

from spotbot.constants import (
    ALLOW_MOCK_CANDLES,
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
    IndicatorEngine,
    _compute_fli_data,
    fli_compute_all_indicators,
    fli_ohlcv_to_df,
)
from spotbot.exchange import ExchangeManager
from spotbot.trading import TradingEngine , _normalize_trade_marker
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


class WalletBuyWorker(QThread):
    """QThread: fetch wallet buy trades from exchange and compute markers.

    Runs in the background so the UI never freezes during the potentially
    slow fetch_my_trades() call.
    """
    wallet_buys_ready = Signal(str, list, float, float)  # pair, chart_markers, total_qty, avg_price
    wallet_buys_error = Signal(str, str)  # pair, error_message

    def __init__(self, exchange_mgr, pair, parent=None):
        super().__init__(parent)
        self.exch_mgr = exchange_mgr
        self.pair = pair
        self._running = True

    def run(self):
        from datetime import datetime, timezone
        from spotbot.chart_renderer import _to_chart_time

        try:
            trades = self.exch_mgr.fetch_my_trades(self.pair)
        except Exception as e:
            self.wallet_buys_error.emit(self.pair, str(e))
            return

        if not trades:
            return

        buy_trades = []
        for t in sorted(trades, key=lambda x: x.get("timestamp") or x.get("datetime") or 0):
            side = str(t.get("side") or "").lower()
            if side != "buy":
                continue
            try:
                ts = int(t.get("timestamp") or t.get("datetime") or 0)
                price = float(t.get("price") or 0)
                qty = float(t.get("amount") or t.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0 or qty <= 0:
                continue
            try:
                dt = datetime.fromtimestamp(
                    ts / 1000 if ts > 1e10 else ts, tz=timezone.utc
                )
                date_str = dt.strftime("%m-%d %H:%M")
            except Exception:
                date_str = "?"
            buy_trades.append({"ts": ts, "price": price, "qty": qty, "date": date_str})

        if not buy_trades:
            return

        chart_markers = []
        for bt in buy_trades:
            chart_time = _to_chart_time(bt["ts"])
            if chart_time is None:
                continue
            chart_markers.append({
                "time": chart_time,
                "position": "belowBar",
                "color": "#e040fb",
                "shape": "arrowUp",
                "text": f"BUY {bt['date']} @ {bt['price']:.4f}",
                "size": 2,
            })

        total_qty = sum(bt["qty"] for bt in buy_trades)
        total_cost = sum(bt["price"] * bt["qty"] for bt in buy_trades)
        avg_price = total_cost / total_qty if total_qty > 0 else 0

        panel_buys = [
            {"date": bt["date"], "price": bt["price"], "qty": bt["qty"]}
            for bt in buy_trades
        ]

        self.wallet_buys_ready.emit(self.pair, chart_markers, total_qty, avg_price)

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)


class BacktestWorker(QThread):
    """QThread: run CombinedMomFliHullStrategy backtest in background.

    Uses backtest_combined_strategy (via backtesting.py) on a DataFrame
    and emits markers + stats.
    """
    backtest_ready = Signal(str, object, object, str)  # pair, markers, stats, duration_str
    backtest_error = Signal(str, str)  # pair, error_message

    def __init__(self, pair, df, fli_params, investment=10.0, parent=None):
        super().__init__(parent)
        self.pair = pair
        self.df = df
        self.fli_params = fli_params
        self.investment = investment
        self._running = True

    def run(self):
        import time as _time

        t0 = _time.time()
        try:
            if not self._running:
                return

            from spotbot.indicators import backtest_combined_strategy

            result = backtest_combined_strategy(self.df, self.investment)
            if not self._running:
                return

            elapsed = _time.time() - t0
            if elapsed < 1:
                duration_str = f"{elapsed*1000:.0f}ms"
            elif elapsed < 60:
                duration_str = f"{elapsed:.1f}s"
            else:
                m, s = divmod(elapsed, 60)
                duration_str = f"{int(m)}m {int(s)}s"

            self.backtest_ready.emit(
                self.pair,
                result.get("markers", []),
                result.get("stats", {}),
                duration_str,
            )
        except Exception as e:
            if self._running:
                self.backtest_error.emit(self.pair, str(e))

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)


class BestTimeframeWorker(QThread):
    """QThread: test all timeframes and find the one with best Equity Final/Peak.

    For each timeframe:
      1. Fetch OHLCV candles
      2. Compute FLI indicators
      3. Run backtest
      4. Record equity_final, equity_peak, win_rate

    Emits progress for each timeframe tested and the final best result.
    """
    tf_progress = Signal(str, str, float, float)  # pair, tf, equity_final, equity_peak
    tf_complete = Signal(str, str, float, float, object)  # pair, best_tf, best_eq_final, best_eq_peak, all_results
    tf_error = Signal(str, str)  # pair, error_message

    TIMEFRAMES = ["3m", "5m", "15m", "30m", "1h"]

    def __init__(self, exchange_mgr, pair, fli_params, investment=10.0, parent=None):
        super().__init__(parent)
        self.exch_mgr = exchange_mgr
        self.pair = pair
        self.fli_params = fli_params
        self.investment = investment
        self._running = True

    def run(self):
        try:
            from spotbot.constants import CANDLE_LIMIT
            from spotbot.indicators import (
                fli_ohlcv_to_df,
                backtest_combined_strategy,
            )

            all_results = []
            best_tf = ""
            best_eq_final = -1.0
            best_eq_peak = -1.0

            for tf in self.TIMEFRAMES:
                if not self._running:
                    break

                try:
                    candles = self.exch_mgr.fetch_ohlcv(self.pair, tf, CANDLE_LIMIT)
                except Exception as e:
                    self.tf_error.emit(self.pair, f"Fetch {tf}: {e}")
                    continue

                if not candles or len(candles) < 30:
                    self.tf_error.emit(self.pair, f"Skip {tf}: < 30 candles")
                    continue

                try:
                    df = fli_ohlcv_to_df(candles)
                    # New strategy computes its own indicators internally
                    result = backtest_combined_strategy(df, self.investment)
                    stats = result.get("stats", {})
                    eq_final = stats.get("equity_final", 0)
                    eq_peak = stats.get("equity_peak", 0)
                    all_results.append({
                        "timeframe": tf,
                        "equity_final": eq_final,
                        "equity_peak": eq_peak,
                        "win_rate": stats.get("win_rate", 0),
                        "total_trades": stats.get("total_trades", 0),
                        "total_pnl_pct": stats.get("total_pnl_pct", 0),
                    })

                    self.tf_progress.emit(self.pair, tf, eq_final, eq_peak)

                    # Score: prioritize equity_final, then equity_peak
                    score = eq_final * 1.0 + eq_peak * 0.5
                    if eq_final > best_eq_final or (
                        eq_final == best_eq_final and eq_peak > best_eq_peak
                    ):
                        best_eq_final = eq_final
                        best_eq_peak = eq_peak
                        best_tf = tf

                except Exception as e:
                    self.tf_error.emit(self.pair, f"Backtest {tf}: {e}")
                    continue

            self.tf_complete.emit(
                self.pair, best_tf, best_eq_final, best_eq_peak, all_results
            )

        except Exception as e:
            self.tf_error.emit(self.pair, str(e))

    def stop(self):
        self._running = False
        self.quit()
        self.wait(5000)


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


class SimulationWorker(QThread):
    """QThread: generates realistic candles via CandleSimulator and emits
    them at a user-controllable speed.

    Two phases:
      1. **History** — emits a batch of historical candles (200+) so the
         chart and indicators can be seeded immediately.
      2. **Live** — emits one new candle every ``interval_ms`` milliseconds
         (configurable via set_interval), simulating real-time streaming.

    Signals mirror ``DataFetchWorker`` so the MainWindow can treat both
    identically — only the data source differs.
    """
    # Emitted when the initial history batch is ready
    history_ready = Signal(str, list, float)   # pair, candles, balance
    # Emitted for each new candle (same shape as WebSocketWorker.candle_update)
    candle_update = Signal(list)                 # [ts, o, h, l, c, v]
    # Status / error
    sim_status = Signal(str)
    sim_error = Signal(str)

    def __init__(self, pair: str, base_price: float, timeframe: str,
                 parent=None):
        super().__init__(parent)
        self.pair = pair
        self.base_price = base_price
        self.timeframe = timeframe
        self._interval_ms = 500        # default: new candle every 500 ms
        self._running = True
        self._paused = False

    def set_interval(self, ms: int):
        """Change the candle generation interval (speed control)."""
        self._interval_ms = max(50, min(60_000, ms))

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def run(self):
        from spotbot.candle_simulator import CandleSimulator, TF_SECONDS

        tf_sec = TF_SECONDS.get(self.timeframe, 180)
        sim = CandleSimulator(
            base_price=self.base_price,
            timeframe_sec=tf_sec,
        )

        # ── Phase 1: generate and emit history ──
        try:
            history = sim.generate_history(count=200)
            self.history_ready.emit(self.pair, history, 10_000.0)
            self.sim_status.emit(
                f"[Simulator] {self.pair}: {len(history)} history candles ready"
            )
        except Exception as e:
            self.sim_error.emit(f"History generation failed: {e}")
            return

        # ── Phase 2: stream new candles at configured interval ──
        while self._running:
            if self._paused:
                self.msleep(500)
                continue
            try:
                candle = sim.next_candle()
                self.candle_update.emit(candle)
            except Exception as e:
                self.sim_error.emit(f"Candle generation error: {e}")
            self.msleep(self._interval_ms)

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)


class SimCandleProcessWorker(QThread):
    """QThread: offloads indicator computation + trading signal evaluation
    for simulation candle updates.

    Without this worker, every simulated candle triggers
    ``IndicatorEngine.compute_all_indicators`` + ``evaluate_signal`` on the
    **main thread**, which freezes the UI at high simulation speeds.

    This worker runs all heavy computation in the background and emits a
    ready-to-use ``enriched`` dict that the main thread can push to the
    chart with minimal work (a single JS call).
    """
    process_done = Signal(str, dict)   # pair, enriched_data
    process_error = Signal(str, str)   # pair, error_message

    def __init__(
        self,
        pair: str,
        candles: list,
        trading_engine: "TradingEngine",
        balance: float = 10_000.0,
        markers: list | None = None,
        timeframe: str = "5m",
        parent=None,
    ):
        super().__init__(parent)
        self.pair = pair
        self.candles = candles
        self.trading_engine = trading_engine
        self.balance = balance
        self.markers = markers or []
        self.timeframe = timeframe
        self._running = True

    def run(self):
        if not self._running:
            return
        try:
            # 1. Compute indicators (the heaviest part — TA-Lib calls)
            indicators = IndicatorEngine.compute_all_indicators(self.candles)
            if not self._running:
                return

            # 2. Evaluate trading signal
            signal_result = None
            if len(self.candles) >= 2:
                try:
                    signal_result = self.trading_engine.evaluate_signal(
                        indicators, self.candles[-1], self.candles
                    )
                except Exception:
                    pass  # Non-fatal: chart still updates
            if not self._running:
                return

            # 3. Build the enriched dict (same shape _on_chart_ready expects)
            enriched = {
                "candles": self.candles,
                "fli_data": None,
                "indicators": indicators,
                "balance": self.balance,
                "markers": self.markers,
                "pair": self.pair,
                "timeframe": self.timeframe,
                "signal_result": signal_result,  # extra: for main thread to handle
            }
            self.process_done.emit(self.pair, enriched)

        except Exception as e:
            if self._running:
                self.process_error.emit(self.pair, str(e))

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)
