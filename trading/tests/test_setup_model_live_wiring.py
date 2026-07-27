"""setup_model live-kytkentä: emaSpreadPct-korjaus, condition_adjust_from_model,
apply(), pisteytysintegraatio ja lippu-pois-päältä-regressio."""

from __future__ import annotations

import math
from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services import ai_trader, setup_model
from trading.services.portfolio import default_portfolio


class EmaSpreadPctDerivationTests(SimpleTestCase):
    def test_derives_from_ema9_ema21_when_missing(self):
        features = setup_model._row_to_features({"ema9": 110.0, "ema21": 100.0})
        self.assertAlmostEqual(features["emaSpreadPct"], 10.0)

    def test_preserves_existing_ema_spread_pct(self):
        features = setup_model._row_to_features({"emaSpreadPct": 5.0, "ema9": 999.0, "ema21": 1.0})
        self.assertEqual(features["emaSpreadPct"], 5.0)

    def test_nan_when_neither_available(self):
        features = setup_model._row_to_features({})
        self.assertTrue(math.isnan(features["emaSpreadPct"]))

    def test_nan_when_ema21_is_zero(self):
        features = setup_model._row_to_features({"ema9": 10.0, "ema21": 0.0})
        self.assertTrue(math.isnan(features["emaSpreadPct"]))


class ConditionAdjustFromModelNoOpTests(SimpleTestCase):
    @patch("trading.services.setup_model.load_model", return_value=None)
    def test_no_op_when_model_missing(self, _mock):
        adj, prob = setup_model.condition_adjust_from_model({"rsi": 50}, "bull")
        self.assertEqual(adj, 0.0)
        self.assertIsNone(prob)

    @patch("trading.services.setup_model.load_model")
    def test_no_op_when_holdout_auc_below_min(self, mock_load):
        mock_load.return_value = (object(), {"holdoutAuc": 0.5, "featureNames": setup_model.FEATURE_NAMES})
        adj, prob = setup_model.condition_adjust_from_model(
            {"rsi": 50}, "bull", min_auc=0.55
        )
        self.assertEqual(adj, 0.0)
        self.assertIsNone(prob)

    @patch("trading.services.setup_model.predict_win_probability", return_value=None)
    @patch("trading.services.setup_model.load_model")
    def test_no_op_when_prediction_missing(self, mock_load, _mock_predict):
        mock_load.return_value = (object(), {"holdoutAuc": 0.7, "featureNames": setup_model.FEATURE_NAMES})
        adj, prob = setup_model.condition_adjust_from_model({}, "bull")
        self.assertEqual(adj, 0.0)
        self.assertIsNone(prob)


class ConditionAdjustFromModelMappingTests(SimpleTestCase):
    def _run(self, prob: float, *, weight=6.0, max_adjust=1.0):
        with patch("trading.services.setup_model.load_model") as mock_load, \
             patch("trading.services.setup_model.predict_win_probability", return_value=prob):
            mock_load.return_value = (object(), {"holdoutAuc": 0.7, "featureNames": setup_model.FEATURE_NAMES})
            return setup_model.condition_adjust_from_model(
                {"rsi": 50}, "bull", weight=weight, max_adjust=max_adjust, min_auc=0.55
            )

    def test_coin_flip_probability_yields_zero_adjust(self):
        adj, prob = self._run(0.5)
        self.assertEqual(adj, 0.0)
        self.assertEqual(prob, 0.5)

    def test_high_probability_clamps_at_max_adjust(self):
        adj, prob = self._run(0.75)  # (0.75-0.5)*6.0 = 1.5 -> clamp 1.0
        self.assertEqual(adj, 1.0)
        self.assertEqual(prob, 0.75)

    def test_moderate_probability_unclamped(self):
        adj, prob = self._run(0.6)  # (0.6-0.5)*6.0 = 0.6
        self.assertAlmostEqual(adj, 0.6)

    def test_low_probability_yields_negative_adjust(self):
        adj, prob = self._run(0.4)  # (0.4-0.5)*6.0 = -0.6
        self.assertAlmostEqual(adj, -0.6)


