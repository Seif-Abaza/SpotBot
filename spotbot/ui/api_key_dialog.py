"""API Key Dialog: manage LIVE / DEMO keypairs with Fernet encryption.

Includes Telegram Bot configuration tab for alert notifications.
"""

import json
import os

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from spotbot.constants import API_KEY_FILE, CONFIG_DIR
from spotbot.styles import STYLE_QSS

TELEGRAM_FILE = os.path.join(CONFIG_DIR, "telegram.json")


def _load_tg_config() -> dict:
    if os.path.exists(TELEGRAM_FILE):
        try:
            with open(TELEGRAM_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_tg_config(config: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(TELEGRAM_FILE, "w") as f:
        json.dump(config, f, indent=2)


class APIKeyDialog(QDialog):
    """Manage LIVE / DEMO API keys and Telegram Bot config in tabbed interface."""

    def __init__(self, exchange_mgr, exchange_name, parent=None):
        super().__init__(parent)
        self.exch_mgr = exchange_mgr
        self.exchange_name = exchange_name
        self.setWindowTitle(f"API Keys  {exchange_name}")
        self.setMinimumWidth(480)
        self.setStyleSheet(STYLE_QSS)

        layout = QVBoxLayout(self)
        lbl = QLabel(f"Manage API keys & Telegram for {exchange_name}")
        lbl.setStyleSheet("color:#f0a500;font-size:14px;font-weight:bold;padding:8px;")
        layout.addWidget(lbl)

        self.tabs = QTabWidget()

        # LIVE tab
        live_w = QWidget()
        live_form = QFormLayout(live_w)
        keys = self.exch_mgr.get_api_key(exchange_name)
        live_keys = keys.get("live", {})
        self.txt_live_key = QLineEdit()
        self.txt_live_key.setPlaceholderText("Live API Key")
        self.txt_live_key.setText(live_keys.get("apiKey", ""))
        live_form.addRow("API Key:", self.txt_live_key)
        self.txt_live_secret = QLineEdit()
        self.txt_live_secret.setPlaceholderText("Live Secret")
        self.txt_live_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_live_secret.setText(live_keys.get("secret", ""))
        live_form.addRow("Secret:", self.txt_live_secret)
        self.chk_live_show = QCheckBox("Show secrets")
        self.chk_live_show.toggled.connect(
            lambda c: self._toggle(self.txt_live_secret, self.txt_live_key, c)
        )
        live_form.addRow("", self.chk_live_show)
        self.tabs.addTab(live_w, "LIVE")

        # DEMO tab
        demo_w = QWidget()
        demo_form = QFormLayout(demo_w)
        demo_keys = keys.get("demo", {})
        self.txt_demo_key = QLineEdit()
        self.txt_demo_key.setPlaceholderText("Demo API Key")
        self.txt_demo_key.setText(demo_keys.get("apiKey", ""))
        demo_form.addRow("API Key:", self.txt_demo_key)
        self.txt_demo_secret = QLineEdit()
        self.txt_demo_secret.setPlaceholderText("Demo Secret")
        self.txt_demo_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_demo_secret.setText(demo_keys.get("secret", ""))
        demo_form.addRow("Secret:", self.txt_demo_secret)
        self.chk_demo_show = QCheckBox("Show secrets")
        self.chk_demo_show.toggled.connect(
            lambda c: self._toggle(self.txt_demo_secret, self.txt_demo_key, c)
        )
        demo_form.addRow("", self.chk_demo_show)
        self.tabs.addTab(demo_w, "DEMO")

        # TELEGRAM tab
        tg_w = QWidget()
        tg_form = QFormLayout(tg_w)
        tg_config = _load_tg_config()
        self.txt_tg_token = QLineEdit()
        self.txt_tg_token.setPlaceholderText("Bot Token from @BotFather")
        self.txt_tg_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_tg_token.setText(tg_config.get("bot_token", ""))
        tg_form.addRow("Bot Token:", self.txt_tg_token)
        self.txt_tg_chat_id = QLineEdit()
        self.txt_tg_chat_id.setPlaceholderText("Chat ID (e.g. -1001234567890)")
        self.txt_tg_chat_id.setText(str(tg_config.get("chat_id", "")))
        tg_form.addRow("Chat ID:", self.txt_tg_chat_id)
        tg_hint = QLabel("Create a bot via @BotFather on Telegram.\nGet your chat ID from @userinfobot.\nSend /start to your bot first.")
        tg_hint.setStyleSheet("color:#848e9c; font-size:10px;")
        tg_hint.setWordWrap(True)
        tg_form.addRow(tg_hint)
        self.tabs.addTab(tg_w, "TELEGRAM")

        layout.addWidget(self.tabs)

        # Buttons
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save All")
        btn_save.clicked.connect(self._on_save)
        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self._on_clear)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_clear)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _toggle(self, s1, s2, checked):
        m = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        s1.setEchoMode(m)
        s2.setEchoMode(m)

    def _on_save(self):
        live_key = self.txt_live_key.text().strip()
        live_secret = self.txt_live_secret.text().strip()
        demo_key = self.txt_demo_key.text().strip()
        demo_secret = self.txt_demo_secret.text().strip()

        live_partial = bool(live_key) != bool(live_secret)
        demo_partial = bool(demo_key) != bool(demo_secret)
        if live_partial or demo_partial:
            missing = []
            if live_partial:
                missing.append("LIVE (key or secret is empty)")
            if demo_partial:
                missing.append("DEMO (key or secret is empty)")
            QMessageBox.warning(
                self,
                "Incomplete API keys",
                "Please fill in BOTH key and secret for each mode you want to save,\n"
                "or clear both fields to skip that mode.\n\n"
                "Incomplete: " + ", ".join(missing),
            )
            return

        MIN_KEY_LEN = 8
        MIN_SECRET_LEN = 8
        for label, k, s in (
            ("LIVE", live_key, live_secret),
            ("DEMO", demo_key, demo_secret),
        ):
            if k and len(k) < MIN_KEY_LEN:
                QMessageBox.warning(self, "API key too short",
                    f"{label} API key is only {len(k)} chars -- looks truncated.")
                return
            if s and len(s) < MIN_SECRET_LEN:
                QMessageBox.warning(self, "API secret too short",
                    f"{label} secret is only {len(s)} chars -- looks truncated.")
                return

        if live_key and live_secret:
            self.exch_mgr.set_api_key(self.exchange_name, live_key, live_secret, mode="live")
        if demo_key and demo_secret:
            self.exch_mgr.set_api_key(self.exchange_name, demo_key, demo_secret, mode="demo")

        # Save Telegram config
        tg_token = self.txt_tg_token.text().strip()
        tg_chat_id = self.txt_tg_chat_id.text().strip()
        if tg_token and tg_chat_id:
            _save_tg_config({"bot_token": tg_token, "chat_id": tg_chat_id})

        QMessageBox.information(self, "Saved", f"All settings saved for {self.exchange_name}")
        self.accept()

    def _on_clear(self):
        self.txt_live_key.clear()
        self.txt_live_secret.clear()
        self.txt_demo_key.clear()
        self.txt_demo_secret.clear()
        self.txt_tg_token.clear()
        self.txt_tg_chat_id.clear()
