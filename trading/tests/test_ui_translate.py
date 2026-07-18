"""Unit tests for Finnish → English API string localization."""

from django.test import SimpleTestCase

from trading.services.ui_translate import (
    localize_api_payload,
    narrative_for_lang,
    translate_text,
)


class TranslateTextTests(SimpleTestCase):
    def test_stop_loss_reason(self):
        fi = (
            "Stop-loss -2.5 % (ATR-raja -3.1 %, bear-regiimi) — "
            "rajataan tappio, pääoma parempaan"
        )
        en = translate_text(fi, "en")
        self.assertIn("Stop-loss -2.5 %", en)
        self.assertIn("ATR limit -3.1 %", en)
        self.assertIn("cut loss", en)
        self.assertNotIn("rajataan", en)

    def test_ei_valinnoissa(self):
        fi = "SOL ei valinnoissa — myydään osa"
        en = translate_text(fi, "en")
        self.assertEqual(en, "SOL not in picks — selling part")

    def test_decision_title_ostetaan(self):
        fi = "Ostetaan 2 kryptoa"
        en = translate_text(fi, "en")
        self.assertEqual(en, "Buying 2 cryptos")

    def test_section_title_markkina_asetelmat(self):
        self.assertEqual(
            translate_text("Markkina-asetelmat", "en"),
            "Market setups",
        )

    def test_lang_fi_leaves_unchanged(self):
        fi = "Ostetaan 2 kryptoa · Markkina-asetelmat · Stop-loss -1.0 %"
        self.assertEqual(translate_text(fi, "fi"), fi)
        self.assertEqual(translate_text(fi, ""), fi)

    def test_gemini_wrapper_translates_free_text_fragments(self):
        fi = (
            "Gemini (7/10): vahva momentum ja likvidi order book · "
            "Hinta 1h +0.5 % · salkun osuus 40 %"
        )
        en = translate_text(fi, "en")
        self.assertTrue(en.startswith("Gemini (7/10): strong momentum"))
        self.assertIn("Price 1h +0.5 %", en)
        self.assertIn("portfolio share 40 %", en)

    def test_trade_reason_gemini_finnish_body(self):
        fi = (
            "Gemini (7/10): XMR:llä on vahva 24h muutos (0.84%) ja positiivinen 1h muutos "
            "(0.37%). RSI (63.0) on korkea, mutta ei vielä ylikuumentunut. "
            "Flow_imbalance_5m on vahvasti positiivinen (1.0), mikä osoittaa aggressiivista ostoa."
        )
        en = translate_text(fi, "en")
        self.assertIn("XMR has a strong 24h change", en)
        self.assertIn("and positive 1h change", en)
        self.assertIn("is high but not yet overheated", en)
        self.assertIn("is strongly positive", en)
        self.assertIn("which indicates aggressive buying", en)
        self.assertNotIn("muutos", en)
        self.assertNotIn("ylikuumentunut", en)

    def test_learning_report_section_lines(self):
        fi = (
            "1459/6648 asetelmaa opittu · Paras: bear · Huonoin: neutral · "
            "Aikastoppi/jumitus: -0.18 €/kauppa (4 kpl) · "
            "Varjosalkku 942.99 € vs. live 943.49 € (ero -0.50 €) · "
            "Estetyt ostot: 331 · Voitto-otot: 69 kpl · netto +553.23 € · "
            "jäi pöydälle 0.28 % · Suositus: Muut myynnit: 9 tappiota"
        )
        en = translate_text(fi, "en")
        self.assertIn("setups learned", en)
        self.assertIn("Best:", en)
        self.assertIn("Worst:", en)
        self.assertIn("Time stop/stuck", en)
        self.assertIn("Shadow portfolio 942.99 € vs live", en)
        self.assertIn("Blocked buys:", en)
        self.assertIn("Profit-takes:", en)
        self.assertIn("left on the table", en)
        self.assertIn("Recommendation:", en)
        self.assertNotIn("asetelmaa", en)
        self.assertNotIn("jumitus", en)

    def test_regime_symbol_change_lines(self):
        fi = (
            "Regiimi vakaa: bull · Käytössä: tasapainotus ≥0.80 %, voitto-otto ×1.00 · "
            "Myynnit: ennakoinnissa 2V/1T · Split-jakoja: 2 (0 auki) · "
            "Kerätään dataa (2/3 split-tapahtumaa) ennen vahvoja johtopäätöksiä · "
            "Estetyt confidence-tasot: 5, 8 · Minimi confidence myynneille: 10/10 · "
            "5/10: 3 kpl, +0.10 € · estetty · Vältetään: BTC · Suositaan: ZEC · "
            "Aktiivinen regiimi: bull · Regiimikohtainen viritys: 4/4 tagattua myyntiä · "
            "Viime 24 h: 1V / 0T · Viime 7 pv: 5V / 2T · "
            "Estetty 1 uutta ostokohdetta: SOL · Ostokielto poistui: XRP · "
            "+4 uutta markkina-asetelmaa opittu (1459 yhteensä) · Kokonaisexpectancy +0.1 → +0.2"
        )
        en = translate_text(fi, "en")
        self.assertIn("Regime stable: bull", en)
        self.assertIn("In use:", en)
        self.assertIn("rebalance", en)
        self.assertIn("Sells:", en)
        self.assertIn("in anticipation", en)
        self.assertIn("Split allocations: 2 (0 open)", en)
        self.assertIn("before strong conclusions", en)
        self.assertIn("Blocked confidence levels:", en)
        self.assertIn("Minimum confidence for sells:", en)
        self.assertIn("· blocked", en)
        self.assertIn("Avoiding:", en)
        self.assertIn("Favoring:", en)
        self.assertIn("Active regime:", en)
        self.assertIn("Regime-specific tuning:", en)
        self.assertIn("Last 24 h:", en)
        self.assertIn("Last 7 d:", en)
        self.assertIn("Blocked 1 new buy targets:", en)
        self.assertIn("Buy block lifted:", en)
        self.assertIn("new market setups learned (1459 total)", en)
        self.assertIn("Overall expectancy", en)
        self.assertNotIn("asetelmaa", en)
        self.assertNotIn("Vältetään", en)
        self.assertNotIn("yhteensä", en)

    def test_gemini_scanned_message(self):
        fi = (
            "Gemini skannasi 42 kryptoparia (ei stablecoineja) · "
            "3 valintaa · 5 signaalia"
        )
        en = translate_text(fi, "en")
        self.assertEqual(
            en,
            "Gemini scanned 42 crypto pairs (no stablecoins) · 3 picks · 5 signals",
        )

    def test_learning_note_gemini_picks(self):
        fi = (
            "Gemini tiukemmin (-0.22 €/kauppa) · valikoivampi win rate 35 % · "
            "Gemini estää conf 5,8,9,10 · Gemini-pickit heikot (0 % osuu) — conf ≥7, osto 50 % · "
            "Pickit häviävät ohituksille (30 % kierroksista)"
        )
        en = translate_text(fi, "en")
        self.assertIn("Gemini stricter", en)
        self.assertIn("more selective win rate 35 %", en)
        self.assertIn("Gemini blocks conf 5,8,9,10", en)
        self.assertIn("Gemini picks weak (0 % hit rate)", en)
        self.assertIn("buy 50 %", en)
        self.assertIn("Picks lose to skips (30 % of rounds)", en)
        self.assertNotIn("tiukemmin", en)
        self.assertNotIn("valikoivampi", en)
        self.assertNotIn("osuu", en)
        self.assertNotIn("kierroksista", en)


