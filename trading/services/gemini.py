"""
Google Gemini -avustettu kaupankäyntianalyysi.
API-avain luetaan vain ympäristömuuttujasta GEMINI_API_KEY (ei koskaan frontendiin).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .bitfinex import is_stablecoin, normalize_symbol

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "45"))
FEE_RATE = 0.0  # Bitfinex poisti kaupankäyntikulut kokonaan
TAX_RATE = 0.30
MIN_ROTATION_INTERVAL_MIN = 30

# Tilapäiset virheet, jotka kannattaa yrittää uudelleen (ruuhka/ylikuormitus)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
GEMINI_MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "2"))
GEMINI_RETRY_BACKOFF_SEC = 1.5


# Halvin oletus — lite-malli riittää, kun tekninen analyysi tekee raskaan työn
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"

# Tuettuja malleja — halvin ensin, ei vanhentuneita (esim. gemini-2.0-flash)
SUPPORTED_GEMINI_MODELS = (
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.5-flash",
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


def _post_with_retry(url: str, api_key: str, prompt: str) -> requests.Response:
    """POST Geminille; uudelleenyritys tilapäisille ruuhka-/ylikuormavirheille.

    Palauttaa vastauksen myös ei-2xx tilassa; lopullinen raise_for_status
    tehdään kutsujassa, jotta virheen detaljit saadaan talteen.
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    last_response: requests.Response | None = None
    for attempt in range(GEMINI_MAX_RETRIES + 1):
        response = requests.post(url, headers=headers, json=payload, timeout=GEMINI_TIMEOUT)
        last_response = response
        if response.status_code not in RETRYABLE_STATUS:
            return response
        if attempt < GEMINI_MAX_RETRIES:
            time.sleep(GEMINI_RETRY_BACKOFF_SEC * (attempt + 1))
    return last_response  # type: ignore[return-value]


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


