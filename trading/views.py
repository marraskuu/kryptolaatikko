import logging
import os
import secrets

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .services.export_excel import build_tax_excel
from .services.health_check import db_diagnostics, run_health_check
from .services.session_state import build_api_payload
from .services.state_store import load_state
from .services.visitor_analytics import record_page_visit

logger = logging.getLogger(__name__)


def index(request):
    try:
        record_page_visit(request)
    except Exception:
        logger.exception("Käyntitallennus epäonnistui")
    return render(request, "trading/index.html")


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
def stats_page(request):
    """
    Kävijätilastot HTML-sivuna.

    GET /stats?key=SECRET_KEY&days=30
    """
    if not _check_admin_key(request):
        return HttpResponse(
            "Ei oikeuksia — avaa /stats?key=SECRET_KEY (tai ADMIN_TASK_KEY)",
            status=403,
            content_type="text/plain; charset=utf-8",
        )

    try:
        days = int(request.GET.get("days", "30"))
    except ValueError:
        days = 30

    try:
        record_page_visit(request, path="/stats")
    except Exception:
        logger.exception("Käyntitallennus /stats epäonnistui")

    from .services.visitor_analytics import get_stats_page_data

    stats = get_stats_page_data(days=days)
    context = {
        **stats,
        "days": days,
        "app_build": getattr(settings, "APP_BUILD", "dev"),
        "stats_key": request.GET.get("key", ""),
    }
    response = render(request, "trading/stats.html", context)
    response["Cache-Control"] = "no-store"
    return response
