import os
from typing import Any

from trading.models import BotState

from .bitfinex import normalize_symbol
from .session_state import default_state

logger = __import__("logging").getLogger(__name__)


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
    obj, created = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
    if created:
        state = obj.data
        state["running"] = True
        _ensure_bot_started_at(state)
        save_state(state)
        return state
    state = obj.data
    changed = _normalize_state_symbols(state)
    if _repair_legacy_tax_withdrawals(state):
        changed = True
    if _ensure_bot_started_at(state):
        changed = True
    if repair_persisted_state(state):
        changed = True
    if changed:
        save_state(state)
    return state


def repair_persisted_state(state: dict[str, Any]) -> bool:
    """Korjaa tunnetut vanhentuneet tilavirheet deployn jälkeen."""
    from .learning_report import clear_stale_narrative_error

    changed = clear_stale_narrative_error(state)
    err = state.get("learningNarrativeError") or (state.get("learningReport") or {}).get("narrativeError")
    if err and "_model_candidates" in str(err):
        changed = clear_stale_narrative_error(state) or changed
    return changed


def save_state(state: dict[str, Any]) -> None:
    BotState.objects.update_or_create(pk=1, defaults={"data": state})
