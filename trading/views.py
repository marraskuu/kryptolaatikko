import datetime as dt
import json
import logging
import os
import secrets
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .changelog import changelog_days_localized
from .i18n_ui import CHANGELOG_UI, EXPLORER_UI, NAV_UI, PAGE_UI
from .security_utils import admin_task_key, rate_limit_exceeded, read_admin_key_from_request, safe_next_path
from .services.bitfinex import get_crypto_label, is_stablecoin
from .services.export_excel import build_tax_excel
from .services.health_check import db_diagnostics, run_health_check
from .services.session_state import build_api_payload
from .services.state_store import load_state
from .services.strategy_explorer import (
    EXPLORER_MAX_DAYS,
    normalize_base_symbol,
    run_explorer_backtest,
)
from .services.visitor_analytics import (
    _client_ip,
    mark_stats_tracking_pause,
    record_page_visit,
    record_visit_duration,
)

logger = logging.getLogger(__name__)


def _public_site_url(request) -> str:
    domain = (getattr(settings, "CUSTOM_DOMAIN", "") or "").strip().removeprefix("www.")
    if domain:
        return f"https://{domain}"
    return request.build_absolute_uri("/").rstrip("/")


def _site_json_ld(canonical_url: str, *, lang: str = "fi") -> str:
    base_url = canonical_url.rstrip("/") or canonical_url
    if base_url.endswith("/eng"):
        site_root = base_url[: -len("/eng")] or base_url
    else:
        site_root = base_url
    if lang == "en":
        website_desc = (
            "Open crypto simulator: live bot, Bitfinex prices "
            "and an approx. €1000 virtual portfolio. No real money, no investment advice."
        )
        app_name = "Crypto Simulator"
        app_desc = (
            "Simulated crypto trading 24/7: technical analysis, order book, "
            "Gemini AI and a learning bot on Bitfinex real-time prices."
        )
        in_lang = "en-US"
    else:
        website_desc = (
            "Avoin kryptovaluutta-simulaattori: live-botti, Bitfinex-kurssit "
            "ja noin 1000 € virtuaalisalkku. Ei oikeaa rahaa eikä sijoitusneuvontaa."
        )
        app_name = "Krypto Simulaattori"
        app_desc = (
            "Simuloitu kryptokaupankäynti 24/7: tekninen analyysi, order book, "
            "Gemini AI ja oppiva botti Bitfinexin reaaliaikaisilla kursseilla."
        )
        in_lang = "fi-FI"
    graph = [
        {
            "@type": "WebSite",
            "@id": f"{site_root}#website",
            "url": site_root + "/",
            "name": "hiekkalaatikko.pro",
            "description": website_desc,
            "inLanguage": in_lang,
        },
        {
            "@type": "SoftwareApplication",
            "@id": f"{site_root}#app",
            "name": app_name,
            "url": canonical_url,
            "applicationCategory": "FinanceApplication",
            "operatingSystem": "Web",
            "description": app_desc,
            "isAccessibleForFree": True,
            "inLanguage": in_lang,
            "offers": {
                "@type": "Offer",
                "price": "0",
                "priceCurrency": "EUR",
            },
        },
    ]
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


def _llms_txt_body(base_url: str) -> str:
    """llms.txt — lyhyt indeksi AI-agenteille ja crawlersille (https://llmstxt.org/)."""
    home = f"{base_url}/"
    return "\n".join(
        [
            "# hiekkalaatikko.pro — Krypto Simulaattori / Crypto Simulator",
            "",
            "> Avoin kryptovaluutta-simulaattori ja simuloitu kaupankäynti-demo. "
            "Live-botti käy kauppaa Bitfinexin reaaliaikaisilla kursseilla noin "
            "1000 € virtuaalisalkulla. Ei oikeaa rahaa, ei sijoituspalvelua, "
            "ei sijoitusneuvontaa.",
            "",
            "> Open crypto simulator and paper-trading demo. A live bot trades on "
            "Bitfinex real-time prices with an approx. €1000 virtual portfolio. "
            "No real money, no investment service, no investment advice.",
            "",
            "## Public pages",
            "",
            f"- [Etusivu / Home (FI)]({home}): salkku, kauppahistoria, oppimisraportit.",
            f"- [Home (EN)]({base_url}/eng/): English UI for the live bot dashboard.",
            f"- [Muutokset (FI)]({base_url}/muutokset/): julkaisuloki päivämäärittäin.",
            f"- [Changelog (EN)]({base_url}/changelog/): release notes in English.",
            "",
            "## Technical summary",
            "",
            "- Market data: Bitfinex (real-time prices).",
            "- Strategy: technical analysis (momentum, RSI, multi-timeframe trend, order book).",
            "- AI: Gemini may complement decisions; the system learns from its own trades.",
            "- Risk: regimes (bull/bear/neutral), profit-taking, bear defense.",
            "",
            "## Not public",
            "",
            "- `/stats/` — admin visitor stats (login required).",
            "- `/api/` — internal bot API (not documented for external use).",
            "",
            "## Discovery",
            "",
            f"- [Sitemap]({base_url}/sitemap.xml)",
            f"- [robots.txt]({base_url}/robots.txt)",
            "",
        ]
    )


