"""
Julkinen muutosloki — /muutokset/

Lisää uusi päivä tai uudet kohdat aina kun teet käyttäjälle näkyvän muutoksen
(deploy, uusi ominaisuus, merkittävä korjaus). Uusin päivä ensin.

Älä lisää /stats-sivun (kävijätilastot, keston seuranta, stats-kirjautuminen)
muutoksia tähän lokiin — ne eivät kuulu julkiseen muutoslokiin.
"""

from __future__ import annotations

from typing import Any

# title = lyhyt otsikko, body = valinnainen tarkennus
# title_en / body_en = optional English translations
Entry = dict[str, str]
Day = dict[str, Any]

CHANGELOG: list[Day] = [
    {
        "date": "2026-07-18",
        "entries": [
            {
                "title": "Englanninkieliset sivut: kauppojen syyt ja Gemini-kertomukset",
                "body": "/eng hakee tilan ?lang=en — syyt, AI-raportit ja oppimisraportti käännetään. Uudet Gemini-signaalit ja kertomukset tallennetaan kaksikielisinä (reason_en / *_en).",
                "title_en": "English pages: trade reasons and Gemini narratives",
                "body_en": "/eng fetches state with ?lang=en — reasons, AI reports and the learning report are localized. New Gemini signals and narratives are stored bilingually (reason_en / *_en).",
            },
            {
                "title": "Englanninkieliset sivut (/eng, /changelog)",
                "body": "Etusivu ja muutosloki saatavilla englanniksi; kielivalitsin headerissa ja footereissa.",
                "title_en": "English pages (/eng, /changelog)",
                "body_en": "Home and changelog available in English; language switcher in the header and footers.",
            },
            {
                "title": "Voittostrategia: karhu-jäädytys + rotaatio + symbolimuisti",
                "body": "Ei uusia ostoja karhussa (live: bear −239 € vs bull +235 €). “Ei valinnoissa” -rotaatio vain voitolla ja selkeällä edgellä. Nettopositiivisia ei estetä symbolimuistissa.",
                "title_en": "Profit strategy: bear freeze + rotation + symbol memory",
                "body_en": "No new buys in a bear market (live: bear −€239 vs bull +€235). “Not in picks” rotation only when in profit and with a clear edge. Net-positive symbols are not blocked in symbol memory.",
            },
        ],
    },
    {
        "date": "2026-07-14",
        "entries": [
            {
                "title": "Tyhjän salkun idle-cash deploy",
                "body": "Kun salkku on tyhjä ja käteistä on yli 35 %, botti voi ostaa parhaan ranked_buyable-kohteen vaikka Gemini-top pick olisi estetty. Score-rangaistus (symbolimuisti) kevenee — krooniset häviäjät ja cooldown pysyvät estettyinä.",
                "title_en": "Empty portfolio idle-cash deploy",
                "body_en": "When the portfolio is empty and cash is over 35%, the bot can buy the best ranked_buyable target even if the Gemini top pick is blocked. Score penalty (symbol memory) is eased — chronic losers and cooldown remain blocked.",
            },
        ],
    },
    {
        "date": "2026-07-12",
        "entries": [
            {
                "title": "Gemini pick-suodatus: micro fail-closed",
                "body": "Pickit ja scan leaders käyttävät samaa blocks_entry-logiikkaa kuin live-ostot — ilman microChecked-tarkistusta ehdotusta ei näytetä.",
                "title_en": "Gemini pick filtering: micro fail-closed",
                "body_en": "Picks and scan leaders use the same blocks_entry logic as live buys — without a microChecked check, a suggestion is not shown.",
            },
            {
                "title": "Muutosloki-linkki etusivun footeriin",
                "body": "Selkeä linkki “Muutokset ja päivitykset” sivun alareunassa.",
                "title_en": "Changelog link in the homepage footer",
                "body_en": "Clear “Changes and updates” link at the bottom of the page.",
            },
            {
                "title": "Muutosloki-sivu (/muutokset)",
                "body": "Uusi sivu kaikille julkaistuille muutoksille päivämäärittäin.",
                "title_en": "Changelog page (/muutokset)",
                "body_en": "New page listing all published changes by date.",
            },
            {
                "title": "Oppimisroadmap-skripti päivitetty",
                "body": "Synkassa tuotannon metriikoiden kanssa — näyttää mitkä oppimisvaiheet ovat valmiina.",
                "title_en": "Learning roadmap script updated",
                "body_en": "In sync with production metrics — shows which learning stages are complete.",
            },
        ],
    },
    {
        "date": "2026-07-11",
        "entries": [
            {
                "title": "Deploy C — setup-oppiminen Geminin kaupankäyntipromptiin",
                "body": "Estetyt setupit, voittajat/häviäjät ja pick_scorecardin micro-bucketit (book/flow/crowd).",
                "title_en": "Deploy C — setup learning in Gemini’s trading prompt",
                "body_en": "Blocked setups, winners/losers, and pick_scorecard micro-buckets (book/flow/crowd).",
            },
            {
                "title": "Deploy B — microstructure Geminin valintapromptiin",
                "body": "Order book, trade flow ja crowd -kentät markkinadatassa; micro-estetyt pickit suodatetaan.",
                "title_en": "Deploy B — microstructure in Gemini’s selection prompt",
                "body_en": "Order book, trade flow, and crowd fields in market data; micro-blocked picks are filtered out.",
            },
            {
                "title": "Deploy A — varjo-oppiminen ennen Geminia",
                "body": "Gemini näkee saman condAdjust/condBlocked-datan kuin moottori; top_picks suodatetaan volyymin ja hinnan mukaan.",
                "title_en": "Deploy A — shadow learning before Gemini",
                "body_en": "Gemini sees the same condAdjust/condBlocked data as the engine; top_picks are filtered by volume and price.",
            },
        ],
    },
    {
        "date": "2026-07-07",
        "entries": [
            {
                "title": "Turvallisuuskovennus",
                "body": "ALLOWED_HOSTS, SECRET_KEY-tarkistus ja rate limitit export-endpointeihin.",
                "title_en": "Security hardening",
                "body_en": "ALLOWED_HOSTS, SECRET_KEY checks, and rate limits on export endpoints.",
            },
            {
                "title": "Yhteystiedot headeriin",
                "body": "Sähköpostilinkki botin URL:n tilalle.",
                "title_en": "Contact details in the header",
                "body_en": "Email link instead of the bot URL.",
            },
            {
                "title": "CI-korjaus",
                "body": "GitHub Actions Django-check toimii tuotanto-SECRET_KEY-asetuksella.",
                "title_en": "CI fix",
                "body_en": "GitHub Actions Django check works with the production SECRET_KEY setting.",
            },
        ],
    },
    {
        "date": "2026-07-06",
        "entries": [
            {
                "title": "GitHub Actions CI",
                "body": "Automaattinen Django-check ja testit jokaisella pushilla.",
                "title_en": "GitHub Actions CI",
                "body_en": "Automatic Django check and tests on every push.",
            },
            {
                "title": "Microstructure fail-closed",
                "body": "Ostoja ei sallita ilman order book -tarkistusta; testit ja regressiosuojat.",
                "title_en": "Microstructure fail-closed",
                "body_en": "Buys are not allowed without an order book check; tests and regression guards.",
            },
            {
                "title": "Bitfinex trade flow",
                "body": "Aggressiivinen osto-/myyntivirta (1 min / 5 min) entry-scoringiin ja setup-avaimiin.",
                "title_en": "Bitfinex trade flow",
                "body_en": "Aggressive buy/sell flow (1 min / 5 min) for entry scoring and setup keys.",
            },
            {
                "title": "Varjosalkku varjopolitiikalle",
                "body": "Luotettavampi vertailu live vs. simuloitu päiväpolitiikka.",
                "title_en": "Shadow portfolio for shadow policy",
                "body_en": "More reliable comparison of live vs. simulated day policy.",
            },
            {
                "title": "Minimi volyymi 200 k€",
                "body": "Uusille ostoille korkeampi likviditeettikynnys; order book -syvyys estää illiquid-trap-ostot.",
                "title_en": "Minimum volume €200k",
                "body_en": "Higher liquidity threshold for new buys; order book depth blocks illiquid-trap buys.",
            },
            {
                "title": "Gemini-narratiivi",
                "body": "Uudelleenyritto 10 min välein API-virheiden jälkeen; kilpailutilanteiden korjaukset.",
                "title_en": "Gemini narrative",
                "body_en": "Retry every 10 minutes after API errors; race-condition fixes.",
            },
            {
                "title": "Tilanhallinnan kovennus",
                "body": "Rinnakkaistallennus ja hintavirheiden jälkeinen palautuminen.",
                "title_en": "State management hardening",
                "body_en": "Concurrent saves and recovery after price errors.",
            },
        ],
    },
    {
        "date": "2026-07-05",
        "entries": [
            {
                "title": "SEO ja löydettävyys",
                "body": "robots.txt, sitemap.xml, parannetut meta-tagit, llms.txt, Schema.org JSON-LD.",
                "title_en": "SEO and discoverability",
                "body_en": "robots.txt, sitemap.xml, improved meta tags, llms.txt, Schema.org JSON-LD.",
            },
            {
                "title": "Karhu-puolustus",
                "body": "Tappiollisen rotaation leikkaus ja käteisvaranto trimmaus laskumarkkinassa.",
                "title_en": "Bear defense",
                "body_en": "Cutting losing rotation and trimming the cash reserve in a down market.",
            },
            {
                "title": "Microstructure voitto-otoissa",
                "body": "Order book ja varjo-oppiminen tiukentavat huippumyyntiä omistuksissa.",
                "title_en": "Microstructure in profit-taking",
                "body_en": "Order book and shadow learning tighten peak sells on holdings.",
            },
        ],
    },
    {
        "date": "2026-06-16",
        "entries": [
            {
                "title": "Gemini-narratiivi",
                "body": "Kiintiö- ja uudelleenyritto-virheiden käsittely parannettu.",
                "title_en": "Gemini narrative",
                "body_en": "Improved handling of quota and retry errors.",
            },
        ],
    },
    {
        "date": "2026-06-15",
        "entries": [
            {
                "title": "Bull-satelliitti",
                "body": "Käteinen jaetaan 65 % ydin + 35 % paras momentum-kohde ilman rotaatiota nousumarkkinassa.",
                "title_en": "Bull satellite",
                "body_en": "Cash is split 65% core + 35% best momentum target without rotation in a bull market.",
            },
            {
                "title": "Positiomäärä regiimin mukaan",
                "body": "Karhu/neutral max 2, nouseva max 3 kryptoa kerrallaan.",
                "title_en": "Position count by regime",
                "body_en": "Bear/neutral max 2, rising max 3 cryptos at a time.",
            },
        ],
    },
    {
        "date": "2026-06-14",
        "entries": [
            {
                "title": "Regiimi-ennakointi",
                "body": "Siirtymävaiheet (bull→bear jne.) vaikuttavat tasapainotukseen ja voitto-ottoon; chip UI:ssa.",
                "title_en": "Regime foresight",
                "body_en": "Transition phases (bull→bear etc.) affect rebalancing and profit-taking; chip in the UI.",
            },
            {
                "title": "Myyntitulosten oppiminen",
                "body": "Voitto/tappio-kategoriat raporttiin ja Gemini-narratiiviin.",
                "title_en": "Sell outcome learning",
                "body_en": "Win/loss categories in the report and Gemini narrative.",
            },
            {
                "title": "Huippumyynti-oppiminen",
                "body": "Exit-setupit ja giveback-analyysi myyntien jälkeen.",
                "title_en": "Peak-sell learning",
                "body_en": "Exit setups and giveback analysis after sells.",
            },
            {
                "title": "Gemini-pick FIFO-linkitys",
                "body": "Pick_scorecard käyttää toteutuneiden kauppojen P/L:ää hypoteettisen sijaan.",
                "title_en": "Gemini pick FIFO linking",
                "body_en": "Pick_scorecard uses realized trade P/L instead of hypothetical.",
            },
            {
                "title": "1 h muutos markkinalistalla",
                "body": "Top-15 parit päivittyvät joka kierros.",
                "title_en": "1 h change on the market list",
                "body_en": "Top-15 pairs update every round.",
            },
        ],
    },
    {
        "date": "2026-06-13",
        "entries": [
            {
                "title": "Bitfinex microstructure",
                "body": "Order book (spread, imbalance, syvyys) ja long/short crowd -signaalit ostoihin.",
                "title_en": "Bitfinex microstructure",
                "body_en": "Order book (spread, imbalance, depth) and long/short crowd signals for buys.",
            },
            {
                "title": "Varjopolitiikka (päivästop & profit lock)",
                "body": "Kerää counterfactual-dataa ilman live-vaikutusta; viisipalstainen UI-paneeli.",
                "title_en": "Shadow policy (day stop & profit lock)",
                "body_en": "Collects counterfactual data without live impact; five-column UI panel.",
            },
            {
                "title": "Historiallinen setup-backfill",
                "body": "5000 kynttilää, round-trip-simulaatio setup-oppimiseen (30 % paino).",
                "title_en": "Historical setup backfill",
                "body_en": "5000 candles, round-trip simulation for setup learning (30% weight).",
            },
            {
                "title": "Gemini-narratiivihistoria",
                "body": "Modal aiempien 6 h raporttien selaamiseen.",
                "title_en": "Gemini narrative history",
                "body_en": "Modal for browsing previous 6 h reports.",
            },
        ],
    },
    {
        "date": "2026-06-12",
        "entries": [
            {
                "title": "Oppimisraportti",
                "body": "Sääntöpohjainen paneeli + 6 h Gemini-kertomus taustalla.",
                "title_en": "Learning report",
                "body_en": "Rule-based panel + 6 h Gemini narrative in the background.",
            },
            {
                "title": "Gemini pick -seuranta",
                "body": "Scorecard: miten edelliset top_pickit menestyivät vs. ohitetut ehdokkaat.",
                "title_en": "Gemini pick tracking",
                "body_en": "Scorecard: how previous top_picks performed vs. skipped candidates.",
            },
            {
                "title": "Markkina-oppimisen backfill",
                "body": "Historialliset kynttilät varjo-oppimiseen; admin-endpoint Railwaylle.",
                "title_en": "Market learning backfill",
                "body_en": "Historical candles for shadow learning; admin endpoint for Railway.",
            },
            {
                "title": "Terveystarkistus /api/health/",
                "body": "DB, worker ja salkun tila diagnostiikkaan.",
                "title_en": "Health check /api/health/",
                "body_en": "DB, worker, and portfolio status for diagnostics.",
            },
            {
                "title": "Sentry",
                "body": "Valinnainen virheseuranta tuotantoon (SENTRY_DSN).",
                "title_en": "Sentry",
                "body_en": "Optional error tracking for production (SENTRY_DSN).",
            },
            {
                "title": "Vero-näyttö",
                "body": "Verot eivät vähennä salkkua; vuosikohtainen erittely.",
                "title_en": "Tax display",
                "body_en": "Taxes do not reduce the portfolio; year-by-year breakdown.",
            },
            {
                "title": "Kaupankäyntikulut nollassa",
                "body": "Bitfinex poisti spot-kulut — rotaatio ilmaista.",
                "title_en": "Trading fees at zero",
                "body_en": "Bitfinex removed spot fees — rotation is free.",
            },
        ],
    },
    {
        "date": "2026-06-11",
        "entries": [
            {
                "title": "Oppimisraportti ja Gemini-kertomus",
                "body": "Ensimmäinen versio oppimispaneelista; 6 h välein uusi narratiivi.",
                "title_en": "Learning report and Gemini narrative",
                "body_en": "First version of the learning panel; new narrative every 6 hours.",
            },
            {
                "title": "Regiimi- ja setup-oppiminen",
                "body": "Kauppakohtainen metadata, regiimikohtainen viritys, huonot setupit estetään.",
                "title_en": "Regime and setup learning",
                "body_en": "Per-trade metadata, regime-specific tuning, bad setups blocked.",
            },
            {
                "title": "Symbolimuisti",
                "body": "Toistuvat häviäjät estetään, voittajia suositaan.",
                "title_en": "Symbol memory",
                "body_en": "Repeat losers are blocked; winners are favored.",
            },
            {
                "title": "Voitto/tappio-erittely",
                "body": "Vuosi/kuukausi/24 h -paneeli realisoituneista kaupoista.",
                "title_en": "Win/loss breakdown",
                "body_en": "Year/month/24 h panel of realized trades.",
            },
            {
                "title": "Max 5 positiota",
                "body": "Gemini ja moottori tukevat jopa viittä kryptoa.",
                "title_en": "Max 5 positions",
                "body_en": "Gemini and the engine support up to five cryptos.",
            },
        ],
    },
    {
        "date": "2026-06-10",
        "entries": [
            {
                "title": "Koko markkinan varjo-oppiminen",
                "body": "Kaikki parit: olosuhde → toteutunut 1 h/4 h tuotto; syöttää rankingiin ja Geminiin.",
                "title_en": "Full-market shadow learning",
                "body_en": "All pairs: condition → realized 1 h/4 h return; feeds ranking and Gemini.",
            },
            {
                "title": "Tuottologiikka v2",
                "body": "ATR-stopit, regiimisuodatin, MTF-vahvistus, fee-tietoinen rotaatio.",
                "title_en": "Profit logic v2",
                "body_en": "ATR stops, regime filter, MTF confirmation, fee-aware rotation.",
            },
            {
                "title": "Voitto-otto trailing",
                "body": "Huipun jälkeinen odotus ja pullback-myynti (+3 % sääntö).",
                "title_en": "Profit-take trailing",
                "body_en": "Wait after the peak and pullback sell (+3% rule).",
            },
            {
                "title": "Gemini-kustannussäästö",
                "body": "Throttle, kevyempi malli ja tiivistetty prompti.",
                "title_en": "Gemini cost savings",
                "body_en": "Throttle, lighter model, and condensed prompt.",
            },
        ],
    },
    {
        "date": "2026-06-09",
        "entries": [
            {
                "title": "Krypto Simulaattori — ensimmäinen julkaisu",
                "body": "Django + Railway + MySQL; Bitfinex-kurssit, 1000 € paper-salkku, 24/7 botti.",
                "title_en": "Crypto Simulator — first release",
                "body_en": "Django + Railway + MySQL; Bitfinex prices, €1000 paper portfolio, 24/7 bot.",
            },
            {
                "title": "Gemini AI kaupankäyntiin",
                "body": "Top picks, signaalit, allokaatiot ja suomenkieliset perustelut.",
                "title_en": "Gemini AI for trading",
                "body_en": "Top picks, signals, allocations, and Finnish-language rationale.",
            },
            {
                "title": "Tekninen analyysi",
                "body": "RSI, EMA, momentum, stop-loss, stablecoin-estot.",
                "title_en": "Technical analysis",
                "body_en": "RSI, EMA, momentum, stop-loss, stablecoin blocks.",
            },
            {
                "title": "Veroraportti ja Excel-vienti",
                "body": "30 % vero voitoista; kauppahistoria päivämäärineen.",
                "title_en": "Tax report and Excel export",
                "body_en": "30% tax on profits; trade history with dates.",
            },
            {
                "title": "Live UI",
                "body": "Salkku, markkinat, kauppaloki ja botin tila reaaliajassa.",
                "title_en": "Live UI",
                "body_en": "Portfolio, markets, trade log, and bot status in real time.",
            },
        ],
    },
]


def changelog_days() -> list[Day]:
    """Palauta päivät uusimmasta vanhimpaan."""
    return sorted(CHANGELOG, key=lambda d: d["date"], reverse=True)


def changelog_days_localized(lang: str = "fi") -> list[Day]:
    """Return days newest-first with title/body localized for lang ('fi'|'en')."""
    days = []
    for day in changelog_days():
        entries = []
        for e in day["entries"]:
            if lang == "en":
                entries.append({
                    "title": e.get("title_en") or e["title"],
                    "body": e.get("body_en") or e.get("body") or "",
                })
            else:
                entries.append({
                    "title": e["title"],
                    "body": e.get("body") or "",
                })
        days.append({"date": day["date"], "entries": entries})
    return days
