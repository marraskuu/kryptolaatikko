"""Ostohetken varjodiagnostiikka (ideat #1 korrelaatio, #2 ATR-koko, #4 Kelly-koko)."""

from django.test import SimpleTestCase

from trading.services.entry_diagnostics_shadow import (
    atr_weighted_shadow_sizes,
    kelly_expectancy_shadow_sizes,
    max_correlation_vs_holdings,
)
from trading.services.learning import _compute_entry_diagnostics_shadow_tuning

# CORR_MIN_SAMPLES = 8 ai_trader.py:ssä — sarjat tarvitsevat vähintään 8 arvoa.
_UP_SERIES = [1.0, 2.0, -1.0, 3.0, 0.0, 2.0, -2.0, 1.0]
_DOWN_SERIES = [-1.0, -2.0, 1.0, -3.0, 0.0, -2.0, 2.0, -1.0]  # täysin vastakkainen


class MaxCorrelationVsHoldingsTests(SimpleTestCase):
    def test_flags_high_correlation_with_held_symbol(self):
        analyses = {
            "tBTCUSD": {"recentReturns": _UP_SERIES},
            "tETHUSD": {"recentReturns": list(_UP_SERIES)},
        }
        result = max_correlation_vs_holdings("tETHUSD", analyses, held_symbols=["tBTCUSD"])
        self.assertIsNotNone(result)
        self.assertTrue(result["highCorrFlag"])
        self.assertEqual(result["maxCorrSymbol"], "tBTCUSD")
        self.assertGreater(result["maxCorrValue"], 0.99)

    def test_no_flag_for_anti_correlated_symbol(self):
        analyses = {
            "tBTCUSD": {"recentReturns": _UP_SERIES},
            "tETHUSD": {"recentReturns": _DOWN_SERIES},
        }
        result = max_correlation_vs_holdings("tETHUSD", analyses, held_symbols=["tBTCUSD"])
        self.assertIsNotNone(result)
        self.assertFalse(result["highCorrFlag"])

    def test_returns_none_when_no_holdings(self):
        analyses = {"tETHUSD": {"recentReturns": _UP_SERIES}}
        self.assertIsNone(max_correlation_vs_holdings("tETHUSD", analyses, held_symbols=[]))

    def test_returns_none_without_recent_returns(self):
        analyses = {"tBTCUSD": {}, "tETHUSD": {}}
        self.assertIsNone(max_correlation_vs_holdings("tETHUSD", analyses, held_symbols=["tBTCUSD"]))


class AtrWeightedShadowSizesTests(SimpleTestCase):
    def test_weights_toward_lower_volatility_symbol_and_preserves_total(self):
        batch = [
            {"symbol": "A", "eurAmount": 100.0, "analysis": {"atrPct": 1.0}},
            {"symbol": "B", "eurAmount": 100.0, "analysis": {"atrPct": 4.0}},
        ]
        shadow = atr_weighted_shadow_sizes(batch)
        self.assertGreater(shadow["A"], shadow["B"])
        self.assertAlmostEqual(shadow["A"] + shadow["B"], 200.0, places=2)

    def test_empty_batch_returns_empty(self):
        self.assertEqual(atr_weighted_shadow_sizes([]), {})


class KellyExpectancyShadowSizesTests(SimpleTestCase):
    def test_skews_toward_higher_expectancy_bucket(self):
        batch = [
            {"symbol": "A", "eurAmount": 100.0, "analysis": {"geminiSignal": {"confidence": 9}}},
            {"symbol": "B", "eurAmount": 100.0, "analysis": {"geminiSignal": {"confidence": 6}}},
        ]
        stats = {
            9: {"trades": 10, "expectancy_eur": 0.5},
            6: {"trades": 10, "expectancy_eur": 2.0},
        }
        shadow = kelly_expectancy_shadow_sizes(batch, stats, min_samples=8)
        self.assertGreater(shadow["B"], shadow["A"])
        self.assertAlmostEqual(shadow["A"] + shadow["B"], 200.0, places=2)

    def test_untagged_symbol_keeps_own_eur_amount(self):
        batch = [
            {"symbol": "A", "eurAmount": 100.0, "analysis": {"geminiSignal": {"confidence": 9}}},
            {"symbol": "C", "eurAmount": 50.0, "analysis": {}},
        ]
        stats = {9: {"trades": 10, "expectancy_eur": 0.5}}
        shadow = kelly_expectancy_shadow_sizes(batch, stats, min_samples=8)
        self.assertEqual(shadow["C"], 50.0)

    def test_no_stats_returns_empty(self):
        batch = [{"symbol": "A", "eurAmount": 100.0, "analysis": {}}]
        self.assertEqual(kelly_expectancy_shadow_sizes(batch, None), {})


class ComputeEntryDiagnosticsShadowTuningTests(SimpleTestCase):
    def test_buckets_realized_pnl_by_shadow_flags(self):
        linked = [
            {
                "sell": {"profitLoss": 5.0, "fee": 0.0},
                "entry_shadow_high_corr_flag": True,
                "entry_shadow_atr_size_delta_eur": 10.0,
                "entry_shadow_kelly_size_delta_eur": None,
            },
            {
                "sell": {"profitLoss": -3.0, "fee": 0.0},
                "entry_shadow_high_corr_flag": False,
                "entry_shadow_atr_size_delta_eur": -5.0,
                "entry_shadow_kelly_size_delta_eur": 2.0,
            },
        ]
        result = _compute_entry_diagnostics_shadow_tuning(linked)
        self.assertEqual(result["correlation_shadow"]["high_corr"]["trades"], 1)
        self.assertEqual(result["correlation_shadow"]["low_corr"]["trades"], 1)
        self.assertEqual(result["atr_size_shadow"]["atr_undersized"]["trades"], 1)
        self.assertEqual(result["atr_size_shadow"]["atr_oversized"]["trades"], 1)
        self.assertEqual(result["kelly_size_shadow"]["kelly_undersized"]["trades"], 1)
        self.assertEqual(result["kelly_size_shadow"]["kelly_oversized"]["trades"], 0)

    def test_empty_linked_returns_zeroed_buckets(self):
        result = _compute_entry_diagnostics_shadow_tuning([])
        self.assertEqual(result["correlation_shadow"]["high_corr"]["trades"], 0)
        self.assertEqual(result["atr_size_shadow"]["atr_oversized"]["trades"], 0)
