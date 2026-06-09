"""
Google Gemini -avustettu kaupankäyntianalyysi.
API-avain luetaan vain ympäristömuuttujasta GEMINI_API_KEY (ei koskaan frontendiin).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from .bitfinex import is_stablecoin, normalize_symbol

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


def _parse_trade_time(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _sell_profit_eur(trade: dict[str, Any]) -> float:
    pl = trade.get("profitLoss")
    if pl is not None:
        return float(pl)
    if trade.get("profit") is not None:
        return float(trade["profit"])
    return float(trade.get("eurTotal", 0)) - float(trade.get("costBasis", 0))


def _summarize_sells(sells: list[dict[str, Any]]) -> dict[str, Any]:
    wins = losses = 0
    net = 0.0
    for t in sells:
        pl = _sell_profit_eur(t)
        net += pl
        if pl > 0.01:
            wins += 1
        elif pl < -0.01:
            losses += 1
    return {
        "sells": len(sells),
        "wins": wins,
        "losses": losses,
        "net_profit_eur": round(net, 2),
    }


def _build_trade_history_summary(
    portfolio: dict[str, Any],
    label_fn,
    limit: int = 15,
) -> dict[str, Any]:
    """Palauttaa Geminille kauppahistorian ja suorituskyvyn — palautekierros."""
    all_trades = [t for t in portfolio.get("trades", []) if t.get("type") in ("buy", "sell")]
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    day_ago = now - timedelta(hours=24)

    def in_period(trade: dict[str, Any], since: datetime) -> bool:
        try:
            return _parse_trade_time(trade["timestamp"]) >= since
        except (ValueError, TypeError, KeyError):
            return False

    sells = [t for t in all_trades if t["type"] == "sell"]
    recent_rows: list[dict[str, Any]] = []
    for t in all_trades[:limit]:
        try:
            time_str = _parse_trade_time(t["timestamp"]).strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError, KeyError):
            time_str = str(t.get("timestamp", ""))[:16]

        row: dict[str, Any] = {
            "time": time_str,
            "type": "osto" if t["type"] == "buy" else "myynti",
            "label": label_fn(t.get("symbol", "")),
            "symbol": t.get("symbol"),
            "price_eur": round(float(t.get("price") or 0), 6),
            "amount": round(float(t.get("amount") or 0), 8),
            "eur_total": round(float(t.get("eurTotal") or 0), 2),
        }
        if t["type"] == "sell":
            pl = _sell_profit_eur(t)
            row["profit_loss_eur"] = round(pl, 2)
            row["result"] = "voitto" if pl > 0.01 else "tappio" if pl < -0.01 else "tasapeli"
        reason = (t.get("reason") or "").strip()[:120]
        if reason:
            row["reason"] = reason
        recent_rows.append(row)

    return {
        "total_trades": len(all_trades),
        "realized_profit_eur_total": round(float(portfolio.get("totalRealizedProfit") or 0), 2),
        "last_7_days": {
            **_summarize_sells([t for t in sells if in_period(t, week_ago)]),
            "buys": len([t for t in all_trades if t["type"] == "buy" and in_period(t, week_ago)]),
        },
        "last_24h": _summarize_sells([t for t in sells if in_period(t, day_ago)]),
        "recent_trades_newest_first": recent_rows,
    }


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
    trade_history = _build_trade_history_summary(portfolio, label_fn)
    prompt = f"""Olet aggressiivinen krypto-salkunhoitaja. AINOA TAVOITE: maksimoida salkun voitto (EUR).

Paper trading, ei oikeaa rahaa — silti pyri aina kasvattamaan salkun arvoa alkupääomasta (1000 EUR).

Salkun tila nyt:
- Arvo yhteensä: {total_value} EUR (P/L {portfolio_pnl_pct:+.2f} % vs alkupääoma)
- Käteinen: {cash} EUR
- Positiot: {json.dumps(held, ensure_ascii=False)}

Kauppahistoria ja palaute (opettele näistä — älä toista tappiollisia linjauksia):
{json.dumps(trade_history, ensure_ascii=False)}

Historian käyttö:
- Katso recent_trades_newest_first: mitkä ostot/myynnit johtivat voittoon vs tappioon
- Jos sama krypto myyty tappiolla usein → vältä uudelleenostoa ilman selkeää käännettä
- Jos momentum-ostot tuottivat voittoa → painota samanlaista strategiaa
- last_7_days / last_24h kertovat lyhyen aikavälin onnistumisen — mukauta aggressiota

Kaupankäyntisäännöt (voitto edellä):
1. Myy heikot positiot (position_pnl_pct < -1 % tai 24h lasku) — vapauta pääoma vahvempiin
2. Osta nousussa olevia, korkean volyymin kohteita (change_24h_pct > 0)
3. Älä pidä tappiollisia pitkään — rotaatio nopeasti
4. Salkussa 1–4 kryptoa — valitse ITSE montako (top_picks 1–4 kohdetta). EI pakko neljää; 1 vahva riittää. Käteinen voi jäädä odottamaan.
5. ÄLÄ osta stablecoineja (USDT, USDC, UDC, STABLE, DAI jne.)
6. Voitto-positio: ÄLÄ myy nousuputkessa — pidä kunnes hinta tasaantuu tai laskee hieman huipusta; automaattinen voitto-myynti +2 %:sta vasta tasaantumisen jälkeen
7. Stop-loss noin -2 %: älä anna tappioiden kasvaa

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

top_picks = 1–4 parasta VOITTOON tähtäävää kohdetta (symbol täsmälleen datasta). Valitse vain ne joihin oikeasti uskot — 1–2 riittää usein.
allocations = sijoitusosuudet VAIN valituille top_picks (alloc_pct, summa ≈ 100). EI tasajaot.
Esim. vahva momentum 40-50 %, keskivahva 25-30 %, täydennys 15-20 %. Min 10 % per valittu kohde.
signals = jokainen held-positio + top_picks + vahvat buy/sell (max 15 riviä). alloc_pct vain buy-kohteille.
Priorisoi: myy tappiolliset, osta momentum-nousuja, keskitä pääoma parhaisiin. Voitolla olevia pidä nousussa.
Perustele päätökset myös historiasta: mitä opit viime kaupoista."""

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
                if not sym or is_stablecoin(str(sym)):
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

            top_picks = [
                s
                for s in (parsed.get("top_picks") or [])
                if isinstance(s, str) and not is_stablecoin(s)
            ][:4]

            allocations_map: dict[str, float] = {}
            for item in parsed.get("allocations") or []:
                sym = item.get("symbol")
                if not sym or item.get("alloc_pct") is None or is_stablecoin(str(sym)):
                    continue
                allocations_map[normalize_symbol(str(sym))] = max(
                    0.0, min(100.0, float(item["alloc_pct"]))
                )

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
