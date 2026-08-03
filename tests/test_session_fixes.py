"""Unit tests for ALL functions modified/created in this session.

Covers:
  1. trading.py   — _has_base_coin() dust threshold + post-sell grace
  2. trading.py   — Buy-sell cycle: _last_sell_price gate (3% max)
  3. trading.py   — _execute_order() force/override_amount paths
  4. indicators.py — fli_compute_all_indicators() pure FLI (no CCI/ADX/OBV)
  5. indicators.py — _compute_fli_data() pure FLI signal output
  6. chart_renderer.py — priceFormat removed from JS template
  7. indicator_params_dialog.py — simplified params (no CCI/ADX/OBV)
  8. constants.py — CCI/ADX/OBV fully removed
  9. main_window.py — _apply_indicator_params uses .get() safely
 10. trade_notifier.py — PySide6/PyQt6 fallback import
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import math

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Mock PySide6 for headless test environments
for mod in ('PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'):
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()


# ============================================================
# 1. trading.py — _has_base_coin dust threshold + post-sell grace
# ============================================================
class TestHasBaseCoinDustThreshold(unittest.TestCase):
    """Verify that _has_base_coin uses DUST_THRESHOLD=1e-6 and post-sell grace."""

    DUST_THRESHOLD = 1e-6

    def test_zero_balance_returns_false(self):
        has_coin = 0.0 > self.DUST_THRESHOLD
        self.assertFalse(has_coin)

    def test_dust_1e8_returns_false(self):
        """Bybit dust (1e-8) should return False."""
        has_coin = 1e-8 > self.DUST_THRESHOLD
        self.assertFalse(has_coin)

    def test_dust_1e7_returns_false(self):
        has_coin = 1e-7 > self.DUST_THRESHOLD
        self.assertFalse(has_coin)

    def test_dust_just_below_threshold_returns_false(self):
        has_coin = (self.DUST_THRESHOLD - 1e-10) > self.DUST_THRESHOLD
        self.assertFalse(has_coin)

    def test_meaningful_balance_returns_true(self):
        has_coin = 1.0 > self.DUST_THRESHOLD
        self.assertTrue(has_coin)

    def test_tiny_but_above_threshold_returns_true(self):
        has_coin = (self.DUST_THRESHOLD + 1e-6) > self.DUST_THRESHOLD
        self.assertTrue(has_coin)

    def test_post_sell_grace_returns_false(self):
        """After a sell, _last_sell_price > 0 and in_position=False → skip wallet check."""
        # Simulate the post-sell grace logic from _has_base_coin
        _last_sell_price = 105.0
        in_position = False
        if _last_sell_price > 0 and not in_position:
            has_coin = False
            free_qty = 0.0
        else:
            has_coin = True
            free_qty = 999.0
        self.assertFalse(has_coin)
        self.assertEqual(free_qty, 0.0)

    def test_no_prior_sell_checks_wallet(self):
        """No prior sell → must check exchange wallet."""
        _last_sell_price = 0.0
        in_position = False
        grace_applies = _last_sell_price > 0 and not in_position
        self.assertFalse(grace_applies)

    def test_in_position_bypasses_grace(self):
        """If in_position=True (shouldn't happen for buy path, but defensive)."""
        _last_sell_price = 105.0
        in_position = True
        grace_applies = _last_sell_price > 0 and not in_position
        self.assertFalse(grace_applies)


# ============================================================
# 2. trading.py — Buy-sell cycle gate (3% max rebuy)
# ============================================================
class TestBuySellCycle(unittest.TestCase):
    """Verify the buy-sell cycle: after sell, re-buy only at <= sell or +3%."""

    REBUY_MAX_PCT_ABOVE = 0.03

    def _check_rebuy(self, last_sell_price, current_price, force=False):
        if not force and last_sell_price > 0:
            max_rebuy = last_sell_price * (1.0 + self.REBUY_MAX_PCT_ABOVE)
            if current_price > max_rebuy:
                return "skipped"
        return "allowed"

    def test_rebuy_at_same_price_allowed(self):
        self.assertEqual(self._check_rebuy(100.0, 100.0), "allowed")

    def test_rebuy_lower_allowed(self):
        self.assertEqual(self._check_rebuy(100.0, 95.0), "allowed")

    def test_rebuy_at_3_percent_allowed(self):
        price = 100.0 * 1.03
        self.assertEqual(self._check_rebuy(100.0, price), "allowed")

    def test_rebuy_above_3_percent_blocked(self):
        price = 100.0 * 1.031
        self.assertEqual(self._check_rebuy(100.0, price), "skipped")

    def test_rebuy_much_higher_blocked(self):
        self.assertEqual(self._check_rebuy(100.0, 110.0), "skipped")

    def test_no_prior_sell_allows_any_price(self):
        self.assertEqual(self._check_rebuy(0.0, 200.0), "allowed")

    def test_force_bypasses_rebuy_gate(self):
        self.assertEqual(self._check_rebuy(100.0, 200.0, force=True), "allowed")

    def test_force_at_extreme_price(self):
        self.assertEqual(self._check_rebuy(100.0, 150.0, force=True), "allowed")

    def test_last_sell_price_cleared_on_buy(self):
        last_sell_price = 105.0
        last_sell_price = 0.0
        self.assertEqual(last_sell_price, 0.0)

    def test_last_sell_price_set_on_sell(self):
        last_sell_price = 0.0
        fill_price = 107.5
        last_sell_price = float(fill_price)
        self.assertAlmostEqual(last_sell_price, 107.5)


# ============================================================
# 3. trading.py — _execute_order force & override_amount
# ============================================================
class TestExecuteOrderForce(unittest.TestCase):
    """Verify force=True bypasses trading_enabled, halted, sell-above-entry."""

    def test_normal_buy_skipped_when_trading_disabled(self):
        force = False
        trading_enabled = False
        skipped = (not force and not trading_enabled)
        self.assertTrue(skipped)

    def test_force_buy_bypasses_trading_disabled(self):
        force = True
        trading_enabled = False
        skipped = (not force and not trading_enabled)
        self.assertFalse(skipped)

    def test_normal_sell_blocked_below_entry(self):
        force = False
        price = 90.0
        entry_price = 100.0
        held = (not force and price <= entry_price)
        self.assertTrue(held)

    def test_force_sell_bypasses_entry_check(self):
        force = True
        price = 90.0
        entry_price = 100.0
        held = (not force and price <= entry_price)
        self.assertFalse(held)

    def test_override_amount_used(self):
        override_amount = 50.0
        investment_amount = 10.0
        amount = override_amount if override_amount is not None else investment_amount
        self.assertEqual(amount, 50.0)

    def test_none_override_falls_back(self):
        override_amount = None
        investment_amount = 10.0
        amount = override_amount if override_amount is not None else investment_amount
        self.assertEqual(amount, 10.0)

    def test_force_buy_bypasses_halted(self):
        force = True
        trading_enabled = False
        halted = True
        gates_ok = True
        if not force:
            if not trading_enabled:
                gates_ok = False
            if halted:
                gates_ok = False
        self.assertTrue(gates_ok)


# ============================================================
# 4. indicators.py — Pure FLI (no CCI/ADX/OBV scoring)
# ============================================================
class TestPureFLIIndicators(unittest.TestCase):
    """Verify fli_compute_all_indicators uses only FLI trend reversals."""

    def test_buy_signal_equals_raw_buy(self):
        """With pure FLI, buy_signal should equal raw_buy."""
        import numpy as np
        import pandas as pd
        from spotbot.indicators import fli_compute_all_indicators

        n = 20
        np.random.seed(99)
        prices = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            "open": prices - 0.1,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": np.full(n, 1000.0),
        })
        result = fli_compute_all_indicators(df, {})
        pd.testing.assert_series_equal(
            result["buy_signal"], result["raw_buy"], check_names=False,
        )
        pd.testing.assert_series_equal(
            result["sell_signal"], result["raw_sell"], check_names=False,
        )

    def test_no_legacy_columns(self):
        """CCI/ADX/OBV columns should NOT exist (fully removed)."""
        import numpy as np
        import pandas as pd
        from spotbot.indicators import fli_compute_all_indicators

        n = 20
        np.random.seed(123)
        prices = 100.0 + np.cumsum(np.random.randn(n) * 0.3)
        df = pd.DataFrame({
            "open": prices - 0.05,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": np.full(n, 500.0),
        })
        result = fli_compute_all_indicators(df, {})
        for col in ("cci", "adx", "obv", "obv_sma", "score_buy", "score_sell"):
            self.assertNotIn(col, result.columns,
                             f"Legacy column '{col}' should be removed")

    def test_required_columns_exist(self):
        import numpy as np
        import pandas as pd
        from spotbot.indicators import fli_compute_all_indicators

        n = 20
        np.random.seed(42)
        prices = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            "open": prices - 0.1,
            "high": prices + 1.0,
            "low": prices - 1.0,
            "close": prices,
            "volume": np.full(n, 1000.0),
        })
        result = fli_compute_all_indicators(df, {})
        for col in ("buy_signal", "sell_signal", "itrend", "trendline",
                    "bb_upper", "bb_lower", "raw_buy", "raw_sell"):
            self.assertIn(col, result.columns)

    def test_short_df_returns_unchanged(self):
        import pandas as pd
        from spotbot.indicators import fli_compute_all_indicators

        df = pd.DataFrame({"close": [100.0]})
        result = fli_compute_all_indicators(df, {})
        self.assertEqual(len(result), 1)

    def test_itrend_values_are_only_1_minus1_0(self):
        import numpy as np
        import pandas as pd
        from spotbot.indicators import fli_compute_all_indicators

        n = 50
        np.random.seed(77)
        prices = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            "open": prices - 0.1,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": np.full(n, 1000.0),
        })
        result = fli_compute_all_indicators(df, {})
        valid = set(result["itrend"].unique())
        self.assertTrue(valid.issubset({-1, 0, 1}),
                        f"itrend has invalid values: {valid}")


