from typing import Any

from trading.models import BotState

from .bitfinex import normalize_symbol
from .session_state import default_state

logger = __import__("logging").getLogger(__name__)


def _normalize_state_symbols(state: dict[str, Any]) -> None:
    """Korjaa vanhat Bitfinex-symbolit (tBTC:USD → tBTCUSD) tietokannassa."""
    portfolio = state.get("portfolio", {})
    holdings = portfolio.get("holdings", {})
    if holdings:
        portfolio["holdings"] = {
            normalize_symbol(sym): data for sym, data in holdings.items()
        }

    for key in ("analyses", "profitWatch", "watches"):
        bucket = state.get(key)
        if not isinstance(bucket, dict):
            continue
        normalized = {normalize_symbol(sym): val for sym, val in bucket.items()}
        bucket.clear()
        bucket.update(normalized)


def load_state() -> dict[str, Any]:
    obj, created = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
    if created:
        state = obj.data
        state["running"] = True
        save_state(state)
        return state
    state = obj.data
    _normalize_state_symbols(state)
    return state


def save_state(state: dict[str, Any]) -> None:
    BotState.objects.update_or_create(pk=1, defaults={"data": state})
