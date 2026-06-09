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


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

# Tuettuja malleja — ei vanhentuneita (esim. gemini-2.0-flash)
SUPPORTED_GEMINI_MODELS = (
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)


def _normalize_model(name: str) -> str:
    name = name.strip().strip('"').strip("'")
    if name.startswith("models/"):
        name = name[len("models/") :]
    return name


def _read_model() -> str:
    raw = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    model = _normalize_model(raw)
    return model or DEFAULT_GEMINI_MODEL


def _model_candidates() -> list[str]:
    primary = _read_model()
    models = [primary]
    for model in SUPPORTED_GEMINI_MODELS:
        if model not in models:
            models.append(model)
    return models


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
                "position_pnl_pct": (
                    round(
                        ((ticker.get("last", 0) - holding["avgPrice"]) / holding["avgPrice"]) * 100,
                        2,
                    )
                    if holding and holding.get("avgPrice")
                    else None
                ),
            }
        )

    for symbol in holdings:
        if symbol in seen or symbol not in tickers:
            continue
        ticker = tickers[symbol]
        analysis = analyses.get(symbol, {})
        holding = holdings[symbol]
        avg = holding["avgPrice"]
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
                "avg_buy_eur": round(avg, 4),
                "position_pnl_pct": round(((ticker.get("last", 0) - avg) / avg) * 100, 2) if avg else None,
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
            "status": "waiting",
            "message": "Gemini odottaa seuraavaa analyysikierrosta",
            "provider": "gemini",
        }
    return {
        **base,
        "ok": False,
        "status": "unconfigured",
        "message": _key_hint(),
        "provider": "technical",
    }


