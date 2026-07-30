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
    "use_cci": True,
    "cci_len": 20,
    "cci_level": 100.0,
    "cci_buffer": 0.0,
    "use_adx": True,
    "adx_len": 14,
    "adx_level": 20,
    "use_obv": True,
    "obv_sma_len": 15,
    "min_score": 1,
}


class IndicatorParamsDialog(QDialog):
    """Modal dialog to view / edit FLI indicator parameters.

    Usage::

        dlg = IndicatorParamsDialog(current_params, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_params = dlg.get_params()
    """

    params_changed = Signal(dict)  # emitted on OK with the full param dict

    def __init__(self, current_params: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙ Indicator Parameters")
        self.setMinimumWidth(420)
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
        )

        params = dict(DEFAULTS)
        if current_params:
            params.update(current_params)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── Signal Source ──
        grp_src = QGroupBox("Trading Signal Source")
        form_src = QFormLayout(grp_src)
        self.cb_signal_source = QComboBox()
        self.cb_signal_source.addItem("FLI (Trendline + BB + CCI + ADX + OBV)", "fli")
        self.cb_signal_source.addItem("RSI + MACD (classic)", "rsi_macd")
        idx = self.cb_signal_source.findData(params.get("signal_source", "fli"))
        if idx >= 0:
            self.cb_signal_source.setCurrentIndex(idx)
        self.cb_signal_source.setToolTip(
            "FLI: uses trendline reversals confirmed by CCI/ADX/OBV\n"
            "RSI+MACD: uses RSI oversold/overbought and MACD crossovers"
        )
        form_src.addRow("Source:", self.cb_signal_source)
        layout.addWidget(grp_src)

        # ── Bollinger Bands ──
        grp_bb = QGroupBox("Bollinger Bands")
        form_bb = QFormLayout(grp_bb)
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
        layout.addWidget(grp_bb)

        # ── ATR ──
        grp_atr = QGroupBox("ATR (Average True Range)")
        form_atr = QFormLayout(grp_atr)
        self.cb_use_atr = QCheckBox("Enable ATR filter")
        self.cb_use_atr.setChecked(bool(params["use_atr"]))
        form_atr.addRow(self.cb_use_atr)

        self.sp_atr_period = QSpinBox()
        self.sp_atr_period.setRange(2, 100)
        self.sp_atr_period.setValue(int(params["atr_period"]))
        form_atr.addRow("ATR Period:", self.sp_atr_period)
        layout.addWidget(grp_atr)

        # ── CCI ──
        grp_cci = QGroupBox("CCI (Commodity Channel Index)")
        form_cci = QFormLayout(grp_cci)
        self.cb_use_cci = QCheckBox("Enable CCI filter")
        self.cb_use_cci.setChecked(bool(params["use_cci"]))
        form_cci.addRow(self.cb_use_cci)

        self.sp_cci_len = QSpinBox()
        self.sp_cci_len.setRange(2, 100)
        self.sp_cci_len.setValue(int(params["cci_len"]))
        form_cci.addRow("CCI Length:", self.sp_cci_len)

        self.sp_cci_level = QDoubleSpinBox()
        self.sp_cci_level.setRange(10.0, 500.0)
        self.sp_cci_level.setSingleStep(10.0)
        self.sp_cci_level.setDecimals(1)
        self.sp_cci_level.setValue(float(params["cci_level"]))
        form_cci.addRow("CCI Level:", self.sp_cci_level)

        self.sp_cci_buffer = QDoubleSpinBox()
        self.sp_cci_buffer.setRange(0.0, 50.0)
        self.sp_cci_buffer.setSingleStep(1.0)
        self.sp_cci_buffer.setDecimals(1)
        self.sp_cci_buffer.setValue(float(params["cci_buffer"]))
        form_cci.addRow("CCI Buffer:", self.sp_cci_buffer)
        layout.addWidget(grp_cci)

        # ── ADX ──
        grp_adx = QGroupBox("ADX (Average Directional Index)")
        form_adx = QFormLayout(grp_adx)
        self.cb_use_adx = QCheckBox("Enable ADX filter")
        self.cb_use_adx.setChecked(bool(params["use_adx"]))
        form_adx.addRow(self.cb_use_adx)

        self.sp_adx_len = QSpinBox()
        self.sp_adx_len.setRange(2, 100)
        self.sp_adx_len.setValue(int(params["adx_len"]))
        form_adx.addRow("ADX Length:", self.sp_adx_len)

        self.sp_adx_level = QDoubleSpinBox()
        self.sp_adx_level.setRange(5.0, 100.0)
        self.sp_adx_level.setSingleStep(5.0)
        self.sp_adx_level.setDecimals(1)
        self.sp_adx_level.setValue(float(params["adx_level"]))
        form_adx.addRow("ADX Level:", self.sp_adx_level)
        layout.addWidget(grp_adx)

        # ── OBV ──
        grp_obv = QGroupBox("OBV (On-Balance Volume)")
        form_obv = QFormLayout(grp_obv)
        self.cb_use_obv = QCheckBox("Enable OBV filter")
        self.cb_use_obv.setChecked(bool(params["use_obv"]))
        form_obv.addRow(self.cb_use_obv)

        self.sp_obv_sma_len = QSpinBox()
        self.sp_obv_sma_len.setRange(2, 200)
        self.sp_obv_sma_len.setValue(int(params["obv_sma_len"]))
        form_obv.addRow("OBV SMA Length:", self.sp_obv_sma_len)
        layout.addWidget(grp_obv)

        # ── Score ──
        grp_score = QGroupBox("Signal Score")
        form_score = QFormLayout(grp_score)
        self.sp_min_score = QSpinBox()
        self.sp_min_score.setRange(0, 3)
        self.sp_min_score.setToolTip(
            "Minimum confirmations required (out of enabled filters: CCI, ADX, OBV).\n"
            "0 = any signal passes, 1 = at least 1 filter must confirm, etc."
        )
        self.sp_min_score.setValue(int(params["min_score"]))
        form_score.addRow("Min Score:", self.sp_min_score)
        layout.addWidget(grp_score)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_reset = QPushButton("↩ Reset Defaults")
        self.btn_reset.setToolTip("Reset all parameters to their default values")
        self.btn_reset.clicked.connect(self._reset_to_defaults)
        btn_row.addWidget(self.btn_reset)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("✅ Apply")
        self.btn_apply.setDefault(True)
        self.btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(self.btn_apply)

        layout.addLayout(btn_row)

    def _reset_to_defaults(self):
        """Reset all spinboxes/checkboxes to default values."""
        idx = self.cb_signal_source.findData(DEFAULTS["signal_source"])
        if idx >= 0:
            self.cb_signal_source.setCurrentIndex(idx)
        self.sp_bb_period.setValue(DEFAULTS["bb_period"])
        self.sp_bb_dev.setValue(DEFAULTS["bb_dev"])
        self.cb_use_atr.setChecked(DEFAULTS["use_atr"])
        self.sp_atr_period.setValue(DEFAULTS["atr_period"])
        self.cb_use_cci.setChecked(DEFAULTS["use_cci"])
        self.sp_cci_len.setValue(DEFAULTS["cci_len"])
        self.sp_cci_level.setValue(DEFAULTS["cci_level"])
        self.sp_cci_buffer.setValue(DEFAULTS["cci_buffer"])
        self.cb_use_adx.setChecked(DEFAULTS["use_adx"])
        self.sp_adx_len.setValue(DEFAULTS["adx_len"])
        self.sp_adx_level.setValue(DEFAULTS["adx_level"])
        self.cb_use_obv.setChecked(DEFAULTS["use_obv"])
        self.sp_obv_sma_len.setValue(DEFAULTS["obv_sma_len"])
        self.sp_min_score.setValue(DEFAULTS["min_score"])

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
            "use_cci": self.cb_use_cci.isChecked(),
            "cci_len": self.sp_cci_len.value(),
            "cci_level": self.sp_cci_level.value(),
            "cci_buffer": self.sp_cci_buffer.value(),
            "use_adx": self.cb_use_adx.isChecked(),
            "adx_len": self.sp_adx_len.value(),
            "adx_level": self.sp_adx_level.value(),
            "use_obv": self.cb_use_obv.isChecked(),
            "obv_sma_len": self.sp_obv_sma_len.value(),
            "min_score": self.sp_min_score.value(),
        }
