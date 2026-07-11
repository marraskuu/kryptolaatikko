"""Gemini top_pick -seuranta: pick-kohtainen tuotto snapshotista ja historia."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from .bitfinex import normalize_symbol
from .gemini import _build_gemini_pick_scorecard
from .trade_meta import entry_meta_from_trade

GEMINI_PICK_HISTORY_LIMIT = 40
RECENT_ROUNDS_UI = 5
MIN_PICK_ROUNDS = 3
MIN_PICKS_TRACKED = 8
PICK_SCALE_MIN = 0.35
PICK_SCALE_MAX = 1.0


def _parse_time(iso: Any) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _is_gemini_pick_buy(trade: dict[str, Any], top_picks: set[str]) -> bool:
    sym = normalize_symbol(str(trade.get("symbol") or ""))
    if sym not in top_picks:
        return False
    if trade.get("geminiPick"):
        return True
    meta = entry_meta_from_trade(trade)
    if meta.get("geminiPick"):
        return True
    if meta.get("geminiConfidence") is not None:
        return True
    reason = (trade.get("reason") or "").lower()
    return "gemini" in reason


def _fifo_executed_pick_outcomes(
    snapshot: dict[str, Any],
    trades: list[dict[str, Any]],
    tickers: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """FIFO P/L Geminin top_pick -ostoille snapshotin jälkeen."""
    snap_time = _parse_time(snapshot.get("timestamp"))
    if not snap_time or not trades:
        return {}

    top_picks = {
        normalize_symbol(str(s)) for s in (snapshot.get("top_picks") or []) if s
    }
    pick_syms = {
        normalize_symbol(str(p.get("symbol")))
        for p in (snapshot.get("picks") or [])
        if p.get("symbol")
    }
    relevant = top_picks | pick_syms
    if not relevant:
        return {}

    chronological = sorted(
        [t for t in trades if t.get("type") in ("buy", "sell") and t.get("symbol")],
        key=lambda t: t.get("timestamp", ""),
    )
    lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total_cost_eur": 0.0,
            "realized_pl_eur": 0.0,
            "open_cost_eur": 0.0,
            "open_amount": 0.0,
            "buy_count": 0,
        }
    )

    for trade in chronological:
        sym = normalize_symbol(str(trade["symbol"]))
        if sym not in relevant:
            continue

        if trade["type"] == "buy":
            trade_time = _parse_time(trade.get("timestamp"))
            is_pick = (
                trade_time is not None
                and trade_time >= snap_time
                and _is_gemini_pick_buy(trade, top_picks)
            )
            amount = float(trade.get("amount") or 0)
            price = float(trade.get("price") or 0)
            eur_total = float(trade.get("eurTotal") or amount * price)
            lots[sym].append(
                {
                    "amount": amount,
                    "price": price,
                    "is_pick": is_pick,
                }
            )
            if is_pick:
                bucket = stats[sym]
                bucket["total_cost_eur"] += eur_total
                bucket["open_cost_eur"] += eur_total
                bucket["open_amount"] += amount
                bucket["buy_count"] += 1
            continue

        sell_amount = float(trade.get("amount") or 0)
        sell_price = float(trade.get("price") or 0)
        while sell_amount > 1e-12 and lots[sym]:
            lot = lots[sym][0]
            take = min(sell_amount, lot["amount"])
            if lot.get("is_pick"):
                cost = take * lot["price"]
                proceeds = take * sell_price
                bucket = stats[sym]
                bucket["realized_pl_eur"] += proceeds - cost
                bucket["open_cost_eur"] -= cost
                bucket["open_amount"] -= take
            lot["amount"] -= take
            sell_amount -= take
            if lot["amount"] <= 1e-12:
                lots[sym].pop(0)

    outcomes: dict[str, dict[str, Any]] = {}
    for sym, bucket in stats.items():
        if bucket["buy_count"] < 1 or bucket["total_cost_eur"] <= 0:
            continue

        tk = tickers.get(sym) or tickers.get(f"t{sym}")
        current = float(tk["last"]) if tk and tk.get("last") else None
        unrealized = 0.0
        if current and bucket["open_amount"] > 1e-12:
            unrealized = bucket["open_amount"] * current - bucket["open_cost_eur"]

        pl_eur = bucket["realized_pl_eur"] + unrealized
        return_pct = round(pl_eur / bucket["total_cost_eur"] * 100, 2)

        if bucket["open_amount"] <= 1e-12:
            status = "closed"
        elif bucket["realized_pl_eur"] != 0:
            status = "partial"
        else:
            status = "open"

        outcomes[sym] = {
            "return_pct": return_pct,
            "pl_eur": round(pl_eur, 2),
            "status": status,
            "buy_count": bucket["buy_count"],
            "total_cost_eur": round(bucket["total_cost_eur"], 2),
        }
    return outcomes


def _recompute_pick_rankings(scorecard: dict[str, Any]) -> None:
    valid_picks = [
        p for p in scorecard.get("pick_outcomes") or [] if p.get("return_since_pct") is not None
    ]
    scorecard["best_pick"] = (
        max(valid_picks, key=lambda x: x["return_since_pct"]) if valid_picks else None
    )
    scorecard["worst_pick"] = (
        min(valid_picks, key=lambda x: x["return_since_pct"]) if valid_picks else None
    )


def _append_executed_lessons(
    scorecard: dict[str, Any],
    executed_count: int,
    hypothetical_count: int,
) -> None:
    lessons: list[str] = list(scorecard.get("lessons") or [])
    if executed_count:
        lessons.append(
            f"{executed_count} pickiä toteutui — tuotto laskettu FIFO-kaupoista"
            + (
                f" ({hypothetical_count} hypoteettista)"
                if hypothetical_count
                else ""
            )
        )
    for p in scorecard.get("pick_outcomes") or []:
        if not p.get("executed"):
            continue
        hyp = p.get("return_hypothetical_pct")
        ret = p.get("return_since_pct")
        if hyp is None or ret is None:
            continue
        gap = ret - hyp
        if abs(gap) >= 0.75:
            label = p.get("label") or p.get("symbol")
            lessons.append(
                f"Toteutunut {label}: {ret:+.1f} % vs hypoteettinen {hyp:+.1f} % "
                f"({p.get('executed_status')})"
            )
            break
    scorecard["lessons"] = lessons[:6]


def _apply_trade_micro_buckets(pick: dict[str, Any], meta: dict[str, Any]) -> None:
    if pick.get("entry_book_bucket"):
        return
    for src, dst in (
        ("bookBucket", "entry_book_bucket"),
        ("crowdBucket", "entry_crowd_bucket"),
        ("flowBucket", "entry_flow_bucket"),
    ):
        if meta.get(src) and not pick.get(dst):
            pick[dst] = meta[src]


def _enrich_pick_micro_from_trades(
    scorecard: dict[str, Any],
    snapshot: dict[str, Any],
    trades: list[dict[str, Any]],
) -> None:
    """Täydennä pick_scorecard micro-bucketit ostotapahtuman metasta jos snapshot puuttuu."""
    snap_time = _parse_time(snapshot.get("timestamp"))
    if not snap_time:
        return

    top_picks = {
        normalize_symbol(str(s)) for s in (snapshot.get("top_picks") or []) if s
    }
    pick_meta: dict[str, dict[str, Any]] = {}
    for trade in sorted(trades, key=lambda t: t.get("timestamp", "")):
        if trade.get("type") != "buy":
            continue
        sym = normalize_symbol(str(trade.get("symbol") or ""))
        if sym in pick_meta:
            continue
        trade_time = _parse_time(trade.get("timestamp"))
        if trade_time is None or trade_time < snap_time:
            continue
        if not _is_gemini_pick_buy(trade, top_picks):
            continue
        meta = entry_meta_from_trade(trade)
        if meta.get("bookBucket") or meta.get("crowdBucket") or meta.get("flowBucket"):
            pick_meta[sym] = meta

    for pick in scorecard.get("pick_outcomes") or []:
        sym = normalize_symbol(str(pick.get("symbol") or ""))
        meta = pick_meta.get(sym)
        if meta:
            _apply_trade_micro_buckets(pick, meta)


def build_pick_scorecard(
    snapshot: dict[str, Any] | None,
    tickers: dict[str, dict[str, Any]],
    total_value: float,
    label_fn: Callable[[str], str],
    trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Scorecard snapshot-hinnasta; toteutuneet pick-ostot korvataan FIFO P/L:llä."""
    scorecard = _build_gemini_pick_scorecard(snapshot, tickers, total_value, label_fn)
    if not scorecard or not snapshot:
        return scorecard

    if trades:
        _enrich_pick_micro_from_trades(scorecard, snapshot, trades)

    if not trades:
        return scorecard

    executed = _fifo_executed_pick_outcomes(snapshot, trades, tickers)
    if not executed:
        return scorecard

    executed_count = 0
    hypothetical_count = 0
    for pick in scorecard.get("pick_outcomes") or []:
        sym = normalize_symbol(str(pick.get("symbol") or ""))
        ex = executed.get(sym)
        if not ex:
            pick["executed"] = False
            hypothetical_count += 1
            continue

        pick["executed"] = True
        pick["return_hypothetical_pct"] = pick.get("return_since_pct")
        pick["return_since_pct"] = ex["return_pct"]
        pick["executed_return_pct"] = ex["return_pct"]
        pick["executed_pl_eur"] = ex["pl_eur"]
        pick["executed_status"] = ex["status"]
        pick["executed_buy_count"] = ex["buy_count"]
        executed_count += 1

    scorecard["executed_picks"] = executed_count
    scorecard["hypothetical_picks"] = hypothetical_count
    _recompute_pick_rankings(scorecard)
    _append_executed_lessons(scorecard, executed_count, hypothetical_count)
    return scorecard


