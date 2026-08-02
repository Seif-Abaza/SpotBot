"""Alert Dialog: create price/indicator alerts with click-on-candle trigger."""

import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from spotbot.constants import CONFIG_DIR


ALERTS_FILE = os.path.join(CONFIG_DIR, "alerts.json")

# Condition types
CONDITION_TYPES = ["Price", "Indicator"]

# Operators
OPERATORS = [
    "Crossing",
    "Crossing Up",
    "Crossing Down",
    "Greater Than",
    "Less Than",
    "Entering Channel",
    "Exiting Channel",
    "Inside Channel",
    "Outside Channel",
    "Moving Up",
    "Moving Down",
    "Moving Up %",
    "Moving Down %",
]

# Indicators available for alert conditions
INDICATOR_LIST = [
    "RSI (14)",
    "MACD Line",
    "MACD Signal",
    "CCI",
    "ADX",
    "OBV",
    "BB Upper",
    "BB Lower",
    "Trendline",
]

# Trigger modes
TRIGGER_MODES = ["Once only", "Every time"]

# Action types
ACTION_TYPES = [
    "None (Notify only)",
    "Limited Buy",
    "Limited Sell",
    "Market Buy",
    "Market Sell",
    "Send Telegram Message",
]


def load_alerts() -> list:
    """Load alerts from JSON file."""
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def save_alerts(alerts: list):
    """Save alerts to JSON file."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)


def load_telegram_config() -> dict:
    """Load Telegram bot config from file."""
    tg_file = os.path.join(CONFIG_DIR, "telegram.json")
    if os.path.exists(tg_file):
        try:
            with open(tg_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_telegram_config(config: dict):
    """Save Telegram bot config to file."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tg_file = os.path.join(CONFIG_DIR, "telegram.json")
    with open(tg_file, "w") as f:
        json.dump(config, f, indent=2)


