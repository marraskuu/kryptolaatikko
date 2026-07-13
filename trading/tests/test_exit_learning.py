"""Exit-learning state updates must not lose observations under bot threads."""

from __future__ import annotations

import threading
from unittest.mock import patch

from django.test import TransactionTestCase

from trading.models import BotState
from trading.services import exit_learning


class ExitLearningConcurrencyTests(TransactionTestCase):
    def setUp(self):
        BotState.objects.update_or_create(pk=4, defaults={"data": {"obs": [], "stats": {}}})

    def tearDown(self):
        BotState.objects.filter(pk=4).delete()

    def _record_exit(self, symbol: str) -> None:
        exit_learning.record_profit_take_exit(
            symbol=symbol,
            sell_price=100.0,
            peak_price=103.0,
            profit_pct=4.0,
            pullback_pct=1.0,
            exit_setup="exit|neutral|p3|rsi_hi|mtf+|bk+|crL",
            trade_id=None,
        )

    def test_concurrent_profit_take_records_preserve_both_observations(self):
        """A blocked first save must not let a second writer save a stale pk=4 snapshot."""
        first_save_started = threading.Event()
        second_save_started = threading.Event()
        save_calls = 0
        save_calls_lock = threading.Lock()
        original_save = exit_learning._save

        def delayed_save(data):
            nonlocal save_calls
            with save_calls_lock:
                save_calls += 1
                call_no = save_calls

            if call_no == 1:
                first_save_started.set()
                second_save_started.wait(timeout=0.5)
            elif call_no == 2:
                second_save_started.set()

            original_save(data)

        with patch.object(exit_learning, "_save", new=delayed_save):
            first = threading.Thread(target=self._record_exit, args=("tBTCUSD",), name="exit-first")
            second = threading.Thread(target=self._record_exit, args=("tETHUSD",), name="exit-second")

            first.start()
            self.assertTrue(first_save_started.wait(timeout=5))
            second.start()

            first.join(timeout=5)
            second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())

        final = BotState.objects.get(pk=4).data
        self.assertCountEqual([obs["s"] for obs in final["obs"]], ["tBTCUSD", "tETHUSD"])