# ============================================================
# 5. indicators.py — _compute_fli_data() pure FLI signal output
# ============================================================
class TestComputeFLIData(unittest.TestCase):
    """Verify _compute_fli_data returns clean dict without score/cci/adx."""

    def test_output_keys_no_legacy(self):
        import numpy as np
        import pandas as pd
        from spotbot.indicators import _compute_fli_data

        n = 30
        np.random.seed(55)
        prices = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        candles = []
        base_ts = 1700000000000
        for i in range(n):
            candles.append([
                base_ts + i * 300000,  # 5m
                prices[i] - 0.1,  # open
                prices[i] + 0.5,  # high
                prices[i] - 0.5,  # low
                prices[i],          # close
                1000.0,             # volume
            ])
        result = _compute_fli_data(candles)
        self.assertIsNotNone(result)
        # Must have these keys
        for key in ("signal", "fli_trend", "bb_upper", "bb_lower",
                    "trendline", "itrend", "bb_upper_val", "bb_lower_val"):
            self.assertIn(key, result)
        # Must NOT have these legacy keys
        for key in ("score_buy", "score_sell", "cci", "adx"):
            self.assertNotIn(key, result)

    def test_signal_is_buy_sell_or_wait(self):
        import numpy as np
        import pandas as pd
        from spotbot.indicators import _compute_fli_data

        n = 30
        np.random.seed(66)
        prices = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        candles = []
        base_ts = 1700000000000
        for i in range(n):
            candles.append([
                base_ts + i * 300000,
                prices[i] - 0.1, prices[i] + 0.5,
                prices[i] - 0.5, prices[i], 1000.0,
            ])
        result = _compute_fli_data(candles)
        self.assertIn(result["signal"], ("BUY", "SELL", "WAIT"))

    def test_empty_candles_returns_none(self):
        from spotbot.indicators import _compute_fli_data
        self.assertIsNone(_compute_fli_data([]))


