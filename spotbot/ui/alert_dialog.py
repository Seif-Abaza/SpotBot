"""Alert Dialog: comprehensive alert system with multi-condition support."""  

import json
import os
import uuid
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QDateTime
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from spotbot.constants import CONFIG_DIR, TIMEFRAMES


ALERTS_FILE = os.path.join(CONFIG_DIR, "alerts.json")
ALERT_LOG_FILE = os.path.join(CONFIG_DIR, "alert_log.json")

CONDITION_TYPES = ["Price", "Indicator"]

OPERATORS_CROSSING = ["Crossing", "Crossing Up", "Crossing Down"]
OPERATORS_COMPARE = ["Greater Than", "Less Than"]
OPERATORS_CHANNEL = ["Entering Channel", "Exiting Channel", "Inside Channel", "Outside Channel"]
OPERATORS_MOVING = ["Moving Up", "Moving Down", "Moving Up %", "Moving Down %"]
ALL_OPERATORS = OPERATORS_CROSSING + OPERATORS_COMPARE + OPERATORS_CHANNEL + OPERATORS_MOVING

INDICATOR_LIST = [
    "BB Upper", "BB Lower", "Trendline",
]

TRIGGER_MODES = ["Once only", "Every time", "Once per bar close", "Once per minute"]

ACTION_ORDER_TYPES = ["None (Notify only)", "Limited Buy", "Limited Sell"]

PLACEHOLDER_HINT = (
    "Available placeholders:\n"
    "  {{pair}}      - Trading pair\n"
    "  {{price}}     - Current price\n"
    "  {{indicator}} - Indicator name\n"
    "  {{value}}     - Alert value\n"
    "  {{operator}}  - Operator\n"
    "  {{time}}      - Trigger time\n"
)

_DARK_QSS = """  
QDialog, QWidget#scrollContent { background:#0b0e11; }
QLabel { color:#eaecef; font-size:12px; }
QGroupBox {
    color:#f0a500; border:1px solid #2b3139; border-radius:6px;
    margin-top:10px; padding-top:14px; font-weight:bold;
}
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 6px; }
QSpinBox, QDoubleSpinBox, QLineEdit, QTextEdit, QDateEdit {
    background:#1e2329; color:#eaecef; border:1px solid #2b3139;
    border-radius:4px; padding:4px; min-height:24px;
}
QTextEdit { min-height:48px; }
QComboBox {
    background:#1e2329; color:#eaecef; border:1px solid #2b3139;
    border-radius:4px; padding:4px; min-height:24px;
}
QComboBox QAbstractItemView {
    background:#1e2329; color:#eaecef; selection-background-color:#2b3139;
}
QCheckBox { color:#eaecef; font-size:12px; spacing:8px; }
QPushButton {
    background:#1e2329; color:#eaecef; border:1px solid #2b3139;
    border-radius:4px; padding:6px 14px; font-weight:bold;
}
QPushButton:hover { background:#2b3139; }
QPushButton#btnAddCond, QPushButton#btnPopup {
    background:#1a3a2a; border-color:#0ecb81; color:#0ecb81;
}
QPushButton#btnAddCond:hover, QPushButton#btnPopup:hover {
    background:#0ecb81; color:#0b0e11;
}
QPushButton#btnSaveAlert {
    background:#f0a500; color:#0b0e11; border-color:#f0a500;
    font-size:13px; padding:8px 24px;
}
QPushButton#btnSaveAlert:hover { background:#d4940a; }
QListWidget {
    background:#1e2329; color:#eaecef; border:1px solid #2b3139; font-size:11px;
}
QListWidget::item { padding:6px 4px; border-bottom:1px solid rgba(43,49,57,0.3); }
QListWidget::item:selected { background:#2b3139; }
QScrollArea { border:none; background:transparent; }
QScrollBar:vertical {
    background:#0b0e11; width:8px; border:none;
}
QScrollBar::handle:vertical {
    background:#2b3139; border-radius:4px; min-height:30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
""" 


def load_alerts() -> list:
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def save_alerts(alerts: list):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)