class AlertDialog(QDialog):
    """Dialog to create/edit/delete price and indicator alerts."""

    alert_created = Signal(dict)  # emitted when an alert is created/updated

    def __init__(self, pair: str, candle_time: int = None, candle_price: float = None,
                 existing_alerts: list = None, parent=None):
        super().__init__(parent)
        self.pair = pair
        self.candle_time = candle_time
        self.candle_price = candle_price
        self.alerts = existing_alerts or load_alerts()
        self._editing_idx = None

        self.setWindowTitle(f"\U0001f514 Alerts — {pair}")
        self.setMinimumSize(680, 520)
        self.setStyleSheet(
            "QDialog{background:#0b0e11;}"
            "QLabel{color:#eaecef; font-size:12px;}"
            "QGroupBox{color:#f0a500; border:1px solid #2b3139; border-radius:6px;"
            " margin-top:10px; padding-top:14px; font-weight:bold;}"
            "QGroupBox::title{subcontrol-origin:margin; left:12px; padding:0 6px;}"
            "QSpinBox, QDoubleSpinBox, QLineEdit{background:#1e2329; color:#eaecef;"
            " border:1px solid #2b3139; border-radius:4px; padding:4px; min-height:24px;}"
            "QComboBox{background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            " border-radius:4px; padding:4px; min-height:24px;}"
            "QComboBox QAbstractItemView{background:#1e2329; color:#eaecef;"
            " selection-background-color:#2b3139;}"
            "QCheckBox{color:#eaecef; font-size:12px; spacing:8px;}"
            "QPushButton{background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            " border-radius:4px; padding:8px 16px; font-weight:bold;}"
            "QPushButton:hover{background:#2b3139;}"
            "QListWidget{background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            " font-size:11px;}"
            "QListWidget::item:selected{background:#2b3139;}"
        )

        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── Left: Alert list ──
        left_panel = QVBoxLayout()
        lbl_list = QLabel("Active Alerts")
        lbl_list.setStyleSheet("color:#f0a500; font-size:13px; font-weight:bold;")
        left_panel.addWidget(lbl_list)

        self.alert_list = QListWidget()
        self.alert_list.currentRowChanged.connect(self._on_select_alert)
        left_panel.addWidget(self.alert_list)

        list_btns = QHBoxLayout()
        self.btn_delete = QPushButton("\U0001f5d1 Delete")
        self.btn_delete.clicked.connect(self._on_delete_alert)
        list_btns.addWidget(self.btn_delete)
        self.btn_clear_all = QPushButton("Clear All")
        self.btn_clear_all.clicked.connect(self._on_clear_all)
        list_btns.addWidget(self.btn_clear_all)
        left_panel.addLayout(list_btns)

        main_layout.addLayout(left_panel, stretch=1)

        # ── Right: Alert form ──
        right_panel = QVBoxLayout()
        right_panel.setSpacing(8)

        # Condition group
        grp_cond = QGroupBox("Condition")
        form_cond = QFormLayout(grp_cond)
        form_cond.setContentsMargins(8, 14, 8, 8)

        self.cb_condition_type = QComboBox()
        self.cb_condition_type.addItems(CONDITION_TYPES)
        self.cb_condition_type.currentTextChanged.connect(self._on_condition_type_changed)
        form_cond.addRow("Type:", self.cb_condition_type)

        self.cb_indicator = QComboBox()
        self.cb_indicator.addItems(INDICATOR_LIST)
        self.cb_indicator.setVisible(False)
        form_cond.addRow("Indicator:", self.cb_indicator)

        self.cb_operator = QComboBox()
        self.cb_operator.addItems(OPERATORS)
        form_cond.addRow("Operator:", self.cb_operator)

        self.sp_value1 = QDoubleSpinBox()
        self.sp_value1.setRange(0.0, 999999999.0)
        self.sp_value1.setDecimals(8)
        self.sp_value1.setValue(candle_price or 0.0)
        if candle_price:
            self.sp_value1.setPrefix("")
        form_cond.addRow("Value 1:", self.sp_value1)

        self.sp_value2 = QDoubleSpinBox()
        self.sp_value2.setRange(0.0, 999999999.0)
        self.sp_value2.setDecimals(8)
        self.sp_value2.setValue(0.0)
        self.sp_value2.setVisible(False)
        form_cond.addRow("Value 2:", self.sp_value2)

        self.lbl_value2_hint = QLabel("")
        self.lbl_value2_hint.setStyleSheet("color:#848e9c; font-size:10px;")
        form_cond.addRow("", self.lbl_value2_hint)

        right_panel.addWidget(grp_cond)

        # Trigger group
        grp_trigger = QGroupBox("Trigger")
        form_trigger = QFormLayout(grp_trigger)
        form_trigger.setContentsMargins(8, 14, 8, 8)

        self.cb_trigger = QComboBox()
        self.cb_trigger.addItems(TRIGGER_MODES)
        form_trigger.addRow("Mode:", self.cb_trigger)

        right_panel.addWidget(grp_trigger)

        # Notification group
        grp_notif = QGroupBox("Notification")
        form_notif = QFormLayout(grp_notif)
        form_notif.setContentsMargins(8, 14, 8, 8)

        self.btn_browse_sound = QPushButton("\U0001f50a Browse Sound")
        self.btn_browse_sound.clicked.connect(self._on_browse_sound)
        self.lbl_sound_path = QLabel("(no sound selected)")
        self.lbl_sound_path.setStyleSheet("color:#848e9c; font-size:11px;")
        sound_row = QHBoxLayout()
        sound_row.addWidget(self.btn_browse_sound)
        sound_row.addWidget(self.lbl_sound_path)
        form_notif.addRow(sound_row)

        self._sound_path = ""

        right_panel.addWidget(grp_notif)

        # Action group
        grp_action = QGroupBox("Action")
        form_action = QFormLayout(grp_action)
        form_action.setContentsMargins(8, 14, 8, 8)

        self.cb_action = QComboBox()
        self.cb_action.addItems(ACTION_TYPES)
        self.cb_action.currentTextChanged.connect(self._on_action_changed)
        form_action.addRow("Action:", self.cb_action)

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
        self.sp_order_qty.setPlaceholderText("USDT amount")
        self.sp_order_qty.setVisible(False)
        form_action.addRow("Qty (USDT):", self.sp_order_qty)

        self.txt_telegram_msg = QLineEdit()
        self.txt_telegram_msg.setPlaceholderText("Message to send via Telegram")
        self.txt_telegram_msg.setVisible(False)
        form_action.addRow("Telegram Msg:", self.txt_telegram_msg)

        right_panel.addWidget(grp_action)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("\u2705 Save Alert")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        right_panel.addLayout(btn_row)

        main_layout.addLayout(right_panel, stretch=2)

        # Populate list
        self._refresh_list()

    def _refresh_list(self):
        """Refresh the alert list widget."""
        self.alert_list.clear()
        pair_alerts = [a for a in self.alerts if a.get("pair") == self.pair]
        for i, a in enumerate(pair_alerts):
            cond = a.get("condition_type", "Price")
            op = a.get("operator", "")
            val = a.get("value1", 0)
            indicator = a.get("indicator", "")
            if cond == "Indicator":
                text = f"{indicator} {op} {val}"
            else:
                text = f"Price {op} {val}"
            item = QListWidgetItem(text)
            self.alert_list.addItem(item)

    def _on_select_alert(self, row: int):
        """Load selected alert into the form."""
        if row < 0:
            self._editing_idx = None
            return
        pair_alerts = [a for a in self.alerts if a.get("pair") == self.pair]
        if row >= len(pair_alerts):
            return
        alert = pair_alerts[row]
        self._editing_idx = self.alerts.index(alert)

        self.cb_condition_type.setCurrentText(alert.get("condition_type", "Price"))
        self.cb_indicator.setCurrentText(alert.get("indicator", "RSI (14)"))
        self.cb_operator.setCurrentText(alert.get("operator", "Greater Than"))
        self.sp_value1.setValue(alert.get("value1", 0))
        self.sp_value2.setValue(alert.get("value2", 0))
        self.cb_trigger.setCurrentText(alert.get("trigger", "Once only"))
        self._sound_path = alert.get("sound_path", "")
        self.lbl_sound_path.setText(self._sound_path or "(no sound selected)")
        self.cb_action.setCurrentText(alert.get("action", "None (Notify only)"))
        self.sp_order_price.setValue(alert.get("order_price", 0))
        self.sp_order_qty.setValue(alert.get("order_qty", 0))
        self.txt_telegram_msg.setText(alert.get("telegram_msg", ""))

    def _on_condition_type_changed(self, text: str):
        self.cb_indicator.setVisible(text == "Indicator")

    def _on_operator_changed(self):
        op = self.cb_operator.currentText()
        needs_two = op in (
            "Entering Channel", "Exiting Channel",
            "Inside Channel", "Outside Channel",
        )
        self.sp_value2.setVisible(needs_two)
        if needs_two:
            self.lbl_value2_hint.setText("Channel upper/lower bounds")
        elif op in ("Moving Up %", "Moving Down %"):
            self.lbl_value2_hint.setText("Percentage change (e.g. 5.0 for 5%)")
        else:
            self.lbl_value2_hint.setText("")

    def _on_action_changed(self, text: str):
        is_limit = "Limited" in text
        is_telegram = "Telegram" in text
        self.sp_order_price.setVisible(is_limit)
        self.sp_order_qty.setVisible(is_limit or is_telegram == False and "None" not in text)
        self.txt_telegram_msg.setVisible(is_telegram)

    def _on_browse_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Alert Sound", "",
            "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"
        )
        if path:
            self._sound_path = path
            self.lbl_sound_path.setText(os.path.basename(path))

    def _get_form_data(self) -> dict:
        return {
            "pair": self.pair,
            "condition_type": self.cb_condition_type.currentText(),
            "indicator": self.cb_indicator.currentText(),
            "operator": self.cb_operator.currentText(),
            "value1": self.sp_value1.value(),
            "value2": self.sp_value2.value(),
            "trigger": self.cb_trigger.currentText(),
            "sound_path": self._sound_path,
            "action": self.cb_action.currentText(),
            "order_price": self.sp_order_price.value(),
            "order_qty": self.sp_order_qty.value(),
            "telegram_msg": self.txt_telegram_msg.text(),
            "enabled": True,
            "fired": False,
            "candle_time": self.candle_time,
        }

    def _on_save(self):
        data = self._get_form_data()
        if self._editing_idx is not None:
            self.alerts[self._editing_idx] = data
        else:
            self.alerts.append(data)
        save_alerts(self.alerts)
        self.alert_created.emit(data)
        self._refresh_list()
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
            self._refresh_list()
            self._editing_idx = None

    def _on_clear_all(self):
        self.alerts = [a for a in self.alerts if a.get("pair") != self.pair]
        save_alerts(self.alerts)
        self._refresh_list()
        self._editing_idx = None