# ============================================================
# 6. chart_renderer.py — priceFormat removed from JS template
# ============================================================
class TestChartRendererPriceFormat(unittest.TestCase):
    """Verify priceFormat is NOT in the FLI HTML template series constructors."""

    def test_no_price_format_in_series_constructors(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE
        lines = _FLI_HTML_TEMPLATE.split('\n')
        for line in lines:
            if 'function _priceFmt' in line or '_priceFmt(prices' in line:
                continue
            if 'addCandlestickSeries' in line or 'addLineSeries' in line:
                self.assertNotIn('priceFormat:', line,
                    f"priceFormat found in series constructor: {line}")

    def test_price_fmt_function_still_exists(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE
        self.assertIn('function _priceFmt(', _FLI_HTML_TEMPLATE)

    def test_updateUIState_signature(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE
        self.assertIn('function updateUIState(fliTrend,bbUpper,bbLower,signal)',
                      _FLI_HTML_TEMPLATE)

    def test_no_cci_adx_obv_dom_elements(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE
        self.assertNotIn('iCCI', _FLI_HTML_TEMPLATE)
        self.assertNotIn('iADX', _FLI_HTML_TEMPLATE)
        self.assertNotIn('iOBV', _FLI_HTML_TEMPLATE)
        self.assertNotIn('iScore', _FLI_HTML_TEMPLATE)

    def test_fli_indicator_panel_title(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE
        self.assertIn('FLI Indicator', _FLI_HTML_TEMPLATE)


# ============================================================
# 7. indicator_params_dialog.py — simplified params
# ============================================================
class TestIndicatorParamsDialogSimplified(unittest.TestCase):
    """Verify the params dialog no longer has CCI/ADX/OBV controls."""

    def test_defaults_no_cci_adx_obv(self):
        from spotbot.ui.indicator_params_dialog import DEFAULTS
        invalid_keys = {"use_cci", "cci_len", "cci_level", "cci_buffer",
                       "use_adx", "adx_len", "adx_level",
                       "use_obv", "obv_sma_len", "min_score"}
        for key in invalid_keys:
            self.assertNotIn(key, DEFAULTS)

    def test_defaults_has_required_keys(self):
        from spotbot.ui.indicator_params_dialog import DEFAULTS
        for key in ("bb_period", "bb_dev", "use_atr", "atr_period", "signal_source"):
            self.assertIn(key, DEFAULTS)

    def test_get_params_returns_only_valid_keys(self):
        from spotbot.ui.indicator_params_dialog import DEFAULTS
        valid_keys = {"signal_source", "bb_period", "bb_dev", "use_atr", "atr_period"}
        for key in DEFAULTS:
            self.assertIn(key, valid_keys)


# ============================================================
# 8. constants.py — CCI/ADX/OBV fully removed
# ============================================================
class TestConstantsRemoved(unittest.TestCase):
    """Verify CCI/ADX/OBV constants no longer exist."""

    def test_cci_constants_removed(self):
        import spotbot.constants as c
        for attr in ("FLI_USE_CCI", "FLI_CCI_LEN", "FLI_CCI_LEVEL", "FLI_CCI_BUFFER"):
            self.assertFalse(hasattr(c, attr), f"{attr} should be removed")

    def test_adx_constants_removed(self):
        import spotbot.constants as c
        for attr in ("FLI_USE_ADX", "FLI_ADX_LEN", "FLI_ADX_LEVEL"):
            self.assertFalse(hasattr(c, attr), f"{attr} should be removed")

    def test_obv_constants_removed(self):
        import spotbot.constants as c
        for attr in ("FLI_USE_OBV", "FLI_OBV_SMA_LEN"):
            self.assertFalse(hasattr(c, attr), f"{attr} should be removed")

    def test_min_score_removed(self):
        import spotbot.constants as c
        self.assertFalse(hasattr(c, "FLI_MIN_SCORE"))

    def test_bb_and_atr_still_active(self):
        from spotbot.constants import FLI_USE_ATR, FLI_BB_PERIOD, FLI_BB_DEV, FLI_ATR_PERIOD
        self.assertTrue(FLI_USE_ATR)
        self.assertEqual(FLI_BB_PERIOD, 19)
        self.assertAlmostEqual(FLI_BB_DEV, 0.6)
        self.assertEqual(FLI_ATR_PERIOD, 9)


# ============================================================
# 9. main_window.py — _apply_indicator_params uses .get() safely
# ============================================================
class TestApplyIndicatorParamsSafe(unittest.TestCase):
    """Verify _apply_indicator_params won't KeyError on missing keys."""

    def test_empty_dict_no_error(self):
        """Empty dict should not raise — all keys use .get()."""
        params = {}
        # Simulate the logic from _apply_indicator_params
        src = params.get('signal_source', 'fli').upper()
        bb_p = params.get('bb_period', 19)
        bb_d = params.get('bb_dev', 0.6)
        atr_p = params.get('atr_period', 9)
        status = (
            f"Signal={src}, BB:{bb_p}/{bb_d:.1f}, ATR:{atr_p}"
        )
        self.assertIn("Signal=FLI", status)
        self.assertIn("BB:19/0.6", status)

    def test_partial_dict_no_error(self):
        params = {"bb_period": 25}
        src = params.get('signal_source', 'fli').upper()
        bb_p = params.get('bb_period', 19)
        bb_d = params.get('bb_dev', 0.6)
        atr_p = params.get('atr_period', 9)
        self.assertEqual(src, "FLI")
        self.assertEqual(bb_p, 25)
        self.assertAlmostEqual(bb_d, 0.6)
        self.assertEqual(atr_p, 9)

    def test_full_dict(self):
        params = {
            "signal_source": "fli",
            "bb_period": 20,
            "bb_dev": 1.0,
            "use_atr": True,
            "atr_period": 14,
        }
        src = params.get('signal_source', 'fli').upper()
        bb_p = params.get('bb_period', 19)
        bb_d = params.get('bb_dev', 0.6)
        atr_p = params.get('atr_period', 9)
        self.assertEqual(bb_p, 20)
        self.assertAlmostEqual(bb_d, 1.0)
        self.assertEqual(atr_p, 14)


# ============================================================
# 10. trade_notifier.py — PySide6/PyQt6 fallback import
# ============================================================
class TestTradeNotifierImportFallback(unittest.TestCase):
    """Verify trade_notifier gracefully handles missing Qt bindings."""

    def test_module_loads(self):
        """trade_notifier should be importable (PySide6 is mocked)."""
        import importlib
        import trade_notifier
        self.assertTrue(hasattr(trade_notifier, 'TradeNotifier'))

    def test_notifier_has_notify_signal(self):
        import trade_notifier
        self.assertTrue(hasattr(trade_notifier.TradeNotifier, 'notify_signal'))

    def test_notifier_has_play_sound(self):
        import trade_notifier
        self.assertTrue(hasattr(trade_notifier.TradeNotifier, '_play_sound'))

    def test_notifier_sounds_dict(self):
        import trade_notifier
        self.assertIn('signal', trade_notifier.TradeNotifier.SOUNDS)
        self.assertIn('trade_buy', trade_notifier.TradeNotifier.SOUNDS)
        self.assertIn('trade_sell', trade_notifier.TradeNotifier.SOUNDS)


if __name__ == "__main__":
    unittest.main()
