"""Rullaava monipäiväinen drawdown-katkaisu (varjodiagnostiikka, idea #3)."""

from django.test import SimpleTestCase

from trading.services.daily_policy_shadow import (
    ROLLING_DRAWDOWN_STOP_PCT,
    ROLLING_DRAWDOWN_WINDOW_DAYS,
    _rolling_window_start_value,
    evaluate_policy,
)


class RollingDrawdownFlagsTests(SimpleTestCase):
    def test_rolling_drawdown_active_below_threshold(self):
        flags = evaluate_policy(0.0, "neutral", None, rolling_pnl_pct=-3.0)
        self.assertEqual(flags["rollingPnlPct"], -3.0)
        self.assertTrue(flags["rollingDrawdownActive"])

    def test_rolling_drawdown_inactive_above_threshold(self):
        flags = evaluate_policy(0.0, "neutral", None, rolling_pnl_pct=-0.5)
        self.assertFalse(flags["rollingDrawdownActive"])

    def test_rolling_pnl_omitted_when_not_provided(self):
        flags = evaluate_policy(0.0, "neutral", None)
        self.assertNotIn("rollingPnlPct", flags)
        self.assertNotIn("rollingDrawdownActive", flags)

    def test_daily_stop_and_aggressive_unchanged_by_rolling_param(self):
        learning = {"stats": {}, "overall_expectancy_eur": 1.0, "samples": 10}
        without_rolling = evaluate_policy(-1.5, "bull", learning)
        with_rolling = evaluate_policy(-1.5, "bull", learning, rolling_pnl_pct=-5.0)
        for key in ("dayPnlPct", "dailyStopActive", "profitLockTier", "aggressiveEligible", "winRate", "overallExp"):
            self.assertEqual(without_rolling[key], with_rolling[key])


class RollingWindowStartValueTests(SimpleTestCase):
    def test_returns_none_without_enough_history(self):
        shadow = {"dayStartValue": 1000.0, "days": []}
        self.assertIsNone(_rolling_window_start_value(shadow, window_days=3))

    def test_returns_start_value_once_enough_days_exist(self):
        # days[0] = eilinen, days[1] = toissapäivä jne. (uusin ensin)
        shadow = {
            "dayStartValue": 1010.0,
            "days": [
                {"dayKey": "2026-07-21", "startValue": 1000.0, "endValue": 1010.0},
                {"dayKey": "2026-07-20", "startValue": 990.0, "endValue": 1000.0},
                {"dayKey": "2026-07-19", "startValue": 980.0, "endValue": 990.0},
            ],
        }
        # window_days=3 tarvitsee 2 päättynyttä päivää -> days[1]["startValue"]
        self.assertEqual(_rolling_window_start_value(shadow, window_days=3), 990.0)

    def test_window_days_one_uses_today_start(self):
        shadow = {"dayStartValue": 1010.0, "days": []}
        self.assertEqual(_rolling_window_start_value(shadow, window_days=1), 1010.0)

    def test_default_window_matches_module_constant(self):
        shadow = {
            "dayStartValue": 1010.0,
            "days": [
                {"startValue": 1000.0},
                {"startValue": 990.0},
            ],
        }
        expected_index = ROLLING_DRAWDOWN_WINDOW_DAYS - 2
        expected = shadow["days"][expected_index]["startValue"] if expected_index >= 0 else shadow["dayStartValue"]
        self.assertEqual(_rolling_window_start_value(shadow), expected)
        self.assertLess(ROLLING_DRAWDOWN_STOP_PCT, 0)
