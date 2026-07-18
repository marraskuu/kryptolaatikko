"""Translate Finnish user-visible API strings to English for /eng/ pages."""

from __future__ import annotations

import copy
import re

# Display keys for learning narratives (Finnish keys stay as the UI fields).
_NARRATIVE_FIELDS = (
    "story",
    "intro",
    "learned",
    "in_use",
    "next_steps",
    "ideas",
    "shadow_learned",
    "shadow_ideas",
    "micro_learned",
    "micro_ideas",
    "exit_learned",
    "exit_ideas",
    "sell_learned",
    "sell_ideas",
    "anticipation_learned",
    "anticipation_ideas",
    "satellite_learned",
    "satellite_ideas",
)

# Ordered (pattern, replacement). More specific patterns first.
_REASON_PATTERNS: list[tuple[str, str]] = [
    # Stop-loss
    (
        r"Stop-loss ([\d.+\-]+ %) \(ATR-raja ([\d.+\-]+ %)(, [^)]+)?\) — "
        r"rajataan tappio, pääoma parempaan",
        r"Stop-loss \1 (ATR limit \2\3) — cut loss, capital to better use",
    ),
    (r"rajataan tappio, pääoma parempaan", "cut loss, capital to better use"),
    (r"-regiimi", "-regime"),
    # Fast exits / memory
    (r"Karhu-kassavara", "Bear cash reserve"),
    (r"trimmaus", "trim"),
    (r"kohti ([\d.+\-]+ %) käteistä", r"toward \1 cash"),
    (r"Krooninen häviäjä", "Chronic loser"),
    (r"täysi myynti", "full sell"),
    (r"Symboli cooldownissa", "Symbol in cooldown"),
    (r"Tunnettu häviäjä", "Known loser"),
    (r"Huono markkina-asetelma", "Bad market setup"),
    (r"Huono oma asetelma", "Bad own setup"),
    (r"Estetty kohde — vapautetaan", "Blocked symbol — releasing"),
    (r"\(historia ", "(history "),
    (r"ei valinnoissa — myydään osa", "not in picks — selling part"),
    (r"ei fokuksessa, vapautetaan pääomaa", "not in focus, freeing capital"),
    # Concentration / rebalance / volume
    (r"Keskittymistila — vahva noste", "Concentration mode — strong momentum"),
    (r"Keskittymistila — tappiolla", "Concentration mode — at a loss"),
    (r"Keskittymistila —", "Concentration mode —"),
    (r"Keskittymistila", "Concentration mode"),
    (r"fokus:", "focus:"),
    (r"Tasapainotus — yli tavoitteen", "Rebalance — above target"),
    (r"Matala volyymi", "Low volume"),
    (r"ei kelpaa uusille ostoille", "not eligible for new buys"),
    (r"vapautetaan likvidimpiin kohteisiin", "releasing to more liquid targets"),
    (r"vapautetaan likvidimpiin", "releasing to more liquid"),
    (r"— myydään$", "— selling"),
    (r"\+ tappio ([\d.+\-]+ %) — myydään", r"+ loss \1 — selling"),
    # Stablecoin / illiquid
    (r"Stablecoin — myydään, ei sijoituskohte", "Stablecoin — selling, not an investment target"),
    (r"Stablecoin — myydään", "Stablecoin — selling"),
    (r"Ohut order book", "Thin order book"),
    (r"ostovelk\.\)", "bid depth)"),
    (r"positio ([\d.+\-]+ €) jumi-riski", r"position \1 stuck risk"),
    (r"jumi-riski", "stuck risk"),
    (r"— myydään tappiolla", "— selling at a loss"),
    (r"— vapautetaan pääomaa", "— freeing capital"),
    (r"Ohut myyntitarjonta", "Thin ask depth"),
    (r"Ohut ostovelkainen", "Thin bid depth"),
    (r"— osto estetty", "— buy blocked"),
    # Profit-take variants
    (
        r"Voitto \+([\d.+\-]+ %) — kotiutetaan ([\d.+\-]+ %) \(porras 1\), "
        r"loppu jää trailing-stopille nousua varten",
        r"Profit +\1 — taking \2 (tier 1), rest stays on trailing stop for upside",
    ),
    (
        r"\+([\d.+\-]+ %) — kotiutettu ([\d.+\-]+ %), loppu trailaten",
        r"+\1 — took \2, rest trailing",
    ),
    (
        r"Voitto ([\d.+\-]+ %) — odotetaan \+([\d.+\-]+ %)",
        r"Profit \1 — waiting for +\2",
    ),
    (
        r"Voitto \+([\d.+\-]+ %) — nopea lasku huipusta ([\d.+\-]+ €) "
        r"\(([^)]+)\) → realisoidaan voitto",
        r"Profit +\1 — fast drop from peak \2 (\3) → take profit",
    ),
    (
        r"Voitto \+([\d.+\-]+ %) — nousu tasaantui \(huippu ([\d.+\-]+ €)\), "
        r"trailing-stop ([^→]+) → realisoidaan voitto",
        r"Profit +\1 — rally cooled (peak \2), trailing-stop \3 → take profit",
    ),
    (r"realisoidaan voitto", "take profit"),
    (
        r"\+([\d.+\-]+ %) — nousuputki jatkuu \(huippu ([\d.+\-]+ €)\), pidetään",
        r"+\1 — uptrend continues (peak \2), holding",
    ),
    (
        r"\+([\d.+\-]+ %) — odotetaan tasaantumista (\d+)s \(huippu ([\d.+\-]+ €)\)",
        r"+\1 — waiting to stabilize \2s (peak \3)",
    ),
    (
        r"\+([\d.+\-]+ %) — tasaantunut, trailing-stop ([^)]+) huipusta \(nyt ([^)]+)\)",
        r"+\1 — stabilized, trailing-stop \2 from peak (now \3)",
    ),
    (r"\+([\d.+\-]+ %) — valmis myyntiin", r"+\1 — ready to sell"),
    (r"Voitto \+", "Profit +"),
    (r"huipusta", "from peak"),
    (r"kotiutetaan", "taking"),
    (r"kotiutettu", "took"),
    (r"loppu trailaten", "rest trailing"),
    # Stuck / time-stop
    (r"Jumitus/aikastoppi lykätty", "Stuck/time-stop deferred"),
    (
        r"Myydään jos tappio ≤([\d.+\-]+ %), 24h ≤([\d.+\-]+ %)",
        r"Sell if loss ≤\1, 24h ≤\2",
    ),
    (r"tai pito ≥([\d.+\-]+ h)", r"or hold ≥\1"),
    (r"\(vanhin erä ", "(oldest lot "),
    (r", pakko ≥", ", force ≥"),
    (r"MTF ylös", "MTF up"),
    (
        r"Positio jämähtänyt ≥([\d.+\-]+ h) \(([^)]+)\)( \(vain vanhat lotit\))? — "
        r"myydään riippumatta markkinan noususta",
        r"Position stuck ≥\1 (\2)\3 — selling regardless of market rise",
    ),
    (r"\(vain vanhat lotit\)", "(old lots only)"),
    (
        r"Aikastoppi ≥([\d.+\-]+ h) — jämähtänyt \(([^)]+)\)( \(vain vanhat lotit\))?, "
        r"vapautetaan pääoma vahvempaan kohteeseen",
        r"Time stop ≥\1 — stuck (\2)\3, freeing capital to a stronger target",
    ),
    (r"Positio jämähtänyt", "Position stuck"),
    (r"Aikastoppi", "Time stop"),
    (r"jämähtänyt", "stuck"),
    (r"vapautetaan pääoma vahvempaan kohteeseen", "freeing capital to a stronger target"),
    (r"myydään riippumatta markkinan noususta", "selling regardless of market rise"),
    # Churn / bear / rotation
    (r"Churn-tauko \(30 min\) — ei rotaatiota vielä", "Churn pause (30 min) — no rotation yet"),
    (r"Churn-tauko", "Churn pause"),
    (r"Karhu-puolustus — ei teknistä rotaatiota tappiolla", "Bear defense — no technical rotation at a loss"),
    (r"Karhu-puolustus — ei rotaatiota tappiolla", "Bear defense — no rotation at a loss"),
    (r"Karhu-puolustus", "Bear defense"),
    (
        r"Ei rotaatiota \(([\d.+\-]+ %)\) — kohteella ei selvää",
        r"No rotation (\1) — target has no clear",
    ),
    (
        r"Oppiminen: rotaatio tuottanut tappiota — pidetään ja",
        "Learning: rotation has been losing — holding and",
    ),
    (r"Oppiminen: rotaatio", "Learning: rotation"),
    (
        r"Tappiolla ([\d.+\-]+ %) — myydään osa ja siirretään vahvempaan",
        r"At a loss \1 — selling part and moving to stronger",
    ),
    (r"Tappiolla ", "At a loss "),
    (r"Pidetään — odotetaan parempaa signaalia", "Holding — waiting for a better signal"),
    (r"Pidetään — odotetaan", "Holding — waiting"),
    (
        r"Nousuputki jatkuu \(\+([\d.+\-]+ %) voitolla\) — "
        r"pidetään kunnes tasaantuu tai tulee pieni lasku",
        r"Uptrend continues (+\1 in profit) — holding until it cools or a small drop",
    ),
    (r"Nousuputki jatkuu", "Uptrend continues"),
    (
        r"myydään vasta tasaantumisen tai pienen laskun jälkeen",
        "sell only after cooling or a small drop",
    ),
    # Gemini wrappers / decisions
    (r"Gemini suosittelee osittaista myyntiä", "Gemini recommends partial sell"),
    (r"Gemini myynti \(([\d]+/10)\) estetty", r"Gemini sell (\1) blocked"),
    (r"Gemini myynti estetty", "Gemini sell blocked"),
    (r"Tekninen myynti", "Technical sell"),
    (r"Gemini: avaa salkku", "Gemini: opening portfolio"),
    (r"Gemini: top-valinta", "Gemini: top pick"),
    (r"Gemini: pidä positio", "Gemini: hold position"),
    (r"Gemini: ostoja ja myyntejä", "Gemini: buys and sells"),
    (r"Gemini: pidetään positioita", "Gemini: holding positions"),
    (r"Gemini: ([\d]+) ostoa", r"Gemini: \1 buys"),
    (r"Gemini: ([\d]+) myyntiä", r"Gemini: \1 sells"),
    (r"Gemini-analyysi valmis", "Gemini analysis ready"),
    (r"AI-analyysi valmis", "AI analysis ready"),
    (r"([\d]+) ostoa · ([\d]+) myyntiä · ([\d]+) pidossa", r"\1 buys · \2 sells · \3 holds"),
    (r"Ostetaan ([\d]+) kryptoa", r"Buying \1 cryptos"),
    (r"Myydään ([\d]+) kryptoa", r"Selling \1 cryptos"),
    (r"Ostoja ja myyntejä", "Buys and sells"),
    (r"Pidetään positioita", "Holding positions"),
    (r"Ei uusia kauppoja tällä kierroksella", "No new trades this round"),
    (r"Ei toimenpiteitä", "No action"),
    (r"Odotetaan parempaa signaalia", "Waiting for a better signal"),
    (r"Gemini \(([\d]+/10)\): ", r"Gemini (\1): "),
    (
        r"Gemini skannasi ([\d]+) kryptoparia \(ei stablecoineja\) · "
        r"([\d]+) valintaa · ([\d]+) signaalia",
        r"Gemini scanned \1 crypto pairs (no stablecoins) · \2 picks · \3 signals",
    ),
    (r"Gemini skannasi ([\d]+) kryptoparia", r"Gemini scanned \1 crypto pairs"),
    (r"valintaa", "picks"),
    (r"signaalia", "signals"),
    (r"Gemini odottaa seuraavaa analyysikierrosta", "Gemini waiting for the next analysis round"),
    (r"Gemini ruuhkautunut — yritetään pian uudelleen, käytetään teknistä analyysiä tällä välin",
     "Gemini congested — retrying soon, using technical analysis for now"),
    (r"Gemini-yhteys epäonnistui", "Gemini connection failed"),
    (r"— käytetään teknistä analyysiä", "— using technical analysis"),
    (r"Ei markkinadataa Geminille", "No market data for Gemini"),
    (r"Oppimisraportti päivitetty", "Learning report updated"),
    (r"Gemini-kertomus epäonnistui", "Gemini narrative failed"),
    (r"Oppimisraportin taustapäivitys epäonnistui", "Learning report background update failed"),
    (r"GEMINI_API_KEY puuttuu Railway Variables / \.env",
     "GEMINI_API_KEY missing from Railway Variables / .env"),
    # Buy / allocation
    (r"Alkuallokaatio", "Initial allocation"),
    (r"Bull-satelliitti", "Bull satellite"),
    (r"Vapaa käteinen — myydään kohde johon ei voi lisätä \(hinta/cooldown\)",
     "Free cash — selling target that cannot be added to (price/cooldown)"),
    (r"Vapaa käteinen", "Free cash"),
    (r"Hinta ", "Price "),
    (r"salkun osuus", "portfolio share"),
    # Learning notes
    (r"rotaatio pois", "rotation off"),
    (r"rotaatio ok", "rotation ok"),
    (r"€/kauppa", "€/trade"),
    (r"oppiminen kerää dataa", "learning is collecting data"),
    (r"Gemini tiukemmin", "Gemini stricter"),
    (r"Gemini varovaisemmin", "Gemini more cautious"),
    (r"Gemini ok", "Gemini ok"),
    (r"valikoivampi win rate", "more selective win rate"),
    (r"valikoivampi", "more selective"),
    (r"tarkempi", "more precise"),
    (r"linja ok", "line ok"),
    (r"stop-loss löysempi", "stop-loss looser"),
    (r"stop-loss tiukempi", "stop-loss tighter"),
    (r"micro (.+) voitto-otto (.+) → tiukempi", r"micro \1 profit-take \2 → tighter"),
    (r"micro (.+) voitto-otto (.+) → löysempi", r"micro \1 profit-take \2 → looser"),
    (r"voitto-otto tiukempi", "profit-take tighter"),
    (r"voitto-otto varovainen", "profit-take cautious"),
    (r"voitto-otto löysempi", "profit-take looser"),
    (r"voitto-otto täysi: lukitse voitto aiemmin", "profit-take full: lock profits earlier"),
    (r"voitto-otto täysi: anna voittojen juosta", "profit-take full: let winners run"),
    (r"Gemini estää conf", "Gemini blocks conf"),
    (
        r"Gemini-pickit heikot \(([\d.+\-]+ %) osuu\) — conf ≥([\d]+), osto ([\d]+) %",
        r"Gemini picks weak (\1 hit rate) — conf ≥\2, buy \3 %",
    ),
    (
        r"Gemini-pickit alle normin \(([\d.+\-]+ %) osuu\) — conf ≥([\d]+), osto ([\d]+) %",
        r"Gemini picks below average (\1 hit rate) — conf ≥\2, buy \3 %",
    ),
    (r"Gemini-pickit ok \(([\d.+\-]+ %) osuu\)", r"Gemini picks ok (\1 hit rate)"),
    (
        r"Gemini-pickien keskituotto ([\d.+\-]+ %) — varovaisemmin",
        r"Gemini picks avg return \1 — more cautious",
    ),
    (
        r"Pickit häviävät ohituksille \(([\d.+\-]+ %) kierroksista\)",
        r"Picks lose to skips (\1 of rounds)",
    ),
    (
        r"Gemini-pick-seuranta kerää dataa — odota seuraavaa analyysikierrosta",
        "Gemini pick tracking is collecting data — wait for the next analysis round",
    ),
    (r"Gemini-pick-hillintä", "Gemini pick throttle"),
    (r"regiimioppiminen ([\d]+)/([\d]+) myyntiä", r"regime learning \1/\2 sells"),
    (r"regiimisäätö:", "regime tuning:"),
    (r"asetelmat: ([\d]+) hyvää, ([\d]+) huonoa", r"setups: \1 good, \2 bad"),
    (r"([\d]+) asetelmaa estetty", r"\1 setups blocked"),
    (r"historia ([\d]+) setuppia \(paino ", r"history \1 setups (weight "),
    (r"huippumyynti ([\d]+) exit-setuppia opittu", r"peak sell \1 exit setups learned"),
    (r"huippumyynti ([\d]+) odottaa arviointia", r"peak sell \1 awaiting evaluation"),
    (r"välttää ([\d]+) häviäjää", r"avoids \1 losers"),
    (r"([\d]+) estetty \(toistuva tappio\)", r"\1 blocked (repeat loss)"),
    (r"([\d]+) estetty score", r"\1 blocked score"),
    (r"([\d]+) cooldownissa", r"\1 in cooldown"),
    (r"suosii ([\d]+) voittajaa", r"favors \1 winners"),
    (r"\bosuu\b", "hit rate"),
    (r"\bosto\b", "buy"),
    (r"kierroksista", "of rounds"),
    (r"pickiä", "picks"),
    (r"Pickit ", "Picks "),
    (r"pickit ", "picks "),
    # Report section titles
    (r"Markkina-asetelmat", "Market setups"),
    (r"Kauppojen oppiminen", "Trade learning"),
    (r"Varjopolitiikka \(testidata\)", "Shadow policy (test data)"),
    (r"Huippumyynti", "Peak sells"),
    (r"Voitto- vs tappiomyynnit", "Win vs loss sells"),
    (r"Regiimin ennakointi", "Regime anticipation"),
    (r"Symbolimuisti", "Symbol memory"),
    (r"Sisäänostoasetelmat", "Entry setups"),
    (r"Markkinaregiimi", "Market regime"),
    (r"Tuotto", "Performance"),
    (r"Setup-oppiminen \(omat sisäänostot\)", "Setup learning (own entries)"),
    (r"Setup-oppiminen:", "Setup learning:"),
    (r"Voitto-otto \(kevyt viritys\)", "Profit-take (light tuning)"),
    (r"Voitto-otto \(täysi optimointi\)", "Profit-take (full optimization)"),
    (r"Varjosalkku vs\. live", "Shadow portfolio vs live"),
    (r"Richer markkina-ämpärit", "Richer market buckets"),
    # Roadmap status / progress
    (r"\baktiivinen\b", "active"),
    (r"\btulossa\b", "upcoming"),
    (r"\bkerätään\b", "collecting"),
    (r"\bkäytössä\b", "in use"),
    # Misc fragments often embedded in reasons
    (r"vapautetaan fokuksen kohteisiin", "releasing to focus targets"),
    (r"tappio ([\d.+\-]+ %) — myydään", r"loss \1 — selling"),
    (r"lievä tappio,", "mild loss,"),
    (r"raja ", "limit "),
    (r"leveä spread", "wide spread"),
    (r"nopea lasku", "fast drop"),
    (r"RSI ([\d]+) yliostettu", r"RSI \1 overbought"),
    (r"RSI ([\d]+) korkea", r"RSI \1 high"),
    (r"nousumomentum, voittopotentiaali", "up momentum, profit potential"),
    (r"lievä nousu", "mild rise"),
    (r"pieni dip, varovainen", "small dip, cautious"),
    (r"voimakas lasku, vältä", "sharp drop, avoid"),
    (r"— laskussa", "— declining"),
    (r"yliextended, voitto talteen", "overextended, take profit"),
    (r"vakaa nousu", "steady rise"),
    # Learning report section lines
    (r"uutta markkina-asetelmaa opittu", "new market setups learned"),
    (r"asetelmaa opittu", "setups learned"),
    (r"Paras:", "Best:"),
    (r"Huonoin:", "Worst:"),
    (r"Aikastoppi/jumitus", "Time stop/stuck"),
    (r"Gemini-myynnit", "Gemini sells"),
    (r"Voitto-otto:", "Profit-take:"),
    (r"\bVoitto-otto\b", "Profit-take"),
    (r"\bRotaatio\b", "Rotation"),
    (r"\bkpl\b", "pcs"),
    (r"Oppiminen kerää vielä kauppadataa", "Learning is still collecting trade data"),
    (r"Varjosalkku ([\d.]+) € vs\. live ([\d.]+) € \(ero ", r"Shadow portfolio \1 € vs live \2 € (diff "),
    (r"Varjosalkku vs\. live:", "Shadow portfolio vs live:"),
    (r"Varjopolitiikka: ([\d]+) kauppaa, arvioitu ero ", r"Shadow policy: \1 trades, estimated diff "),
    (r"Varjopolitiikka kerää dataa — liian vähän kauppoja vertailuun",
     "Shadow policy is collecting data — too few trades to compare"),
    (r"Estetyt ostot:", "Blocked buys:"),
    (r"Estetyt myynnit:", "Blocked sells:"),
    (r"Aikaisempi voitto-otto:", "Earlier profit-take:"),
    (r"signaalia \(~", "signals (~"),
    (r"peilattu ([\d]+) kauppaa, ohitettu ([\d]+)\)", r"mirrored \1 trades, skipped \2)"),
    (r"arvio, ei takaa voittoa", "estimate, does not guarantee profit"),
    (r"Estettyjen kauppojen counterfactual-yhteenveto ", "Blocked-trades counterfactual summary "),
    (r"\((\d+) kauppaa\)", r"(\1 trades)"),
    (r"Päivästop/profit-lock olisi välttänyt tappiollisia myyntejä",
     "Day-stop/profit-lock would have avoided losing sells"),
    (r"Ostojen rajoitus olisi säästänyt tappioita",
     "Buy limits would have reduced losses"),
    (r"Varjopolitiikan counterfactual-arvio ", "Shadow policy counterfactual estimate "),
    (r"Viime kierros:", "Last round:"),
    (r"Order book, trade flow ja crowd -data kerätään kierroksittain",
     "Order book, trade flow and crowd data are collected each round"),
    (r"Ei vielä suljettuja kauppoja micro-meta-datalla — keruu alkaa uusista ostoista",
     "No closed trades with micro-meta yet — collection starts with new buys"),
    (r"Suljetut kaupat micro-datalla:", "Closed trades with micro-data:"),
    (r" · netto ", " · net "),
    (r"Ostopaine \(bk\+\):", "Buy pressure (bk+):"),
    (r"Myyntipaine \(bk-\):", "Sell pressure (bk-):"),
    (r"Ostoalotteinen flow \(fl\+\):", "Buy-initiated flow (fl+):"),
    (r"Myyntialotteinen flow \(fl-\):", "Sell-initiated flow (fl-):"),
    (r"Nyt: ", "Now: "),
    (r"\((\d+) kauppaa / 1 min\)", r"(\1 trades / 1 min)"),
    (r"Exit-setuppeja: ([\d]+)/([\d]+) valmiina · ([\d]+) odottaa arviointia",
     r"Exit setups: \1/\2 ready · \3 awaiting evaluation"),
    (r"Huippumyynti-oppiminen pois päältä", "Peak-sell learning disabled"),
    (r"Huippumyynti-oppiminen kerää dataa voitto-otoista",
     "Peak-sell learning is collecting profit-take data"),
    (r"Ei vielä voitto-ottomyyntejä — dynaaminen trailing aktiivinen RSI/MTF/book-signaaleilla",
     "No profit-take sells yet — dynamic trailing active with RSI/MTF/book signals"),
    (r"Voitto-otot:", "Profit-takes:"),
    (r"Exit-metalla:", "With exit meta:"),
    (r"Keskimääräinen giveback myynnissä:", "Average giveback at sell:"),
    (r"huipusta", "from peak"),
    (r"jäi pöydälle", "left on the table"),
    (r"Viime ([\d]+) myyntiä: ([\d]+)V / ([\d]+)T · netto ",
     r"Last \1 sells: \2W / \3L · net "),
    (r"Voitoissa:", "In wins:"),
    (r"Tappioissa:", "In losses:"),
    (r"\bMuu\b", "Other"),
    (r"Suositus:", "Recommendation:"),
    (r"Muut myynnit:", "Other sells:"),
    (r"tappiota, netto ", "losses, net "),
    (r"hillitse tätä myyntityyppiä tappiossa", "curb this sell type when losing"),
    (r"tuottaa keskimäärin ", "averages "),
    (r"€/voitto — suosi tätä polkua kun positio on plussalla",
     "€/win — favor this path when the position is profitable"),
    (r"Voitto-myyntien osuus ", "Winning-sell share "),
    (r"keskity vähentämään tappiollisia pakko-/rotaatiomyyntejä",
     "focus on reducing forced/rotation loss sells"),
    (r"Kerätään lisää myyntidataa ennen vahvoja suosituksia",
     "Collecting more sell data before strong recommendations"),
    (r"Voitoissa annettiin keskimäärin ", "Wins gave back on average "),
    (r" % takaisin huipusta — tiukempi trailing voi parantaa nettotuottoa",
     " % from the peak — tighter trailing may improve net return"),
    (r"Voittojen keskikoko ", "Average win size "),
    (r" on pieni — harkitse pidempää pitoa vahvoissa trendeissä \(bull/regiimi\)",
     " is small — consider longer holds in strong trends (bull/regime)"),
    (r"Rotaatio on jo hillitty oppimisen perusteella — jatka seurantaa",
     "Rotation is already eased by learning — keep monitoring"),
    (r"vaativat vähintään conf ", "require at least conf "),
    (r"— säilytä korkea kynnys", "— keep the high threshold"),
    (r"Myyntijakauma tasapainoinen — jatka nykyistä linjaa ja kerää lisää näytteitä",
     "Sell mix is balanced — keep the current line and gather more samples"),
    (r"Microstructure pois päältä \(MICROSTRUCTURE_ENABLED=0\)",
     "Microstructure disabled (MICROSTRUCTURE_ENABLED=0)"),
    # Gemini free-text fragments (trade reasons)
    (r":llä on vahva ", " has a strong "),
    (r":lla on vahva ", " has a strong "),
    (r"24h muutos", "24h change"),
    (r"1h muutos", "1h change"),
    (r"4h muutos", "4h change"),
    (r"\bmuutos\b", "change"),
    (r"ja positiivinen ", "and positive "),
    (r"on korkea, mutta ei vielä ylikuumentunut", "is high but not yet overheated"),
    (r"on korkea", "is high"),
    (r"ei vielä ylikuumentunut", "not yet overheated"),
    (r"EMA-trendi on bullish", "EMA trend is bullish"),
    (r"EMA-trendi on bearish", "EMA trend is bearish"),
    (r"on vahvasti positiivinen", "is strongly positive"),
    (r"on vahvasti negatiivinen", "is strongly negative"),
    (r"mikä osoittaa aggressiivista ostoa", "which indicates aggressive buying"),
    (r"mikä osoittaa aggressiivista myyntiä", "which indicates aggressive selling"),
    (r"\bVaikka\b", "Although"),
    (r"\bvaikka\b", "although"),
    (r"positiivinen ", "positive "),
    (r"negatiivinen ", "negative "),
    (r"vahva ", "strong "),
    (r"heikko ", "weak "),
    # Regime anticipation / bull satellite / gemini conf / symbol memory / changes
    (r"Regiimi vakaa:", "Regime stable:"),
    (r"Ennakointi:", "Anticipation:"),
    (r"Käytössä:", "In use:"),
    (r"tasapainotus ", "rebalance "),
    (r"voitto-otto ×", "profit-take ×"),
    (r"Myynnit:", "Sells:"),
    (r"ennakoinnissa ", "in anticipation "),
    (r"vakaassa ", "when stable "),
    (r"Phase-meta myynneissä:", "Phase meta on sells:"),
    (r"\(kerätään\)", "(collecting)"),
    (r"Split-jakoja: ([\d]+) \((\d+) auki\)", r"Split allocations: \1 (\2 open)"),
    (r"Kerätään dataa \(([\d]+)/([\d]+) split-tapahtumaa\) ennen vahvoja johtopäätöksiä",
     r"Collecting data (\1/\2 split events) before strong conclusions"),
    (r"Kerätään dataa \(([\d]+)/([\d]+)\) — Gemini-ostot ",
     r"Collecting data (\1/\2) — Gemini buys "),
    (r"Gemini-ostot ", "Gemini buys "),
    (r", Gemini-myynnit ", ", Gemini sells "),
    (r"Oppiminen: sulkeutuneet ostot \+ Gemini-aloittamat myynnit",
     "Learning: closed buys + Gemini-initiated sells"),
    (r"Estetyt confidence-tasot:", "Blocked confidence levels:"),
    (r"Minimi confidence myynneille:", "Minimum confidence for sells:"),
    (r" · estetty", " · blocked"),
    (r" · skaalattu ", " · scaled "),
    (r"Estetty:", "Blocked:"),
    (r"Vältetään:", "Avoiding:"),
    (r"Suositaan:", "Favoring:"),
    (r"Suositellaan:", "Recommended:"),
    (r"Ei symbolikohtaisia estoja tai suosituksia vielä",
     "No symbol-specific blocks or recommendations yet"),
    (r"Aktiivinen regiimi:", "Active regime:"),
    (r"Regiimikohtainen viritys:", "Regime-specific tuning:"),
    (r"tagattua myyntiä", "tagged sells"),
    (r"regiimitagattua myyntiä", "regime-tagged sells"),
    (r"Regiimisäätö aktiivinen", "Regime tuning active"),
    (r"Regiimisäätö:", "Regime tuning:"),
    (r"Viime 24 h:", "Last 24 h:"),
    (r"Viime 7 pv:", "Last 7 d:"),
    (r"Estetty ([\d]+) uutta ostokohdetta:", r"Blocked \1 new buy targets:"),
    (r"Ostokielto poistui:", "Buy block lifted:"),
    (r"\((\d+) yhteensä\)", r"(\1 total)"),
    (r"Kokonaisexpectancy ", "Overall expectancy "),
    (r"Gemini-confidence-esto keveni", "Gemini confidence block eased"),
    (r"Rotaatio kytketty pois oppimisen perusteella",
     "Rotation disabled based on learning"),
    (r"Rotaatio palautettu päälle", "Rotation re-enabled"),
    (r"Sisäänostokynnys score ", "Entry score threshold "),
    (r"Ensimmäinen raportti — vertailukohtaa ei vielä ole\.",
     "First report — no previous baseline yet."),
    (r"Ei merkittäviä säätömuutoksia edelliseen raporttiin\.",
     "No significant tuning changes vs previous report."),
    (r"Strategia käytössä — odottaa ensimmäistä 65/35-jakoa bull-regiimissä",
     "Strategy active — waiting for the first 65/35 split in a bull regime"),
    (r"Bull-satelliitti \(65/35\) — odottaa ensimmäistä jakotilannetta",
     "Bull satellite (65/35) — waiting for the first split opportunity"),
    (r"Vs pelkkä ydin:", "Vs core only:"),
    (r"yhteis etu ", "total edge "),
    (r", keskim\. ", ", avg "),
    (r"Jako on tuottanut keskimäärin ", "Split has averaged "),
    (r" € enemmän kuin pelkkä ydin", " € more than core only"),
    (r"Jako on jäänyt keskimäärin ", "Split has averaged "),
    (r" € alle pelkkä ydin - tiukenna kynnyksiä",
     " € below core only — tighten thresholds"),
    (r"Enemmän voitollisia \((\d+)\) kuin tappiollisia \((\d+)\) vs pelkkä ydin",
     r"More winning (\1) than losing (\2) vs core only"),
    (r"Tappiollisia jakoja \((\d+)\) enemmän kuin voitollisia \((\d+)\)",
     r"More losing splits (\1) than winning (\2)"),
    (r"Tulokset tasaiset — jatketaan seurantaa",
     "Results even — continue monitoring"),
    (r"Nousevaan siirtymässä", "Entering bull"),
    (r"Nouseva muodostumassa", "Bull emerging"),
    (r"Laskevaan siirtymässä", "Entering bear"),
    (r"Laskeva muodostumassa", "Bear emerging"),
    (r"Neutraaliin siirtymässä", "Entering neutral"),
    (r"Neutraali muodostumassa", "Neutral emerging"),
    (r"Ennakointi aktiivinen", "Anticipation active"),
    (r"riskiregiimi ", "risk regime "),
    (r"Ennakointivaiheessa myynnit heikompia kuin vakaassa regiimissä — "
     r"pidä kiinni tiukemmista tappiorajoista \(rotaatio/aikastoppi\)",
     "Sells are weaker in anticipation than in a stable regime — "
     "stick to tighter loss limits (rotation/time-stop)"),
    (r"pito ([\d.]+) h \+ 1h ", r"hold \1 h + 1h "),
    (r"pito ([\d.]+) h \+ myyntiflow", r"hold \1 h + sell flow"),
    (r"pito ([\d.]+) h \+ hiipuva momentum", r"hold \1 h + fading momentum"),
    (r"pitkä pito ([\d.]+) h \+ hiipuva 1h/flow", r"long hold \1 h + fading 1h/flow"),
    (r"pito ([\d.]+) h \+ hiipuva 1h/flow", r"hold \1 h + fading 1h/flow"),
    (r"myyntialotteinen flow", "sell-initiated flow"),
    (r"ostoalotteinen flow", "buy-initiated flow"),
    (r"pitkä pito: ohita porras 1 → trailing",
     "long hold: skip tier 1 → trailing"),
    (r"pito: isompi porras 1 \+ nopea trailing",
     "hold: larger tier 1 + fast trailing"),
    (r"\(porras 1\)", "(tier 1)"),
    (r"loppu jää trailing-stopille nousua varten",
     "rest stays on trailing stop for upside"),
]

