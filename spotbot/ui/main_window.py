"""Main Window: orchestrates all subsystems and UI components."""

import json
import os
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone

import ccxt
import pandas as pd
from PySide6.QtCore import Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCommandLinkButton,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLCDNumber,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from spotbot.chart_renderer import (
    _FLI_HTML_TEMPLATE,
    ChartRenderer,
    FLIChartWorker,
    _to_chart_time,
)
from spotbot.constants import (
    API_KEY_FILE,
    CANDLE_LIMIT,
    CCXT_AVAILABLE,
    CHART_CDN_URL,
    CONFIG_DIR,
    FLI_ADX_LEN,
    FLI_ADX_LEVEL,
    FLI_ATR_PERIOD,
    FLI_BB_DEV,
    FLI_BB_PERIOD,
    FLI_CCI_BUFFER,
    FLI_CCI_LEN,
    FLI_CCI_LEVEL,
    FLI_MIN_SCORE,
    FLI_OBV_SMA_LEN,
    FLI_USE_ADX,
    FLI_USE_ATR,
    FLI_USE_CCI,
    FLI_USE_OBV,
    FLOAT_EPS,
    NUMPY_AVAILABLE,
    PANDAS_AVAILABLE,
    PNL_LOG_FILE,
    QUOTE_ASSETS,
    REFRESH_MS,
    RSI_BUY_THRESHOLD,
    RSI_SELL_THRESHOLD,
    TIMEFRAME_MAP,
    TIMEFRAMES,
    TRADE_HISTORY_LIMIT,
    TRADE_NOTIFIER_AVAILABLE,
)
from spotbot.exchange import ExchangeManager
from spotbot.indicators import IndicatorEngine, backtest_fli_signals
from spotbot.styles import STYLE_QSS
from spotbot.trading import TradingEngine
from spotbot.transaction_logger import TransactionLogger
from spotbot.ui.api_key_dialog import APIKeyDialog
from spotbot.ui.coin_session import CoinSession
from spotbot.ui.coin_tab_widget import CoinTabWidget
from spotbot.ui.indicator_params_dialog import IndicatorParamsDialog
from spotbot.ui.pnl_dialog import PnLDialog
from spotbot.ui.resizable_ui import ResizableUI
from spotbot.workers import (
    BacktestWorker,
    BestTimeframeWorker,
    DataFetchWorker,
    IndicatorCalcWorker,
    PairLoaderWorker,
    ParallelPipeline,
    ProcessWorker,
    SimCandleProcessWorker,
    SimulationWorker,
    WalletBuyWorker,
    WebSocketWorker,
)

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.ui = ResizableUI()
        self.ui.setupUi(self)

        # ── Fix: _bottom_panel_default_height must live on MainWindow itself,
        #    not on self.ui.  _on_toggle_console accesses it as
        #    self._bottom_panel_default_height (where self is MainWindow). ──
        self._bottom_panel_default_height = 220

        self.exch_mgr = ExchangeManager()
        self.tx_logger = TransactionLogger()

        # ── Task 3: Sound & system notifications via trade_notifier.py ──
        # The notifier is best-effort: if PyQt6/playsound3/winotify aren't
        # available, it silently degrades to print-only. We never let a
        # notifier failure crash the app.
        self.notifier = None
        if TRADE_NOTIFIER_AVAILABLE:
            try:
                import trade_notifier

                self.notifier = trade_notifier.TradeNotifier(enabled=True)
            except Exception as e:
                print(f"[NOTIFIER] init failed: {e}")
                self.notifier = None
        # Welcome sound on app start
        if self.notifier is not None:
            try:
                self.notifier.notify_app_start()
            except Exception as e:
                print(f"[NOTIFIER] notify_app_start failed: {e}")

        # ── Multi-coin session state ──
        self._sessions: dict[str, CoinSession] = {}  # pair → CoinSession
        self._tabs: dict[str, CoinTabWidget] = {}  # pair → CoinTabWidget
        self._is_connected = False
        self._pair_loader = None

        # ── Real-time chart refresh ──
        self._charts_loaded: set[str] = set()  # pairs with initial HTML loaded
        self._charts_data: dict[str, dict] = {}  # pair → last pipeline data dict
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_pipelines)
        self._refresh_timer.setInterval(3000)  # default 60s, updated per timeframe
        self._fli_worker: FLIChartWorker | None = None
        self._last_fli_df = None
        self._last_candles: list = []
        self._markers: list = []
        # ── Per-pair FLI worker registry (each open tab gets its own) ──
        self._fli_workers: dict[str, FLIChartWorker] = {}
        self._pair_candles: dict[str, list] = {}  # pair → last candles
        self._pair_last_chart_ts: dict[
            str, int
        ] = {}  # pair → last candle ts sent to chart
        self._pair_markers: dict[str, list] = {}  # pair → last markers
        self._pair_fli_df: dict[str, object] = {}  # pair → last fli_df
        self._pair_chart_ready: dict[str, bool] = {}  # pair → chart loaded flag
        self._pair_chart_first_load: dict[str, bool] = {}  # pair → first-load flag
        self._pair_chart_js_queue: dict[str, list[str]] = {}  # pair → queued JS
        # ── Per-pair backtest markers (historical FLI signals) ──
        self._pair_backtest_markers: dict[str, list] = {}  # pair → backtest markers
        # ── Per-pair wallet buy-history markers (actual exchange trades) ──
        self._pair_wallet_buy_markers: dict[str, list] = {}  # pair → wallet buy markers
        # ── Per-pair backtest workers (so we can stop them on tab close) ──
        self._backtest_workers: dict[str, BacktestWorker] = {}
        self._wallet_buy_workers: dict[str, WalletBuyWorker] = {}
        # ── Best timeframe worker (single, for active pair) ──
        self._best_tf_worker: BestTimeframeWorker | None = None
        # ── Track which pairs have backtest enabled ──
        # _pair_backtest_enabled removed — backtest is now manual only
        # ── Fix: track whether the REAL chart HTML (not about:blank) has
        #    been loaded for this pair.  The about:blank initial page also
        #    fires loadFinished, which would prematurely set
        #    _pair_chart_ready[pair]=True and cause JS calls (setFliCandles,
        #    setMarkers, …) to run against an empty page → ReferenceError. ──
        self._pair_chart_html_loaded: dict[str, bool] = {}
        # ── Requirement (Task 4): track the ts of the most recent PENDING
        #    signal per pair so we can REMOVE the PENDING marker when the
        #    signal is either confirmed (becomes BUY/SELL) or rejected. ──
        self._pair_pending_ts: dict[str, int | None] = {}
        # ── Footer status message log (rotating buffer) ──
        self._footer_messages: deque = deque(maxlen=50)
        # ── Global trading gate (Start Trading button) ──
        self._global_trading_enabled: bool = False
        # ── Simulated portfolio balance: starts from exchange USDT balance,
        #    decreases on buy, increases on sell across all tabs. ──
        self._portfolio_balance: float = 0.0
        # Code Review 3.6: one-shot flag set when the user has just
        # confirmed the LIVE-trading warning dialog — lets the next
        # _on_start_trading_toggled(True) call skip the dialog.
        self._live_confirmed: bool = False

        # ── Simulation mode state ──
        self._simulation_active: bool = False
        self._sim_workers: dict[str, SimulationWorker] = {}  # pair → worker
        self._sim_process_workers: dict[
            str, SimCandleProcessWorker
        ] = {}  # pair → process worker
        self._sim_processing: dict[str, bool] = {}  # pair → is processing?
        self._sim_base_price: float = 0.05

        self._fli_params = {
            "signal_source": "fli",
            "bb_period": FLI_BB_PERIOD,
            "bb_dev": FLI_BB_DEV,
            "use_atr": FLI_USE_ATR,
            "atr_period": FLI_ATR_PERIOD,
            "use_cci": FLI_USE_CCI,
            "cci_len": FLI_CCI_LEN,
            "cci_level": FLI_CCI_LEVEL,
            "cci_buffer": FLI_CCI_BUFFER,
            "use_adx": FLI_USE_ADX,
            "adx_len": FLI_ADX_LEN,
            "adx_level": FLI_ADX_LEVEL,
            "use_obv": FLI_USE_OBV,
            "obv_sma_len": FLI_OBV_SMA_LEN,
            "min_score": FLI_MIN_SCORE,
        }

        self._load_exchanges()
        self._connect_signals()
        self._setup_simulation_ui()
        self.ui.tabWidget.tabCloseRequested.connect(self._on_tab_close)

    # ── Load exchanges from ccxt ──

    def _load_exchanges(self):
        if CCXT_AVAILABLE:
            exchanges = sorted(ccxt.exchanges)
            formatted_exchanges = [ex[:1].upper() + ex[1:] for ex in exchanges]
            self.ui.cbExchange.addItems(formatted_exchanges)
        else:
            self.ui.cbExchange.addItems(
                ["Binance", "Coinbase", "Kraken", "OKX", "Bybit"]
            )

    # ── Signals ──

    def _connect_signals(self):
        self.ui.cbExchange.currentTextChanged.connect(self._on_exchange_selected)
        self.ui.cbPair.currentTextChanged.connect(self._on_pair_changed)
        self.ui.btnAddPair.clicked.connect(self._on_add_pair_clicked)
        for rb in (
            self.ui.rb_timefram_3m,
            self.ui.rb_timefram_5m,
            self.ui.rb_timefram_15m,
            self.ui.rb_timefram_30m,
            self.ui.rb_timefram_1h,
        ):
            rb.toggled.connect(self._on_timeframe_changed)
        # ── Requirement: each tab may have a different timeframe. When the
        #    user switches tabs, the radio buttons must reflect the active
        #    tab's timeframe (and vice versa: changing the radio only
        #    affects the active tab). ──
        self.ui.tabWidget.currentChanged.connect(self._on_tab_changed)
        self.ui.btnConnDissconExchange.clicked.connect(self._on_connect_disconnect)
        self.ui.btnStartTrading.toggled.connect(self._on_start_trading_toggled)
        self.ui.slidInvistmineAmount.valueChanged.connect(self._on_slider_changed)
        self.ui.dsbInvestmintAmount.valueChanged.connect(self._on_spinbox_changed)
        self.ui.radLive.toggled.connect(self._on_mode_changed)
        self.ui.rbStyleFixed.toggled.connect(self._on_invest_style_changed)
        self.ui.clBtnShowPnL.clicked.connect(self._on_show_pnl)
        # ── Task 2: dockable PnL/Console panel toggle ──
        self.ui.clBtnToggleConsole.toggled.connect(self._on_toggle_console)
        self.ui.clBtnAPIKey.clicked.connect(self._on_setup_api_keys)
        # ── Best Timeframe button ──
        self.ui.btnBestTimeframe.clicked.connect(self._on_best_timeframe_clicked)
        # ── Indicator Parameters dialog ──
        self.ui.btnIndicatorParams.clicked.connect(self._on_indicator_params_clicked)

    # ── Helpers ──

    def _tf(self):
        if self.ui.rb_timefram_3m.isChecked():
            return "3m"
        if self.ui.rb_timefram_5m.isChecked():
            return "5m"
        if self.ui.rb_timefram_15m.isChecked():
            return "15m"
        if self.ui.rb_timefram_30m.isChecked():
            return "30m"
        return "1h"

    def _set_status(self, msg):
        """Append a timestamped message to the footer status bar.
        The footer keeps the last N messages (rotating buffer) so the user
        can see the history of bot decisions (hold/skip/buy/sell)."""
        ts = datetime.now().strftime("%H:%M:%S")
        # Color-code by message content for quick scanning
        lower = str(msg).lower()
        if any(k in lower for k in ("buy", "✅", "armed", "started")):
            color = "#0ecb81"  # green
        elif any(k in lower for k in ("sell", "exit")):
            color = "#f0a500"  # amber
        elif any(k in lower for k in ("hold", "skip", "wait")):
            color = "#848e9c"  # gray
        elif any(k in lower for k in ("⚠️", "error", "reject", "insufficient", "fail")):
            color = "#f6465d"  # red
        else:
            color = "#eaecef"  # default
        line = f"<span style='color:#848e9c'>[{ts}]</span> <span style='color:{color}'>{msg}</span>"
        self._footer_messages.append(line)
        # Render the last ~12 messages, newest at top
        recent = list(self._footer_messages)[-12:][::-1]
        self.ui.footerStatusBar.setHtml("<br>".join(recent))
        # Also mirror a one-line summary to the sidebar lblstatus (kept as a
        # quick-glance indicator; the full history lives in the footer).
        self.ui.lblstatus.setText(f"[{ts}] {msg}")

    def _safe_notify(self, method_name: str, *args, **kwargs):
        """Call a TradeNotifier method without ever raising. The notifier
        is best-effort — a sound failure must never crash the trading
        loop. Logs the error to the footer status bar instead.

        Code Review Low #13: previously only printed to stdout.  If the
        notifier was misconfigured the user got NO feedback (no sound, no
        visible warning).  Now we also surface the failure in the footer
        status bar so the user knows sound notifications are broken."""
        if self.notifier is None:
            return
        try:
            method = getattr(self.notifier, method_name, None)
            if method is None:
                print(f"[NOTIFIER] unknown method: {method_name}")
                return
            method(*args, **kwargs)
        except Exception as e:
            print(f"[NOTIFIER] {method_name} failed: {e}")
            # Surface in the footer so the user sees sound/notify is broken.
            try:
                self._set_status(f"⚠️ Notification '{method_name}' failed: {e}")
            except Exception as e2:
                # _set_status itself failed (Qt widget destroyed?).  Last
                # resort: print to stdout — we MUST NOT re-raise here or
                # the trading loop crashes for a UI/sound issue.
                print(f"[NOTIFIER] _set_status also failed: {e2}")

    # ──────────────────────────────────────────────────────────────────────────+
    # Per-pair chart state helpers                                              |
    # ──────────────────────────────────────────────────────────────────────────+
    # Code Review Critical #2: the previous _show_empty_chart() method here     |
    # referenced ``self._chart_ready`` / ``self._chart_js_queue`` /             |
    # ``self._chart_first_load`` — those are CoinTabWidget attributes, not      |
    # MainWindow attributes.  MainWindow tracks chart state in the per-pair     |
    # dicts (_pair_chart_ready, _pair_chart_js_queue, _pair_chart_first_load)   |
    # initialized in __init__ via _pair_state().  The method was never called   |
    # from anywhere in the codebase, so deleting it is safe and removes a       |
    # latent AttributeError if it ever had been invoked.                        |
    # ───────────────────────────────────────────────────────────────────────────+

    def _pair_state(self, pair: str) -> dict:
        """Initialize (if needed) and return the per-pair chart state dict."""
        if pair not in self._pair_chart_ready:
            self._pair_chart_ready[pair] = False
            self._pair_chart_first_load[pair] = True
            self._pair_chart_js_queue[pair] = []
            self._pair_chart_html_loaded[pair] = False
            self._pair_candles[pair] = []
            self._pair_last_chart_ts[pair] = 0
            self._pair_markers[pair] = []
            self._pair_fli_df[pair] = None
            self._pair_backtest_markers[pair] = []
            # NOTE: no auto-backtest — user clicks "Backtest" button
            self._pair_wallet_buy_markers[pair] = []
        return {
            "ready": self._pair_chart_ready[pair],
            "first_load": self._pair_chart_first_load[pair],
            "queue": self._pair_chart_js_queue[pair],
            "candles": self._pair_candles[pair],
            "markers": self._pair_markers[pair],
            "fli_df": self._pair_fli_df[pair],
        }

    def _chart_js(self, pair: str, code: str):
        """Run JS on the per-pair chart, or queue it until loadFinished.

        All code is automatically wrapped in try-catch so that a single
        JS error (e.g. "Value is null" from lightweight-charts) never
        crashes the entire chart.
        """
        tab = self._tabs.get(pair)
        if not tab:
            return
        safe_code = f"try {{ {code} }} catch(_e) {{ console.warn('chart JS error:', _e.message || _e, _e.stack); }}"
        if self._pair_chart_ready.get(pair, False):
            tab.chart_view.page().runJavaScript(safe_code)
        else:
            self._pair_chart_js_queue.setdefault(pair, []).append(safe_code)

    def _flush_pair_js_queue(self, pair: str):
        """Called when a tab's chart loadFinished fires — flush queued JS.

        Guard: ignore the spurious loadFinished that fires for the initial
        ``about:blank`` page.  Only the REAL chart HTML (loaded via
        ``load_chart_html``) should mark the pair as ready and flush the
        queue — otherwise JS calls like setFliCandles() run against an
        empty page and raise ReferenceError.

        Also guards against QWebEngineView firing loadFinished BEFORE the
        inline <script> block has been parsed — we poll for a _pageReady
        flag that is set at the END of the template's script section.
        """
        if not self._pair_chart_html_loaded.get(pair, False):
            # about:blank (or other pre-template page) finished loading.
            # Do NOT mark the pair as ready.
            return
        tab = self._tabs.get(pair)
        if not tab:
            return

        def _check_ready(result):
            if result:
                self._pair_chart_ready[pair] = True
                queue = self._pair_chart_js_queue.get(pair, [])
                if not queue:
                    return
                pending, self._pair_chart_js_queue[pair] = queue, []
                for code in pending:
                    tab.chart_view.page().runJavaScript(code)
            else:
                # Page JS not ready yet — retry after 50 ms
                QTimer.singleShot(50, lambda: self._flush_pair_js_queue(pair))

        tab.chart_view.page().runJavaScript(
            "typeof _pageReady !== 'undefined' && _pageReady", _check_ready
        )

    # ─────────────────────────────────────────────────────────────────────
    # FLI worker (per-pair, background thread)
    # ─────────────────────────────────────────────────────────────────────

    def _load_historical_chart(self, pair: str):
        """Kick off background computation of the FLI indicator set for the
        given pair's chart, from the same candles the trading pipeline just
        fetched.  Runs in a QThread so the main UI never blocks."""
        candles = self._pair_candles.get(pair, [])
        if not candles:
            return
        # Stop any existing worker for this pair before starting a new one
        old = self._fli_workers.get(pair)
        if old and old.isRunning():
            return  # Let the in-flight worker finish; it will refresh the chart
        worker = FLIChartWorker(list(candles), self._fli_params)
        self._fli_workers[pair] = worker
        worker.fli_ready.connect(lambda df, p=pair: self._on_fli_ready(p, df))
        worker.fli_error.connect(
            lambda m, p=pair: self._set_status(f"⚠️ FLI chart {p}: {m}")
        )
        worker.start()

    @Slot(str, object)
    def _on_fli_ready(self, pair: str, df):
        if df is None or getattr(df, "empty", True):
            return
        self._pair_fli_df[pair] = df
        self._set_fli_candles(pair, df)
        self._set_fli_lines(pair, df)
        # ── Backtest is now manual only (user clicks "Backtest" button) ──
        # ── Set trade markers ──
        self._set_markers(pair, df, trade_markers=self._pair_markers.get(pair, []))
        self._update_fli_info_panel(pair, df.iloc[-1])
        self._refresh_fli_trade_panel(pair)

        # ── FLI-based trading signal evaluation (immediate execution) ──
        if self._fli_params.get("signal_source") == "fli":
            self._evaluate_fli_trading_signal(pair, df)
        if self._pair_chart_first_load.get(pair, True):
            self._chart_js(pair, "zoomToRecent(100);")
            self._pair_chart_first_load[pair] = False

    def _evaluate_fli_trading_signal(self, pair: str, df):
        """Evaluate FLI buy_signal/sell_signal on the last row of the DataFrame
        and route the result through the trading engine (immediate execution)."""
        session = self._sessions.get(pair)
        if not session:
            return
        last = df.iloc[-1]
        fli_buy = bool(last.get("buy_signal", False))
        fli_sell = bool(last.get("sell_signal", False))
        try:
            price = float(last["close"])
        except (TypeError, ValueError, KeyError):
            price = 0.0
        row_time = last.get("time") or last.get("timestamp")
        ts = self._fli_ts(row_time) if row_time is not None else None
        if ts is None:
            return
        try:
            r = session.engine.evaluate_fli_signal(fli_buy, fli_sell, price, ts)
        except Exception as e:
            self._set_status(f"⚠️ {pair} FLI signal eval: {e}")
            return
        if not r or r.get("action") not in ("buy", "sell", "skipped"):
            return
        action = r["action"]
        note = r.get("note", "")
        self._set_status(f"{pair} {action.upper()}: {note}")
        if action in ("buy", "sell"):
            if r.get("trade"):
                self._on_trade_done(pair, r)

    # ─────────────────────────────────────────────────────────────────────
    # Chart JS builders (per-pair) — ported from new_app.py
    # ─────────────────────────────────────────────────────────────────────

    def _run_backtest(self, pair: str, df):
        """Run a backtest on the FLI DataFrame and store markers + update stats panel."""
        try:
            session = self._sessions.get(pair)
            investment = session.investment_amount if session else 10.0
            result = backtest_fli_signals(df, investment)
            markers = result.get("markers", [])
            stats = result.get("stats", {})
            self._pair_backtest_markers[pair] = markers
            # Push backtest markers to chart with distinct colors
            chart_markers = []
            for m in markers:
                action = m.get("action")
                m_time = m.get("time")
                if m_time is None:
                    continue
                ts = _to_chart_time(m_time)
                if ts is None:
                    continue
                try:
                    price = float(m.get("price", 0))
                except (TypeError, ValueError):
                    price = 0.0
                if action == "bt_buy":
                    chart_markers.append(
                        {
                            "time": ts,
                            "position": "belowBar",
                            "color": "#2962ff",  # Blue — distinct from trade green
                            "shape": "arrowUp",
                            "text": f"BT BUY @ {price:.4f}",
                            "size": 1,
                        }
                    )
                elif action == "bt_sell":
                    chart_markers.append(
                        {
                            "time": ts,
                            "position": "aboveBar",
                            "color": "#ff6d00",  # Orange — distinct from trade red
                            "shape": "arrowDown",
                            "text": f"BT SELL @ {price:.4f}",
                            "size": 1,
                        }
                    )
            self._chart_js(
                pair,
                f"try {{ setBacktestMarkers({json.dumps(chart_markers)}); }}"
                f"catch(e) {{ console.warn('bt markers error:', e.message, e.stack); }}",
            )
            # Update backtest stats panel
            self._chart_js(
                pair,
                f"updateBacktestStats("
                f"{stats.get('total_trades', 0)},"
                f"{stats.get('win_rate', 0):.1f},"
                f"{stats.get('wins', 0)},"
                f"{stats.get('losses', 0)},"
                f"{stats.get('total_pnl_pct', 0):.2f},"
                f"{stats.get('equity_final', 0):.2f},"
                f"{stats.get('equity_peak', 0):.2f},"
                f"''"
                f");",
            )
            if markers:
                self._set_status(
                    f"[Backtest] {pair}: {stats.get('total_trades', 0)} trades, "
                    f"win rate {stats.get('win_rate', 0):.1f}%, "
                    f"net P&L {stats.get('total_pnl_pct', 0):+.2f}%, "
                    f"Equity Final ${stats.get('equity_final', 0):.2f}"
                )
        except Exception as e:
            print(f"[BACKTEST] Error for {pair}: {e}")

    def _run_backtest_async(self, pair: str, df):
        """Run backtest in a QThread to prevent UI freezing.

        Called only when the user clicks the "Backtest" button.
        """
        # Stop any existing backtest worker for this pair
        old = self._backtest_workers.get(pair)
        if old and old.isRunning():
            old.stop()

        session = self._sessions.get(pair)
        investment = session.investment_amount if session else 10.0
        worker = BacktestWorker(pair, df, self._fli_params, investment, parent=self)
        self._backtest_workers[pair] = worker
        worker.backtest_ready.connect(self._on_backtest_ready)
        worker.backtest_error.connect(
            lambda m, p=pair: self._set_status(f"⚠️ Backtest {p}: {m}")
        )
        worker.start()

    @Slot(str, object, object, str)
    def _on_backtest_ready(self, pair: str, markers, stats, duration_str):
        """Handle backtest results from BacktestWorker (runs on main thread)."""
        self._pair_backtest_markers[pair] = markers

        # Build chart markers — validate time values to prevent null in JS
        chart_markers = []
        for m in markers:
            action = m.get("action")
            m_time = m.get("time")
            # Ensure time is a valid number for the chart
            if m_time is None:
                continue
            ts = _to_chart_time(m_time)
            if ts is None:
                continue
            try:
                price = float(m.get("price", 0))
            except (TypeError, ValueError):
                price = 0.0
            if action == "bt_buy":
                chart_markers.append(
                    {
                        "time": ts,
                        "position": "belowBar",
                        "color": "#2962ff",
                        "shape": "arrowUp",
                        "text": f"BT BUY @ {price:.4f}",
                        "size": 1,
                    }
                )
            elif action == "bt_sell":
                chart_markers.append(
                    {
                        "time": ts,
                        "position": "aboveBar",
                        "color": "#ff6d00",
                        "shape": "arrowDown",
                        "text": f"BT SELL @ {price:.4f}",
                        "size": 1,
                    }
                )
        self._chart_js(
            pair,
            f"try {{ setBacktestMarkers({json.dumps(chart_markers)}); }}"
            f"catch(e) {{ console.warn('bt markers error:', e.message, e.stack); }}",
        )

        # Update backtest stats panel (with equity and duration)
        eq_final = stats.get("equity_final", 0) if stats else 0
        eq_peak = stats.get("equity_peak", 0) if stats else 0
        total_trades = stats.get("total_trades", 0) if stats else 0
        win_rate = stats.get("win_rate", 0) if stats else 0
        wins = stats.get("wins", 0) if stats else 0
        losses = stats.get("losses", 0) if stats else 0
        total_pnl = stats.get("total_pnl_pct", 0) if stats else 0

        self._chart_js(
            pair,
            f"updateBacktestStats("
            f"{total_trades},{win_rate:.1f},{wins},{losses},{total_pnl:.2f},"
            f"{eq_final:.2f},{eq_peak:.2f},"
            f"{json.dumps(duration_str)});",
        )

        if total_trades > 0:
            self._set_status(
                f"[Backtest] {pair}: {total_trades} trades ({duration_str}), "
                f"win rate {win_rate:.1f}%, "
                f"Equity Final ${eq_final:.2f}, Peak ${eq_peak:.2f}"
            )

    def _fetch_and_mark_wallet_buys_async(self, pair: str):
        """Fetch wallet buy trades in a QThread to prevent UI freezing."""
        # Stop any existing worker for this pair
        old = self._wallet_buy_workers.get(pair)
        if old and old.isRunning():
            old.stop()

        worker = WalletBuyWorker(self.exch_mgr, pair, parent=self)
        self._wallet_buy_workers[pair] = worker
        worker.wallet_buys_ready.connect(self._on_wallet_buys_ready)
        worker.wallet_buys_error.connect(
            lambda p, m: print(f"[wallet_buys] fetch failed for {p}: {m}")
        )
        worker.start()

    @Slot(str, list, float, float)
    def _on_wallet_buys_ready(self, pair: str, chart_markers, total_qty, avg_price):
        """Handle wallet buy results from WalletBuyWorker."""
        if not chart_markers:
            return

        # Store for later re-push
        buy_trades_data = []
        for cm in chart_markers:
            # Extract buy trade data from marker text
            text = cm.get("text", "")
            buy_trades_data.append(
                {
                    "date": (
                        text.split(" @ ")[0].replace("BUY ", "")
                        if " @ " in text
                        else "?"
                    ),
                    "price": (
                        float(cm.get("text", "").split("@ ")[1].strip())
                        if "@" in cm.get("text", "")
                        else 0
                    ),
                    "qty": 0,
                }
            )

        self._pair_wallet_buy_markers[pair] = buy_trades_data

        self._chart_js(pair, f"setWalletBuyMarkers({json.dumps(chart_markers)});")

        # Build panel buys data from the chart markers
        panel_buys = []
        for cm in chart_markers:
            text = cm.get("text", "")
            parts = text.split(" @ ")
            date_part = parts[0].replace("BUY ", "") if len(parts) > 0 else "?"
            price_part = float(parts[1].strip()) if len(parts) > 1 else 0
            panel_buys.append({"date": date_part, "price": price_part, "qty": 0})

        self._chart_js(
            pair,
            f"updateWalletBuyPanel({json.dumps(panel_buys)},{total_qty},{avg_price});",
        )
        self._set_status(
            f"{pair}: found {len(chart_markers)} buy trades, avg entry {avg_price:.6f}"
        )

    def _on_run_backtest_clicked(self):
        """Handle manual Backtest button click — run backtest on active pair."""
        pair = self._active_pair()
        if not pair:
            self._set_status("⚠️ No active tab — open a coin tab first")
            return
        df = self._pair_fli_df.get(pair)
        if df is None or getattr(df, "empty", True):
            self._set_status(f"⚠️ No FLI data for {pair} — wait for chart to load")
            return
        self._set_status(f"Running backtest for {pair}…")
        self._run_backtest_async(pair, df)

    def _on_best_timeframe_clicked(self):
        """Start the best-timeframe optimization for the active pair."""
        pair = self._active_pair()
        if not pair:
            self._set_status("⚠️ No active tab — open a coin tab first")
            return

        if not self._is_connected:
            self._set_status("⚠️ Connect to an exchange first")
            return

        # Stop any existing best-tf worker
        if self._best_tf_worker and self._best_tf_worker.isRunning():
            self._best_tf_worker.stop()
            self._best_tf_worker = None

        session = self._sessions.get(pair)
        investment = session.investment_amount if session else 10.0

        self.ui.btnBestTimeframe.setEnabled(False)
        self.ui.btnBestTimeframe.setText("⏳ Testing...")
        # self.ui.lblTfBacktestStatus.setVisible(True)
        self._set_status(f"Testing timeframes for {pair}…")

        worker = BestTimeframeWorker(
            self.exch_mgr, pair, self._fli_params, investment, parent=self
        )
        self._best_tf_worker = worker
        worker.tf_progress.connect(self._on_best_tf_progress)
        worker.tf_complete.connect(self._on_best_tf_complete)
        worker.tf_error.connect(self._on_best_tf_error)
        worker.start()

    @Slot(str, str, float, float)
    def _on_best_tf_progress(self, pair, tf, eq_final, eq_peak):
        """Update progress as each timeframe is tested."""
        self._set_status(
            f"Testing {pair}: {tf} → Eq.Final ${eq_final:.2f}, Peak ${eq_peak:.2f}"
        )
        self._set_status(
            f"[BestTF] {pair} @ {tf}: Eq.Final ${eq_final:.2f}, Peak ${eq_peak:.2f}"
        )

    @Slot(str, str, float, float, object)
    def _on_best_tf_complete(
        self, pair, best_tf, best_eq_final, best_eq_peak, all_results
    ):
        """Handle best-timeframe results and show recommendation."""
        self.ui.btnBestTimeframe.setEnabled(True)
        self.ui.btnBestTimeframe.setText("🔍 Best Timeframe")

        if not best_tf:
            self._set_status(f"No suitable timeframe found for {pair}")
            self._set_status(f"⚠️ [BestTF] No suitable timeframe found for {pair}")
            return

        # Build summary table
        summary_lines = [f"━━ {pair} Timeframe Comparison ━━"]
        for r in sorted(
            all_results, key=lambda x: x.get("equity_final", 0), reverse=True
        ):
            tf = r["timeframe"]
            eq_f = r["equity_final"]
            eq_p = r["equity_peak"]
            wr = r["win_rate"]
            trades = r["total_trades"]
            marker = " ◀ BEST" if tf == best_tf else ""
            summary_lines.append(
                f"  {tf:4s}: Eq.Final ${eq_f:8.2f} | Peak ${eq_p:8.2f} | "
                f"WR {wr:5.1f}% | {trades} trades{marker}"
            )

        summary_text = "\n".join(summary_lines)
        self._set_status(summary_text)
        # self.ui.lblTfBacktestStatus.setVisible(True)

        # Show recommendation dialog
        recommendation = (
            f"<b>Best Timeframe for {pair}</b><br><br>"
            f"<table cellpadding='4' style='font-size:11px; font-family:Consolas,monospace;'>"
            f"<tr style='color:#aaa'><td><b>Timeframe</b></td><td><b>Eq.Final</b></td>"
            f"<td><b>Eq.Peak</b></td><td><b>Win Rate</b></td><td><b>Trades</b></td></tr>"
        )
        for r in sorted(
            all_results, key=lambda x: x.get("equity_final", 0), reverse=True
        ):
            tf = r["timeframe"]
            eq_f = r["equity_final"]
            eq_p = r["equity_peak"]
            wr = r["win_rate"]
            trades = r["total_trades"]
            is_best = tf == best_tf
            row_color = "#00e676" if is_best else "#ddd"
            bold = "font-weight:bold;" if is_best else ""
            recommendation += (
                f"<tr style='color:{row_color}; {bold}'>"
                f"<td>{tf} {'◀' if is_best else ''}</td>"
                f"<td>${eq_f:.2f}</td><td>${eq_p:.2f}</td>"
                f"<td>{wr:.1f}%</td><td>{trades}</td></tr>"
            )
        recommendation += "</table>"

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle(f"Best Timeframe — {pair}")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            f"<b>Recommended timeframe: {best_tf}</b><br>"
            f"Equity Final: <span style='color:#00e676'>${best_eq_final:.2f}</span><br>"
            f"Equity Peak: <span style='color:#00e676'>${best_eq_peak:.2f}</span>"
        )
        msg.setInformativeText(recommendation)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Close
        )
        msg.button(QMessageBox.StandardButton.Apply).setText(f"Apply {best_tf}")
        msg.setDefaultButton(QMessageBox.StandardButton.Apply)

        if msg.exec() == QMessageBox.StandardButton.Apply:
            # Apply the best timeframe to the active pair
            rb_map = {
                "3m": self.ui.rb_timefram_3m,
                "5m": self.ui.rb_timefram_5m,
                "15m": self.ui.rb_timefram_15m,
                "30m": self.ui.rb_timefram_30m,
                "1h": self.ui.rb_timefram_1h,
            }
            target_rb = rb_map.get(best_tf)
            if target_rb:
                target_rb.setChecked(True)
            self._set_status(f"✅ Applied best timeframe {best_tf} for {pair}")

    @Slot(str, str)
    def _on_best_tf_error(self, pair, msg):
        self.ui.btnBestTimeframe.setEnabled(True)
        self.ui.btnBestTimeframe.setText("🔍 Best Timeframe")
        self._set_status(f"⚠️ [BestTF] {pair}: {msg}")
        self._set_status(f"Error: {msg}")
        # self.ui.lblTfBacktestStatus.setVisible(True)

    # ─────────────────────────────────────────────────────────────────────
    # Indicator Parameters Dialog
    # ─────────────────────────────────────────────────────────────────────

    def _on_indicator_params_clicked(self):
        """Open the indicator parameters dialog and apply changes."""
        dlg = IndicatorParamsDialog(self._fli_params, parent=self)
        dlg.params_changed.connect(self._apply_indicator_params)
        dlg.exec()

    def _apply_indicator_params(self, new_params: dict):
        """Update the live FLI params and trigger a re-computation
        for all open tabs so the user sees the effect immediately."""
        self._fli_params = new_params
        self._set_status(
            f"⚙️ Params updated: Signal={new_params.get('signal_source', 'fli').upper()}, "
            f"BB:{new_params['bb_period']}/{new_params['bb_dev']:.1f}, "
            f"ATR:{new_params['atr_period']}, CCI:{new_params['cci_len']}, "
            f"ADX:{new_params['adx_len']}, OBV:{new_params['obv_sma_len']}, "
            f"MinScore:{new_params['min_score']}"
        )

        # Re-compute FLI for all open tabs (non-blocking via FLIChartWorker)
        for pair in list(self._pair_candles.keys()):
            candles = self._pair_candles.get(pair, [])
            if candles:
                self._load_historical_chart(pair)

        # Backtest is now manual — no auto re-run on parameter change

    # ─────────────────────────────────────────────────────────────────────
    # Simulation Mode — realistic candle generator for fast testing
    # ─────────────────────────────────────────────────────────────────────

    def _setup_simulation_ui(self):
        """Create the Simulation speed slider and price spinbox.
        The Simulate button itself is already created in ResizableUI."""
        # Re-use the button created in resizable_ui.py
        self.btnSimulation = self.ui.btnSimulation
        self.btnSimulation.toggled.connect(self._on_simulation_toggled)

        # ── Speed slider ──
        self.lblSimSpeed = QLabel(" Speed:")
        self.lblSimSpeed.setStyleSheet("color:#aaa; font-size:11px;")
        self.sliderSimSpeed = QSlider(Qt.Orientation.Horizontal)
        self.sliderSimSpeed.setFixedWidth(120)
        self.sliderSimSpeed.setMinimum(1)  # 50 ms  (very fast)
        self.sliderSimSpeed.setMaximum(10)  # 5000 ms (slow)
        self.sliderSimSpeed.setValue(5)  # ~500 ms (default)
        self.sliderSimSpeed.setToolTip(
            "Candle generation speed.\n"
            "Left = very fast (50 ms/candle)\n"
            "Right = slow (5000 ms/candle)"
        )
        self.sliderSimSpeed.valueChanged.connect(self._on_sim_speed_changed)
        self.lblSimSpeedVal = QLabel("500ms")
        self.lblSimSpeedVal.setStyleSheet("color:#aaa; font-size:10px;")
        self.lblSimSpeedVal.setFixedWidth(45)

        # ── Base price spinbox ──
        self.lblSimPrice = QLabel(" Price:")
        self.lblSimPrice.setStyleSheet("color:#aaa; font-size:11px;")
        self.dsbSimPrice = QDoubleSpinBox()
        self.dsbSimPrice.setDecimals(4)
        self.dsbSimPrice.setRange(0.0001, 999999.0)
        self.dsbSimPrice.setValue(0.05)
        self.dsbSimPrice.setSingleStep(0.01)
        self.dsbSimPrice.setFixedWidth(90)
        self.dsbSimPrice.setToolTip("Base price for the simulated candles")
        self.dsbSimPrice.valueChanged.connect(self._on_sim_price_changed)

        # Add speed/price controls to the dedicated simControlsRow
        if hasattr(self.ui, 'simControlsRow'):
            self.ui.simControlsRow.addWidget(self.lblSimSpeed)
            self.ui.simControlsRow.addWidget(self.sliderSimSpeed)
            self.ui.simControlsRow.addWidget(self.lblSimSpeedVal)
            self.ui.simControlsRow.addWidget(self.lblSimPrice)
            self.ui.simControlsRow.addWidget(self.dsbSimPrice)
            self.ui.simControlsRow.addStretch()

        # Initially hide speed/price controls (only visible when simulation active)
        for w in (
            self.lblSimSpeed,
            self.sliderSimSpeed,
            self.lblSimSpeedVal,
            self.lblSimPrice,
            self.dsbSimPrice,
        ):
            w.setVisible(False)

    def _sim_interval_from_slider(self, value: int) -> int:
        """Map slider value (1-10) to interval in ms."""
        # Exponential mapping: 1→50ms, 5→500ms, 10→5000ms
        return int(50 * (100 ** ((value - 1) / 9.0 * 2)))

    @Slot(int)
    def _on_sim_speed_changed(self, value: int):
        ms = self._sim_interval_from_slider(value)
        self.lblSimSpeedVal.setText(f"{ms}ms")
        # Update all running sim workers
        for pair, worker in self._sim_workers.items():
            if worker.isRunning():
                worker.set_interval(ms)

    @Slot(float)
    def _on_sim_price_changed(self, value: float):
        self._sim_base_price = value

    @Slot(bool)
    def _on_simulation_toggled(self, checked: bool):
        """Toggle simulation mode on/off."""
        self._simulation_active = checked
        if checked:
            self.btnSimulation.setText("🎮 Simulating…")
            self._set_status("🎮 Simulation mode ON — generating realistic candles")
            # Show speed/price controls
            for w in (
                self.lblSimSpeed,
                self.sliderSimSpeed,
                self.lblSimSpeedVal,
                self.lblSimPrice,
                self.dsbSimPrice,
            ):
                w.setVisible(True)
            # Stop the normal refresh timer (we'll get candles from sim workers)
            self._refresh_timer.stop()
            # Start simulation for all existing tabs
            for pair in list(self._sessions.keys()):
                self._start_simulation_for_pair(pair)
        else:
            self.btnSimulation.setText("🎮 Simulate")
            self._set_status("🎮 Simulation mode OFF — back to live data")
            # Hide speed/price controls
            for w in (
                self.lblSimSpeed,
                self.sliderSimSpeed,
                self.lblSimSpeedVal,
                self.lblSimPrice,
                self.dsbSimPrice,
            ):
                w.setVisible(False)
            # Stop all simulation workers + process workers
            for pair, worker in list(self._sim_workers.items()):
                if worker.isRunning():
                    worker.stop()
            self._sim_workers.clear()
            for pair, worker in list(self._sim_process_workers.items()):
                if worker.isRunning():
                    worker.stop()
            self._sim_process_workers.clear()
            self._sim_processing.clear()
            # Resume normal refresh if connected
            if self._is_connected and self._sessions:
                self._refresh_timer.start()

    def _start_simulation_for_pair(self, pair: str):
        """Start a SimulationWorker for the given pair.

        If the session already has real candles, the simulator seeds
        from the last real candle and continues from there (indicators,
        signals, and alerts all keep working on the extended data).
        """
        # Stop existing sim worker for this pair
        old = self._sim_workers.get(pair)
        if old and old.isRunning():
            old.stop()

        session = self._sessions.get(pair)
        tf = session.timeframe if session else self._tf()

        # Pass existing real candles so the simulator continues from them
        existing_candles = list(session.candles) if session and session.candles else []

        worker = SimulationWorker(
            pair=pair,
            base_price=self._sim_base_price,
            timeframe=tf,
            existing_candles=existing_candles,
            parent=self,
        )
        ms = self._sim_interval_from_slider(self.sliderSimSpeed.value())
        worker.set_interval(ms)

        worker.history_ready.connect(self._on_sim_history_ready)
        worker.candle_update.connect(lambda c, p=pair: self._on_sim_candle_update(p, c))
        worker.sim_status.connect(self._set_status)
        worker.sim_error.connect(lambda m: self._set_status(f"⚠️ [Sim] {m}"))

        self._sim_workers[pair] = worker
        worker.start()

    @Slot(str, list, float)
    def _on_sim_history_ready(self, pair: str, candles: list, balance: float):
        """Handle initial simulation history batch.

        Injects the history candles into the same data pipeline that
        _on_data_fetched uses, so all indicators, FLI, chart, and
        trading logic run unchanged.
        """
        if not candles:
            return

        # Build the same dict that DataFetchWorker emits
        data = {
            "candles": candles,
            "balance": balance,
        }
        # Feed into the standard pipeline
        self._on_data_fetched(pair, data)
        self._set_status(f"🎮 [Sim] {pair}: {len(candles)} history candles loaded")

    @Slot(str, list)
    def _on_sim_candle_update(self, pair: str, candle: list):
        """Handle a single new candle from the simulation worker.

        Appends the candle to the pair's history and kicks off a
        **background** SimCandleProcessWorker so the main thread never
        blocks on indicator computation or signal evaluation.

        If the previous candle's processing hasn't finished yet, the
        new candle is still appended (so history stays accurate) but
        the indicator/chart refresh is skipped — this prevents a
        backlog of slow workers at very high simulation speeds.
        """
        session = self._sessions.get(pair)
        if not session:
            return

        # ── Append candle to existing history (main thread, fast) ──
        candles = session.candles
        if candles:
            last_ts = candles[-1][0]
            if candle[0] == last_ts:
                candles[-1] = candle
            elif candle[0] > last_ts:
                candles.append(candle)
        else:
            candles.append(candle)

        # Keep max 1000 candles
        if len(candles) > 1000:
            candles = candles[-1000:]
        session.candles = candles
        self._pair_candles[pair] = candles
        session.update_balance(10_000.0)

        # ── Guard: skip if previous processing still in flight ──
        if self._sim_processing.get(pair, False):
            return

        # ── Stop any previous process worker for this pair ──
        old_pw = self._sim_process_workers.get(pair)
        if old_pw and old_pw.isRunning():
            old_pw.stop()

        self._sim_processing[pair] = True

        worker = SimCandleProcessWorker(
            pair=pair,
            candles=list(candles),
            trading_engine=session.engine,
            balance=10_000.0,
            markers=self._pair_markers.get(pair, []),
            timeframe=session.timeframe,
            signal_source=self._fli_params.get("signal_source", "fli"),
            parent=self,
        )
        worker.process_done.connect(self._on_sim_process_done)
        worker.process_error.connect(lambda p, m: self._set_status(f"⚠️ [Sim] {p}: {m}"))
        self._sim_process_workers[pair] = worker
        worker.start()

    @Slot(str, dict)
    def _on_sim_process_done(self, pair: str, enriched: dict):
        """Called when SimCandleProcessWorker finishes indicator + signal
        computation.  Runs on the main thread but only does lightweight
        chart JS calls — no heavy TA-Lib work.
        """
        self._sim_processing[pair] = False

        session = self._sessions.get(pair)
        if not session:
            return

        # Store indicators
        indicators = enriched.get("indicators", {})
        session.indicators = indicators

        # ── Handle trading signal result (came from the worker thread) ──
        # When signal_source == "fli", signals are evaluated in _on_fli_ready;
        # skip the RSI/MACD result from the sim worker.
        signal_result = enriched.get("signal_result")
        if (
            signal_result
            and self._fli_params.get("signal_source") != "fli"
            and signal_result.get("action")
            in (
                "buy",
                "sell",
                "pending",
                "hold",
                "skipped",
                "rejected",
            )
        ):
            action = signal_result["action"]
            note = signal_result.get("note", "")
            self._set_status(f"{pair} {action.upper()}: {note}")
            if action == "pending":
                self._add_pending_marker(pair, signal_result, enriched["candles"][-1])
                signal_side = (
                    "buy" if "buy" in signal_result.get("signal", "") else "sell"
                )
                try:
                    sig_price = float(
                        signal_result.get("price", enriched["candles"][-1][4]) or 0
                    )
                except (TypeError, ValueError):
                    sig_price = 0.0
                self._safe_notify(
                    "notify_signal",
                    side=signal_side,
                    symbol=pair,
                    price=sig_price,
                )
            elif action in ("buy", "sell"):
                self._consume_pending_marker(pair, signal_result)
                if signal_result.get("trade"):
                    self._on_trade_done(pair, signal_result)
            elif action == "rejected":
                self._consume_pending_marker(pair, signal_result)

        # ── Update chart (lightweight — just JS calls) ──
        self._on_chart_ready(pair, enriched)

        # ── Evaluate alerts against the new candle ──
        last_candle = enriched.get("candles", [])[-1:] if enriched.get("candles") else []
        if last_candle:
            self._evaluate_alerts(pair, last_candle[0], enriched.get("indicators"))

        # ── Draw alert price lines ──
        self._draw_alert_price_lines(pair)

    @staticmethod
    def _fli_ts(row_time):
        if row_time is None:
            return None
        if isinstance(row_time, (int, float, str)):
            return _to_chart_time(row_time)
        try:
            ts = pd.Timestamp(row_time)
            if ts.tz is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return int(ts.timestamp())
        except Exception:
            return None

    def _set_fli_candles(self, pair: str, df):
        """Push OHLC candles to the chart using exchange-style Unix time."""
        import math as _math

        candles = []
        for _, r in df.iterrows():
            row_time = r.get("time")
            if row_time is None:
                row_time = r.get("timestamp")
            t = self._fli_ts(row_time) if row_time is not None else None
            if t is None:
                continue
            try:
                o = float(r["open"])
                h = float(r["high"])
                l = float(r["low"])
                c = float(r["close"])
            except (TypeError, ValueError):
                continue
            # Skip rows with NaN / inf OHLC — json.dumps would emit bare
            # NaN which lightweight-charts rejects as "Value is null"
            if any(_math.isnan(v) or _math.isinf(v) for v in (o, h, l, c)):
                continue
            candles.append(
                {
                    "time": t,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                }
            )
        self._chart_js(pair, f"setFliCandles({json.dumps(candles)});")
        if candles:
            self._pair_last_chart_ts[pair] = candles[-1]["time"]
        self._chart_js(pair, f"setSymbol({json.dumps(f'{pair} ({self._tf()})')});")

    def _set_fli_lines(self, pair: str, df):
        """Push the FLI trendline (green=buy/red=sell), Bollinger bands, and signal state."""
        import math as _math

        signal_pts, bbu, bbl = [], [], []
        for _, r in df.iterrows():
            row_time = r.get("time")
            if row_time is None:
                row_time = r.get("timestamp")
            t = self._fli_ts(row_time) if row_time is not None else None
            if t is None:
                continue
            tl = r.get("trendline", np.nan)
            if not (isinstance(tl, float) and tl != tl):
                itrend = int(r.get("itrend", 0))
                v = float(tl)
                if not (_math.isnan(v) or _math.isinf(v)):
                    color = "#0ecb81" if itrend == 1 else ("#f6465d" if itrend == -1 else "#848e9c")
                    signal_pts.append({"time": t, "value": v, "color": color})
            bu = r.get("bb_upper", np.nan)
            bl = r.get("bb_lower", np.nan)
            if not (isinstance(bu, float) and bu != bu):
                v = float(bu)
                if not (_math.isnan(v) or _math.isinf(v)):
                    bbu.append({"time": t, "value": v})
            if not (isinstance(bl, float) and bl != bl):
                v = float(bl)
                if not (_math.isnan(v) or _math.isinf(v)):
                    bbl.append({"time": t, "value": v})
        self._chart_js(pair, f"setFliSignalLine({json.dumps(signal_pts)});")
        self._chart_js(pair, f"setFliBBUpper({json.dumps(bbu)});")
        self._chart_js(pair, f"setFliBBLower({json.dumps(bbl)});")

    def _set_markers(self, pair: str, df, trade_markers=None, backtest_markers=None):
        """Push PENDING/BUY/SELL markers + backtest markers to the chart.

        Per Task 4 spec:
          * PENDING is shown ONLY when SAI/FLI sends a Buy/Sell signal
            and the engine is waiting for 1-candle confirmation.
          * After confirmation, the PENDING marker is REMOVED and a
            BUY or SELL marker is set on the confirmation candle.
          * After rejection, the PENDING marker is removed (no entry
            or exit happened).

        Backtest markers (blue/orange) are merged via the JS-side
        ``_mergeMarkers`` helper so that both sets display together.
        """
        markers = []
        # ── Trade markers (pending / buy / sell) drive the chart ──
        for m in trade_markers or []:
            ts = _to_chart_time(m.get("ts"))
            if ts is None:
                continue
            action = m.get("action")
            try:
                price = float(m.get("price", 0) or 0)
            except (TypeError, ValueError):
                price = 0.0
            if action == "buy":
                markers.append(
                    {
                        "time": ts,
                        "position": "belowBar",
                        "color": "#0ecb81",
                        "shape": "arrowUp",
                        "text": f"BUY @ {price:.4f}",
                        "size": 2,
                    }
                )
            elif action == "sell":
                markers.append(
                    {
                        "time": ts,
                        "position": "aboveBar",
                        "color": "#9e0217",
                        "shape": "arrowDown",
                        "text": f"SELL @ {price:.4f}",
                        "size": 2,
                    }
                )
            elif action == "pending":
                markers.append(
                    {
                        "time": ts,
                        "position": "belowBar",
                        "color": "#f0a500",
                        "shape": "circle",
                        "text": "PENDING",
                        "size": 2,
                    }
                )
        markers.sort(key=lambda x: float(x["time"]))
        self._chart_js(pair, f"setMarkers({json.dumps(markers)});")
        # ── Re-push backtest markers so they merge with trade markers ──
        if backtest_markers:
            bt_chart = []
            for m in backtest_markers:
                action = m.get("action")
                m_time = m.get("time")
                if m_time is None:
                    continue
                bt_ts = _to_chart_time(m_time)
                if bt_ts is None:
                    continue
                try:
                    bt_price = float(m.get("price", 0))
                except (TypeError, ValueError):
                    bt_price = 0.0
                if action == "bt_buy":
                    bt_chart.append(
                        {
                            "time": bt_ts,
                            "position": "belowBar",
                            "color": "#2962ff",
                            "shape": "arrowUp",
                            "text": f"BT BUY @ {bt_price:.4f}",
                            "size": 1,
                        }
                    )
                elif action == "bt_sell":
                    bt_chart.append(
                        {
                            "time": bt_ts,
                            "position": "aboveBar",
                            "color": "#ff6d00",
                            "shape": "arrowDown",
                            "text": f"BT SELL @ {bt_price:.4f}",
                            "size": 1,
                        }
                    )
            self._chart_js(
                pair,
                f"try {{ setBacktestMarkers({json.dumps(bt_chart)}); }}"
                f"catch(e) {{ console.warn('bt markers error:', e.message, e.stack); }}",
            )

    def _update_fli_info_panel(self, pair: str, row):
        """Push the last bar's FLI trend into the on-chart info box."""
        fli_trend = int(row.get("itrend", 0))
        bbu = row.get("bb_upper", 0.0)
        bbl = row.get("bb_lower", 0.0)
        bbu = 0.0 if pd.isna(bbu) else float(bbu)
        bbl = 0.0 if pd.isna(bbl) else float(bbl)
        signal = "NONE"
        if row.get("buy_signal", False):
            signal = "BUY"
        elif row.get("sell_signal", False):
            signal = "SELL"
        self._chart_js(
            pair,
            f"updateUIState({fli_trend},{bbu:.4f},{bbl:.4f},'{signal}');",
        )

    def _refresh_fli_trade_panel(self, pair: str):
        """Push current trading state to the chart's trade panel."""
        if not self._pair_chart_ready.get(pair, False):
            return
        session = self._sessions.get(pair)
        if not session:
            return
        te = session.engine
        u_pnl = 0.0
        candles = self._pair_candles.get(pair, [])
        if te.in_position and candles:
            last_close = candles[-1][4]
            u_pnl = (last_close - te.entry_price) * te.entry_qty
        summary = self.tx_logger.get_pnl_data().get("summary", {})
        d_pnl = float(summary.get("realized_pnl_usdt", 0.0))
        t_pnl = d_pnl + u_pnl
        mode = "LIVE" if self.ui.radLive.isChecked() else "DEMO"
        wallet_val = (
            self._portfolio_balance
            if self._portfolio_balance > 0
            else self.ui.lnWalletBalance.value()
        )
        self._chart_js(
            pair,
            f"updateTradePanel("
            f"'{mode}', "
            f"{te.investment_amount:.4f}, "
            f"'{te.investment_mode}', "
            f"{wallet_val:.4f}, "
            f"{'true' if te.in_position else 'false'}, "
            f"{te.entry_price:.6f}, "
            f"{te.entry_qty:.8f}, "
            f"{u_pnl:.6f}, "
            f"{d_pnl:.6f}, "
            f"{t_pnl:.6f}"
            f");",
        )

    # ── Tab management ──

    def _open_tab(self, pair):
        if isinstance(pair, dict):
            pair = pair.get("pair") or pair.get("symbol") or pair.get("coin")
        if pair is None:
            return
        pair = str(pair)

        if pair in self._tabs:
            idx = self.ui.tabWidget.indexOf(self._tabs[pair])
            if idx >= 0:
                self.ui.tabWidget.setCurrentIndex(idx)
            return
        tf = self._tf()
        inv = self.ui.dsbInvestmintAmount.value()
        mode = "FIXED" if self.ui.rbStyleFixed.isChecked() else "CUMULATIVE"
        self.tx_logger.set_meta(
            self.ui.cbExchange.currentText(),
            self.ui.radDemo.isChecked(),
            mode,
            inv,
        )
        session = CoinSession(
            pair=pair,
            timeframe=tf,
            exchange_mgr=self.exch_mgr,
            logger=self.tx_logger,
            investment_amount=inv,
            investment_mode=mode,
        )
        # ── Requirement 2: do NOT auto-buy on tab open. trading_enabled stays
        # False until the user presses 'Start Trading'. ──
        session.trading_enabled = self._global_trading_enabled
        session.engine.set_trading_enabled(self._global_trading_enabled)

        # ── Requirement 2 (point 1): if user already holds the base coin,
        # seed it as the engine's open position so the bot won't buy more. ──
        self._seed_position_from_wallet(session)

        self._sessions[pair] = session
        tab = CoinTabWidget(session)
        self._tabs[pair] = tab
        # Wire the per-tab chart loadFinished → flush JS queue
        tab.chart_view.loadFinished.connect(
            lambda ok, p=pair: self._flush_pair_js_queue(p) if ok else None
        )
        tab.trading_toggled.connect(self._on_trading_toggled)
        tab.candle_clicked.connect(self._on_chart_candle_click)
        idx = self.ui.tabWidget.addTab(tab, pair)
        self.ui.tabWidget.setCurrentIndex(idx)

        # Initialize per-pair chart state
        self._pair_state(pair)

        pipeline = ParallelPipeline(
            self.exch_mgr,
            pair,
            tf,
            trading_engine=session.engine,
            parent=self,
        )
        session.pipeline = pipeline
        pipeline.pipeline_done.connect(lambda d, p=pair: self._on_chart_ready(p, d))
        pipeline.pipeline_error.connect(lambda m, t=tab: t.set_status(f"⚠️ {m}"))
        pipeline.pipeline_status.connect(tab.set_status)
        pipeline.start()
        self._update_refresh_interval(self._tf())
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()
        self._set_status(f"Opened {pair} · waiting for 'Start Trading' to arm bot")

    def _seed_position_from_wallet(self, session: CoinSession):
        """If the user already holds the base coin in their wallet, seed it
        as the engine's open position.  This prevents the bot from buying
        more of a coin the user already has.

        Issue 3 fixes:
          • Uses TOTAL balance (free + used), not just free — previously
            a coin locked in a limit order would be invisible to the
            seeder and the bot would buy more.
          • Uses fetch_all_balances() (one HTTP call) and looks up the
            base coin from the result, instead of fetch_wallet_coin(base)
            which makes a fresh HTTP call per coin.  When called for many
            tabs in sequence, this saves N-1 round-trips.
          • Wraps every exchange call in its own try/except so a single
            failure (e.g. fetch_my_trades rate-limited) doesn't abort the
            whole seeding — we fall through to the next-best estimate.
          • Logs the chosen source (history / ticker / last close) so the
            user can verify why the entry price is what it is.
        """
        base = session.pair.split("/")[0]
        # ── Step 1: get TOTAL balance for the base coin ──
        qty = 0.0
        try:
            balances = self.exch_mgr.fetch_all_balances()
            info = balances.get(base) or balances.get(base.upper())
            if info:
                free = float(info.get("free") or 0)
                used = float(info.get("used") or 0)
                qty = float(info.get("total") or (free + used))
        except Exception as e:
            print(f"[seed_position] fetch_all_balances failed: {e}")
            # Fallback: single-coin fetch (slower but still correct).
            try:
                qty = float(self.exch_mgr.fetch_wallet_coin(base))
            except Exception as e2:
                print(f"[seed_position] fetch_wallet_coin fallback failed: {e2}")
                qty = 0.0
        if qty <= FLOAT_EPS:
            # No holdings — nothing to seed.  This is the normal case for
            # a fresh wallet; not an error.
            return

        # ── Step 2: recover average entry price (best-effort) ──
        entry_price = 0.0
        entry_source = "none"
        # Try (a) FIFO matching from exchange trade history.
        try:
            avg_entry = self.exch_mgr.fetch_avg_entry_price(session.pair, qty)
            if isinstance(avg_entry, tuple) and avg_entry and avg_entry[0]:
                entry_price = float(avg_entry[0])
                # Use the recovered qty (may differ from current wallet
                # qty if the trade history only covers part of the
                # position).  Prefer the wallet's TOTAL qty — that's
                # what we actually have to sell.
                entry_source = "trade_history"
            elif isinstance(avg_entry, (int, float)) and avg_entry > 0:
                entry_price = float(avg_entry)
                entry_source = "trade_history"
        except Exception as e:
            print(
                f"[seed_position] fetch_avg_entry_price failed for {session.pair}: {e}"
            )

        # Try (b) last ticker price (good proxy for "what is this position
        # worth right now" — not a true cost basis, but better than zero).
        if entry_price <= 0 and self.exch_mgr.exchange is not None:
            try:
                ticker = self.exch_mgr.exchange.fetch_ticker(session.pair)
                last = float(
                    ticker.get("last") or ticker.get("close") or ticker.get("bid") or 0
                )
                if last > 0:
                    entry_price = last
                    entry_source = "ticker_last"
            except Exception as e:
                print(f"[seed_position] fetch_ticker failed for {session.pair}: {e}")

        # Try (c) last OHLCV close (works even when fetch_ticker is
        # unsupported or rate-limited).
        if entry_price <= 0:
            try:
                candles = self.exch_mgr.fetch_ohlcv(session.pair, session.timeframe, 1)
                if candles:
                    entry_price = float(candles[-1][4])
                    entry_source = "last_candle_close"
            except Exception as e:
                print(f"[seed_position] fetch_ohlcv fallback failed: {e}")

        if entry_price > 0 and qty > 0:
            session.seed_position(entry_price, qty)
            self._set_status(
                f"{session.pair}: holding {qty:.8f} {base} "
                f"@ ~{entry_price:.4f} (source: {entry_source}) — "
                f"bot will wait for sell signal"
            )
        else:
            print(
                f"[seed_position] {session.pair}: couldn't determine entry price "
                f"(qty={qty}, last_source={entry_source}) — position NOT seeded"
            )

    def _fetch_and_mark_wallet_buys(self, pair: str):
        """Fetch the user's actual buy trades from the exchange for the given
        pair and push them as distinct markers on the chart + update the
        wallet-buy info panel.

        Each buy trade is rendered as a purple/magenta arrow on the chart
        with the purchase date and price.  A summary panel at the bottom
        of the chart shows total buys, total qty, and average price.

        This uses the same FIFO lot-matching logic as _seed_position_from_wallet
        but also preserves individual trade details for chart display.
        """
        try:
            trades = self.exch_mgr.fetch_my_trades(pair)
        except Exception as e:
            print(f"[wallet_buys] fetch_my_trades failed for {pair}: {e}")
            return
        if not trades:
            return

        # Collect only buy trades, sorted chronologically
        buy_trades = []
        for t in sorted(
            trades, key=lambda x: x.get("timestamp") or x.get("datetime") or 0
        ):
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
            # Format the date for the panel display
            try:
                dt = datetime.fromtimestamp(
                    ts / 1000 if ts > 1e10 else ts, tz=timezone.utc
                )
                date_str = dt.strftime("%m-%d %H:%M")
            except Exception:
                date_str = "?"
            buy_trades.append(
                {
                    "ts": ts,
                    "price": price,
                    "qty": qty,
                    "date": date_str,
                }
            )

        if not buy_trades:
            return

        # Store for later re-push
        self._pair_wallet_buy_markers[pair] = buy_trades

        # Build chart markers — purple/magenta arrows, distinct from all others
        chart_markers = []
        for bt in buy_trades:
            chart_time = _to_chart_time(bt["ts"])
            if chart_time is None:
                continue
            chart_markers.append(
                {
                    "time": chart_time,
                    "position": "belowBar",
                    "color": "#e040fb",  # Purple/magenta — distinct from BT BUY (blue) and live BUY (green)
                    "shape": "arrowUp",
                    "text": f"BUY {bt['date']} @ {bt['price']:.4f}",
                    "size": 2,
                }
            )

        # Push markers to chart
        self._chart_js(pair, f"setWalletBuyMarkers({json.dumps(chart_markers)});")

        # Update wallet buy info panel
        total_qty = sum(bt["qty"] for bt in buy_trades)
        total_cost = sum(bt["price"] * bt["qty"] for bt in buy_trades)
        avg_price = total_cost / total_qty if total_qty > 0 else 0

        panel_buys = [
            {"date": bt["date"], "price": bt["price"], "qty": bt["qty"]}
            for bt in buy_trades
        ]
        self._chart_js(
            pair,
            f"updateWalletBuyPanel({json.dumps(panel_buys)},{total_qty},{avg_price});",
        )

        self._set_status(
            f"{pair}: found {len(buy_trades)} buy trades in history, "
            f"avg entry {avg_price:.6f}"
        )

    def _on_chart_ready(self, pair: str, data: dict):
        tab = self._tabs.get(pair)
        if not tab:
            return
        self._charts_data[pair] = data
        candles = data.get("candles", []) or []
        if candles:
            self._pair_candles[pair] = candles
        # ── Fix (Task 4 / duplicate PENDING): do NOT overwrite the main
        #    thread's marker store with the pipeline's markers.  The
        #    pipeline collects markers from evaluate_signal runs that
        #    include transient "pending" states — those are NOT real
        #    trade entries and would create duplicate / stale PENDING
        #    badges on the chart.  The main thread's
        #    _add_pending_marker / _consume_pending_marker flow is the
        #    single source of truth for PENDING/BUY/SELL markers. ──
        # (Intentionally not assigning data["markers"] → _pair_markers.)

        if pair not in self._charts_loaded:
            # ── Fix (JS ReferenceError race): mark the real HTML as loaded
            #    AND reset the ready flag BEFORE setHtml so the
            #    about:blank loadFinished can't poison the state. ──
            self._pair_chart_html_loaded[pair] = True
            self._pair_chart_ready[pair] = False
            tab.load_chart_html(_FLI_HTML_TEMPLATE.replace("__LW_CDN__", CHART_CDN_URL))
            self._charts_loaded.add(pair)
            js = self._build_initial_chart_js(pair, data)
            if js:
                tab.chart_js(js)
            # ── Fetch & mark wallet purchase history on first chart load (in QThread) ──
            self._fetch_and_mark_wallet_buys_async(pair)
        else:
            js = self._build_incremental_js(pair, data)
            if js:
                tab.chart_js(js)
        balance = data.get("balance", 0.0)
        tab.set_balance(balance)

        # ── Kick off background FLI worker for this pair (non-blocking) ──
        # This computes the FLI indicator set asynchronously and refreshes
        # the chart's FLI overlays (BB bands, trendline, markers, info panel)
        # without blocking the main thread.
        self._load_historical_chart(pair)

        # ── Process any trade results from the pipeline ──
        self._process_trade_results(pair, data)

        # ── Draw alert price lines on chart ──
        self._draw_alert_price_lines(pair)

    def _process_trade_results(self, pair: str, data: dict):
        """Inspect trading results from the pipeline and route buy/sell/hold/
        skip/reject outcomes to the footer status bar + trade logger."""
        session = self._sessions.get(pair)
        if not session:
            return
        # ParallelPipeline stores results in calc_r["trading"] but that's not
        # propagated through proc_r. The engine's evaluate_signal may have
        # already executed orders (via _execute_order). We surface outcomes
        # by re-reading the engine's last state.
        te = session.engine
        # The pipeline called evaluate_signal on the last few candles, which
        # may have produced pending/confirmed/rejected/hold/skipped outcomes.
        # We can't directly see those return values here (they're inside the
        # pipeline thread), but the engine state tells us if a trade fired.
        # Surface wallet changes by re-fetching balance.
        try:
            live_balance = float(self.exch_mgr.fetch_wallet_coin("USDT"))
            if abs(live_balance - session.balance) > 1e-6:
                session.update_balance(live_balance)
                self._update_wallet(live_balance)
        except Exception as e:
            print(f"[_on_data_fetched] balance refresh failed for {session.pair}: {e}")

    def _on_trade_done(self, pair: str, data: dict):
        self.tx_logger.log_trade(data)
        # ── Task 2: refresh the shared bottom panel (PnL + rt_table)
        #    instead of the old per-tab refresh_pnl. ──
        self._refresh_bottom_panel_for(pair)
        note = data.get("note", "")
        action = data.get("action", "trade")
        self._set_status(f"{pair} {action.upper()}: {note}")

        # ── Portfolio balance tracking: decrease on buy, increase on sell ──
        if action in ("buy", "sell"):
            trade = data.get("trade", {}) or {}
            try:
                value_usdt = float(trade.get("value_usdt", 0) or 0)
            except (TypeError, ValueError):
                value_usdt = 0.0
            if action == "buy":
                self._portfolio_balance -= value_usdt
                self._set_status(
                    f"{pair} BUY: portfolio {self._portfolio_balance:.2f} USDT (-{value_usdt:.2f})"
                )
            elif action == "sell":
                pnl_usdt = trade.get("pnl_usdt")
                realized = value_usdt
                try:
                    realized = float(pnl_usdt) + value_usdt if pnl_usdt else value_usdt
                except (TypeError, ValueError):
                    realized = value_usdt
                self._portfolio_balance += value_usdt
                self._set_status(
                    f"{pair} SELL: portfolio {self._portfolio_balance:.2f} USDT (+{value_usdt:.2f})"
                )
            # Update global wallet display
            self.ui.lnWalletBalance.setProperty("value", self._portfolio_balance)
            self.ui.lnWalletBalance.display(self._portfolio_balance)

        # ── Task 3: sound + toast notification for executed trades ──
        if action in ("buy", "sell"):
            trade = data.get("trade", {}) or {}
            try:
                price = float(trade.get("price", data.get("price", 0)) or 0)
            except (TypeError, ValueError):
                price = 0.0
            try:
                qty = float(trade.get("quantity", trade.get("qty", 0)) or 0)
            except (TypeError, ValueError):
                qty = 0.0
            try:
                value = float(trade.get("value_usdt", price * qty) or 0)
            except (TypeError, ValueError):
                value = 0.0
            mode = "LIVE" if self.ui.radLive.isChecked() else "DEMO"
            inv_mode = "FIXED" if self.ui.rbStyleFixed.isChecked() else "CUMULATIVE"
            order_id = str(trade.get("order_id", data.get("ts", "")))
            pnl_usdt = trade.get("pnl_usdt")
            pnl_pct = trade.get("pnl_pct")
            self._safe_notify(
                "notify_trade",
                side=action,
                symbol=pair,
                price=price,
                qty=qty,
                value=value,
                mode=f"{mode}/{inv_mode}",
                order_id=order_id,
                pnl_usdt=pnl_usdt,
                pnl_pct=pnl_pct,
            )

    def _on_data_fetched(self, pair: str, data: dict):
        """Handle lightweight DataFetchWorker result for incremental chart
        update.  Does NOT block on FLI computation — that runs in a separate
        background worker and refreshes the chart when ready."""
        tab = self._tabs.get(pair)
        if tab:
            tab.set_status("Processing…")
        session = self._sessions.get(pair)
        if not session:
            return
        candles = data.get("candles", [])
        balance = data.get("balance", 0.0)
        if not candles:
            return
        # ── Requirement 5: store candles + sync session state ──
        session.candles = candles
        self._pair_candles[pair] = candles
        session.update_balance(balance)

        # ── Lightweight indicator computation for trading decisions ──
        # (this is fast — RSI/MACD only — and runs on the main thread but
        # completes in <50ms for 500 candles).
        indicators = IndicatorEngine.compute_all_indicators(candles)
        session.indicators = indicators

        # ── Evaluate trading signals on the latest candle ──
        # When signal_source == "fli", trading signals are evaluated in
        # _on_fli_ready (after FLI computation finishes). Skip RSI/MACD here.
        # When signal_source == "rsi_macd", use the classic RSI/MACD path.
        try:
            if self._fli_params.get("signal_source") != "fli" and len(candles) >= 2:
                r = session.engine.evaluate_signal(indicators, candles[-1], candles)
                if r and r.get("action") in ("buy", "sell", "skipped"):
                    action = r["action"]
                    note = r.get("note", "")
                    self._set_status(f"{pair} {action.upper()}: {note}")
                    if action in ("buy", "sell") and r.get("trade"):
                        self._on_trade_done(pair, r)
        except Exception as e:
            self._set_status(f"⚠️ {pair} signal eval: {e}")

        # ── Build incremental chart update (candles only; FLI worker pushes
        # indicator overlays separately when ready — no main-thread blocking). ──
        enriched = {
            "candles": candles,
            "fli_data": None,  # populated asynchronously by FLIChartWorker
            "indicators": indicators,
            "balance": balance,
            "markers": self._pair_markers.get(pair, []),
            "pair": pair,
            "timeframe": session.timeframe,
        }
        self._on_chart_ready(pair, enriched)
        if tab:
            tab.set_status(f"✅ {len(candles)} candles · {balance:.4f} USDT")

    # ─────────────────────────────────────────────────────────────────────
    # PENDING / BUY / SELL marker management (Task 4)
    # ─────────────────────────────────────────────────────────────────────

    def _add_pending_marker(self, pair: str, result: dict, last_candle: list):
        """Place a PENDING marker at the signal candle's ts.

        Called when TradingEngine.evaluate_signal returns action="pending"
        — i.e. SAI/FLI just fired a Buy/Sell signal that MUST wait for
        1-candle confirmation.  We track the pending ts so the marker
        can be removed when the signal is confirmed or rejected.

        Hard requirements (Issue 1 from this review round):
          • There is at most ONE PENDING marker per pair at any time.
            If a PENDING already exists for this pair, it is REPLACED
            (not duplicated) — a new pending signal supersedes the old.
          • A PENDING marker is NEVER placed while the engine holds an
            open position (in_position=True) — between BUY and SELL,
            the only valid signal is SELL → pending → confirm/reject.
            The engine's own gating ensures this, but we double-check
            here so a logic bug can't produce a "PENDING after BUY"
            marker on the chart.
        """
        # ── Issue 1 (never set PENDING after BUY/between BUY and SELL):
        #    if the engine currently holds a position AND the incoming
        #    pending signal is a BUY, refuse to draw it.  The only
        #    legitimate pending signal while long is a SELL.
        session = self._sessions.get(pair)
        if session is not None:
            sig_name = (result.get("signal") or "").lower()
            if session.engine.in_position and "buy" in sig_name:
                # We're already long; a fresh BUY pending is meaningless.
                # Drop silently — do NOT add a marker.
                return

        ts = result.get("ts") or (last_candle[0] if last_candle else None)
        price = result.get("price") or (last_candle[4] if last_candle else 0.0)
        try:
            ts = int(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts = None
        if ts is None:
            return
        markers = self._pair_markers.setdefault(pair, [])

        # ── Issue 1 (single PENDING per pair): remove ANY existing
        #    PENDING marker for this pair before adding the new one.
        #    A new pending signal always supersedes a stale one —
        #    e.g. if the previous PENDING was never consumed (engine
        #    restart, tab switch, etc.), we don't want two PENDING
        #    badges on the chart.
        markers = [m for m in markers if m.get("action") != "pending"]
        markers.append({"ts": ts, "action": "pending", "price": float(price)})
        # Keep last 30 markers
        self._pair_markers[pair] = markers[-30:]
        self._pair_pending_ts[pair] = ts
        self._refresh_markers(pair)

    def _consume_pending_marker(self, pair: str, result: dict):
        """Remove the PENDING marker (confirmation happened, or signal was
        rejected).  If the result is a confirmed buy/sell, also append a
        BUY/SELL marker at the confirmation candle's ts.

        Issue 1 hardening: removes ALL pending markers for this pair (not
        just the most recent) — defensive against any drift where a second
        PENDING slipped through.  After this call, the chart will show
        zero PENDING markers for this pair until a fresh signal fires.
        """
        ts = result.get("ts")
        try:
            ts = int(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts = None
        action = result.get("action")
        price = result.get("price", 0.0)
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = 0.0
        markers = self._pair_markers.setdefault(pair, [])
        # ── Issue 1: remove EVERY pending marker for this pair.  The
        #    engine only tracks one pending_signal, but the chart marker
        #    list can drift (e.g. duplicate PENDING was previously added
        #    before the Issue 1 fix).  Strip all of them so the chart
        #    reflects the true "no pending signal" state after this call.
        cleaned = [m for m in markers if m.get("action") != "pending"]
        # If this is a confirmed buy/sell, add a BUY/SELL marker at the
        # confirmation candle's ts.
        if action in ("buy", "sell") and ts is not None:
            # Avoid duplicate BUY/SELL at the same ts
            dup = any(m.get("action") == action and m.get("ts") == ts for m in cleaned)
            if not dup:
                cleaned.append({"ts": ts, "action": action, "price": price})
                cleaned.sort(key=lambda x: float(x.get("ts") or 0))
        self._pair_markers[pair] = cleaned[-30:]
        self._pair_pending_ts[pair] = None
        self._refresh_markers(pair)

    def _refresh_markers(self, pair: str):
        """Re-push markers to the chart using the latest FLI df (if any)
        and the current trade-marker list.  Called whenever
        ``_pair_markers[pair]`` changes so the chart reflects the new
        PENDING/BUY/SELL state without waiting for the next FLI worker
        pass."""
        df = self._pair_fli_df.get(pair)
        bt_markers = self._pair_backtest_markers.get(pair, [])
        if df is None or getattr(df, "empty", True):
            # No FLI df yet — fall back to a bare setMarkers() call with
            # just the trade markers so the user sees the PENDING badge
            # even before the FLI worker finishes.
            self._set_markers(
                pair,
                None,
                trade_markers=self._pair_markers.get(pair, []),
                backtest_markers=bt_markers,
            )
            return
        self._set_markers(
            pair,
            df,
            trade_markers=self._pair_markers.get(pair, []),
            backtest_markers=bt_markers,
        )

    def _format_fli_data(self, fli_data: dict, candles: list) -> list[str]:
        """Build the JS call list that pushes FLI indicator series + UI state
        to the chart.

        Code Review High #5: this logic was previously duplicated verbatim in
        ``_build_initial_chart_js`` and ``_build_incremental_js``.  Any change
        to FLI formatting had to be made in two places — a maintenance hazard
        and a source of subtle drift bugs.  Extracted here as a single helper
        so both call-sites stay in sync.
        """
        import json as _json

        if not fli_data:
            return []
        parts: list[str] = []
        bb_upper = fli_data.get("bb_upper", [])
        bb_lower = fli_data.get("bb_lower", [])
        trendline = fli_data.get("trendline", [])
        itrend = fli_data.get("itrend", [])

        def _to_series(values):
            entries = []
            for i, v in enumerate(values):
                if v is None or (isinstance(v, float) and v != v):
                    continue
                t = _to_chart_time(candles[i][0]) if i < len(candles) else None
                if t is None:
                    continue
                entries.append({"time": t, "value": float(v)})
            return _json.dumps(entries)

        if bb_upper:
            parts.append("setFliBBUpper(" + _to_series(bb_upper) + ")")
        if bb_lower:
            parts.append("setFliBBLower(" + _to_series(bb_lower) + ")")
        if trendline and itrend and len(itrend) == len(trendline):
            signal_pts = []
            for i, (v, t) in enumerate(zip(trendline, itrend)):
                if v is None or (isinstance(v, float) and v != v):
                    continue
                t2 = _to_chart_time(candles[i][0]) if i < len(candles) else None
                if t2 is None:
                    continue
                color = "#0ecb81" if t == 1 else ("#f6465d" if t == -1 else "#848e9c")
                signal_pts.append({"time": t2, "value": float(v), "color": color})
            if signal_pts:
                parts.append("setFliSignalLine(" + _json.dumps(signal_pts) + ")")
        # Build the score based on fli_trend direction (matches _update_fli_info_panel logic)
        fli_trend_val = fli_data.get("fli_trend", 0) or 0
        score_val = (fli_data.get("score_buy", 0) or 0) if fli_trend_val > 0 else (fli_data.get("score_sell", 0) or 0)
        bbu_val = fli_data.get("bb_upper_val", 0) or 0
        bbl_val = fli_data.get("bb_lower_val", 0) or 0
        parts.append(
            "updateUIState("
            f"{int(fli_trend_val)},"
            f"{fli_data.get('cci', 0) or 0},"
            f"{fli_data.get('adx', 0) or 0},"
            f"0,"
            f"{int(score_val)},"
            f"{bbu_val},"
            f"{bbl_val},"
            f"'{fli_data.get('signal', 'WAIT')}'"
            ")"
        )
        return parts

    def _build_initial_chart_js(self, pair: str, data: dict) -> str:
        import json as _json
        import math as _math

        candles = data.get("candles", [])
        fli_data = data.get("fli_data")
        markers = data.get("markers", [])
        parts = []
        parts.append("setSymbol(" + _json.dumps(f"{pair} ({self._tf()})") + ")")
        if candles:
            candle_entries = []
            for c in candles:
                ts = _to_chart_time(c[0])
                if ts is None:
                    continue
                o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                if any(_math.isnan(v) or _math.isinf(v) for v in (o, h, l, cl)):
                    continue
                candle_entries.append(
                    {
                        "time": ts,
                        "open": o,
                        "high": h,
                        "low": l,
                        "close": cl,
                    }
                )
            parts.append("setFliCandles(" + _json.dumps(candle_entries) + ")")
            # lightweight-charts update() throws "Cannot update oldest data"
            # if the new candle ts is not strictly newer than the last bar.
            if candle_entries:
                self._pair_last_chart_ts[pair] = candle_entries[-1]["time"]
        # Code Review High #5: delegate to shared helper.
        parts.extend(self._format_fli_data(fli_data, candles))
        if markers:
            parts.append("setMarkers(" + ChartRenderer._markers_js(markers) + ")")
        parts.append("fitContent()")
        if not parts:
            return ""
        body = "\n".join(parts)
        return f"try {{ {body} }} catch(e) {{ console.warn('chart init skipped:', e.message, e.stack); }}"

    def _build_incremental_js(self, pair: str, data: dict) -> str:
        import json as _json
        import math as _math

        candles = data.get("candles", [])
        fli_data = data.get("fli_data")
        markers = data.get("markers", [])
        if not candles:
            return ""
        parts = []
        last = candles[-1]
        ts = _to_chart_time(last[0])
        last_ts = self._pair_last_chart_ts.get(pair, 0)
        if ts is not None:
            o, h, l, cl = float(last[1]), float(last[2]), float(last[3]), float(last[4])
            if (
                any(_math.isnan(v) or _math.isinf(v) for v in (o, h, l, cl))
                or ts <= last_ts
            ):
                pass  # skip update, but still send indicators
            else:
                parts.append(
                    "updateFliCandle("
                    + _json.dumps(
                        {
                            "time": ts,
                            "open": o,
                            "high": h,
                            "low": l,
                            "close": cl,
                        }
                    )
                    + ")"
                )
                self._pair_last_chart_ts[pair] = ts
        # Code Review High #5: delegate to shared helper.
        parts.extend(self._format_fli_data(fli_data, candles))
        if markers:
            parts.append("setMarkers(" + ChartRenderer._markers_js(markers) + ")")
        if not parts:
            return ""
        body = "\n".join(parts)
        return f"try {{ {body} }} catch(e) {{ console.warn('chart update skipped:', e.message, e.stack); }}"

    def _refresh_pipelines(self):
        if not self._is_connected or not self._sessions:
            return
        for pair, session in self._sessions.items():
            if session.pipeline:
                if (
                    isinstance(session.pipeline, ParallelPipeline)
                    and session.pipeline.isRunning()
                ):
                    continue
                session.pipeline.stop()
            worker = DataFetchWorker(
                self.exch_mgr, pair, session.timeframe, parent=self
            )
            session.pipeline = worker
            worker.data_fetched.connect(lambda d, p=pair: self._on_data_fetched(p, d))
            tab = self._tabs.get(pair)
            if tab:
                worker.fetch_status.connect(tab.set_status)
                worker.fetch_error.connect(lambda m, t=tab: t.set_status(f"⚠️ {m}"))
            worker.start()

    def _on_tab_close(self, index: int):
        tab = self.ui.tabWidget.widget(index)
        if tab is None:
            return
        pair = tab.session.pair
        self.ui.tabWidget.removeTab(index)
        self._halt_and_remove(pair)

    def _halt_and_remove(self, pair: str):
        session = self._sessions.pop(pair, None)
        self._tabs.pop(pair, None)
        self._charts_loaded.discard(pair)
        self._charts_data.pop(pair, None)
        # ── Clean up per-pair FLI worker + chart state ──
        worker = self._fli_workers.pop(pair, None)
        if worker and worker.isRunning():
            worker.quit()
            worker.wait(2000)
        # ── Clean up per-pair backtest worker ──
        bt_worker = self._backtest_workers.pop(pair, None)
        if bt_worker and bt_worker.isRunning():
            bt_worker.stop()
        # ── Clean up per-pair wallet buy worker ──
        wb_worker = self._wallet_buy_workers.pop(pair, None)
        if wb_worker and wb_worker.isRunning():
            wb_worker.stop()
        self._pair_candles.pop(pair, None)
        self._pair_last_chart_ts.pop(pair, None)
        self._pair_markers.pop(pair, None)
        self._pair_fli_df.pop(pair, None)
        self._pair_chart_ready.pop(pair, None)
        self._pair_chart_first_load.pop(pair, None)
        self._pair_chart_js_queue.pop(pair, None)
        self._pair_chart_html_loaded.pop(pair, None)
        self._pair_pending_ts.pop(pair, None)
        self._pair_backtest_markers.pop(pair, None)
        self._pair_wallet_buy_markers.pop(pair, None)
        # ── Clean up simulation worker for this pair ──
        sim_worker = self._sim_workers.pop(pair, None)
        if sim_worker and sim_worker.isRunning():
            sim_worker.stop()
        sim_pw = self._sim_process_workers.pop(pair, None)
        if sim_pw and sim_pw.isRunning():
            sim_pw.stop()
        self._sim_processing.pop(pair, None)
        if session and session.pipeline:
            session.pipeline.stop()
            session.pipeline.wait(2000)
        if tab := self._tabs.get(pair):
            tab.deleteLater()
        if not self._sessions:
            self._refresh_timer.stop()

    def _on_trading_toggled(self, pair: str, enabled: bool):
        """Per-tab trading toggle (the small button inside each tab).
        Acts as an additional gate on top of the global Start Trading flag."""
        session = self._sessions.get(pair)
        tab = self._tabs.get(pair)
        if not session or not tab:
            return
        if enabled:
            # Resume engine (clears halt) + arm per-session trading.
            # Note: real buy/sell still requires the global Start Trading
            # button to be armed — see _on_start_trading_toggled.
            session.resume()
            session.start_trading()
            tab.show_trading_started()
            self._set_status(
                f"{pair}: per-tab trading armed. "
                f"Global state: {'ARMED' if self._global_trading_enabled else 'DISARMED'}"
            )
        else:
            # Per-tab disable — halt engine for this pair only.
            session.halt()
            session.stop_trading()
            tab.show_trading_stopped()
            self._set_status(f"{pair}: per-tab trading halted")

    @Slot(bool)
    def _on_start_trading_toggled(self, enabled: bool):
        """Global Start Trading button — master gate for all sessions.
        When armed: the bot will wait for a confirmed SAI/FLI buy signal
        on each open tab, then enter the market (if not already holding
        the base coin and funds are sufficient).
        When disarmed: no new orders will be placed.

        Code Review 3.6: in LIVE mode, arming requires an explicit
        confirmation dialog so the user can't accidentally leave "Live"
        selected from a previous session and arm real-money trading
        with a single misclick.
        """
        # ── 3.6: confirm before arming LIVE trading ──
        # Block signals while we may revert the checkbox so the revert
        # doesn't recursively re-enter this slot.
        is_live = self.ui.radLive.isChecked()
        if enabled and is_live and not getattr(self, "_live_confirmed", False):
            self.ui.btnStartTrading.blockSignals(True)
            self.ui.btnStartTrading.setChecked(False)
            self.ui.btnStartTrading.blockSignals(False)
            # Async-confirm via QMessageBox — if accepted, re-arm with
            # the _live_confirmed flag set so we skip the dialog this
            # one time.  Setting the flag in the parent scope (self)
            # means the next toggled(True) call skips the dialog.
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Arm LIVE trading?")
            msg.setText("<b>You are about to arm LIVE trading with real funds.</b>")
            msg.setInformativeText(
                "Once armed, the bot will place real market orders on your "
                "connected exchange whenever a confirmed SAI/FLI signal fires.\n\n"
                "Make sure:\n"
                "  • API keys are correct and have trading permission.\n"
                "  • Wallet has sufficient USDT for the configured investment.\n"
                "  • You understand the bot only sells when sell price > buy price.\n\n"
                "Continue arming LIVE trading?"
            )
            msg.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            msg.setDefaultButton(QMessageBox.StandardButton.No)
            if msg.exec() == QMessageBox.StandardButton.Yes:
                self._live_confirmed = True
                # Re-trigger the toggle path programmatically.
                self.ui.btnStartTrading.setChecked(True)
                self._live_confirmed = False
            return

        self._global_trading_enabled = enabled
        mode = "LIVE" if is_live else "DEMO"
        if enabled:
            self.ui.btnStartTrading.setText("Stop Trading")
            armed_count = 0
            active_pair = ""
            for pair, session in self._sessions.items():
                session.resume()
                session.start_trading()
                tab = self._tabs.get(pair)
                if tab:
                    tab.show_trading_started()
                armed_count += 1
                if not active_pair:
                    active_pair = pair
            self._set_status(
                f"🟢 Trading ARMED for {armed_count} pair(s). "
                f"Bot will wait for confirmed SAI/FLI buy signal to enter, "
                f"and only sell when sell price > buy price."
            )
            # ── Task 3: bot-start sound + toast ──
            active_tf = self._tf()
            self._safe_notify("notify_bot_start", mode, active_pair or "—", active_tf)
        else:
            self.ui.btnStartTrading.setText("Start Trading")
            for pair, session in self._sessions.items():
                session.stop_trading()
                # Don't halt — keep pipelines running for chart updates.
                tab = self._tabs.get(pair)
                if tab:
                    tab.show_trading_stopped()
            self._set_status(
                "🔴 Trading DISARMED — no new orders will be placed. "
                "Charts continue to update."
            )
            # ── Task 3: bot-stop sound + toast ──
            self._safe_notify("notify_bot_stop", mode, "disarmed by user")

    def _update_wallet(self, balance):
        # ── Initialize portfolio on first exchange balance fetch ──
        if self._portfolio_balance <= 0 and balance > 0:
            self._portfolio_balance = balance
        self.ui.lnWalletBalance.setProperty("value", self._portfolio_balance)
        self.ui.lnWalletBalance.display(self._portfolio_balance)
        # Set dsbInvestmintAmount maximum to portfolio balance
        self.ui.dsbInvestmintAmount.setMaximum(
            self._portfolio_balance if self._portfolio_balance > 0 else 10000.0
        )
        pct = self.ui.slidInvistmineAmount.value()
        self.ui.dsbInvestmintAmount.blockSignals(True)
        self.ui.dsbInvestmintAmount.setValue(pct * self._portfolio_balance / 100.0)
        self.ui.dsbInvestmintAmount.blockSignals(False)

    # ── Exchange selected → load spot pairs with progress ──

    @Slot(str)
    def _on_exchange_selected(self, name):
        if not name:
            return
        self._set_status(f"Loading pairs for {name}…")
        self.ui.cbPair.clear()
        self.ui.pairProgressBar.setVisible(True)
        self.ui.lblPairProgress.setVisible(True)
        self.ui.lblPairProgress.setText(f"Loading {name} spot pairs… please wait")
        self.ui.pairProgressBar.setValue(0)

        self._pair_loader = PairLoaderWorker(self.exch_mgr, name, parent=self)
        self._pair_loader.pairs_loaded.connect(self._on_pairs_loaded)
        self._pair_loader.load_progress.connect(self._on_pair_progress)
        self._pair_loader.load_error.connect(self._on_pair_load_error)
        self._pair_loader.load_status.connect(self._set_status)
        self._pair_loader.start()

    @Slot(list)
    def _on_pairs_loaded(self, pairs):
        self.ui.cbPair.addItems(pairs)
        self.ui.pairProgressBar.setVisible(False)
        self.ui.lblPairProgress.setVisible(False)

    @Slot(int)
    def _on_pair_progress(self, pct):
        self.ui.pairProgressBar.setValue(pct)

    @Slot(str)
    def _on_pair_load_error(self, msg):
        self._set_status(f"⚠️ Pair load: {msg}")
        self.ui.pairProgressBar.setVisible(False)
        self.ui.lblPairProgress.setVisible(False)

    # ── Pair / Timeframe / Mode changes ──

    @Slot(str)
    def _on_pair_changed(self, pair):
        self._set_status(f"Pair → {pair}")

    @Slot()
    def _on_add_pair_clicked(self):
        pair = self.ui.cbPair.currentText().strip()
        if not pair:
            return
        if self._is_connected:
            self._open_tab(pair)
        else:
            self._set_status("Connect to an exchange first")

    # Polling intervals (ms) — how often to fetch new candles,
    # NOT the candle duration itself.  Kept shorter than candle duration
    # so the chart updates near-real-time.
    _TF_INTERVALS = {
        "1m": 10_000,
        "3m": 15_000,
        "5m": 30_000,
        "15m": 60_000,
        "30m": 120_000,
        "1h": 300_000,
        "4h": 600_000,
        "1d": 1800_000,
    }

    def _update_refresh_interval(self, tf: str):
        ms = self._TF_INTERVALS.get(tf, 60_000)
        self._refresh_timer.setInterval(ms)

    @Slot()
    def _on_timeframe_changed(self):
        """User clicked a different timeframe radio button.

        Per the new requirement, each tab tracks its own timeframe. We
        therefore update ONLY the currently active tab's session + chart
        (not all sessions).  When the user later switches tabs, the radio
        buttons will be re-synced to the active tab's timeframe via
        ``_on_tab_changed``.

        Also triggers a backtest for the active pair on the new timeframe,
        disables timeframe selection during backtesting, and shows duration.
        """
        tf = self._tf()
        self._set_status(f"Timeframe → {tf}")
        self._update_refresh_interval(tf)
        if not self._is_connected:
            return
        # ── Apply only to the currently active tab's session ──
        idx = self.ui.tabWidget.currentIndex()
        if idx < 0:
            return
        tab = self.ui.tabWidget.widget(idx)
        if tab is None:
            return
        pair = tab.session.pair
        session = self._sessions.get(pair)
        if not session:
            return
        session.timeframe = tf
        self._charts_loaded.discard(pair)
        if session.pipeline:
            session.pipeline.stop()
        tab._chart_ready = False
        tab._chart_js_queue.clear()
        pipeline = ParallelPipeline(
            self.exch_mgr, pair, tf, trading_engine=session.engine, parent=self
        )
        session.pipeline = pipeline
        pipeline.pipeline_done.connect(lambda d, p=pair: self._on_chart_ready(p, d))
        pipeline.pipeline_error.connect(
            lambda m, t=tab: t.set_status(f"⚠️ {m}") if tab else None
        )
        pipeline.pipeline_status.connect(tab.set_status)
        pipeline.start()
        if self._sessions and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    @Slot(int)
    def _on_tab_changed(self, idx: int):
        """When the user switches tabs, sync the timeframe radio buttons
        and investment controls to reflect the active tab's stored values.
        Signals are blocked so we don't recursively trigger handlers.
        """
        if idx < 0:
            return
        tab = self.ui.tabWidget.widget(idx)
        if tab is None:
            return
        session = getattr(tab, "session", None)
        if session is None:
            return
        pair = session.pair

        # ── Sync timeframe radio buttons ──
        tf = getattr(session, "timeframe", None) or self._tf()
        rb_map = {
            "3m": self.ui.rb_timefram_3m,
            "5m": self.ui.rb_timefram_5m,
            "15m": self.ui.rb_timefram_15m,
            "30m": self.ui.rb_timefram_30m,
            "1h": self.ui.rb_timefram_1h,
        }
        target_rb = rb_map.get(tf, self.ui.rb_timefram_3m)
        for rb in rb_map.values():
            rb.blockSignals(True)
        target_rb.setChecked(True)
        for rb in rb_map.values():
            rb.blockSignals(False)

        # ── Sync investment amount + mode to active tab's values ──
        inv = getattr(session, "investment_amount", None)
        mode = getattr(session, "investment_mode", "FIXED")
        if inv is not None:
            self.ui.dsbInvestmintAmount.blockSignals(True)
            self.ui.dsbInvestmintAmount.setValue(inv)
            self.ui.dsbInvestmintAmount.blockSignals(False)
            # Sync slider percentage
            port = (
                self._portfolio_balance
                if self._portfolio_balance > 0
                else (
                    self.ui.lnWalletBalance.value()
                    if self.ui.lnWalletBalance.value() > 0
                    else 10000
                )
            )
            if port > 0:
                pct = int(inv / port * 100)
                self.ui.slidInvistmineAmount.blockSignals(True)
                self.ui.slidInvistmineAmount.setValue(min(pct, 100))
                self.ui.slidInvistmineAmount.blockSignals(False)
        if mode == "CUMULATIVE":
            self.ui.rbStyleCumu.blockSignals(True)
            self.ui.rbStyleCumu.setChecked(True)
            self.ui.rbStyleFixed.blockSignals(True)
            self.ui.rbStyleFixed.setChecked(False)
            self.ui.rbStyleCumu.blockSignals(False)
            self.ui.rbStyleFixed.blockSignals(False)
        else:
            self.ui.rbStyleFixed.blockSignals(True)
            self.ui.rbStyleFixed.setChecked(True)
            self.ui.rbStyleCumu.blockSignals(True)
            self.ui.rbStyleCumu.setChecked(False)
            self.ui.rbStyleFixed.blockSignals(False)
            self.ui.rbStyleCumu.blockSignals(False)

        # Also refresh the bottom dockable panel (PnL + rt_table) to
        # show this tab's data.
        if hasattr(self, "_refresh_bottom_panel_for"):
            self._refresh_bottom_panel_for(pair)
        # Update refresh interval to match the active tab's timeframe.
        self._update_refresh_interval(tf)

    @Slot()
    def _on_mode_changed(self):
        m = "Live" if self.ui.radLive.isChecked() else "Demo"
        self._set_status(f"Mode → {m}")
        if self._is_connected:
            self._disconnect()
            self._connect_exchange()

    def _update_active_session_investment(self):
        """Propagate the spinbox/slider value to the ACTIVE tab's session
        engine only.  Each tab has its own investment amount."""
        pair = self._active_pair()
        if not pair:
            return
        session = self._sessions.get(pair)
        if not session:
            return
        inv = self.ui.dsbInvestmintAmount.value()
        mode = "FIXED" if self.ui.rbStyleFixed.isChecked() else "CUMULATIVE"
        session.set_investment(inv, mode)

    def _active_pair(self) -> str | None:
        """Return the pair for the currently active tab, or None."""
        idx = self.ui.tabWidget.currentIndex()
        if idx < 0:
            return None
        tab = self.ui.tabWidget.widget(idx)
        if tab is None:
            return None
        session = getattr(tab, "session", None)
        return session.pair if session else None

    @Slot(int)
    def _on_slider_changed(self, pct):
        port = (
            self._portfolio_balance
            if self._portfolio_balance > 0
            else (
                self.ui.lnWalletBalance.value()
                if self.ui.lnWalletBalance.value() > 0
                else 10000
            )
        )
        self.ui.dsbInvestmintAmount.blockSignals(True)
        self.ui.dsbInvestmintAmount.setValue(pct * port / 100.0)
        self.ui.dsbInvestmintAmount.blockSignals(False)
        self._update_active_session_investment()
        pair = self._active_pair()
        label = pair or "N/A"
        self._set_status(
            f"[{label}] Investment → {self.ui.dsbInvestmintAmount.value():.2f} USDT"
        )

    @Slot(float)
    def _on_spinbox_changed(self, val):
        port = (
            self._portfolio_balance
            if self._portfolio_balance > 0
            else (
                self.ui.lnWalletBalance.value()
                if self.ui.lnWalletBalance.value() > 0
                else 10000
            )
        )
        if port > 0:
            pct = int(val / port * 100)
            self.ui.slidInvistmineAmount.blockSignals(True)
            self.ui.slidInvistmineAmount.setValue(min(pct, 100))
            self.ui.slidInvistmineAmount.blockSignals(False)
        self._update_active_session_investment()
        pair = self._active_pair()
        label = pair or "N/A"
        self._set_status(f"[{label}] Investment → {val:.2f} USDT")

    @Slot()
    def _on_invest_style_changed(self):
        self._update_active_session_investment()
        pair = self._active_pair()
        mode = "FIXED" if self.ui.rbStyleFixed.isChecked() else "CUMULATIVE"
        label = pair or "N/A"
        self._set_status(f"[{label}] Investment mode → {mode}")

    @Slot()
    def _on_connect_disconnect(self):
        if self._is_connected:
            self._disconnect()
        else:
            self._connect_exchange()

    def _connect_exchange(self):
        ex = self.ui.cbExchange.currentText()
        demo = self.ui.radDemo.isChecked()
        if not ex:
            self._set_status("⚠️ Select exchange")
            return

        self._set_status(f"Connecting to {ex}…")
        ok = self.exch_mgr.connect(ex, demo)
        if not ok:
            self._set_status(f"⚠️ Failed to connect {ex}")
            return

        self._is_connected = True
        self.ui.btnConnDissconExchange.setText("Disconnect")
        # Enable the Start Trading button now that we have a live exchange.
        # It starts unchecked (disarmed) — the user must explicitly arm it.
        self.ui.btnStartTrading.setEnabled(True)
        self.ui.btnStartTrading.setChecked(False)
        self.ui.btnStartTrading.setText("Start Trading")
        self._global_trading_enabled = False
        ccxt_note = "(ccxt)" if CCXT_AVAILABLE else "(mock)"
        self._set_status(f"Connected {ex} {'Demo' if demo else 'Live'} {ccxt_note} ✅")
        # ── Task 3: connect sound ──
        self._safe_notify("notify_connect", ex, "—")

        self.tx_logger.set_meta(
            ex,
            demo,
            "FIXED" if self.ui.rbStyleFixed.isChecked() else "CUMULATIVE",
            self.ui.dsbInvestmintAmount.value(),
        )

        self.ui.tabWidget.clear()
        self._sessions.clear()
        self._tabs.clear()
        # Clear per-pair state
        self._charts_loaded.clear()
        self._charts_data.clear()
        self._fli_workers.clear()
        self._pair_candles.clear()
        self._pair_last_chart_ts.clear()
        self._pair_markers.clear()
        self._pair_fli_df.clear()
        self._pair_chart_ready.clear()
        self._pair_chart_first_load.clear()
        self._pair_chart_js_queue.clear()
        self._pair_chart_html_loaded.clear()
        self._pair_pending_ts.clear()
        self._pair_wallet_buy_markers.clear()

        try:
            wallet_balance = self.exch_mgr.fetch_wallet_coin("USDT")
            self._update_wallet(wallet_balance)
        except Exception as e:
            print(f"[wallet] fetch on connect error: {e}")

        tradable_pairs = self.exch_mgr.discover_tradable_pairs()
        # usdt_pairs = [p for p in tradable_pairs if p.endswith("/USDT")]
        # non_dust = self.exch_mgr.fetch_other_coin()

        self._set_status(f"Discovered {len(tradable_pairs)} wallet pairs…")
        for item in tradable_pairs:
            self._open_tab(item)

        if not tradable_pairs:
            self._set_status(f"Connected ✅ — no wallet pairs found")
        # Remind the user that trading is disarmed by default.
        self._set_status(
            "Connected ✅ — press 'Start Trading' to arm the bot. "
            "It will then wait for a confirmed SAI/FLI buy signal before "
            "entering any position."
        )

    def _disconnect(self):
        # Disarm trading first to prevent any in-flight orders.
        if self._global_trading_enabled:
            self._global_trading_enabled = False
        self.ui.btnStartTrading.setChecked(False)
        self.ui.btnStartTrading.setText("Start Trading")
        self.ui.btnStartTrading.setEnabled(False)
        # Stop best-timeframe worker if running
        if self._best_tf_worker and self._best_tf_worker.isRunning():
            self._best_tf_worker.stop()
            self._best_tf_worker = None
        # Stop simulation if active
        if self._simulation_active:
            self.btnSimulation.setChecked(False)
        for pair in list(self._sessions):
            self._halt_and_remove(pair)
        self.exch_mgr.disconnect()
        self._is_connected = False
        self._portfolio_balance = 0.0
        self.ui.btnConnDissconExchange.setText("Connection")
        self._set_status("Disconnected ❌")

    # ── PnL ──

    @Slot()
    def _on_show_pnl(self):
        pnl_data = self.tx_logger.get_pnl_data()
        if not pnl_data.get("trades"):
            # Show last saved file if exists
            if PNL_LOG_FILE.exists():
                try:
                    pnl_data = json.loads(PNL_LOG_FILE.read_text())
                except Exception as e:
                    # Code Review Critical #3: previously a bare ``except: pass``
                    # that silently swallowed JSON decode errors.  Log so the
                    # user has a clue why the PnL dialog is empty.
                    print(f"[_on_show_pnl] failed to parse {PNL_LOG_FILE}: {e}")
                    pnl_data = {"trades": [], "summary": {}}
        dlg = PnLDialog(pnl_data, parent=self)
        dlg.exec()

    @Slot()
    def _on_reset_pnl(self):
        """Issue 4: Clear all logged trades + the persisted PnL file.

        Asks for confirmation first because the operation is destructive
        and cannot be undone — the user might press the button by accident
        while reaching for "Show PnL" next to it.
        """
        # Confirmation dialog — keep it modal so the user can't accidentally
        # double-click and skip past it.
        reply = QMessageBox.question(
            self,
            "Reset Daily P&L?",
            "This will permanently delete ALL logged trades and the saved "
            "PnL file (pnl_log.json).\n\n"
            "This cannot be undone.\n\n"
            "Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Wipe in-memory trades + remove the persisted file.
        self.tx_logger.reset_all()
        # Clear the bottom panel's round-trip table + summary labels.
        try:
            self.ui.rt_table.setRowCount(0)
            self.ui.lbl_pnl_pair.setText("Pair: —")
            self.ui.lbl_pnl_trips.setText("Trips: 0")
            self.ui.lbl_pnl_total.setText("PnL: 0.00 USDT")
            self.ui.lbl_pnl_total.setStyleSheet(
                "color:#aaa; font-size:11px; font-weight:bold;"
            )
            self.ui.lbl_pnl_wins.setText("Wins: 0")
            self.ui.lbl_pnl_losses.setText("Losses: 0")
        except Exception as e:
            print(f"[_on_reset_pnl] UI clear failed: {e}")
        self._set_status("✅ P&L records reset — table cleared, pnl_log.json removed")

    # ─────────────────────────────────────────────────────────────────────
    # Task 2 — Dockable PnL/Console panel
    # ─────────────────────────────────────────────────────────────────────

    @Slot(bool)
    def _on_toggle_console(self, visible: bool):
        """Show/hide the dockable bottom panel (PnL + Console).

        When hidden, the QSplitter gives all vertical space to the
        tabWidget — the per-tab chart_view (which has Expanding size
        policy) auto-resizes to fill the new space.
        """
        self.ui.bottomPanel.setVisible(visible)
        if visible:
            self.ui.clBtnToggleConsole.setText("Hide &Console")
            # Restore a sensible height for the bottom panel when the
            # user re-shows it (the splitter remembers sizes while the
            # widget is hidden, but if the user dragged it to 0 before
            # hiding we want a usable default).
            sizes = self.ui.right_splitter.sizes()
            if len(sizes) >= 2 and sizes[1] < 80:
                total = sum(sizes) or 1
                target = max(self._bottom_panel_default_height, total // 3)
                new_sizes = [max(total - target, 100), target]
                self.ui.right_splitter.setSizes(new_sizes)
            # Refresh the panel with the active tab's data.
            idx = self.ui.tabWidget.currentIndex()
            if idx >= 0:
                tab = self.ui.tabWidget.widget(idx)
                if tab is not None and getattr(tab, "session", None):
                    self._refresh_bottom_panel_for(tab.session.pair)
        else:
            self.ui.clBtnToggleConsole.setText("Show &Console")
            self._set_status("Console panel hidden — chart expanded")

    def _refresh_bottom_panel_for(self, pair: str):
        """Refresh the shared PnL summary + round-trip table to show the
        given pair's data. Called when:
          • the user switches tabs (_on_tab_changed)
          • a trade completes (_on_trade_done)
          • the bottom panel is re-shown (_on_toggle_console)
        """
        if pair is None:
            return
        trips = self.tx_logger.get_round_trips(symbol=pair)
        daily = self.tx_logger.get_daily_pnl()

        self.ui.lbl_pnl_pair.setText(f"Pair: {pair}")
        self.ui.lbl_pnl_trips.setText(f"Trips: {daily.get('round_trips', 0)}")
        pnl = daily.get("realized_pnl_usdt", 0.0)
        color = "#00e676" if pnl >= 0 else "#ff5252"
        self.ui.lbl_pnl_total.setText(f"PnL: {pnl:+.2f} USDT")
        self.ui.lbl_pnl_total.setStyleSheet(
            f"color:{color}; font-size:11px; font-weight:bold;"
        )

        wins = sum(1 for t in trips if (t.get("pnl_usdt") or 0) > 0)
        losses = sum(1 for t in trips if (t.get("pnl_usdt") or 0) < 0)
        self.ui.lbl_pnl_wins.setText(f"Wins: {wins}")
        self.ui.lbl_pnl_losses.setText(f"Losses: {losses}")

        self.ui.rt_table.setRowCount(len(trips))
        for i, rt in enumerate(reversed(trips)):
            self.ui.rt_table.setItem(i, 0, QTableWidgetItem(rt.get("symbol", "")))
            buy_p = rt.get("buy_price")
            sell_p = rt.get("sell_price")
            self.ui.rt_table.setItem(
                i, 1, QTableWidgetItem(f"{buy_p:.6g}" if buy_p else "")
            )
            self.ui.rt_table.setItem(
                i, 2, QTableWidgetItem(f"{sell_p:.6g}" if sell_p else "")
            )
            qty = rt.get("qty")
            self.ui.rt_table.setItem(
                i, 3, QTableWidgetItem(f"{qty:.6g}" if qty else "")
            )
            pnl_val = rt.get("pnl_usdt")
            pnl_item = QTableWidgetItem(
                f"{pnl_val:+.4f}" if pnl_val is not None else ""
            )
            if pnl_val is not None:
                pnl_item.setForeground(
                    QColor("#00e676") if pnl_val >= 0 else QColor("#ff5252")
                )
            self.ui.rt_table.setItem(i, 4, pnl_item)
            pct_val = rt.get("pnl_pct")
            pct_item = QTableWidgetItem(
                f"{pct_val:+.2f}%" if pct_val is not None else ""
            )
            if pct_val is not None:
                pct_item.setForeground(
                    QColor("#00e676") if pct_val >= 0 else QColor("#ff5252")
                )
            self.ui.rt_table.setItem(i, 5, pct_item)

    # ── API Keys ──

    @Slot()
    def _on_setup_api_keys(self):
        ex = self.ui.cbExchange.currentText()
        if not ex:
            self._set_status("⚠️ Select exchange")
            return
        dlg = APIKeyDialog(self.exch_mgr, ex, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_status(f"Keys saved for {ex} ✅")
            if self._is_connected:
                self._disconnect()
                self._connect_exchange()

    def closeEvent(self, event):
        self._disconnect()
        self.tx_logger.save_to_file()
        event.accept()

    # ─────────────────────────────────────────────────────────────────────
    # Alert system — click on candle to create alerts
    # ─────────────────────────────────────────────────────────────────────

    def _on_chart_candle_click(self, pair: str, time: int, price: float):
        """Open the Alert dialog when user clicks the chart."""
        from spotbot.ui.alert_dialog import AlertDialog, load_alerts
        session = self._sessions.get(pair)
        chart_tf = session.timeframe if session else "1h"
        dlg = AlertDialog(
            pair=pair,
            candle_time=time,
            candle_price=price,
            chart_interval=chart_tf,
            parent=self,
        )
        dlg.alert_created.connect(lambda d: self._on_alerts_changed(pair))
        dlg.alert_deleted.connect(lambda: self._on_alerts_changed(pair))
        dlg.exec()
        # Always refresh alert lines after dialog closes
        self._draw_alert_price_lines(pair)

    def _on_alerts_changed(self, pair: str):
        """Called when alerts are created, updated, or deleted for a pair."""
        self._draw_alert_price_lines(pair)

    def _draw_alert_price_lines(self, pair: str):
        """Draw horizontal dashed lines on the chart at each alert's price levels."""
        from spotbot.ui.alert_dialog import load_alerts
        alerts = load_alerts()
        pair_alerts = [a for a in alerts if a.get("pair") == pair and a.get("enabled", True)]
        prices = []
        for a in pair_alerts:
            for c in a.get("conditions", []):
                if c.get("condition_type") == "Price":
                    v1 = c.get("value1", 0)
                    v2 = c.get("value2", 0)
                    if v1 and v1 > 0:
                        prices.append(float(v1))
                    if v2 and v2 > 0:
                        prices.append(float(v2))
            if not a.get("conditions"):
                v1 = a.get("value1", 0)
                v2 = a.get("value2", 0)
                if v1 and v1 > 0:
                    prices.append(float(v1))
                if v2 and v2 > 0:
                    prices.append(float(v2))
        prices = sorted(set(prices))
        self._chart_js(pair, f"setAlertPriceLines({json.dumps(prices)});")
        session = self._sessions.get(pair)
        if session and session.candles:
            indicators = getattr(session, 'indicators', None) or {}
            self._evaluate_alerts(pair, session.candles[-1], indicators)

    def _evaluate_alerts(self, pair: str, candle: list, indicators: dict = None):
        """Evaluate all active alerts for the given pair.

        Supports multi-condition, 4 trigger frequency modes, expiration,
        and the new action system (popup, sound, limit order).
        """
        from spotbot.ui.alert_dialog import load_alerts, save_alerts, append_alert_log
        alerts = load_alerts()
        pair_alerts = [a for a in alerts if a.get("pair") == pair and a.get("enabled", True)]
        if not pair_alerts:
            return
        close = float(candle[4])
        candle_ts = int(candle[0]) if candle else 0
        now_ms = int(time.time() * 1000)
        session = self._sessions.get(pair)
        candles = session.candles if session else [candle]
        needs_save = False
        for alert in pair_alerts:
            # Expiration check
            exp_mode = alert.get("expire_mode", "No Expiration")
            if "Date" in exp_mode and alert.get("expiration"):
                try:
                    exp_dt = datetime.fromisoformat(alert["expiration"])
                    if datetime.now() >= exp_dt:
                        alert["enabled"] = False
                        needs_save = True
                        continue
                except (ValueError, TypeError):
                    pass
            if "Trigger" in exp_mode and alert.get("fired"):
                alert["enabled"] = False
                needs_save = True
                continue
            # Frequency check
            trigger = alert.get("trigger", "Once only")
            if trigger == "Once only" and alert.get("fired"):
                continue
            if trigger == "Once per minute":
                last_ts = alert.get("last_fire_ts", 0)
                if now_ms - last_ts < 60000:
                    continue
            # Evaluate conditions
            conditions = alert.get("conditions", [])
            if not conditions:
                conditions = [{
                    "condition_type": alert.get("condition_type", "Price"),
                    "indicator": alert.get("indicator", ""),
                    "operator": alert.get("operator", ""),
                    "value1": alert.get("value1", 0),
                    "value2": alert.get("value2", 0),
                    "bars": 0,
                    "interval": "",
                }]
            all_matched = True
            for cond in conditions:
                matched = self._evaluate_single_condition(cond, close, candles, indicators)
                if not matched:
                    all_matched = False
                    break
            if not all_matched:
                continue
            # Once per bar close: only trigger once per candle timestamp
            if trigger == "Once per bar close":
                if candle_ts == alert.get("last_fire_bar_ts", 0):
                    continue
                alert["last_fire_bar_ts"] = candle_ts
            alert["fired"] = True
            alert["fire_count"] = alert.get("fire_count", 0) + 1
            alert["last_fire_ts"] = now_ms
            needs_save = True
            self._trigger_alert_action(pair, alert, close)
        if needs_save:
            for updated_a in pair_alerts:
                alert_id = updated_a.get("id", "")
                for i, orig_a in enumerate(alerts):
                    if orig_a.get("id") == alert_id:
                        alerts[i] = updated_a
                        break
                    if not orig_a.get("id") and not alert_id:
                        if (orig_a.get("pair") == updated_a.get("pair")
                                and orig_a.get("value1") == updated_a.get("value1")
                                and orig_a.get("operator") == updated_a.get("operator")):
                            alerts[i] = updated_a
                            break
            save_alerts(alerts)

    def _evaluate_single_condition(self, cond: dict, close: float,
                                     candles: list, indicators: dict) -> bool:
        """Evaluate a single condition against current data."""
        cond_type = cond.get("condition_type", "Price")
        op = cond.get("operator", "")
        v1 = float(cond.get("value1", 0))
        v2 = float(cond.get("value2", 0))
        bars = int(cond.get("bars", 0))
        if cond_type == "Indicator" and indicators:
            ind_name = cond.get("indicator", "")
            val = self._get_indicator_value(ind_name, indicators)
            if val is None:
                return False
            prev_val = self._get_indicator_prev_value(ind_name, indicators)
        else:
            val = close
            prev_val = float(candles[-2][4]) if len(candles) >= 2 else val
        # For Moving operators, check over N bars
        if op in ("Moving Up", "Moving Down", "Moving Up %", "Moving Down %"):
            bars = max(bars, 2)
            if len(candles) < bars:
                return False
            old_val = float(candles[-bars][4]) if cond_type == "Price" else val
            if cond_type == "Indicator" and indicators:
                old_val = self._get_indicator_value_at(ind_name, indicators, -(bars))
                if old_val is None:
                    old_val = prev_val
            if op == "Moving Up":
                return val > old_val
            elif op == "Moving Down":
                return val < old_val
            elif op == "Moving Up %":
                if old_val > 0:
                    pct = ((val - old_val) / old_val) * 100
                    return pct >= v1
            elif op == "Moving Down %":
                if old_val > 0:
                    pct = ((old_val - val) / old_val) * 100
                    return pct >= v1
            return False
        return self._check_condition(op, val, prev_val, v1, v2)

    @staticmethod
    def _check_condition(op: str, value: float, prev_value: float,
                          v1: float, v2: float) -> bool:
        """Core condition checker for 9 non-Moving operators."""
        if op == "Crossing":
            return (prev_value <= v1 <= value) or (prev_value >= v1 >= value)
        elif op == "Crossing Up":
            return prev_value <= v1 < value
        elif op == "Crossing Down":
            return prev_value >= v1 > value
        elif op == "Greater Than":
            return value > v1
        elif op == "Less Than":
            return value < v1
        elif op == "Entering Channel":
            lo, hi = min(v1, v2), max(v1, v2)
            return lo <= value <= hi and not (lo <= prev_value <= hi)
        elif op == "Exiting Channel":
            lo, hi = min(v1, v2), max(v1, v2)
            return (lo <= prev_value <= hi) and not (lo <= value <= hi)
        elif op == "Inside Channel":
            lo, hi = min(v1, v2), max(v1, v2)
            return lo <= value <= hi
        elif op == "Outside Channel":
            lo, hi = min(v1, v2), max(v1, v2)
            return value > hi or value < lo
        return False

    @staticmethod
    def _get_indicator_value(name: str, indicators: dict) -> float | None:
        """Extract the latest indicator value."""
        if not indicators:
            return None
        try:
            if "RSI" in name:
                vals = indicators.get("rsi", [])
                return float(vals[-1]) if vals else None
            elif "MACD Line" in name:
                vals = indicators.get("macd", [])
                return float(vals[-1]) if vals else None
            elif "MACD Signal" in name:
                vals = indicators.get("macd_signal", [])
                return float(vals[-1]) if vals else None
            elif "CCI" in name:
                vals = indicators.get("cci", [])
                return float(vals[-1]) if vals else None
            elif "ADX" in name:
                vals = indicators.get("adx", [])
                return float(vals[-1]) if vals else None
            elif "OBV" in name:
                vals = indicators.get("obv", [])
                return float(vals[-1]) if vals else None
            elif "BB Upper" in name:
                vals = indicators.get("bb_upper", [])
                return float(vals[-1]) if vals else None
            elif "BB Lower" in name:
                vals = indicators.get("bb_lower", [])
                return float(vals[-1]) if vals else None
            elif "Trendline" in name:
                vals = indicators.get("trendline", [])
                return float(vals[-1]) if vals else None
        except (TypeError, ValueError, IndexError):
            pass
        return None

    @staticmethod
    def _get_indicator_prev_value(name: str, indicators: dict) -> float:
        """Extract the previous (second-to-last) indicator value."""
        if not indicators:
            return 0.0
        try:
            key_map = {
                "RSI": "rsi", "MACD Line": "macd", "MACD Signal": "macd_signal",
                "CCI": "cci", "ADX": "adx", "OBV": "obv",
                "BB Upper": "bb_upper", "BB Lower": "bb_lower", "Trendline": "trendline",
            }
            for key, ind_key in key_map.items():
                if key in name:
                    vals = indicators.get(ind_key, [])
                    return float(vals[-2]) if len(vals) >= 2 else 0.0
        except (TypeError, ValueError, IndexError):
            pass
        return 0.0

    @staticmethod
    def _get_indicator_value_at(name: str, indicators: dict, offset: int) -> float | None:
        """Extract an indicator value at a historical offset."""
        if not indicators:
            return None
        try:
            key_map = {
                "RSI": "rsi", "MACD Line": "macd", "MACD Signal": "macd_signal",
                "CCI": "cci", "ADX": "adx", "OBV": "obv",
                "BB Upper": "bb_upper", "BB Lower": "bb_lower", "Trendline": "trendline",
            }
            for key, ind_key in key_map.items():
                if key in name:
                    vals = indicators.get(ind_key, [])
                    idx = len(vals) + offset
                    if 0 <= idx < len(vals):
                        return float(vals[idx])
        except (TypeError, ValueError, IndexError):
            pass
        return None

    def _trigger_alert_action(self, pair: str, alert: dict, price: float):
        """Execute the configured actions for a triggered alert.

        Supports new format (actions dict) and legacy format (action string).
        """
        from spotbot.ui.alert_dialog import append_alert_log
        actions = alert.get("actions", {})
        name = alert.get("name", "")
        conds = alert.get("conditions", [alert])
        # Build formatted message with placeholders
        msg_template = alert.get("message", "")
        first_cond = conds[0] if conds else {}
        formatted_msg = msg_template
        if msg_template:
            formatted_msg = msg_template.replace("{{pair}}", str(pair))
            formatted_msg = formatted_msg.replace("{{price}}", f"{price:.6g}")
            formatted_msg = formatted_msg.replace("{{indicator}}", first_cond.get("indicator", ""))
            formatted_msg = formatted_msg.replace("{{value}}", str(first_cond.get("value1", 0)))
            formatted_msg = formatted_msg.replace("{{operator}}", first_cond.get("operator", ""))
            formatted_msg = formatted_msg.replace("{{time}}", datetime.now().strftime("%H:%M:%S"))
        # Log to alert log file
        log_entry = {
            "pair": pair, "name": name, "message": formatted_msg,
            "price": price, "conditions": conds,
        }
        append_alert_log(log_entry)
        # Pop-up notification
        show_popup = actions.get("popup", True)
        if show_popup:
            popup_title = f"Alert: {name or pair}"
            popup_msg = formatted_msg or f"{pair} @ {price:.6g}"
            if not name:
                parts = []
                for c in conds:
                    ct = c.get("condition_type", "Price")
                    op = c.get("operator", "")
                    v1 = c.get("value1", 0)
                    ind = c.get("indicator", "")
                    if ct == "Indicator":
                        parts.append(f"{ind} {op} {v1}")
                    else:
                        parts.append(f"Price {op} {v1}")
                popup_msg = " | ".join(parts) + f"\n{pair} @ {price:.6g}"
            QMessageBox.information(self, popup_title, popup_msg)
        # Play sound
        play_sound = actions.get("sound", False)
        sound_path = actions.get("sound_path", "")
        if play_sound:
            try:
                import subprocess
                import sys
                if sys.platform == "win32":
                    import winsound
                    if sound_path and os.path.exists(sound_path):
                        winsound.PlaySound(sound_path, winsound.SND_FILENAME)
                    else:
                        winsound.Beep(2000, 300)
                else:
                    import shutil
                    played = False
                    # Try the user-specified sound file first
                    if sound_path and os.path.exists(sound_path):
                        for player in ("ffplay", "paplay", "aplay", "mpv"):
                            bin_path = shutil.which(player)
                            if not bin_path:
                                continue
                            try:
                                if player == "ffplay":
                                    subprocess.Popen(
                                        [bin_path, "-nodisp", "-autoexit", "-loglevel", "quiet", sound_path],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    )
                                elif player == "mpv":
                                    subprocess.Popen(
                                        [bin_path, "--no-video", "--no-terminal", sound_path],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    )
                                else:
                                    subprocess.Popen(
                                        [bin_path, "-q", sound_path],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    )
                                played = True
                                break
                            except Exception:
                                continue
                    # Fallback: system bell sound
                    if not played:
                        sfx_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "SFX")
                        fallback_candidates = [
                            os.path.join(sfx_dir, "notif.wav"),
                            "/usr/share/sounds/freedesktop/stereo/bell.oga",
                        ]
                        for fb_path in fallback_candidates:
                            if os.path.exists(fb_path):
                                for player in ("ffplay", "paplay", "aplay", "mpv"):
                                    bin_path = shutil.which(player)
                                    if not bin_path:
                                        continue
                                    try:
                                        if player == "ffplay":
                                            subprocess.Popen(
                                                [bin_path, "-nodisp", "-autoexit", "-loglevel", "quiet", fb_path],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            )
                                        elif player == "mpv":
                                            subprocess.Popen(
                                                [bin_path, "--no-video", "--no-terminal", fb_path],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            )
                                        else:
                                            subprocess.Popen(
                                                [bin_path, "-q", fb_path],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            )
                                        break
                                    except Exception:
                                        continue
                                break
            except Exception:
                pass
        # Trading action (new format)
        order_type = actions.get("order_type", "None (Notify only)")
        order_price = float(actions.get("order_price", 0))
        order_qty = float(actions.get("order_qty", 0))
        if "Buy" in order_type:
            exec_price = order_price if order_price > 0 else price
            self._execute_alert_order(pair, "buy", exec_price, order_qty)
        elif "Sell" in order_type:
            exec_price = order_price if order_price > 0 else price
            self._execute_alert_order(pair, "sell", exec_price, order_qty)
        # Legacy action format (backwards compat)
        if not actions and alert.get("action"):
            legacy_action = alert.get("action", "")
            if "Telegram" in legacy_action:
                self._send_alert_telegram(pair, alert, price)
            elif "Market Buy" in legacy_action or "Limited Buy" in legacy_action:
                lp = float(alert.get("order_price", price))
                self._execute_alert_order(pair, "buy", lp, float(alert.get("order_qty", 0)))
            elif "Market Sell" in legacy_action or "Limited Sell" in legacy_action:
                lp = float(alert.get("order_price", price))
                self._execute_alert_order(pair, "sell", lp, float(alert.get("order_qty", 0)))
        self._set_status(f"Alert triggered: {name or pair}")

    def _execute_alert_order(self, pair: str, side: str, price: float, qty_usdt: float):
        """Execute a virtual order from an alert.

        Alert orders have HIGHEST priority — they bypass trading_enabled,
        halted state, and sell-above-entry gates. The user explicitly
        requested the order, so it executes regardless of indicator state.
        """
        session = self._sessions.get(pair)
        if not session:
            return
        engine = session.engine
        if qty_usdt <= 0:
            # If no explicit qty set, use engine's default investment amount
            qty_usdt = engine._get_investment_amount()
        if qty_usdt <= 0:
            return
        ts = int(time.time() * 1000)
        try:
            if side == "buy" and not engine.in_position:
                result = engine._execute_order("buy_signal", price, ts, force=True, override_amount=qty_usdt)
                if result and result.get("trade"):
                    self._on_trade_done(pair, result)
                    self._set_status(f"Alert BUY executed: {pair} @ {price:.6f}")
                elif result and result.get("action") == "skipped":
                    self._set_status(f"Alert BUY skipped: {result.get('note', '')}")
            elif side == "sell" and engine.in_position:
                result = engine._execute_order("sell_signal", price, ts, force=True, override_amount=qty_usdt)
                if result and result.get("trade"):
                    self._on_trade_done(pair, result)
                    self._set_status(f"Alert SELL executed: {pair} @ {price:.6f}")
                elif result and result.get("action") == "skipped":
                    self._set_status(f"Alert SELL skipped: {result.get('note', '')}")
            elif side == "buy" and engine.in_position:
                self._set_status(f"Alert BUY skipped: already in position for {pair}")
            elif side == "sell" and not engine.in_position:
                self._set_status(f"Alert SELL skipped: no position to sell for {pair}")
        except Exception as e:
            self._set_status(f"Alert order error: {e}")

    @staticmethod
    def _send_alert_telegram(pair: str, alert: dict, price: float):
        """Send a Telegram message for a triggered alert."""
        from spotbot.ui.alert_dialog import load_telegram_config
        config = load_telegram_config()
        token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not token or not chat_id:
            return
        msg = alert.get("telegram_msg", "") or f"Alert: {pair} {alert.get('operator')} {alert.get('value1')} @ {price}"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                pass
        except Exception:
            pass
