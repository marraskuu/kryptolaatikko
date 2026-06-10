from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .services.export_excel import build_tax_excel
from .services.session_state import build_api_payload
from .services.state_store import load_state


def index(request):
    return render(request, "trading/index.html")


def _db_diagnostics() -> dict:
    """Kevyt diagnostiikka: säilyykö tila deployien yli (MySQL) vai ei (SQLite)."""
    import os

    from django.db import connection

    engine = connection.settings_dict.get("ENGINE", "")
    short = engine.rsplit(".", 1)[-1]
    persistent = short not in ("sqlite3",)

    from urllib.parse import urlparse

    url_vars = ("MYSQL_URL", "DATABASE_URL", "MYSQL_PUBLIC_URL")
    parts_vars = ("MYSQLHOST", "MYSQL_HOST", "MYSQLDATABASE", "MYSQL_DATABASE")
    env_present = {k: bool(os.environ.get(k, "").strip()) for k in url_vars + parts_vars}
    unresolved = {
        k: True
        for k in url_vars
        if "${" in os.environ.get(k, "") or "${{" in os.environ.get(k, "")
    }

    url_schemes = {}
    for k in url_vars:
        raw = os.environ.get(k, "").strip()
        if not raw:
            continue
        cleaned = raw.strip("'\"").strip()
        url_schemes[k] = {
            "scheme": urlparse(cleaned).scheme or "(none)",
            "len": len(cleaned),
            "quoted": raw != cleaned,
        }

    return {
        "engine": short,
        "persistent": persistent,
        "host": connection.settings_dict.get("HOST") or None,
        "name": connection.settings_dict.get("NAME") if persistent else "ephemeral",
        "envPresent": env_present,
        "unresolvedRefs": unresolved,
        "urlSchemes": url_schemes,
    }


@csrf_exempt
@require_GET
def api_state(request):
    state = load_state()
    payload = build_api_payload(state)
    payload["error"] = state.get("error")
    payload["autoRun"] = True
    payload["db"] = _db_diagnostics()
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
