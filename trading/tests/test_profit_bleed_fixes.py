"""Vuodon-stop: karhuostosulku, kokokatto, Gemini cash/micro-esto."""

from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import (
    MAX_SINGLE_BUY_PORTFOLIO_PCT,
    _bear_buy_freeze_active,
    _cap_buy_eur,
    _gemini_buy_allowed,
    _gemini_prefers_cash,
    _is_buy_blocked,
    _plan_initial_allocation,
    make_trading_decisions,
)
from trading.services.portfolio import default_portfolio

_MICRO_OK = {"microChecked": True, "microBlocked": False}


class BearFreezeOfficialRegimeTests(SimpleTestCase):
    def test_freeze_when_official_bear_even_if_bull_emerging(self):
        info = {
            "regime": "bear",
            "phase": "bull_emerging",
            "shift_to": "bull",
            "shift_strength": "moderate",
        }
        self.assertTrue(_bear_buy_freeze_active(info))

    def test_no_freeze_in_clean_bull(self):
        info = {"regime": "bull", "phase": "bull"}
        self.assertFalse(_bear_buy_freeze_active(info))

    def test_buy_blocked_via_regime_info_despite_entry_bull_string(self):
        analysis = {
            "currentPrice": 60_000.0,
            "volumeEur": 5_000_000.0,
            "action": "buy",
            "score": 8,
            "mtfAlign": 1,
            "changePct": 1.0,
            "condAdjust": 0.0,
            **_MICRO_OK,
        }
        blocked = _is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="bull",  # anticipated
            regime_info={"regime": "bear", "phase": "bear"},
        )
        self.assertTrue(blocked)


class SizeCapTests(SimpleTestCase):
    def test_cap_buy_eur_limits_to_portfolio_pct(self):
        capped = _cap_buy_eur(900.0, portfolio_value=900.0)
        self.assertAlmostEqual(capped, 900.0 * MAX_SINGLE_BUY_PORTFOLIO_PCT, places=2)

    def test_cap_accounts_for_existing_position(self):
        capped = _cap_buy_eur(
            500.0,
            portfolio_value=1000.0,
            current_position_eur=250.0,
        )
        self.assertAlmostEqual(capped, 50.0, places=2)

    def test_initial_allocation_does_not_deploy_all_cash(self):
        picks = [
            {
                "symbol": "tBTCUSD",
                "analysis": {
                    "currentPrice": 60_000.0,
                    "volumeEur": 5_000_000.0,
                    "score": 8,
                    **_MICRO_OK,
                },
                "rank": 8,
            }
        ]
        planned = _plan_initial_allocation(
            picks,
            cash=900.0,
            gemini_insights=None,
            gemini_active=False,
            analyses={picks[0]["symbol"]: picks[0]["analysis"]},
        )
        self.assertEqual(len(planned), 1)
        self.assertLessEqual(planned[0]["eurAmount"], 900.0 * MAX_SINGLE_BUY_PORTFOLIO_PCT + 0.01)
        self.assertGreater(planned[0]["eurAmount"], 100.0)


class GeminiCashMicroGateTests(SimpleTestCase):
    def test_prefers_cash_from_hold_action(self):
        self.assertTrue(_gemini_prefers_cash({"action": "hold", "confidence": 8}))

    def test_prefers_cash_from_reason_text(self):
        self.assertTrue(
            _gemini_prefers_cash(
                {
                    "action": "buy",
                    "confidence": 5,
                    "reason": "Mikrorakenne estetty (micro_blocked=true). Käteisen pitäminen on ensisijaista.",
                }
            )
        )

    def test_gemini_buy_allowed_rejects_cash_first_signal(self):
        insights = {
            "top_picks": ["tBTCUSD"],
            "signals": {
                "tBTCUSD": {
                    "action": "buy",
                    "confidence": 8,
                    "reason": "Holding cash is primary to protect capital.",
                }
            },
        }
        ok = _gemini_buy_allowed(
            "tBTCUSD",
            {"currentPrice": 1.0},
            insights,
            gemini_active=True,
            gemini_buy_min_confidence=7,
        )
        self.assertFalse(ok)

    def test_micro_blocked_field_blocks_even_if_micro_module_off(self):
        analysis = {
            "currentPrice": 60_000.0,
            "volumeEur": 5_000_000.0,
            "action": "buy",
            "score": 9,
            "mtfAlign": 1,
            "changePct": 2.0,
            "microChecked": True,
            "microBlocked": True,
        }
        with patch("trading.services.market_microstructure.ENABLED", False):
            blocked = _is_buy_blocked(
                "tBTCUSD",
                analysis,
                blocked_buys=set(),
                blocked_setups=set(),
                regime="bull",
            )
        self.assertTrue(blocked)