def _aggregate_stats(history: list[dict[str, Any]]) -> dict[str, Any]:
    returns: list[float] = []
    executed_returns: list[float] = []
    pick_beats_skipped = 0
    comparisons = 0

    for rnd in history:
        sc = rnd.get("scorecard") or {}
        for p in sc.get("pick_outcomes") or []:
            ret = p.get("return_since_pct")
            if ret is not None:
                returns.append(float(ret))
                if p.get("executed"):
                    executed_returns.append(float(ret))
        best_pick = sc.get("best_pick")
        best_skipped = sc.get("best_skipped")
        if (
            best_pick
            and best_skipped
            and best_pick.get("return_since_pct") is not None
            and best_skipped.get("return_since_pct") is not None
        ):
            comparisons += 1
            if float(best_pick["return_since_pct"]) >= float(best_skipped["return_since_pct"]):
                pick_beats_skipped += 1

    n = len(returns)
    if n == 0:
        return {
            "rounds": len(history),
            "picks_tracked": 0,
            "executed_picks_tracked": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "executed_win_rate_pct": None,
            "executed_avg_return_pct": None,
            "pick_beats_skipped_pct": None,
        }

    wins = sum(1 for r in returns if r > 0.05)
    exec_n = len(executed_returns)
    exec_wins = sum(1 for r in executed_returns if r > 0.05)
    return {
        "rounds": len(history),
        "picks_tracked": n,
        "executed_picks_tracked": exec_n,
        "win_rate_pct": round(wins / n * 100, 1),
        "avg_return_pct": round(sum(returns) / n, 2),
        "executed_win_rate_pct": round(exec_wins / exec_n * 100, 1) if exec_n else None,
        "executed_avg_return_pct": round(sum(executed_returns) / exec_n, 2) if exec_n else None,
        "pick_beats_skipped_pct": (
            round(pick_beats_skipped / comparisons * 100, 1) if comparisons else None
        ),
    }


