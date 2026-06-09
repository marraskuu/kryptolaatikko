"""
Google Gemini -avustettu kaupankäyntianalyysi.
API-avain luetaan vain ympäristömuuttujasta GEMINI_API_KEY (ei koskaan frontendiin).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "45"))


def _read_model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash").strip()


def _read_api_key() -> str:
    """Luetaan aina tuoreena — Railway/inject voi tulla käyttöön importin jälkeen."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_KEY"):
        value = os.environ.get(name, "").strip().strip('"').strip("'")
        if value:
            return value
    return ""


def is_configured() -> bool:
    key = _read_api_key()
    # AIzaSy… = perinteinen AI Studio -avain, AQ.… = uusi GCP service account -sidonnainen avain
    return bool(key) and (key.startswith("AIza") or key.startswith("AQ."))


def _key_format() -> str:
    key = _read_api_key()
    if key.startswith("AQ."):
        return "gcp-bound"
    if key.startswith("AIza"):
        return "legacy"
    return "unknown"


def _key_hint() -> str:
    key = _read_api_key()
    if not key:
        return "GEMINI_API_KEY puuttuu Railway Variables / .env"
    if not (key.startswith("AIza") or key.startswith("AQ.")):
        return "Avain ei tunnistettu — pitäisi alkaa AIzaSy tai AQ."
    return f"Avain asetettu ({_key_format()})"


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _build_market_summary(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    label_fn,
    limit: int = 25,
) -> list[dict[str, Any]]:
    ranked = sorted(
        tickers.items(),
        key=lambda x: x[1].get("volumeEur", 0),
        reverse=True,
    )[:limit]

    holdings = portfolio.get("holdings", {})
    rows = []
    seen = set()

    for symbol, ticker in ranked:
        seen.add(symbol)
        analysis = analyses.get(symbol, {})
        holding = holdings.get(symbol)
        rows.append(
            {
                "symbol": symbol,
                "label": label_fn(symbol),
                "price_eur": round(ticker.get("last", 0), 4),
                "change_24h_pct": round(ticker.get("changePct", 0), 2),
                "volume_eur": round(ticker.get("volumeEur", 0), 0),
                "rsi": round(analysis.get("rsi", 50), 1),
                "momentum_pct": round(analysis.get("momentum", 0), 2),
                "technical_action": analysis.get("action", "hold"),
                "technical_score": analysis.get("score", 0),
                "held": bool(holding),
                "avg_buy_eur": round(holding["avgPrice"], 4) if holding else None,
            }
        )

    for symbol in holdings:
        if symbol in seen or symbol not in tickers:
            continue
        ticker = tickers[symbol]
        analysis = analyses.get(symbol, {})
        holding = holdings[symbol]
        rows.append(
            {
                "symbol": symbol,
                "label": label_fn(symbol),
                "price_eur": round(ticker.get("last", 0), 4),
                "change_24h_pct": round(ticker.get("changePct", 0), 2),
                "volume_eur": round(ticker.get("volumeEur", 0), 0),
                "rsi": round(analysis.get("rsi", 50), 1),
                "momentum_pct": round(analysis.get("momentum", 0), 2),
                "technical_action": analysis.get("action", "hold"),
                "technical_score": analysis.get("score", 0),
                "held": True,
                "avg_buy_eur": round(holding["avgPrice"], 4),
            }
        )

    return rows


def get_status() -> dict[str, Any]:
    """Turvallinen tila UI:lle — ei koskaan paljasta avainta."""
    key = _read_api_key()
    base = {
        "configured": is_configured(),
        "keyPresent": bool(key),
        "keyLength": len(key),
        "keyFormat": _key_format() if key else "none",
    }
    if is_configured():
        return {
            **base,
            "ok": False,
            "message": "Gemini odottaa seuraavaa analyysikierrosta",
            "provider": "gemini",
        }
    return {
        **base,
        "ok": False,
        "message": _key_hint(),
        "provider": "technical",
    }


