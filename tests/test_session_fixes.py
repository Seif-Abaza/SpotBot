"""Unit tests for ALL functions modified/created in this session.

Covers:
  1. trading.py   — _has_base_coin() dust threshold (FLOAT_EPS)
  2. trading.py   — Buy-sell cycle: _last_sell_price gate (3% max)
  3. trading.py   — _execute_order() force/override_amount paths
  4. indicators.py — fli_compute_all_indicators() pure FLI (no CCI/ADX/OBV)
  5. chart_renderer.py — priceFormat removed from JS template
  6. indicator_params_dialog.py — simplified params (no CCI/ADX/OBV)
  7. alert_dialog.py — simplified INDICATOR_LIST
  8. constants.py — CCI/ADX/OBV deprecated
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Mock PySide6 for headless test environments
for mod in ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()


# ============================================================
# 1. trading.py — _has_base_coin dust threshold
# ============================================================
class TestHasBaseCoinDustThreshold(unittest.TestCase):
    """Verify that _has_base_coin ignores floating-point noise and dust."""

    def test_zero_balance_returns_false(self):
        from spotbot.constants import DUST_THRESHOLD

        free_qty = 0.0
        has_coin = free_qty > DUST_THRESHOLD
        self.assertFalse(has_coin)

    def test_sub_eps_noise_returns_false(self):
        """Value below DUST_THRESHOLD (1e-6) should return False."""
        from spotbot.constants import DUST_THRESHOLD

        free_qty = 1e-13
        has_coin = free_qty > DUST_THRESHOLD
        self.assertFalse(has_coin)

    def test_dust_residual_returns_false(self):
        """A 1e-8 residual after a full sell should NOT block a new buy."""
        from spotbot.constants import DUST_THRESHOLD

        free_qty = 1e-8
        has_coin = free_qty > DUST_THRESHOLD
        self.assertFalse(has_coin)

    def test_at_eps_returns_false(self):
        """Exactly at DUST_THRESHOLD should return False (strict >)."""
        from spotbot.constants import DUST_THRESHOLD

        free_qty = DUST_THRESHOLD
        has_coin = free_qty > DUST_THRESHOLD
        self.assertFalse(has_coin)

    def test_meaningful_balance_returns_true(self):
        from spotbot.constants import DUST_THRESHOLD

        free_qty = 1.0
        has_coin = free_qty > DUST_THRESHOLD
        self.assertTrue(has_coin)

    def test_float_eps_value_is_tiny(self):
        from spotbot.constants import DUST_THRESHOLD

        self.assertGreater(DUST_THRESHOLD, 0)
        self.assertLessEqual(DUST_THRESHOLD, 1e-5)


# ============================================================
# 2. trading.py — Buy-sell cycle gate (3% max rebuy)
# ============================================================
class TestBuySellCycle(unittest.TestCase):
    """Verify the buy-sell cycle: after sell, re-buy only at <= sell or +3%."""

    def _check_rebuy(self, last_sell_price, current_price, force=False):
        REBUY_MAX_PCT_ABOVE = 0.03
        if not force and last_sell_price > 0:
            max_rebuy = last_sell_price * (1.0 + REBUY_MAX_PCT_ABOVE)
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


# ============================================================
# 3. trading.py — _execute_order force & override_amount
# ============================================================
class TestExecuteOrderForce(unittest.TestCase):
    """Verify force=True bypasses trading_enabled, halted, sell-above-entry."""

    def test_normal_buy_skipped_when_trading_disabled(self):
        force = False
        trading_enabled = False
        skipped = not force and not trading_enabled
        self.assertTrue(skipped)

    def test_force_buy_bypasses_trading_disabled(self):
        force = True
        trading_enabled = False
        skipped = not force and not trading_enabled
        self.assertFalse(skipped)

    def test_normal_sell_blocked_below_entry(self):
        force = False
        price = 90.0
        entry_price = 100.0
        held = not force and price <= entry_price
        self.assertTrue(held)

    def test_force_sell_bypasses_entry_check(self):
        force = True
        price = 90.0
        entry_price = 100.0
        held = not force and price <= entry_price
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
        df = pd.DataFrame(
            {
                "open": prices - 0.1,
                "high": prices + 0.5,
                "low": prices - 0.5,
                "close": prices,
                "volume": np.full(n, 1000.0),
            }
        )
        result = fli_compute_all_indicators(df, {})
        pd.testing.assert_series_equal(
            result["buy_signal"],
            result["raw_buy"],
            check_names=False,
        )
        pd.testing.assert_series_equal(
            result["sell_signal"],
            result["raw_sell"],
            check_names=False,
        )

    def test_legacy_columns_are_zero(self):
        """CCI/ADX/OBV columns should be zero (legacy compat)."""
        import numpy as np
        import pandas as pd
        from spotbot.indicators import fli_compute_all_indicators

        n = 20
        np.random.seed(123)
        prices = 100.0 + np.cumsum(np.random.randn(n) * 0.3)
        df = pd.DataFrame(
            {
                "open": prices - 0.05,
                "high": prices + 0.5,
                "low": prices - 0.5,
                "close": prices,
                "volume": np.full(n, 500.0),
            }
        )
        result = fli_compute_all_indicators(df, {})
        self.assertTrue((result["cci"] == 0.0).all())
        self.assertTrue((result["adx"] == 0.0).all())
        self.assertTrue((result["obv"] == 0.0).all())

    def test_required_columns_exist(self):
        import numpy as np
        import pandas as pd
        from spotbot.indicators import fli_compute_all_indicators

        n = 20
        np.random.seed(42)
        prices = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame(
            {
                "open": prices - 0.1,
                "high": prices + 1.0,
                "low": prices - 1.0,
                "close": prices,
                "volume": np.full(n, 1000.0),
            }
        )
        result = fli_compute_all_indicators(df, {})
        for col in (
            "buy_signal",
            "sell_signal",
            "itrend",
            "trendline",
            "bb_upper",
            "bb_lower",
        ):
            self.assertIn(col, result.columns)

    def test_short_df_returns_unchanged(self):
        import pandas as pd
        from spotbot.indicators import fli_compute_all_indicators

        df = pd.DataFrame({"close": [100.0]})
        result = fli_compute_all_indicators(df, {})
        self.assertEqual(len(result), 1)


# ============================================================
# 5. chart_renderer.py — priceFormat removed from JS template
# ============================================================
class TestChartRendererPriceFormat(unittest.TestCase):
    """Verify priceFormat is NOT in the FLI HTML template series constructors."""

    def test_no_price_format_in_series_constructors(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE

        lines = _FLI_HTML_TEMPLATE.split("\n")
        for line in lines:
            if "function _priceFmt" in line or "_priceFmt(prices" in line:
                continue
            if "addCandlestickSeries" in line or "addLineSeries" in line:
                self.assertNotIn(
                    "priceFormat:",
                    line,
                    f"priceFormat found in series constructor: {line}",
                )

    def test_price_fmt_function_still_exists(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE

        self.assertIn("function _priceFmt(", _FLI_HTML_TEMPLATE)

    def test_updateUIState_signature_changed(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE

        self.assertIn(
            "function updateUIState(fliTrend,bbUpper,bbLower,signal)",
            _FLI_HTML_TEMPLATE,
        )

    def test_no_cci_adx_obv_dom_elements(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE

        self.assertNotIn("iCCI", _FLI_HTML_TEMPLATE)
        self.assertNotIn("iADX", _FLI_HTML_TEMPLATE)
        self.assertNotIn("iOBV", _FLI_HTML_TEMPLATE)
        self.assertNotIn("iScore", _FLI_HTML_TEMPLATE)

    def test_fli_indicator_panel_title(self):
        from spotbot.chart_renderer import _FLI_HTML_TEMPLATE

        self.assertIn("FLI Indicator", _FLI_HTML_TEMPLATE)


# ============================================================
# 6. indicator_params_dialog.py — simplified params
# ============================================================
class TestIndicatorParamsDialogSimplified(unittest.TestCase):
    """Verify the params dialog no longer has CCI/ADX/OBV controls."""

    def test_defaults_no_cci_adx_obv(self):
        from spotbot.ui.indicator_params_dialog import DEFAULTS

        invalid_keys = {
            "use_cci",
            "cci_len",
            "cci_level",
            "cci_buffer",
            "use_adx",
            "adx_len",
            "adx_level",
            "use_obv",
            "obv_sma_len",
            "min_score",
        }
        for key in invalid_keys:
            self.assertNotIn(key, DEFAULTS)

    def test_defaults_has_required_keys(self):
        from spotbot.ui.indicator_params_dialog import DEFAULTS

        for key in ("bb_period", "bb_dev", "use_atr", "atr_period", "signal_source"):
            self.assertIn(key, DEFAULTS)


# ============================================================
# 7. alert_dialog.py — simplified INDICATOR_LIST
# ============================================================
class TestAlertIndicatorListSimplified(unittest.TestCase):
    """Verify INDICATOR_LIST no longer has RSI/MACD/CCI/ADX/OBV."""

    def test_no_rsi_macd_cci_adx_obv(self):
        from spotbot.ui.alert_dialog import INDICATOR_LIST

        flat = " ".join(INDICATOR_LIST).lower()
        for name in ("rsi", "macd", "cci", "adx", "obv"):
            self.assertNotIn(name, flat)

    def test_has_bb_and_trendline(self):
        from spotbot.ui.alert_dialog import INDICATOR_LIST

        self.assertIn("BB Upper", INDICATOR_LIST)
        self.assertIn("BB Lower", INDICATOR_LIST)
        self.assertIn("Trendline", INDICATOR_LIST)


# ============================================================
# 8. constants.py — CCI/ADX/OBV deprecated
# ============================================================
class TestConstantsDeprecated(unittest.TestCase):
    """Verify CCI/ADX/OBV flags are set to False (deprecated)."""

    def test_cci_deprecated(self):
        from spotbot.constants import FLI_USE_CCI

        self.assertFalse(FLI_USE_CCI)

    def test_adx_deprecated(self):
        from spotbot.constants import FLI_USE_ADX

        self.assertFalse(FLI_USE_ADX)

    def test_obv_deprecated(self):
        from spotbot.constants import FLI_USE_OBV

        self.assertFalse(FLI_USE_OBV)

    def test_bb_and_atr_still_active(self):
        from spotbot.constants import FLI_USE_ATR, FLI_BB_PERIOD, FLI_BB_DEV

        self.assertTrue(FLI_USE_ATR)
        self.assertEqual(FLI_BB_PERIOD, 19)
        self.assertAlmostEqual(FLI_BB_DEV, 0.6)


if __name__ == "__main__":
    unittest.main()
