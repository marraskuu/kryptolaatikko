"""fetch_candles: 429-uudelleenyritys ei saa palauttaa hiljaa tyhjää dataa liian aikaisin."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from trading.services import bitfinex


def _mock_response(*, status_code: int = 200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


class FetchCandlesRetryTests(SimpleTestCase):
    def setUp(self):
        # EUR-quote välttää _eur_factor_for_symbolin ylimääräisen tEURUSD-kutsun.
        bitfinex._crypto_meta["tBTCEUR"] = {"quote": "EUR"}

    def tearDown(self):
        bitfinex._crypto_meta.pop("tBTCEUR", None)

    @patch("trading.services.bitfinex.time.sleep")
    @patch("trading.services.bitfinex.requests.get")
    def test_retries_after_429_then_succeeds(self, mock_get, mock_sleep):
        candle_row = [1700000000000, 100.0, 105.0, 110.0, 95.0, 12.5]
        mock_get.side_effect = [
            _mock_response(status_code=429),
            _mock_response(status_code=200, json_data=[candle_row]),
        ]

        result = bitfinex.fetch_candles("tBTCEUR", "1h", limit=10)

        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["close"], 105.0)

    @patch("trading.services.bitfinex.time.sleep")
    @patch("trading.services.bitfinex.requests.get")
    def test_gives_up_after_max_retries_returns_empty_without_raising(self, mock_get, mock_sleep):
        attempts = bitfinex.CANDLES_RATE_LIMIT_RETRIES + 1
        mock_get.side_effect = [_mock_response(status_code=429) for _ in range(attempts)]

        result = bitfinex.fetch_candles("tBTCEUR", "1h", limit=10)

        self.assertEqual(result, [])
        self.assertEqual(mock_get.call_count, attempts)
        self.assertEqual(mock_sleep.call_count, bitfinex.CANDLES_RATE_LIMIT_RETRIES)

    @patch("trading.services.bitfinex.time.sleep")
    @patch("trading.services.bitfinex.requests.get")
    def test_404_returns_immediately_without_retry(self, mock_get, mock_sleep):
        mock_get.side_effect = [_mock_response(status_code=404)]

        result = bitfinex.fetch_candles("tBTCEUR", "1h", limit=10)

        self.assertEqual(result, [])
        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("trading.services.bitfinex.time.sleep")
    @patch("trading.services.bitfinex.requests.get")
    def test_back_to_back_calls_each_recover_from_rate_limit(self, mock_get, mock_sleep):
        """Simuloi market- ja setup-backfillin peräkkäiset kutsut samalle symbolille:
        molemmat osuvat 429:ään mutta toipuvat, kuten tuotannon viikkoajossa tapahtuisi."""
        candle_row = [1700000000000, 1.0, 1.0, 1.0, 1.0, 1.0]
        mock_get.side_effect = [
            _mock_response(status_code=429),
            _mock_response(status_code=200, json_data=[candle_row]),
            _mock_response(status_code=429),
            _mock_response(status_code=200, json_data=[candle_row]),
        ]

        first = bitfinex.fetch_candles("tBTCEUR", "1h", limit=10)
        second = bitfinex.fetch_candles("tBTCEUR", "1h", limit=10)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(mock_get.call_count, 4)
