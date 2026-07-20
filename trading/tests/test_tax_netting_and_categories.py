"""Vero lasketaan vuoden nettoluovutusvoitosta, ja exit-syyt kategorisoituvat oikein.

Ks. muutosloki: vero laskettiin aiemmin bruttovoitoista nettouttamatta tappioita
saman vuoden sisällä (Suomen verotuksessa luovutustappiot vähennetään ensin saman
vuoden voitoista, ylijäävä osa seuraavien 5 v voitoista). "other"-kategoria niputti
useita eri exit-mekanismeja yhteen peittäen niiden oman expectancyn.
"""

from django.test import SimpleTestCase

from trading.services.learning import _category
from trading.services.portfolio import Portfolio, default_portfolio


def _sell(symbol: str, profit_loss: float, timestamp: str) -> dict:
    return {
        "type": "sell",
        "symbol": symbol,
        "amount": 1.0,
        "price": 100.0,
        "eurTotal": 100.0 + profit_loss,
        "costBasis": 100.0,
        "profitLoss": profit_loss,
        "profit": max(profit_loss, 0.0),
        "tax": max(profit_loss, 0.0) * 0.30,
        "timestamp": timestamp,
        "reason": "test",
    }


class TaxNettingTests(SimpleTestCase):
    def _portfolio_with_trades(self, trades: list[dict]) -> Portfolio:
        data = default_portfolio()
        data["trades"] = trades
        return Portfolio(data)

    def test_net_loss_year_owes_no_tax(self):
        """Vuosi jossa voitot < tappiot ei synnytä veroa, vaikka yksittäisiä voittoja on."""
        trades = [
            _sell("tBTCUSD", 825.27, "2026-03-01T12:00:00Z"),
            _sell("tETHUSD", -886.77, "2026-05-01T12:00:00Z"),
        ]
        portfolio = self._portfolio_with_trades(trades)
        tax_by_year = portfolio.tax_owed_by_year()
        self.assertEqual(tax_by_year[2026], 0.0)

    def test_gain_year_taxes_only_net_amount(self):
        """Voittovuonna vero lasketaan netosta, ei bruttovoitosta."""
        trades = [
            _sell("tBTCUSD", 1000.0, "2026-03-01T12:00:00Z"),
            _sell("tETHUSD", -400.0, "2026-05-01T12:00:00Z"),
        ]
        portfolio = self._portfolio_with_trades(trades)
        tax_by_year = portfolio.tax_owed_by_year()
        self.assertAlmostEqual(tax_by_year[2026], (1000.0 - 400.0) * 0.30, places=6)

    def test_loss_carries_forward_to_next_year_gain(self):
        """Edellisvuoden käyttämätön tappio vähennetään seuraavan vuoden voitosta."""
        trades = [
            _sell("tBTCUSD", -500.0, "2025-06-01T12:00:00Z"),
            _sell("tETHUSD", 300.0, "2026-06-01T12:00:00Z"),
        ]
        portfolio = self._portfolio_with_trades(trades)
        tax_by_year = portfolio.tax_owed_by_year()
        self.assertEqual(tax_by_year[2025], 0.0)
        # 500 € tappiosta 300 € käytetään 2026 voittoa vastaan -> ei veroa vielä.
        self.assertEqual(tax_by_year[2026], 0.0)
        self.assertAlmostEqual(portfolio.loss_carryforward(), 200.0, places=6)

    def test_loss_carryforward_is_usable_in_fifth_following_year(self):
        """Vuoden 2020 tappio on vielä vähennyskelpoinen vuonna 2025."""
        trades = [
            _sell("tBTCUSD", -500.0, "2020-06-01T12:00:00Z"),
            _sell("tETHUSD", 300.0, "2025-06-01T12:00:00Z"),
        ]
        portfolio = self._portfolio_with_trades(trades)
        tax_by_year = portfolio.tax_owed_by_year()
        self.assertEqual(tax_by_year[2025], 0.0)
        self.assertAlmostEqual(portfolio.loss_carryforward(as_of_year=2025), 200.0, places=6)

    def test_loss_carryforward_expires_after_five_following_years(self):
        """Vuoden 2020 tappio ei saa enää pienentää vuoden 2026 veroa."""
        trades = [
            _sell("tBTCUSD", -500.0, "2020-06-01T12:00:00Z"),
            _sell("tETHUSD", 300.0, "2026-06-01T12:00:00Z"),
        ]
        portfolio = self._portfolio_with_trades(trades)
        tax_by_year = portfolio.tax_owed_by_year()
        self.assertAlmostEqual(tax_by_year[2026], 300.0 * 0.30, places=6)
        self.assertEqual(portfolio.loss_carryforward(as_of_year=2026), 0.0)

    def test_get_tax_summary_reflects_net_current_year(self):
        trades = [
            _sell("tBTCUSD", 825.27, "2026-03-01T12:00:00Z"),
            _sell("tETHUSD", -886.77, "2026-05-01T12:00:00Z"),
        ]
        portfolio = self._portfolio_with_trades(trades)
        summary = portfolio.get_tax_summary({})
        self.assertEqual(summary["currentYearTax"], 0.0)
        self.assertAlmostEqual(summary["currentYearRealized"], -61.5, places=1)
        self.assertGreater(summary["currentYearGrossWins"], 0)


class ExitReasonCategoryTests(SimpleTestCase):
    def test_bear_cash_trim_is_its_own_category(self):
        reason = "Karhu-kassavara — BTC trimmaus 50 € kohti 25 % käteistä"
        self.assertEqual(_category(reason), "bear_cash_trim")

    def test_known_loser_reasons_are_loser_release(self):
        reasons = [
            "Krooninen häviäjä — täysi myynti -1.2 % (raja -0.8 %)",
            "Symboli cooldownissa — täysi myynti -0.9 % (raja -0.8 %)",
            "Tunnettu häviäjä (score -2.5) — täysi myynti -1.0 %",
            "Estetty kohde — vapautetaan -0.5 % (historia -6.20 €, 5V/3T)",
        ]
        for reason in reasons:
            self.assertEqual(_category(reason), "loser_release", reason)

    def test_bad_setup_reasons_are_setup_exit(self):
        reasons = [
            "Huono markkina-asetelma — täysi myynti -1.0 % (raja -0.8 %)",
            "Huono oma asetelma — täysi myynti -1.1 % (raja -0.8 %)",
        ]
        for reason in reasons:
            self.assertEqual(_category(reason), "setup_exit", reason)

    def test_existing_categories_still_match(self):
        cases = {
            "Stop-loss -2.0 % (ATR-raja -1.5 %) — rajataan tappio, pääoma parempaan": "stop_loss",
            "Aikastoppi ≥4 h — jämähtänyt (-0.3 %), myydään riippumatta noususta": "time_stop",
            "Voitto +2.5 % — nousu tasaantui (huippu 100 €), trailing-stop -0.5 % huipusta": "profit_take",
            "Gemini (9/10): vahva momentum": "gemini_sell",
            "Ei valinnoissa — myydään osa 30 %": "rotation",
        }
        for reason, expected in cases.items():
            self.assertEqual(_category(reason), expected, reason)

    def test_unrecognized_reason_still_falls_back_to_other(self):
        self.assertEqual(_category("Jokin täysin uusi syy"), "other")
