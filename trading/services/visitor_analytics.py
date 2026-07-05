"""Kevyt kävijäseuranta — vain sivulataukset (GET), ei API-pollauksia."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db.models import Avg, Count, Max
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

_geo_cache: dict[str, dict[str, str]] = {}

# Admin-kävijätilastot — ei julkisia tilastoja
_STATS_PATH_PREFIX = "/stats"


def _public_visits_qs():
    from trading.models import PageVisit

    return PageVisit.objects.filter(is_bot=False).exclude(path__startswith=_STATS_PATH_PREFIX)


def _is_private_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return True


def _geo_lookup(client_ip: str) -> dict[str, str]:
    """Maa + operaattori (ip-api.com, muistissa)."""
    empty = {"country_code": "", "isp": ""}
    if not client_ip or _is_private_ip(client_ip):
        return empty

    cached = _geo_cache.get(client_ip)
    if cached is not None:
        return cached

    result = dict(empty)
    try:
        resp = requests.get(
            f"http://ip-api.com/json/{client_ip}",
            params={"fields": "countryCode,isp,org,status"},
            timeout=1.2,
        )
        if resp.ok:
            data = resp.json()
            if data.get("status") == "success":
                result["country_code"] = (data.get("countryCode") or "").upper()[:2]
                result["isp"] = (
                    (data.get("isp") or data.get("org") or "").strip()[:128]
                )
    except Exception:
        logger.debug("Geo lookup failed for %s", client_ip, exc_info=True)

    _geo_cache[client_ip] = result
    return result


def isp_for_ip(client_ip: str, stored_isp: str = "") -> str:
    if stored_isp:
        return stored_isp
    if not client_ip or client_ip.startswith("hash "):
        return ""
    return _geo_lookup(client_ip).get("isp") or ""


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

    return _geo_lookup(client_ip).get("country_code") or ""


def _isp_for_request(request, client_ip: str) -> str:
    if not client_ip or _is_private_ip(client_ip):
        return ""
    return _geo_lookup(client_ip).get("isp") or ""


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


def format_duration_label(duration_sec: int | None) -> str:
    if duration_sec is None:
        return "—"
    sec = max(0, int(duration_sec))
    if sec < 60:
        return f"{sec} s"
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rem_min = minutes % 60
    if rem_min:
        return f"{hours} t {rem_min} min"
    return f"{hours} t"


def _avg_duration_summary(qs) -> dict[str, Any]:
    """Keskimääräinen kesto vain käynneille joilla duration_sec on mitattu."""
    total = qs.count()
    measured_qs = qs.filter(duration_sec__isnull=False)
    measured = measured_qs.count()
    avg_val = measured_qs.aggregate(avg=Avg("duration_sec"))["avg"]
    avg_sec = int(round(avg_val)) if avg_val is not None else None
    return {
        "label": format_duration_label(avg_sec),
        "avg_sec": avg_sec,
        "measured": measured,
        "total": total,
    }


def _period_starts() -> tuple[Any, Any, Any]:
    now = timezone.localtime(timezone.now())
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)
    year_start = today_start.replace(month=1, day=1)
    return today_start, month_start, year_start


def get_visit_count_cards() -> dict[str, int]:
    """Käyntimäärät: kaikki ajat, tänään ja kuluvan kuukauden aikana."""
    from trading.models import PageVisit

    today_start, month_start, _ = _period_starts()
    base = _public_visits_qs()
    return {
        "all": base.count(),
        "today": base.filter(visited_at__gte=today_start).count(),
        "month": base.filter(visited_at__gte=month_start).count(),
    }


def get_avg_duration_cards() -> dict[str, dict[str, Any]]:
    """Keskimääräinen sivullaolo: tänään, tässä kuussa, tänä vuonna."""
    from trading.models import PageVisit

    today_start, month_start, year_start = _period_starts()
    base = _public_visits_qs()

    return {
        "today": _avg_duration_summary(base.filter(visited_at__gte=today_start)),
        "month": _avg_duration_summary(base.filter(visited_at__gte=month_start)),
        "year": _avg_duration_summary(base.filter(visited_at__gte=year_start)),
    }


def record_page_visit(request, path: str = "/") -> int | None:
    """Tallenna yksi etusivun käynti. Palauttaa rivin id:n keston raportointia varten."""
    if request.method != "GET":
        return None

    path = (path or "/")[:200]
    if path.startswith(_STATS_PATH_PREFIX):
        return None

    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:256]
    if is_bot_user_agent(user_agent):
        return None

    referer = (request.META.get("HTTP_REFERER") or "")[:512]
    source, source_host = parse_referrer(referer)
    client_ip = _client_ip(request) or None
    country_code = _country_code_for_request(request, client_ip or "")
    client_isp = _isp_for_request(request, client_ip or "")

    from trading.models import PageVisit

    visit = PageVisit.objects.create(
        path=path[:200],
        referer=referer,
        referer_source=source[:64],
        referer_host=source_host[:128],
        user_agent=user_agent,
        ip_hash=ip_hash_for_request(request),
        client_ip=client_ip,
        client_isp=client_isp[:128],
        country_code=country_code,
        is_bot=False,
    )
    return visit.pk


def record_visit_duration(visit_id: int, duration_sec: int) -> bool:
    """Päivitä käynnin kesto — kasvaa heartbeat/poistumisraporttien mukana."""
    if visit_id <= 0:
        return False
    duration_sec = max(1, min(int(duration_sec), 86400))
    from trading.models import PageVisit

    visit = PageVisit.objects.filter(pk=visit_id, is_bot=False).only("duration_sec").first()
    if not visit:
        return False
    current = visit.duration_sec
    if current is not None and duration_sec <= current:
        return True
    updated = PageVisit.objects.filter(pk=visit_id, is_bot=False).update(
        duration_sec=duration_sec
    )
    return updated > 0


def get_visitor_stats(*, days: int = 30) -> dict[str, Any]:
    """Yhteenveto admin/API:lle."""
    days = max(1, min(int(days), 365))
    since = timezone.now() - timedelta(days=days)
    human = _public_visits_qs().filter(visited_at__gte=since)

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


def _enrich_visit_row(row: dict[str, Any]) -> dict[str, Any]:
    """Lisää näyttömuotoilu ja operaattori (vanhoille riveille lookup)."""
    out = dict(row)
    out["country_name"] = country_name(out.get("country_code") or "")
    raw_ip = out.get("client_ip") or ""
    out["client_isp"] = isp_for_ip(raw_ip, out.get("client_isp") or "")
    if not raw_ip and out.get("ip_hash"):
        out["client_ip"] = f"hash {out['ip_hash'][:10]}…"
    out["duration_label"] = format_duration_label(out.get("duration_sec"))
    return out


def get_stats_page_data(*, days: int = 30) -> dict[str, Any]:
    """HTML /stats-sivulle: päivittäiset käynnit, IP:t ja maat."""
    days = max(1, min(int(days), 365))
    since = timezone.now() - timedelta(days=days)
    human = _public_visits_qs().filter(visited_at__gte=since)

    base = get_visitor_stats(days=days)

    visit_rows = list(
        human.order_by("-visited_at").values(
            "visited_at",
            "path",
            "client_ip",
            "ip_hash",
            "country_code",
            "referer_source",
            "client_isp",
            "duration_sec",
        )
    )
    visits_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in visit_rows:
        day_key = row["visited_at"].date().isoformat()
        visits_by_day[day_key].append(_enrich_visit_row(row))

    by_day_raw = base["byDay"]
    max_day_visits = max((int(row["visits"]) for row in by_day_raw), default=1) or 1
    by_day = []
    for row in by_day_raw:
        visits = int(row["visits"])
        day = row["day"]
        day_key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        by_day.append(
            {
                "day": day_key,
                "day_label": day.strftime("%d.%m.%Y") if hasattr(day, "strftime") else str(day),
                "visits": visits,
                "unique_visitors": int(row["unique_visitors"]),
                "bar_pct": int(round(100 * visits / max_day_visits)),
                "visit_log": visits_by_day.get(day_key, []),
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
        .values("client_ip")
        .annotate(
            visits=Count("id"),
            last_visit=Max("visited_at"),
            country_code=Max("country_code"),
            client_isp=Max("client_isp"),
        )
        .order_by("-visits", "-last_visit")[:100]
    )
    by_ip = [
        {
            "ip": row["client_ip"],
            "country_code": row["country_code"] or "",
            "country_name": country_name(row["country_code"] or ""),
            "client_isp": isp_for_ip(row["client_ip"], row.get("client_isp") or ""),
            "visits": int(row["visits"]),
            "last_visit": row["last_visit"],
        }
        for row in by_ip_raw
    ]

    if len(by_ip) < 100:
        legacy_raw = list(
            human.filter(client_ip__isnull=True)
            .values("ip_hash")
            .annotate(visits=Count("id"), last_visit=Max("visited_at"))
            .order_by("-visits", "-last_visit")[: max(0, 100 - len(by_ip))]
        )
        for row in legacy_raw:
            h = row["ip_hash"] or ""
            by_ip.append(
                {
                    "ip": f"hash {h[:10]}…" if h else "—",
                    "country_code": "",
                    "country_name": "vanha (ei IP:tä)",
                    "visits": int(row["visits"]),
                    "last_visit": row["last_visit"],
                    "legacy": True,
                }
            )

    recent = [_enrich_visit_row(row) for row in visit_rows[:80]]

    unknown_country = human.filter(country_code="").count()

    return {
        **base,
        "byDay": by_day,
        "byCountry": by_country,
        "byIp": by_ip,
        "recentVisits": recent,
        "unknownCountryVisits": unknown_country,
        "visitCounts": get_visit_count_cards(),
        "avgDuration": get_avg_duration_cards(),
        "generatedAt": timezone.now(),
    }