def _share_links(url: str, title: str) -> dict[str, str]:
    """Somejako-osoitteet footerin ikoneille — ei vaadi some-alustan omaa SDK:ta."""
    encoded_url = quote(url, safe="")
    encoded_text = quote(title, safe="")
    return {
        "whatsapp": f"https://wa.me/?text={encoded_text}%20{encoded_url}",
        "facebook": f"https://www.facebook.com/sharer/sharer.php?u={encoded_url}",
        "x": f"https://twitter.com/intent/tweet?url={encoded_url}&text={encoded_text}",
        "linkedin": f"https://www.linkedin.com/sharing/share-offsite/?url={encoded_url}",
    }


def _topbar_context(request, *, lang: str, current: str) -> dict[str, Any]:
    """Hampurilaisvalikko + jakonapit -yläpalkin konteksti (kaikki julkiset sivut).

    Jakonapit jakavat aina etusivun — ei sitä sivua, jolla kävijä sattuu olemaan
    — koska etusivu on se, mitä halutaan levittää somessa."""
    nav = dict(NAV_UI[lang])
    nav["current"] = current
    base = _public_site_url(request)
    home_title = PAGE_UI[lang]["og_title"]
    return {
        "nav": nav,
        "share": _share_links(f"{base}{nav['home_href']}", home_title),
    }


def _render_home(request, *, lang: str):
    visit_id = None
    try:
        visit_id = record_page_visit(request, request.path)
    except Exception:
        logger.exception("Käyntitallennus epäonnistui")
    base = _public_site_url(request)
    canonical_url = f"{base}/eng/" if lang == "en" else f"{base}/"
    ui = PAGE_UI[lang]
    return render(
        request,
        "trading/index.html",
        {
            "visit_id": visit_id,
            "canonical_url": canonical_url,
            "alternate_fi": f"{base}/",
            "alternate_en": f"{base}/eng/",
            "og_image": f"{base}{static('trading/img/og-image.jpg')}",
            "json_ld": _site_json_ld(canonical_url, lang=lang),
            "ui": ui,
            **_topbar_context(request, lang=lang, current="home"),
        },
    )


def index(request):
    return _render_home(request, lang="fi")


def index_en(request):
    return _render_home(request, lang="en")


def _format_changelog_date(iso: str, lang: str = "fi") -> str:
    y, m, d = iso.split("-")
    if lang == "en":
        months = (
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        )
        return f"{months[int(m) - 1]} {int(d)}, {y}"
    return f"{int(d)}.{int(m)}.{y}"


def _render_changelog(request, *, lang: str):
    try:
        record_page_visit(request, request.path)
    except Exception:
        logger.exception("Käyntitallennus epäonnistui")
    days = [
        {
            "date": day["date"],
            "date_display": _format_changelog_date(day["date"], lang),
            "entries": day["entries"],
        }
        for day in changelog_days_localized(lang)
    ]
    base = _public_site_url(request)
    path = "/changelog/" if lang == "en" else "/muutokset/"
    canonical_url = f"{base}{path}"
    ui = dict(CHANGELOG_UI[lang])
    ui["subtitle"] = ui["subtitle"].format(build=settings.APP_BUILD)
    return render(
        request,
        "trading/muutokset.html",
        {
            "days": days,
            "canonical_url": canonical_url,
            "alternate_fi": f"{base}/muutokset/",
            "alternate_en": f"{base}/changelog/",
            "og_image": f"{base}{static('trading/img/og-image.jpg')}",
            "app_build": settings.APP_BUILD,
            "ui": ui,
            **_topbar_context(request, lang=lang, current="changelog"),
        },
    )


