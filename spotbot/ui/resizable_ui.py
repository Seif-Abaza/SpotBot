"""Resizable UI builder: sidebar + chart tabs + dockable PnL/console panel."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QCommandLinkButton,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLCDNumber,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ResizableUI:
    def setupUi(self, widget: QWidget):
        widget.setObjectName("MainFrame")
        widget.resize(1280, 860)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QHBoxLayout(widget)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── LEFT SIDEBAR ──
        self.groupBox = QGroupBox("Exchange")
        self.groupBox.setObjectName("groupBox")
        self.groupBox.setMinimumWidth(255)
        self.groupBox.setMaximumWidth(340)
        side = QVBoxLayout(self.groupBox)
        side.setContentsMargins(8, 14, 8, 8)
        side.setSpacing(6)

        # Live / Demo
        self.radLive = QRadioButton("Live")
        self.radLive.setObjectName("radLive")
        self.radLive.setChecked(True)
        self.radDemo = QRadioButton("Demo")
        self.radDemo.setObjectName("radDemo")
        row_mode = QHBoxLayout()
        row_mode.addWidget(self.radLive)
        row_mode.addWidget(self.radDemo)
        side.addLayout(row_mode)

        # Exchange combo (loaded from ccxt.exchanges)
        self.cbExchange = QComboBox()
        self.cbExchange.setObjectName("cbExchange")
        self.cbExchange.setPlaceholderText("Exchange name")
        side.addWidget(self.cbExchange)

        # Pair combo (loaded dynamically) + Add button
        pair_row = QHBoxLayout()
        pair_row.setContentsMargins(0, 0, 0, 0)
        pair_row.setSpacing(4)
        self.cbPair = QComboBox()
        self.cbPair.setObjectName("cbPair")
        self.cbPair.setEditable(True)
        self.cbPair.setPlaceholderText("Pair Spot")
        self.cbPair.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        pair_row.addWidget(self.cbPair, stretch=1)
        self.btnAddPair = QPushButton("+ Add")
        self.btnAddPair.setObjectName("btnAddPair")
        self.btnAddPair.setFixedHeight(self.cbPair.sizeHint().height())
        self.btnAddPair.setToolTip("Open selected pair in a new tab")
        pair_row.addWidget(self.btnAddPair)
        side.addLayout(pair_row)

        # ── Pair loading progress ──
        self.pairProgressBar = QProgressBar()
        self.pairProgressBar.setObjectName("pairProgressBar")
        self.pairProgressBar.setRange(0, 100)
        self.pairProgressBar.setValue(0)
        self.pairProgressBar.setVisible(False)
        side.addWidget(self.pairProgressBar)
        self.lblPairProgress = QLabel("")
        self.lblPairProgress.setObjectName("lblPairProgress")
        self.lblPairProgress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lblPairProgress.setVisible(False)
        side.addWidget(self.lblPairProgress)

        # ── Wallet Group ──
        self.groupBox_wallet = QGroupBox("Wallet")
        self.groupBox_wallet.setObjectName("groupBox_2")
        wl = QVBoxLayout(self.groupBox_wallet)
        wl.setContentsMargins(6, 14, 6, 6)
        self.lnWalletBalance = QLCDNumber()
        self.lnWalletBalance.setObjectName("lnWalletBalance")
        self.lnWalletBalance.setSmallDecimalPoint(True)
        self.lnWalletBalance.setDigitCount(12)
        self.lnWalletBalance.setMode(QLCDNumber.Mode.Dec)
        self.lnWalletBalance.setSegmentStyle(QLCDNumber.SegmentStyle.Flat)
        self.lnWalletBalance.setProperty("value", 0.0)
        wl.addWidget(self.lnWalletBalance)
        side.addWidget(self.groupBox_wallet)

        # ── Timeframe (4 only) ──
        self.groupBox_timeframe = QGroupBox("Timeframe")
        self.groupBox_timeframe.setObjectName("groupBox_3")
        tl = QVBoxLayout(self.groupBox_timeframe)
        tl.setContentsMargins(6, 14, 6, 6)
        row_tf = QHBoxLayout()
        self.rb_timefram_3m = QRadioButton("3m")
        self.rb_timefram_3m.setObjectName("rb_timefram_5m")
        self.rb_timefram_3m.setMinimumSize(QSize(51, 0))
        self.rb_timefram_3m.setChecked(True)
        self.rb_timefram_5m = QRadioButton("5m")
        self.rb_timefram_5m.setObjectName("rb_timefram_5m")
        self.rb_timefram_5m.setMinimumSize(QSize(51, 0))
        self.rb_timefram_5m.setChecked(False)
        self.rb_timefram_15m = QRadioButton("15m")
        self.rb_timefram_15m.setObjectName("rb_timefram_15m")
        self.rb_timefram_15m.setMinimumSize(QSize(51, 0))
        self.rb_timefram_30m = QRadioButton("30m")
        self.rb_timefram_30m.setObjectName("rb_timefram_30m")
        self.rb_timefram_30m.setMinimumSize(QSize(51, 0))
        self.rb_timefram_1h = QRadioButton("1h")
        self.rb_timefram_1h.setObjectName("rb_timefram_1h")
        self.rb_timefram_1h.setMinimumSize(QSize(51, 0))
        for rb in (
            self.rb_timefram_3m,
            self.rb_timefram_5m,
            self.rb_timefram_15m,
            self.rb_timefram_30m,
            self.rb_timefram_1h,
        ):
            row_tf.addWidget(rb)
        tl.addLayout(row_tf)

        # ── Params + Best Timeframe (horizontal) ──
        row_btns = QHBoxLayout()
        row_btns.setSpacing(6)
        self.btnBestTimeframe = QPushButton("🔍 Best TF")
        self.btnBestTimeframe.setObjectName("btnBestTimeframe")
        self.btnBestTimeframe.setMinimumHeight(34)
        self.btnBestTimeframe.setToolTip(
            "Test all timeframes (3m, 5m, 15m, 30m, 1h) for the active coin\n"
            "and find the one with the highest Equity Final & Equity Peak.\n"
            "Results are ranked and the best timeframe is suggested."
        )
        row_btns.addWidget(self.btnBestTimeframe)

        self.btnIndicatorParams = QPushButton("⚙ Params")
        self.btnIndicatorParams.setObjectName("btnIndicatorParams")
        self.btnIndicatorParams.setMinimumHeight(34)
        self.btnIndicatorParams.setToolTip(
            "Open the Indicator Parameters dialog to fine-tune\n"
            "FLI/SAI settings: BB, ATR, CCI, ADX, OBV, Min Score.\n"
            "Changes take effect on the next computation cycle."
        )
        row_btns.addWidget(self.btnIndicatorParams)
        tl.addLayout(row_btns)

        # ── Timeframe backtest progress label ──
        # self.lblTfBacktestStatus = QLabel("")
        # self.lblTfBacktestStatus.setObjectName("lblTfBacktestStatus")
        # self.lblTfBacktestStatus.setWordWrap(True)
        # self.lblTfBacktestStatus.setStyleSheet(
        #     "color:#f0a500; font-size:10px; padding:2px 4px;"
        # )
        # self.lblTfBacktestStatus.setVisible(False)
        # tl.addWidget(self.lblTfBacktestStatus)

        side.addWidget(self.groupBox_timeframe)

        # ── Investmint Group ──
        self.groupBox_investmint = QGroupBox("Investmint")
        self.groupBox_investmint.setObjectName("groupBox_4")
        il = QVBoxLayout(self.groupBox_investmint)
        il.setContentsMargins(6, 14, 6, 6)
        self.dsbInvestmintAmount = QDoubleSpinBox()
        self.dsbInvestmintAmount.setObjectName("dsbInvestmintAmount")
        self.dsbInvestmintAmount.setRange(0.0, 10000.0)
        self.dsbInvestmintAmount.setDecimals(2)
        self.dsbInvestmintAmount.setValue(10.0)
        self.dsbInvestmintAmount.setSingleStep(0.5)
        il.addWidget(self.dsbInvestmintAmount)
        self.slidInvistmineAmount = QSlider(Qt.Orientation.Horizontal)
        self.slidInvistmineAmount.setObjectName("slidInvistmineAmount")
        self.slidInvistmineAmount.setRange(0, 100)
        self.slidInvistmineAmount.setValue(10)
        self.slidInvistmineAmount.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slidInvistmineAmount.setTickInterval(5)
        il.addWidget(self.slidInvistmineAmount)
        row_inv = QHBoxLayout()
        self.rbStyleFixed = QRadioButton("Fixed")
        self.rbStyleFixed.setObjectName("rbStyleFixed")
        self.rbStyleFixed.setChecked(True)
        self.rbStyleCumu = QRadioButton("Cumulative")
        self.rbStyleCumu.setObjectName("rbStyleCumu")
        row_inv.addWidget(self.rbStyleFixed)
        row_inv.addWidget(self.rbStyleCumu)
        il.addLayout(row_inv)
        side.addWidget(self.groupBox_investmint)

        # ── Connection / Start Trading / Simulate (horizontal) ──
        row_ctrl = QHBoxLayout()
        row_ctrl.setSpacing(6)
        self.btnConnDissconExchange = QPushButton("Connection")
        self.btnConnDissconExchange.setObjectName("btnConnDissconExchange")
        self.btnConnDissconExchange.setMinimumHeight(42)
        row_ctrl.addWidget(self.btnConnDissconExchange)

        self.btnStartTrading = QPushButton("Start Trading")
        self.btnStartTrading.setObjectName("btnStartTrading")
        self.btnStartTrading.setMinimumHeight(42)
        self.btnStartTrading.setCheckable(True)
        self.btnStartTrading.setChecked(False)
        self.btnStartTrading.setEnabled(False)
        self.btnStartTrading.setToolTip(
            "Arm automated trading. The bot will execute on confirmed signals."
        )
        row_ctrl.addWidget(self.btnStartTrading)

        self.btnSimulation = QPushButton("🎮 Simulate")
        self.btnSimulation.setObjectName("btnSimulation")
        self.btnSimulation.setMinimumHeight(42)
        self.btnSimulation.setCheckable(True)
        self.btnSimulation.setToolTip(
            "Toggle chart simulation mode.\n\n"
            "When active, generates realistic fake candles locally\n"
            "instead of fetching from the exchange.\n\n"
            "All indicators, trading signals, markers, and backtest\n"
            "run normally — only the data source is mocked.\n\n"
            "No real orders are placed during simulation."
        )
        row_ctrl.addWidget(self.btnSimulation)
        side.addLayout(row_ctrl)

        # ── Sim speed/price controls (hidden by default, shown when Simulate active) ──
        self.simControlsRow = QHBoxLayout()
        self.simControlsRow.setSpacing(4)
        self.simControlsRow.setContentsMargins(0, 0, 0, 0)
        side.addLayout(self.simControlsRow)

        # Show PnL
        self.clBtnShowPnL = QCommandLinkButton("Show &PnL")
        self.clBtnShowPnL.setObjectName("clBtnShowPnL")
        side.addWidget(self.clBtnShowPnL)

        # ── Task 2: Toggle button for the dockable PnL & Console panel ──
        # Checkable — when checked, the bottom panel is visible; when
        # unchecked, it's hidden and the chart_view auto-resizes to
        # fill the entire tab area (handled by the QSplitter).
        self.clBtnToggleConsole = QCommandLinkButton("Show &Console")
        self.clBtnToggleConsole.setObjectName("clBtnToggleConsole")
        self.clBtnToggleConsole.setCheckable(True)
        self.clBtnToggleConsole.setChecked(True)
        self.clBtnToggleConsole.setToolTip(
            "Show or hide the dockable PnL + Console panel at the bottom. "
            "When hidden, the chart expands to fill the available space."
        )
        side.addWidget(self.clBtnToggleConsole)

        # Setup API Keys
        self.clBtnAPIKey = QCommandLinkButton("Setup API Keys")
        self.clBtnAPIKey.setObjectName("clBtnAPIKey")
        side.addWidget(self.clBtnAPIKey)

        # Spacer
        side.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

        # Status
        self.lblstatus = QLabel("Status: Ready")
        self.lblstatus.setObjectName("lblstatus")
        self.lblstatus.setWordWrap(True)
        self.lblstatus.setMinimumHeight(55)
        self.lblstatus.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        side.addWidget(self.lblstatus)

        root.addWidget(self.groupBox, stretch=0)

        # ── Right side: tabs on top, dockable bottom panel on bottom ──
        # We use a QSplitter (vertical) so the user can drag-resize the
        # two regions AND so the bottom panel can be hidden (the splitter
        # then gives all space to the tabWidget → chart_view auto-resizes).
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.setContentsMargins(0, 0, 0, 0)
        self.right_splitter.setChildrenCollapsible(False)

        self.tabWidget = QTabWidget()
        self.tabWidget.setTabsClosable(True)
        self.tabWidget.setMovable(True)
        self.tabWidget.setDocumentMode(True)
        self.tabWidget.setTabBarAutoHide(True)
        self.right_splitter.addWidget(self.tabWidget)

        # ── Dockable bottom panel: PnL summary + TabWidget(rt_table | footerStatusBar) ──
        #    Lives in a single QFrame so it can be shown/hidden
        #    as a unit via the clBtnToggleConsole checkable button. ──
        self.bottomPanel = QFrame()
        self.bottomPanel.setObjectName("bottomPanel")
        self.bottomPanel.setStyleSheet(
            "QFrame#bottomPanel{background:#0b0e11; border-top:2px solid #f0a500;}"
        )
        bp_layout = QVBoxLayout(self.bottomPanel)
        bp_layout.setContentsMargins(6, 4, 6, 4)
        bp_layout.setSpacing(4)

        # ── Daily PnL summary box (shared across all tabs — shows the
        #    currently active tab's data, refreshed on tab switch). ──
        self.pnl_box = QGroupBox("Daily PnL")
        self.pnl_box.setStyleSheet(
            "QGroupBox{color:#aaa; border:1px solid #444; border-radius:4px;"
            "margin-top:8px; padding-top:14px;}"
            "QGroupBox::title{subcontrol-origin:margin; left:10px; padding:0 4px;}"
        )
        pnl_layout = QHBoxLayout(self.pnl_box)
        pnl_layout.setContentsMargins(8, 4, 8, 4)
        self.lbl_pnl_pair = QLabel("Pair: —")
        self.lbl_pnl_pair.setStyleSheet(
            "color:#00e676; font-size:11px; font-weight:bold;"
        )
        self.lbl_pnl_trips = QLabel("Trips: 0")
        self.lbl_pnl_trips.setStyleSheet("color:#aaa; font-size:11px;")
        self.lbl_pnl_total = QLabel("PnL: 0.00 USDT")
        self.lbl_pnl_total.setStyleSheet(
            "color:#aaa; font-size:11px; font-weight:bold;"
        )
        self.lbl_pnl_wins = QLabel("Wins: 0")
        self.lbl_pnl_wins.setStyleSheet("color:#00e676; font-size:11px;")
        self.lbl_pnl_losses = QLabel("Losses: 0")
        self.lbl_pnl_losses.setStyleSheet("color:#ff5252; font-size:11px;")
        for lbl in (
            self.lbl_pnl_pair,
            self.lbl_pnl_trips,
            self.lbl_pnl_total,
            self.lbl_pnl_wins,
            self.lbl_pnl_losses,
        ):
            pnl_layout.addWidget(lbl)
        pnl_layout.addStretch()
        bp_layout.addWidget(self.pnl_box)

        # ── TabWidget for rt_table and footerStatusBar (separate tabs) ──
        self.bottomTabWidget = QTabWidget()
        self.bottomTabWidget.setDocumentMode(True)
        self.bottomTabWidget.setTabBarAutoHide(False)
        self.bottomTabWidget.setStyleSheet(
            "QTabWidget::pane{border:1px solid #333; border-radius:2px;}"
            "QTabBar::tab{background:#1a1a2e; color:#aaa; padding:4px 12px;"
            " border:1px solid #333; border-bottom:none; border-radius:3px 3px 0 0;"
            " font-size:11px; font-weight:bold;}"
            "QTabBar::tab:selected{background:#0b0e11; color:#f0a500; border-color:#f0a500;}"
        )

        # Tab 1: Console Log
        footer_log_widget = QWidget()
        footer_log_layout = QVBoxLayout(footer_log_widget)
        footer_log_layout.setContentsMargins(4, 4, 4, 4)
        self.footerStatusBar = QTextEdit("Ready")
        self.footerStatusBar.setObjectName("footerStatusBar")
        self.footerStatusBar.setMinimumHeight(40)
        self.footerStatusBar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.footerStatusBar.setReadOnly(True)
        self.footerStatusBar.setAcceptRichText(True)

        self.footerStatusBar.setStyleSheet("color:#aaa; font-size:11px;")
        footer_log_layout.addWidget(self.footerStatusBar)
        self.bottomTabWidget.addTab(footer_log_widget, "Console Log")

        # Tab 2: Round-trip table
        self.rt_table = QTableWidget(0, 6)
        self.rt_table.setHorizontalHeaderLabels(
            ["Pair", "Buy", "Sell", "Qty", "PnL USDT", "PnL %"]
        )
        self.rt_table.horizontalHeader().setStretchLastSection(True)
        self.rt_table.verticalHeader().setVisible(False)
        self.rt_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rt_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rt_table.setAlternatingRowColors(True)
        self.rt_table.setStyleSheet(
            "QTableWidget{background:#1a1a2e; color:#ddd; gridline-color:#333;"
            " font-size:11px;}"
            "QHeaderView::section{background:#252540; color:#aaa; padding:3px;"
            " border:1px solid #333; font-size:11px;}"
        )
        self.rt_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.bottomTabWidget.addTab(self.rt_table, "Round Trips")

        bp_layout.addWidget(self.bottomTabWidget)

        self.right_splitter.addWidget(self.bottomPanel)
        # Default split: chart gets ~70%, bottom panel ~30%.
        self.right_splitter.setStretchFactor(0, 7)
        self.right_splitter.setStretchFactor(1, 3)
        # ── Fix (chart not visible at startup): explicitly set initial
        #    splitter sizes.  Without this, the bottomPanel's sizeHint
        #    (driven by the round-trip table + footer label) can crowd
        #    out the tabWidget, leaving the chart with 0 height. ──
        self.right_splitter.setSizes([600, 220])
        # Save initial sizes so we can restore them after a hide/show cycle.
        self._bottom_panel_default_height = 220

        root.addWidget(self.right_splitter, stretch=1)