_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat), repl) for pat, repl in _REASON_PATTERNS
]


def translate_text(text: str, lang: str = "en") -> str:
    """If lang != 'en' or empty, return text. Else apply ordered regex replacements.

    Gemini free-text is translated with the same patterns (not left as Finnish).
    """
    if lang != "en" or not text:
        return text

    out = text
    for pattern, repl in _COMPILED:
        out = pattern.sub(repl, out)
    return out


def narrative_for_lang(narrative: dict | None, lang: str) -> dict | None:
    """Return narrative with primary fields in requested language.

    Prefer *_en fields when lang=='en'; keep Finnish keys as the display fields.
    """
    if not narrative or not isinstance(narrative, dict):
        return narrative
    out = copy.deepcopy(narrative)
    if lang != "en":
        return out
    for key in _NARRATIVE_FIELDS:
        en_key = f"{key}_en"
        en_val = out.get(en_key)
        if isinstance(en_val, str) and en_val.strip():
            out[key] = en_val
    return out


def localize_api_payload(payload: dict, lang: str) -> dict:
    """Deep-copy and translate user-visible Finnish fields when lang=='en'."""
    data = copy.deepcopy(payload)
    if lang != "en":
        return data

    def _localize_reason(obj: dict) -> None:
        en = obj.get("reasonEn") or obj.get("reason_en")
        if isinstance(en, str) and en.strip():
            obj["reason"] = en
        elif isinstance(obj.get("reason"), str):
            obj["reason"] = translate_text(obj["reason"], lang)

    portfolio = data.get("portfolio")
    if isinstance(portfolio, dict):
        trades = portfolio.get("trades")
        if isinstance(trades, list):
            for trade in trades:
                if isinstance(trade, dict):
                    _localize_reason(trade)

    ai_events = data.get("aiEvents")
    if isinstance(ai_events, list):
        for event in ai_events:
            if isinstance(event, dict):
                _localize_reason(event)

    profit_watch = data.get("profitWatch")
    if isinstance(profit_watch, dict):
        for watch in profit_watch.values():
            if isinstance(watch, dict) and isinstance(watch.get("statusText"), str):
                watch["statusText"] = translate_text(watch["statusText"], lang)
            if isinstance(watch, dict) and isinstance(watch.get("reason"), str):
                watch["reason"] = translate_text(watch["reason"], lang)

    report = data.get("lastAIReport")
    if isinstance(report, dict):
        for key in ("title", "subtitle"):
            if isinstance(report.get(key), str):
                report[key] = translate_text(report[key], lang)
        for list_key in ("buys", "sells", "holds"):
            items = report.get(list_key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        _localize_reason(item)

    learning = data.get("learning")
    if isinstance(learning, dict) and isinstance(learning.get("note"), str):
        learning["note"] = translate_text(learning["note"], lang)

    lr = data.get("learningReport")
    if isinstance(lr, dict):
        sections = lr.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                if isinstance(section.get("title"), str):
                    section["title"] = translate_text(section["title"], lang)
                lines = section.get("lines")
                if isinstance(lines, list):
                    section["lines"] = [
                        translate_text(line, lang) if isinstance(line, str) else line
                        for line in lines
                    ]
        changes = lr.get("changes")
        if isinstance(changes, list):
            lr["changes"] = [
                translate_text(c, lang) if isinstance(c, str) else c for c in changes
            ]
        roadmap = lr.get("roadmap")
        if isinstance(roadmap, list):
            for item in roadmap:
                if not isinstance(item, dict):
                    continue
                for key in ("label", "action", "progress", "status"):
                    if isinstance(item.get(key), str):
                        item[key] = translate_text(item[key], lang)
        if isinstance(lr.get("narrative"), dict):
            lr["narrative"] = narrative_for_lang(lr["narrative"], lang)
        if isinstance(lr.get("narrativeError"), str):
            lr["narrativeError"] = translate_text(lr["narrativeError"], lang)

    if isinstance(data.get("learningNarrative"), dict):
        data["learningNarrative"] = narrative_for_lang(data["learningNarrative"], lang)
    if isinstance(data.get("error"), str):
        data["error"] = translate_text(data["error"], lang)

    history = data.get("geminiNarrativeHistory")
    if isinstance(history, list):
        for entry in history:
            if not isinstance(entry, dict):
                continue
            if isinstance(entry.get("narrative"), dict):
                entry["narrative"] = narrative_for_lang(entry["narrative"], lang)
            else:
                localized = narrative_for_lang(entry, lang)
                if localized is not None:
                    for key in _NARRATIVE_FIELDS:
                        if key in localized:
                            entry[key] = localized[key]

    gemini_status = data.get("geminiStatus")
    if isinstance(gemini_status, dict) and isinstance(gemini_status.get("message"), str):
        gemini_status["message"] = translate_text(gemini_status["message"], lang)

    analyses = data.get("analyses")
    if isinstance(analyses, dict):
        for analysis in analyses.values():
            if not isinstance(analysis, dict):
                continue
            signal = analysis.get("geminiSignal")
            if isinstance(signal, dict):
                en = signal.get("reason_en") or signal.get("reasonEn")
                if isinstance(en, str) and en.strip():
                    signal["reason"] = en
                elif isinstance(signal.get("reason"), str):
                    # Template wrappers only; Finnish free-text stays until reason_en exists.
                    signal["reason"] = translate_text(signal["reason"], lang)

    return data
