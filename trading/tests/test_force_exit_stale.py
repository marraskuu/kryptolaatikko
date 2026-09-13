"""Force-exit: non-major max hold + stale armed trailing."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import FORCE_EXIT_NON_MAJOR_HOURS, make_trading_decisions
from trading.services.portfolio import default_portfolio
from trading.services.sell_strategy import FORCE_EXIT_ARMED_STALE_HOURS, update_profit_sell

_MICRO_OK = {"microChecked": True, "microBlocked": False}


class ArmedStaleForceExitTests(SimpleTestCase):
    def test_armed_stale_forces_sell_without_pullback(self):
        watches = {
            "tBTCUSD": {
                "active": True,
                "peakPrice": 100.0,
                "peakTime": 1_000_000,
                "prevPrice": 100.0,
                "armed": True,
                "tier1Taken": True,
            }
        }
        stale_ms = int(FORCE_EXIT_ARMED_STALE_HOURS * 3600 * 1000) + 60_000
        result = update_profit_sell(
            watches,
            "tBTCUSD",
            current_price=100.0,
            avg_price=96.0,
            now_ms=1_000_000 + stale_ms,
            atr_pct=0.4,
        )
        self.assertTrue(result["shouldSell"])
        self.assertIn("vanhentunut", result["reason"])

    def test_fresh_armed_waits_for_pullback(self):
        watches = {
            "tBTCUSD": {
                "active": True,
                "peakPrice": 100.0,
                "peakTime": 1_000_000,
                "prevPrice": 100.0,
                "armed": True,
                "tier1Taken": True,
            }
        }
        result = update_profit_sell(
            watches,
            "tBTCUSD",
            current_price=100.0,
            avg_price=96.0,
            now_ms=1_000_000 + 60_000,
            atr_pct=0.4,
        )
        self.assertFalse(result["shouldSell"])


class NonMajorMaxHoldTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_alt_bond_force_sold_after_max_hold(self):
        sym = "tALT2612UST"
        opened = (datetime.now(timezone.utc) - timedelta(hours=FORCE_EXIT_NON_MAJOR_HOURS + 1)).isoformat()
        analyses = {
            sym: {
                "currentPrice": 97.0,
                "volumeEur": 300_000.0,
                "action": "hold",
                "score": 3,
                "mtfAlign": 0,
                "changePct": 0.1,
                "change4hPct": 0.0,
                "atrPct": 0.2,
                **_MICRO_OK,
            }
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 500.0
        portfolio["holdings"] = {
            sym: {"amount": 2.5, "avgPrice": 92.6, "openedAt": opened}
        }
        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=742.0,
            label_fn=lambda s: s,
            gemini_insights={},
            regime="bear",
            regime_info={"regime": "bear", "phase": "bear", "breadth_up_pct": 18.0},
            learning={"entry_score_min": 4, "blocked_buys": []},
        )
        sells = [d for d in result["decisions"] if d.get("type") == "sell" and d.get("symbol") == sym]
        self.assertTrue(sells)
        self.assertIn("Max-pito non-major", sells[0]["reason"])

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_major_not_force_sold_by_non_major_rule(self):
        sym = "tBTCUSD"
        opened = (datetime.now(timezone.utc) - timedelta(hours=FORCE_EXIT_NON_MAJOR_HOURS + 10)).isoformat()
        analyses = {
            sym: {
                "currentPrice": 70_000.0,
                "volumeEur": 5_000_000.0,
                "action": "hold",
                "score": 5,
                "mtfAlign": 0,
                "changePct": 0.5,
                "change4hPct": 0.2,
                "atrPct": 1.2,
                **_MICRO_OK,
            }
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 200.0
        portfolio["holdings"] = {
            sym: {"amount": 0.01, "avgPrice": 68_000.0, "openedAt": opened}
        }
        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=900.0,
            label_fn=lambda s: s,
            gemini_insights={},
            regime="bear",
            regime_info={"regime": "bear", "phase": "bear", "breadth_up_pct": 18.0},
            learning={"entry_score_min": 4, "blocked_buys": [], "rotation_enabled": False},
        )
        sells = [d for d in result["decisions"] if d.get("type") == "sell" and d.get("symbol") == sym]
        for s in sells:
            self.assertNotIn("Max-pito non-major", s.get("reason") or "")