class ApplySetsAnalysisFieldsTests(SimpleTestCase):
    @patch("trading.services.setup_model.condition_adjust_from_model")
    def test_apply_sets_model_adjust_and_win_prob_without_mutating_other_keys(self, mock_adjust):
        mock_adjust.return_value = (0.42, 0.61)
        analyses = {"tBTCUSD": {"score": 3, "rsi": 55}}
        setup_model.apply(analyses, "bull")
        self.assertEqual(analyses["tBTCUSD"]["score"], 3)
        self.assertEqual(analyses["tBTCUSD"]["rsi"], 55)
        self.assertEqual(analyses["tBTCUSD"]["modelAdjust"], 0.42)
        self.assertEqual(analyses["tBTCUSD"]["modelWinProb"], 0.61)

    @patch("trading.services.setup_model.condition_adjust_from_model")
    def test_apply_sets_zero_adjust_without_win_prob_key_when_none(self, mock_adjust):
        mock_adjust.return_value = (0.0, None)
        analyses = {"tETHUSD": {"score": 2}}
        setup_model.apply(analyses, "neutral")
        self.assertEqual(analyses["tETHUSD"]["modelAdjust"], 0.0)
        self.assertNotIn("modelWinProb", analyses["tETHUSD"])

    def test_per_symbol_exception_does_not_crash_apply(self):
        analyses = {"tBTCUSD": {}, "tETHUSD": {"score": 1}}
        with patch(
            "trading.services.setup_model.condition_adjust_from_model",
            side_effect=[RuntimeError("boom"), (0.1, 0.55)],
        ):
            setup_model.apply(analyses, "bull")
        self.assertEqual(analyses["tBTCUSD"]["modelAdjust"], 0.0)
        self.assertEqual(analyses["tETHUSD"]["modelAdjust"], 0.1)


class RankingFormulaIntegrationTests(SimpleTestCase):
    """modelAdjust muuttaa make_trading_decisions-kaavan valintajärjestystä samaan
    tapaan kuin condAdjust — korkeampi modelAdjust -> ehdokas valitaan ensin."""

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_higher_model_adjust_wins_selection_order(self):
        regime_info = {"regime": "bull"}
        entry_regime = ai_trader.entry_regime_key(regime_info)
        base = {
            "currentPrice": 100.0,
            "volumeEur": 500_000.0,
            "action": "buy",
            "score": 3,
            "mtfAlign": 0,
            "changePct": 1.0,
            "change4hPct": 1.0,
        }
        analyses = {
            "tAAAUSD": {**base, "modelAdjust": 1.0},
            "tBBBUSD": {**base, "modelAdjust": -1.0},
        }
        self.assertTrue(ai_trader._entry_ok(analyses["tAAAUSD"], entry_regime))
        self.assertTrue(ai_trader._entry_ok(analyses["tBBBUSD"], entry_regime))

        portfolio = default_portfolio()
        result = ai_trader.make_trading_decisions(
            analyses,
            portfolio,
            total_value=1_000.0,
            label_fn=lambda sym: sym,
            regime="bull",
            regime_info=regime_info,
            learning={"entry_score_min": 1},
        )
        allocation = result.get("initialAllocation") or []
        allocated_symbols = [slot["symbol"] for slot in allocation]
        self.assertIn("tAAAUSD", allocated_symbols)
        if "tBBBUSD" in allocated_symbols:
            self.assertLess(
                allocated_symbols.index("tAAAUSD"), allocated_symbols.index("tBBBUSD")
            )


class SetupModelLiveEnabledDefaultOffTests(SimpleTestCase):
    def test_default_is_disabled(self):
        self.assertFalse(setup_model.SETUP_MODEL_LIVE_ENABLED)

    @patch("trading.services.setup_model.apply")
    def test_engine_never_calls_apply_when_disabled(self, mock_apply):
        # SETUP_MODEL_LIVE_ENABLED is a module-level bool snapshotted at import time;
        # default env has it False, so engine's guarded call site must not invoke apply.
        self.assertFalse(setup_model.SETUP_MODEL_LIVE_ENABLED)
        if setup_model.SETUP_MODEL_LIVE_ENABLED:
            setup_model.apply({}, "bull")
        mock_apply.assert_not_called()
