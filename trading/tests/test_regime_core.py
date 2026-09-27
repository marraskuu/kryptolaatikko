"""Regime Core: idle vs Gemini zombie, major PT widen, no winner rotation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import (
    IDLE_EMPTY_ENTRY_SCORE_CAP,
    MAX_ENTRY_CHANGE_24H_MAJOR_BULL_PCT,
    _gemini_buyable_pick_count,
    _is_buy_major,
    _recently_lost_symbols,
    make_trading_decisions,
)
from trading.services.portfolio import default_portfolio
from trading.services import sell_strategy
from trading.services.sell_strategy import (
    MAJOR_PARTIAL_TAKE_TRIGGER_PCT,
    MAJOR_PROFIT_TRIGGER_FLOOR_PCT,
    MAJOR_PULLBACK_FLOOR_PCT,
    update_profit_sell,
)

_MICRO_OK = {"microChecked": True, "microBlocked": False}


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _major_analysis(sym: str = "tBTCUSD", **extra):
    base = {
        "currentPrice": 70_000.0 if "BTC" in sym else 100.0,
        "volumeEur": 5_000_000.0,
        "action": "buy",
        "score": 5,
        "mtfAlign": 1,
        "changePct": 2.0,
        "change4hPct": 0.5,
        "condAdjust": 0.0,
        **_MICRO_OK,
    }
    base.update(extra)
    return base


class GeminiBuyablePickCountTests(SimpleTestCase):
    def test_hold_picks_count_as_zero_buyable(self):
        insights = {
            "top_picks": ["tSOLUSD", "tXRPUSD"],
            "signals": {
                "tSOLUSD": {"action": "hold", "confidence": 8, "reason": "Voitolla oleva positio"},
                "tXRPUSD": {"action": "hold", "confidence": 7, "reason": "pidä"},
            },
        }
        self.assertEqual(_gemini_buyable_pick_count(insights), 0)

    def test_buy_pick_counts(self):
        insights = {
            "top_picks": ["tBTCUSD"],
            "signals": {
                "tBTCUSD": {"action": "buy", "confidence": 8, "reason": "trend"},
            },
        }
        self.assertEqual(_gemini_buyable_pick_count(insights), 1)


class IdleEmptyVsZombieGeminiTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_idle_deploys_despite_gemini_hold_top_picks(self):
        """Zombie hold top_picks must not lock empty-book idle deploy."""
        sym = "tETHUSD"
        analyses = {
            sym: _major_analysis(sym, currentPrice=2500.0, score=5, changePct=1.5),
            "tBTCUSD": _major_analysis("tBTCUSD", score=3, changePct=0.5),
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 900.0
        portfolio["holdings"] = {}
        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=900.0,
            label_fn=lambda s: s,
            gemini_insights={
                "top_picks": ["tSOLUSD"],
                "signals": {
                    "tSOLUSD": {
                        "action": "hold",
                        "confidence": 8,
                        "reason": "Voitolla oleva positio (+0.1 %)",
                    },
                },
            },
            regime="bull",
            regime_info={
                "regime": "bull",
                "phase": "bull",
                "breadth_up_pct": 70.0,
                "btc_trend_pct": 4.0,
                "btc_trend_blocks_buy": False,
            },
            learning={
                "entry_score_min": 4,
                "blocked_buys": [],
                "rotation_enabled": False,
            },
        )
        self.assertTrue(result.get("initialAllocation") or result.get("idleEmptyDeploy"))
        self.assertTrue(result.get("idleEmptyDeploy"))
        alloc_syms = [a["symbol"] for a in (result.get("initialAllocation") or [])]
        self.assertTrue(alloc_syms)
        self.assertLessEqual(IDLE_EMPTY_ENTRY_SCORE_CAP, 3)


class RebuyAfterProfitTakeTests(SimpleTestCase):
    def test_profit_take_sell_blocks_rebuy(self):
        portfolio = default_portfolio()
        portfolio["trades"] = [
            {
                "type": "sell",
                "symbol": "tSOLUSD",
                "timestamp": _iso(60),
                "profitLoss": 7.8,
                "reason": "Voitto +3.9 % — nousu tasaantui, trailing-stop -0.95 % huipusta",
            }
        ]
        self.assertIn("tSOLUSD", _recently_lost_symbols(portfolio))


class MajorProfitTakeWidenTests(SimpleTestCase):
    def test_major_needs_larger_pullback_than_non_major(self):
        watches: dict = {}
        # Arm at major floor (~4.5%), small dip 0.5% from peak must NOT sell
        avg = 100.0
        peak = 105.0
        now = 1_000_000
        watches["tBTCUSD"] = {
            "active": True,
            "peakPrice": peak,
            "peakTime": now - 200_000,
            "prevPrice": peak,
            "armed": True,
            "tier1Taken": True,
        }
        result = update_profit_sell(
            watches,
            "tBTCUSD",
            current_price=peak * 0.995,  # −0.5% from peak, still +4.5% vs cost roughly
            avg_price=avg,
            now_ms=now,
            atr_pct=0.8,
            is_major=True,
            defense_regime="bull",
        )
        self.assertFalse(result["shouldSell"])
        self.assertGreaterEqual(MAJOR_PULLBACK_FLOOR_PCT, 1.5)
        self.assertGreaterEqual(MAJOR_PROFIT_TRIGGER_FLOOR_PCT, 4.0)

    def test_non_major_stale_armed_still_exits(self):
        watches = {
            "tALT2612UST": {
                "active": True,
                "peakPrice": 100.0,
                "peakTime": 1_000_000,
                "prevPrice": 100.0,
                "armed": True,
                "tier1Taken": True,
            }
        }
        stale_ms = int(sell_strategy.FORCE_EXIT_ARMED_STALE_HOURS * 3600 * 1000) + 60_000
        result = update_profit_sell(
            watches,
            "tALT2612UST",
            current_price=100.0,
            avg_price=96.0,
            now_ms=1_000_000 + stale_ms,
            atr_pct=0.3,
            is_major=False,
            defense_regime="bull",
        )
        self.assertTrue(result["shouldSell"])

    def test_major_stale_armed_does_not_force_exit(self):
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
        stale_ms = int(8 * 3600 * 1000) + 60_000
        result = update_profit_sell(
            watches,
            "tBTCUSD",
            current_price=105.0,
            avg_price=100.0,
            now_ms=1_000_000 + stale_ms,
            atr_pct=0.8,
            is_major=True,
            defense_regime="bull",
        )
        self.assertFalse(result["shouldSell"])

    def test_major_partial_is_later(self):
        self.assertGreaterEqual(MAX_ENTRY_CHANGE_24H_MAJOR_BULL_PCT, 6.0)
        self.assertGreaterEqual(MAJOR_PARTIAL_TAKE_TRIGGER_PCT, 8.0)


class NoMajorWinnerRotationTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_concentration_skips_major_in_profit(self):
        sym = "tSOLUSD"
        opened = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        analyses = {
            sym: _major_analysis(
                sym,
                currentPrice=110.0,
                score=4,
                changePct=1.0,
                mtfAlign=1,
            ),
            "tBTCUSD": _major_analysis("tBTCUSD", score=12, changePct=3.0, mtfAlign=1),
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 100.0
        portfolio["holdings"] = {
            sym: {"amount": 2.0, "avgPrice": 100.0, "openedAt": opened}
        }
        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=320.0,
            label_fn=lambda s: s.replace("t", "").replace("USD", ""),
            gemini_insights={
                "top_picks": ["tBTCUSD"],
                "signals": {
                    "tBTCUSD": {"action": "buy", "confidence": 9, "reason": "strong"},
                },
            },
            regime="bull",
            regime_info={
                "regime": "bull",
                "phase": "bull",
                "breadth_up_pct": 60.0,
                "btc_trend_pct": 5.0,
                "btc_trend_blocks_buy": False,
            },
            learning={
                "entry_score_min": 1,
                "blocked_buys": [],
                "rotation_enabled": True,
            },
        )
        sells = [
            d
            for d in result.get("decisions") or []
            if d.get("type") == "sell" and d.get("symbol") == sym
        ]
        for s in sells:
            reason = s.get("reason") or ""
            self.assertNotIn("Keskittymistila", reason)
            self.assertNotIn("ei valinnoissa", reason)


class MajorHelpersTests(SimpleTestCase):
    def test_btc_is_major(self):
        self.assertTrue(_is_buy_major("tBTCUSD"))
        self.assertFalse(_is_buy_major("tALT2612UST"))
