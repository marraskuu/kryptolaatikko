"""blockedBuyCounterfactualEur: kumulatiivinen, ei enää pelkkä avoimien (<=40)
estettyjen ostojen hetkikuva.

Ennen v5:tä suljetun (myydyn) tai 40 kohteen ikkunasta pudonneen estetyn oston
viimeisin arvio katosi jäljettömästi eikä koskaan päätynyt mihinkään
kumulatiiviseen summaan — minkä vuoksi tuotannossa blockedBuyCounterfactualEur
näytti 0,00 € vaikka estettyjä ostoja oli satoja."""

from django.test import SimpleTestCase

from trading.services.daily_policy_shadow import (
    OPEN_BLOCKED_BUY_LIMIT,
    _update_blocked_buy_mtm,
    _get_shadow,
    record_executed_trade,
)

_BLOCK_FLAGS = {"dailyStopActive": True, "profitLockTier": "none"}
_ALLOW_FLAGS = {"dailyStopActive": False, "profitLockTier": "none"}


def _base_state() -> dict:
    return {
        "portfolio": {"holdings": {}, "trades": [], "cash": 1000.0},
        "tickers": {},
    }


class BlockedBuyCounterfactualTests(SimpleTestCase):
    def test_open_position_tracked_as_unrealized_not_realized(self):
        state = _base_state()
        record_executed_trade(
            state,
            trade_type="buy",
            symbol="tXMRUST",
            eur_amount=100.0,
            reason="Gemini buy",
            flags=_BLOCK_FLAGS,
            price=100.0,
            amount=1.0,
        )
        shadow = _get_shadow(state)
        state["portfolio"]["holdings"] = {"tXMRUST": {"amount": 1.0, "avgPrice": 100.0}}
        state["tickers"] = {"tXMRUST": {"last": 90.0}}

        _update_blocked_buy_mtm(shadow, state, total_value=990.0)

        summary = shadow["summary"]
        self.assertEqual(len(shadow["openBlockedBuys"]), 1)
        # Hinta laski 100 -> 90, eli estetty osto olisi ollut hyvä esto: +10 €.
        self.assertEqual(summary["blockedBuyUnrealizedEur"], 10.0)
        self.assertEqual(summary["blockedBuyRealizedCounterfactualEur"], 0.0)
        self.assertEqual(summary["blockedBuyCounterfactualEur"], 10.0)

    def test_closed_position_finalizes_into_realized_cumulative(self):
        state = _base_state()
        record_executed_trade(
            state,
            trade_type="buy",
            symbol="tXMRUST",
            eur_amount=100.0,
            reason="Gemini buy",
            flags=_BLOCK_FLAGS,
            price=100.0,
            amount=1.0,
        )
        shadow = _get_shadow(state)
        state["portfolio"]["holdings"] = {"tXMRUST": {"amount": 1.0, "avgPrice": 100.0}}
        state["tickers"] = {"tXMRUST": {"last": 90.0}}
        _update_blocked_buy_mtm(shadow, state, total_value=990.0)

        # Positio myyty livenä -> ei enää holdingsissa. Viimeisin arvio (+10 €)
        # tulee lukita pysyvästi sen sijaan että se katoaisi.
        state["portfolio"]["holdings"] = {}
        _update_blocked_buy_mtm(shadow, state, total_value=1010.0)

        summary = shadow["summary"]
        self.assertEqual(shadow["openBlockedBuys"], [])
        self.assertEqual(summary["blockedBuyRealizedCounterfactualEur"], 10.0)
        self.assertEqual(summary["blockedBuyUnrealizedEur"], 0.0)
        self.assertEqual(summary["blockedBuyCounterfactualEur"], 10.0)

        # Ja se pysyy lukittuna vielä seuraavallakin kierroksella.
        _update_blocked_buy_mtm(shadow, state, total_value=1010.0)
        self.assertEqual(shadow["summary"]["blockedBuyRealizedCounterfactualEur"], 10.0)

    def test_eviction_beyond_window_finalizes_oldest_entry(self):
        state = _base_state()
        for i in range(OPEN_BLOCKED_BUY_LIMIT + 1):
            record_executed_trade(
                state,
                trade_type="buy",
                symbol=f"tSYM{i}",
                eur_amount=10.0,
                reason="Gemini buy",
                flags=_BLOCK_FLAGS,
                price=10.0,
                amount=1.0,
            )
        shadow = _get_shadow(state)
        self.assertEqual(len(shadow["openBlockedBuys"]), OPEN_BLOCKED_BUY_LIMIT)
        # Vanhin (tSYM0) putosi ikkunasta ilman koskaan asetettua unrealizedPnl:ää
        # (0.0) -> lukitus tapahtuu silti, summaa ei jätetä käsittelemättä.
        self.assertNotIn(
            "tSYM0", {item["symbol"] for item in shadow["openBlockedBuys"]}
        )
        self.assertEqual(shadow["summary"]["blockedBuyRealizedCounterfactualEur"], 0.0)

    def test_not_blocked_buy_does_not_open_tracking(self):
        state = _base_state()
        record_executed_trade(
            state,
            trade_type="buy",
            symbol="tXMRUST",
            eur_amount=100.0,
            reason="Gemini buy",
            flags=_ALLOW_FLAGS,
            price=100.0,
            amount=1.0,
        )
        shadow = _get_shadow(state)
        self.assertEqual(shadow.get("openBlockedBuys"), [])
