"""Entry point for SpotBot (Targov v3.0)."""

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from spotbot.constants import TRADE_NOTIFIER_AVAILABLE
from spotbot.exception_logger import excepthook
from spotbot.styles import STYLE_QSS
from spotbot.ui.main_window import MainWindow


def main():
    sys.excepthook = excepthook
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── Apply QSS (Targov v3.0) ──
    app.setStyleSheet(STYLE_QSS)

    # ── Palette fallback ──
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#0b0e11"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#eaecef"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#1e2329"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#2b3139"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#eaecef"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#1e2329"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#eaecef"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#f0a500"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#0b0e11"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1e2329"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#eaecef"))
    app.setPalette(pal)

    window = MainWindow()
    window.setWindowTitle("Targov v3.0 — Trading Dashboard")
    window.show()
    if not app.exec():
        sys.exit()


if __name__ == "__main__":
    main()
