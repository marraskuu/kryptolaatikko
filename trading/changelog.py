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
        "date": "2026-07-27",
        "entries": [
            {
                "title": "Korjaa setup-historiabackfillin hiljainen epäonnistuminen rate limitistä",
                "body": "Viikoittainen historiabackfill hakee kynttilät kahdessa peräkkäisessä vaiheessa (market + setup) samalle ~40 kryptolle — setup-vaihe alkoi heti market-vaiheen perään, jolloin Bitfinexin candle-rajapinnan pyyntöraja (429) osui usein koko setup-vaiheeseen ja se palautti hiljaa tyhjän datan joka viikko. fetch_candles yrittää nyt uudelleen viivästyksellä 429-vastauksen jälkeen sen sijaan että antaisi periksi heti.",
                "title_en": "Fix silent rate-limit failure in setup history backfill",
                "body_en": "The weekly history backfill fetches candles in two back-to-back phases (market + setup) for the same ~40 cryptos — the setup phase started right after the market phase, so Bitfinex's candle endpoint rate limit (429) often hit the entire setup phase, silently returning empty data every week. fetch_candles now retries with a backoff delay after a 429 instead of giving up immediately.",
            },
            {
                "title": "Tiukempi stop-loss-katto isoimpien tappioiden rajoittamiseksi",
                "body": "Stop-loss-katto (suurin sallittu tappio ennen pakkomyyntiä) oli selvästi löysempi kuin voitonoton triggerit — jopa -9 % (bull) / -8 % (neutral) / -5,5 % (bear), kun voitonotto liikkuu vain +1,2–5 %:n haarukassa. Katot tiukennettiin (-7,0 / -6,5 / -5,0 %) ja lisäksi otettiin käyttöön absoluuttinen -7,5 %:n varajarru joka pätee riippumatta regiimistä tai oppimisen hienosäädöstä. Kaikki arvot env-säädettäviä.",
                "title_en": "Tighter stop-loss cap to limit worst-case losses",
                "body_en": "The stop-loss cap (largest loss tolerated before a forced sell) was noticeably looser than the profit-take triggers — up to -9% (bull) / -8% (neutral) / -5.5% (bear), while profit-taking only ranges +1.2–5%. Caps were tightened (-7.0 / -6.5 / -5.0%), plus a new absolute -7.5% backstop that applies regardless of regime or learning-based tuning. All values are env-configurable.",
            },
            {
                "title": "Karhu-jäädytyksen poikkeuskynnys env-säädettäväksi",
                "body": "BEAR_FREEZE_EXCEPTION_MIN_ADJUST (kynnys joka päästää poikkeuksellisen vahvan varjo-oppimis-asetelman läpi karhu-jäädytyksestä) oli kovakoodattu 3.0. Tehtiin env-säädettäväksi, oletus löysempi 2.0. Vaikuttaa käytännössä vain Gemini-aktiiviseen ostopolkuun — tekninen (ei-Gemini) polku ei pääse karhuregiimissä läpi tästä kynnyksestä riippumatta, koska sen oma mtf-ehto on saavuttamaton (dokumentoitu tunnettuna rajoituksena, ei korjattu tässä).",
                "title_en": "Bear-freeze exception threshold made env-configurable",
                "body_en": "BEAR_FREEZE_EXCEPTION_MIN_ADJUST (the threshold letting an exceptionally strong shadow-learned setup through the bear-market buy freeze) was hardcoded at 3.0. Made env-configurable, default loosened to 2.0. In practice only affects the Gemini-active buy path — the technical (non-Gemini) path can't pass through in bear regime regardless of this threshold, since its own mtf condition is unreachable (documented as a known limitation, not fixed here).",
            },
            {
                "title": "Kelly/ATR-varjokoon kytkentä ostohetken kokoon (pois päältä oletuksena)",
                "body": "entry_diagnostics_shadow.py laski jo Kelly-expectancy- ja ATR-painotetun ostokoon jokaiselle kaupalle, mutta tulosta ei koskaan käytetty. Uusi ENTRY_SIZE_KELLY_BLEND_WEIGHT/ENTRY_SIZE_ATR_BLEND_WEIGHT sekoittaa nämä oikeaan eur_amount:iin — voittajat isommiksi, häviäjät pienemmiksi. Oletus 0.0 (ei vaikutusta) kunnes varjodata osoittaa riittävästi näytteitä; sekoitus säilyttää aina kokonaispanostuksen, ei koskaan ohita entry-portteja.",
                "title_en": "Wire Kelly/ATR shadow sizing into entry size (off by default)",
                "body_en": "entry_diagnostics_shadow.py already computed a Kelly-expectancy and ATR-weighted buy size for every trade, but never used the result. New ENTRY_SIZE_KELLY_BLEND_WEIGHT/ENTRY_SIZE_ATR_BLEND_WEIGHT blends these into the real eur_amount — sizing winners bigger, losers smaller. Defaults to 0.0 (no effect) until the shadow data shows enough samples; the blend always preserves total capital deployed and never bypasses any entry gate.",
            },
            {
                "title": "Koulutetun scikit-learn-mallin kytkentä live-pisteytykseen (lipulla, pois päältä)",
                "body": "setup_model.py:n voittotodennäköisyysmalli (manage.py train_setup_model, BotState pk=5) oli koulutettu mutta täysin kytkemättä live-päätöksiin. Nyt SETUP_MODEL_LIVE_ENABLED=1 kytkee sen pisteytykseen condAdjust:n rinnalle uutena modelAdjust-terminä (maltillinen paino, ei vaikutusta jos holdout-AUC on liian matala). Pois päältä oletuksena — riskialtein neljästä tämän päivän muutoksesta, vaatii mallin koulutuksen ensin. Korjattiin myös piilevä feature-skeemavirhe: live-analyysillä ei ollut koskaan emaSpreadPct-avainta, joten malli olisi saanut sen aina tyhjänä ilman korjausta.",
                "title_en": "Wire trained scikit-learn model into live scoring (flagged, off by default)",
                "body_en": "setup_model.py's win-probability model (manage.py train_setup_model, BotState pk=5) was trained but completely disconnected from live decisions. SETUP_MODEL_LIVE_ENABLED=1 now wires it into scoring alongside condAdjust as a new modelAdjust term (modest weight, no effect if holdout AUC is too low). Off by default — the riskiest of today's four changes, requires training the model first. Also fixed a latent feature-schema bug: live analysis never had an emaSpreadPct key, so the model would always have received it blank without this fix.",
            },
        ],
    },
    {
        "date": "2026-07-26",
        "entries": [
            {
                "title": "Kokeellinen scikit-learn-malli setup-historiadatan päälle",
                "body": "Historiallinen backfill (setup_historical_backfill.py) kerää nyt rivikohtaisia feature-näytteitä bucketoidun tilaston rinnalla, ja niistä voi kouluttaa mallin (uusi manage.py train_setup_model / /api/admin/train-model/) ennustamaan kaupan onnistumistodennäköisyyttä. Vain offline-koulutusputki tässä vaiheessa — malli ei vielä vaikuta live-osto/myyntipäätöksiin.",
                "title_en": "Experimental scikit-learn model on top of setup history data",
                "body_en": "Historical backfill (setup_historical_backfill.py) now also collects row-level feature samples alongside the bucketed stats, and a model can be trained on them (new manage.py train_setup_model / /api/admin/train-model/) to predict trade win probability. Offline training pipeline only for now — the model doesn't yet affect live buy/sell decisions.",
            },
            {
                "title": "Isompi automaattinen historiabackfill (viikoittain)",
                "body": "Viikoittainen taustabackfill hakee nyt 40 kryptoa aiemman 20:n sijaan ja 10 000 kynttilää per krypto (n. 416 vrk, aiemmin 5 000 ≈ 208 vrk) — enemmän dataa sekä varjo-oppimiselle että uudelle scikit-learn-mallille joka viikko ilman erillistä käynnistystä.",
                "title_en": "Bigger automatic weekly history backfill",
                "body_en": "The weekly background backfill now fetches 40 cryptos instead of 20, and 10,000 candles per crypto (~416 days, up from 5,000 ≈ 208 days) — more data for both shadow learning and the new scikit-learn model every week with no manual trigger needed.",
            },
        ],
    },
    {
        "date": "2026-07-23",
        "entries": [
            {
                "title": "Korjattu kynttilähistorian haku 4+ kirjaimisille kryptoille",
                "body": "Kryptot, joiden lyhenne on eri kuin 3 merkkiä (esim. AAVE, LINK), eivät hakeneet lainkaan kynttilä-, tilauskirja- tai kauppahistoriaa Bitfinexiltä — API vaatii näille kaksoispisteellisen muodon (tAAVE:USD). Korjattu kaikkialla, myös Strategy Explorerissa.",
                "title_en": "Fixed candle-history fetch for 4+ letter cryptos",
                "body_en": "Cryptos with a ticker other than 3 letters (e.g. AAVE, LINK) weren't fetching any candle, order-book, or trade history from Bitfinex at all — the API requires a colon form for these (tAAVE:USD). Fixed everywhere, including in Strategy Explorer.",
            },
            {
                "title": "Strategy Explorer nyt osa navigaatiota, myös englanniksi",
                "body": "Uusi hampurilaisvalikko ja jakonapit sivujen yläreunassa (etusivu, muutosloki, Strategy Explorer). Strategy Explorerista julkaistiin englanninkielinen versio (/strategy-explorer/en/), ja sivu on nyt hakukoneiden löydettävissä.",
                "title_en": "Strategy Explorer now in the main navigation, and in English",
                "body_en": "A new hamburger menu and share buttons at the top of every page (home, changelog, Strategy Explorer). An English version of Strategy Explorer was published (/strategy-explorer/en/), and the page is now discoverable by search engines.",
            },
        ],
    },
    {
        "date": "2026-07-22",
        "entries": [
            {
                "title": "Uusi \"Botin päätökset\" -aikajana",
                "body": "Botin viimeisimmät päätökset (ostot, myynnit, hold-päätökset, seurannat, Gemini-skannaukset) näkyvät nyt omana, isompana ja selattavana aikajanana Kauppahistorian ja Oppimisraportin välissä — läpinäkyvämpi ja opettavaisempi kuin pelkkä kauppalista. AI-päätökset-paneeli näyttää jatkossa vain viimeisimmän kierroksen otsikon.",
                "title_en": "New \"Bot decisions\" timeline",
                "body_en": "The bot's most recent decisions (buys, sells, holds, watches, Gemini scans) now appear as their own larger, browsable timeline between Trade history and the Learning report — more transparent and informative than a plain trade list. The AI decisions panel now shows only the latest cycle's headline.",
            },
            {
                "title": "Viisi uutta varjoseurantaa oppimisraporttiin",
                "body": "Oppimisraportissa näkyy nyt kolme uutta testidata-korttia: ostohetken korrelaatio- ja kokodiagnostiikka, hintapiikin järkevyystarkistus order bookia vasten sekä rullaava monipäiväinen drawdown-mittari varjopolitiikassa. Kerätään dataa taustalla — ei vielä vaikuta oikeisiin kauppoihin.",
                "title_en": "Five new shadow-tracking diagnostics in the learning report",
                "body_en": "The learning report now shows three new \"test data\" cards: entry-time correlation and sizing diagnostics, a price-spike sanity check against the order book, and a rolling multi-day drawdown flag in the shadow policy. These collect data in the background — they don't affect real trades yet.",
            },
            {
                "title": "Gemini 3.5 Flash Lite oletusmallina",
                "body": "AI-analyysi ja oppimiskertomus käyttävät nyt gemini-3.5-flash-lite -mallia (aiemmin 2.5 Flash Lite).",
                "title_en": "Gemini 3.5 Flash Lite as default model",
                "body_en": "AI analysis and the learning narrative now use the gemini-3.5-flash-lite model (previously 2.5 Flash Lite).",
            },
            {
                "title": "Somejakokuva sekä hakukoneotsikoiden ja -kuvausten viilaus",
                "body": "Linkin jakaminen WhatsAppissa, Facebookissa, X:ssä ja LinkedInissä näyttää nyt kunnollisen esikatselukuvan (og:image). Lisäksi etusivun ja Muutokset-sivun otsikot ja kuvaukset hiottu hakukoneita varten: brändinimi \"Krypto Simulaattori\" mukaan otsikkoon ja hakusanoja (mm. \"paperikaupankäynti\") kuvauksiin.",
                "title_en": "Social share preview image, plus SEO title/description tuning",
                "body_en": "Sharing the link on WhatsApp, Facebook, X and LinkedIn now shows a proper preview image (og:image). Also refined the home page and Changelog page titles/descriptions for search engines: the \"Crypto Simulator\" brand name now appears in the title, and descriptions include keywords like \"paper trading\".",
            },
        ],
    },
    {
        "date": "2026-07-20",
        "entries": [
            {
                "title": "Vero verovuoden nettotuloksesta, ei bruttovoitoista",
                "body": "Vero laskettiin aiemmin jokaisesta voitollisesta myynnistä erikseen nettouttamatta saman vuoden tappioita. Nyt vero lasketaan nettoluovutusvoitosta, ja käyttämätön tappio siirtyy vähennettäväksi seuraavien 5 vuoden voitoista (TVL) — myös ladattava Excel-veroraportti korjattu vastaavasti.",
                "title_en": "Tax on the tax year's net result, not gross wins",
                "body_en": "Tax was previously calculated per winning sale without netting the same year's losses. Tax is now calculated on net capital gains, with unused losses carried forward against the next 5 years' gains — the downloadable Excel tax report was fixed to match.",
            },
            {
                "title": "Myyntisyiden kategorisointi: kolme uutta ryhmää “other”-korista",
                "body": "Karhu-kassavaran trimmaukset, häviäjän vapautukset ja huonon asetelman myynnit erottuvat nyt omiksi kategorioikseen oppimisessa ja raporteissa sen sijaan että niputtuisivat yhteen “other”-koriin (aiemmin suurin häviäjäryhmä, win rate 16 %).",
                "title_en": "Exit reason categories: three new groups split out of “other”",
                "body_en": "Bear cash-reserve trims, loser releases, and bad-setup exits now show up as their own categories in learning and reports instead of being lumped into “other” (previously the biggest losing group, 16% win rate).",
            },
            {
                "title": "Gemini-luottamus: kova esto tappiollisille tasoille",
                "body": "Lievästi tappiollinen luottamustaso esti aiemmin vain puolella ostokoolla — nyt mikä tahansa negatiivinen odotusarvo estää oston kokonaan, ei vain hidasta tappiota.",
                "title_en": "Gemini confidence: hard block for losing levels",
                "body_en": "A mildly losing confidence level previously only halved the buy size — now any negative expected value blocks the buy entirely instead of just slowing the loss.",
            },
            {
                "title": "Symbolimuisti: pysyvä nettotappio estää myös uudet ostot",
                "body": "Esto koski aiemmin vain nopeaa myyntiä olemassa olevalle positiolle — sama kynnys (score ≤ −2.0) estää nyt myös uuden oston, kunnes symbolin nettotulos kääntyy positiiviseksi.",
                "title_en": "Symbol memory: persistent net loss blocks new buys too",
                "body_en": "The block previously only forced a fast exit on an existing position — the same threshold (score ≤ −2.0) now also blocks new buys until the symbol's net result turns positive.",
            },
            {
                "title": "Karhu-jäädytys: kapea poikkeus parhaalle opitulle asetelmalle",
                "body": "Varjo-oppimisen paras löydetty asetelma on ollut nimenomaan karhuregiimissä (+3,19 % / 1h), mutta jäädytys esti sen toteutumisen kokonaan. Osto sallitaan nyt kun opittu signaali on vahvasti positiivinen.",
                "title_en": "Bear freeze: narrow exception for the best learned setup",
                "body_en": "Shadow learning's best-found setup has specifically been in a bear regime (+3.19% / 1h), but the freeze blocked it entirely. Buys are now allowed when the learned signal is strongly positive.",
            },
            {
                "title": "Symbolimuisti: pakkomyynnit pois esto-/rankinglaskennasta",
                "body": "Karhu-kassavaran trimmaukset, aikastopit ja häviäjän vapautukset ovat salkun riskienhallintaa, eivät signaali symbolin laadusta — ne kuitenkin laskettiin mukaan symbolin voitto/tappio-muistiin, jolloin muutaman sentin pakkomyynnit karhuregiimissä saattoivat leimata hyvänkin symbolin krooniseksi häviäjäksi. Nämä kolme kategoriaa jätetään nyt pois symbolimuistin netto- ja estolaskennasta — tuotantodatalla testattuna erottelu tarkentui selvästi kumpaankin suuntaan (mm. eräs symboli nousi piilossa olleesta nollatuloksesta muistin parhaaksi).",
                "title_en": "Symbol memory: forced exits excluded from block/ranking calculation",
                "body_en": "Bear cash-reserve trims, time-stops, and loser releases are portfolio risk management, not a signal about a symbol's quality — but they were still counted in the per-symbol win/loss memory, so a handful of cent-sized forced sells during a bear regime could brand an otherwise good symbol a chronic loser. These three categories are now excluded from the symbol memory's net and block calculation — tested against production data, the split sharpened noticeably in both directions (one symbol went from a hidden breakeven result to the memory's best performer).",
            },
            {
                "title": "Osto estetty myös neutraalissa regiimissä kun crowd on jo pitkänä",
                "body": "Karhuregiimissä on jo aiemmin estetty ostot kun positiomuistin mukaan enemmistö on jo pitkänä (crowd long ≥ 85 %) — data näytti saman ilmiön yhtä selvänä myös neutraalissa regiimissä (122 kauppaa, netto −34,63 €), kun taas bull-regiimissä sama tilanne on ollut kannattava (156 kauppaa, netto +33,68 €) eikä sitä siksi rajoiteta. Esto laajennettiin koskemaan neutraalia regiimiä bullia koskematta.",
                "title_en": "Buys now blocked in neutral regime too when crowd is already long",
                "body_en": "Buys have already been blocked in a bear regime when positioning data shows the crowd is already long (≥85%) — the data showed the same pattern just as clearly in the neutral regime (122 trades, net −€34.63), whereas the same setup has been profitable in a bull regime (156 trades, net +€33.68) and is therefore left untouched. The block now extends to the neutral regime without touching bull.",
            },
        ],
    },
    {
        "date": "2026-07-18",
        "entries": [
            {
                "title": "Korjaus: pitkä pito ei anna Gemini-osamyyntiä ennen trailingia",
                "body": "Kun ≥4 h + fade ohittaa porras 1:n, tier1Taken merkitään heti — Gemini ei voi trimmaa positioita siinä ikkunassa.",
                "title_en": "Fix: long hold blocks Gemini partial sells before trailing",
                "body_en": "When ≥4 h + fade skips tier 1, tier1Taken is set immediately so Gemini cannot trim the position in that window.",
            },
            {
                "title": "Huippumyynti: pitkä pito × porrastettu voitto-otto",
                "body": "≥2 h + hiipuva momentum → isompi porras 1 ja loppu heti trailingiin. ≥4 h + fade → ohita porras 1, myy tiukalla trailingilla koko position.",
                "title_en": "Peak sell: long hold × partial profit-take",
                "body_en": "≥2 h + fading momentum → larger tier-1 take and remainder armed for trailing. ≥4 h + fade → skip tier 1 and trail the full position tightly.",
            },
            {
                "title": "Huippumyynti: aiempi arm pitkille pidoille",
                "body": "Kun positio on ≥2–4 h ja 1h-muutos/flow heikkenee, voitto-otto aktivoituu aiemmin ja trailing kiristyy. Holdingeille haetaan myös trade flow exit-polulla.",
                "title_en": "Peak sell: earlier arm for long holds",
                "body_en": "When a position is held ≥2–4 h and 1h change/flow fades, profit-take arms earlier and trailing tightens. Holdings also get trade flow on the exit path.",
            },
            {
                "title": "Englanninkieliset sivut: regiimi-, satelliitti- ja muutokset-kortit",
                "body": "Oppimisraportin loput kortit ja “muutokset edelliseen” -rivit käännetään /eng-sivulla.",
                "title_en": "English pages: regime, satellite and changes cards",
                "body_en": "Remaining learning-report cards and “changes since previous” lines are translated on /eng.",
            },
            {
                "title": "Englanninkieliset sivut: kauppahistoria ja oppimiskortit",
                "body": "Kauppojen Gemini-perustelut ja oppimisraportin korttien rivit käännetään /eng-sivulla.",
                "title_en": "English pages: trade history and learning cards",
                "body_en": "Gemini trade reasons and learning-report card lines are translated on /eng.",
            },
            {
                "title": "Englanninkieliset sivut: oppimismuistiinpanot ja Gemini-kertomus",
                "body": "Oppimischipin suomenkieliset fragmentit käännetään. Vanha Gemini-kertomus täydennetään eng-kentillä taustalla kun /eng avataan.",
                "title_en": "English pages: learning notes and Gemini narrative",
                "body_en": "Finnish fragments in the learning chip are translated. Opening /eng backfills English fields on the existing Gemini narrative in the background.",
            },
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
