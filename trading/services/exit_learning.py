"""
Huippumyynnin oppiminen: mitataan voitto-otosta jälkikäteen kuinka paljon
voittoa jäi pöydälle vs. annettiin takaisin ennen myyntiä.

Tallennus BotState pk=4 — ei kuormita päätilaa.
"""

from __future__ import annotations

import time
from typing import Any

from .market_learning import _mtf_token, _regime_str, _rsi_bucket
from .market_microstructure import book_bucket, crowd_bucket

HORIZONS = {"1h": 3600, "4h": 14400}
MAX_HORIZON_SEC = 14400
EVAL_SLACK_SEC = 1800
MIN_SAMPLES_LIGHT = 4
MIN_SAMPLES = 10
MAX_OBS = 2000
BUCKET_CAP_N = 200
DECAY = 0.85
GIVEBACK_TIGHTEN_PCT = 1.0
LEFT_ON_TABLE_LOOSE_PCT = 1.2

_DEFAULT = {"obs": [], "stats": {}}


def _load() -> dict[str, Any]:
    from trading.models import BotState

    obj, _ = BotState.objects.get_or_create(pk=4, defaults={"data": dict(_DEFAULT)})
    data = obj.data or {}
    data.setdefault("obs", [])
    data.setdefault("stats", {})
    return data


def _save(data: dict[str, Any]) -> None:
    from trading.models import BotState

    BotState.objects.update_or_create(pk=4, defaults={"data": data})


def _profit_bucket(profit_pct: float) -> str:
    if profit_pct < 2:
        return "p1"
    if profit_pct < 4:
        return "p2"
    if profit_pct < 7:
        return "p3"
    return "p4"


def exit_setup_key_for_analysis(
    analysis: dict[str, Any] | None,
    regime: Any,
    profit_pct: float,
) -> str:
    """Myyntihetken asetelma: regiimi × voitto-% × RSI × MTF × book × crowd."""
    reg = _regime_str(regime)
    pb = _profit_bucket(profit_pct)
    if not analysis:
        return f"exit|{reg}|{pb}|rsi_md|mtf0|bk0|cr0"

    rsi = _rsi_bucket(analysis.get("rsi"))
    mtf = _mtf_token(analysis.get("mtfAlign"))
    book = analysis.get("bookBucket") or book_bucket(analysis.get("bookImbalance"))
    crowd = analysis.get("crowdBucket") or crowd_bucket(analysis.get("longShortRatio"))
    return f"exit|{reg}|{pb}|{rsi}|{mtf}|{book}|{crowd}"


def _exit_keys_fallback(analysis: dict[str, Any] | None, regime: Any, profit_pct: float) -> list[str]:
    full = exit_setup_key_for_analysis(analysis, regime, profit_pct)
    parts = full.split("|")
    if len(parts) < 7:
        return [full]
    reg, pb, rsi, mtf, book, crowd = parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
    return [
        full,
        f"exit|{reg}|{pb}|{rsi}|{mtf}|{book}",
        f"exit|{reg}|{pb}|{rsi}|{mtf}",
        f"exit|{reg}|{pb}|{rsi}",
        f"exit|{reg}|{pb}",
    ]


def _update_stat(
    stats: dict[str, Any],
    key: str,
    *,
    giveback: float,
    left_on_table: float,
) -> None:
    st = stats.setdefault(key, {"n": 0.0, "giveback_sum": 0.0, "left_sum": 0.0})
    st["n"] += 1.0
    st["giveback_sum"] += giveback
    st["left_sum"] += left_on_table
    if st["n"] > BUCKET_CAP_N:
        st["n"] *= DECAY
        st["giveback_sum"] *= DECAY
        st["left_sum"] *= DECAY


def record_profit_take_exit(
    *,
    symbol: str,
    sell_price: float,
    peak_price: float,
    profit_pct: float,
    pullback_pct: float,
    exit_setup: str,
    trade_id: int | None = None,
) -> None:
    """Kirjaa voitto-myynti arvioitavaksi (1h/4h jälkiseuranta)."""
    if sell_price <= 0 or peak_price <= 0:
        return
    store = _load()
    obs: list[dict[str, Any]] = store["obs"]
    if len(obs) >= MAX_OBS:
        obs.pop(0)
    obs.append(
        {
            "s": symbol,
            "t": int(time.time() * 1000),
            "sell": sell_price,
            "peak": peak_price,
            "profitPct": round(profit_pct, 2),
            "giveback": round(pullback_pct, 3),
            "setup": exit_setup,
            "maxHigh": sell_price,
            "done": {},
            "tradeId": trade_id,
        }
    )
    store["obs"] = obs
    _save(store)


