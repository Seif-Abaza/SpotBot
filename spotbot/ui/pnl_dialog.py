"""PnL Dialog: structured JSON → formatted profit/loss table."""
import json

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from spotbot.constants import PNL_LOG_FILE
class PnLDialog(QDialog):
    """Shows PnL data in the exact JSON structure with a nice daily table."""

    def __init__(self, pnl_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📊 Daily P&L")
        self.resize(750, 500)
        self.setStyleSheet(STYLE_QSS)

        layout = QVBoxLayout(self)

        # ── Header ──
        date = pnl_data.get("date", "")
        exchange = pnl_data.get("exchange", "")
        mode = pnl_data.get("mode", "")
        inv_mode = pnl_data.get("investment_mode", "")
        summary = pnl_data.get("summary", {})

        hdr = QLabel(f"📅 {date}  ·  {exchange}  ·  {mode}  ·  {inv_mode}")
        hdr.setStyleSheet("color:#f0a500;font-size:15px;font-weight:bold;padding:6px;")
        layout.addWidget(hdr)

        # ── Summary bar ──
        total = summary.get("total_trades", 0)
        buys = summary.get("buys", 0)
        sells = summary.get("sells", 0)
        rpnl = summary.get("realized_pnl_usdt", 0)
        rpnl_pct = summary.get("realized_pnl_pct", 0)
        pnl_color = "#0ecb81" if rpnl >= 0 else "#f6465d"

        sum_lbl = QLabel(
            f"Trades: {total}  |  Buys: {buys}  |  Sells: {sells}  |  "
            f"PnL: <span style='color:{pnl_color}'>{rpnl:.4f} USDT ({rpnl_pct:.2f}%)</span>"
        )
        sum_lbl.setStyleSheet(
            "font-size:14px;font-weight:600;font-family:Consolas;padding:4px;"
        )
        layout.addWidget(sum_lbl)

        # ── Trades Table ──
        trades = pnl_data.get("trades", [])
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            [
                "Time",
                "Side",
                "Price",
                "Quantity",
                "Value USDT",
                "PnL USDT",
                "PnL %",
                "Note",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self.table.setRowCount(len(trades))
        for row, t in enumerate(trades):
            # Time (short)
            ts = t.get("timestamp", "")
            short_ts = ts.split("T")[1][:8] if "T" in ts else ts[:16]
            self.table.setItem(row, 0, QTableWidgetItem(short_ts))

            # Side with color
            side = t.get("side", "")
            side_item = QTableWidgetItem(side.upper())
            side_item.setForeground(QColor("#0ecb81" if side == "buy" else "#f6465d"))
            self.table.setItem(row, 1, side_item)

            # Price
            self.table.setItem(row, 2, QTableWidgetItem(f"{t.get('price', 0):.6f}"))

            # Quantity
            self.table.setItem(row, 3, QTableWidgetItem(f"{t.get('quantity', 0):.2f}"))

            # Value
            self.table.setItem(
                row, 4, QTableWidgetItem(f"{t.get('value_usdt', 0):.4f}")
            )

            # PnL USDT
            pnl = t.get("pnl_usdt")
            pnl_item = QTableWidgetItem(f"{pnl:.4f}" if pnl is not None else "—")
            if pnl is not None:
                pnl_item.setForeground(QColor("#0ecb81" if pnl >= 0 else "#f6465d"))
            self.table.setItem(row, 5, pnl_item)

            # PnL %
            pct = t.get("pnl_pct")
            pct_item = QTableWidgetItem(f"{pct:.2f}%" if pct is not None else "—")
            if pct is not None:
                pct_item.setForeground(QColor("#0ecb81" if pct >= 0 else "#f6465d"))
            self.table.setItem(row, 6, pct_item)

            # Note (shortened)
            note = t.get("note", "")
            self.table.setItem(row, 7, QTableWidgetItem(note[:60]))

        layout.addWidget(self.table)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)


