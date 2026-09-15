"""Force-exit: non-major max hold + stale armed trailing."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services import engine
from trading.services.ai_trader import FORCE_EXIT_NON_MAJOR_HOURS, make_trading_decisions
from trading.services.portfolio import default_portfolio
from trading.services.sell_strategy import FORCE_EXIT_ARMED_STALE_HOURS, update_profit_sell
from trading.services.session_state import default_state

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


class GenericSellWatchCleanupTests(SimpleTestCase):
    def test_full_decision_sell_clears_stale_profit_watch_state(self):
        sym = "tXMRUSD"
        state = default_state()
        state["portfolio"] = default_portfolio()
        state["portfolio"]["cash"] = 0.0
        state["portfolio"]["holdings"] = {
            sym: {
                "amount": 1.0,
                "avgPrice": 95.0,
                "openedAt": datetime.now(timezone.utc).isoformat(),
            }
        }
        state["tickers"] = {
            sym: {"symbol": sym, "last": 100.0, "changePct": 0.5, "volumeEur": 1_000_000.0}
        }
        analysis = {
            "currentPrice": 100.0,
            "volumeEur": 1_000_000.0,
            "action": "hold",
            "score": 3,
            "mtfAlign": 0,
            "changePct": 0.5,
            "change4hPct": 0.0,
            "atrPct": 0.5,
            **_MICRO_OK,
        }
        state["analyses"] = {sym: analysis}
        state["watches"] = {
            sym: {
                "active": True,
                "peakPrice": 120.0,
                "peakTime": 1_000_000,
                "prevPrice": 120.0,
                "armed": True,
                "tier1Taken": True,
            }
        }
        state["profitWatch"] = {sym: {"status": "armed", "statusText": "old watch"}}
        state["watchLogKeys"] = {sym: "old-key"}
        saved: dict[str, dict] = {}
        decisions = [
            {
                "type": "sell",
                "symbol": sym,
                "amount": 1.0,
                "eurAmount": 100.0,
                "reason": "Max-pito non-major ≥24 h (25 h, +5.3 %) — vapautetaan pääomaa",
                "analysis": analysis,
            }
        ]

        def capture_save(updated: dict) -> None:
            saved["state"] = updated

        patchers = [
            patch.object(engine, "load_state", return_value=state),
            patch.object(engine, "save_state", side_effect=capture_save),
            patch.object(engine, "_refresh_analyses", return_value=None),
            patch.object(engine, "_enrich_holdings", return_value=None),
            patch.object(engine, "enrich_display_timeframes", return_value=None),
            patch("trading.services.entry_structure.enrich_mtf_entry_signals", return_value={}),
            patch.object(engine, "compute_market_regime", return_value={"regime": "bear"}),
            patch.object(engine, "enrich_regime_phase", side_effect=lambda info, *_: info),
            patch("trading.services.btc_trend_gate.refresh_btc_trend", return_value={}),
            patch(
                "trading.services.btc_trend_gate.attach_btc_trend_to_regime",
                side_effect=lambda info, _trend: info,
            ),
            patch("trading.services.regime_anticipation_learning.record_regime_snapshot", return_value=None),
            patch.object(engine, "compute_tuning", return_value={"entry_score_min": 4}),
            patch.object(engine.market_microstructure, "enrich_analyses", return_value={}),
            patch.object(engine.market_learning, "step", return_value=({}, {})),
            patch.object(engine.market_learning, "apply", return_value=None),
            patch("trading.services.market_learning_backfill.get_backfill_status", return_value={}),
            patch("trading.services.market_learning_backfill.maybe_schedule_historical_backfill", return_value=None),
            patch.object(engine.exit_learning, "step", return_value={}),
            patch.object(engine.exit_learning, "get_summary", return_value={}),
            patch.object(engine, "gemini_configured", return_value=False),
            patch.object(
                engine,
                "record_cycle",
                return_value={"dailyStopActive": False, "profitLockTier": "none"},
            ),
            patch.object(engine, "_check_profit_sells", return_value=[]),
            patch.object(
                engine,
                "make_trading_decisions",
                return_value={"decisions": decisions, "topSymbols": []},
            ),
            patch.object(engine, "_log_shadow_trade", return_value=None),
        ]
        with ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            engine.execute_trading_cycle()

        final = saved["state"]
        self.assertNotIn(sym, final["portfolio"]["holdings"])
        self.assertNotIn(sym, final["watches"])
        self.assertNotIn(sym, final["profitWatch"])
        self.assertNotIn(sym, final["watchLogKeys"])
