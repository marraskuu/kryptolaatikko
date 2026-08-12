"""Päivästopin/voittolukon kytkentä oikeisiin kauppoihin (2026-07-29).

Varjopolitiikka (daily_policy_shadow.py) mittasi 40 päivän ajan, että
harkinnanvaraisten myyntien (rotaatio, karhu-kassatrimmaus, "other") esto
päivästopin tai kiinteän voittolukon aikana olisi tuottanut ~+165 €/40pv.
_apply_daily_policy_sell_gate on tämän kytkentä livekauppoihin — pakolliset
myynnit (stop-loss, aikastoppi, voitto-otto) eivät ole harkinnanvaraisia ja
kulkevat läpi normaalisti."""

from __future__ import annotations

from django.test import SimpleTestCase

from trading.services.engine import _apply_daily_policy_sell_gate


def _sell(symbol: str, reason: str) -> dict:
    return {"type": "sell", "symbol": symbol, "amount": 1.0, "eurAmount": 100.0, "reason": reason}


def _buy(symbol: str) -> dict:
    return {"type": "buy", "symbol": symbol, "amount": 1.0, "eurAmount": 100.0, "reason": "osto"}


class DailyPolicySellGateTests(SimpleTestCase):
    def test_no_shadow_flags_leaves_decisions_untouched(self):
        decisions = [_sell("tBTCUSD", "Karhu-kassavara — trimmaus")]
        result = _apply_daily_policy_sell_gate(decisions, None)
        self.assertEqual(result, decisions)

    def test_not_blocked_leaves_decisions_untouched(self):
        decisions = [_sell("tBTCUSD", "Karhu-kassavara — trimmaus")]
        flags = {"dailyStopActive": False, "profitLockTier": "soft"}
        result = _apply_daily_policy_sell_gate(decisions, flags)
        self.assertEqual(result, decisions)

    def test_daily_stop_blocks_discretionary_sell(self):
        decisions = [_sell("tBTCUSD", "Karhu-kassavara — ETH trimmaus 50 € kohti 25 % käteistä")]
        flags = {"dailyStopActive": True, "profitLockTier": "none"}
        result = _apply_daily_policy_sell_gate(decisions, flags)
        self.assertEqual(result, [])

    def test_profit_lock_firm_blocks_discretionary_sell(self):
        decisions = [_sell("tBTCUSD", "Rotaatio — parempi kohde saatavilla")]
        flags = {"dailyStopActive": False, "profitLockTier": "firm"}
        result = _apply_daily_policy_sell_gate(decisions, flags)
        self.assertEqual(result, [])

    def test_daily_stop_does_not_block_stop_loss_sell(self):
        decisions = [_sell("tBTCUSD", "Stop-loss -1.2 % (ATR-raja -1.1 %, bear-regiimi)")]
        flags = {"dailyStopActive": True, "profitLockTier": "none"}
        result = _apply_daily_policy_sell_gate(decisions, flags)
        self.assertEqual(result, decisions)

    def test_daily_stop_does_not_block_profit_take_sell(self):
        decisions = [_sell("tBTCUSD", "Voitto +1.5 % — nousu tasaantui, trailing-stop -0.9 % huipusta")]
        flags = {"dailyStopActive": True, "profitLockTier": "none"}
        result = _apply_daily_policy_sell_gate(decisions, flags)
        self.assertEqual(result, decisions)

    def test_daily_stop_does_not_block_stuck_time_stop_sell(self):
        decisions = [
            _sell(
                "tBTCUSD",
                "Positio jämähtänyt ≥24 h (-1.2 %) — myydään riippumatta markkinan noususta",
            )
        ]
        flags = {"dailyStopActive": True, "profitLockTier": "none"}
        result = _apply_daily_policy_sell_gate(decisions, flags)
        self.assertEqual(result, decisions)

    def test_daily_stop_does_not_block_fast_loss_sells(self):
        reasons = [
            "Tunnettu häviäjä (score -2.5) — täysi myynti -1.0 %",
            "Huono markkina-asetelma — täysi myynti -1.0 % (raja -0.8 %)",
            "Huono oma asetelma — täysi myynti -1.1 % (raja -0.8 %)",
        ]
        flags = {"dailyStopActive": True, "profitLockTier": "none"}

        for reason in reasons:
            with self.subTest(reason=reason):
                decisions = [_sell("tBTCUSD", reason)]
                result = _apply_daily_policy_sell_gate(decisions, flags)
                self.assertEqual(result, decisions)

    def test_daily_stop_does_not_block_buys(self):
        decisions = [_buy("tBTCUSD")]
        flags = {"dailyStopActive": True, "profitLockTier": "none"}
        result = _apply_daily_policy_sell_gate(decisions, flags)
        self.assertEqual(result, decisions)

    def test_mixed_decisions_only_discretionary_sell_removed(self):
        decisions = [
            _buy("tETHUSD"),
            _sell("tBTCUSD", "Stop-loss -1.2 %"),
            _sell("tXMRUSD", "Karhu-kassavara — trimmaus"),
        ]
        flags = {"dailyStopActive": True, "profitLockTier": "none"}
        result = _apply_daily_policy_sell_gate(decisions, flags)
        self.assertEqual(len(result), 2)
        self.assertNotIn("tXMRUSD", {d["symbol"] for d in result})