def muutokset_page(request):
    return _render_changelog(request, lang="fi")


def changelog_page(request):
    return _render_changelog(request, lang="en")


@csrf_exempt
@require_GET
def api_health(request):
    """
    Terveystarkastus.

    Oletus (kevyt): DB + worker + portfolio — nopea Railway-healthcheck.
    ?deep=1: myös Bitfinex + Gemini.
    """
    deep = request.GET.get("deep", "").lower() in ("1", "true", "yes")
    payload = run_health_check(deep=deep)
    payload["appBuild"] = getattr(settings, "APP_BUILD", "dev")
    status_code = 200 if payload.get("ok") else 503
    response = JsonResponse(payload, status=status_code)
    response["Cache-Control"] = "no-store"
    return response


def _db_diagnostics() -> dict:
    if settings.DEBUG:
        return db_diagnostics()
    from django.db import connection

    engine = connection.settings_dict.get("ENGINE", "")
    short = engine.rsplit(".", 1)[-1]
    return {"engine": short, "persistent": short != "sqlite3"}


@csrf_exempt
@require_GET
def api_state(request):
    state = load_state()
    try:
        from .services.bot_worker import bot_is_stale, bot_stale_seconds, maybe_wake_bot
        from .services.learning_report import kick_narrative_refresh_if_due, persist_ensure_narrative_error_state

        maybe_wake_bot(state)
        persist_ensure_narrative_error_state(state)
        kick_narrative_refresh_if_due()
        state = load_state()
        bot_stale = bot_is_stale(state)
        stale_sec = int(bot_stale_seconds(state))
    except Exception:
        logger.exception("Bot wake failed")
        bot_stale = False
        stale_sec = 0

    payload = build_api_payload(state)
    lang = (request.GET.get("lang") or "fi").strip().lower()
    if lang not in ("fi", "en"):
        lang = "fi"
    if lang == "en":
        try:
            from .services.learning_report import kick_narrative_en_backfill_if_needed

            kick_narrative_en_backfill_if_needed()
        except Exception:
            logger.exception("Narrative EN backfill kick failed")
        from .services.ui_translate import localize_api_payload

        payload = localize_api_payload(payload, "en")
    payload["lang"] = lang
    payload["error"] = state.get("error")
    payload["autoRun"] = True
    payload["db"] = _db_diagnostics()
    payload["appBuild"] = getattr(settings, "APP_BUILD", "dev")
    payload["botStale"] = bot_stale
    payload["botStaleSec"] = stale_sec
    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response["Pragma"] = "no-cache"
    return response


@csrf_exempt
@require_GET
def api_export(request):
    client_ip = _client_ip(request) or "unknown"
    if rate_limit_exceeded("api-export", client_ip, limit=20, window_sec=60):
        return HttpResponse(status=429)

    state = load_state()
    try:
        buffer, filename = build_tax_excel(state["portfolio"])
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _admin_task_key() -> str | None:
    return admin_task_key()


def _check_admin_key(request) -> bool:
    expected = _admin_task_key()
    if not expected:
        return False
    supplied = read_admin_key_from_request(request)
    if not supplied:
        return False
    return secrets.compare_digest(supplied, expected)


def _stats_login_url(next_path: str = "/stats/") -> str:
    from urllib.parse import quote

    return f"/stats/login/?next={quote(next_path, safe='')}"


def _require_stats_superuser(request) -> HttpResponse | None:
    if not request.user.is_authenticated:
        query = request.GET.urlencode()
        next_path = request.path + (f"?{query}" if query else "")
        return redirect(_stats_login_url(next_path))
    if not request.user.is_superuser:
        return HttpResponse(
            "Vain superuser-käyttäjällä on pääsy tilastoihin.",
            status=403,
            content_type="text/plain; charset=utf-8",
        )
    return None


