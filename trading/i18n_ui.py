"""UI-merkkijonot etusivun templatelle (fi / en)."""

from __future__ import annotations

PAGE_UI: dict[str, dict[str, str]] = {
    "fi": {
        "html_lang": "fi",
        "og_locale": "fi_FI",
        "title": "Krypto Simulaattori — ilmainen live-kryptobotti | hiekkalaatikko.pro",
        "description": (
            "Ilmainen krypto simulaattori ja paperikaupankäynti: live-kryptobotti käy kauppaa "
            "Bitfinex-kursseilla 1000 € virtuaalisalkulla. Tekninen analyysi + Gemini AI."
        ),
        "og_title": "Live-kryptobotti — simuloitu kaupankäynti Bitfinexillä",
        "og_description": (
            "Avoin kryptovaluutta-simulaattori: live-botti, Bitfinex-kurssit ja oppiva AI. "
            "Simuloitu kaupankäynti ilman taloudellista riskiä."
        ),
        "twitter_description": (
            "Seuraa live-kryptobottia 1000 € virtuaalisalkulla. Bitfinex, tekninen analyysi "
            "ja Gemini AI — ilmainen demo."
        ),
        "h1": "Krypto Simulaattori",
        "live_badge": "● Live — automaattinen",
        "ai_badge": "Tekninen AI",
        "subtitle": "Bitfinex-kurssit · AI-kaupankäynti · 1000 € · Live 24/7",
        "lang_switch_label": "EN",
        "lang_switch_href": "/eng/",
        "lang_switch_title": "English version",
        "lang": "fi",
        "lang_fi_href": "/",
        "lang_en_href": "/eng/",
        "lang_fi_title": "Suomi",
        "lang_en_title": "English",
        "stat_portfolio": "Salkun arvo yhteensä",
        "stat_breakdown": "Käteinen + kryptot",
        "stat_crypto": "Kryptoissa",
        "stat_cash": "Vapaa käteinen: 0,00 €",
        "stat_tax": "Vero myyntivoitoista (30 %)",
        "stat_tax_estimate": "Arvio avoimista: 0,00 €",
        "stat_trades": "Kauppoja",
        "stat_trades_month": "Tässä kuussa: 0",
        "stat_trades_24h": "Viime 24 h: 0",
        "stat_next": "Seuraava analyysi",
        "shadow_today": "Varjosalkku · tänään",
        "shadow_live_today": "Live tänään —",
        "shadow_year": "Varjosalkku · vuosi",
        "shadow_live_year": "Live vuosi —",
        "shadow_sim": "Simulaation tila",
        "shadow_thresholds": "Stop −1 % · lock +0,5 / +1 %",
        "shadow_vs_live": "Varjo vs. live (salkku)",
        "shadow_parallel": "Rinnakkaisvarjosalkku",
        "shadow_would_block": "Olisiko estetty",
        "shadow_buys_sells": "Ostot · myynnit",
        "shadow_data": "Kerätty data",
        "shadow_mirrored": "Peilatut / ohitetut kaupat —",
        "wl_title": "Myyntien tulos — vain realisoidut",
        "wl_note": "Avoimet positiot: — · Kaikki myynnit: —",
        "wl_wins": "Voitolliset",
        "wl_losses": "Tappiolliset",
        "wl_net": "Yhteensä",
        "wl_year": "Tänä vuonna",
        "wl_month": "Tässä kuussa",
        "wl_day": "Viime 24 h",
        "markets_h2": "Markkinat (Bitfinex)",
        "markets_updating": "Päivitetään…",
        "markets_search": "Hae kryptoa (esim. BTC, SOL)…",
        "markets_count": "0 kryptoparia",
        "ai_h2": "AI-päätökset",
        "ai_auto": "Automaattinen",
        "ai_placeholder": "Botti käy kauppaa automaattisesti — data päivittyy muutaman sekunnin välein.",
        "portfolio_h2": "Salkku",
        "portfolio_pnl_title": "Avoin voitto/tappio (päivittyy 5 s välein)",
        "th_crypto": "Krypto",
        "th_amount": "Määrä",
        "th_price": "Kurssi",
        "th_value": "Arvo",
        "th_share": "Osuus",
        "th_pnl": "Voitto/tappio (reaaliaika)",
        "th_change24": "Muutos 24h",
        "trades_h2": "Kauppahistoria",
        "trades_filter_aria": "Suodata kauppoja",
        "filter_all": "Kaikki",
        "filter_buys": "Ostot",
        "filter_sells": "Myynnit",
        "export_title": "Lataa Excel verottajalle",
        "export_btn": "⬇ Lataa Excel",
        "trades_empty": "Ei kauppoja vielä.",
        "timeline_h2": "Botin päätökset",
        "timeline_empty": "Ei päätöksiä vielä — botti aloittaa pian.",
        "learning_title": "Oppimisraportti",
        "learning_title_hint": "Näytä aiemmat Gemini-kertomukset",
        "learning_meta": "Päivitetään…",
        "learning_empty": "Oppimisraportti latautuu…",
        "footer_h2": "Mitä hiekkalaatikko.pro on?",
        "footer_p1": (
            "Hiekkalaatikko.pro on avoin kryptovaluutta-<strong>simulaattori</strong> ja "
            "simuloitu kaupankäynti -demo. Sivulla seuraat live-bottia, joka käy kauppaa "
            "<strong>Bitfinexin</strong> reaaliaikaisilla kursseilla noin 1&nbsp;000&nbsp;€ "
            "<strong>virtuaalisalkulla</strong>. Kyseessä ei ole oikea sijoituspalvelu eikä "
            "sivusto anna sijoitusneuvontaa — mitään oikeaa rahaa ei liikuta."
        ),
        "footer_p2": (
            "Botti toimii <strong>24/7</strong>: se analysoi markkinoita teknisillä signaaleilla "
            "(momentum, RSI, moniaikainen trendi, order book) ja tekee ostoja, myyntejä ja "
            "voittojen kotiutusta automaattisesti. <strong>Gemini AI</strong> voi täydentää "
            "päätöksiä, ja järjestelmä <strong>oppii</strong> omista kaupoistaan — huonot "
            "setupit estetään, hyviä painotetaan ja regiimeihin (nousu, lasku, neutraali) "
            "sopeutetaan riskinhallintaa."
        ),
        "footer_p3": (
            "Sivu sopii erityisesti kryptomarkkinoiden seurantaan, algoritmisen kaupankäynnin "
            "kokeiluun ja oppimiseen ilman taloudellista riskiä. Voit seurata salkun arvoa, "
            "kauppahistoriaa, oppimisraportteja ja botin päätöksiä reaaliajassa selaimessa."
        ),
        "footer_nav_aria": "Sivuston linkit",
        "footer_changelog": "Muutokset ja päivitykset",
        "footer_changelog_href": "/muutokset/",
        "share_label": "Jaa sivu:",
        "share_nav_aria": "Jaa somessa",
        "share_whatsapp": "Jaa WhatsAppissa",
        "share_facebook": "Jaa Facebookissa",
        "share_x": "Jaa X:ssä",
        "share_linkedin": "Jaa LinkedInissä",
        "modal_title": "Geminin kertomukset",
        "modal_close": "Sulje",
        "modal_search": "Hae päivämäärällä tai tekstillä…",
        "modal_list_aria": "Kertomuslista",
    },
    "en": {
        "html_lang": "en",
        "og_locale": "en_US",
        "title": "Crypto Simulator — free live crypto bot | hiekkalaatikko.pro",
        "description": (
            "Free crypto simulator for paper trading: a live bot trades Bitfinex prices "
            "with a €1000 virtual portfolio. Technical analysis + Gemini AI, no real money."
        ),
        "og_title": "Live crypto bot — simulated Bitfinex trading",
        "og_description": (
            "Open crypto simulator: live bot, Bitfinex prices and learning AI. "
            "Paper trading with no financial risk."
        ),
        "twitter_description": (
            "Follow a live crypto bot with a €1000 virtual portfolio. Bitfinex, "
            "technical analysis and Gemini AI — free demo."
        ),
        "h1": "Crypto Simulator",
        "live_badge": "● Live — automatic",
        "ai_badge": "Technical AI",
        "subtitle": "Bitfinex prices · AI trading · €1000 · Live 24/7",
        "lang_switch_label": "FI",
        "lang_switch_href": "/",
        "lang_switch_title": "Suomenkielinen versio",
        "lang": "en",
        "lang_fi_href": "/",
        "lang_en_href": "/eng/",
        "lang_fi_title": "Suomi",
        "lang_en_title": "English",
        "stat_portfolio": "Total portfolio value",
        "stat_breakdown": "Cash + crypto",
        "stat_crypto": "In crypto",
        "stat_cash": "Free cash: €0.00",
        "stat_tax": "Tax on gains (30%)",
        "stat_tax_estimate": "Estimate if sold now: €0.00",
        "stat_trades": "Trades",
        "stat_trades_month": "This month: 0",
        "stat_trades_24h": "Last 24 h: 0",
        "stat_next": "Next analysis",
        "shadow_today": "Shadow portfolio · today",
        "shadow_live_today": "Live today —",
        "shadow_year": "Shadow portfolio · year",
        "shadow_live_year": "Live year —",
        "shadow_sim": "Simulation status",
        "shadow_thresholds": "Stop −1% · lock +0.5 / +1%",
        "shadow_vs_live": "Shadow vs live (portfolio)",
        "shadow_parallel": "Parallel shadow portfolio",
        "shadow_would_block": "Would have blocked",
        "shadow_buys_sells": "Buys · sells",
        "shadow_data": "Collected data",
        "shadow_mirrored": "Mirrored / skipped trades —",
        "wl_title": "Sell results — realized only",
        "wl_note": "Open positions: — · All sells: —",
        "wl_wins": "Winners",
        "wl_losses": "Losers",
        "wl_net": "Total",
        "wl_year": "This year",
        "wl_month": "This month",
        "wl_day": "Last 24 h",
        "markets_h2": "Markets (Bitfinex)",
        "markets_updating": "Updating…",
        "markets_search": "Search crypto (e.g. BTC, SOL)…",
        "markets_count": "0 crypto pairs",
        "ai_h2": "AI decisions",
        "ai_auto": "Automatic",
        "ai_placeholder": "The bot trades automatically — data refreshes every few seconds.",
        "portfolio_h2": "Portfolio",
        "portfolio_pnl_title": "Open P/L (updates every 5 s)",
        "th_crypto": "Crypto",
        "th_amount": "Amount",
        "th_price": "Price",
        "th_value": "Value",
        "th_share": "Share",
        "th_pnl": "P/L (live)",
        "th_change24": "Change 24h",
        "trades_h2": "Trade history",
        "trades_filter_aria": "Filter trades",
        "filter_all": "All",
        "filter_buys": "Buys",
        "filter_sells": "Sells",
        "export_title": "Download Excel for tax reporting",
        "export_btn": "⬇ Download Excel",
        "trades_empty": "No trades yet.",
        "timeline_h2": "Bot decisions",
        "timeline_empty": "No decisions yet — the bot will start soon.",
        "learning_title": "Learning report",
        "learning_title_hint": "Show previous Gemini narratives",
        "learning_meta": "Updating…",
        "learning_empty": "Loading learning report…",
        "footer_h2": "What is hiekkalaatikko.pro?",
        "footer_p1": (
            "Hiekkalaatikko.pro is an open crypto <strong>simulator</strong> and paper-trading demo. "
            "You follow a live bot that trades on <strong>Bitfinex</strong> real-time prices with an "
            "approx. €1&nbsp;000 <strong>virtual portfolio</strong>. This is not a real investment "
            "service and the site does not give investment advice — no real money moves."
        ),
        "footer_p2": (
            "The bot runs <strong>24/7</strong>: it analyzes markets with technical signals "
            "(momentum, RSI, multi-timeframe trend, order book) and buys, sells and takes profits "
            "automatically. <strong>Gemini AI</strong> can complement decisions, and the system "
            "<strong>learns</strong> from its own trades — bad setups are blocked, good ones are "
            "favored, and risk management adapts to regimes (bull, bear, neutral)."
        ),
        "footer_p3": (
            "The page is especially suited for watching crypto markets, experimenting with "
            "algorithmic trading, and learning without financial risk. You can follow portfolio "
            "value, trade history, learning reports and bot decisions in real time in the browser."
        ),
        "footer_nav_aria": "Site links",
        "footer_changelog": "Changelog",
        "footer_changelog_href": "/changelog/",
        "share_label": "Share this page:",
        "share_nav_aria": "Share on social media",
        "share_whatsapp": "Share on WhatsApp",
        "share_facebook": "Share on Facebook",
        "share_x": "Share on X",
        "share_linkedin": "Share on LinkedIn",
        "modal_title": "Gemini narratives",
        "modal_close": "Close",
        "modal_search": "Search by date or text…",
        "modal_list_aria": "Narrative list",
    },
}


