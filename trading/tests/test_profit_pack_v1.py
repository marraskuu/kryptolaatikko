"""Profit Pack v1: chase-katto, breadth, majors, setup-exit pois, R:R-voitonotto."""

from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import (
    MAX_ENTRY_CHANGE_24H_PCT,
    MIN_BREADTH_UP_PCT_FOR_BUY,
    SETUP_FAST_EXIT_ENABLED,
    _fast_loss_exit_reason,
    _is_buy_blocked,
    _is_buy_major,
    make_trading_decisions,
)
from trading.services.portfolio import default_portfolio
from trading.services import sell_strategy

_MICRO_OK = {"microChecked": True, "microBlocked": False}


def _btc_analysis(**extra):
    base = {
        "currentPrice": 60_000.0,
        "volumeEur": 5_000_000.0,
        "action": "buy",
        "score": 9,
        "mtfAlign": 1,
        "changePct": 2.0,
        "change4hPct": 1.0,
        "condAdjust": 0.0,
        **_MICRO_OK,
    }
    base.update(extra)
    return base


class MajorsAllowlistTests(SimpleTestCase):
    def test_btc_eth_are_majors(self):
        self.assertTrue(_is_buy_major("tBTCUSD"))
        self.assertTrue(_is_buy_major("tETHUST"))
        self.assertTrue(_is_buy_major("tSOLUSD"))

    def test_dated_alt_futures_rejected(self):
        self.assertFalse(_is_buy_major("tALT2612UST"))

    def test_random_alt_rejected(self):
        self.assertFalse(_is_buy_major("tDOGEUSD"))
        self.assertFalse(_is_buy_major("tZECUSD"))


class ChaseAndBreadthGateTests(SimpleTestCase):
    def test_chase_24h_blocks_buy(self):
        analysis = _btc_analysis(changePct=MAX_ENTRY_CHANGE_24H_PCT)
        blocked = _is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="bull",
            regime_info={"regime": "bull", "breadth_up_pct": 55.0},
        )
        self.assertTrue(blocked)

    def test_calm_entry_allowed(self):
        analysis = _btc_analysis(changePct=2.0)
        blocked = _is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="bull",
            regime_info={"regime": "bull", "breadth_up_pct": 55.0},
        )
        self.assertFalse(blocked)

    def test_low_breadth_blocks_buy(self):
        analysis = _btc_analysis(changePct=1.5)
        blocked = _is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="bull",
            regime_info={
                "regime": "bull",
                "breadth_up_pct": MIN_BREADTH_UP_PCT_FOR_BUY - 1.0,
            },
        )
        self.assertTrue(blocked)


class SetupFastExitTests(SimpleTestCase):
    def test_setup_fast_exit_disabled_by_default(self):
        self.assertFalse(SETUP_FAST_EXIT_ENABLED)
        reason = _fast_loss_exit_reason(
            "tBTCUSD",
            -1.6,
            _btc_analysis(condBlocked=True),
            "bull",
            {},
            blocked_setups=set(),
        )
        self.assertIsNone(reason)

    def test_chronic_loser_still_exits(self):
        reason = _fast_loss_exit_reason(
            "tBTCUSD",
            -1.6,
            _btc_analysis(),
            "bull",
            {"tBTCUSD": {"chronic": True, "net_eur": -10}},
            blocked_setups=set(),
        )
        self.assertIsNotNone(reason)
        self.assertIn("Krooninen", reason or "")


class ProfitTakeRRTests(SimpleTestCase):
    def test_partial_take_is_later_and_smaller(self):
        self.assertGreaterEqual(sell_strategy.PARTIAL_TAKE_TRIGGER_PCT, 3.0)
        self.assertLessEqual(sell_strategy.PARTIAL_TAKE_FRACTION, 0.25)
        self.assertGreaterEqual(sell_strategy.PROFIT_TRIGGER_FLOOR_PCT, 1.5)


class EmptyBookRespectsProfitPackTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_no_allocation_to_non_major(self):
        sym = "tDOGEUSD"
        analyses = {
            sym: {
                "currentPrice": 0.12,
                "volumeEur": 2_000_000.0,
                "action": "buy",
                "score": 12,
                "mtfAlign": 1,
                "changePct": 2.0,
                "change4hPct": 1.0,
                **_MICRO_OK,
            }
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
                "top_picks": [sym],
                "signals": {sym: {"action": "buy", "confidence": 8, "reason": "pump"}},
            },
            regime="bull",
            regime_info={"regime": "bull", "phase": "bull", "breadth_up_pct": 60.0},
            learning={"entry_score_min": 1, "blocked_buys": []},
        )
        self.assertFalse(result.get("initialAllocation"))