def load_alert_log() -> list:
    if os.path.exists(ALERT_LOG_FILE):
        try:
            with open(ALERT_LOG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def append_alert_log(entry: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    log = load_alert_log()
    entry["triggered_at"] = datetime.utcnow().isoformat() + "Z"
    log.append(entry)
    if len(log) > 200:
        log = log[-200:]
    with open(ALERT_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def load_telegram_config() -> dict:
    tg_file = os.path.join(CONFIG_DIR, "telegram.json")
    if os.path.exists(tg_file):
        try:
            with open(tg_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_telegram_config(config: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tg_file = os.path.join(CONFIG_DIR, "telegram.json")
    with open(tg_file, "w") as f:
        json.dump(config, f, indent=2)



class ConditionWidget(QFrame):
    """A single condition row: type, indicator, operator, value1, value2, bars, interval."""
    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, data: dict = None, chart_interval: str = "1h", parent=None):
        super().__init__(parent)
        self.chart_interval = chart_interval
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background:#161a1e; border:1px solid #2b3139; border-radius:6px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self.cb_type = QComboBox()
        self.cb_type.addItems(CONDITION_TYPES)
        self.cb_type.setFixedWidth(80)
        self.cb_type.currentTextChanged.connect(self._on_type_changed)
        layout.addWidget(QLabel("Type:"))
        layout.addWidget(self.cb_type)

        self.cb_indicator = QComboBox()
        self.cb_indicator.addItems(INDICATOR_LIST)
        self.cb_indicator.setFixedWidth(110)
        self.cb_indicator.setVisible(False)
        self.cb_indicator.currentTextChanged.connect(lambda _: self.changed.emit())
        layout.addWidget(self.cb_indicator)

        self.cb_operator = QComboBox()
        self.cb_operator.addItems(ALL_OPERATORS)
        self.cb_operator.setFixedWidth(130)
        self.cb_operator.currentTextChanged.connect(self._on_operator_changed)
        layout.addWidget(QLabel("Op:"))
        layout.addWidget(self.cb_operator)

        self.sp_value1 = QDoubleSpinBox()
        self.sp_value1.setRange(-999999999, 999999999)
        self.sp_value1.setDecimals(8)
        self.sp_value1.setFixedWidth(120)
        self.sp_value1.valueChanged.connect(lambda _: self.changed.emit())
        layout.addWidget(self.sp_value1)

        self.sp_value2 = QDoubleSpinBox()
        self.sp_value2.setRange(-999999999, 999999999)
        self.sp_value2.setDecimals(8)
        self.sp_value2.setFixedWidth(120)
        self.sp_value2.setVisible(False)
        self.sp_value2.valueChanged.connect(lambda _: self.changed.emit())
        layout.addWidget(self.sp_value2)

        self.sp_bars = QSpinBox()
        self.sp_bars.setRange(1, 999)
        self.sp_bars.setValue(5)
        self.sp_bars.setFixedWidth(60)
        self.sp_bars.setVisible(False)
        self.sp_bars.valueChanged.connect(lambda _: self.changed.emit())
        layout.addWidget(QLabel("Bars:"))
        layout.addWidget(self.sp_bars)

        self.cb_interval = QComboBox()
        self.cb_interval.addItems(TIMEFRAMES)
        idx = list(TIMEFRAMES).index(chart_interval) if chart_interval in TIMEFRAMES else 4
        self.cb_interval.setCurrentIndex(idx)
        self.cb_interval.setFixedWidth(60)
        self.cb_interval.currentTextChanged.connect(lambda _: self.changed.emit())
        layout.addWidget(QLabel("TF:"))
        layout.addWidget(self.cb_interval)

        self.btn_remove = QPushButton("X")
        self.btn_remove.setFixedSize(24, 24)
        self.btn_remove.setStyleSheet(
            "QPushButton { background:#3a1a1a; color:#f6465d; border:1px solid #f6465d;"
            " border-radius:4px; font-weight:bold; padding:0; }"
            "QPushButton:hover { background:#f6465d; color:#0b0e11; }"
        )
        self.btn_remove.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(self.btn_remove)
        layout.addStretch()

        if data:
            self.load_from_dict(data)

    def _on_type_changed(self, text: str):
        self.cb_indicator.setVisible(text == "Indicator")
        self.changed.emit()

    def _on_operator_changed(self, text: str):
        self.sp_value2.setVisible(text in OPERATORS_CHANNEL)
        self.sp_bars.setVisible(text in OPERATORS_MOVING)
        self.changed.emit()

    def get_data(self) -> dict:
        return {
            "condition_type": self.cb_type.currentText(),
            "indicator": self.cb_indicator.currentText(),
            "operator": self.cb_operator.currentText(),
            "value1": self.sp_value1.value(),
            "value2": self.sp_value2.value(),
            "bars": self.sp_bars.value(),
            "interval": self.cb_interval.currentText(),
        }

    def load_from_dict(self, d: dict):
        self.cb_type.setCurrentText(d.get("condition_type", "Price"))
        self.cb_indicator.setCurrentText(d.get("indicator", "RSI (14)"))
        self.cb_operator.setCurrentText(d.get("operator", "Greater Than"))
        self.sp_value1.setValue(d.get("value1", 0))
        self.sp_value2.setValue(d.get("value2", 0))
        self.sp_bars.setValue(d.get("bars", 5))
        self.cb_interval.setCurrentText(d.get("interval", self.chart_interval))

    def get_preview_text(self) -> str:
        ct = self.cb_type.currentText()
        op = self.cb_operator.currentText()
        v1 = self.sp_value1.value()
        v2 = self.sp_value2.value()
        bars = self.sp_bars.value()
        tf = self.cb_interval.currentText()
        ind = self.cb_indicator.currentText()
        if ct == "Indicator":
            if op in OPERATORS_CHANNEL:
                return f"[{tf}] {ind} {op} {v1}/{v2}"
            elif op in OPERATORS_MOVING:
                return f"[{tf}] {ind} {op} {v1}% in {bars}bars"
            return f"[{tf}] {ind} {op} {v1}"
        else:
            if op in OPERATORS_CHANNEL:
                return f"[{tf}] Price {op} {v1}/{v2}"
            elif op in OPERATORS_MOVING:
                suffix = "%" if "%" in op else ""
                return f"[{tf}] Price {op} {v1}{suffix} in {bars}bars"
            return f"[{tf}] Price {op} {v1}"


class ConditionChip(QPushButton):
    """Clickable chip showing condition preview. Click to edit."""
    edit_requested = Signal(int)
    remove_requested = Signal(int)

    def __init__(self, text: str, index: int, parent=None):
        super().__init__(parent)
        self._index = index
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton {"
            "  background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            "  border-radius:12px; padding:4px 12px; font-size:11px;"
            "  text-align:left;"
            "}"
            "QPushButton:hover { background:#2b3139; border-color:#f0a500; }"
        )
        self.clicked.connect(lambda: self.edit_requested.emit(self._index))



class EditMessageDialog(QDialog):
    """Sub-dialog for editing alert name and message with placeholder support."""

    def __init__(self, name: str = "", message: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Message")
        self.setMinimumSize(460, 340)
        self.setStyleSheet(_DARK_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        form = QFormLayout()
        form.setSpacing(8)

        self.txt_name = QLineEdit(name)
        self.txt_name.setStyleSheet("font-size:13px; font-weight:bold;")
        form.addRow("Alert Name:", self.txt_name)

        self.txt_message = QTextEdit(message)
        self.txt_message.setAcceptRichText(False)
        form.addRow("Message:", self.txt_message)

        layout.addLayout(form)

        hint = QLabel(PLACEHOLDER_HINT)
        hint.setStyleSheet("color:#848e9c; font-size:10px; padding:6px; background:#161a1e; border-radius:4px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_ok = QPushButton("OK")
        btn_ok.setObjectName("btnSaveAlert")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def get_data(self) -> tuple:
        return self.txt_name.text().strip(), self.txt_message.toPlainText()



class AlertDialog(QDialog):
    """Dialog to create/edit/delete price and indicator alerts.

    Supports multi-condition alerts (up to 5),
    4 trigger frequency modes, expiration, message placeholders,
    and actions (popup, sound, limit order).
    """

    alert_created = Signal(dict)
    alert_deleted = Signal()

    MAX_CONDITIONS = 5

    def __init__(self, pair: str, candle_time: int = None, candle_price: float = None,
                 chart_interval: str = "1h", existing_alerts: list = None, parent=None):
        super().__init__(parent)
        self.pair = pair
        self.candle_time = candle_time
        self.candle_price = candle_price
        self.chart_interval = chart_interval
        self.alerts = existing_alerts or load_alerts()
        self._editing_idx = None
        self._conditions: list = []
        self._condition_editor = None
        self._editing_condition_idx = -1
        self._sound_path = ""
        self._alert_name = ""
        self._alert_message = ""

        self.setWindowTitle(f"🔔 Alert Settings — {pair}")
        self.setMinimumSize(920, 720)
        self.resize(1020, 760)
        self.setStyleSheet(_DARK_QSS)

        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # Left panel: Alert list
        left_panel = QVBoxLayout()
        left_panel.setSpacing(6)

        lbl_list = QLabel("Alert Manager")
        lbl_list.setStyleSheet("color:#f0a500; font-size:14px; font-weight:bold;")
        left_panel.addWidget(lbl_list)

        self.alert_list = QListWidget()
        self.alert_list.currentRowChanged.connect(self._on_select_alert)
        left_panel.addWidget(self.alert_list)

        list_btns = QHBoxLayout()
        self.btn_delete = QPushButton("🗑 Delete")
        self.btn_delete.setStyleSheet(
            "QPushButton{background:#3a1a1a;color:#f6465d;border:1px solid #f6465d;}"
            "QPushButton:hover{background:#f6465d;color:#0b0e11;}"
        )
        self.btn_delete.clicked.connect(self._on_delete_alert)
        list_btns.addWidget(self.btn_delete)
        self.btn_clear_all = QPushButton("Clear All")
        self.btn_clear_all.clicked.connect(self._on_clear_all)
        list_btns.addWidget(self.btn_clear_all)
        left_panel.addLayout(list_btns)

        main_layout.addLayout(left_panel, stretch=2)

        # Right panel: scrollable form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_content.setObjectName("scrollContent")
        right_panel = QVBoxLayout(scroll_content)
        right_panel.setSpacing(8)
        right_panel.setContentsMargins(4, 4, 4, 4)

        # Symbol
        lbl_symbol = QLabel(f"📊 Symbol:  {pair}")
        lbl_symbol.setStyleSheet("color:#f0a500; font-size:14px; font-weight:bold; padding:4px 0;")
        right_panel.addWidget(lbl_symbol)

        # Conditions Group
        self.grp_cond = QGroupBox("Conditions")
        self._cond_layout = QVBoxLayout(self.grp_cond)
        self._cond_layout.setContentsMargins(8, 14, 8, 8)
        self._cond_layout.setSpacing(6)

        # Condition chips area
        self._chips_widget = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_widget)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(6)
        self._chips_layout.addStretch()
        self._cond_layout.addWidget(self._chips_widget)

        # Add condition button
        self.btn_add_cond = QPushButton("+ Add Condition")
        self.btn_add_cond.setObjectName("btnAddCond")
        self.btn_add_cond.clicked.connect(self._on_add_condition)
        self._cond_layout.addWidget(self.btn_add_cond)

        right_panel.addWidget(self.grp_cond)

        # Trigger Group
        grp_trigger = QGroupBox("Trigger")
        form_trigger = QFormLayout(grp_trigger)
        form_trigger.setContentsMargins(8, 14, 8, 8)

        self.cb_trigger = QComboBox()
        self.cb_trigger.addItems(TRIGGER_MODES)
        form_trigger.addRow("Frequency:", self.cb_trigger)

        right_panel.addWidget(grp_trigger)

        # Expiration Group
        grp_expiration = QGroupBox("Expiration")
        exp_layout = QVBoxLayout(grp_expiration)
        exp_layout.setContentsMargins(8, 14, 8, 8)

        self.cb_expire_mode = QComboBox()
        self.cb_expire_mode.addItems(["No Expiration", "Expire at Date", "Expire after Trigger"])
        self.cb_expire_mode.currentTextChanged.connect(self._on_expire_mode_changed)
        exp_layout.addWidget(self.cb_expire_mode)

        self.dt_expiration = QDateEdit()
        self.dt_expiration.setCalendarPopup(True)
        self.dt_expiration.setDateTime(QDateTime.currentDateTime().addDays(7))
        self.dt_expiration.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.dt_expiration.setVisible(False)
        exp_layout.addWidget(self.dt_expiration)

        right_panel.addWidget(grp_expiration)

        # Message Group
        grp_msg = QGroupBox("Message")
        msg_layout = QVBoxLayout(grp_msg)
        msg_layout.setContentsMargins(8, 14, 8, 8)

        self.lbl_msg_preview = QLabel("(click Edit to set name & message)")
        self.lbl_msg_preview.setStyleSheet("color:#848e9c; font-size:11px; padding:4px;")
        self.lbl_msg_preview.setWordWrap(True)
        msg_layout.addWidget(self.lbl_msg_preview)

        self.btn_edit_msg = QPushButton("📝 Edit Message")
        self.btn_edit_msg.setObjectName("btnPopup")
        self.btn_edit_msg.clicked.connect(self._on_edit_message)
        msg_layout.addWidget(self.btn_edit_msg)

        right_panel.addWidget(grp_msg)

        # Actions Group
        grp_action = QGroupBox("Alert Actions")
        form_action = QFormLayout(grp_action)
        form_action.setContentsMargins(8, 14, 8, 8)

        self.chk_popup = QCheckBox("Show Pop-up")
        self.chk_popup.setChecked(True)
        form_action.addRow(self.chk_popup)

        self.chk_sound = QCheckBox("Play Sound")
        self.chk_sound.toggled.connect(self._on_sound_toggled)
        form_action.addRow(self.chk_sound)

        sound_row = QHBoxLayout()
        self.btn_browse_sound = QPushButton("🔊 Browse...")
        self.btn_browse_sound.clicked.connect(self._on_browse_sound)
        self.lbl_sound_path = QLabel("(default alert sound)")
        self.lbl_sound_path.setStyleSheet("color:#848e9c; font-size:10px;")
        sound_row.addWidget(self.btn_browse_sound)
        sound_row.addWidget(self.lbl_sound_path)
        form_action.addRow(sound_row)
        self.btn_browse_sound.setVisible(False)
        self.lbl_sound_path.setVisible(False)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2b3139;")
        form_action.addRow(sep)

        self.cb_action = QComboBox()
        self.cb_action.addItems(ACTION_ORDER_TYPES)
        self.cb_action.currentTextChanged.connect(self._on_action_changed)
        form_action.addRow("Order Action:", self.cb_action)

        self.sp_order_price = QDoubleSpinBox()
        self.sp_order_price.setRange(0.0, 999999999.0)
        self.sp_order_price.setDecimals(8)
        self.sp_order_price.setValue(candle_price or 0.0)
        self.sp_order_price.setVisible(False)
        form_action.addRow("Order Price:", self.sp_order_price)

        self.sp_order_qty = QDoubleSpinBox()
        self.sp_order_qty.setRange(0.0, 999999999.0)
        self.sp_order_qty.setDecimals(8)
        self.sp_order_qty.setValue(0.0)
        self.sp_order_qty.setVisible(False)
        form_action.addRow("Qty (USDT):", self.sp_order_qty)

        right_panel.addWidget(grp_action)

        # Save / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_clear_form = QPushButton("Clear Form")
        self.btn_clear_form.clicked.connect(self._clear_form)
        btn_row.addWidget(self.btn_clear_form)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("✅ Save Alert")
        self.btn_save.setObjectName("btnSaveAlert")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        right_panel.addLayout(btn_row)
        right_panel.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, stretch=4)

        self._refresh_list()
        # Do NOT auto-add a condition; user clicks "+ Add Condition" explicitly


    # Condition management

    def _on_add_condition(self):
        # First finalize any open editor (save to conditions list as a chip)
        self._finalize_condition()
        if len(self._conditions) >= self.MAX_CONDITIONS:
            QMessageBox.information(self, "Limit Reached", f"Maximum {self.MAX_CONDITIONS} conditions per alert.")
            return
        data = {"value1": self.candle_price or 0.0}
        cw = ConditionWidget(data=data, chart_interval=self.chart_interval)
        cw.changed.connect(lambda: self._update_condition_from_editor())
        cw.remove_requested.connect(self._on_remove_condition_editor)
        self._cond_layout.insertWidget(self._cond_layout.count() - 1, cw)
        self._condition_editor = cw
        self._editing_condition_idx = -1

    def _remove_condition_editor(self):
        if self._condition_editor is not None:
            self._condition_editor.setParent(None)
            self._condition_editor.deleteLater()
            self._condition_editor = None

    def _on_remove_condition_editor(self, widget):
        self._remove_condition_editor()
        self._editing_condition_idx = -1

    def _update_condition_from_editor(self):
        if self._condition_editor is None:
            return
        data = self._condition_editor.get_data()
        idx = self._editing_condition_idx
        if 0 <= idx < len(self._conditions):
            self._conditions[idx] = data
            self._refresh_chips()

    def _finalize_condition(self):
        if self._condition_editor is None:
            return
        data = self._condition_editor.get_data()
        if self._editing_condition_idx >= 0:
            self._conditions[self._editing_condition_idx] = data
        else:
            self._conditions.append(data)
        self._remove_condition_editor()
        self._editing_condition_idx = -1
        self._refresh_chips()

    def _refresh_chips(self):
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for i, cond in enumerate(self._conditions):
            ct = cond.get("condition_type", "Price")
            op = cond.get("operator", "")
            v1 = cond.get("value1", 0)
            v2 = cond.get("value2", 0)
            bars = cond.get("bars", 5)
            tf = cond.get("interval", self.chart_interval)
            ind = cond.get("indicator", "")
            if ct == "Indicator":
                if op in OPERATORS_CHANNEL:
                    text = f"[{tf}] {ind} {op} {v1}/{v2}"
                elif op in OPERATORS_MOVING:
                    text = f"[{tf}] {ind} {op} {v1}% in {bars}bars"
                else:
                    text = f"[{tf}] {ind} {op} {v1}"
            else:
                if op in OPERATORS_CHANNEL:
                    text = f"[{tf}] Price {op} {v1}/{v2}"
                elif op in OPERATORS_MOVING:
                    suffix = "%" if "%" in op else ""
                    text = f"[{tf}] Price {op} {v1}{suffix} in {bars}bars"
                else:
                    text = f"[{tf}] Price {op} {v1}"
            chip = ConditionChip(text, i)
            chip.edit_requested.connect(self._on_edit_condition)
            self._chips_layout.insertWidget(self._chips_layout.count() - 1, chip)

    def _on_edit_condition(self, idx: int):
        if idx < 0 or idx >= len(self._conditions):
            return
        self._finalize_condition()
        self._editing_condition_idx = idx
        cw = ConditionWidget(data=self._conditions[idx], chart_interval=self.chart_interval)
        cw.changed.connect(lambda: self._update_condition_from_editor())
        cw.remove_requested.connect(self._on_remove_condition_editor)
        self._cond_layout.insertWidget(self._cond_layout.count() - 1, cw)
        self._condition_editor = cw

    # Expiration

    def _on_expire_mode_changed(self, text: str):
        self.dt_expiration.setVisible("Date" in text)

    # Message

    def _on_edit_message(self):
        dlg = EditMessageDialog(self._alert_name, self._alert_message, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._alert_name, self._alert_message = dlg.get_data()
            self._update_msg_preview()

    def _update_msg_preview(self):  # noqa: E501
        if self._alert_name:
            self.lbl_msg_preview.setText(f"\U0001f4cb {self._alert_name}" + "\n" + (self._alert_message or ""))
            self.lbl_msg_preview.setStyleSheet("color:#eaecef; font-size:11px; padding:4px; background:#161a1e; border-radius:4px;")
        else:
            self.lbl_msg_preview.setText("(click Edit to set name & message)")
            self.lbl_msg_preview.setStyleSheet("color:#848e9c; font-size:11px; padding:4px;")

    # Action visibility

    def _on_action_changed(self, text: str):
        is_limit = "Limited" in text
        self.sp_order_price.setVisible(is_limit)
        self.sp_order_qty.setVisible(is_limit)

    def _on_sound_toggled(self, checked: bool):
        self.btn_browse_sound.setVisible(checked)
        self.lbl_sound_path.setVisible(checked)

    def _on_browse_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Alert Sound", "",
            "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"
        )
        if path:
            self._sound_path = path
            self.lbl_sound_path.setText(os.path.basename(path))


    # Form data

    def _get_form_data(self) -> dict:
        self._finalize_condition()
        expire_mode = self.cb_expire_mode.currentText()
        expiration = None
        if "Date" in expire_mode:
            expiration = self.dt_expiration.dateTime().toString("yyyy-MM-ddTHH:mm:ss")
        return {
            "id": str(uuid.uuid4()),
            "pair": self.pair,
            "name": self._alert_name,
            "message": self._alert_message,
            "conditions": list(self._conditions),
            "trigger": self.cb_trigger.currentText(),
            "expire_mode": expire_mode,
            "expiration": expiration,
            "actions": {
                "popup": self.chk_popup.isChecked(),
                "sound": self.chk_sound.isChecked(),
                "sound_path": self._sound_path,
                "order_type": self.cb_action.currentText(),
                "order_price": self.sp_order_price.value(),
                "order_qty": self.sp_order_qty.value(),
            },
            "enabled": True,
            "fired": False,
            "fire_count": 0,
            "last_fire_ts": 0,
            "last_fire_bar_ts": 0,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "candle_time": self.candle_time,
        }

    def _load_form_from_alert(self, alert: dict):
        self._clear_form()
        self._alert_name = alert.get("name", "")
        self._alert_message = alert.get("message", "")
        self._update_msg_preview()
        self.cb_trigger.setCurrentText(alert.get("trigger", "Once only"))
        self.cb_expire_mode.setCurrentText(alert.get("expire_mode", "No Expiration"))
        exp = alert.get("expiration")
        if exp:
            try:
                dt = QDateTime.fromString(exp, "yyyy-MM-ddTHH:mm:ss")
                if dt.isValid():
                    self.dt_expiration.setDateTime(dt)
            except Exception:
                pass
        actions = alert.get("actions", {})
        self.chk_popup.setChecked(actions.get("popup", True))
        self.chk_sound.setChecked(actions.get("sound", False))
        self._sound_path = actions.get("sound_path", "")
        self.lbl_sound_path.setText(os.path.basename(self._sound_path) if self._sound_path else "(default alert sound)")
        self.btn_browse_sound.setVisible(self.chk_sound.isChecked())
        self.lbl_sound_path.setVisible(self.chk_sound.isChecked())
        self.cb_action.setCurrentText(actions.get("order_type", "None (Notify only)"))
        self.sp_order_price.setValue(actions.get("order_price", 0))
        self.sp_order_qty.setValue(actions.get("order_qty", 0))
        # Load conditions
        conds = alert.get("conditions", [])
        self._conditions = []
        for c in conds:
            self._conditions.append(dict(c))
        self._refresh_chips()

    # Save / Delete / List

    def _on_save(self):
        if not self._conditions:
            QMessageBox.warning(self, "No Condition", "Add at least one condition.")
            return
        data = self._get_form_data()
        if self._editing_idx is not None:
            # Preserve the original id and fire state
            old = self.alerts[self._editing_idx]
            data["id"] = old.get("id", data["id"])
            data["fired"] = old.get("fired", False)
            data["fire_count"] = old.get("fire_count", 0)
            data["last_fire_ts"] = old.get("last_fire_ts", 0)
            data["last_fire_bar_ts"] = old.get("last_fire_bar_ts", 0)
            self.alerts[self._editing_idx] = data
        else:
            self.alerts.append(data)
        save_alerts(self.alerts)
        self.alert_created.emit(data)
        self._refresh_list()
        self._clear_form()
        self._editing_idx = None

    def _on_delete_alert(self):
        row = self.alert_list.currentRow()
        if row < 0:
            return
        pair_alerts = [a for a in self.alerts if a.get("pair") == self.pair]
        if row < len(pair_alerts):
            alert = pair_alerts[row]
            idx = self.alerts.index(alert)
            self.alerts.pop(idx)
            save_alerts(self.alerts)
            self.alert_deleted.emit()
            self._refresh_list()
            self._clear_form()
            self._editing_idx = None

    def _on_clear_all(self):
        reply = QMessageBox.question(
            self, "Clear All",
            f"Delete ALL alerts for {self.pair}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.alerts = [a for a in self.alerts if a.get("pair") != self.pair]
            save_alerts(self.alerts)
            self.alert_deleted.emit()
            self._refresh_list()
            self._clear_form()
            self._editing_idx = None

    def _clear_form(self):
        self._remove_condition_editor()
        self._conditions = []
        self._editing_condition_idx = -1
        self._refresh_chips()
        self._alert_name = ""
        self._alert_message = ""
        self._update_msg_preview()
        self.cb_trigger.setCurrentIndex(0)
        self.cb_expire_mode.setCurrentIndex(0)
        self.chk_popup.setChecked(True)
        self.chk_sound.setChecked(False)
        self.cb_action.setCurrentIndex(0)
        self.sp_order_price.setValue(self.candle_price or 0.0)
        self.sp_order_qty.setValue(0.0)  # noqa: C901
        self._editing_idx = None
        self.alert_list.clearSelection()

    def _refresh_list(self):
        self.alert_list.clear()
        pair_alerts = [a for a in self.alerts if a.get("pair") == self.pair]
        for a in pair_alerts:
            name = a.get("name", "")
            conds = a.get("conditions", [])
            if not conds:
                conds = [{"condition_type": a.get("condition_type", "Price"),
                         "operator": a.get("operator", ""),
                         "value1": a.get("value1", 0),
                         "indicator": a.get("indicator", ""),
                         "interval": ""}]
            parts = []
            for c in conds[:2]:
                ct = c.get("condition_type", "Price")
                op = c.get("operator", "")
                v1 = c.get("value1", 0)
                ind = c.get("indicator", "")
                if ct == "Indicator":
                    parts.append(f"{ind} {op} {v1}")
                else:
                    parts.append(f"Price {op} {v1}")
            text = " & ".join(parts)
            if len(conds) > 2:
                text += f" (+{len(conds)-2} more)"
            if name:
                text = f"[{name}] {text}"
            enabled = a.get("enabled", True)
            if not enabled:
                text = f"(OFF) {text}"
            item = QListWidgetItem(text)
            if not enabled:
                item.setForeground(QColor(100, 100, 100)) if hasattr(QColor, '__call__') else None
                try:
                    from PySide6.QtGui import QColor as QC
                    item.setForeground(QC("#646464"))
                except Exception:
                    pass
            self.alert_list.addItem(item)

    def _on_select_alert(self, row: int):
        if row < 0:
            self._editing_idx = None
            return
        pair_alerts = [a for a in self.alerts if a.get("pair") == self.pair]
        if row >= len(pair_alerts):
            return
        alert = pair_alerts[row]
        self._editing_idx = self.alerts.index(alert)
        self._load_form_from_alert(alert)



class TelegramSetupDialog(QDialog):
    """Dialog to configure Telegram Bot settings for alert notifications."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📨 Telegram Bot Setup")
        self.setMinimumWidth(440)
        self.setStyleSheet(_DARK_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl = QLabel("Configure Telegram Bot for alert notifications")
        lbl.setStyleSheet("color:#f0a500; font-size:14px; font-weight:bold; padding:8px;")
        layout.addWidget(lbl)

        grp = QGroupBox("Bot Configuration")
        form = QFormLayout(grp)
        form.setContentsMargins(8, 14, 8, 8)

        config = load_telegram_config()

        self.txt_bot_token = QLineEdit()
        self.txt_bot_token.setPlaceholderText("123456:ABC-DEF...")
        self.txt_bot_token.setText(config.get("bot_token", ""))
        self.txt_bot_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Bot Token:", self.txt_bot_token)

        self.txt_chat_id = QLineEdit()
        self.txt_chat_id.setPlaceholderText("Chat ID (e.g. -1001234567890)")
        self.txt_chat_id.setText(str(config.get("chat_id", "")))
        form.addRow("Chat ID:", self.txt_chat_id)

        layout.addWidget(grp)

        hint = QLabel(
            "\u2139\ufe0f Create a bot via @BotFather on Telegram.\n"
            "Get your chat ID from @userinfobot.\n"
            "Send /start to your bot first, then use the chat ID."
        )
        hint.setStyleSheet("color:#848e9c; font-size:11px; padding:4px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_test = QPushButton("📣 Test")
        btn_test.clicked.connect(self._on_test)
        btn_row.addWidget(btn_test)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_save = QPushButton("💾 Save")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    def _on_save(self):
        token = self.txt_bot_token.text().strip()
        chat_id = self.txt_chat_id.text().strip()
        if not token or not chat_id:
            QMessageBox.warning(self, "Incomplete", "Both Bot Token and Chat ID are required.")
            return
        save_telegram_config({"bot_token": token, "chat_id": chat_id})
        QMessageBox.information(self, "Saved", "✅ Telegram configuration saved.")
        self.accept()

    def _on_test(self):
        import urllib.request
        token = self.txt_bot_token.text().strip()
        chat_id = self.txt_chat_id.text().strip()
        if not token or not chat_id:
            QMessageBox.warning(self, "Incomplete", "Enter both Bot Token and Chat ID first.")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": "✅ SpotBot alert test - Telegram is working!"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    QMessageBox.information(self, "Success", "✅ Test message sent!")
                else:
                    QMessageBox.warning(self, "Failed", f"Telegram error: {result.get('description', 'Unknown')}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to send: {e}")
