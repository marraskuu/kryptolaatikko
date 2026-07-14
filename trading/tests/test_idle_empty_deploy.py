"""Tyhjä salkku + idle-cash: deploy parhaaseen ranked_buyable-kohteeseen."""

from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import make_trading_decisions
from trading.services.portfolio import default_portfolio

_MICRO_OK = {"microChecked": True, "microBlocked": False}


class IdleEmptyDeployTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_idle_empty_deploy_reallows_score_blocked_symbol(self):
        """Score-esto poistuu idle-tyhjällä salkulla — BTC voi palata (A + B)."""
        btc = "tBTCUSD"
        analyses = {
            btc: {
                "currentPrice": 60_000.0,
                "volumeEur": 5_000_000.0,
                "action": "buy",
                "score": 8,
                "mtfAlign": 2,
                "changePct": 2.0,
                "change4hPct": 1.0,
                **_MICRO_OK,
            },
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 910.0
        portfolio["holdings"] = {}

        gemini_insights = {
            "top_picks": ["tLTCUSD"],
            "signals": {
                "tLTCUSD": {"action": "buy", "confidence": 8, "reason": "momentum"},
            },
        }

        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=910.0,
            label_fn=lambda sym: sym.replace("t", "").replace("USD", ""),
            gemini_insights=gemini_insights,
            regime="bull",
            regime_info={"regime": "bull", "phase": "bull"},
            learning={
                "entry_score_min": 1,
                "blocked_buys": [btc],
                "symbol_memory": {
                    btc: {
                        "blocked": False,
                        "chronic": False,
                        "cooldown_min": 0,
                        "score_adjust": -2.5,
                        "net_eur": -4.0,
                        "wins": 10,
                        "losses": 12,
                    }
                },
            },
        )

        allocation = result.get("initialAllocation") or []
        symbols = [slot["symbol"] for slot in allocation]
        self.assertIn(btc, symbols)
        self.assertTrue(result.get("idleEmptyDeploy"))

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_idle_empty_deploy_keeps_chronic_loser_blocked(self):
        """Krooninen häviäjä pysyy estettynä vaikka salkku tyhjä."""
        chronic = "tETHUSD"
        analyses = {
            chronic: {
                "currentPrice": 3_000.0,
                "volumeEur": 500_000.0,
                "action": "buy",
                "score": 9,
                "mtfAlign": 2,
                "changePct": 3.0,
                "change4hPct": 1.0,
                **_MICRO_OK,
            },
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 910.0
        portfolio["holdings"] = {}

        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=910.0,
            label_fn=lambda sym: sym.replace("t", "").replace("USD", ""),
            regime="bull",
            regime_info={"regime": "bull", "phase": "bull"},
            learning={
                "entry_score_min": 1,
                "blocked_buys": [chronic],
                "symbol_memory": {
                    chronic: {
                        "blocked": True,
                        "chronic": True,
                        "cooldown_min": 0,
                        "score_adjust": -4.0,
                        "net_eur": -8.0,
                        "wins": 0,
                        "losses": 4,
                    }
                },
            },
        )

        self.assertFalse(result.get("initialAllocation"))
        self.assertFalse(result.get("idleEmptyDeploy"))

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_no_idle_bypass_when_cash_share_low(self):
        """Alle idle-kynnyksen ei ohiteta Gemini-top pick -porttia."""
        blocked = "tBTCUSD"
        fallback = "tLTCUSD"
        analyses = {
            blocked: {
                "currentPrice": 60_000.0,
                "volumeEur": 5_000_000.0,
                "action": "buy",
                "score": 8,
                "mtfAlign": 2,
                "changePct": 2.0,
                **_MICRO_OK,
            },
            fallback: {
                "currentPrice": 80.0,
                "volumeEur": 500_000.0,
                "action": "buy",
                "score": 5,
                "mtfAlign": 2,
                "changePct": 2.5,
                **_MICRO_OK,
            },
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 120.0
        portfolio["holdings"] = {}

        gemini_insights = {
            "top_picks": [blocked],
            "signals": {
                blocked: {"action": "buy", "confidence": 8, "reason": "momentum"},
            },
        }

        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=1_000.0,
            label_fn=lambda sym: sym.replace("t", "").replace("USD", ""),
            gemini_insights=gemini_insights,
            regime="bull",
            regime_info={"regime": "bull", "phase": "bull"},
            learning={"entry_score_min": 1, "blocked_buys": [blocked]},
        )

        self.assertFalse(result.get("initialAllocation"))
        self.assertFalse(result.get("idleEmptyDeploy"))