def _compact_round(rnd: dict[str, Any]) -> dict[str, Any]:
    sc = rnd.get("scorecard") or {}
    picks = [
        {
            "label": p.get("label"),
            "return_pct": p.get("return_since_pct"),
            "setup": p.get("entry_setup"),
            "executed": p.get("executed"),
            "executed_status": p.get("executed_status"),
        }
        for p in (sc.get("pick_outcomes") or [])
    ]
    lessons = sc.get("lessons") or []
    return {
        "timestamp": rnd.get("timestamp"),
        "regime": rnd.get("regime"),
        "minutes": sc.get("minutes_since_snapshot"),
        "portfolio_change_pct": sc.get("portfolio_change_pct_since"),
        "picks": picks,
        "best_pick": sc.get("best_pick"),
        "best_skipped": sc.get("best_skipped"),
        "executed_picks": sc.get("executed_picks"),
        "lesson": lessons[0] if lessons else None,
    }


def _portfolio_trades(state: dict[str, Any]) -> list[dict[str, Any]]:
    portfolio = state.get("portfolio") or {}
    return list(portfolio.get("trades") or [])


def archive_previous_snapshot(
    state: dict[str, Any],
    tickers: dict[str, dict[str, Any]],
    total_value: float,
    label_fn: Callable[[str], str],
) -> bool:
    """Arkistoi edellisen Geminin snapshotin tulokset ennen uuden tallennusta."""
    snapshot = state.get("lastGeminiSnapshot")
    if not snapshot or not snapshot.get("picks"):
        return False

    scorecard = build_pick_scorecard(
        snapshot,
        tickers,
        total_value,
        label_fn,
        trades=_portfolio_trades(state),
    )
    if not scorecard:
        return False

    record = {
        "timestamp": snapshot.get("timestamp"),
        "regime": snapshot.get("regime"),
        "top_picks": snapshot.get("top_picks") or [],
        "snapshot_total_value": snapshot.get("total_value"),
        "scorecard": scorecard,
    }
    history: list[dict[str, Any]] = list(state.get("geminiPickHistory") or [])
    if history and history[0].get("timestamp") == record["timestamp"]:
        return False

    history.insert(0, record)
    state["geminiPickHistory"] = history[:GEMINI_PICK_HISTORY_LIMIT]
    state["geminiPickStats"] = _aggregate_stats(state["geminiPickHistory"])
    return True


