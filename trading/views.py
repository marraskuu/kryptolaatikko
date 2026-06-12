import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .services.export_excel import build_tax_excel
from .services.health_check import db_diagnostics, run_health_check
from .services.session_state import build_api_payload
from .services.state_store import load_state

logger = logging.getLogger(__name__)


def index(request):
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

        maybe_wake_bot(state)
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
