"""Terveystarkastus — Railway, cron ja skriptit."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from django.db import connection

from .ai_trader import IDLE_CASH_DEPLOY_PCT, IDLE_CASH_MIN_EUR
from .bot_worker import BOT_STALE_SEC, get_worker_status
from .gemini import _read_model, is_configured
from .state_store import load_state


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_diagnostics() -> dict[str, Any]:
    engine = connection.settings_dict.get("ENGINE", "")
    short = engine.rsplit(".", 1)[-1]
    persistent = short not in ("sqlite3",)

    url_vars = ("MYSQL_URL", "DATABASE_URL", "MYSQL_PUBLIC_URL")
    parts_vars = ("MYSQLHOST", "MYSQL_HOST", "MYSQLDATABASE", "MYSQL_DATABASE")
    env_present = {k: bool(os.environ.get(k, "").strip()) for k in url_vars + parts_vars}
    unresolved = {
        k: True
        for k in url_vars
        if "${" in os.environ.get(k, "") or "${{" in os.environ.get(k, "")
    }

    url_schemes: dict[str, Any] = {}
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


def _check_database() -> dict[str, Any]:
    diag = db_diagnostics()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        ok = not diag.get("unresolvedRefs")
        return {
            "ok": ok,
            "reachable": True,
            **diag,
        }
    except Exception as exc:
        return {
            "ok": False,
            "reachable": False,
            "error": str(exc),
            **diag,
        }


def _check_worker(state: dict[str, Any]) -> dict[str, Any]:
    ws = get_worker_status()
    last_ms = max(state.get("lastPriceTick") or 0, state.get("lastTradeTick") or 0)
    stale_sec = (
        max(0.0, time.time() - last_ms / 1000) if last_ms else 9999.0
    )
    stale = stale_sec >= BOT_STALE_SEC
    ok = ws.get("disabled") or (ws.get("alive") and not stale)
    return {
        "ok": ok,
        "stale": stale,
        "staleSec": int(stale_sec),
        "lastPriceTick": state.get("lastPriceTick"),
        "lastTradeTick": state.get("lastTradeTick"),
        **ws,
    }


def _check_gemini() -> dict[str, Any]:
    configured = is_configured()
    return {
        "ok": configured,
        "configured": configured,
        "model": _read_model(),
    }


def _check_bitfinex() -> dict[str, Any]:
    try:
        from .bitfinex import fetch_all_markets

        tickers, meta = fetch_all_markets()
        count = len(tickers) if tickers else 0
        return {
            "ok": count > 0,
            "tickerCount": count,
            "meta": meta,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _check_portfolio(state: dict[str, Any]) -> dict[str, Any]:
    portfolio = state.get("portfolio") or {}
    cash = float(portfolio.get("cash") or 0)
    tickers = state.get("tickers") or {}
    holdings = portfolio.get("holdings") or {}

    holdings_value = 0.0
    for symbol, holding in holdings.items():
        ticker = tickers.get(symbol) or {}
        price = float(ticker.get("last") or 0)
        holdings_value += float(holding.get("amount") or 0) * price

    total = cash + holdings_value
    cash_pct = (cash / total * 100) if total > 0 else 0.0
    idle_cash = cash >= IDLE_CASH_MIN_EUR and (
        total <= 0 or cash / total >= IDLE_CASH_DEPLOY_PCT
    )
    warnings: list[str] = []
    if idle_cash:
        warnings.append(f"idle_cash ({cash_pct:.0f} % käteistä)")
    if state.get("error"):
        warnings.append(f"state_error: {state['error']}")

    return {
        "ok": not state.get("error"),
        "cash": round(cash, 2),
        "holdingsValue": round(holdings_value, 2),
        "totalValue": round(total, 2),
        "cashPct": round(cash_pct, 1),
        "idleCash": idle_cash,
        "positionCount": len(holdings),
        "warnings": warnings,
    }


def _aggregate_status(checks: dict[str, dict[str, Any]]) -> str:
    critical = ("database", "worker")
    if any(not checks.get(k, {}).get("ok", False) for k in critical):
        return "unhealthy"
    optional = ("bitfinex", "gemini", "portfolio")
    if any(not checks.get(k, {}).get("ok", True) for k in optional if k in checks):
        return "degraded"
    warnings = checks.get("portfolio", {}).get("warnings") or []
    if warnings:
        return "degraded"
    return "healthy"


def run_health_check(*, deep: bool = False) -> dict[str, Any]:
    """
    Terveystarkastus.

    deep=False: nopea (DB + worker + portfolio) — sopii Railwaylle.
    deep=True:  + Bitfinex + Gemini + yksityiskohtaiset varoitukset.
    """
    state = load_state()
    checks: dict[str, dict[str, Any]] = {
        "database": _check_database(),
        "worker": _check_worker(state),
        "portfolio": _check_portfolio(state),
    }
    if deep:
        checks["bitfinex"] = _check_bitfinex()
        checks["gemini"] = _check_gemini()

    status = _aggregate_status(checks)
    warnings: list[str] = []
    errors: list[str] = []

    for name, chk in checks.items():
        if not chk.get("ok"):
            msg = chk.get("error") or f"{name} failed"
            if name in ("database", "worker"):
                errors.append(msg)
            else:
                warnings.append(msg)
        warnings.extend(chk.get("warnings") or [])

    if checks["worker"].get("stale") and not checks["worker"].get("disabled"):
        warnings.append(
            f"bot_stale ({checks['worker'].get('staleSec', '?')} s)"
        )

    return {
        "ok": status != "unhealthy",
        "status": status,
        "checkedAt": _now_iso(),
        "deep": deep,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }
