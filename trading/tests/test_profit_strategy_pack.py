"""Karhu-jäädytys, rotaation hillintä, symbolimuisti nettopositiivisille."""

from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import (
    _deploy_cash_to_targets,
    _entry_ok,
    make_trading_decisions,
)
from trading.services.learning import compute_tuning
from trading.services.portfolio import default_portfolio

_MICRO_OK = {"microChecked": True, "microBlocked": False}


def _buy_analysis(**overrides):
    base = {
        "currentPrice": 100.0,
        "volumeEur": 500_000.0,
        "action": "buy",
        "score": 5,
        "mtfAlign": 2,
        "changePct": 2.0,
        "change4hPct": 1.0,
        **_MICRO_OK,
    }
    base.update(overrides)
    return base


class BearBuyFreezeTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    @patch("trading.services.ai_trader.BEAR_BUY_FREEZE", True)
    def test_entry_ok_rejects_bear(self):
        analysis = _buy_analysis()
        self.assertFalse(_entry_ok(analysis, "bear"))
        self.assertTrue(_entry_ok(analysis, "bull"))

    @patch("trading.services.market_microstructure.ENABLED", False)
    @patch("trading.services.ai_trader.BEAR_BUY_FREEZE", True)
    def test_no_initial_allocation_in_bear(self):
        symbol = "tETHUSD"
        analyses = {symbol: _buy_analysis(currentPrice=3_000.0)}
        portfolio = default_portfolio()
        portfolio["cash"] = 900.0
        portfolio["holdings"] = {}

        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=900.0,
            label_fn=lambda s: "ETH",
            regime="bear",
            regime_info={"regime": "bear", "phase": "bear"},
            learning={"entry_score_min": 1},
        )
        self.assertFalse(result.get("initialAllocation"))


class RotationOutOfPicksTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_ei_valinnoissa_skips_losing_position(self):
        """Tappiolla olevaa positiota ei trimata 'ei valinnoissa' -syystä."""
        held = "tBTCUSD"
        target = "tETHUSD"
        analyses = {
            held: _buy_analysis(
                currentPrice=50_000.0,
                changePct=-0.5,
                change4hPct=-0.2,
                mtfAlign=0,
                score=2,
            ),
            target: _buy_analysis(currentPrice=3_000.0, score=8, mtfAlign=2),
        }
        holdings = {
            held: {"amount": 0.01, "avgPrice": 52_000.0},
        }
        decisions: list = []
        _deploy_cash_to_targets(
            decisions,
            holdings,
            cash=50.0,
            total_value=550.0,
            weights={target: 1.0},
            target_symbols=[target],
            analyses=analyses,
            label_fn=lambda s: s.replace("t", "").replace("USD", ""),
            gemini_active=False,
            skip_sell_symbols=set(),
            blocked_buys=set(),
            best_target_edge=3.0,
            regime="bull",
        )
        sells = [d for d in decisions if d.get("type") == "sell" and d.get("symbol") == held]
        self.assertFalse(sells)

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_ei_valinnoissa_trims_when_profitable(self):
        held = "tBTCUSD"
        target = "tETHUSD"
        analyses = {
            held: _buy_analysis(
                currentPrice=55_000.0,
                changePct=0.2,
                change4hPct=0.1,
                mtfAlign=0,
                score=2,
            ),
            target: _buy_analysis(
                currentPrice=3_000.0,
                score=8,
                mtfAlign=2,
                change4hPct=4.0,
                change1hPct=1.0,
            ),
        }
        holdings = {
            held: {"amount": 0.01, "avgPrice": 50_000.0},
        }
        decisions: list = []
        _deploy_cash_to_targets(
            decisions,
            holdings,
            cash=50.0,
            total_value=600.0,
            weights={target: 1.0},
            target_symbols=[target],
            analyses=analyses,
            label_fn=lambda s: s.replace("t", "").replace("USD", ""),
            gemini_active=False,
            skip_sell_symbols=set(),
            blocked_buys=set(),
            best_target_edge=5.0,
            regime="bull",
        )
        sells = [d for d in decisions if d.get("type") == "sell" and d.get("symbol") == held]
        self.assertTrue(sells)
        self.assertIn("ei valinnoissa", sells[0]["reason"])


class SymbolMemoryNetPositiveTests(SimpleTestCase):
    def test_net_positive_cooldown_not_in_blocked_buys(self):
        """Tuore tappio + historiallinen nettoplussa → ei blocked_buys."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        portfolio = default_portfolio()
        portfolio["trades"] = [
            {
                "type": "sell",
                "symbol": "tETHUSD",
                "profitLoss": -0.5,
                "timestamp": (now - timedelta(minutes=10)).isoformat(),
            },
            {
                "type": "sell",
                "symbol": "tETHUSD",
                "profitLoss": 8.0,
                "timestamp": (now - timedelta(days=2)).isoformat(),
            },
            {
                "type": "sell",
                "symbol": "tETHUSD",
                "profitLoss": 7.0,
                "timestamp": (now - timedelta(days=3)).isoformat(),
            },
        ]
        with (
            patch(
                "trading.services.setup_historical_backfill.load_setup_stats",
                return_value={},
            ),
            patch(
                "trading.services.setup_historical_backfill.get_setup_backfill_status",
                return_value={},
            ),
        ):
            tuning = compute_tuning(portfolio)
        mem = tuning.get("symbol_memory") or {}
        eth = mem.get("tETHUSD") or {}
        self.assertGreater(float(eth.get("net_eur") or 0), 0)
        self.assertTrue(eth.get("blocked"))  # cooldown aktiivinen muistissa
        blocked = set(tuning.get("blocked_buys") or [])
        self.assertNotIn("tETHUSD", blocked)

    def test_net_negative_score_block_still_applies(self):
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        portfolio = default_portfolio()
        portfolio["trades"] = [
            {
                "type": "sell",
                "symbol": "tHYPEUST",
                "profitLoss": -8.0,
                "timestamp": old,
            },
            {
                "type": "sell",
                "symbol": "tHYPEUST",
                "profitLoss": -7.0,
                "timestamp": old,
            },
            {
                "type": "sell",
                "symbol": "tHYPEUST",
                "profitLoss": -5.0,
                "timestamp": old,
            },
        ]
        with (
            patch(
                "trading.services.setup_historical_backfill.load_setup_stats",
                return_value={},
            ),
            patch(
                "trading.services.setup_historical_backfill.get_setup_backfill_status",
                return_value={},
            ),
        ):
            tuning = compute_tuning(portfolio)
        blocked = set(tuning.get("blocked_buys") or [])
        self.assertIn("tHYPEUST", blocked)
