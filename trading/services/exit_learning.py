"""
Huippumyynnin oppiminen: mitataan voitto-otosta jälkikäteen kuinka paljon
voittoa jäi pöydälle vs. annettiin takaisin ennen myyntiä.

Tallennus BotState pk=4 — ei kuormita päätilaa.
"""

from __future__ import annotations

from copy import deepcopy
import threading
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
_exit_learning_lock = threading.RLock()


def _load() -> dict[str, Any]:
    from trading.models import BotState

    obj, _ = BotState.objects.get_or_create(pk=4, defaults={"data": dict(_DEFAULT)})
    data = obj.data or {}
    data.setdefault("obs", [])
    data.setdefault("stats", {})
    return deepcopy(data)


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
    with _exit_learning_lock:
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
    with _exit_learning_lock:
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


def _is_profit_take_sell(trade: dict[str, Any]) -> bool:
    reason = (trade.get("reason") or "").lower()
    return trade.get("type") == "sell" and (
        "huipusta" in reason
        or "realisoidaan voitto" in reason
        or "kotiut" in reason
        or trade.get("exitSetup") is not None
        or trade.get("givebackPct") is not None
    )


def _closed_exit_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linked: list[dict[str, Any]] = []
    for trade in trades:
        if trade.get("type") != "sell" or not _is_profit_take_sell(trade):
            continue
        linked.append(
            {
                "symbol": trade.get("symbol"),
                "net_eur": round(
                    float(trade.get("profitLoss") or 0) - float(trade.get("fee") or 0),
                    2,
                ),
                "profit_pct_at_sell": trade.get("profitPctAtSell"),
                "giveback_pct": trade.get("givebackPct"),
                "exit_setup": trade.get("exitSetup"),
                "rsi": trade.get("rsi"),
                "mtf_align": trade.get("mtfAlign"),
                "book_bucket": trade.get("bookBucket"),
                "crowd_bucket": trade.get("crowdBucket"),
                "regime": trade.get("regime"),
            }
        )
    return linked


