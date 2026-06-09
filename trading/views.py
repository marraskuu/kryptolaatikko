from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .services.export_excel import build_tax_excel
from .services.session_state import build_api_payload
from .services.state_store import load_state


def index(request):
    return render(request, "trading/index.html")


@csrf_exempt
@require_GET
def api_state(request):
    state = load_state()
    payload = build_api_payload(state)
    payload["error"] = state.get("error")
    payload["autoRun"] = True
    return JsonResponse(payload)


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
