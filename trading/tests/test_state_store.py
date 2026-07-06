"""BotState-tallennus — rinnakkaiset päivitykset eivät ylikirjoita toisiaan."""

import threading

from django.test import TestCase, TransactionTestCase

from trading.models import BotState
from trading.services.portfolio import default_portfolio
from trading.services.session_state import default_state
from trading.services.state_store import (
    load_state,
    mark_state_keys_deleted,
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