class LocalizeApiPayloadTests(SimpleTestCase):
    def test_translates_trade_reason(self):
        payload = {
            "portfolio": {
                "trades": [
                    {
                        "type": "sell",
                        "reason": "ETH ei valinnoissa — myydään osa",
                    }
                ]
            }
        }
        out = localize_api_payload(payload, "en")
        self.assertEqual(
            out["portfolio"]["trades"][0]["reason"],
            "ETH not in picks — selling part",
        )
        # Original unchanged (deep copy)
        self.assertEqual(
            payload["portfolio"]["trades"][0]["reason"],
            "ETH ei valinnoissa — myydään osa",
        )

    def test_prefers_reason_en_on_trades(self):
        payload = {
            "portfolio": {
                "trades": [
                    {
                        "type": "buy",
                        "reason": "Gemini (8/10): vahva momentum",
                        "reasonEn": "Gemini (8/10): strong momentum",
                    }
                ]
            },
            "aiEvents": [
                {
                    "reason": "Gemini (8/10): vahva momentum",
                    "reasonEn": "Gemini (8/10): strong momentum",
                }
            ],
        }
        out = localize_api_payload(payload, "en")
        self.assertEqual(
            out["portfolio"]["trades"][0]["reason"],
            "Gemini (8/10): strong momentum",
        )
        self.assertEqual(out["aiEvents"][0]["reason"], "Gemini (8/10): strong momentum")

    def test_prefers_gemini_signal_reason_en(self):
        payload = {
            "analyses": {
                "tETHUSD": {
                    "geminiSignal": {
                        "reason": "vahva momentum",
                        "reason_en": "strong momentum",
                    }
                }
            }
        }
        out = localize_api_payload(payload, "en")
        self.assertEqual(
            out["analyses"]["tETHUSD"]["geminiSignal"]["reason"],
            "strong momentum",
        )

    def test_lang_fi_noop(self):
        payload = {
            "lastAIReport": {"title": "Ostetaan 2 kryptoa"},
            "learning": {"note": "oppiminen kerää dataa"},
        }
        out = localize_api_payload(payload, "fi")
        self.assertEqual(out["lastAIReport"]["title"], "Ostetaan 2 kryptoa")
        self.assertEqual(out["learning"]["note"], "oppiminen kerää dataa")

    def test_profit_watch_and_report_sections(self):
        payload = {
            "profitWatch": {
                "tETHUSD": {
                    "statusText": "Voitto 2.5 % — odotetaan +3.0 %",
                }
            },
            "learningReport": {
                "sections": [
                    {
                        "title": "Markkina-asetelmat",
                        "lines": ["rotaatio pois (+0.10 €/kauppa)"],
                    }
                ],
                "roadmap": [
                    {
                        "label": "Voitto-otto (kevyt viritys)",
                        "status": "aktiivinen",
                        "progress": "käytössä",
                        "action": "Kevyt profit-take -viritys learning.py:ssä",
                    }
                ],
                "changes": ["rotaatio pois"],
            },
            "geminiStatus": {
                "message": "Gemini odottaa seuraavaa analyysikierrosta",
            },
        }
        out = localize_api_payload(payload, "en")
        self.assertIn("Profit 2.5 %", out["profitWatch"]["tETHUSD"]["statusText"])
        self.assertEqual(
            out["learningReport"]["sections"][0]["title"],
            "Market setups",
        )
        self.assertIn("rotation off", out["learningReport"]["sections"][0]["lines"][0])
        self.assertEqual(out["learningReport"]["roadmap"][0]["status"], "active")
        self.assertEqual(out["learningReport"]["roadmap"][0]["progress"], "in use")
        self.assertEqual(out["learningReport"]["changes"][0], "rotation off")
        self.assertIn("waiting for the next", out["geminiStatus"]["message"])


class NarrativeForLangTests(SimpleTestCase):
    def test_prefers_story_en(self):
        narrative = {
            "story": "Suomenkielinen tarina",
            "story_en": "English story",
            "intro": "Fi intro",
        }
        out = narrative_for_lang(narrative, "en")
        self.assertEqual(out["story"], "English story")
        self.assertEqual(out["intro"], "Fi intro")
        self.assertEqual(out["story_en"], "English story")

    def test_leaves_finnish_when_no_en(self):
        narrative = {"story": "Vain suomeksi"}
        out = narrative_for_lang(narrative, "en")
        self.assertEqual(out["story"], "Vain suomeksi")

    def test_fi_lang_unchanged(self):
        narrative = {"story": "Suomi", "story_en": "English"}
        out = narrative_for_lang(narrative, "fi")
        self.assertEqual(out["story"], "Suomi")
