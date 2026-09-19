import os
import threading
from copy import deepcopy
from typing import Any

from trading.models import BotState

from .bitfinex import normalize_symbol
from .session_state import default_state

logger = __import__("logging").getLogger(__name__)

_state_lock = threading.RLock()

# Sisäinen avain — poistetaan ennen DB-tallennusta.
STATE_DELETED_KEYS = "__deletedKeys__"


def mark_state_keys_deleted(state: dict[str, Any], *keys: str) -> None:
    """Merkitse ylätason avaimet poistettaviksi seuraavassa save_state-kutsussa."""
    if not keys:
        return
    for key in keys:
        state.pop(key, None)
    pending = list(state.get(STATE_DELETED_KEYS) or [])
    for key in keys:
        if key not in pending:
            pending.append(key)
    state[STATE_DELETED_KEYS] = pending


def _portfolio_version(portfolio: dict[str, Any]) -> tuple[int, int]:
    return (int(portfolio.get("tradeId") or 0), len(portfolio.get("trades") or []))


def _merge_portfolio(latest: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Säilytä uudempi salkkuversio — estää vanhan snapshotin kaupan peruutuksen."""
    if _portfolio_version(snapshot) > _portfolio_version(latest):
        return deepcopy(snapshot)
    return deepcopy(latest)


def _merge_concurrent_state(latest: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Yhdistä DB:n tuorein tila ja tallentajan snapshot — säilytä avaimet joita snapshot ei koske."""
    merged = deepcopy(latest)
    for key, value in snapshot.items():
        if key == STATE_DELETED_KEYS:
            continue
        if key == "portfolio" and isinstance(value, dict):
            merged[key] = _merge_portfolio(merged.get("portfolio") or {}, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _load_state_unlocked() -> tuple[dict[str, Any], bool]:
    """Lataa tila lukon sisällä. Palauttaa (state, created)."""
    obj, created = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
    if created:
        state = deepcopy(obj.data)
        state["running"] = True
        _ensure_bot_started_at(state)
        return state, True
    return deepcopy(obj.data), False


def _ensure_bot_started_at(state: dict[str, Any]) -> bool:
    """Aseta botStartedAt kerran — ensimmäisestä kaupasta tai ympäristömuuttujasta."""
    if state.get("botStartedAt"):
        return False

    env = os.environ.get("BOT_STARTED_AT", "").strip()
    if env:
        state["botStartedAt"] = env
        return True

    trades = state.get("portfolio", {}).get("trades", [])
    timestamps = [
        t["timestamp"]
        for t in trades
        if t.get("type") in ("buy", "sell") and t.get("timestamp")
    ]
    if timestamps:
        state["botStartedAt"] = min(timestamps)
        return True

    from datetime import datetime, timezone

    state["botStartedAt"] = datetime.now(timezone.utc).isoformat()
    return True


def _normalize_state_symbols(state: dict[str, Any]) -> bool:
    """Korjaa vanhat Bitfinex-symbolit (tBTC:USD → tBTCUSD) tietokannassa."""
    changed = False
    portfolio = state.get("portfolio", {})
    holdings = portfolio.get("holdings", {})
    if holdings:
        normalized = {normalize_symbol(sym): data for sym, data in holdings.items()}
        if normalized != holdings:
            portfolio["holdings"] = normalized
            changed = True

    for key in ("analyses", "profitWatch", "watches"):
        bucket = state.get(key)
        if not isinstance(bucket, dict):
            continue
        normalized = {normalize_symbol(sym): val for sym, val in bucket.items()}
        if normalized != bucket:
            bucket.clear()
            bucket.update(normalized)
            changed = True
    return changed


def _repair_legacy_tax_withdrawals(state: dict[str, Any]) -> bool:
    """Palauta käteinen vanhoista vero-tapahtumista (ennen portfolio.py-korjausta).

    Aikaisemmin simulaattori vähensi 30 % veron suoraan käteisestä tax-tyyppisinä
    kauppoina. Nykyään vero on vain raportointia varten.
    """
    portfolio = state.get("portfolio")
    if not isinstance(portfolio, dict):
        return False
    trades = portfolio.get("trades")
    if not isinstance(trades, list):
        return False

    tax_trades = [t for t in trades if t.get("type") == "tax"]
    if not tax_trades:
        return False

    refund = sum(float(t.get("eurTotal") or 0.0) for t in tax_trades)
    if refund > 0:
        portfolio["cash"] = float(portfolio.get("cash") or 0.0) + refund
    portfolio["trades"] = [t for t in trades if t.get("type") != "tax"]
    logger.info("Palautettu %.2f € käteistä %d vanhasta vero-tapahtumasta", refund, len(tax_trades))
    return True


def load_state() -> dict[str, Any]:
    with _state_lock:
        state, created = _load_state_unlocked()
        if created:
            save_state(state)
            return deepcopy(state)

        changed = _normalize_state_symbols(state)
        if _repair_legacy_tax_withdrawals(state):
            changed = True
        if _ensure_bot_started_at(state):
            changed = True
        if repair_persisted_state(state):
            changed = True
        if changed:
            save_state(state)
        return deepcopy(state)


def repair_persisted_state(state: dict[str, Any]) -> bool:
    """Korjaa tunnetut vanhentuneet tilavirheet deployn jälkeen."""
    from .learning_report import clear_stale_narrative_error

    changed = clear_stale_narrative_error(state)
    err = state.get("learningNarrativeError") or (state.get("learningReport") or {}).get("narrativeError")
    if err and "_model_candidates" in str(err):
        changed = clear_stale_narrative_error(state) or changed
    return changed


def save_state(state: dict[str, Any]) -> None:
    with _state_lock:
        snapshot = deepcopy(state)
        deleted = list(snapshot.pop(STATE_DELETED_KEYS, None) or [])
        obj, _ = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
        merged = _merge_concurrent_state(obj.data or {}, snapshot)
        for key in deleted:
            merged.pop(key, None)
        merged.pop(STATE_DELETED_KEYS, None)
        obj.data = merged
        obj.save(update_fields=["data", "updated_at"])


def patch_state_keys(fragment: dict[str, Any]) -> None:
    """Päivitä vain valitut ylätason avaimet — ei ylikirjoita koko tilaa vanhalla snapshotilla."""
    if not fragment:
        return
    with _state_lock:
        snapshot = deepcopy(fragment)
        deleted = list(snapshot.pop(STATE_DELETED_KEYS, None) or [])
        obj, _ = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
        merged = deepcopy(obj.data or {})
        for key, value in snapshot.items():
            if key == "portfolio" and isinstance(value, dict):
                merged[key] = _merge_portfolio(merged.get("portfolio") or {}, value)
            else:
                merged[key] = deepcopy(value)
        for key in deleted:
            merged.pop(key, None)
        merged.pop(STATE_DELETED_KEYS, None)
        obj.data = merged
        obj.save(update_fields=["data", "updated_at"])


def patch_narrative_error_state(state: dict[str, Any]) -> None:
    """Tallenna virhe-/siivousavaimet ilman narratiivin ylikirjoitusta."""
    fragment: dict[str, Any] = {}
    for key in ("learningNarrativeError", "learningNarrativeErrorAt"):
        if key in state:
            fragment[key] = state[key]
    deleted = list(state.get(STATE_DELETED_KEYS) or [])
    if deleted:
        fragment[STATE_DELETED_KEYS] = deleted
    if fragment:
        patch_state_keys(fragment)
    report = state.get("learningReport")
    if isinstance(report, dict) and "narrativeError" not in report:
        patch_learning_report_pop_fields("narrativeError")


def patch_learning_narrative_success(
    *,
    narrative: dict[str, Any],
    narrative_at: str,
    history_entry: dict[str, Any],
    history_limit: int,
    fallback_report: dict[str, Any] | None = None,
    next_narrative_in_sec: int | None = None,
) -> None:
    """Tallenna Gemini-narratiivi koskematta tuoreisiin trading-/kurssikenttiin."""
    with _state_lock:
        obj, _ = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
        merged = deepcopy(obj.data or {})
        merged["lastLearningNarrativeAt"] = narrative_at
        merged["learningNarrative"] = deepcopy(narrative)
        merged.pop("learningNarrativeError", None)
        merged.pop("learningNarrativeErrorAt", None)
        merged.pop("learningNarrativePendingSince", None)

        history = list(merged.get("learningReportHistory") or [])
        history.insert(0, deepcopy(history_entry))
        merged["learningReportHistory"] = history[:history_limit]

        report = dict(merged.get("learningReport") or fallback_report or {})
        report["narrative"] = deepcopy(narrative)
        report["narrativePending"] = False
        report["lastNarrativeAt"] = narrative_at
        if next_narrative_in_sec is not None:
            report["nextNarrativeInSec"] = next_narrative_in_sec
        report.pop("narrativeError", None)
        merged["learningReport"] = report

        obj.data = merged
        obj.save(update_fields=["data", "updated_at"])


def patch_learning_narrative_error(
    *,
    message: str,
    error_at: str,
    fallback_report: dict[str, Any] | None = None,
    next_narrative_in_sec: int | None = None,
) -> None:
    """Tallenna Gemini-virhe ja pending-siivous ilman koko BotState-snapshotia."""
    with _state_lock:
        obj, _ = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
        merged = deepcopy(obj.data or {})
        merged["learningNarrativeError"] = message
        merged["learningNarrativeErrorAt"] = error_at
        merged.pop("learningNarrativePendingSince", None)

        report = dict(merged.get("learningReport") or fallback_report or {})
        report["narrativePending"] = False
        report["narrativeError"] = message
        if next_narrative_in_sec is not None:
            report["nextNarrativeInSec"] = next_narrative_in_sec
        merged["learningReport"] = report

        obj.data = merged
        obj.save(update_fields=["data", "updated_at"])


def patch_learning_report_pop_fields(*fields: str) -> None:
    """Poista kenttiä tallennetusta learningReportista."""
    if not fields:
        return
    with _state_lock:
        obj, _ = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
        merged = deepcopy(obj.data or {})
        report = dict(merged.get("learningReport") or {})
        changed = False
        for field in fields:
            if field in report:
                report.pop(field, None)
                changed = True
        if not changed:
            return
        merged["learningReport"] = report
        obj.data = merged
        obj.save(update_fields=["data", "updated_at"])


def patch_learning_report_rule_cards(
    *,
    last_learning_report_at: str | None = None,
    snapshot: Any = None,
    sections: Any = None,
    timestamp: Any = None,
    changes: Any = None,
) -> None:
    """Päivitä rule-kortit — säilytä narratiivi- ja virhekentät DB:stä."""
    with _state_lock:
        obj, _ = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
        merged = deepcopy(obj.data or {})
        if last_learning_report_at:
            merged["lastLearningReportAt"] = last_learning_report_at
        if snapshot is not None:
            merged["learningReportSnapshot"] = deepcopy(snapshot)
        report = dict(merged.get("learningReport") or {})
        if sections is not None:
            report["sections"] = deepcopy(sections)
        if timestamp is not None:
            report["timestamp"] = timestamp
        if changes is not None:
            report["changes"] = deepcopy(changes)
        merged["learningReport"] = report
        obj.data = merged
        obj.save(update_fields=["data", "updated_at"])
