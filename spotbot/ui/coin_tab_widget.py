"""Closable tab widget that hosts CoinSession instances."""

from PySide6.QtCore import Signal, Slot, QUrl, QTimer, QObject
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

from spotbot.ui.coin_session import CoinSession


class _ChartBridge(QObject):
    """Bridge object exposed to JS via QWebChannel.

    JS calls ``Qt.onChartCandleClick(time, price)`` which arrives here.
    """
    candle_clicked = Signal(int, float)  # time, price

    @Slot(int, float)
    def onChartCandleClick(self, time: int, price: float):
        self.candle_clicked.emit(time, price)


class CoinTabWidget(QWidget):
    """A closable tab that hosts a chart + status bar for one trading pair."""

    trading_toggled = Signal(str, bool)  # pair, enabled
    candle_clicked = Signal(str, int, float)  # pair, time, price

    def __init__(self, session: "CoinSession", parent=None):
        super().__init__(parent)
        self.session = session
        self._chart_ready = False
        self._chart_js_queue: list[str] = []
        self._html_loaded = False  # True only after load_chart_html() is called

        # WebChannel bridge for JS->Python communication
        self._bridge = _ChartBridge(self)
        self._bridge.candle_clicked.connect(self._on_bridge_candle_click)
        self._channel = QWebChannel(self)
        self._channel.registerObject("Qt", self._bridge)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Chart view
        self.chart_view = QWebEngineView()
        self.chart_view.setUrl(QUrl("about:blank"))
        self.chart_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.chart_view.page().setWebChannel(self._channel)
        self.chart_view.loadFinished.connect(self._on_chart_loaded)
        layout.addWidget(self.chart_view, stretch=1)

        # Status bar
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 2)
        self.lbl_pair = QLabel(session.pair)
        self.lbl_pair.setStyleSheet("color:#00e676; font-weight:bold;")
        self.lbl_status = QLabel("Idle")
        self.lbl_balance = QLabel("0.00")
        self.lbl_status.setStyleSheet("color:#aaa;")

        bar.addWidget(self.lbl_pair)
        bar.addWidget(self.lbl_status, stretch=1)
        bar.addWidget(self.lbl_balance)

        self.btn_toggle_trading = QPushButton("Not Trading")
        self.btn_toggle_trading.setFixedSize(110, 28)
        self.btn_toggle_trading.setToolTip("Toggle automated trading ON / OFF")
        self.btn_toggle_trading.setStyleSheet(
            "QPushButton{background:#b71c1c; color:#fff; border:1px solid #f44336;"
            " border-radius:4px; font-weight:bold;}"
            "QPushButton:hover{background:#c62828;}"
        )
        self.btn_toggle_trading.clicked.connect(self._on_toggle_trading)

        bar.addWidget(self.btn_toggle_trading)
        layout.addLayout(bar)

    def _on_bridge_candle_click(self, time: int, price: float):
        self.candle_clicked.emit(self.session.pair, time, price)

    # Chart helpers

    @Slot(bool)
    def _on_chart_loaded(self, ok):
        if not ok:
            return
        # Ignore loadFinished from about:blank — only process real chart HTML
        if not self._html_loaded:
            return

        self._do_ready_check()

    def _do_ready_check(self):
        """Poll _pageReady and flush the JS queue once the page is ready."""
        def _check(result):
            if not self._html_loaded:
                return  # Chart was reset; stop polling
            if result:
                self._chart_ready = True
                pending, self._chart_js_queue = self._chart_js_queue, []
                for code in pending:
                    self.chart_view.page().runJavaScript(code)
            else:
                QTimer.singleShot(50, self._do_ready_check)

        self.chart_view.page().runJavaScript(
            "typeof _pageReady !== 'undefined' && _pageReady", _check
        )

    def load_chart_html(self, html: str):
        self._chart_ready = False
        self._html_loaded = True  # Mark that real chart HTML is being loaded
        self._chart_js_queue.clear()
        self.chart_view.setHtml(html)

    def chart_js(self, code: str):
        safe_code = f"try {{ {code} }} catch(_e) {{ console.warn('chart JS error:', _e.message || _e, _e.stack); }}"
        if self._chart_ready:
            self.chart_view.page().runJavaScript(safe_code)
        else:
            self._chart_js_queue.append(safe_code)

    # Status helpers

    def set_status(self, msg: str):
        self.lbl_status.setText(msg)

    def set_balance(self, balance: float):
        self.lbl_balance.setText(f"{balance:.4f}")

    def _on_toggle_trading(self):
        pair = self.session.pair
        new_state = not self.session.trading_enabled
        self.trading_toggled.emit(pair, new_state)

    def show_trading_started(self):
        self.btn_toggle_trading.setText("Trading")
        self.btn_toggle_trading.setStyleSheet(
            "QPushButton{background:#1b5e20; color:#fff; border:1px solid #4caf50;"
            " border-radius:4px; font-weight:bold;}"
            "QPushButton:hover{background:#2e7d32;}"
        )
        self.lbl_status.setText("Trading ON")

    def show_trading_stopped(self):
        self.btn_toggle_trading.setText("Not Trading")
        self.btn_toggle_trading.setStyleSheet(
            "QPushButton{background:#b71c1c; color:#fff; border:1px solid #f44336;"
            " border-radius:4px; font-weight:bold;}"
            "QPushButton:hover{background:#c62828;}"
        )
        self.lbl_status.setText("Trading OFF")
