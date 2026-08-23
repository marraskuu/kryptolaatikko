import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from trading import views
from trading.services import market_learning_backfill as backfill


def _set_backfill_running(value: bool) -> None:
    with backfill._backfill_lock:
        backfill._backfill_running = value


class HistoricalBackfillLockTests(SimpleTestCase):
    def tearDown(self):
        _set_backfill_running(False)
        super().tearDown()

    def test_exclusive_runner_rejects_overlap_without_running_backfill(self):
        _set_backfill_running(True)

        with patch(
            "trading.services.market_learning_backfill.run_historical_backfill"
        ) as run_market:
            with self.assertRaises(backfill.HistoricalBackfillAlreadyRunning):
                backfill.run_historical_backfills_exclusive()

        run_market.assert_not_called()

    @patch(
        "trading.services.market_learning_backfill.run_historical_backfill",
        side_effect=RuntimeError("boom"),
    )
    def test_exclusive_runner_clears_running_flag_after_failure(self, _run_market):
        _set_backfill_running(False)

        with self.assertRaises(RuntimeError):
            backfill.run_historical_backfills_exclusive()

        self.assertFalse(backfill._backfill_running)

    @patch("trading.services.setup_historical_backfill.get_setup_backfill_status", return_value={})
    @patch(
        "trading.services.market_learning_backfill.get_backfill_status",
        return_value={"historyBackfillRunning": True},
    )
    @patch("trading.views._check_admin_key", return_value=True)
    def test_sync_endpoint_returns_conflict_when_backfill_running(
        self,
        _check_admin_key,
        _get_backfill_status,
        _get_setup_backfill_status,
    ):
        _set_backfill_running(True)
        request = RequestFactory().get("/api/admin/historical-backfill/?async=0")

        with patch(
            "trading.services.market_learning_backfill.run_historical_backfill"
        ) as run_market:
            response = views.api_historical_backfill(request)

        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"], "historical_backfill_already_running")
        self.assertTrue(payload["historyBackfillRunning"])
        run_market.assert_not_called()