@csrf_exempt
@require_GET
def api_historical_backfill(request):
    """
    Historiallinen backfill ilman Railway-konsolia.

    GET /api/admin/historical-backfill/?key=...&force=1
    Avain: X-Admin-Task-Key-header (suositus) tai ?key= — tuotannossa johdettu SECRET_KEY:stä jos ADMIN_TASK_KEY puuttuu.
    Oletus: taustasäie (async=1). Tila: /api/state/ marketLearning.
    """
    if not _check_admin_key(request):
        return JsonResponse({"error": "unauthorized"}, status=403)

    from .services.market_learning_backfill import (
        get_backfill_status,
        maybe_schedule_historical_backfill,
        run_historical_backfill,
    )

    force = request.GET.get("force", "").lower() in ("1", "true", "yes")
    run_async = request.GET.get("async", "1").lower() not in ("0", "false", "no")

    if run_async:
        started = maybe_schedule_historical_backfill(force=force)
        payload = {"scheduled": started, "force": force, "async": True}
        payload.update(get_backfill_status())
        return JsonResponse(payload)

    try:
        result = run_historical_backfill()
        from .services.setup_historical_backfill import (
            get_setup_backfill_status,
            run_setup_historical_backfill,
        )

        setup_result = run_setup_historical_backfill()
    except Exception as exc:
        logger.exception("Historical backfill failed")
        return JsonResponse({"error": str(exc)}, status=500)

    payload = {
        "scheduled": False,
        "force": force,
        "async": False,
        "result": result,
        "setupResult": setup_result,
    }
    payload.update(get_backfill_status())
    payload.update(get_setup_backfill_status())
    return JsonResponse(payload)


@csrf_exempt
@require_GET
def api_visitor_stats(request):
    """
    Kävijätilastot (Django PageVisit).

    GET /api/admin/visitor-stats/?key=...&days=30
    """
    if not _check_admin_key(request):
        return JsonResponse({"error": "unauthorized"}, status=403)

    from .services.visitor_analytics import get_visitor_stats

    try:
        days = int(request.GET.get("days", "30"))
    except ValueError:
        days = 30

    payload = get_visitor_stats(days=days)
    payload["appBuild"] = getattr(settings, "APP_BUILD", "dev")
    return JsonResponse(payload)


@require_GET
def robots_txt(request):
    """Hakukoneet: julkinen etusivu, ei stats/API/admin."""
    base = _public_site_url(request)
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /stats/",
        "Disallow: /api/",
        "Disallow: /admin/",
        "",
        f"Sitemap: {base}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@require_GET
def sitemap_xml(request):
    """Julkinen sivusto — etusivu, muutosloki ja Strategy Explorer.

    Etusivun lastmod = tänään (sisältö päivittyy jatkuvasti, live-data).
    Muutosloki- ja Strategy Explorer -sivujen lastmod = uusimman julkaistun
    muutoslokipäivän päivämäärä — ei "tänään" joka pyynnöllä, koska sivut eivät
    oikeasti muutu joka päivä ja väärä lastmod heikentää hakukoneiden
    luottamusta signaaliin.
    """
    base = _public_site_url(request)
    today = timezone.localdate().isoformat()
    changelog_entries = changelog_days_localized("fi")
    changelog_lastmod = changelog_entries[0]["date"] if changelog_entries else today
    urls = [
        (f"{base}/", "daily", "1.0", today),
        (f"{base}/eng/", "daily", "0.9", today),
        (f"{base}/muutokset/", "weekly", "0.6", changelog_lastmod),
        (f"{base}/changelog/", "weekly", "0.6", changelog_lastmod),
        (f"{base}/strategy-explorer/", "weekly", "0.6", changelog_lastmod),
        (f"{base}/strategy-explorer/en/", "weekly", "0.5", changelog_lastmod),
    ]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n',
    ]
    for loc, freq, priority, lastmod in urls:
        parts.append("  <url>\n")
        parts.append(f"    <loc>{loc}</loc>\n")
        parts.append(f"    <lastmod>{lastmod}</lastmod>\n")
        parts.append(f"    <changefreq>{freq}</changefreq>\n")
        parts.append(f"    <priority>{priority}</priority>\n")
        parts.append("  </url>\n")
    parts.append("</urlset>\n")
    return HttpResponse("".join(parts), content_type="application/xml; charset=utf-8")


