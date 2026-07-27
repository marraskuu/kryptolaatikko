"""blended_entry_size_eur: Kelly/ATR-varjokoon sekoitus eur_amount:iin, oletuksena pois päältä."""

from __future__ import annotations

from django.test import SimpleTestCase

from trading.services.engine import _apply_entry_size_blend
from trading.services.entry_diagnostics_shadow import blended_entry_size_eur


class BlendedEntrySizeTests(SimpleTestCase):
    def test_zero_weight_returns_original_unchanged(self):
        result = blended_entry_size_eur(
            "tBTCUSD",
            100.0,
            atr_shadow_map={"tBTCUSD": 200.0},
            kelly_shadow_map={"tBTCUSD": 300.0},
            kelly_weight=0.0,
            atr_weight=0.0,
        )
        self.assertEqual(result, 100.0)

    def test_kelly_takes_priority_over_atr_when_both_weighted(self):
        result = blended_entry_size_eur(
            "tBTCUSD",
            100.0,
            atr_shadow_map={"tBTCUSD": 200.0},
            kelly_shadow_map={"tBTCUSD": 300.0},
            kelly_weight=1.0,
            atr_weight=1.0,
        )
        self.assertEqual(result, 300.0)

    def test_untagged_symbol_falls_through_to_atr(self):
        result = blended_entry_size_eur(
            "tETHUSD",
            100.0,
            atr_shadow_map={"tETHUSD": 150.0},
            kelly_shadow_map={"tBTCUSD": 300.0},  # different symbol only
            kelly_weight=1.0,
            atr_weight=1.0,
        )
        self.assertEqual(result, 150.0)

    def test_neither_map_covers_symbol_returns_original(self):
        result = blended_entry_size_eur(
            "tXRPUSD",
            42.0,
            atr_shadow_map={},
            kelly_shadow_map={},
            kelly_weight=1.0,
            atr_weight=1.0,
        )
        self.assertEqual(result, 42.0)

    def test_partial_weight_blends_linearly(self):
        result = blended_entry_size_eur(
            "tBTCUSD",
            100.0,
            atr_shadow_map={"tBTCUSD": 200.0},
            kelly_shadow_map={},
            kelly_weight=0.0,
            atr_weight=0.5,
        )
        self.assertEqual(result, 150.0)

    def test_below_min_trade_eur_reverts_to_original(self):
        result = blended_entry_size_eur(
            "tBTCUSD",
            50.0,
            atr_shadow_map={"tBTCUSD": 1.0},
            kelly_shadow_map={},
            kelly_weight=0.0,
            atr_weight=1.0,
            min_trade_eur=10.0,
        )
        self.assertEqual(result, 50.0)

    def test_weight_clamped_to_one(self):
        result = blended_entry_size_eur(
            "tBTCUSD",
            100.0,
            atr_shadow_map={"tBTCUSD": 300.0},
            kelly_shadow_map={},
            kelly_weight=0.0,
            atr_weight=5.0,  # out-of-range weight should clamp to 1.0
        )
        self.assertEqual(result, 300.0)


class BlendPreservesBatchTotalTests(SimpleTestCase):
    def test_sum_preserved_across_full_kelly_coverage_at_any_weight(self):
        original = {"tBTCUSD": 100.0, "tETHUSD": 200.0, "tXRPUSD": 300.0}
        kelly_map = {"tBTCUSD": 150.0, "tETHUSD": 250.0, "tXRPUSD": 200.0}
        self.assertEqual(sum(kelly_map.values()), sum(original.values()))

        for weight in (0.0, 0.3, 0.7, 1.0):
            blended_total = sum(
                blended_entry_size_eur(
                    sym,
                    eur,
                    atr_shadow_map={},
                    kelly_shadow_map=kelly_map,
                    kelly_weight=weight,
                    atr_weight=0.0,
                )
                for sym, eur in original.items()
            )
            self.assertAlmostEqual(blended_total, sum(original.values()), places=2)


class ApplyEntrySizeBlendEngineWiringTests(SimpleTestCase):
    def test_default_zero_weights_leave_eur_amount_untouched(self):
        buy_decisions = [
            {"symbol": "tBTCUSD", "eurAmount": 100.0, "amount": 0.001,
             "analysis": {"currentPrice": 100000.0}},
        ]
        _apply_entry_size_blend(
            buy_decisions,
            atr_shadow_map={"tBTCUSD": 200.0},
            kelly_shadow_map={"tBTCUSD": 300.0},
            kelly_weight=0.0,
            atr_weight=0.0,
        )
        self.assertEqual(buy_decisions[0]["eurAmount"], 100.0)
        self.assertEqual(buy_decisions[0]["amount"], 0.001)

    def test_nonzero_weight_updates_eur_amount_and_amount(self):
        buy_decisions = [
            {"symbol": "tBTCUSD", "eurAmount": 100.0, "amount": 0.001,
             "analysis": {"currentPrice": 100000.0}},
        ]
        _apply_entry_size_blend(
            buy_decisions,
            atr_shadow_map={"tBTCUSD": 200.0},
            kelly_shadow_map={},
            kelly_weight=0.0,
            atr_weight=1.0,
        )
        self.assertEqual(buy_decisions[0]["eurAmount"], 200.0)
        self.assertAlmostEqual(buy_decisions[0]["amount"], 200.0 / 100000.0)

    def test_missing_price_skips_symbol_without_raising(self):
        buy_decisions = [
            {"symbol": "tBTCUSD", "eurAmount": 100.0, "amount": 0.001, "analysis": {}},
        ]
        _apply_entry_size_blend(
            buy_decisions,
            atr_shadow_map={"tBTCUSD": 200.0},
            kelly_shadow_map={},
            kelly_weight=0.0,
            atr_weight=1.0,
        )
        self.assertEqual(buy_decisions[0]["eurAmount"], 100.0)