def log_startup_status() -> None:
    status = get_status()
    logger.info(
        "Gemini startup: present=%s len=%s format=%s configured=%s",
        status["keyPresent"],
        status["keyLength"],
        status["keyFormat"],
        status["configured"],
    )


def advise_portfolio(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    label_fn,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """
    Palauttaa (insights, status).
    insights: { top_picks: [...], signals: { symbol: { action, confidence, reason } } }
    status: { ok, message, provider } — turvallinen UI:lle, ei avainta.
    """
    if not is_configured():
        return None, {
            "ok": False,
            "message": _key_hint(),
            "provider": "technical",
            "configured": False,
        }

    market = _build_market_summary(tickers, analyses, portfolio, label_fn)
    if not market:
        return None, {"ok": False, "message": "Ei markkinadataa Geminille", "provider": "gemini", "configured": True}

    cash = round(portfolio.get("cash", 0), 2)
    prompt = f"""Olet kryptovaluutta-simulaattorin kaupankäyntiavustaja (paper trading, ei oikeaa rahaa).

Säännöt:
- Salkussa max 3–4 kryptoa, alkupääoma 1000 EUR, käteinen nyt {cash} EUR
- 30 % vero vain voitoista; myy voitolla +3 % strategian mukaan erikseen
- Valitse likvidit parit (korkea volume_eur), älä spekuloi obskureilla

Markkinadata (JSON):
{json.dumps(market, ensure_ascii=False)}

Vastaa VAIN validilla JSON:lla (ei markdownia):
{{
  "top_picks": ["tSYM1", "tSYM2", "tSYM3", "tSYM4"],
  "signals": [
    {{
      "symbol": "tSYM1",
      "action": "buy|sell|hold",
      "confidence": 1-10,
      "reason": "lyhyt perustelu suomeksi"
    }}
  ]
}}

top_picks = 3-4 parasta ostokohdetta nyt (symbol-kentät täsmälleen).
signals = kaikki held=true positiot + top_picks (max 12 riviä)."""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_read_model()}:generateContent"
    )
    api_key = _read_api_key()

    try:
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "responseMimeType": "application/json",
                },
            },
            timeout=GEMINI_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = _extract_json(text)

        signals_list = parsed.get("signals") or []
        signals_map = {}
        for item in signals_list:
            sym = item.get("symbol")
            if not sym:
                continue
            action = str(item.get("action", "hold")).lower()
            if action not in ("buy", "sell", "hold"):
                action = "hold"
            confidence = max(1, min(10, int(item.get("confidence", 5))))
            signals_map[sym] = {
                "action": action,
                "confidence": confidence,
                "reason": str(item.get("reason", "")).strip()[:240],
            }

        top_picks = [s for s in (parsed.get("top_picks") or []) if isinstance(s, str)][:4]

        insights = {"top_picks": top_picks, "signals": signals_map}
        return insights, {
            "ok": True,
            "message": f"Gemini analysoi {len(signals_map)} signaalia",
            "provider": "gemini",
            "model": _read_model(),
            "configured": True,
            "keyFormat": _key_format(),
        }

    except requests.RequestException as exc:
        detail = ""
        if hasattr(exc, "response") and exc.response is not None:
            try:
                err_body = exc.response.json()
                detail = err_body.get("error", {}).get("message", "")[:120]
            except (ValueError, AttributeError):
                detail = exc.response.text[:120] if exc.response.text else ""
        logger.warning("Gemini API error: %s %s", type(exc).__name__, detail)
        msg = "Gemini-yhteys epäonnistui — käytetään teknistä analyysiä"
        if detail:
            msg = f"{msg} ({detail})"
        return None, {
            "ok": False,
            "message": msg,
            "provider": "gemini",
            "configured": True,
        }
    except (KeyError, IndexError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("Gemini parse error: %s", type(exc).__name__)
        return None, {
            "ok": False,
            "message": "Gemini-vastaus virheellinen — käytetään teknistä analyysiä",
            "provider": "gemini",
            "configured": True,
        }
