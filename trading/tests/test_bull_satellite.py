from django.test import SimpleTestCase

from trading.services.bull_satellite import evaluate_bull_satellite_split


def _analysis(**overrides):
    data = {
        "currentPrice": 100.0,
        "volumeEur": 1_000_000.0,
        "score": 6,
        "changePct": 2.0,
        "change4hPct": 1.0,
        "change1hPct": 0.2,
        "mtfAlign": 1,
        "microChecked": True,
        "microBlocked": False,
    }
    data.update(overrides)
    return data


class BullSatelliteSplitTests(SimpleTestCase):
    def _split(self, *, primary_blocked: bool = False):
        primary = "tBTCUSD"
        satellite = "tETHUSD"
        analyses = {
            primary: _analysis(currentPrice=600.0, microBlocked=primary_blocked),
            satellite: _analysis(
                currentPrice=100.0,
                score=8,
                changePct=7.0,
                change4hPct=6.0,
                change1hPct=2.0,
                mtfAlign=2,
            ),
        }

        return evaluate_bull_satellite_split(
            regime="bull",
            regime_info={"regime": "bull", "phase": "bull"},
            holdings={primary: {"amount": 1.0, "avgPrice": 550.0}},
            analyses=analyses,
            total_value=1_000.0,
            available_cash=100.0,
            gemini_insights=None,
            gemini_active=False,
            ranked_buyable=[{"symbol": satellite, "analysis": analyses[satellite]}],
            buy_blocked=lambda _sym, analysis: bool((analysis or {}).get("microBlocked")),
            entry_score_min=1,
        )

    def test_split_allowed_when_primary_and_satellite_pass_gates(self):
        split = self._split()

        self.assertIsNotNone(split)
        self.assertEqual(split["primary"], "tBTCUSD")
        self.assertEqual(split["satellite"], "tETHUSD")

    def test_split_rejected_when_primary_buy_gate_blocks_add(self):
        """65/35 split must not bypass live blockers on the primary add leg."""
        self.assertIsNone(self._split(primary_blocked=True))
