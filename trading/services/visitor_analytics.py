"""Kevyt kävijäseuranta — vain etusivun GET-lataukset, ei API-pollauksia."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

logger = logging.getLogger(__name__)

BOT_UA_MARKERS = (
    "bot",
    "spider",
    "crawler",
    "slurp",
    "facebookexternalhit",
    "preview",
    "headless",
)


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.META.get("REMOTE_ADDR") or "").strip()


def ip_hash_for_request(request) -> str:
    raw = f"{_client_ip(request)}:{settings.SECRET_KEY}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def is_bot_user_agent(user_agent: str) -> bool:
    ua = user_agent.lower()
    return any(marker in ua for marker in BOT_UA_MARKERS)


def parse_referrer(referer: str) -> tuple[str, str]:
    """Palauta (lähde-ryhmä, host)."""
    referer = (referer or "").strip()
    if not referer:
        return "direct", ""

    try:
        host = urlparse(referer).netloc.lower().removeprefix("www.")
    except ValueError:
        return "other", ""

    if not host:
        return "direct", ""

    if "google." in host or host == "google.com":
        return "Google", host
    if "bing." in host or host.endswith("bing.com"):
        return "Bing", host
    if any(x in host for x in ("facebook.", "fb.", "instagram.", "l.facebook.com")):
        return "Facebook", host
    if any(x in host for x in ("twitter.", "t.co", "x.com")):
        return "X / Twitter", host
    if "reddit." in host:
        return "Reddit", host
    if "linkedin." in host:
        return "LinkedIn", host
    custom = (getattr(settings, "CUSTOM_DOMAIN", "") or "").strip().lower().removeprefix("www.")
    if custom and (host == custom or host == f"www.{custom}"):
        return "internal", host
    if "railway.app" in host:
        return "Railway", host

    return host, host


def record_page_visit(request, path: str = "/") -> None:
    """Tallenna yksi etusivun käynti (ei blokkaa virheellä)."""
    if request.method != "GET":
        return

    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:256]
    if is_bot_user_agent(user_agent):
        return

    referer = (request.META.get("HTTP_REFERER") or "")[:512]
    source, source_host = parse_referrer(referer)

    from trading.models import PageVisit

    PageVisit.objects.create(
        path=path[:200],
        referer=referer,
        referer_source=source[:64],
        referer_host=source_host[:128],
        user_agent=user_agent,
        ip_hash=ip_hash_for_request(request),
        is_bot=False,
    )


def get_visitor_stats(*, days: int = 30) -> dict[str, Any]:
    """Yhteenveto admin/API:lle."""
    from trading.models import PageVisit

    days = max(1, min(int(days), 365))
    since = timezone.now() - timedelta(days=days)
    human = PageVisit.objects.filter(visited_at__gte=since, is_bot=False)

    total = human.count()
    unique = human.values("ip_hash").distinct().count()

    by_source = list(
        human.values("referer_source")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )
    by_day = list(
        human.annotate(day=TruncDate("visited_at"))
        .values("day")
        .annotate(visits=Count("id"), unique_visitors=Count("ip_hash", distinct=True))
        .order_by("-day")[:days]
    )

    return {
        "days": days,
        "since": since.isoformat(),
        "totalVisits": total,
        "uniqueVisitors": unique,
        "bySource": by_source,
        "byDay": by_day,
    }
