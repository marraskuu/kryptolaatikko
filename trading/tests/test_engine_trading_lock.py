import threading
from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services import engine
from trading.services.session_state import default_state


class TradingStateLockTests(SimpleTestCase):
    def setUp(self):
        self.original_lock = engine._trading_state_lock
        engine._trading_state_lock = threading.RLock()

    def tearDown(self):
        engine._trading_state_lock = self.original_lock
        engine._cycle_running = False
        engine._cycle_started_at = 0.0

    def test_refresh_prices_waits_for_trading_lock_before_loading_state(self):
        load_called = threading.Event()
        finished = threading.Event()

        def fake_load_state():
            load_called.set()
            return default_state()

        def run_refresh():
            try:
                engine.refresh_prices()
            finally:
                finished.set()

        engine._trading_state_lock.acquire()
        try:
            with (
                patch.object(engine, "load_state", side_effect=fake_load_state),
                patch.object(engine, "fetch_all_markets", return_value=({}, {})),
                patch.object(engine, "save_state"),
            ):
                thread = threading.Thread(target=run_refresh, name="test-refresh-lock")
                thread.start()
                self.assertFalse(load_called.wait(0.05))
                self.assertFalse(finished.is_set())
        finally:
            engine._trading_state_lock.release()

        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(load_called.is_set())
        self.assertTrue(finished.is_set())

    def test_trading_cycle_waits_for_trading_lock_before_loading_state(self):
        load_called = threading.Event()
        finished = threading.Event()
        state = default_state()
        state["error"] = "price feed down"

        def fake_load_state():
            load_called.set()
            return state

        def run_cycle():
            try:
                engine.execute_trading_cycle()
            finally:
                finished.set()

        engine._trading_state_lock.acquire()
        try:
            with (
                patch.object(engine, "load_state", side_effect=fake_load_state),
                patch.object(engine, "_try_clear_price_error", return_value=False),
            ):
                thread = threading.Thread(target=run_cycle, name="test-cycle-lock")
                thread.start()
                self.assertFalse(load_called.wait(0.05))
                self.assertFalse(finished.is_set())
        finally:
            engine._trading_state_lock.release()

        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(load_called.is_set())
        self.assertTrue(finished.is_set())
