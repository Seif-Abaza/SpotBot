# AGENTS.md — SpotBot (Targov v3.0)

PySide6 trading-bot dashboard for Bybit (ccxt/ccxt.pro). Structured as a
`spotbot/` Python package with a thin `app.py` entry point plus a Jupyter
backtesting notebook. No build step, no package manifest.

> **This file is a git-tracked copy.** `.gitignore` also lists it (pre-existing
> tracked file stays tracked). Edits to this file are visible to git.

## Run

```bash
cd /media/abaza/USBDisk/Project/Bot/Targov/resource/SAiBot/SpotBot_Online
python3 app.py
```

- GUI entry: `app.py` → `spotbot.main.main()`. The **PyQt6→PySide6 shim lives
  at `app.py` lines 16-44** (`sys.modules['PyQt6.*']` → PySide6) so
  `trade_notifier.py` (imports PyQt6) loads without PyQt6 installed. Do **not**
  remove that shim.
- `spotbot.main.main()` installs `sys.excepthook = excepthook` from
  `spotbot.exception_logger`, applies `STYLE_QSS` + "Fusion" style + a dark
  `QPalette` (bg `#0b0e11`, accent `#f0a500`), and opens `MainWindow` titled
  "Targov v3.0 — Trading Dashboard".
- On Linux the system-tray notification backend needs a running desktop
  session (Qt `QSystemTrayIcon`). Sound fallback uses `paplay`, `aplay`,
  `mpv`, `ffplay`, or `sox` (first found).
- Charts load `lightweight-charts@4.2.0` from `unpkg.com` CDN at runtime — the
  app needs internet on first render.

## Package layout

```
spotbot/
├── __init__.py            # empty
├── constants.py           # all constants + optional-dep import guards
├── styles.py              # STYLE_QSS string (embedded QSS theme)
├── indicators.py          # IndicatorEngine (RSI/MACD/BB/EMA/SMA) + FLI/SAI
│                          #   chart overlay math (fli_compute_*) + TradeSignal
├── indicators_backup.py   # legacy duplicate of indicators.py math — NOT used
│                          #   at runtime; keep in sync or ignore
├── candle_simulator.py    # CandleSimulator: realistic mock OHLCV source
│                          #   ([ts_ms,o,h,l,c,v]); used only under mock gate
├── exchange.py            # ExchangeManager (ccxt REST + ccxt.pro WS, key vault)
├── trading.py             # TradingEngine (strategy, order exec, signals)
├── transaction_logger.py  # TransactionLogger (pnl_log.json + transactions/)
├── exception_logger.py    # log_exception() / excepthook → exception_log.txt @ root
├── chart_renderer.py      # ChartRenderer + FLIChartWorker (lightweight-charts)
├── workers.py             # QThread workers (WS, PairLoader, DataFetch, etc.)
├── main.py                # entry point (QApplication, palette, MainWindow)
└── ui/
    ├── __init__.py
    ├── resizable_ui.py       # ResizableUI (sidebar + chart tabs + dockable panel)
    ├── coin_session.py       # CoinSession (per-coin state container)
    ├── coin_tab_widget.py    # CoinTabWidget (closable tab hosting CoinSession)
    ├── api_key_dialog.py     # APIKeyDialog
    ├── pnl_dialog.py         # PnLDialog
    ├── alert_dialog.py       # AlertDialog (price/indicator alerts, click-on-candle)
    ├── indicator_params_dialog.py # runtime FLI/SAI param editor
    └── main_window.py        # MainWindow (orchestrates all subsystems)
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
`backtesting.py` + `talib`.

## Mock trading gate (CRITICAL)

`spotbot/constants.py` defines:
`ALLOW_MOCK_CANDLES = (not CCXT_AVAILABLE) or env(APPV3_ALLOW_MOCKS in (1,"true","yes"))`.

- Mocks auto-enable only when no real exchange lib is installed.
- When ccxt IS available, mocking requires `APPV3_ALLOW_MOCKS=1` so production
  never trades on fake data.
- Data is mocked with `CandleSimulator`; indicator math, engine evaluation,
  charting, backtest, PnL all run the real code paths.

## Config & runtime state

The repo-root `config.json` is **stale / not loaded by any code**. ⚠️ It IS
git-tracked and contains real LIVE/DEMO API keys + secrets — never rely on it;
treat it as a secret leak risk. Runtime config lives under the user home:

Path (under `~/.targov_dashboard/`) | What
------------------------------------|--------------------------------------------
`api_keys.json`                      | Encrypted LIVE/DEMO keys
`pnl_log.json`                       | Daily P&L ledger
`alerts.json`                        | Price/indicator alerts (AlertDialog persistence)
`transactions/transactions_YYYY-MM-DD.json` | Per-day trade log (repo `transactions/`)

- API keys are Fernet-encrypted (passphrase derives from `Path.home()`). If
  `cryptography` is absent, keys fall back to plaintext with `chmod 0o600`.
  Old plaintext files auto-migrate on next save.
- The QSS stylesheet is embedded as `STYLE_QSS` in `spotbot/styles.py` —
  `style.qss` on disk is a standalone copy, **not** read at runtime.
- **`exception_log.txt`** is appended at the repo root by
  `spotbot/exception_logger.py` with `[relpath:lineno]` tags (unhandled
  exceptions go through the global `sys.excepthook`).

## Architecture

```
main() ──► MainWindow ──► CoinTabWidget ──► CoinSession ──► ChartRenderer
                                                       │
                                                       ├─ ExchangeManager   (ccxt REST + ccxt.pro WS, key vault)
                                                       ├─ TradingEngine      (strategy, order exec, signals)
                                                       ├─ TransactionLogger  (pnl_log.json + transactions/)
                                                       └─ Worker threads     (FLIChart, WS, PairLoader, DataFetch, IndicatorCalc, Process, ParallelPipeline)
