"""Kevyt kävijäseuranta — vain sivulataukset (GET), ei API-pollauksia."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db.models import Count, Max
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

COUNTRY_NAMES: dict[str, str] = {
    "FI": "Suomi",
    "SE": "Ruotsi",
    "NO": "Norja",
    "DK": "Tanska",
    "EE": "Viro",
    "DE": "Saksa",
    "GB": "Iso-Britannia",
    "US": "Yhdysvallat",
    "NL": "Alankomaat",
    "FR": "Ranska",
    "ES": "Espanja",
    "IT": "Italia",
    "PL": "Puola",
    "RU": "Venäjä",
    "UA": "Ukraina",
    "CN": "Kiina",
    "JP": "Japani",
    "IN": "Intia",
    "BR": "Brasilia",
    "CA": "Kanada",
    "AU": "Australia",
    "CH": "Sveitsi",
    "AT": "Itävalta",
    "BE": "Belgia",
    "IE": "Irlanti",
    "PT": "Portugali",
    "CZ": "Tšekki",
    "GR": "Kreikka",
    "TR": "Turkki",
    "IL": "Israel",
    "SG": "Singapore",
    "HK": "Hongkong",
    "KR": "Etelä-Korea",
    "TW": "Taiwan",
    "MX": "Meksiko",
    "AR": "Argentiina",
    "ZA": "Etelä-Afrikka",
    "AE": "Arabiemiirikunnat",
    "LT": "Liettua",
    "LV": "Latvia",
}

_geo_cache: dict[str, str] = {}


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.META.get("REMOTE_ADDR") or "").strip()


def ip_hash_for_request(request) -> str:
    raw = f"{_client_ip(request)}:{settings.SECRET_KEY}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def country_name(code: str) -> str:
    code = (code or "").upper()
    if not code:
        return "—"
    return COUNTRY_NAMES.get(code, code)


def _is_private_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return True


def _country_code_for_request(request, client_ip: str) -> str:
    for header in (
        "HTTP_CF_IPCOUNTRY",
        "HTTP_X_COUNTRY_CODE",
        "HTTP_CLOUDFRONT_VIEWER_COUNTRY",
    ):
        code = (request.META.get(header) or "").strip().upper()
        if len(code) == 2 and code not in ("XX", "T1", "ZZ"):
            return code

    if not client_ip or _is_private_ip(client_ip):
        return ""

    cached = _geo_cache.get(client_ip)
    if cached is not None:
        return cached

    code = ""
    try:
        resp = requests.get(
            f"http://ip-api.com/json/{client_ip}",
            params={"fields": "countryCode,status"},
            timeout=1.2,
        )
        if resp.ok:
            data = resp.json()
            if data.get("status") == "success":
                code = (data.get("countryCode") or "").upper()[:2]
    except Exception:
        logger.debug("Geo lookup failed for %s", client_ip, exc_info=True)

    _geo_cache[client_ip] = code
    return code


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
    client_ip = _client_ip(request) or None
    country_code = _country_code_for_request(request, client_ip or "")

    from trading.models import PageVisit

    PageVisit.objects.create(
        path=path[:200],
        referer=referer,
        referer_source=source[:64],
        referer_host=source_host[:128],
        user_agent=user_agent,
        ip_hash=ip_hash_for_request(request),
        client_ip=client_ip,
        country_code=country_code,
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


def get_stats_page_data(*, days: int = 30) -> dict[str, Any]:
    """HTML /stats-sivulle: päivittäiset käynnit, IP:t ja maat."""
    from trading.models import PageVisit

    days = max(1, min(int(days), 365))
    since = timezone.now() - timedelta(days=days)
    human = PageVisit.objects.filter(visited_at__gte=since, is_bot=False)

    base = get_visitor_stats(days=days)

    by_day_raw = base["byDay"]
    max_day_visits = max((int(row["visits"]) for row in by_day_raw), default=1) or 1
    by_day = []
    for row in by_day_raw:
        visits = int(row["visits"])
        day = row["day"]
        by_day.append(
            {
                "day": day.isoformat() if hasattr(day, "isoformat") else str(day),
                "day_label": day.strftime("%d.%m.%Y") if hasattr(day, "strftime") else str(day),
                "visits": visits,
                "unique_visitors": int(row["unique_visitors"]),
                "bar_pct": round(100 * visits / max_day_visits, 1),
            }
        )

    by_country_raw = list(
        human.exclude(country_code="")
        .values("country_code")
        .annotate(
            visits=Count("id"),
            unique_ips=Count("client_ip", distinct=True),
        )
        .order_by("-visits")[:40]
    )
    by_country = [
        {
            "code": row["country_code"],
            "name": country_name(row["country_code"]),
            "visits": int(row["visits"]),
            "unique_ips": int(row["unique_ips"]),
        }
        for row in by_country_raw
    ]

    by_ip_raw = list(
        human.exclude(client_ip__isnull=True)
        .exclude(client_ip="")
        .values("client_ip", "country_code")
        .annotate(
            visits=Count("id"),
            last_visit=Max("visited_at"),
        )
        .order_by("-visits", "-last_visit")[:100]
    )
    by_ip = [
        {
            "ip": row["client_ip"],
            "country_code": row["country_code"] or "",
            "country_name": country_name(row["country_code"] or ""),
            "visits": int(row["visits"]),
            "last_visit": row["last_visit"],
        }
        for row in by_ip_raw
    ]

    recent = list(
        human.order_by("-visited_at")
        .values(
            "visited_at",
            "path",
            "client_ip",
            "country_code",
            "referer_source",
        )[:80]
    )
    for row in recent:
        row["country_name"] = country_name(row.get("country_code") or "")

    unknown_country = human.filter(country_code="").count()

    return {
        **base,
        "byDay": by_day,
        "byCountry": by_country,
        "byIp": by_ip,
        "recentVisits": recent,
        "unknownCountryVisits": unknown_country,
        "generatedAt": timezone.now(),
    }
