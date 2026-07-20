"""Pitkä pito + hiipuva 1h/flow → aiempi arm + tiukempi trailing."""

from django.test import SimpleTestCase

from trading.services.ai_trader import _gemini_partial_block_reason
from trading.services.sell_strategy import (
    LONG_HOLD_EARLY_TRIGGER_MULT,
    LONG_HOLD_STRICT_TRIGGER_MULT,
    compute_peak_exit_adjustments,
    long_hold_partial_policy,
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


class LongHoldPartialTakeTests(SimpleTestCase):
    def test_partial_policy_2h_larger_fraction(self):
        policy = long_hold_partial_policy(
            {"change1hPct": -0.2, "flowBucket": "fl-"},
            hold_age_hours=2.5,
            profit_pct=2.0,
        )
        self.assertFalse(policy["skip_partial"])
        self.assertEqual(policy["fraction_mult"], 1.5)
        self.assertTrue(policy["arm_remainder"])
        self.assertLess(policy["trigger_mult"], 1.0)

    def test_partial_policy_4h_skips(self):
        policy = long_hold_partial_policy(
            {"change1hPct": -0.1, "flowBucket": "fl-"},
            hold_age_hours=5.0,
            profit_pct=2.0,
        )
        self.assertTrue(policy["skip_partial"])
        self.assertTrue(any("ohita porras" in s for s in policy["signals"]))

    def test_partial_larger_and_armed_when_2h_fade(self):
        watches: dict = {}
        # 2.6 % > lowered partial trigger (~2.5 * 0.75 = 1.875)
        result = update_profit_sell(
            watches,
            "tETHUSD",
            current_price=102.6,
            avg_price=100.0,
            now_ms=2_000_000,
            atr_pct=None,
            analysis={"change1hPct": -0.2, "flowBucket": "fl-"},
            hold_age_hours=2.5,
        )
        self.assertEqual(result["status"], "tier1")
        self.assertTrue(result["shouldSell"])
        self.assertGreater(result["sellFraction"], 0.30)
        self.assertLessEqual(result["sellFraction"], 0.55)
        self.assertTrue(watches["tETHUSD"]["armed"])
        self.assertTrue(
            any("porras 1" in s or "pito" in s for s in result["exitSignals"])
        )

    def test_strict_long_hold_skips_partial_for_full_trail(self):
        watches: dict = {}
        result = update_profit_sell(
            watches,
            "tXMRUSD",
            current_price=103.0,
            avg_price=100.0,
            now_ms=3_000_000,
            atr_pct=None,
            analysis={"change1hPct": -0.1, "flowBucket": "fl-"},
            hold_age_hours=5.0,
        )
        # Ei tier1-myyntiä — ohitettu porras, trailing aktiivinen
        self.assertNotEqual(result["status"], "tier1")
        self.assertTrue(watches["tXMRUSD"].get("tier1Taken"))
        self.assertTrue(watches["tXMRUSD"]["active"])
        self.assertTrue(
            any(
                "ohita porras" in s or "trailing" in s
                for s in (result.get("exitSignals") or [])
            )
            or watches["tXMRUSD"].get("tier1Taken")
        )

    def test_skip_partial_below_trigger_does_not_poison_later_partial(self):
        """Below trigger Gemini is guarded, but tier1 is not marked as done."""
        watches: dict = {}
        result = update_profit_sell(
            watches,
            "tXMRUSD",
            current_price=101.0,
            avg_price=100.0,
            now_ms=3_500_000,
            atr_pct=None,
            analysis={"change1hPct": -0.2, "flowBucket": "fl-"},
            hold_age_hours=5.0,
        )
        self.assertNotEqual(result["status"], "tier1")
        self.assertFalse(watches["tXMRUSD"].get("tier1Taken"))
        self.assertTrue(watches["tXMRUSD"].get("skipPartialActive"))
        self.assertAlmostEqual(result["profitPct"], 1.0, places=1)

        recovered = update_profit_sell(
            watches,
            "tXMRUSD",
            current_price=103.0,
            avg_price=100.0,
            now_ms=3_560_000,
            atr_pct=None,
            analysis={"change1hPct": 0.4, "flowBucket": "fl+"},
            hold_age_hours=5.0,
        )
        self.assertEqual(recovered["status"], "tier1")
        self.assertTrue(recovered["shouldSell"])
        self.assertTrue(watches["tXMRUSD"].get("tier1Taken"))
        self.assertFalse(watches["tXMRUSD"].get("skipPartialActive"))

    def test_normal_partial_unchanged_without_fade(self):
        watches: dict = {}
        result = update_profit_sell(
            watches,
            "tBTCUSD",
            current_price=103.0,
            avg_price=100.0,
            now_ms=4_000_000,
            atr_pct=None,
            analysis={"change1hPct": 1.0, "flowBucket": "fl+"},
            hold_age_hours=5.0,
        )
        self.assertEqual(result["status"], "tier1")
        self.assertAlmostEqual(result["sellFraction"], 0.30, places=2)
        self.assertFalse(watches["tBTCUSD"]["armed"])


class GeminiPartialBlockReasonTests(SimpleTestCase):
    def test_skip_partial_active_uses_temporary_block_reason(self):
        reason = _gemini_partial_block_reason(
            "tXMRUSD",
            {"tXMRUSD": {"skipPartialActive": True}},
        )
        self.assertIn("pitkä pito", reason)
        self.assertNotIn("porras 1 jo tehty", reason)

    def test_tier1_taken_still_blocks_as_completed_partial(self):
        reason = _gemini_partial_block_reason(
            "tXMRUSD",
            {"tXMRUSD": {"tier1Taken": True}},
        )
        self.assertIn("porras 1 jo tehty", reason)
