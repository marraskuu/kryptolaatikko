import json

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .services.engine import (
    execute_trading_cycle,
    refresh_prices,
    reset_bot,
    start_bot,
    stop_bot,
)
from .services.export_excel import build_tax_excel
from .services.session_state import build_api_payload, load_state


@ensure_csrf_cookie
def index(request):
    return render(request, "trading/index.html")


@require_GET
def api_state(request):
    state = load_state(request.session)
    payload = build_api_payload(state)
    payload["error"] = state.get("error")
    return JsonResponse(payload)


@require_POST
def api_start(request):
    try:
        payload = start_bot(request.session)
        return JsonResponse(payload)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@require_POST
def api_stop(request):
    payload = stop_bot(request.session)
    return JsonResponse(payload)


@require_POST
def api_reset(request):
    payload = reset_bot(request.session)
    return JsonResponse(payload)


@require_POST
def api_prices(request):
    try:
        payload = refresh_prices(request.session)
        return JsonResponse(payload)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@require_POST
def api_trade(request):
    try:
        payload = execute_trading_cycle(request.session)
        return JsonResponse(payload)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@require_GET
def api_export(request):
    state = load_state(request.session)
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