def step(tickers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Päivitä odottavat havainnot ja palauta yhteenveto."""
    store = _load()
    now = int(time.time() * 1000)
    obs: list[dict[str, Any]] = store["obs"]
    stats: dict[str, Any] = store["stats"]
    survivors: list[dict[str, Any]] = []
    completed = 0

    for o in obs:
        sym = o.get("s")
        tk = tickers.get(sym) if sym else None
        price_now = float(tk["last"]) if tk and tk.get("last") else None
        age_sec = (now - o.get("t", now)) / 1000.0
        done = dict(o.get("done") or {})

        if price_now and price_now > float(o.get("maxHigh") or 0):
            o["maxHigh"] = price_now

        sell_price = float(o.get("sell") or 0)
        max_high = float(o.get("maxHigh") or sell_price)
        left_on_table = ((max_high - sell_price) / sell_price * 100.0) if sell_price else 0.0
        giveback = float(o.get("giveback") or 0)
        setup = o.get("setup") or "exit|neutral|p1|rsi_md|mtf0|bk0|cr0"

        for hz, sec in HORIZONS.items():
            if hz in done or age_sec < sec:
                continue
            _update_stat(stats, setup, giveback=giveback, left_on_table=left_on_table)
            done[hz] = True
            completed += 1

        o["done"] = done
        if age_sec < MAX_HORIZON_SEC + EVAL_SLACK_SEC:
            survivors.append(o)

    store["obs"] = survivors
    store["stats"] = stats
    _save(store)

    setups_ready = sum(
        1 for st in stats.values() if int(st.get("n", 0)) >= MIN_SAMPLES_LIGHT
    )
    return {
        "pending": len(survivors),
        "completedThisStep": completed,
        "setupsTracked": len(stats),
        "setupsReady": setups_ready,
        "totalSamples": int(sum(st.get("n", 0) for st in stats.values())),
    }


def _stat_for_key(stats: dict[str, Any], keys: list[str]) -> dict[str, float] | None:
    for key in keys:
        st = stats.get(key)
        if not st:
            continue
        n = float(st.get("n") or 0)
        if n < MIN_SAMPLES_LIGHT:
            continue
        return {
            "n": n,
            "avg_giveback": float(st.get("giveback_sum") or 0) / n,
            "avg_left": float(st.get("left_sum") or 0) / n,
            "key": key,
        }
    return None


def adjustments_for_analysis(
    analysis: dict[str, Any] | None,
    regime: Any,
    profit_pct: float,
) -> dict[str, Any]:
    """Opittu säätö voitto-ottoon (tiukempi/löysempi trailing)."""
    store = _load()
    stats = store.get("stats") or {}
    keys = _exit_keys_fallback(analysis, regime, profit_pct)
    st = _stat_for_key(stats, keys)
    out: dict[str, Any] = {
        "stabilize_mult": 1.0,
        "pullback_mult": 1.0,
        "learned": False,
        "exit_setup": keys[0],
    }
    if not st:
        return out

    out["learned"] = True
    out["samples"] = int(st["n"])
    out["exit_setup"] = st["key"]
    avg_giveback = st["avg_giveback"]
    avg_left = st["avg_left"]

    if st["n"] >= MIN_SAMPLES and avg_giveback >= GIVEBACK_TIGHTEN_PCT and avg_left < 0.6:
        out["stabilize_mult"] = 0.72
        out["pullback_mult"] = 0.78
        out["note"] = "tiukempi huippumyynti (paljon giveback)"
    elif st["n"] >= MIN_SAMPLES_LIGHT and avg_giveback >= 0.7 and avg_left < 0.4:
        out["stabilize_mult"] = 0.85
        out["pullback_mult"] = 0.88
        out["note"] = "varovaisempi huippumyynti"
    elif avg_left >= LEFT_ON_TABLE_LOOSE_PCT:
        out["stabilize_mult"] = 1.15
        out["pullback_mult"] = 1.12
        out["note"] = "annetaan voittojen juosta"
    elif avg_left >= 0.7:
        out["stabilize_mult"] = 1.08
        out["pullback_mult"] = 1.05
        out["note"] = "hieman löysempi trailing"

    return out


def get_summary() -> dict[str, Any]:
    store = _load()
    stats = store.get("stats") or {}
    top: list[dict[str, Any]] = []
    for key, st in stats.items():
        n = float(st.get("n") or 0)
        if n < MIN_SAMPLES_LIGHT:
            continue
        top.append(
            {
                "setup": key,
                "n": int(n),
                "avg_giveback": round(float(st.get("giveback_sum") or 0) / n, 2),
                "avg_left_on_table": round(float(st.get("left_sum") or 0) / n, 2),
            }
        )
    top.sort(key=lambda x: (-x["n"], -x["avg_giveback"]))
    return {
        "pending": len(store.get("obs") or []),
        "setupsTracked": len(stats),
        "topSetups": top[:6],
    }
