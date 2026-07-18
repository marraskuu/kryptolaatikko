"""Pitkä pito + hiipuva 1h/flow → aiempi arm + tiukempi trailing."""

from django.test import SimpleTestCase

from trading.services.sell_strategy import (
    LONG_HOLD_EARLY_TRIGGER_MULT,
    LONG_HOLD_STRICT_TRIGGER_MULT,
    compute_peak_exit_adjustments,
    long_hold_trigger_mult,
    update_profit_sell,
)


class LongHoldPeakExitTests(SimpleTestCase):
    def test_trigger_mult_early_when_2h_and_1h_fades(self):
        mult, signals = long_hold_trigger_mult(
            {"change1hPct": -0.2},
            hold_age_hours=2.5,
            profit_pct=1.2,
        )
        self.assertEqual(mult, LONG_HOLD_EARLY_TRIGGER_MULT)
        self.assertTrue(signals)

    def test_trigger_mult_strict_when_4h_and_flow_sell(self):
        mult, _ = long_hold_trigger_mult(
            {"change1hPct": 0.5, "flowBucket": "fl-"},
            hold_age_hours=4.5,
            profit_pct=1.0,
        )
        self.assertEqual(mult, LONG_HOLD_STRICT_TRIGGER_MULT)

    def test_no_early_arm_when_momentum_strong(self):
        mult, signals = long_hold_trigger_mult(
            {"change1hPct": 0.8, "flowBucket": "fl+"},
            hold_age_hours=5.0,
            profit_pct=1.5,
        )
        self.assertEqual(mult, 1.0)
        self.assertFalse(signals)

    def test_peak_adj_tightens_on_long_hold_fade(self):
        base = compute_peak_exit_adjustments(
            {"change1hPct": 0.5, "flowBucket": "fl+"},
            profit_pct=1.5,
            elapsed_ms=0,
            pullback_pct=0.0,
            hold_age_hours=1.0,
        )
        faded = compute_peak_exit_adjustments(
            {"change1hPct": -0.3, "flowBucket": "fl-"},
            profit_pct=1.5,
            elapsed_ms=0,
            pullback_pct=0.0,
            hold_age_hours=4.5,
        )
        self.assertTrue(faded["force_arm"])
        self.assertLess(faded["pullback_mult"], base["pullback_mult"])
        self.assertLess(faded["stabilize_ms"], base["stabilize_ms"])
        self.assertTrue(any("pitkä pito" in s or "hiipuva" in s for s in faded["signals"]))

    def test_update_profit_sell_arms_earlier_on_long_hold_fade(self):
        # Normaali trigger ~2 %; 4h + fade → 65 % → ~1.3 %
        watches: dict = {}
        analysis = {"change1hPct": -0.1, "flowBucket": "fl-"}
        result = update_profit_sell(
            watches,
            "tXMRUSD",
            current_price=101.5,
            avg_price=100.0,
            now_ms=1_000_000,
            atr_pct=None,
            analysis=analysis,
            hold_age_hours=4.5,
        )
        self.assertNotEqual(result["status"], "below_trigger")
        self.assertTrue(watches["tXMRUSD"]["active"])
        self.assertGreaterEqual(result["profitPct"], 1.4)
