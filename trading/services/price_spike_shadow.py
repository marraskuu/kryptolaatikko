"""Hintapiikin järkevyystarkistus order bookia vasten (varjodiagnostiikka, idea #5).

Tunnistaa epätavallisen suuren tick-to-tick-hintaliikkeen (15 s) ja tarkistaa
order bookin vahvistaako liike suunnan. Kirjaa tapahtuman ja mittaa myöhemmin
mitä hinnalle oikeasti kävi — sama "signaali → toteutunut lopputulos myöhemmin"
-periaate kuin market_learning.py:ssä, mutta EI koskaan ohita, viivästytä tai
muuta oikeaa hintapäivitystä/kauppasykliä.

Data tallentuu state["priceSpikeShadow"] (BotState pk=1) — harvinainen
tapahtumapohjainen kirjoitus (vain havaitulla piikillä), ei joka-tickin
näytteenotto kuten market_learning.py:ssä (pk=2), joten omaa BotState-riviä
ei tarvita.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .bitfinex import fetch_order_book, parse_order_book

SPIKE_THRESHOLD_PCT = 3.0   # tick-to-tick (15 s) hyppy, joka herättää tarkistuksen
RESOLVE_HORIZON_SEC = 900   # 15 min — mitä hinnalle kävi piikin jälkeen
BOOK_IMBALANCE_MIN = 0.05   # kynnys, jolla order book katsotaan vahvistavan suunnan
EVENT_LIMIT = 60
PENDING_LIMIT = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def default_shadow_state() -> dict[str, Any]:
    return {
        "version": 1,
        "pending": [],
        "events": [],
        "summary": {
            "spikesDetected": 0,
            "bookConfirmed": 0,
            "bookUnconfirmed": 0,
            "unconfirmedReverted": 0,
            "unconfirmedContinued": 0,
            "confirmedReverted": 0,
            "confirmedContinued": 0,
        },
    }


def _get_shadow(state: dict[str, Any]) -> dict[str, Any]:
    shadow = state.get("priceSpikeShadow")
    if not shadow:
        shadow = default_shadow_state()
        state["priceSpikeShadow"] = shadow
    return shadow


def detect_price_spikes(
    prev_tickers: dict[str, dict[str, Any]],
    tickers: dict[str, dict[str, Any]],
    threshold_pct: float = SPIKE_THRESHOLD_PCT,
) -> list[dict[str, Any]]:
    """Puhdas vertailu, ei I/O:ta. Palauttaa listan piikeistä tällä tickillä."""
    spikes: list[dict[str, Any]] = []
    if not prev_tickers or not tickers:
        return spikes
    for symbol, ticker in tickers.items():
        prev = prev_tickers.get(symbol)
        if not prev:
            continue
        price = float(ticker.get("last") or 0)
        prev_price = float(prev.get("last") or 0)
        if price <= 0 or prev_price <= 0:
            continue
        move_pct = (price - prev_price) / prev_price * 100
        if abs(move_pct) >= threshold_pct:
            spikes.append(
                {
                    "symbol": symbol,
                    "price": price,
                    "prevPrice": prev_price,
                    "movePct": round(move_pct, 3),
                }
            )
    return spikes


def _classify_book_confirmation(book: dict[str, Any] | None, move_pct: float) -> bool | None:
    """Vahvistaako order bookin osto/myyntiepätasapaino hintaliikkeen suunnan? None = ei dataa."""
    if not book:
        return None
    imbalance = book.get("bookImbalance")
    if imbalance is None:
        return None
    if move_pct > 0:
        return imbalance > BOOK_IMBALANCE_MIN
    if move_pct < 0:
        return imbalance < -BOOK_IMBALANCE_MIN
    return None


def record_spike_event(state: dict[str, Any], spike: dict[str, Any]) -> None:
    """Hae order book havaitulle symbolille (harvinainen tapahtuma, halpa 240 req/min-budjetissa)."""
    shadow = _get_shadow(state)
    summary = shadow["summary"]
    summary["spikesDetected"] = int(summary.get("spikesDetected") or 0) + 1

    book = None
    try:
        rows = fetch_order_book(spike["symbol"])
        book = parse_order_book(rows)
    except Exception:
        book = None

    confirmed = _classify_book_confirmation(book, spike["movePct"])
    if confirmed is True:
        summary["bookConfirmed"] = int(summary.get("bookConfirmed") or 0) + 1
    elif confirmed is False:
        summary["bookUnconfirmed"] = int(summary.get("bookUnconfirmed") or 0) + 1

    pending = shadow.setdefault("pending", [])
    pending.append(
        {
            "symbol": spike["symbol"],
            "detectedAt": _now_iso(),
            "resolveAt": _now_ts() + RESOLVE_HORIZON_SEC,
            "prevPrice": spike["prevPrice"],
            "priceAtDetection": spike["price"],
            "movePct": spike["movePct"],
            "bookConfirmed": confirmed,
            "bookImbalance": (book or {}).get("bookImbalance"),
            "bookSpreadPct": (book or {}).get("bookSpreadPct"),
        }
    )
    shadow["pending"] = pending[-PENDING_LIMIT:]


def _resolve_one(item: dict[str, Any], tickers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Laske toteutunut lopputulos yhdelle odottavalle piikkihavainnolle."""
    ticker = tickers.get(item["symbol"])
    later_price = float(ticker.get("last") or 0) if ticker else 0.0

    outcome_move_pct = None
    reverted = None
    if later_price > 0:
        outcome_move_pct = round(
            (later_price - item["priceAtDetection"]) / item["priceAtDetection"] * 100, 3
        )
        # "Kääntyi" = hinta antoi takaisin vähintään puolet piikin matkasta
        # verrattuna piikkiä edeltäneeseen hintaan (ei jatkanut samaan suuntaan).
        spike_distance = item["priceAtDetection"] - item["prevPrice"]
        if spike_distance != 0:
            retrace = (item["priceAtDetection"] - later_price) / spike_distance
            reverted = retrace >= 0.5

    return {
        "symbol": item["symbol"],
        "detectedAt": item["detectedAt"],
        "movePct": item["movePct"],
        "bookConfirmed": item.get("bookConfirmed"),
        "outcomeMovePct": outcome_move_pct,
        "reverted": reverted,
    }


