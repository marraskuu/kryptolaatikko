"""BotState-tallennus — rinnakkaiset päivitykset eivät ylikirjoita toisiaan."""

import threading

from django.test import TestCase, TransactionTestCase

from trading.models import BotState
from trading.services.portfolio import Portfolio, default_portfolio
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

    def test_concurrent_portfolio_trades_with_same_next_id_are_merged(self):
        """Rinnakkaiset kaupat eivät saa kadota, vaikka snapshotit käyttävät samaa id:tä."""
        btc_state = load_state()
        eth_state = load_state()

        btc_portfolio = Portfolio(btc_state["portfolio"])
        self.assertTrue(btc_portfolio.buy("tBTCUSD", 100.0, 100.0, "BTC buy"))
        btc_state["portfolio"] = btc_portfolio.to_dict()

        eth_portfolio = Portfolio(eth_state["portfolio"])
        self.assertTrue(eth_portfolio.buy("tETHUSD", 150.0, 50.0, "ETH buy"))
        eth_state["portfolio"] = eth_portfolio.to_dict()

        self.assertEqual(btc_state["portfolio"]["tradeId"], 1)
        self.assertEqual(eth_state["portfolio"]["tradeId"], 1)

        save_state(btc_state)
        save_state(eth_state)

        final = BotState.objects.get(pk=1).data["portfolio"]
        trades_by_symbol = {trade["symbol"]: trade for trade in final["trades"]}
        self.assertEqual(set(trades_by_symbol), {"tBTCUSD", "tETHUSD"})
        self.assertEqual(final["tradeId"], 2)
        self.assertEqual({trade["id"] for trade in final["trades"]}, {1, 2})
        self.assertAlmostEqual(final["cash"], 750.0)
        self.assertAlmostEqual(final["holdings"]["tBTCUSD"]["amount"], 1.0)
        self.assertAlmostEqual(final["holdings"]["tETHUSD"]["amount"], 3.0)