@require_GET
def llms_txt(request):
    """AI-agentit: lyhyt markdown-indeksi sivustosta (https://llmstxt.org/)."""
    body = _llms_txt_body(_public_site_url(request))
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


GOOGLE_SITE_VERIFICATION_FILE = "google311958127e9d9124.html"
GOOGLE_SITE_VERIFICATION_BODY = (
    f"google-site-verification: {GOOGLE_SITE_VERIFICATION_FILE}"
)


@require_GET
def google_site_verification(request):
    """Google Search Console — HTML-tiedoston vahvistus."""
    return HttpResponse(GOOGLE_SITE_VERIFICATION_BODY, content_type="text/html; charset=utf-8")


@csrf_exempt
@require_http_methods(["POST"])
def api_visit_record(request):
    """
    Varmuuskäynti kun palvelin ei antanut visit-id:tä (esim. Chrome-prerender).
    """
    client_ip = _client_ip(request) or "unknown"
    if rate_limit_exceeded("visit-record", client_ip, limit=30, window_sec=60):
        return JsonResponse({"error": "rate limit"}, status=429)

    visit_id = record_page_visit(request, "/", client_fallback=True)
    if not visit_id:
        return JsonResponse({"recorded": False}, status=204)
    return JsonResponse({"visit_id": visit_id, "recorded": True})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_visit_duration(request):
    """
    Selain raportoi sivullaoloajan poistuessaan tai heartbeatilla.

    GET/POST /api/visit-duration/?id=123&sec=45
    (sendBeacon lähettää POSTin — query string säilyy.)
    """
    client_ip = _client_ip(request) or "unknown"
    if rate_limit_exceeded("visit-duration", client_ip, limit=120, window_sec=60):
        return HttpResponse(status=429)

    try:
        visit_id = int(request.GET.get("id") or request.POST.get("id") or 0)
        duration_sec = int(request.GET.get("sec") or request.POST.get("sec") or 0)
    except (TypeError, ValueError):
        return HttpResponse(status=400)

    if record_visit_duration(visit_id, duration_sec):
        return HttpResponse(status=204)
    return HttpResponse(status=404)


@csrf_exempt
@require_http_methods(["POST"])
def api_share_click(request):
    """Kirjaa klikkaus footerin somejakoikonista (WhatsApp/Facebook/X/LinkedIn)."""
    client_ip = _client_ip(request) or "unknown"
    if rate_limit_exceeded("share-click", client_ip, limit=20, window_sec=60):
        return HttpResponse(status=429)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        payload = {}
    platform = str(payload.get("platform") or request.POST.get("platform") or "").strip()
    lang = str(payload.get("lang") or request.POST.get("lang") or "")

    from .services.visitor_analytics import record_share_click

    if record_share_click(platform, lang):
        return HttpResponse(status=204)
    return HttpResponse(status=400)


def stats_login(request):
    """Kirjautuminen /stats-sivulle (Django superuser)."""
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect(safe_next_path(request.GET.get("next")))

    error = None
    next_url = safe_next_path(request.POST.get("next") or request.GET.get("next"))

    if request.method == "POST":
        client_ip = _client_ip(request) or "unknown"
        if rate_limit_exceeded("stats-login", client_ip, limit=8, window_sec=300):
            error = "Liian monta yritystä — odota hetki ja yritä uudelleen."
        else:
            username = (request.POST.get("username") or "").strip()
            password = request.POST.get("password") or ""
            user = authenticate(request, username=username, password=password)
            if user is not None and user.is_superuser:
                login(request, user)
                return mark_stats_tracking_pause(redirect(next_url))
            error = "Virheellinen tunnus tai salasana — tarvitaan superuser-oikeudet."

    return render(
        request,
        "trading/stats_login.html",
        {"error": error, "next": next_url},
    )


@require_GET
def stats_logout(request):
    logout(request)
    return redirect("/stats/login/")


@require_GET
def stats_page(request):
    """
    Kävijätilastot HTML-sivuna (vaatii superuser-kirjautumisen).

    GET /stats/?days=30
    """
    denied = _require_stats_superuser(request)
    if denied:
        return denied

    try:
        days = int(request.GET.get("days", "30"))
    except ValueError:
        days = 30

    from .services.visitor_analytics import get_share_click_stats, get_stats_page_data

    stats = get_stats_page_data(days=days)
    context = {
        **stats,
        "shareStats": get_share_click_stats(days=days),
        "days": days,
        "app_build": getattr(settings, "APP_BUILD", "dev"),
        "stats_user": request.user.username,
    }
    response = render(request, "trading/stats.html", context)
    response["Cache-Control"] = "no-store"
    return mark_stats_tracking_pause(response)


