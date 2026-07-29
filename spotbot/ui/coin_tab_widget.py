"""Closable tab widget that hosts CoinSession instances."""

from PySide6.QtCore import Signal, Slot, QUrl, Qt
from PySide6.QtWidgets import (
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
)
from PySide6.QtWebEngineWidgets import QWebEngineView


class CoinTabWidget(QWidget):
    """A closable tab that hosts a chart + status bar for one trading pair."""

    trading_toggled = Signal(str, bool)  # pair, enabled

    def __init__(self, session: "CoinSession", parent=None):
        super().__init__(parent)
        self.session = session
        self._chart_ready = False
        self._chart_js_queue: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Chart view ──
        self.chart_view = QWebEngineView()
        self.chart_view.setUrl(QUrl("about:blank"))
        self.chart_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.chart_view.loadFinished.connect(self._on_chart_loaded)
        layout.addWidget(self.chart_view, stretch=1)

        # ── Status bar ──
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

        self.btn_toggle_trading = QPushButton("🔴 Not Trading")
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

        # NOTE: The PnL summary box, round-trip table, and footer status
        # bar used to live inside each tab. They have been promoted to a
        # single shared dockable panel at the bottom of the MainWindow so
        # the user can show/hide them and let the chart_view auto-resize.
        # See MainWindow._build_bottom_panel / _refresh_bottom_panel_for.

    # ── Chart helpers ──

    @Slot(bool)
    def _on_chart_loaded(self, ok):
        self._chart_ready = bool(ok)
        if not ok:
            return
        pending, self._chart_js_queue = self._chart_js_queue, []
        for code in pending:
            self.chart_view.page().runJavaScript(code)

    def load_chart_html(self, html: str):
        self._chart_ready = False
        self._chart_js_queue.clear()
        self.chart_view.setHtml(html)

    def chart_js(self, code: str):
        if self._chart_ready:
            self.chart_view.page().runJavaScript(code)
        else:
            self._chart_js_queue.append(code)

    # ── Status helpers ──

    def set_status(self, msg: str):
        self.lbl_status.setText(msg)

    def set_balance(self, balance: float):
        self.lbl_balance.setText(f"{balance:.4f}")

    def _on_toggle_trading(self):
        pair = self.session.pair
        new_state = not self.session.trading_enabled
        self.trading_toggled.emit(pair, new_state)

    def show_trading_started(self):
        self.btn_toggle_trading.setText("🟢 Trading")
        self.btn_toggle_trading.setStyleSheet(
            "QPushButton{background:#1b5e20; color:#fff; border:1px solid #4caf50;"
            " border-radius:4px; font-weight:bold;}"
            "QPushButton:hover{background:#2e7d32;}"
        )
        self.lbl_status.setText("Trading ON")

    def show_trading_stopped(self):
        self.btn_toggle_trading.setText("🔴 Not Trading")
        self.btn_toggle_trading.setStyleSheet(
            "QPushButton{background:#b71c1c; color:#fff; border:1px solid #f44336;"
            " border-radius:4px; font-weight:bold;}"
            "QPushButton:hover{background:#c62828;}"
        )
        self.lbl_status.setText("Trading OFF")