class GeminiPartialAllocationTests(SimpleTestCase):
    def _analysis(self, price: float, score: int = 8) -> dict:
        return {
            "currentPrice": price,
            "volumeEur": 5_000_000.0,
            "action": "buy",
            "score": score,
            "mtfAlign": 2,
            "changePct": 2.0,
            "change4hPct": 1.0,
            **_MICRO_OK,
        }

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_initial_allocation_preserves_gemini_cash_reserve(self):
        symbols = ["tBTCUSD", "tETHUSD", "tSOLUSD"]
        analyses = {
            "tBTCUSD": self._analysis(60_000.0, 9),
            "tETHUSD": self._analysis(3_000.0, 8),
            "tSOLUSD": self._analysis(150.0, 7),
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 1000.0
        portfolio["holdings"] = {}
        gemini_insights = {
            "top_picks": symbols,
            "allocations": {
                "tBTCUSD": 20,
                "tETHUSD": 20,
                "tSOLUSD": 10,
            },
            "signals": {
                sym: {"action": "buy", "confidence": 8, "reason": "strong setup"}
                for sym in symbols
            },
        }

        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=1000.0,
            label_fn=lambda s: s,
            gemini_insights=gemini_insights,
            regime="bull",
            regime_info={"regime": "bull", "phase": "bull"},
            learning={"entry_score_min": 1, "blocked_buys": []},
        )

        allocation = result.get("initialAllocation") or []
        self.assertEqual(len(allocation), 3)
        self.assertAlmostEqual(
            sum(slot["eurAmount"] for slot in allocation),
            500.0,
            places=2,
        )

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_cash_deploy_does_not_spend_unallocated_gemini_cash(self):
        analyses = {
            "tBTCUSD": self._analysis(60_000.0, 9),
            "tETHUSD": self._analysis(1_000.0, 8),
            "tSOLUSD": self._analysis(150.0, 7),
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 900.0
        portfolio["holdings"] = {
            "tETHUSD": {"amount": 0.1, "avgPrice": 1_000.0},
        }
        gemini_insights = {
            "top_picks": ["tBTCUSD", "tETHUSD", "tSOLUSD"],
            "allocations": {
                "tBTCUSD": 20,
                "tETHUSD": 10,
                "tSOLUSD": 20,
            },
            "signals": {
                sym: {"action": "buy", "confidence": 8, "reason": "strong setup"}
                for sym in ("tBTCUSD", "tETHUSD", "tSOLUSD")
            },
        }

        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=1000.0,
            label_fn=lambda s: s,
            gemini_insights=gemini_insights,
            regime="bull",
            regime_info={"regime": "bull", "phase": "bull"},
            learning={"entry_score_min": 1, "blocked_buys": []},
        )

        buys = [d for d in result["decisions"] if d["type"] == "buy"]
        self.assertEqual({d["symbol"] for d in buys}, {"tBTCUSD", "tSOLUSD"})
        self.assertAlmostEqual(sum(d["eurAmount"] for d in buys), 400.0, places=2)


class EmptyBookBearNoDeployTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_official_bear_blocks_empty_gemini_buy(self):
        pick = "tBTCUSD"
        analyses = {
            pick: {
                "currentPrice": 60_000.0,
                "volumeEur": 5_000_000.0,
                "action": "buy",
                "score": 9,
                "mtfAlign": 1,
                "changePct": 2.0,
                "change4hPct": 1.0,
                "condAdjust": 0.5,
                **_MICRO_OK,
            }
        }
        portfolio = default_portfolio()
        portfolio["cash"] = 910.0
        portfolio["holdings"] = {}
        gemini_insights = {
            "top_picks": [pick],
            "signals": {
                pick: {"action": "buy", "confidence": 8, "reason": "momentum"},
            },
        }
        result = make_trading_decisions(
            analyses,
            portfolio,
            total_value=910.0,
            label_fn=lambda s: s,
            gemini_insights=gemini_insights,
            regime="bull",
            regime_info={
                "regime": "bear",
                "phase": "bull_emerging",
                "shift_to": "bull",
                "shift_strength": "moderate",
            },
            learning={"entry_score_min": 1, "blocked_buys": []},
        )
        self.assertFalse(result.get("initialAllocation"))
