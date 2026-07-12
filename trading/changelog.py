"""
Julkinen muutosloki — /muutokset/

Lisää uusi päivä tai uudet kohdat aina kun teet käyttäjälle näkyvän muutoksen
(deploy, uusi ominaisuus, merkittävä korjaus). Uusin päivä ensin.
"""

from __future__ import annotations

from typing import Any

# title = lyhyt otsikko, body = valinnainen tarkennus
Entry = dict[str, str]
Day = dict[str, Any]

CHANGELOG: list[Day] = [
    {
        "date": "2026-07-12",
        "entries": [
            {
                "title": "Muutosloki-linkki etusivun footeriin",
                "body": "Selkeä linkki “Muutokset ja päivitykset” sivun alareunassa.",
            },
            {
                "title": "Muutosloki-sivu (/muutokset)",
                "body": "Uusi sivu kaikille julkaistuille muutoksille päivämäärittäin.",
            },
            {
                "title": "Oppimisroadmap-skripti päivitetty",
                "body": "Synkassa tuotannon metriikoiden kanssa — näyttää mitkä oppimisvaiheet ovat valmiina.",
            },
        ],
    },
    {
        "date": "2026-07-11",
        "entries": [
            {
                "title": "Deploy C — setup-oppiminen Geminin kaupankäyntipromptiin",
                "body": "Estetyt setupit, voittajat/häviäjät ja pick_scorecardin micro-bucketit (book/flow/crowd).",
            },
            {
                "title": "Deploy B — microstructure Geminin valintapromptiin",
                "body": "Order book, trade flow ja crowd -kentät markkinadatassa; micro-estetyt pickit suodatetaan.",
            },
            {
                "title": "Deploy A — varjo-oppiminen ennen Geminia",
                "body": "Gemini näkee saman condAdjust/condBlocked-datan kuin moottori; top_picks suodatetaan volyymin ja hinnan mukaan.",
            },
        ],
    },
    {
        "date": "2026-07-09",
        "entries": [
            {
                "title": "Kävijäseurannan korjaus",
                "body": "Oikeat käynnit tallentuvat myös prerender- ja IP-virhetilanteissa; asiakaspuolen varmuuskäynti.",
            },
        ],
    },
    {
        "date": "2026-07-07",
        "entries": [
            {
                "title": "Turvallisuuskovennus",
                "body": "ALLOWED_HOSTS, SECRET_KEY-tarkistus, rate limitit stats-kirjautumiseen ja export-endpointeihin.",
            },
            {"title": "Yhteystiedot headeriin", "body": "Sähköpostilinkki botin URL:n tilalle."},
            {"title": "CI-korjaus", "body": "GitHub Actions Django-check toimii tuotanto-SECRET_KEY-asetuksella."},
        ],
    },
    {
        "date": "2026-07-06",
        "entries": [
            {
                "title": "GitHub Actions CI",
                "body": "Automaattinen Django-check ja testit jokaisella pushilla.",
            },
            {
                "title": "Microstructure fail-closed",
                "body": "Ostoja ei sallita ilman order book -tarkistusta; testit ja regressiosuojat.",
            },
            {
                "title": "Bitfinex trade flow",
                "body": "Aggressiivinen osto-/myyntivirta (1 min / 5 min) entry-scoringiin ja setup-avaimiin.",
            },
            {
                "title": "Varjosalkku varjopolitiikalle",
                "body": "Luotettavampi vertailu live vs. simuloitu päiväpolitiikka.",
            },
            {
                "title": "Minimi volyymi 200 k€",
                "body": "Uusille ostoille korkeampi likviditeettikynnys; order book -syvyys estää illiquid-trap-ostot.",
            },
            {
                "title": "Gemini-narratiivi",
                "body": "Uudelleenyritto 10 min välein API-virheiden jälkeen; kilpailutilanteiden korjaukset.",
            },
            {
                "title": "Tilanhallinnan kovennus",
                "body": "Rinnakkaistallennus ja hintavirheiden jälkeinen palautuminen.",
            },
        ],
    },
    {
        "date": "2026-07-05",
        "entries": [
            {
                "title": "Kävijätilastot (/stats)",
                "body": "Suojattu Django-kirjautuminen, päivittäiset käynnit, maat, IP/ISP, kesto ja viisipalkki.",
            },
            {
                "title": "Sivun keston seuranta",
                "body": "Selain lähettää keston poistuessa; keskimääräiset kestot tänään/kuukausi/vuosi.",
            },
            {"title": "SEO ja löydettävyys", "body": "robots.txt, sitemap.xml, parannetut meta-tagit, llms.txt, Schema.org JSON-LD."},
            {
                "title": "Karhu-puolustus",
                "body": "Tappiollisen rotaation leikkaus ja käteisvaranto trimmaus laskumarkkinassa.",
            },
            {
                "title": "Microstructure voitto-otoissa",
                "body": "Order book ja varjo-oppiminen tiukentavat huippumyyntiä omistuksissa.",
            },
        ],
    },
    {
        "date": "2026-06-16",
        "entries": [
            {"title": "Gemini-narratiivi", "body": "Kiintiö- ja uudelleenyritto-virheiden käsittely parannettu."},
        ],
    },
    {
        "date": "2026-06-15",
        "entries": [
            {
                "title": "Bull-satelliitti",
                "body": "Käteinen jaetaan 65 % ydin + 35 % paras momentum-kohde ilman rotaatiota nousumarkkinassa.",
            },
            {
                "title": "Positiomäärä regiimin mukaan",
                "body": "Karhu/neutral max 2, nouseva max 3 kryptoa kerrallaan.",
            },
        ],
    },
    {
        "date": "2026-06-14",
        "entries": [
            {
                "title": "Regiimi-ennakointi",
                "body": "Siirtymävaiheet (bull→bear jne.) vaikuttavat tasapainotukseen ja voitto-ottoon; chip UI:ssa.",
            },
            {
                "title": "Myyntitulosten oppiminen",
                "body": "Voitto/tappio-kategoriat raporttiin ja Gemini-narratiiviin.",
            },
            {
                "title": "Huippumyynti-oppiminen",
                "body": "Exit-setupit ja giveback-analyysi myyntien jälkeen.",
            },
            {
                "title": "Gemini-pick FIFO-linkitys",
                "body": "Pick_scorecard käyttää toteutuneiden kauppojen P/L:ää hypoteettisen sijaan.",
            },
            {"title": "1 h muutos markkinalistalla", "body": "Top-15 parit päivittyvät joka kierros."},
        ],
    },
    {
        "date": "2026-06-13",
        "entries": [
            {
                "title": "Bitfinex microstructure",
                "body": "Order book (spread, imbalance, syvyys) ja long/short crowd -signaalit ostoihin.",
            },
            {
                "title": "Varjopolitiikka (päivästop & profit lock)",
                "body": "Kerää counterfactual-dataa ilman live-vaikutusta; viisipalstainen UI-paneeli.",
            },
            {
                "title": "Historiallinen setup-backfill",
                "body": "5000 kynttilää, round-trip-simulaatio setup-oppimiseen (30 % paino).",
            },
            {
                "title": "Gemini-narratiivihistoria",
                "body": "Modal aiempien 6 h raporttien selaamiseen.",
            },
        ],
    },
    {
        "date": "2026-06-12",
        "entries": [
            {
                "title": "Oppimisraportti",
                "body": "Sääntöpohjainen paneeli + 6 h Gemini-kertomus taustalla.",
            },
            {
                "title": "Gemini pick -seuranta",
                "body": "Scorecard: miten edelliset top_pickit menestyivät vs. ohitetut ehdokkaat.",
            },
            {
                "title": "Markkina-oppimisen backfill",
                "body": "Historialliset kynttilät varjo-oppimiseen; admin-endpoint Railwaylle.",
            },
            {
                "title": "Terveystarkistus /api/health/",
                "body": "DB, worker ja salkun tila diagnostiikkaan.",
            },
            {"title": "Sentry", "body": "Valinnainen virheseuranta tuotantoon (SENTRY_DSN)."},
            {"title": "Vero-näyttö", "body": "Verot eivät vähennä salkkua; vuosikohtainen erittely."},
            {"title": "Kaupankäyntikulut nollassa", "body": "Bitfinex poisti spot-kulut — rotaatio ilmaista."},
        ],
    },
    {
        "date": "2026-06-11",
        "entries": [
            {
                "title": "Oppimisraportti ja Gemini-kertomus",
                "body": "Ensimmäinen versio oppimispaneelista; 6 h välein uusi narratiivi.",
            },
            {
                "title": "Regiimi- ja setup-oppiminen",
                "body": "Kauppakohtainen metadata, regiimikohtainen viritys, huonot setupit estetään.",
            },
            {
                "title": "Symbolimuisti",
                "body": "Toistuvat häviäjät estetään, voittajia suositaan.",
            },
            {
                "title": "Voitto/tappio-erittely",
                "body": "Vuosi/kuukausi/24 h -paneeli realisoituneista kaupoista.",
            },
            {"title": "Max 5 positiota", "body": "Gemini ja moottori tukevat jopa viittä kryptoa."},
        ],
    },
    {
        "date": "2026-06-10",
        "entries": [
            {
                "title": "Koko markkinan varjo-oppiminen",
                "body": "Kaikki parit: olosuhde → toteutunut 1 h/4 h tuotto; syöttää rankingiin ja Geminiin.",
            },
            {
                "title": "Tuottologiikka v2",
                "body": "ATR-stopit, regiimisuodatin, MTF-vahvistus, fee-tietoinen rotaatio.",
            },
            {
                "title": "Voitto-otto trailing",
                "body": "Huipun jälkeinen odotus ja pullback-myynti (+3 % sääntö).",
            },
            {"title": "Gemini-kustannussäästö", "body": "Throttle, kevyempi malli ja tiivistetty prompti."},
        ],
    },
    {
        "date": "2026-06-09",
        "entries": [
            {
                "title": "Krypto Simulaattori — ensimmäinen julkaisu",
                "body": "Django + Railway + MySQL; Bitfinex-kurssit, 1000 € paper-salkku, 24/7 botti.",
            },
            {
                "title": "Gemini AI kaupankäyntiin",
                "body": "Top picks, signaalit, allokaatiot ja suomenkieliset perustelut.",
            },
            {
                "title": "Tekninen analyysi",
                "body": "RSI, EMA, momentum, stop-loss, stablecoin-estot.",
            },
            {"title": "Veroraportti ja Excel-vienti", "body": "30 % vero voitoista; kauppahistoria päivämäärineen."},
            {"title": "Live UI", "body": "Salkku, markkinat, kauppaloki ja botin tila reaaliajassa."},
        ],
    },
]


def changelog_days() -> list[Day]:
    """Palauta päivät uusimmasta vanhimpaan."""
    return sorted(CHANGELOG, key=lambda d: d["date"], reverse=True)
