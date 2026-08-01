"""Estä välitön takaisinosto tuoreen tappiollisen myynnin kohteeseen.

Regressio: Keskittymistila ja tyhjän salkun idle-cash-pudotus ohittivat aiemmin
30 min churn-cooldownin kokonaan, mikä salli saman symbolin oston/myynnin
useita kertoja tunneissa täydellä salkkukoolla (esim. tXMRUST 5x <24 h:ssa,
2026-07-30/31, netto selvästi negatiivinen).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import (
    SYMBOL_REBUY_COOLDOWN_SEC,
    _recently_lost_symbols,
    make_trading_decisions,
)
from trading.services.portfolio import default_portfolio

_MICRO_OK = {"microChecked": True, "microBlocked": False}


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class RecentlyLostSymbolsTests(SimpleTestCase):
    def test_recent_losing_sell_is_blocked(self):
        portfolio = default_portfolio()
        portfolio["trades"] = [
            {
                "type": "sell",
                "symbol": "tXMRUST",
                "timestamp": _iso(60),
                "profitLoss": -10.5,
            }
        ]
        self.assertIn("tXMRUST", _recently_lost_symbols(portfolio))

    def test_recent_profitable_sell_is_not_blocked(self):
        portfolio = default_portfolio()
        portfolio["trades"] = [
            {
                "type": "sell",
                "symbol": "tXMRUST",
                "timestamp": _iso(60),
                "profitLoss": 8.4,
            }
        ]
        self.assertEqual(_recently_lost_symbols(portfolio), set())

    def test_losing_sell_outside_cooldown_window_is_not_blocked(self):
        portfolio = default_portfolio()
        portfolio["trades"] = [
            {
                "type": "sell",
                "symbol": "tXMRUST",
                "timestamp": _iso(SYMBOL_REBUY_COOLDOWN_SEC + 60),
                "profitLoss": -10.5,
            }
        ]
        self.assertEqual(_recently_lost_symbols(portfolio), set())

    def test_buys_are_ignored(self):
        portfolio = default_portfolio()
        portfolio["trades"] = [
            {"type": "buy", "symbol": "tXMRUST", "timestamp": _iso(60)},
        ]
        self.assertEqual(_recently_lost_symbols(portfolio), set())


class SymbolRebuyCooldownIntegrationTests(SimpleTestCase):
    """Sama esto tyhjän salkun idle-cash-deployn läpi (aiemmin hard_blocked_buys-
    joukko, joka ei sisältänyt tuoreita tappiollisia myyntejä)."""

    def _analyses(self, symbol: str) -> dict:
        return {
            symbol: {
                "currentPrice": 300.0,
                "volumeEur": 5_000_000.0,
                "action": "buy",
                "score": 9,
                "mtfAlign": 2,
                "changePct": 3.0,
                "change4hPct": 2.0,
                **_MICRO_OK,
            },
        }

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_idle_empty_deploy_blocks_just_stopped_out_symbol(self):
        symbol = "tXMRUST"
        portfolio = default_portfolio()
        portfolio["cash"] = 910.0
        portfolio["holdings"] = {}
        portfolio["trades"] = [
            {
                "type": "sell",
                "symbol": symbol,
                "timestamp": _iso(300),
                "profitLoss": -10.5,
                "reason": "Stop-loss -1.5 %",
            }
        ]

        result = make_trading_decisions(
            self._analyses(symbol),
            portfolio,
            total_value=910.0,
            label_fn=lambda sym: sym.replace("t", "").replace("USD", ""),
            regime="neutral",
            regime_info={"regime": "neutral", "phase": "neutral"},
            learning={"entry_score_min": 1},
        )

        allocation = result.get("initialAllocation") or []
        symbols = [slot["symbol"] for slot in allocation]
        self.assertNotIn(symbol, symbols)

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_idle_empty_deploy_allows_symbol_after_profitable_exit(self):
        symbol = "tXMRUST"
        portfolio = default_portfolio()
        portfolio["cash"] = 910.0
        portfolio["holdings"] = {}
        portfolio["trades"] = [
            {
                "type": "sell",
                "symbol": symbol,
                "timestamp": _iso(180),
                "profitLoss": 8.4,
                "reason": "Voitto +1.2 % — nousu tasaantui, trailing",
            }
        ]

        result = make_trading_decisions(
            self._analyses(symbol),
            portfolio,
            total_value=910.0,
            label_fn=lambda sym: sym.replace("t", "").replace("USD", ""),
            regime="neutral",
            regime_info={"regime": "neutral", "phase": "neutral"},
            learning={"entry_score_min": 1},
        )

        allocation = result.get("initialAllocation") or []
        symbols = [slot["symbol"] for slot in allocation]
        self.assertIn(symbol, symbols)