class TelegramSetupDialog(QDialog):
    """Dialog to configure Telegram Bot settings for alert notifications."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("\U0001f4e8 Telegram Bot Setup")
        self.setMinimumWidth(440)
        self.setStyleSheet(
            "QDialog{background:#0b0e11;}"
            "QLabel{color:#eaecef; font-size:12px;}"
            "QGroupBox{color:#f0a500; border:1px solid #2b3139; border-radius:6px;"
            " margin-top:10px; padding-top:14px; font-weight:bold;}"
            "QGroupBox::title{subcontrol-origin:margin; left:12px; padding:0 6px;}"
            "QLineEdit{background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            " border-radius:4px; padding:4px; min-height:24px;}"
            "QPushButton{background:#1e2329; color:#eaecef; border:1px solid #2b3139;"
            " border-radius:4px; padding:8px 20px; font-weight:bold;}"
            "QPushButton:hover{background:#2b3139;}"
        )

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
        btn_test = QPushButton("\U0001f4e3 Test")
        btn_test.clicked.connect(self._on_test)
        btn_row.addWidget(btn_test)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_save = QPushButton("\U0001f4be Save")
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
        QMessageBox.information(self, "Saved", "\u2705 Telegram configuration saved.")
        self.accept()

    def _on_test(self):
        import urllib.request
        import urllib.error
        token = self.txt_bot_token.text().strip()
        chat_id = self.txt_chat_id.text().strip()
        if not token or not chat_id:
            QMessageBox.warning(self, "Incomplete", "Enter both Bot Token and Chat ID first.")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": "\u2705 SpotBot alert test - Telegram is working!"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    QMessageBox.information(self, "Success", "\u2705 Test message sent!")
                else:
                    QMessageBox.warning(self, "Failed", f"Telegram error: {result.get('description', 'Unknown')}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to send: {e}")
