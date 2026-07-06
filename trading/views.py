import json
import logging
import os
import secrets

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .services.export_excel import build_tax_excel
from .services.health_check import db_diagnostics, run_health_check
from .services.session_state import build_api_payload
from .services.state_store import load_state
from .services.visitor_analytics import (
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


def _site_json_ld(base_url: str) -> str:
    """Schema.org WebSite + SoftwareApplication etusivulle (hakukoneet / AI-tiivistelmät)."""
    graph = [
        {
            "@type": "WebSite",
            "@id": f"{base_url}#website",
            "url": base_url,
            "name": "hiekkalaatikko.pro",
            "description": (
                "Avoin kryptovaluutta-simulaattori: live-botti, Bitfinex-kurssit "
                "ja noin 1000 € virtuaalisalkku. Ei oikeaa rahaa eikä sijoitusneuvontaa."
            ),
            "inLanguage": "fi-FI",
        },
        {
            "@type": "SoftwareApplication",
            "@id": f"{base_url}#app",
            "name": "Krypto Simulaattori",
            "url": base_url,
            "applicationCategory": "FinanceApplication",
            "operatingSystem": "Web",
            "description": (
                "Simuloitu kryptokaupankäynti 24/7: tekninen analyysi, order book, "
                "Gemini AI ja oppiva botti Bitfinexin reaaliaikaisilla kursseilla."
            ),
            "isAccessibleForFree": True,
            "inLanguage": "fi-FI",
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
            "# hiekkalaatikko.pro — Krypto Simulaattori",
            "",
            "> Avoin kryptovaluutta-simulaattori ja simuloitu kaupankäynti-demo. "
            "Live-botti käy kauppaa Bitfinexin reaaliaikaisilla kursseilla noin "
            "1000 € virtuaalisalkulla. Ei oikeaa rahaa, ei sijoituspalvelua, "
            "ei sijoitusneuvontaa.",
            "",
            "## Julkinen sisältö",
            "",
            f"- [Etusivu — live-kryptobotti]({home}): salkku, kauppahistoria, "
            "oppimisraportit, regiimi ja botin päätökset reaaliajassa selaimessa.",
            "",
            "## Tekninen yhteenveto",
            "",
            "- Markkinadata: Bitfinex (reaaliaikaiset kurssit).",
            "- Strategia: tekninen analyysi (momentum, RSI, moniaikainen trendi, order book).",
            "- AI: Gemini voi täydentää päätöksiä; järjestelmä oppii omista kaupoistaan.",
            "- Riskinhallinta: regiimit (nousu, lasku, neutraali), voittojen kotiutus, karhu-puolustus.",
            "",
            "## Ei julkista",
            "",
            "- `/stats/` — ylläpitäjän kävijätilastot (vaatii kirjautumisen).",
            "- `/api/` — botin sisäinen API (ei dokumentoitu ulkoiseen käyttöön).",
            "",
            "## Löydettävyys",
            "",
            f"- [Sitemap]({base_url}/sitemap.xml)",
            f"- [robots.txt]({base_url}/robots.txt)",
            "",
        ]
    )


def index(request):
    visit_id = None
    try:
        visit_id = record_page_visit(request, request.path)
    except Exception:
        logger.exception("Käyntitallennus epäonnistui")
    canonical_url = f"{_public_site_url(request)}/"
    return render(
        request,
        "trading/index.html",
        {
            "visit_id": visit_id,
            "canonical_url": canonical_url,
            "json_ld": _site_json_ld(canonical_url),
        },
    )


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
    return db_diagnostics()


@csrf_exempt
@require_GET
def api_state(request):
    state = load_state()
    try:
        from .services.bot_worker import bot_is_stale, bot_stale_seconds, maybe_wake_bot
        from .services.learning_report import kick_narrative_refresh_if_due

        maybe_wake_bot(state)
        kick_narrative_refresh_if_due()
        state = load_state()
        bot_stale = bot_is_stale(state)
        stale_sec = int(bot_stale_seconds(state))
    except Exception:
        logger.exception("Bot wake failed")
        bot_stale = False
        stale_sec = 0

    payload = build_api_payload(state)
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


def _admin_task_key() -> str:
    return os.environ.get("ADMIN_TASK_KEY") or settings.SECRET_KEY


def _check_admin_key(request) -> bool:
    supplied = request.GET.get("key", "")
    expected = _admin_task_key()
    if not supplied or not expected:
        return False
    return secrets.compare_digest(supplied, expected)


def _stats_login_url(next_path: str = "/stats/") -> str:
    from urllib.parse import quote

    return f"/stats/login/?next={quote(next_path, safe='')}"


def _require_stats_superuser(request) -> HttpResponse | None:
    if not request.user.is_authenticated:
        query = request.GET.urlencode()
        next_path = request.path + (f"?{query}" if query else "")
        return mark_stats_tracking_pause(redirect(_stats_login_url(next_path)))
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

    GET /api/admin/historical-backfill/?key=SECRET_KEY&force=1
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

    GET /api/admin/visitor-stats/?key=SECRET_KEY&days=30
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
    """Yksi julkinen URL — etusivu."""
    base = _public_site_url(request)
    lastmod = timezone.localdate().isoformat()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{base}/</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")


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
@require_http_methods(["GET", "POST"])
def api_visit_duration(request):
    """
    Selain raportoi sivullaoloajan poistuessaan tai heartbeatilla.

    GET/POST /api/visit-duration/?id=123&sec=45
    (sendBeacon lähettää POSTin — query string säilyy.)
    """
    try:
        visit_id = int(request.GET.get("id") or request.POST.get("id") or 0)
        duration_sec = int(request.GET.get("sec") or request.POST.get("sec") or 0)
    except (TypeError, ValueError):
        return HttpResponse(status=400)

    if record_visit_duration(visit_id, duration_sec):
        return HttpResponse(status=204)
    return HttpResponse(status=404)


def stats_login(request):
    """Kirjautuminen /stats-sivulle (Django superuser)."""
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect(request.GET.get("next") or "/stats/")

    error = None
    next_url = request.POST.get("next") or request.GET.get("next") or "/stats/"

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_superuser:
            login(request, user)
            return mark_stats_tracking_pause(redirect(next_url))
        error = "Virheellinen tunnus tai salasana — tarvitaan superuser-oikeudet."

    return mark_stats_tracking_pause(
        render(
            request,
            "trading/stats_login.html",
            {"error": error, "next": next_url},
        )
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

    from .services.visitor_analytics import get_stats_page_data

    stats = get_stats_page_data(days=days)
    context = {
        **stats,
        "days": days,
        "app_build": getattr(settings, "APP_BUILD", "dev"),
        "stats_user": request.user.username,
    }
    response = render(request, "trading/stats.html", context)
    response["Cache-Control"] = "no-store"
    return mark_stats_tracking_pause(response)
