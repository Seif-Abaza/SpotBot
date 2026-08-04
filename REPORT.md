# Code Review: Revert of `1dee732` (commits `61d435e` + `982e54b`)

## Summary

This review covers commits `61d435e` ("Fix Error Main Window line 922") and
`982e54b` (removal of accidentally-committed `__pycache__` binaries), which
together resolve a mid-flight revert of `1dee732` by restoring the deprecated
CCI/ADX/OBV indicators, the `FLOAT_EPS`-based dust threshold in
`_has_base_coin`, and a hard PyQt6 import in `trade_notifier.py`. The revert
restores the previous trading behavior, but it reintroduces a real dust-threshold
bug, ships a broken `updateUIState(...)` call site that corrupts the chart HUD
on initial load, and leaves a git-tracked `config.json` containing live API
credentials.

**Verdict**: [ ] Approve | [x] Request Changes | [ ] Comment

## Critical Issues (Must Fix)

### 1. [`config.json:13-14`] Security: Live API credentials committed to git
- **Current**: `config.json` is git-tracked (`git ls-files` shows it) and
  contains a real key pair:
  ```json
  "api_key": "EEBpfbiY9LcwxdDVoZ",
  "api_secret": "yyxS9eVfc4bENYKrzVm3Clf3v4EDgv0RYV3p",
  ```
  `AGENTS.md` confirms this file is **stale and loaded by no code**, i.e. it
  exists solely as a credential leak inside the repository history.
- **Impact**: Anyone with repo access can drain/abuse the Bybit account.
  Secrets also persist in git history even after deletion.
- **Suggested**:
  1. Rotate/revoke those API credentials on the exchange immediately.
  2. Delete `config.json` from the repo (`git rm --cached config.json`) and
     `git filter-repo` to purge it from history.
  3. Add `config.json` to `.gitignore`.

### 2. [`spotbot/ui/main_window.py:2152`] Bug: `updateUIState(...)` called with 8 args, JS defines 4
- **Current**: the initial-chart builder emits:
  ```python
  "updateUIState("
  f"{int(fli_trend_val)},"
  f"{fli_data.get('cci', 0) or 0},"   # 2nd arg
  f"{fli_data.get('adx', 0) or 0},"   # 3rd arg
  f"0,"
  f"{int(score_val)},"
  f"{bbu_val},"
  f"{bbl_val},"
  f"'{fli_data.get('signal', 'WAIT')}'"
  ")"
  ```
  but `spotbot/chart_renderer.py:678` declares only
  `function updateUIState(fliTrend,bbUpper,bbLower,signal)`.
- **Impact**: JavaScript ignores trailing args, so on initial chart load the HUD
  maps `bbUpper ← cci`, `bbLower ← adx`, and `signal ← 0` (the literal string
  `"0"`), which falls into the `else` branch — BB upper/lower display **wrong
  values** (CCI/ADX) and the badge always shows `SCANNING...` even when a real
  signal exists. The live-update path at `main_window.py:1460` calls with the
  correct 4 args, so the bug only shows on initial chart build.
- **Suggested**: Either extend the JS to accept and render the 8 values
  (cci/adx/score are currently dropped), or revert this call site to the
  4-argument form matching `_update_fli_info_panel`. Prefer the former if the
  extra values are meant to be shown; otherwise delete the unused args.
- **Test gap**: `tests/test_session_fixes.py:277`
  (`test_updateUIState_signature_changed`) asserts the 4-arg JS signature still
  exists but never checks the **caller**, so this regression passes CI.

## Major Issues (Should Fix)

### 1. [`spotbot/trading.py:687`] Logic: dust threshold reverted, contradicting its own comment
- **Current**:
  ```python
  # Ignore dust — after a full sell the exchange may leave
  # a tiny residual (e.g. 1e-8) which should NOT block a new buy.
  return (free_qty > FLOAT_EPS, free_qty)   # FLOAT_EPS == 1e-12
  ```
- **Impact**: `1e-8 > 1e-12` is `True`, so a real-world 1e-8 residual after a
  full sell is treated as "still holding the coin" and **blocks the next buy** —
  exactly the bug `1dee732` fixed via `DUST_THRESHOLD = 1e-6`. The revert
  reintroduces it; the comment and code are in direct contradiction.
- **Suggested**: Reintroduce a dedicated dust threshold (e.g. `1e-6`), and fix
  the test that only covers `1e-13` (below EPS) — add a case for a typical
  1e-8 residual asserting `has_coin is False`.

