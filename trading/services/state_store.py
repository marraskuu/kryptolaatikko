from typing import Any

from trading.models import BotState

from .session_state import default_state

logger = __import__("logging").getLogger(__name__)


def load_state() -> dict[str, Any]:
    obj, created = BotState.objects.get_or_create(pk=1, defaults={"data": default_state()})
    if created:
        state = obj.data
        state["running"] = True
        save_state(state)
        return state
    return obj.data


def save_state(state: dict[str, Any]) -> None:
    BotState.objects.update_or_create(pk=1, defaults={"data": state})