def build_gemini_context(
    portfolio: dict[str, Any],
    learning: dict[str, Any] | None = None,
    bot_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Konteksti Geminin oppimiskertomukseen — huippumyynti ja exit-setup-oppiminen."""
    learning = learning or {}
    bot_state = bot_state or {}
    trades = portfolio.get("trades") or []
    linked = _closed_exit_trades(trades)
    summary = get_summary()
    store = _load()
    stats = store.get("stats") or {}
    exit_state = bot_state.get("exitLearning") or learning.get("exit_learning") or {}

    total_net = round(sum(x["net_eur"] for x in linked), 2)
    wins = sum(1 for x in linked if x["net_eur"] > 0.01)
    losses = sum(1 for x in linked if x["net_eur"] < -0.01)
    with_meta = sum(1 for x in linked if x.get("exit_setup") or x.get("giveback_pct") is not None)
    avg_giveback = None
    givebacks = [float(x["giveback_pct"]) for x in linked if x.get("giveback_pct") is not None]
    if givebacks:
        avg_giveback = round(sum(givebacks) / len(givebacks), 2)

    by_rsi: dict[str, dict[str, float]] = {}
    for item in linked:
        setup = item.get("exit_setup") or ""
        rsi_token = setup.split("|")[3] if setup.startswith("exit|") and len(setup.split("|")) > 3 else "rsi_md"
        bucket = by_rsi.setdefault(rsi_token, {"n": 0.0, "giveback_sum": 0.0, "left_sum": 0.0})
        bucket["n"] += 1.0
        if item.get("giveback_pct") is not None:
            bucket["giveback_sum"] += float(item["giveback_pct"])

    setup_stats: list[dict[str, Any]] = []
    for key, st in stats.items():
        n = float(st.get("n") or 0)
        if n < MIN_SAMPLES_LIGHT:
            continue
        setup_stats.append(
            {
                "setup": key,
                "samples": int(n),
                "avg_giveback_pct": round(float(st.get("giveback_sum") or 0) / n, 2),
                "avg_left_on_table_pct": round(float(st.get("left_sum") or 0) / n, 2),
            }
        )
    setup_stats.sort(key=lambda x: (-x["samples"], -x["avg_giveback_pct"]))

    examples: list[dict[str, Any]] = []
    for item in sorted(linked, key=lambda x: float(x.get("giveback_pct") or 0))[:3]:
        if item.get("giveback_pct") is None:
            continue
        examples.append({**item, "type": "early_or_tight"})
    for item in sorted(linked, key=lambda x: float(x.get("giveback_pct") or 0), reverse=True)[:3]:
        if item.get("giveback_pct") is None:
            continue
        examples.append({**item, "type": "late_or_gave_back"})

    return {
        "enabled": True,
        "usage": {
            "dynamicSignals": [
                "RSI ≥ 72 → lyhyempi tasaantumisodotus + tiukempi trailing",
                "MTF negatiivinen → arming heti",
                "Order book bk- → tiukempi trailing",
                "Crowd crL → nopeampi reagointi",
                "Nopea lasku ≥ 0,5 % huipusta 15 min sisällä → myy heti",
                "Exit-setup-oppiminen (BotState pk=4) säätää trailingia datan perusteella",
            ],
            "fieldsOnSell": ["exitSetup", "givebackPct", "peakPriceAtSell", "profitPctAtSell"],
        },
        "operational": {
            "pendingEvaluations": int(exit_state.get("pending") or summary.get("pending") or 0),
            "setupsTracked": int(exit_state.get("setupsTracked") or summary.get("setupsTracked") or 0),
            "setupsReady": int(exit_state.get("setupsReady") or 0),
            "totalSamples": int(exit_state.get("totalSamples") or 0),
        },
        "closedExitsWithMeta": with_meta,
        "closedExitsTotal": len(linked),
        "closedExitsNetEur": total_net,
        "closedExitsWinRate": round(wins / len(linked), 2) if linked else None,
        "closedExitsWins": wins,
        "closedExitsLosses": losses,
        "avgGivebackPctAtSell": avg_giveback,
        "learnedExitSetups": setup_stats[:6],
        "topSetupsFromSummary": summary.get("topSetups") or [],
        "examples": examples[:6],
    }


def learning_report_lines(context: dict[str, Any]) -> list[str]:
    """Rule-pohjaiset rivit oppimisraportin korttiin."""
    if not context.get("enabled"):
        return ["Huippumyynti-oppiminen pois päältä"]

    lines: list[str] = []
    op = context.get("operational") or {}
    pending = int(op.get("pendingEvaluations") or 0)
    tracked = int(op.get("setupsTracked") or 0)
    ready = int(op.get("setupsReady") or 0)
    if tracked:
        lines.append(f"Exit-setuppeja: {ready}/{tracked} valmiina · {pending} odottaa arviointia")
    else:
        lines.append("Huippumyynti-oppiminen kerää dataa voitto-otoista")

    n = int(context.get("closedExitsTotal") or 0)
    meta_n = int(context.get("closedExitsWithMeta") or 0)
    if n == 0:
        lines.append("Ei vielä voitto-ottomyyntejä — dynaaminen trailing aktiivinen RSI/MTF/book-signaaleilla")
        return lines

    net = context.get("closedExitsNetEur")
    wr = context.get("closedExitsWinRate")
    lines.append(
        f"Voitto-otot: {n} kpl · netto {net:+.2f} €"
        + (f" · win rate {wr * 100:.0f} %" if wr is not None else "")
    )
    if meta_n:
        lines.append(f"Exit-metalla: {meta_n} kpl")
    avg_gb = context.get("avgGivebackPctAtSell")
    if avg_gb is not None:
        lines.append(f"Keskimääräinen giveback myynnissä: {avg_gb:.2f} % huipusta")

    for item in (context.get("learnedExitSetups") or [])[:2]:
        lines.append(
            f"{item['setup']}: giveback {item['avg_giveback_pct']:.2f} % · "
            f"jäi pöydälle {item['avg_left_on_table_pct']:.2f} % (n={item['samples']})"
        )

    return lines
