"""QDialog for editing FLI/SAI indicator parameters at runtime.

Changes take effect immediately for the next indicator computation cycle
(FLI chart refresh, backtest, or new candle evaluation).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


# Default values (must match constants.py)
DEFAULTS = {
    "signal_source": "fli",  # "fli" = FLI indicators, "rsi_macd" = RSI+MACD
    "bb_period": 19,
    "bb_dev": 0.6,
    "use_atr": True,
    "atr_period": 9,
}


class IndicatorParamsDialog(QDialog):
    """Modal dialog to view / edit FLI indicator parameters.

    Simplified: only BB and ATR params. CCI/ADX/OBV removed.
    """

    params_changed = Signal(dict)  # emitted on OK with the full param dict

    def __init__(self, current_params: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("\u2699 FLI Parameters")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setStyleSheet(
            "QDialog{background:#0b0e11;}"
            "QLabel{color:#eaecef; font-size:12px;}"
            "QGroupBox{color:#f0a500; border:1px solid #2b3139; border-radius:6px;"
            " margin-top:10px; padding-top:14px; font-weight:bold;}"
            "QGroupBox::title{subcontrol-origin:margin; left:12px; padding:0 6px;}"
            "QSpinBox, QDoubleSpinBox{background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            " border-radius:4px; padding:4px; min-height:24px;}"
            "QCheckBox{color:#eaecef; font-size:12px; spacing:8px;}"
            "QCheckBox::indicator{width:16px; height:16px;}"
            "QPushButton{background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            " border-radius:4px; padding:8px 20px; font-weight:bold;}"
            "QPushButton:hover{background:#2b3139;}"
            "QComboBox{background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            " border-radius:4px; padding:4px; min-height:24px;}"
            "QComboBox QAbstractItemView{background:#1e2329; color:#eaecef; selection-background-color:#2b3139;}"
        )

        params = dict(DEFAULTS)
        if current_params:
            params.update(current_params)

        # ── Main layout: vertical (title row + groups + buttons) ──
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── Top row: Signal Source (full width) ──
        grp_src = QGroupBox("Trading Signal Source")
        form_src = QFormLayout(grp_src)
        form_src.setContentsMargins(8, 14, 8, 8)
        self.cb_signal_source = QComboBox()
        self.cb_signal_source.addItem("FLI (Trendline + BB + ATR)", "fli")
        self.cb_signal_source.addItem("RSI + MACD (classic)", "rsi_macd")
        idx = self.cb_signal_source.findData(params.get("signal_source", "fli"))
        if idx >= 0:
            self.cb_signal_source.setCurrentIndex(idx)
        self.cb_signal_source.setToolTip(
            "FLI: uses trendline reversals (1=buy, -1=sell, 0=wait)\n"
            "RSI+MACD: uses RSI oversold/overbought and MACD crossovers"
        )
        form_src.addRow("Source:", self.cb_signal_source)
        main_layout.addWidget(grp_src)

        # ── Grid layout: 2 columns for BB and ATR ──
        grid = QGridLayout()
        grid.setSpacing(8)

        # Col 0: Bollinger Bands
        grp_bb = QGroupBox("Bollinger Bands")
        form_bb = QFormLayout(grp_bb)
        form_bb.setContentsMargins(8, 14, 8, 8)
        self.sp_bb_period = QSpinBox()
        self.sp_bb_period.setRange(5, 100)
        self.sp_bb_period.setValue(int(params["bb_period"]))
        form_bb.addRow("BB Period:", self.sp_bb_period)

        self.sp_bb_dev = QDoubleSpinBox()
        self.sp_bb_dev.setRange(0.1, 5.0)
        self.sp_bb_dev.setSingleStep(0.1)
        self.sp_bb_dev.setDecimals(2)
        self.sp_bb_dev.setValue(float(params["bb_dev"]))
        form_bb.addRow("BB Deviation:", self.sp_bb_dev)
        grid.addWidget(grp_bb, 0, 0)

        # Col 1: ATR
        grp_atr = QGroupBox("ATR (Average True Range)")
        form_atr = QFormLayout(grp_atr)
        form_atr.setContentsMargins(8, 14, 8, 8)
        self.cb_use_atr = QCheckBox("Enable ATR filter")
        self.cb_use_atr.setChecked(bool(params["use_atr"]))
        form_atr.addRow(self.cb_use_atr)

        self.sp_atr_period = QSpinBox()
        self.sp_atr_period.setRange(2, 100)
        self.sp_atr_period.setValue(int(params["atr_period"]))
        form_atr.addRow("ATR Period:", self.sp_atr_period)
        grid.addWidget(grp_atr, 0, 1)

        main_layout.addLayout(grid, 1)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("\u2705 Apply")
        self.btn_apply.setDefault(True)
        self.btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(self.btn_apply)

        main_layout.addLayout(btn_row)

    def _apply(self):
        """Collect values and accept the dialog."""
        self.params_changed.emit(self.get_params())
        self.accept()

    def get_params(self) -> dict:
        """Return the current parameter values as a dict."""
        return {
            "signal_source": self.cb_signal_source.currentData(),
            "bb_period": self.sp_bb_period.value(),
            "bb_dev": self.sp_bb_dev.value(),
            "use_atr": self.cb_use_atr.isChecked(),
            "atr_period": self.sp_atr_period.value(),
        }
