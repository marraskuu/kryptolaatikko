"""Gemini top_pick -seuranta: pick-kohtainen tuotto snapshotista ja historia."""

from __future__ import annotations

from typing import Any, Callable

from .gemini import _build_gemini_pick_scorecard

GEMINI_PICK_HISTORY_LIMIT = 40
RECENT_ROUNDS_UI = 5
MIN_PICK_ROUNDS = 3
MIN_PICKS_TRACKED = 8
PICK_SCALE_MIN = 0.35
PICK_SCALE_MAX = 1.0


def _aggregate_stats(history: list[dict[str, Any]]) -> dict[str, Any]:
    returns: list[float] = []
    pick_beats_skipped = 0
    comparisons = 0

    for rnd in history:
        sc = rnd.get("scorecard") or {}
        for p in sc.get("pick_outcomes") or []:
            ret = p.get("return_since_pct")
            if ret is not None:
                returns.append(float(ret))
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
            "win_rate_pct": None,
            "avg_return_pct": None,
            "pick_beats_skipped_pct": None,
        }

    wins = sum(1 for r in returns if r > 0.05)
    return {
        "rounds": len(history),
        "picks_tracked": n,
        "win_rate_pct": round(wins / n * 100, 1),
        "avg_return_pct": round(sum(returns) / n, 2),
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
        "lesson": lessons[0] if lessons else None,
    }


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

    scorecard = _build_gemini_pick_scorecard(snapshot, tickers, total_value, label_fn)
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
    current = None
    if snapshot and snapshot.get("picks") and tickers:
        current = _build_gemini_pick_scorecard(snapshot, tickers, total_value, label_fn)

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
    if n < MIN_PICKS_TRACKED or rounds < MIN_PICK_ROUNDS:
        if n > 0:
            notes.append(
                f"Gemini-pick-hillintä {n}/{MIN_PICKS_TRACKED} pickiä "
                f"({rounds}/{MIN_PICK_ROUNDS} kierrosta)"
            )
        return tuning, notes

    win_rate = stats.get("win_rate_pct")
    avg_ret = stats.get("avg_return_pct")
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
    if n < 1:
        current = tracking.get("current")
        if not current or not current.get("pick_outcomes"):
            return ["Gemini-pick-seuranta kerää dataa — odota seuraavaa analyysikierrosta"]
        lines = ["Seuraava kierros arkistoidaan kun Gemini päivittyy (~10 min):"]
        for p in current.get("pick_outcomes") or []:
            ret = p.get("return_since_pct")
            if ret is None:
                continue
            lines.append(f"  {p.get('label')}: {ret:+.1f} % (odottaa)")
        return lines

    lines = [
        (
            f"{stats.get('rounds', 0)} kierrosta · {n} pickiä · "
            f"osuu {stats.get('win_rate_pct', 0)} % · "
            f"keskituotto {stats.get('avg_return_pct', 0):+.2f} %"
        )
    ]
    beats = stats.get("pick_beats_skipped_pct")
    if beats is not None:
        lines.append(f"Pickit voittivat ohitetun parhaan {beats} % kierroksista")

    for rnd in (tracking.get("recent") or [])[:3]:
        ts = (rnd.get("timestamp") or "")[:16].replace("T", " ")
        lesson = rnd.get("lesson")
        if lesson:
            lines.append(f"{ts}: {lesson}")
    return lines