def _build_scan_leaders(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    label_fn,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Tekninen esikarsinta — parhaat ehdokkaat koko markkinasta Geminille."""
    rows: list[dict[str, Any]] = []
    for symbol, ticker in tickers.items():
        if is_stablecoin(symbol):
            continue
        analysis = analyses.get(symbol, {})
        if analysis.get("currentPrice", 0) <= 0:
            continue
        change_24h = analysis.get("changePct")
        if change_24h is None:
            change_24h = ticker.get("changePct", 0)
        rows.append(
            {
                "symbol": symbol,
                "label": label_fn(symbol),
                "technical_score": analysis.get("score", 0),
                "change_24h_pct": round(change_24h, 2),
                "change_1h_pct": round(analysis["change1hPct"], 2)
                if analysis.get("change1hPct") is not None
                else None,
                "technical_action": analysis.get("action", "hold"),
                "rsi": round(analysis.get("rsi", 50), 1),
                "ema_trend": (
                    "bullish"
                    if analysis.get("ema9", 0) > analysis.get("ema21", 0)
                    else "bearish"
                    if analysis.get("ema9") is not None
                    else None
                ),
            }
        )
    rows.sort(
        key=lambda r: (
            -r["technical_score"],
            -r["change_24h_pct"],
            -tickers.get(r["symbol"], {}).get("volumeEur", 0),
        )
    )
    return rows[:limit]


def _technical_top_picks(
    analyses: dict[str, dict[str, Any]],
    limit: int = 4,
) -> list[str]:
    ranked = sorted(
        [
            (sym, a)
            for sym, a in analyses.items()
            if not is_stablecoin(sym) and a.get("currentPrice", 0) > 0
        ],
        key=lambda x: (
            -x[1].get("score", 0),
            -(x[1].get("changePct") or x[1].get("momentum") or 0),
        ),
    )
    return [normalize_symbol(sym) for sym, _ in ranked[:limit]]


def _build_market_summary(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    label_fn,
    limit: int = 0,
) -> list[dict[str, Any]]:
    ranked = sorted(
        ((sym, t) for sym, t in tickers.items() if not is_stablecoin(sym)),
        key=lambda x: x[1].get("volumeEur", 0),
        reverse=True,
    )
    if limit > 0:
        ranked = ranked[:limit]

    holdings = portfolio.get("holdings", {})
    rows = []
    seen = set()

    for symbol, ticker in ranked:
        seen.add(symbol)
        analysis = analyses.get(symbol, {})
        holding = holdings.get(symbol)
        ema9 = analysis.get("ema9")
        ema21 = analysis.get("ema21")
        ema_trend = None
        if ema9 is not None and ema21 is not None:
            ema_trend = "bullish" if ema9 > ema21 else "bearish"
        rows.append(
            {
                "symbol": symbol,
                "label": label_fn(symbol),
                "price_eur": round(ticker.get("last", 0), 4),
                "change_1h_pct": round(analysis["change1hPct"], 2)
                if analysis.get("change1hPct") is not None
                else None,
                "change_4h_pct": round(analysis["change4hPct"], 2)
                if analysis.get("change4hPct") is not None
                else None,
                "change_24h_pct": round(ticker.get("changePct", 0), 2),
                "volume_eur": round(ticker.get("volumeEur", 0), 0),
                "rsi": round(analysis.get("rsi", 50), 1),
                "ema_trend": ema_trend,
                "momentum_pct": round(analysis.get("momentum", 0), 2),
                "technical_action": analysis.get("action", "hold"),
                "technical_score": analysis.get("score", 0),
                "deep_analysis": not analysis.get("quick", True),
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
        if symbol in seen or symbol not in tickers or is_stablecoin(symbol):
            continue
        ticker = tickers[symbol]
        analysis = analyses.get(symbol, {})
        holding = holdings[symbol]
        avg = holding["avgPrice"]
        ema9 = analysis.get("ema9")
        ema21 = analysis.get("ema21")
        ema_trend = None
        if ema9 is not None and ema21 is not None:
            ema_trend = "bullish" if ema9 > ema21 else "bearish"
        rows.append(
            {
                "symbol": symbol,
                "label": label_fn(symbol),
                "price_eur": round(ticker.get("last", 0), 4),
                "change_1h_pct": round(analysis["change1hPct"], 2)
                if analysis.get("change1hPct") is not None
                else None,
                "change_4h_pct": round(analysis["change4hPct"], 2)
                if analysis.get("change4hPct") is not None
                else None,
                "change_24h_pct": round(ticker.get("changePct", 0), 2),
                "volume_eur": round(ticker.get("volumeEur", 0), 0),
                "rsi": round(analysis.get("rsi", 50), 1),
                "ema_trend": ema_trend,
                "momentum_pct": round(analysis.get("momentum", 0), 2),
                "technical_action": analysis.get("action", "hold"),
                "technical_score": analysis.get("score", 0),
                "deep_analysis": not analysis.get("quick", True),
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


def _estimate_fees(trades: list[dict[str, Any]]) -> float:
    total = 0.0
    for t in trades:
        if t.get("type") in ("buy", "sell"):
            total += float(t.get("fee") or 0)
            if not t.get("fee") and t.get("eurTotal"):
                total += float(t["eurTotal"]) * FEE_RATE
    return round(total, 2)


def _build_symbol_performance(
    trades: list[dict[str, Any]],
    label_fn,
) -> list[dict[str, Any]]:
    """Symbolikohtainen voitto/tappio ja keskimääräinen pitoaika."""
    chronological = sorted(
        [t for t in trades if t.get("type") in ("buy", "sell")],
        key=lambda t: t.get("timestamp", ""),
    )
    open_lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "wins": 0,
            "losses": 0,
            "net_eur": 0.0,
            "hold_hours_win": [],
            "hold_hours_loss": [],
            "buys": 0,
            "sells": 0,
        }
    )

    for trade in chronological:
        sym = trade.get("symbol")
        if not sym:
            continue
        if trade["type"] == "buy":
            stats[sym]["buys"] += 1
            try:
                open_lots[sym].append(
                    {
                        "time": _parse_trade_time(trade["timestamp"]),
                        "amount": float(trade.get("amount") or 0),
                    }
                )
            except (ValueError, TypeError, KeyError):
                continue
            continue

        stats[sym]["sells"] += 1
        pl = _sell_profit_eur(trade)
        stats[sym]["net_eur"] += pl
        try:
            sell_time = _parse_trade_time(trade["timestamp"])
        except (ValueError, TypeError, KeyError):
            sell_time = None

        sell_amount = float(trade.get("amount") or 0)
        hold_hours: list[float] = []
        while sell_amount > 1e-12 and open_lots[sym]:
            lot = open_lots[sym][0]
            take = min(sell_amount, lot["amount"])
            if sell_time:
                hold_hours.append((sell_time - lot["time"]).total_seconds() / 3600)
            lot["amount"] -= take
            sell_amount -= take
            if lot["amount"] <= 1e-12:
                open_lots[sym].pop(0)

        if pl > 0.01:
            stats[sym]["wins"] += 1
            stats[sym]["hold_hours_win"].extend(hold_hours)
        elif pl < -0.01:
            stats[sym]["losses"] += 1
            stats[sym]["hold_hours_loss"].extend(hold_hours)

    rows: list[dict[str, Any]] = []
    for sym, data in stats.items():
        if data["sells"] == 0 and data["buys"] == 0:
            continue
        avg_win_h = (
            round(sum(data["hold_hours_win"]) / len(data["hold_hours_win"]), 1)
            if data["hold_hours_win"]
            else None
        )
        avg_loss_h = (
            round(sum(data["hold_hours_loss"]) / len(data["hold_hours_loss"]), 1)
            if data["hold_hours_loss"]
            else None
        )
        net = round(data["net_eur"], 2)
        note = ""
        if data["losses"] >= 2 and net < -5:
            note = "vältä uudelleenostoa ilman selkeää käännettä"
        elif data["wins"] >= 2 and net > 5:
            note = "toimiva linja — momentum/pitoaika ok"
        rows.append(
            {
                "symbol": sym,
                "label": label_fn(sym),
                "sells": data["sells"],
                "wins": data["wins"],
                "losses": data["losses"],
                "net_profit_eur": net,
                "avg_hold_hours_on_wins": avg_win_h,
                "avg_hold_hours_on_losses": avg_loss_h,
                "note": note,
            }
        )

    rows.sort(key=lambda r: r["net_profit_eur"])
    return rows[:12]


def _build_last_gemini_review(
    last_snapshot: dict[str, Any] | None,
    total_value: float,
    label_fn,
) -> dict[str, Any] | None:
    if not last_snapshot or not last_snapshot.get("top_picks"):
        return None
    try:
        snap_time = _parse_trade_time(last_snapshot["timestamp"])
        minutes_ago = int((datetime.now(timezone.utc) - snap_time).total_seconds() / 60)
    except (ValueError, TypeError, KeyError):
        minutes_ago = None

    snap_value = float(last_snapshot.get("total_value") or 0)
    change_pct = None
    if snap_value > 0:
        change_pct = round(((total_value - snap_value) / snap_value) * 100, 2)

    picks = [label_fn(str(s)) for s in last_snapshot.get("top_picks", [])[:4]]
    lesson = ""
    if change_pct is not None:
        if change_pct >= 0.5:
            lesson = "Viime valinnat toimivat — jatka samankaltaista linjaa"
        elif change_pct <= -0.5:
            lesson = "Viime valinnat heikensivät salkkua — harkitse rotaatiota varovaisemmin"
        else:
            lesson = "Viime valinnoilla vähäinen vaikutus — odota selkeämpää signaalia ennen churnia"

    return {
        "minutes_ago": minutes_ago,
        "top_picks_labels": picks,
        "portfolio_change_pct_since": change_pct,
        "lesson": lesson,
    }


def _build_costs_and_churn(
    all_trades: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    week_ago = now - timedelta(days=7)
    day_ago = now - timedelta(hours=24)

    def in_period(trade: dict[str, Any], since: datetime) -> bool:
        try:
            return _parse_trade_time(trade["timestamp"]) >= since
        except (ValueError, TypeError, KeyError):
            return False

    week_trades = [t for t in all_trades if in_period(t, week_ago)]
    day_trades = [t for t in all_trades if in_period(t, day_ago)]
    week_fees = _estimate_fees(week_trades)
    day_fees = _estimate_fees(day_trades)
    week_sells = _summarize_sells([t for t in week_trades if t["type"] == "sell"])

    # Bitfinex poisti kaupankäyntikulut → churn ei enää maksa kuluina. Ainoa
    # rotaation kustannus on 30 % voittovero realisoiduista voitoista, joten
    # varoita vain jos realisoinnit ovat olleet tappiollisia (turhaa pääoman
    # heittelyä) — ei enää kuluperusteella.
    churn_warning = ""
    week_count = len(week_trades)
    if week_count >= 15 and week_sells.get("net_profit_eur", 0) < 0:
        churn_warning = (
            f"Paljon kauppoja ({week_count}/7 pv) ja myynnit tappiolla "
            f"({week_sells.get('net_profit_eur', 0)} EUR) — vältä turhaa heittelyä"
        )

    return {
        "fee_rate_pct": round(FEE_RATE * 100, 2),
        "tax_on_realized_profits_pct": int(TAX_RATE * 100),
        "min_minutes_between_rotations": MIN_ROTATION_INTERVAL_MIN,
        "last_7_days": {
            "trade_count": week_count,
            "estimated_fees_eur": week_fees,
            "sell_net_profit_eur": week_sells.get("net_profit_eur", 0),
            "trades_per_day": round(week_count / 7, 1),
        },
        "last_24h": {
            "trade_count": len(day_trades),
            "estimated_fees_eur": day_fees,
        },
        "churn_warning": churn_warning or None,
    }


def _build_trade_history_summary(
    portfolio: dict[str, Any],
    label_fn,
    total_value: float,
    last_gemini_snapshot: dict[str, Any] | None = None,
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
        "by_symbol": _build_symbol_performance(all_trades, label_fn),
        "costs_and_churn": _build_costs_and_churn(all_trades, now),
        "last_gemini_review": _build_last_gemini_review(
            last_gemini_snapshot, total_value, label_fn
        ),
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


def _compact_learning(learning: dict[str, Any] | None) -> dict[str, Any]:
    """Tiivis oppimisyhteenveto promptiin — ei raakatilastoja/koko symbolimuistia (tokenit)."""
    if not learning:
        return {}
    mem = learning.get("symbol_memory") or {}
    losers = sorted(
        (s for s, m in mem.items() if (m.get("score_adjust") or 0) < 0),
        key=lambda s: mem[s].get("net_eur", 0),
    )[:6]
    winners = sorted(
        (s for s, m in mem.items() if (m.get("score_adjust") or 0) > 0),
        key=lambda s: mem[s].get("net_eur", 0),
        reverse=True,
    )[:6]
    return {
        "note": learning.get("note"),
        "rotation_enabled": learning.get("rotation_enabled"),
        "entry_score_min": learning.get("entry_score_min"),
        "overall_expectancy_eur": learning.get("overall_expectancy_eur"),
        "stats": learning.get("stats"),
        "blocked_buys": learning.get("blocked_buys"),
        "losers": losers,
        "winners": winners,
    }


def advise_portfolio(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    label_fn,
    last_gemini_snapshot: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
    learning: dict[str, Any] | None = None,
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

    # Kustannussäästö: lähetä vain top 20 volyymillä + omistukset Geminille,
    # mutta scan_leaders esikarsii KOKO markkinan teknisesti (ilmaiseksi),
    # joten mikään potentiaalinen ei jää huomaamatta.
    total_pairs = sum(1 for s in tickers if not is_stablecoin(s))
    market = _build_market_summary(tickers, analyses, portfolio, label_fn, limit=20)
    market_count = total_pairs
    scan_leaders = _build_scan_leaders(tickers, analyses, label_fn)
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
    trade_history = _build_trade_history_summary(
        portfolio, label_fn, total_value, last_gemini_snapshot
    )
    costs = trade_history.get("costs_and_churn") or {}
    regime_json = json.dumps(regime or {}, ensure_ascii=False)
    learning_json = json.dumps(_compact_learning(learning), ensure_ascii=False)
    market_setups_json = json.dumps((learning or {}).get("market_setups") or {}, ensure_ascii=False)
    prompt = f"""Olet aggressiivinen krypto-salkunhoitaja. AINOA TAVOITE: maksimoida salkun voitto (EUR).

Paper trading, ei oikeaa rahaa — silti pyri aina kasvattamaan salkun arvoa alkupääomasta (1000 EUR).

Markkinaregiimi (BTC-trendi & markkinaleveys):
{regime_json}
- bull → voit olla aggressiivinen momentumissa
- neutral → valikoi vahvimmat, vältä heikkoja
- bear → defensiivinen: vain vahvimmat aikajänteet linjassa (mtfAlign=1), ei putoavia veitsiä, pienempi positiomäärä

Oppiminen omasta historiasta (expectancy per kauppatyyppi + symbolimuisti):
{learning_json}
- Jos rotation-expectancy negatiivinen → ÄLÄ rotatoi pienistä syistä, pidä voittajia
- Painota kauppatyyppejä joilla positiivinen expectancy
- blocked_buys = älä osta näitä nyt (tuore tappio); losers = vältä, winners = suosi

Koko markkinan varjo-oppiminen (olosuhde → toteutunut 1h tuotto, setup = "regiimi|24h-haarukka"):
{market_setups_json}
- best = historiallisesti tuottoisin asetelma, worst = häviävin → vältä worst-tyyppisiä ostoja

Kustannukset:
- Kaupankäyntikulu: {costs.get('fee_rate_pct', 0)} % — Bitfinex POISTI kaupankäyntikulut kokonaan, joten ostot/myynnit ovat ILMAISIA. Rotaatio ei enää maksa kuluja.
- Voittovero: {costs.get('tax_on_realized_profits_pct', 30)} % realisoiduista voitoista maksetaan ERIKSEEN (ei vähennetä salkusta) — ei estä rotaatiota. Anna silti selkeiden voittajien juosta.
- Rotaatiota max kerran {costs.get('min_minutes_between_rotations', 30)} min (paitsi stop-loss / voitto-myynti)
- Koska kuluja ei ole, voit rotatoida vapaammin heikoista vahvempiin — vältä silti turhaa noise-heittelyä ja tappioiden lukitsemista

Salkun tila nyt:
- Arvo yhteensä: {total_value} EUR (P/L {portfolio_pnl_pct:+.2f} % vs alkupääoma)
- Käteinen: {cash} EUR
- Positiot: {json.dumps(held, ensure_ascii=False)}

Kauppahistoria ja palaute (opettele näistä — älä toista tappiollisia linjauksia):
{json.dumps(trade_history, ensure_ascii=False)}

Historian käyttö:
- recent_trades_newest_first: mitkä ostot/myynnit johtivat voittoon vs tappioon
- by_symbol: symbolikohtainen netto, voitto/tappio-määrät, keskimääräinen pitoaika — vältä toistuvasti tappiollisia
- last_gemini_review: arvioi edellisen päätöksesi onnistuminen ennen uutta rotaatiota
- costs_and_churn: kuluja ei ole — keskity siihen ettet realisoi voittoja turhaan (30 % vero) etkä lukitse tappioita ilman syytä
- Jos sama krypto myyty tappiolla usein → vältä uudelleenostoa ilman selkeää käännettä (RSI<40, EMA bullish)
- Voittavilla symboleilla pidä pidempään (katso avg_hold_hours_on_wins)

Kaupankäyntisäännöt (voitto edellä):
1. Myy heikot positiot (position_pnl_pct < -1 % tai 24h lasku) — vapauta pääoma vahvempiin
2. Osta nousussa olevia, korkean volyymin kohteita — tarkista change_1h_pct, change_4h_pct, change_24h_pct, RSI, ema_trend
3. Älä pidä tappiollisia pitkään — rotaatio nopeasti MUTTA älä churnaa (max 1 rotaatio / 30 min)
4. Salkussa 1–5 kryptoa — valitse ITSE montako (top_picks 1–5 kohdetta). EI pakko viittä; 1 vahva riittää. KAIKKI pääoma aina kryptoissa — käteistä EI jätetä odottamaan (allocations summa ≈ 100 %).
5. Rotaatio osittain: voit myydä osan positioista ja ostaa sillä uutta — ei pakko myydä koko positioa kerralla.
6. ÄLÄ osta stablecoineja (USDT, USDC, UDC, STABLE, DAI jne.)
7. Voitto-positio: ÄLÄ myy nousuputkessa — pidä kunnes hinta tasaantuu tai laskee hieman huipusta; automaattinen voitto-myynti +2 %:sta vasta tasaantumisen jälkeen
8. Stop-loss noin -2 %: älä anna tappioiden kasvaa
9. Vältä ostamasta ylikuumentuneita (RSI > 70 tai change_24h_pct > 12) ellei selkeää jatkoa
10. Priorisoi kohteet joissa deep_analysis=true JA technical_score korkea JA ema_trend=bullish
11. Perustele hintaliike AINA datan muutos-%:llä (change_1h_pct, change_24h_pct) — älä keksi “massiivista nousua” jos 24h on alle +2 %

TEHTÄVÄ — KOKO MARKKINA esikarsittu ({market_count} kryptoparia, EI stablecoineja):
- momentum_johtajat = paras tekninen esikarsinta KAIKISTA {market_count} parista (ilmainen laskenta) — käytä tätä koko markkinan kattavuuteen
- markkinadata = top 20 volyymillä + omistukset (yksityiskohtainen data) — vertaa jokaista nykyisiin positioihin
- top_picks = parhaat 1–5 (momentum_johtajat + markkinadata yhdessä; ei vain salkun omistuksia, ellei ne ole oikeasti parhaita)
- Jos salkussa oleva on heikoin tekninen_score / momentum → ehdota parempaa johtajalistalta
- signals: jokainen held-positio + KAIKKI top_picks + vähintään 3 parasta momentum_johtajaa joita et osta (action hold/buy)

momentum_johtajat (tekninen esikarsinta KAIKISTA {market_count} parista):
{json.dumps(scan_leaders, ensure_ascii=False)}

Markkinadata — top 20 volyymillä + omistukset, stablecoinit pois (change_1h/4h/24h, RSI, EMA-trendi, momentum):
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

top_picks = 1–5 parasta VOITTOON tähtaisevaa kohdetta KOKO markkinadata-listasta (symbol täsmälleen datasta).
allocations = sijoitusosuudet VAIN valituille top_picks (alloc_pct, summa = 100). EI tasajaot, EI käteistä sivuun.
signals = held-positiot + top_picks + vähintään 3 parasta momentum_johtajaa (max 20 riviä). alloc_pct vain buy-kohteille JSON-kentässä — ÄLÄ kirjoita prosentteja reason-kenttään (ne näytetään erikseen salkun osuutena).
Priorisoi: myy tappiolliset, osta momentum-nousuja, keskitä pääoma parhaisiin. Voitolla olevia pidä nousussa.
Perustele päätökset myös historiasta: mitä opit viime kaupoista."""

    api_key = _read_api_key()
    errors: list[str] = []
    transient_only = True
    configured_model = _read_model()

    for model in _model_candidates():
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        try:
            response = _post_with_retry(url, api_key, prompt)
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

            top_picks_fallback = False
            if not top_picks:
                top_picks = _technical_top_picks(analyses, 4)
                top_picks_fallback = True

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
                "marketScanned": market_count,
                "topPicksFallback": top_picks_fallback,
            }
            return insights, {
                "ok": True,
                "status": "ok",
                "message": (
                    f"Gemini skannasi {market_count} kryptoparia (ei stablecoineja) · "
                    f"{len(top_picks)} valintaa · {len(signals_map)} signaalia"
                ),
                "provider": "gemini",
                "model": model,
                "configured": True,
                "keyFormat": _key_format(),
            }

        except requests.RequestException as exc:
            detail = ""
            status_code = None
            if hasattr(exc, "response") and exc.response is not None:
                status_code = exc.response.status_code
                try:
                    err_body = exc.response.json()
                    detail = err_body.get("error", {}).get("message", "")[:160]
                except (ValueError, AttributeError):
                    detail = exc.response.text[:160] if exc.response.text else ""
            if status_code is not None and status_code not in RETRYABLE_STATUS:
                transient_only = False
            err_msg = detail or type(exc).__name__
            errors.append(f"{model}: {err_msg}")
            logger.warning("Gemini API error (%s): %s", model, err_msg)
            continue
        except (KeyError, IndexError, json.JSONDecodeError, ValueError, TypeError) as exc:
            transient_only = False
            err_msg = type(exc).__name__
            errors.append(f"{model}: {err_msg}")
            logger.warning("Gemini parse error (%s): %s", model, err_msg)
            continue

    primary_err = next((e for e in errors if e.startswith(f"{configured_model}:")), None)
    fallback_err = errors[0] if errors else ""
    detail = (primary_err or fallback_err).split(": ", 1)[-1] if (primary_err or fallback_err) else ""
    if transient_only and errors:
        msg = "Gemini ruuhkautunut — yritetään pian uudelleen, käytetään teknistä analyysiä tällä välin"
    else:
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


def generate_learning_narrative(
    structured_report: dict[str, Any],
    previous_narrative: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Gemini selittää oppimisen — erillinen 6 h raportti, ei muuta kaupankäyntisääntöjä."""
    if not is_configured():
        return None, {
            "ok": False,
            "message": _key_hint(),
            "provider": "technical",
            "configured": False,
        }

    sections_text = json.dumps(structured_report.get("sections") or [], ensure_ascii=False)
    changes_text = json.dumps(structured_report.get("changes") or [], ensure_ascii=False)
    roadmap_text = json.dumps(structured_report.get("roadmap") or [], ensure_ascii=False)
    prev_text = json.dumps(previous_narrative or {}, ensure_ascii=False)

    prompt = f"""Olet krypto-simulaattorin oppimisraportin kirjoittaja. Kirjoita sijoittajalle selkeä, vapaamuotoinen kertomus suomeksi.

TÄRKEÄÄ:
- Kaikki säätöpäätökset on JO toteutettu koodissa (learning.py). Älä keksi uusia automaattisia sääntöjä.
- Perustu vain annettuun dataan — älä keksi kauppoja tai lukuja.
- "story" = pääteksti: luettava kertomus 3–5 kappaletta (ei luetteloa).
- "ideas" = erillinen lyhyt kappale: 1–2 ehdotusta ihmiselle — EI vielä käytössä bottiin.

Sisällytä kertomukseen:
1) Mitä botti on oppinut (markkina-asetelmat, kauppatyypit, Gemini-conf, symbolit)
2) Miten oppia jo hyödynnetään käytännössä (rotaatio, estot, suosikit)
3) Mitä odotetaan seuraavaksi (roadmap)
4) Rehellinen arvio: missä dataa on vielä vähän

Oppimisdata:
{sections_text}

Muutokset edelliseen raporttiin:
{changes_text}

Roadmap:
{roadmap_text}

Edellinen kertomus (viite):
{prev_text}

Vastaa VAIN validilla JSON:lla:
{{
  "story": "Vapaamuotoinen kertomus 3–5 kappaletta. Erota kappaleet tyhjällä rivillä (\\\\n\\\\n).",
  "intro": "Yksi lause: tilanne nyt",
  "learned": "Lyhyt bullet-lista uusista oivalluksista (valinnainen, \\\\n)",
  "in_use": "Lyhyt bullet-lista: mitä jo tehdään eri tavalla (valinnainen, \\\\n)",
  "next_steps": "Mitä aktivoituu kun dataa kertyy (1–2 lausetta)",
  "ideas": "Ehdotukset — selvästi merkittynä ettei vielä käytössä (1 kappale)"
}}"""

    api_key = _read_api_key()
    configured_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
    models = _model_candidates(configured_model)

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            response = _post_with_retry(url, api_key, prompt)
            response.raise_for_status()
            body = response.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json(text)
            narrative = {
                "story": str(parsed.get("story") or "").strip(),
                "intro": str(parsed.get("intro") or "").strip(),
                "learned": str(parsed.get("learned") or "").strip(),
                "in_use": str(parsed.get("in_use") or "").strip(),
                "next_steps": str(parsed.get("next_steps") or "").strip(),
                "ideas": str(parsed.get("ideas") or "").strip(),
                "source": "gemini",
                "model": model,
            }
            return narrative, {
                "ok": True,
                "message": "Oppimisraportti päivitetty",
                "provider": "gemini",
                "model": model,
                "configured": True,
            }
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Learning narrative Gemini error (%s): %s", model, exc)
            continue

    return None, {
        "ok": False,
        "message": "Oppimisraportin Gemini-kutsu epäonnistui",
        "provider": "gemini",
        "configured": True,
    }
