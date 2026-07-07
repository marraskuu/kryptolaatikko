"""HTTP-turvallisuusapu — ei vaadi erillisiä Railway-asetuksia."""

from __future__ import annotations

import hashlib
import os

from django.conf import settings
from django.core.cache import cache


def safe_next_path(url: str | None, default: str = "/stats/") -> str:
    """Salli vain suhteellinen polku — estää avoimen uudelleenohjauksen."""
    path = (url or "").strip()
    if not path.startswith("/") or path.startswith("//"):
        return default
    if "\\" in path or "\0" in path or ":" in path.split("/")[0]:
        return default
    return path


def admin_task_key() -> str | None:
    """Admin-API-avain — erillinen env tai johdettu SECRET_KEY:stä (ei sama kuin Django-sessio)."""
    explicit = os.environ.get("ADMIN_TASK_KEY", "").strip()
    if explicit:
        return explicit
    secret = settings.SECRET_KEY
    if not secret or secret == "dev-only-change-in-production":
        return secret if settings.DEBUG else None
    if settings.DEBUG:
        return secret
    return hashlib.sha256(f"admin-task:{secret}".encode()).hexdigest()


def read_admin_key_from_request(request) -> str:
    """Lue admin-avain headerista tai querystä (header vähemmän vuotoinen)."""
    header = (request.headers.get("X-Admin-Task-Key") or "").strip()
    if header:
        return header
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.GET.get("key") or "").strip()


def rate_limit_exceeded(scope: str, client_id: str, *, limit: int, window_sec: int) -> bool:
    """Yksinkertainen IP/pohjainen rate limit Django-cachen kautta."""
    if not client_id:
        client_id = "unknown"
    cache_key = f"rl:{scope}:{client_id}"
    try:
        count = cache.get(cache_key, 0)
        if count >= limit:
            return True
        cache.set(cache_key, count + 1, window_sec)
    except Exception:
        return False
    return False
