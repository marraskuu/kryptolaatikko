"""Hintapiikin järkevyystarkistus order bookia vasten (varjodiagnostiikka, idea #5)."""

from django.test import SimpleTestCase

from trading.services.price_spike_shadow import (
    _classify_book_confirmation,
    _resolve_one,
    detect_price_spikes,
    resolve_pending_events,
    select_top_spikes,
)


class DetectPriceSpikesTests(SimpleTestCase):
    def test_flags_symbol_moving_above_threshold(self):
        prev = {"tBTCUSD": {"last": 100.0}}
        cur = {"tBTCUSD": {"last": 104.0}}  # +4 %
        spikes = detect_price_spikes(prev, cur, threshold_pct=3.0)
        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0]["symbol"], "tBTCUSD")
        self.assertAlmostEqual(spikes[0]["movePct"], 4.0, places=3)

    def test_ignores_move_below_threshold(self):
        prev = {"tBTCUSD": {"last": 100.0}}
        cur = {"tBTCUSD": {"last": 101.0}}  # +1 %
        spikes = detect_price_spikes(prev, cur, threshold_pct=3.0)
        self.assertEqual(spikes, [])

    def test_ignores_symbol_missing_from_either_side(self):
        prev = {"tBTCUSD": {"last": 100.0}}
        cur = {"tETHUSD": {"last": 50.0}}
        self.assertEqual(detect_price_spikes(prev, cur), [])
        self.assertEqual(detect_price_spikes({}, cur), [])
        self.assertEqual(detect_price_spikes(prev, {}), [])


class BookConfirmationTests(SimpleTestCase):
    def test_confirms_upward_move_with_positive_imbalance(self):
        self.assertTrue(_classify_book_confirmation({"bookImbalance": 0.3}, move_pct=4.0))

    def test_rejects_upward_move_with_negative_imbalance(self):
        self.assertFalse(_classify_book_confirmation({"bookImbalance": -0.2}, move_pct=4.0))

    def test_confirms_downward_move_with_negative_imbalance(self):
        self.assertTrue(_classify_book_confirmation({"bookImbalance": -0.3}, move_pct=-4.0))

    def test_returns_none_without_book_data(self):
        self.assertIsNone(_classify_book_confirmation(None, move_pct=4.0))
        self.assertIsNone(_classify_book_confirmation({}, move_pct=4.0))


class ResolvePendingEventsTests(SimpleTestCase):
    def test_resolve_one_computes_outcome_and_reversion(self):
        item = {
            "symbol": "tBTCUSD",
            "detectedAt": "2026-07-22T10:00:00+00:00",
            "prevPrice": 100.0,
            "priceAtDetection": 104.0,
            "movePct": 4.0,
            "bookConfirmed": False,
        }
        # Hinta antoi takaisin koko piikin (palasi 100:aan) -> reverted = True.
        tickers = {"tBTCUSD": {"last": 100.0}}
        resolved = _resolve_one(item, tickers)
        self.assertAlmostEqual(resolved["outcomeMovePct"], -3.846, places=2)
        self.assertTrue(resolved["reverted"])

    def test_resolve_one_continuation_not_reverted(self):
        item = {
            "symbol": "tBTCUSD",
            "detectedAt": "2026-07-22T10:00:00+00:00",
            "prevPrice": 100.0,
            "priceAtDetection": 104.0,
            "movePct": 4.0,
            "bookConfirmed": True,
        }
        # Hinta jatkoi ylös -> ei kääntynyt.
        tickers = {"tBTCUSD": {"last": 108.0}}
        resolved = _resolve_one(item, tickers)
        self.assertFalse(resolved["reverted"])

    def test_resolve_pending_events_moves_due_items_and_updates_summary(self):
        state = {
            "priceSpikeShadow": {
                "version": 1,
                "pending": [
                    {
                        "symbol": "tBTCUSD",
                        "detectedAt": "2026-07-22T10:00:00+00:00",
                        "resolveAt": 0.0,  # jo erääntynyt
                        "prevPrice": 100.0,
                        "priceAtDetection": 104.0,
                        "movePct": 4.0,
                        "bookConfirmed": False,
                        "bookImbalance": -0.1,
                        "bookSpreadPct": 0.1,
                    }
                ],
                "events": [],
                "summary": {
                    "spikesDetected": 1,
                    "bookConfirmed": 0,
                    "bookUnconfirmed": 1,
                    "unconfirmedReverted": 0,
                    "unconfirmedContinued": 0,
                    "confirmedReverted": 0,
                    "confirmedContinued": 0,
                },
            }
        }
        resolve_pending_events(state, {"tBTCUSD": {"last": 100.0}})
        shadow = state["priceSpikeShadow"]
        self.assertEqual(shadow["pending"], [])
        self.assertEqual(len(shadow["events"]), 1)
        self.assertEqual(shadow["summary"]["unconfirmedReverted"], 1)

    def test_resolve_pending_events_keeps_not_yet_due_items(self):
        state = {
            "priceSpikeShadow": {
                "version": 1,
                "pending": [
                    {
                        "symbol": "tBTCUSD",
                        "detectedAt": "2026-07-22T10:00:00+00:00",
                        "resolveAt": 9_999_999_999.0,  # kaukana tulevaisuudessa
                        "prevPrice": 100.0,
                        "priceAtDetection": 104.0,
                        "movePct": 4.0,
                        "bookConfirmed": None,
                    }
                ],
                "events": [],
                "summary": {},
            }
        }
        resolve_pending_events(state, {"tBTCUSD": {"last": 105.0}})
        shadow = state["priceSpikeShadow"]
        self.assertEqual(len(shadow["pending"]), 1)
        self.assertEqual(shadow["events"], [])


class SelectTopSpikesTests(SimpleTestCase):
    def test_returns_all_when_under_limit(self):
        spikes = [{"symbol": "A", "movePct": 3.0}, {"symbol": "B", "movePct": -4.0}]
        self.assertEqual(select_top_spikes(spikes, limit=3), spikes)

    def test_caps_to_limit_keeping_largest_moves(self):
        spikes = [
            {"symbol": "A", "movePct": 3.0},
            {"symbol": "B", "movePct": -9.0},
            {"symbol": "C", "movePct": 5.0},
            {"symbol": "D", "movePct": 3.5},
        ]
        top = select_top_spikes(spikes, limit=2)
        self.assertEqual(len(top), 2)
        self.assertEqual({s["symbol"] for s in top}, {"B", "C"})

    def test_empty_list_returns_empty(self):
        self.assertEqual(select_top_spikes([], limit=3), [])