def resolve_pending_events(state: dict[str, Any], tickers: dict[str, dict[str, Any]]) -> None:
    """Täytä myöhemmin toteutunut hintaliike odottaville tapahtumille — kutsutaan joka tickillä."""
    shadow = _get_shadow(state)
    pending = shadow.get("pending") or []
    if not pending:
        return

    now_ts = _now_ts()
    still_pending: list[dict[str, Any]] = []
    events = shadow.setdefault("events", [])
    summary = shadow["summary"]

    for item in pending:
        if now_ts < float(item.get("resolveAt") or 0):
            still_pending.append(item)
            continue

        resolved = _resolve_one(item, tickers)
        confirmed = resolved["bookConfirmed"]
        reverted = resolved["reverted"]
        if reverted is not None:
            if confirmed is False:
                key = "unconfirmedReverted" if reverted else "unconfirmedContinued"
                summary[key] = int(summary.get(key) or 0) + 1
            elif confirmed is True:
                key = "confirmedReverted" if reverted else "confirmedContinued"
                summary[key] = int(summary.get(key) or 0) + 1

        events.insert(0, resolved)

    shadow["events"] = events[:EVENT_LIMIT]
    shadow["pending"] = still_pending[-PENDING_LIMIT:]


def build_api_summary(state: dict[str, Any]) -> dict[str, Any]:
    shadow = state.get("priceSpikeShadow") or default_shadow_state()
    return {
        "enabled": True,
        "summary": shadow.get("summary") or {},
        "recentEvents": (shadow.get("events") or [])[:8],
        "pendingCount": len(shadow.get("pending") or []),
        "thresholds": {
            "spikeThresholdPct": SPIKE_THRESHOLD_PCT,
            "resolveHorizonSec": RESOLVE_HORIZON_SEC,
        },
    }


def build_gemini_context(state: dict[str, Any]) -> dict[str, Any]:
    api = build_api_summary(state)
    return {
        "note": (
            "Hintapiikkien järkevyystarkistus order bookia vasten — havainnollistaa "
            "kannattaisiko epätavallinen tick-hyppy ohittaa. EI vaikuta oikeisiin kauppoihin."
        ),
        "summary": api.get("summary"),
        "thresholds": api.get("thresholds"),
        "recentEvents": api.get("recentEvents"),
    }


def learning_report_lines(state: dict[str, Any]) -> list[str]:
    api = build_api_summary(state)
    summary = api.get("summary") or {}
    detected = int(summary.get("spikesDetected") or 0)
    if detected < 3:
        return ["Hintapiikkiseuranta kerää dataa — liian vähän havaintoja vertailuun"]

    lines = [f"Hintapiikit: {detected} havaintoa"]

    unconfirmed_total = int(summary.get("unconfirmedReverted") or 0) + int(
        summary.get("unconfirmedContinued") or 0
    )
    if unconfirmed_total:
        reverted = int(summary.get("unconfirmedReverted") or 0)
        lines.append(
            f"Ei order book -vahvistusta: {unconfirmed_total} tapausta, "
            f"{reverted} kääntyi takaisin ({reverted / unconfirmed_total:.0%})"
        )

    confirmed_total = int(summary.get("confirmedReverted") or 0) + int(
        summary.get("confirmedContinued") or 0
    )
    if confirmed_total:
        continued = int(summary.get("confirmedContinued") or 0)
        lines.append(
            f"Order book vahvisti: {confirmed_total} tapausta, "
            f"{continued} jatkoi samaan suuntaan ({continued / confirmed_total:.0%})"
        )
    return lines