### 2. [`trade_notifier.py:38-39`] Maintainability: hard PyQt6 dependency without fallback
- **Current**: `trade_notifier.py` does `from PyQt6.QtWidgets import
  QSystemTrayIcon, QMenu` with no fallback, and `spotbot/ui/main_window.py:139`
  imports it with a bare `import trade_notifier`.
- **Impact**: The app works only because (a) `app.py:16-44` installs a
  PySide6→PyQt6 alias shim before importing anything, and (b) PyQt6 happens to
  be installed in this env. Any direct entry point (`python -m spotbot.main`,
  tests, a future headless consumer) that imports `main_window` without the
  shim crashes if PyQt6 is absent. The removed
  `TestTradeNotifierImportFallback` used to guard this.
- **Suggested**: Wrap the import in `try/except ImportError` and gate usage on
  a `TRADE_NOTIFIER_AVAILABLE` flag (as `constants.py:42-47` already does), or
  restore the PySide6/PyQt6 fallback. Also make `constants.py`/`main_window.py`
  agree on the single availability flag instead of the current split.

## Minor Issues (Nice to Have)

### 1. [`spotbot/ui/main_window.py:924-925`] Bug (cosmetic): f-string missing separator
- **Current**:
  ```python
  f"ATR:{new_params['atr_period']}"
  f"MinScore:{new_params['min_score']}"
  ```
  Two adjacent f-strings concatenate to `ATR:9MinScore:1` (no space/comma).
- **Suggested**: `f"ATR:{...}, MinScore:{...}"`. Also consider `.get(...)` for
  `min_score` to avoid a `KeyError` if the params dict ever lacks it.

### 2. [`spotbot/indicators.py`] Maintainability: dead code kept for "compat"
- `fli_compute_cci/adx/obv` and the deprecated `FLI_USE_CCI/ADX/OBV` constants
  are referenced only by `indicators_backup.py` (a backup module) and as unused
  defaults in `main_window.py` `_fli_params`. `indicators.py` sets
  `df["cci"] = 0.0` etc. and ignores `use_cci/use_adx/use_obv`.
- **Impact**: A misleading config surface — users can toggle CCI/ADX/OBV in the
  params dialog with zero effect. `FLI_MIN_SCORE` *is* genuinely used, so the
  dead entries should be trimmed or documented as inert.
- **Suggested**: Delete the unused `fli_compute_*` helpers and deprecated
  constants, or keep only what `indicators_backup.py` genuinely needs.

### 3. [`.gitignore`] Style: redundant entries
- `spotbot/__pycache__/`, `spotbot/ui/__pycache__/`, `ui/__pycache__/` are
  already covered by the existing `__pycache__/` pattern; `ui/__pycache__/`
  also names a path the app doesn't use (it lives at `spotbot/ui`). Harmless,
  but the diff adds noise. The underlying `__pycache__` purge in `982e54b` is
  good hygiene.

## Positive Feedback

- **Tests pass**: `84 passed` in the full suite — the restore keeps behavior
  verifiable.
- **`982e54b` cleanly removed accidentally-committed `__pycache__` binaries** —
  good commit hygiene after the earlier conflict mess.
- **`FLOAT_EPS` kept as the single source of truth** in `constants.py` rather
  than re-hard-coding magic numbers in `trading.py`.
- **Deprecated constants/helpers were re-added with clear naming** to restore
  backward compatibility instead of breaking callers.
- **`AGENTS.md` is honest about `config.json`** being a leak risk — the repo
  just needs to act on it.
- **`tests/test_session_fixes.py` is well-structured** with clearly labeled
  sections and documented intent for each case.

## Questions for Author

- Is `config.json` the only file with live credentials? Are the keys in
  `api_keys`/`api_secrets` maps (lines 5, 9) also real, or placeholders?
- For `updateUIState`, is the intent to display CCI/ADX/score in the chart HUD
  (then update the JS), or was the 8-arg call a leftover experiment that should
  be reverted to 4 args?
- Was `DUST_THRESHOLD=1e-6` in `1dee732` removed deliberately? The 1e-8
  residual scenario in the comment still needs an actual fix.
- Should `trade_notifier.py` support running without PyQt6 at all, or is PyQt6
  now a hard dependency of the whole app?

## Test Coverage Assessment

- [x] Happy path tested
- [x] Error cases tested
- [ ] Edge cases tested — missing: dust residual 1e-8 in `_has_base_coin`;
      `updateUIState` caller/callee arg-count parity
