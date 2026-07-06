"""ranked_buyable-fallback — karhussa ei osteta ilman _entry_ok-suodatinta."""

from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import _entry_ok, entry_regime_key, make_trading_decisions
from trading.services.portfolio import default_portfolio


class RankedBuyableFallbackTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_bear_fallback_skips_symbols_failing_entry_ok(self):
        """Likvidit mutta heikot signaalit eivät päädy alkuallokaatioon karhumarkkinassa."""
        symbol = "tETHUSD"
        regime_info = {"regime": "bear"}
        entry_regime = entry_regime_key(regime_info)
        analysis = {
            "currentPrice": 3_000.0,
            "volumeEur": 500_000.0,
            "action": "buy",
            "score": 0,
            "mtfAlign": 0,
            "changePct": 2.0,
            "change4hPct": 1.0,
        }
        self.assertFalse(_entry_ok(analysis, entry_regime))

        analyses = {symbol: analysis}
        portfolio = default_portfolio()

        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=1_000.0,
            label_fn=lambda sym: sym,
            regime="bear",
            regime_info=regime_info,
            learning={"entry_score_min": 1},
        )

        allocation = result.get("initialAllocation") or []
        allocated_symbols = [slot["symbol"] for slot in allocation]
        self.assertNotIn(symbol, allocated_symbols)

        buy_decisions = [d for d in result.get("decisions", []) if d.get("type") == "buy"]
        self.assertFalse(any(d.get("symbol") == symbol for d in buy_decisions))
        self.assertFalse(result.get("initialAllocation"))
