"""BotState-tallennus — rinnakkaiset päivitykset eivät ylikirjoita toisiaan."""

import threading

from django.test import TestCase, TransactionTestCase

from trading.models import BotState
from trading.services.portfolio import default_portfolio
from trading.services.session_state import default_state
from trading.services.state_store import (
    STATE_DELETED_KEYS,
    load_state,
    mark_state_keys_deleted,
    patch_state_keys,
    save_state,
)


class StateStoreConcurrencyTests(TransactionTestCase):
    def setUp(self):
        BotState.objects.update_or_create(pk=1, defaults={"data": default_state()})

    def tearDown(self):
        BotState.objects.filter(pk=1).update(data=default_state())

    def test_concurrent_save_state_preserves_both_keys(self):
        """Kaksi säiettä päivittää eri avaimia — molempien arvot säilyvät."""
        barrier = threading.Barrier(2)

        def worker_a():
            state = load_state()
            state["_testMarkerA"] = 1
            barrier.wait()
            save_state(state)

        def worker_b():
            state = load_state()
            state["_testMarkerB"] = 2
            barrier.wait()
            save_state(state)

        threads = [
            threading.Thread(target=worker_a, name="state-a"),
            threading.Thread(target=worker_b, name="state-b"),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        final = BotState.objects.get(pk=1).data
        self.assertEqual(final.get("_testMarkerA"), 1)
        self.assertEqual(final.get("_testMarkerB"), 2)

    def test_deleted_keys_removed_from_db(self):
        state = load_state()
        state["learningNarrativeError"] = "test error"
        save_state(state)

        state = load_state()
        mark_state_keys_deleted(state, "learningNarrativeError")
        save_state(state)

        final = BotState.objects.get(pk=1).data
        self.assertNotIn("learningNarrativeError", final)

    def test_patch_state_keys_applies_deletions(self):
        state = load_state()
        state["learningNarrativeError"] = "test error"
        save_state(state)

        state = load_state()
        mark_state_keys_deleted(state, "learningNarrativeError")
        patch_state_keys({STATE_DELETED_KEYS: list(state.get(STATE_DELETED_KEYS) or [])})

        final = load_state()
        self.assertNotIn("learningNarrativeError", final)

    def test_stale_portfolio_not_overwritten(self):
        """Vanha salkku-snapshot ei peru uudempaa tradeId-versiota."""
        state = load_state()
        portfolio = dict(state["portfolio"])
        portfolio["tradeId"] = 10
        portfolio["cash"] = 42.0
        state["portfolio"] = portfolio
        save_state(state)

        stale = load_state()
        stale["_testMarker"] = 1
        stale["portfolio"] = default_portfolio()
        save_state(stale)

        final = BotState.objects.get(pk=1).data
        self.assertEqual(final["portfolio"]["tradeId"], 10)
        self.assertEqual(final["portfolio"]["cash"], 42.0)
        self.assertEqual(final.get("_testMarker"), 1)

    def test_stale_snapshot_does_not_clear_concurrent_price_error(self):
        """Vanha snapshot ei saa kuitata tuoretta Bitfinex-virhetta pois."""
        state = load_state()
        state["lastPriceTick"] = 1_000
        state["tickers"] = {"tBTCUSD": {"last": 100.0}}
        state["analyses"] = {"tBTCUSD": {"quick": True, "price": 100.0}}
        state["error"] = None
        save_state(state)

        stale = load_state()
        failed_refresh = load_state()
        failed_refresh["error"] = "Bitfinex timeout"
        save_state(failed_refresh)

        stale["error"] = None
        stale["_testMarker"] = "stale-worker-finished"
        save_state(stale)

        final = BotState.objects.get(pk=1).data
        self.assertEqual(final.get("error"), "Bitfinex timeout")
        self.assertEqual(final.get("_testMarker"), "stale-worker-finished")

    def test_older_price_snapshot_does_not_restore_stale_market_data(self):
        """Uudemmat kurssit ja analyysit sailyvat vanhan workerin tallennuksen yli."""
        state = load_state()
        state["lastPriceTick"] = 1_000
        state["tickers"] = {"tBTCUSD": {"last": 100.0}}
        state["analyses"] = {"tBTCUSD": {"quick": True, "price": 100.0}}
        state["error"] = None
        save_state(state)

        stale = load_state()
        fresh = load_state()
        fresh["lastPriceTick"] = 2_000
        fresh["tickers"] = {"tBTCUSD": {"last": 125.0}}
        fresh["analyses"] = {"tBTCUSD": {"quick": True, "price": 125.0}}
        fresh["error"] = None
        save_state(fresh)

        stale["tickers"] = {"tBTCUSD": {"last": 95.0}}
        stale["analyses"] = {"tBTCUSD": {"quick": True, "price": 95.0}}
        stale["_testMarker"] = "stale-worker-finished"
        save_state(stale)

        final = BotState.objects.get(pk=1).data
        self.assertEqual(final.get("lastPriceTick"), 2_000)
        self.assertEqual(final.get("tickers"), {"tBTCUSD": {"last": 125.0}})
        self.assertEqual(final.get("analyses"), {"tBTCUSD": {"quick": True, "price": 125.0}})
        self.assertIsNone(final.get("error"))
        self.assertEqual(final.get("_testMarker"), "stale-worker-finished")