- [x] Integration/import chain present (`import spotbot.main` verified)
- Note: several tests assert the *reverted* behavior while the JS
  caller (`main_window.py`) was *not* reverted in sync — the suite currently
  passes but doesn't catch the HUD mismatch.

## flake8 report
./spotbot/exchange.py:25:1: F401 'spotbot.constants.CCXT_PRO_AVAILABLE' imported but unused
./spotbot/exchange.py:25:1: F401 'spotbot.constants.CCXT_AVAILABLE as _CCXT_AVAILABLE' imported but unused
./spotbot/exchange.py:318:5: C901 'ExchangeManager.discover_tradable_pairs' is too complex (35)
./spotbot/exchange.py:613:13: E741 ambiguous variable name 'l'
./spotbot/indicators.py:26:1: F401 'spotbot.constants.FLOAT_EPS' imported but unused
./spotbot/indicators.py:212:65: F821 undefined name 'FLI_MIN_SCORE'
./spotbot/indicators.py:213:68: F821 undefined name 'FLI_MIN_SCORE'
./spotbot/indicators.py:253:31: E203 whitespace before ':'
./spotbot/indicators.py:258:50: E203 whitespace before ':'
./spotbot/indicators.py:321:42: E203 whitespace before ':'
./spotbot/indicators.py:326:42: E203 whitespace before ':'
./spotbot/indicators_backup.py:23:1: F401 'spotbot.constants.FLOAT_EPS' imported but unused
./spotbot/indicators_backup.py:30:1: E302 expected 2 blank lines, found 0
./spotbot/indicators_backup.py:221:1: C901 '_compute_fli_data' is too complex (11)
./spotbot/indicators_backup.py:266:1: E303 too many blank lines (3)
./spotbot/indicators_backup.py:279:31: E203 whitespace before ':'
./spotbot/indicators_backup.py:284:50: E203 whitespace before ':'
./spotbot/indicators_backup.py:347:42: E203 whitespace before ':'
./spotbot/indicators_backup.py:352:42: E203 whitespace before ':'
./spotbot/indicators_backup.py:436:13: F841 local variable 'pnl_usdt' is assigned to but never used
./spotbot/trading.py:3:1: F401 'json' imported but unused
./spotbot/trading.py:4:1: F401 'math' imported but unused
./spotbot/trading.py:5:1: F401 'random' imported but unused
./spotbot/trading.py:7:1: F401 'time' imported but unused
./spotbot/trading.py:21:1: F401 'spotbot.constants.CONFIRM_MULTIPLIER' imported but unused
./spotbot/trading.py:21:1: F401 'spotbot.constants.DEFAULT_SLIPPAGE' imported but unused
./spotbot/trading.py:21:1: F401 'spotbot.constants.DEFAULT_TAKER_FEE' imported but unused
./spotbot/trading.py:21:1: F401 'spotbot.constants.MACD_BUY_CONFIRM_EPS' imported but unused
./spotbot/trading.py:21:1: F401 'spotbot.constants.MACD_SELL_CONFIRM_EPS' imported but unused
./spotbot/trading.py:21:1: F401 'spotbot.constants.RSI_BUY_CONFIRM' imported but unused
./spotbot/trading.py:21:1: F401 'spotbot.constants.RSI_SELL_CONFIRM' imported but unused
./spotbot/trading.py:21:1: F401 'spotbot.constants.TRADE_HISTORY_LIMIT' imported but unused
./spotbot/trading.py:21:1: E402 module level import not at top of file
./spotbot/trading.py:34:1: E402 module level import not at top of file
./spotbot/trading.py:35:1: E402 module level import not at top of file
./spotbot/trading.py:72:63: F821 undefined name 'TransactionLogger'
./spotbot/trading.py:197:5: C901 'TradingEngine._evaluate_signal_locked' is too complex (14)
./spotbot/trading.py:255:5: C901 'TradingEngine._execute_order' is too complex (24)
./spotbot/ui/alert_dialog.py:1:77: W291 trailing whitespace
./spotbot/ui/alert_dialog.py:48:16: W291 trailing whitespace
./spotbot/ui/alert_dialog.py:98:4: W291 trailing whitespace
./spotbot/ui/alert_dialog.py:157:1: E303 too many blank lines (3)
./spotbot/ui/alert_dialog.py:316:1: E303 too many blank lines (3)
./spotbot/ui/alert_dialog.py:363:1: E303 too many blank lines (3)
./spotbot/ui/alert_dialog.py:587:5: E303 too many blank lines (2)
./spotbot/ui/alert_dialog.py:589:5: E301 expected 1 blank line, found 0
./spotbot/ui/alert_dialog.py:695:128: E501 line too long (132 > 127 characters)
./spotbot/ui/alert_dialog.py:721:5: E303 too many blank lines (2)
./spotbot/ui/alert_dialog.py:723:5: E301 expected 1 blank line, found 0
./spotbot/ui/alert_dialog.py:858:5: C901 'AlertDialog._refresh_list' is too complex (11)
./spotbot/ui/alert_dialog.py:867:26: E128 continuation line under-indented for visual indent
./spotbot/ui/alert_dialog.py:868:26: E128 continuation line under-indented for visual indent
./spotbot/ui/alert_dialog.py:869:26: E128 continuation line under-indented for visual indent
./spotbot/ui/alert_dialog.py:890:36: F821 undefined name 'QColor'
./spotbot/ui/alert_dialog.py:890:70: F821 undefined name 'QColor'
./spotbot/ui/alert_dialog.py:911:1: E303 too many blank lines (3)
./spotbot/ui/main_window.py:5:1: F401 'sys' imported but unused
./spotbot/ui/main_window.py:13:1: F401 'PySide6.QtCore.QUrl' imported but unused
./spotbot/ui/main_window.py:13:1: F401 'PySide6.QtCore.Signal' imported but unused
./spotbot/ui/main_window.py:14:1: F401 'PySide6.QtGui.QFont' imported but unused
./spotbot/ui/main_window.py:14:1: F401 'PySide6.QtGui.QIcon' imported but unused
./spotbot/ui/main_window.py:14:1: F401 'PySide6.QtGui.QPalette' imported but unused
./spotbot/ui/main_window.py:15:1: F401 'PySide6.QtWebEngineWidgets.QWebEngineView' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QApplication' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QCheckBox' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QComboBox' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QCommandLinkButton' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QFormLayout' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QFrame' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QGroupBox' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QHBoxLayout' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QHeaderView' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QLCDNumber' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QLineEdit' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QProgressBar' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QPushButton' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QRadioButton' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QSizePolicy' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QSpacerItem' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QSplitter' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QTableWidget' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QTabWidget' imported but unused
./spotbot/ui/main_window.py:16:1: F401 'PySide6.QtWidgets.QVBoxLayout' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.API_KEY_FILE' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.CANDLE_LIMIT' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.CONFIG_DIR' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.PANDAS_AVAILABLE' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.QUOTE_ASSETS' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.REFRESH_MS' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.RSI_BUY_THRESHOLD' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.RSI_SELL_THRESHOLD' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.TIMEFRAME_MAP' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.TIMEFRAMES' imported but unused
./spotbot/ui/main_window.py:52:1: F401 'spotbot.constants.TRADE_HISTORY_LIMIT' imported but unused
./spotbot/ui/main_window.py:87:1: F401 'spotbot.styles.STYLE_QSS' imported but unused
./spotbot/ui/main_window.py:88:1: F401 'spotbot.trading.TradingEngine' imported but unused
./spotbot/ui/main_window.py:96:1: F401 'spotbot.workers.IndicatorCalcWorker' imported but unused
./spotbot/ui/main_window.py:96:1: F401 'spotbot.workers.ProcessWorker' imported but unused
./spotbot/ui/main_window.py:96:1: F401 'spotbot.workers.WebSocketWorker' imported but unused
./spotbot/ui/main_window.py:113:5: F811 redefinition of unused 'NUMPY_AVAILABLE' from line 52
./spotbot/ui/main_window.py:525:5: C901 'MainWindow._run_backtest' is too complex (11)
./spotbot/ui/main_window.py:1284:17: E741 ambiguous variable name 'l'
./spotbot/ui/main_window.py:1343:5: C901 'MainWindow._set_markers' is too complex (16)
./spotbot/ui/main_window.py:1573:5: C901 'MainWindow._seed_position_from_wallet' is too complex (20)
./spotbot/ui/main_window.py:1674:5: C901 'MainWindow._fetch_and_mark_wallet_buys' is too complex (14)
./spotbot/ui/main_window.py:1832:9: F841 local variable 'te' is assigned to but never used
./spotbot/ui/main_window.py:1846:5: C901 'MainWindow._on_trade_done' is too complex (15)
./spotbot/ui/main_window.py:1873:21: F841 local variable 'realized' is assigned to but never used
./spotbot/ui/main_window.py:2095:5: C901 'MainWindow._format_fli_data' is too complex (13)
./spotbot/ui/main_window.py:2802:30: F541 f-string is missing placeholders
./spotbot/ui/main_window.py:3011:9: F401 'spotbot.ui.alert_dialog.load_alerts' imported but unused
./spotbot/ui/main_window.py:3064:5: C901 'MainWindow._evaluate_alerts' is too complex (23)
./spotbot/ui/main_window.py:3070:9: F401 'spotbot.ui.alert_dialog.append_alert_log' imported but unused
./spotbot/ui/main_window.py:3159:5: C901 'MainWindow._evaluate_single_condition' is too complex (13)
./spotbot/ui/main_window.py:3232:5: C901 'MainWindow._get_indicator_value' is too complex (13)
./spotbot/ui/main_window.py:3322:5: C901 'MainWindow._trigger_alert_action' is too complex (33)
./spotbot/ui/main_window.py:3384:17: F811 redefinition of unused 'sys' from line 5
./spotbot/ui/main_window.py:3522:5: C901 'MainWindow._execute_alert_order' is too complex (14)
./spotbot/ui/main_window.py:3585:61: F841 local variable 'resp' is assigned to but never used
./spotbot/workers.py:4:1: F401 'json' imported but unused
./spotbot/workers.py:10:1: F401 'spotbot.constants.CCXT_AVAILABLE' imported but unused
./spotbot/workers.py:10:1: F401 'spotbot.constants.FLOAT_EPS' imported but unused
./spotbot/workers.py:10:1: F401 'spotbot.constants.NUMPY_AVAILABLE' imported but unused
./spotbot/workers.py:10:1: F401 'spotbot.constants.PANDAS_AVAILABLE' imported but unused
./spotbot/workers.py:10:1: F401 'spotbot.constants.TIMEFRAME_MAP' imported but unused
./spotbot/workers.py:10:1: F401 'spotbot.constants.TRADE_HISTORY_LIMIT' imported but unused
./spotbot/workers.py:21:1: F401 'spotbot.indicators.fli_compute_all_indicators' imported but unused
./spotbot/workers.py:21:1: F401 'spotbot.indicators.fli_ohlcv_to_df' imported but unused
./spotbot/workers.py:28:42: E203 whitespace before ','
./spotbot/workers.py:29:1: F401 'spotbot.transaction_logger.TransactionLogger' imported but unused
./spotbot/workers.py:252:5: C901 'WalletBuyWorker.run' is too complex (14)
./spotbot/workers.py:308:9: F841 local variable 'panel_buys' is assigned to but never used
./spotbot/workers.py:409:5: C901 'BestTimeframeWorker.run' is too complex (11)
./spotbot/workers.py:412:13: F811 redefinition of unused 'fli_ohlcv_to_df' from line 21
./spotbot/workers.py:455:21: F841 local variable 'score' is assigned to but never used
./spotbot/workers.py:492:5: C901 'ParallelPipeline.run' is too complex (20)
./spotbot/workers.py:582:25: F841 local variable 'trading' is assigned to but never used
19    C901 'ExchangeManager.discover_tradable_pairs' is too complex (35)
3     E128 continuation line under-indented for visual indent
9     E203 whitespace before ':'
2     E301 expected 1 blank line, found 0
1     E302 expected 2 blank lines, found 0
7     E303 too many blank lines (3)
3     E402 module level import not at top of file
1     E501 line too long (132 > 127 characters)
2     E741 ambiguous variable name 'l'
71    F401 'spotbot.constants.CCXT_PRO_AVAILABLE' imported but unused
1     F541 f-string is missing placeholders
3     F811 redefinition of unused 'NUMPY_AVAILABLE' from line 52
5     F821 undefined name 'FLI_MIN_SCORE'
7     F841 local variable 'pnl_usdt' is assigned to but never used
3     W291 trailing whitespace

command:
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics


## Checklist

- [ ] No security vulnerabilities — **FAIL**: live API keys tracked in `config.json`
- [x] Performance is acceptable
- [ ] Code is readable — mostly; comment/behavior contradiction in `_has_base_coin`
- [x] Tests are adequate for covered paths
- [x] Documentation is present (AGENTS.md is accurate and current)