CHANGELOG_UI: dict[str, dict[str, str]] = {
    "fi": {
        "html_lang": "fi",
        "og_locale": "fi_FI",
        "title": "Muutokset ja päivitykset — Krypto Simulaattori | hiekkalaatikko.pro",
        "description": (
            "Krypto Simulaattori -kryptobotin muutosloki: uudet ominaisuudet, tekninen "
            "analyysi ja Gemini AI -päivitykset sekä korjaukset päivämäärittäin."
        ),
        "og_title": "Muutokset ja päivitykset — hiekkalaatikko.pro",
        "og_description": (
            "Kryptosimulaattorin muutosloki: uudet ominaisuudet ja korjaukset päivämäärittäin."
        ),
        "h1": "Muutokset",
        "back": "← Etusivu",
        "back_href": "/",
        "subtitle": "hiekkalaatikko.pro · julkaistut päivitykset · build {build}",
        "intro": "Kaikki merkittävät lisäykset ja korjaukset live-simulaattoriin. Uusin ensin.",
        "footer_back": "← Takaisin Krypto Simulaattoriin",
        "lang_switch_label": "EN",
        "lang_switch_href": "/changelog/",
        "lang_switch_title": "English version",
        "lang": "fi",
        "lang_fi_href": "/muutokset/",
        "lang_en_href": "/changelog/",
        "lang_fi_title": "Suomi",
        "lang_en_title": "English",
    },
    "en": {
        "html_lang": "en",
        "og_locale": "en_US",
        "title": "Changelog — Crypto Simulator | hiekkalaatikko.pro",
        "description": (
            "Crypto Simulator changelog: new features, technical analysis and Gemini AI "
            "updates, and fixes for the live trading bot, by date."
        ),
        "og_title": "Changelog — hiekkalaatikko.pro",
        "og_description": (
            "Crypto simulator changelog: new features and fixes by date."
        ),
        "h1": "Changelog",
        "back": "← Home",
        "back_href": "/eng/",
        "subtitle": "hiekkalaatikko.pro · published updates · build {build}",
        "intro": "All significant additions and fixes to the live simulator. Newest first.",
        "footer_back": "← Back to Crypto Simulator",
        "lang_switch_label": "FI",
        "lang_switch_href": "/muutokset/",
        "lang_switch_title": "Suomenkielinen versio",
        "lang": "en",
        "lang_fi_href": "/muutokset/",
        "lang_en_href": "/changelog/",
        "lang_fi_title": "Suomi",
        "lang_en_title": "English",
    },
}
