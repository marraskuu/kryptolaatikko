from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services import setup_historical_backfill, strategy_explorer


def _candle(close: float) -> dict:
    return {
        "timestamp": 1700000000000,
        "open": close,
        "close": close,
        "high": close,
        "low": close,
        "volume": 1.0,
    }


class ExitSimulationAccountingTests(SimpleTestCase):
    def setUp(self):
        self.candles = [_candle(100.0), _candle(103.0), _candle(95.0)]
        self.analysis = {"atrPct": 1.0}

    @patch("trading.services.setup_historical_backfill.dynamic_stop_pct", return_value=-5.0)
    def test_backfill_stop_after_partial_take_counts_locked_profit_once(self, _mock_stop):
        result = setup_historical_backfill.simulate_round_trip_pct(
            self.candles,
            0,
            self.analysis,
            "neutral",
        )

        self.assertAlmostEqual(result, -2.6)

    @patch("trading.services.strategy_explorer.dynamic_stop_pct", return_value=-5.0)
    def test_explorer_stop_after_partial_take_counts_locked_profit_once(self, _mock_stop):
        result = strategy_explorer._simulate_trade(
            self.candles,
            0,
            self.analysis,
            "neutral",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["reason"], "stop")
        self.assertAlmostEqual(result["returnPct"], -2.6)