def log_startup_status() -> None:
    status = get_status()
    logger.info(
        "Gemini startup: present=%s len=%s format=%s configured=%s model=%s",
        status["keyPresent"],
        status["keyLength"],
        status["keyFormat"],
        status["configured"],
        _read_model(),
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
    initial = float(portfolio.get("initialCapital", 1000))
    holdings = portfolio.get("holdings", {})
    holdings_value = sum(
        h["amount"] * analyses.get(sym, {}).get("currentPrice", 0)
        for sym, h in holdings.items()
    )
    total_value = round(cash + holdings_value, 2)
    portfolio_pnl_pct = round(((total_value - initial) / initial) * 100, 2) if initial else 0

    held = [
        {
            "symbol": sym,
            "label": label_fn(sym),
            "value_eur": round(h["amount"] * analyses.get(sym, {}).get("currentPrice", 0), 2),
            "position_pnl_pct": round(
                (
                    (analyses.get(sym, {}).get("currentPrice", 0) - h["avgPrice"])
                    / h["avgPrice"]
                )
                * 100,
                2,
            )
            if h.get("avgPrice")
            else None,
        }
        for sym, h in holdings.items()
    ]
    prompt = f"""Olet aggressiivinen krypto-salkunhoitaja. AINOA TAVOITE: maksimoida salkun voitto (EUR).

Paper trading, ei oikeaa rahaa — silti pyri aina kasvattamaan salkun arvoa alkupääomasta (1000 EUR).

Salkun tila nyt:
- Arvo yhteensä: {total_value} EUR (P/L {portfolio_pnl_pct:+.2f} % vs alkupääoma)
- Käteinen: {cash} EUR
- Positiot: {json.dumps(held, ensure_ascii=False)}

Kaupankäyntisäännöt (voitto edellä):
1. Myy heikot positiot (position_pnl_pct < -1 % tai 24h lasku) — vapauta pääoma vahvempiin
2. Osta nousussa olevia, korkean volyymin kohteita (change_24h_pct > 0)
3. Älä pidä tappiollisia pitkään — rotaatio nopeasti
4. Max 3–4 kryptoa kerrallaan
5. Voitto-positio: ÄLÄ myy nousuputkessa — pidä kunnes hinta tasaantuu tai laskee hieman huipusta; automaattinen voitto-myynti +2 %:sta vasta tasaantumisen jälkeen
6. Stop-loss noin -2 %: älä anna tappioiden kasvaa

Markkinadata (JSON):
{json.dumps(market, ensure_ascii=False)}

Vastaa VAIN validilla JSON:lla (ei markdownia):
{{
  "top_picks": ["tSYM1", "tSYM2", "tSYM3", "tSYM4"],
  "allocations": [
    {{
      "symbol": "tSYM1",
      "alloc_pct": 40,
      "reason": "miksi juuri tämä osuus — vahvin voittopotentiaali"
    }}
  ],
  "signals": [
    {{
      "symbol": "tSYM1",
      "action": "buy|sell|hold",
      "confidence": 1-10,
      "alloc_pct": 40,
      "reason": "konkreettinen voitto-orientoitunut perustelu suomeksi"
    }}
  ]
}}

top_picks = 3-4 parasta VOITTOON tähtäävää kohdetta (symbol täsmälleen datasta).
allocations = sijoitusosuudet käteisestä/salkusta (alloc_pct, summa ≈ 100). EI tasajaot — enemmän parhaisiin, vähemmän heikompiin.
Esim. vahva momentum 40-50 %, keskivahva 25-30 %, täydennys 15-20 %. Min 10 % per valittu kohde.
signals = jokainen held-positio + top_picks + vahvat buy/sell (max 15 riviä). alloc_pct vain buy-kohteille.
Priorisoi: myy tappiolliset, osta momentum-nousuja, keskitä pääoma parhaisiin. Voitolla olevia pidä nousussa."""

    api_key = _read_api_key()
    errors: list[str] = []
    configured_model = _read_model()

    for model in _model_candidates():
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
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
                        "temperature": 0.2,
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
                signal: dict[str, Any] = {
                    "action": action,
                    "confidence": confidence,
                    "reason": str(item.get("reason", "")).strip()[:240],
                }
                if item.get("alloc_pct") is not None:
                    signal["alloc_pct"] = max(0.0, min(100.0, float(item["alloc_pct"])))
                signals_map[sym] = signal

            top_picks = [s for s in (parsed.get("top_picks") or []) if isinstance(s, str)][:4]

            allocations_map: dict[str, float] = {}
            for item in parsed.get("allocations") or []:
                sym = item.get("symbol")
                if not sym or item.get("alloc_pct") is None:
                    continue
                allocations_map[str(sym)] = max(0.0, min(100.0, float(item["alloc_pct"])))

            insights = {
                "top_picks": top_picks,
                "signals": signals_map,
                "allocations": allocations_map,
            }
            return insights, {
                "ok": True,
                "status": "ok",
                "message": f"Gemini analysoi {len(signals_map)} signaalia",
                "provider": "gemini",
                "model": model,
                "configured": True,
                "keyFormat": _key_format(),
            }

        except requests.RequestException as exc:
            detail = ""
            if hasattr(exc, "response") and exc.response is not None:
                try:
                    err_body = exc.response.json()
                    detail = err_body.get("error", {}).get("message", "")[:160]
                except (ValueError, AttributeError):
                    detail = exc.response.text[:160] if exc.response.text else ""
            err_msg = detail or type(exc).__name__
            errors.append(f"{model}: {err_msg}")
            logger.warning("Gemini API error (%s): %s", model, err_msg)
            continue
        except (KeyError, IndexError, json.JSONDecodeError, ValueError, TypeError) as exc:
            err_msg = type(exc).__name__
            errors.append(f"{model}: {err_msg}")
            logger.warning("Gemini parse error (%s): %s", model, err_msg)
            continue

    primary_err = next((e for e in errors if e.startswith(f"{configured_model}:")), None)
    fallback_err = errors[0] if errors else ""
    detail = (primary_err or fallback_err).split(": ", 1)[-1] if (primary_err or fallback_err) else ""
    msg = f"Gemini-yhteys epäonnistui ({configured_model}) — käytetään teknistä analyysiä"
    if detail:
        msg = f"{msg} ({detail})"
    return None, {
        "ok": False,
        "status": "error",
        "message": msg,
        "provider": "gemini",
        "model": configured_model,
        "configured": True,
    }