```

- **Two independent signal stacks in `spotbot/indicators.py`:**
  - `IndicatorEngine` → RSI / MACD / Bollinger / EMA / SMA — the trading
    strategy's actual signal source.
  - FLI / SAI (`fli_compute_*`) — **chart display overlay only**, *never*
    passed to `TradingEngine.evaluate_signal`.
- `TradingEngine` raises `TradeSignal` (in `spotbot/indicators.py`) to abort a
  trade — catch it in any signal-path code.
- LIVE mode requires an explicit confirmation dialog. Never auto-arm LIVE.
- `ExchangeManager` resolves exchange classes via `getattr(ccxt, name)` — only
  exchanges available in the installed ccxt version are offered.
- `wait_poll`/dialog classes follow producer→consumer `Signal`→slot patterns;
  worker threads must emit `pyqtSignal` (never touch widgets off the GUI thread).

## Key constants (`spotbot/constants.py`)

| Constant | Value / Meaning |
|----------|-----------------|
| `TIMEFRAMES` | `3m 5m 15m 30m 1h 4h 1d` |
| `CANDLE_LIMIT` | 500 |
| `REFRESH_MS` | 3000 (UI poll) |
| `QUOTE_ASSETS` | USDT, USD, USDC, BUSD, DAI, FDUSD, TUSD |
| `RSI_BUY_THRESHOLD` | 30 (`RSI_BUY_CONFIRM` 35 / `RSI_SELL` 70 / `RSI_SELL_CONFIRM` 65) |
| `MACD_*_CONFIRM_EPS` | 0.0 (line must cross to confirm) |
| `FLOAT_EPS` | 1e-12 |
| `FLI_*` | defaults (BB, ATR, CCI, ADX, OBV + `FLI_MIN_SCORE`); overlay-only |

## Testing / verification

No test suite exists. After editing package files:

```bash
# syntax check all package files
for f in spotbot/*.py spotbot/ui/*.py; do python3 -c "import ast; ast.parse(open('$f').read())"; done

# import chain
python3 -c "import spotbot.main"

# full app launch (GUI)
python3 app.py
```

- Optional deps degrade gracefully via import-guard flags (`CCXT_AVAILABLE`,
  `NUMPY_AVAILABLE`, etc.) — check those flags at runtime, don't assume.
- .vscode `settings.json` is a conda env manager; VSCode is the dev IDE.

## Dead code removed / notes

- `spotbot/indicators_backup.py` is a stale duplicate of `indicators.py` — not
  imported anywhere at runtime.
- `_fli_lines_js` replaced by `_set_fli_lines` / `_build_initial_chart_js` in
  `chart_renderer.py`.
- `CoinPipelineThread` (worker pipeline) was deleted before the refactor.