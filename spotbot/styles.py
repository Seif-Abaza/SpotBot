"""Qt stylesheet for SpotBot (Targov v3.0 dark theme).

The QSS is embedded as a string constant rather than loaded from style.qss
on disk — edit STYLE_QSS here to change the theme.
"""

STYLE_QSS = """
/* ─── Targov v3.0 — Professional Dark Theme ─── */

QMainWindow { background-color: #0b0e11; }
QWidget { background-color: #0b0e11; color: #eaecef;
    font-family: "Segoe UI","Helvetica Neue","Consolas",sans-serif; font-size: 12px; }

QGroupBox { border: 1px solid #2b3139; border-radius: 6px;
    margin-top: 12px; padding: 16px 12px 12px 12px;
    font-weight: 700; color: #eaecef; }
QGroupBox::title { subcontrol-origin: margin; left: 14px;
    padding: 0 8px; color: #f0a500; font-size: 12px; }

QPushButton { background-color: #1e2329; color: #eaecef;
    border: 1px solid #2b3139; border-radius: 6px;
    padding: 6px 14px; font-size: 12px; font-weight: 600; }
QPushButton:hover { border-color: #f0a500; color: #f0a500; }
QPushButton:pressed { background-color: #0b0e11; }
QPushButton:disabled { background-color: #1e2329; color: #474d57; border-color: #1e2329; }

QCommandLinkButton { color: #f0a500; font-size: 13px; font-weight: 600; padding: 4px; }
QCommandLinkButton:hover { color: #f5c040; }

QComboBox { background-color: #1e2329; color: #eaecef;
    border: 1px solid #2b3139; border-radius: 6px;
    padding: 8px 12px; font-size: 13px; font-family: "Consolas","SF Mono",monospace; }
QComboBox:focus { border: 1px solid #f0a500; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid #848e9c; }
QComboBox QAbstractItemView { background-color: #1e2329; color: #eaecef;
    selection-background-color: #2b3139; selection-color: #f0a500;
    border: 1px solid #2b3139; border-radius: 6px; outline: none; }

QDoubleSpinBox, QSpinBox { background-color: #1e2329; color: #eaecef;
    border: 1px solid #2b3139; border-radius: 6px; padding: 8px 12px;
    font-size: 13px; font-weight: 600; font-family: "Consolas","SF Mono",monospace; }
QDoubleSpinBox:focus { border: 1px solid #f0a500; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; }

QSlider::groove:horizontal { background: #2b3139; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: #f0a500; width: 16px; height: 16px;
    margin: -6px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover { background: #f5c040; }
QSlider::sub-page:horizontal { background: #f0a500; border-radius: 2px; }

QLCDNumber { background-color: #1e2329; color: #0ecb81;
    border: 1px solid #2b3139; border-radius: 6px; }

QRadioButton { color: #eaecef; spacing: 6px; font-size: 12px; }
QRadioButton::indicator { width: 16px; height: 16px;
    border-radius: 9px; border: 2px solid #2b3139; background-color: #1e2329; }
QRadioButton::indicator:checked { background-color: #f0a500; border-color: #f0a500; }

QProgressBar { background-color: #1e2329; border: 1px solid #2b3139;
    border-radius: 6px; text-align: center; color: #eaecef; font-weight: 600; height: 22px; }
QProgressBar::chunk { background-color: #f0a500; border-radius: 4px; }

QLabel#lblstatus { background-color: #0b0e11; border: 1px solid #2b3139;
    border-radius: 6px; padding: 8px; color: #0ecb81;
    font-size: 11px; font-weight: 600; font-family: "Consolas","SF Mono",monospace; }

QTextEdit#footerStatusBar { background-color: #0b0e11; border-top: 2px solid #f0a500;
    border-radius: 0px; padding: 6px 10px; color: #eaecef;
    font-size: 11px; font-family: "Consolas","SF Mono",monospace; }

QPushButton#btnStartTrading { background-color: #1e2329; color: #eaecef;
    border: 1px solid #f0a500; border-radius: 6px;
    font-size: 13px; font-weight: 700; }
QPushButton#btnStartTrading:hover { background-color: #2b3139; color: #f0a500; }
QPushButton#btnStartTrading:checked { background-color: #1b5e20; color: #fff;
    border-color: #4caf50; }
QPushButton#btnStartTrading:disabled { background-color: #1e2329; color: #474d57;
    border-color: #2b3139; }

QLabel#lblPairProgress { color: #848e9c; font-size: 11px; }

QTableWidget, QTableView { background-color: #0b0e11;
    alternate-background-color: #12161c; color: #eaecef;
    gridline-color: #1e2329; border: none;
    selection-background-color: #1e2329; selection-color: #f0a500;
    font-size: 11px; font-family: "Consolas","SF Mono",monospace; }
QHeaderView::section { background-color: #0b0e11; color: #555b65;
    border: none; border-bottom: 1px solid #1e2329;
    padding: 6px 8px; font-size: 10px; font-weight: 700; }

QCheckBox { color: #eaecef; spacing: 8px; font-size: 12px; }
QCheckBox::indicator { width: 16px; height: 16px;
    border: 1px solid #474d57; border-radius: 3px; background-color: #1e2329; }
QCheckBox::indicator:checked { background-color: #f0a500; border-color: #f0a500; }

QLineEdit, QTextEdit { background-color: #1e2329; color: #eaecef;
    border: 1px solid #2b3139; border-radius: 6px; padding: 8px 12px;
    font-size: 13px; font-family: "Consolas","SF Mono",monospace;
    selection-background-color: #f0a500; selection-color: #0b0e11; }
QLineEdit:focus { border: 1px solid #f0a500; }

QTabWidget::pane { border: 1px solid #2b3139; border-radius: 6px; background: #0b0e11; }
QTabBar::tab { background: #1e2329; color: #848e9c; padding: 8px 18px;
    border: 1px solid #2b3139; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    font-size: 12px; font-weight: 600; }
QTabBar::tab:selected { background: #0b0e11; color: #f0a500; border-bottom: 2px solid #f0a500; }

QScrollBar:vertical { background: #0b0e11; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #2b3139; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QToolTip { background-color: #1e2329; color: #eaecef;
    border: 1px solid #f0a500; border-radius: 4px; padding: 6px 10px; font-size: 11px; }

QDialog { background-color: #0b0e11; }
"""
