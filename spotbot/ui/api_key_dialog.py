"""API Key Dialog: manage LIVE / DEMO keypairs with Fernet encryption."""
import json
import os

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)
from spotbot.constants import API_KEY_FILE, CONFIG_DIR
class APIKeyDialog(QDialog):
    """🔑 Manage both keypairs (LIVE / DEMO) in tabbed interface."""

    def __init__(self, exchange_mgr, exchange_name, parent=None):
        super().__init__(parent)
        self.exch_mgr = exchange_mgr
        self.exchange_name = exchange_name
        self.setWindowTitle(f"🔑 API Keys — {exchange_name}")
        self.setMinimumWidth(480)
        self.setStyleSheet(STYLE_QSS)

        layout = QVBoxLayout(self)
        lbl = QLabel(f"Manage LIVE & DEMO API keys for {exchange_name}")
        lbl.setStyleSheet("color:#f0a500;font-size:14px;font-weight:bold;padding:8px;")
        layout.addWidget(lbl)

        # ── Tabs: LIVE / DEMO ──
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
        self.tabs.addTab(live_w, "🟢 LIVE")

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
        self.tabs.addTab(demo_w, "🟡 DEMO")

        layout.addWidget(self.tabs)

        # Buttons
        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 Save Both")
        btn_save.clicked.connect(self._on_save)
        btn_clear = QPushButton("🗑 Clear All")
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
        # Code Review Medium #10: validate inputs before persisting.
        # Previous implementation would happily save an empty key/secret
        # pair (or one with whitespace only) and then silently fail at
        # connect-time with a cryptic ccxt AuthenticationError.
        live_key = self.txt_live_key.text().strip()
        live_secret = self.txt_live_secret.text().strip()
        demo_key = self.txt_demo_key.text().strip()
        demo_secret = self.txt_demo_secret.text().strip()

        # A mode is "provided" only if BOTH key and secret are filled.
        # Anything else is treated as "skip this mode" — but if the user
        # typed only one of the two fields, that's almost certainly a
        # mistake, so we warn and abort.
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

        # Minimum-length sanity check (ccxt keys are typically ≥ 8 chars;
        # secrets are usually longer).  This catches accidental paste
        # truncation without being overly strict.
        MIN_KEY_LEN = 8
        MIN_SECRET_LEN = 8
        for label, k, s in (
            ("LIVE", live_key, live_secret),
            ("DEMO", demo_key, demo_secret),
        ):
            if k and len(k) < MIN_KEY_LEN:
                QMessageBox.warning(
                    self,
                    "API key too short",
                    f"{label} API key is only {len(k)} chars — looks truncated. "
                    f"Minimum is {MIN_KEY_LEN}.",
                )
                return
            if s and len(s) < MIN_SECRET_LEN:
                QMessageBox.warning(
                    self,
                    "API secret too short",
                    f"{label} secret is only {len(s)} chars — looks truncated. "
                    f"Minimum is {MIN_SECRET_LEN}.",
                )
                return

        # Only persist non-empty modes (so the user can clear a mode by
        # blanking both fields without overwriting the other).
        if live_key and live_secret:
            self.exch_mgr.set_api_key(
                self.exchange_name, live_key, live_secret, mode="live"
            )
        if demo_key and demo_secret:
            self.exch_mgr.set_api_key(
                self.exchange_name, demo_key, demo_secret, mode="demo"
            )
        QMessageBox.information(
            self, "Saved", f"✅ LIVE & DEMO keys saved for {self.exchange_name}"
        )
        self.accept()

    def _on_clear(self):
        self.txt_live_key.clear()
        self.txt_live_secret.clear()
        self.txt_demo_key.clear()
        self.txt_demo_secret.clear()