def _render_strategy_explorer(request, *, lang: str):
    """
    Valitse krypto ja aikaväli, aja botin oikea osto/myynti-logiikka
    historiaan. Ks. services/strategy_explorer.py.
    """
    base = _public_site_url(request)
    ui = EXPLORER_UI[lang]
    canonical_url = f"{base}{ui['lang_en_href' if lang == 'en' else 'lang_fi_href']}"
    return render(
        request,
        "trading/strategy_explorer.html",
        {
            "app_build": getattr(settings, "APP_BUILD", "dev"),
            "canonical_url": canonical_url,
            "alternate_fi": f"{base}/strategy-explorer/",
            "alternate_en": f"{base}/strategy-explorer/en/",
            "og_image": f"{base}{static('trading/img/og-image.jpg')}",
            "ui": ui,
            **_topbar_context(request, lang=lang, current="explorer"),
        },
    )


@require_GET
def strategy_explorer_page(request):
    return _render_strategy_explorer(request, lang="fi")


@require_GET
def strategy_explorer_page_en(request):
    return _render_strategy_explorer(request, lang="en")


@require_GET
def api_strategy_explorer_symbols(request):
    """
    GET /api/strategy-explorer/symbols/

    Tradattavat kryptot (ilman stablecoineja), volyymijärjestyksessä —
    valintalistaa varten. Käyttää bot-loopin jo hakemaa tickers-tilaa,
    ei uutta Bitfinex-kutsua.
    """
    state = load_state()
    tickers = state.get("tickers") or {}
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    ranked = sorted(
        tickers.items(), key=lambda kv: kv[1].get("volumeEur", 0) or 0, reverse=True
    )
    for symbol, ticker in ranked:
        if is_stablecoin(symbol):
            continue
        base = get_crypto_label(symbol)
        if not base or base in seen:
            continue
        seen.add(base)
        items.append({"base": base, "volumeEur": ticker.get("volumeEur", 0) or 0})

    response = JsonResponse({"symbols": items})
    response["Cache-Control"] = "public, max-age=60"
    return response


@require_GET
def api_strategy_explorer(request):
    """
    GET /api/strategy-explorer/?symbol=BTC&start=2026-01-01&end=2026-03-01

    Ajaa yhden kryptoparin backtestin annetulle aikavälille. Tulokset
    välimuistissa tunnin ajan symbol+aikaväli-yhdistelmää kohti, jotta
    Bitfinexiä ei rummuteta samoilla hauilla.
    """
    client_ip = _client_ip(request) or "unknown"
    if rate_limit_exceeded("strategy-explorer", client_ip, limit=10, window_sec=60):
        return JsonResponse({"error": "rate_limit"}, status=429)

    symbol = (request.GET.get("symbol") or "").strip()
    start_str = (request.GET.get("start") or "").strip()
    end_str = (request.GET.get("end") or "").strip()
    if not symbol or not start_str or not end_str:
        return JsonResponse({"error": "missing_params"}, status=400)

    try:
        start_date = dt.datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        end_date = dt.datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return JsonResponse({"error": "bad_date"}, status=400)

    if end_date <= start_date:
        return JsonResponse({"error": "range_order"}, status=400)
    if (end_date - start_date).days > EXPLORER_MAX_DAYS:
        return JsonResponse(
            {"error": "range_too_long", "maxDays": EXPLORER_MAX_DAYS}, status=400
        )

    start_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000) + 24 * 3600 * 1000

    cache_key = f"explorer:{normalize_base_symbol(symbol)}:{start_str}:{end_str}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    try:
        result = run_explorer_backtest(symbol, start_ms, end_ms)
    except Exception:
        logger.exception("Strategy explorer backtest epäonnistui")
        return JsonResponse({"error": "internal_error"}, status=500)

    if "error" in result:
        return JsonResponse(result, status=400)

    cache.set(cache_key, result, 3600)
    return JsonResponse(result)
