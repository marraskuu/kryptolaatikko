from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import make_trading_decisions
from trading.services.engine import _refresh_analyses
from trading.services.portfolio import default_portfolio


_MICRO_OK = {"microChecked": True, "microBlocked": False}


def _buy_analysis(price: float, score: int = 8) -> dict:
    return {
        "currentPrice": price,
        "volumeEur": 1_000_000.0,
        "action": "buy",
        "score": score,
        "mtfAlign": 2,
        "changePct": 2.0,
        "change4hPct": 1.0,
        **_MICRO_OK,
    }


class EngineStaleAnalysesTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    @patch("trading.services.engine.analyze_ticker_quick")
    def test_refresh_analyses_drops_symbols_missing_from_current_tickers(self, mock_quick):
        mock_quick.return_value = _buy_analysis(3_000.0, score=4)
        stale_symbol = "tBTCUSD"
        live_symbol = "tETHUSD"
        state = {
            "tickers": {
                live_symbol: {
                    "last": 3_000.0,
                    "volumeEur": 1_000_000.0,
                    "changePct": 1.0,
                }
            },
            "analyses": {
                stale_symbol: _buy_analysis(60_000.0, score=10),
            },
        }

        _refresh_analyses(state)

        self.assertNotIn(stale_symbol, state["analyses"])
        self.assertIn(live_symbol, state["analyses"])

        portfolio = default_portfolio()
        result = make_trading_decisions(
            state["analyses"],
            portfolio,
            total_value=1_000.0,
            label_fn=lambda sym: sym,
            regime="bull",
            regime_info={"regime": "bull", "phase": "bull"},
            learning={"entry_score_min": 1},
        )

        allocated = [slot["symbol"] for slot in result.get("initialAllocation") or []]
        self.assertNotIn(stale_symbol, allocated)