def build_pick_tracking(
    state: dict[str, Any],
    tickers: dict[str, dict[str, Any]],
    total_value: float,
    label_fn: Callable[[str], str],
) -> dict[str, Any]:
    """Nykyinen odottava scorecard + arkistoitu historia UI:lle."""
    snapshot = state.get("lastGeminiSnapshot")
    trades = _portfolio_trades(state)
    current = None
    if snapshot and snapshot.get("picks") and tickers:
        current = build_pick_scorecard(snapshot, tickers, total_value, label_fn, trades=trades)

    history: list[dict[str, Any]] = list(state.get("geminiPickHistory") or [])
    stats = state.get("geminiPickStats") or _aggregate_stats(history)

    return {
        "current": current,
        "stats": stats,
        "recent": [_compact_round(r) for r in history[:RECENT_ROUNDS_UI]],
    }


def compute_pick_tuning(
    stats: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Programmatinen Gemini-pick -hillintä arkistoidun scorecard-datan perusteella."""
    stats = stats or {}
    notes: list[str] = []
    tuning: dict[str, Any] = {
        "gemini_buy_min_confidence": 5,
        "gemini_pick_buy_scale": 1.0,
    }

    rounds = int(stats.get("rounds") or 0)
    n = int(stats.get("picks_tracked") or 0)
    exec_n = int(stats.get("executed_picks_tracked") or 0)
    if n < MIN_PICKS_TRACKED or rounds < MIN_PICK_ROUNDS:
        if n > 0:
            notes.append(
                f"Gemini-pick-hillintä {n}/{MIN_PICKS_TRACKED} pickiä "
                f"({rounds}/{MIN_PICK_ROUNDS} kierrosta)"
            )
        return tuning, notes

    win_rate = stats.get("win_rate_pct")
    avg_ret = stats.get("avg_return_pct")
    if exec_n >= 3:
        win_rate = stats.get("executed_win_rate_pct")
        avg_ret = stats.get("executed_avg_return_pct")

    beats = stats.get("pick_beats_skipped_pct")

    min_conf = 5
    scale = 1.0

    if win_rate is not None:
        if win_rate < 20:
            min_conf = 7
            scale = 0.5
            notes.append(f"Gemini-pickit heikot ({win_rate:.0f} % osuu) — conf ≥7, osto 50 %")
        elif win_rate < 35:
            min_conf = 6
            scale = 0.7
            notes.append(f"Gemini-pickit alle normin ({win_rate:.0f} % osuu) — conf ≥6, osto 70 %")
        elif win_rate >= 45 and (avg_ret is None or avg_ret >= 0):
            notes.append(f"Gemini-pickit ok ({win_rate:.0f} % osuu)")

    if avg_ret is not None and avg_ret < -0.5:
        min_conf = max(min_conf, 6)
        scale = min(scale, 0.75)
        notes.append(f"Gemini-pickien keskituotto {avg_ret:+.2f} % — varovaisemmin")

    if beats is not None and rounds >= 5 and beats < 40:
        scale = min(scale, 0.6)
        min_conf = max(min_conf, 6)
        notes.append(f"Pickit häviävät ohituksille ({beats:.0f} % kierroksista)")

    tuning["gemini_buy_min_confidence"] = min_conf
    tuning["gemini_pick_buy_scale"] = max(
        PICK_SCALE_MIN, min(PICK_SCALE_MAX, round(scale, 2))
    )
    tuning["gemini_pick_stats"] = {
        "rounds": rounds,
        "picks_tracked": n,
        "executed_picks_tracked": exec_n,
        "win_rate_pct": win_rate,
        "avg_return_pct": avg_ret,
        "pick_beats_skipped_pct": beats,
    }
    return tuning, notes


def learning_report_lines(tracking: dict[str, Any] | None) -> list[str]:
    """Lyhyet rivit oppimisraportin staattiseen osioon."""
    if not tracking:
        return []
    stats = tracking.get("stats") or {}
    n = int(stats.get("picks_tracked") or 0)
    exec_n = int(stats.get("executed_picks_tracked") or 0)
    if n < 1:
        current = tracking.get("current")
        if not current or not current.get("pick_outcomes"):
            return ["Gemini-pick-seuranta kerää dataa — odota seuraavaa analyysikierrosta"]
        lines = ["Seuraava kierros arkistoidaan kun Gemini päivittyy (~10 min):"]
        for p in current.get("pick_outcomes") or []:
            ret = p.get("return_since_pct")
            if ret is None:
                continue
            suffix = ""
            if p.get("executed"):
                suffix = f" [FIFO {p.get('executed_status', 'open')}]"
            elif p.get("return_hypothetical_pct") is not None:
                suffix = " [hypoteettinen]"
            lines.append(f"  {p.get('label')}: {ret:+.1f} %{suffix} (odottaa)")
        return lines

    exec_note = f" · {exec_n} toteutunutta FIFO:lla" if exec_n else ""
    lines = [
        (
            f"{stats.get('rounds', 0)} kierrosta · {n} pickiä{exec_note} · "
            f"osuu {stats.get('win_rate_pct', 0)} % · "
            f"keskituotto {stats.get('avg_return_pct', 0):+.2f} %"
        )
    ]
    if exec_n >= 3 and stats.get("executed_avg_return_pct") is not None:
        lines.append(
            f"Toteutuneet pickit: osuu {stats.get('executed_win_rate_pct', 0)} % · "
            f"keski {stats.get('executed_avg_return_pct', 0):+.2f} %"
        )
    beats = stats.get("pick_beats_skipped_pct")
    if beats is not None:
        lines.append(f"Pickit voittivat ohitetun parhaan {beats} % kierroksista")

    for rnd in (tracking.get("recent") or [])[:3]:
        ts = (rnd.get("timestamp") or "")[:16].replace("T", " ")
        lesson = rnd.get("lesson")
        if lesson:
            lines.append(f"{ts}: {lesson}")
    return lines
