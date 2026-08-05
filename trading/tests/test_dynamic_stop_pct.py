"""dynamic_stop_pct: tiukennetut katot, env-override ja absoluuttinen backstop."""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from trading.services import ai_trader


class DynamicStopPctCapTests(SimpleTestCase):
    def test_bull_cap_uses_new_tightened_default(self):
        analysis = {"atrPct": 20.0}  # extreme ATR -> should clamp at cap
        stop = ai_trader.dynamic_stop_pct(analysis, "bull")
        self.assertEqual(stop, ai_trader.STOP_CAP_BULL_PCT)

    def test_neutral_cap_uses_new_tightened_default(self):
        analysis = {"atrPct": 20.0}
        stop = ai_trader.dynamic_stop_pct(analysis, "neutral")
        self.assertEqual(stop, ai_trader.STOP_CAP_NEUTRAL_PCT)

    def test_bear_cap_uses_new_tightened_default(self):
        analysis = {"atrPct": 20.0}
        stop = ai_trader.dynamic_stop_pct(analysis, "bear")
        self.assertEqual(stop, ai_trader.STOP_CAP_BEAR_PCT)

    def test_moderate_atr_stays_within_bounds_unchanged(self):
        # Small ATR -> stop determined by atr_mult*atr, not by cap/floor -> unaffected by this change.
        analysis = {"atrPct": 1.0}
        stop = ai_trader.dynamic_stop_pct(analysis, "neutral")
        expected = -ai_trader.REGIME_STOP_PROFILES["neutral"]["atr_mult"] * 1.0
        self.assertAlmostEqual(stop, expected)


class DynamicStopPctAbsoluteBackstopTests(SimpleTestCase):
    def test_loosened_cap_scale_still_bounded_by_absolute_backstop(self):
        analysis = {"atrPct": 50.0}
        # Simulate a hypothetical/buggy tuner loosening the cap well past the profile default.
        stop_tuning = {"atr_scale": 1.0, "floor_scale": 1.0, "cap_scale": 3.0}
        stop = ai_trader.dynamic_stop_pct(analysis, "bull", stop_tuning)
        self.assertGreaterEqual(stop, ai_trader.STOP_LOSS_ABS_MAX_PCT)
        self.assertEqual(stop, ai_trader.STOP_LOSS_ABS_MAX_PCT)

    def test_backstop_does_not_affect_normal_tighter_caps(self):
        analysis = {"atrPct": 20.0}
        stop = ai_trader.dynamic_stop_pct(analysis, "bear")
        # Bear cap is within the absolute backstop, so backstop is a no-op here.
        self.assertEqual(stop, ai_trader.STOP_CAP_BEAR_PCT)


class DynamicStopPctEnvOverrideTests(SimpleTestCase):
    @mock.patch.dict("os.environ", {"STOP_CAP_BULL_PCT": "-4.0"})
    def test_env_override_changes_effective_cap(self):
        import importlib

        reloaded = importlib.reload(ai_trader)
        try:
            analysis = {"atrPct": 20.0}
            stop = reloaded.dynamic_stop_pct(analysis, "bull")
            self.assertEqual(stop, -4.0)
        finally:
            importlib.reload(ai_trader)
