# AGENTS.md — SpotBot (Targov v3.0)

PySide6 trading-bot dashboard for Bybit (ccxt/ccxt.pro). Structured as a
`spotbot/` Python package with a thin `app.py` entry point plus a Jupyter
backtesting notebook. No build step, no package manifest, no git repo.

## Run

```bash
cd /media/abaza/USBDisk/Project/Bot/Targov/resource/SAIBot/SpotBot
python3 app.py
```

- GUI is PySide6; `trade_notifier.py` imports PyQt6 — `app.py` shims
  `PyQt6.*` → `PySide6.*` in `sys.modules` at lines 63-92 so the notifier
  imports without PyQt6 installed. Do **not** remove that shim.
- On Linux the system-tray notification backend needs a running desktop
  session (Qt `QSystemTrayIcon`). Sound fallback uses `paplay`, `aplay`,
  `mpv`, `ffplay`, or `sox` (first found).
- Charts load `lightweight-charts@4.2.0` from `unpkg.com` CDN at runtime —
  the app needs internet on first render.

## Package layout

```
spotbot/
├── __init__.py          # empty
├── constants.py         # all constants + optional-dep import guards
├── styles.py            # STYLE_QSS string (embedded QSS theme)
├── indicators.py        # FLI indicator math, IndicatorEngine, TradeSignal
├── exchange.py          # ExchangeManager (ccxt REST + ccxt.pro WS, key vault)
├── trading.py           # TradingEngine (FLI strategy, order exec, signals)
├── transaction_logger.py # TransactionLogger (pnl_log.json + transactions/)
├── chart_renderer.py    # ChartRenderer + FLIChartWorker (lightweight-charts)
├── workers.py           # QThread workers (WS, PairLoader, DataFetch, etc.)
├── main.py              # entry point (QApplication, palette, MainWindow)
└── ui/
    ├── __init__.py
    ├── resizable_ui.py  # ResizableUI (sidebar + chart tabs + dockable panel)
    ├── coin_session.py  # CoinSession (per-coin state container)
    ├── coin_tab_widget.py # CoinTabWidget (closable tab hosting CoinSession)
    ├── api_key_dialog.py # APIKeyDialog
    ├── pnl_dialog.py    # PnLDialog
    └── main_window.py   # MainWindow (orchestrates all subsystems)
```

## Dependencies (pip)

| Package      | Purpose                          | Optional? |
|--------------|----------------------------------|-----------|
| PySide6      | GUI + WebEngine charts           | required  |
| ccxt         | REST exchange calls              | required  |
| ccxt.pro     | WebSocket streaming              | required  |
| numpy        | Indicator math                   | required  |
| pandas       | OHLCV / dataframe                | required  |
| python-utils | `to_float` helper                | required  |
| playsound3   | Sound effects                    | optional  |
| cryptography | Fernet-encrypt API keys at rest  | optional  |

`pandas-ta` is installed but **not** imported by the app (the app computes
indicators manually). The notebook (`crypto_backtesting.ipynb`) uses
`backtesting.py` + `talib` instead.

## Config & runtime state

The repo-root `config.json` is **stale / not loaded** by any code. Runtime
config lives under the user home directory:

| Path                                         | What                          |
|----------------------------------------------|-------------------------------|
| `~/.targov_dashboard/api_keys.json`          | Encrypted LIVE/DEMO keys      |
| `~/.targov_dashboard/pnl_log.json`           | Daily P&L ledger              |
| `transactions/transactions_YYYY-MM-DD.json`  | Per-day trade log (repo)      |

- API keys are encrypted with Fernet (SHA-256 of a machine-local passphrase
  derived from `Path.home()`). If `cryptography` is absent, keys fall back to
  plaintext with `chmod 0o600`. Old plaintext files auto-migrate on next save.
- The QSS stylesheet is embedded as the `STYLE_QSS` string constant in
  `spotbot/styles.py` — `style.qss` on disk is a standalone copy, **not** read
  at runtime. Edit `STYLE_QSS` in `spotbot/styles.py` to change the theme.

## Architecture

```
main()  ──►  MainWindow  ──►  CoinTabWidget  ──►  CoinSession  ──►  ChartRenderer
                                                          │
                                                          ├─ ExchangeManager   (ccxt REST + ccxt.pro WS, key vault)
                                                          ├─ TradingEngine     (FLI strategy, order exec, signals)
                                                          ├─ TransactionLogger (pnl_log.json + transactions/)
                                                          └─ Worker threads    (FLIChart, WS, PairLoader, DataFetch, IndicatorCalc, Process, ParallelPipeline)
```

- `TradingEngine` raises `TradeSignal` (in `spotbot/indicators.py`) to abort a
  trade — catch it in any signal-path code.
- LIVE mode requires an explicit confirmation dialog (Code Review 3.6).
  Never auto-arm LIVE.
- `ExchangeManager` resolves exchange classes via `getattr(ccxt, name)` —
  only exchanges available in the installed ccxt version are offered.

## Key constants

| Constant            | Value / Meaning                              |
|---------------------|----------------------------------------------|
| `TIMEFRAMES`        | `3m 5m 15m 30m 1h 4h 1d`                     |
| `CANDLE_LIMIT`      | 500                                          |
| `REFRESH_MS`        | 3000 (UI poll)                               |
| `QUOTE_ASSETS`      | USDT, USD, USDC, BUSD, DAI, FDUSD, TUSD      |
| `RSI_BUY_THRESHOLD` | 30 / `RSI_SELL_THRESHOLD` 70                 |
| `MACD_*_CONFIRM_EPS`| 0.0 (line must cross to confirm)             |
| `FLOAT_EPS`         | 1e-12 (zero-qty guard)                       |

## Testing / verification

No test suite exists. After editing package files:

```bash
# syntax check all files
for f in spotbot/*.py spotbot/ui/*.py; do python3 -c "import ast; ast.parse(open('$f').read())"; done

# import chain
python3 -c "import spotbot.main"

# full app launch (GUI)
python3 app.py
```

The import-level `try/except` blocks mean missing optional deps degrade
gracefully — always check the `CCXT_AVAILABLE`, `NUMPY_AVAILABLE`, etc.
flags at runtime rather than assuming.

## Dead code removed

- `_fli_lines_js` — never called, replaced by `_set_fli_lines` and
  `_build_initial_chart_js` in `spotbot/chart_renderer.py`.
- `CoinPipelineThread` — already deleted before refactoring (comment marker
  at old line 3727).
