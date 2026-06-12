#!/usr/bin/env python3
"""
Terveystarkastus — CLI / cron / Cursor-automaatio.

Käyttö:
  python scripts/health_check.py
  python scripts/health_check.py --deep
  python scripts/health_check.py --url https://hiekkalaatikko.pro/api/health/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_URL = "https://hiekkalaatikko.pro/api/health/"


def _fetch_remote(url: str, deep: bool) -> tuple[int, dict]:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    if deep:
        query["deep"] = ["1"]
    new_query = urllib.parse.urlencode(query, doseq=True)
    target = urllib.parse.urlunparse(parsed._replace(query=new_query))
    req = urllib.request.Request(target, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.status, json.loads(resp.read().decode())


def _run_local(deep: bool) -> tuple[int, dict]:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from trading.services.health_check import run_health_check

    payload = run_health_check(deep=deep)
    status = 200 if payload.get("ok") else 503
    return status, payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Krypto Simulaattori — terveystarkastus")
    parser.add_argument("--deep", action="store_true", help="Bitfinex + Gemini mukaan")
    parser.add_argument(
        "--url",
        default="",
        help=f"Etä-API (oletus paikallinen Django; anna URL tuotantoon)",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help=f"Käytä tuotannon health-API:a ({DEFAULT_URL})",
    )
    args = parser.parse_args()

    try:
        if args.remote or args.url:
            url = args.url or DEFAULT_URL
            status, payload = _fetch_remote(url, args.deep)
        else:
            status, payload = _run_local(args.deep)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"error": body}
        status = exc.code
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if status >= 500 or not payload.get("ok"):
        return 1
    if payload.get("status") == "degraded":
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
