from typing import Any

from trading.models import BotState

from .bitfinex import normalize_symbol
from .session_state import default_state

logger = __import__("logging").getLogger(__name__)


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
        save_state(state)
        return state
    state = obj.data
    changed = _normalize_state_symbols(state)
    if _repair_legacy_tax_withdrawals(state):
        changed = True
    if changed:
        save_state(state)
    return state


def save_state(state: dict[str, Any]) -> None:
    BotState.objects.update_or_create(pk=1, defaults={"data": state})
