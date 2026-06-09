import logging
import os
import threading
import time

from .engine import execute_trading_cycle, refresh_prices
from .session_state import log_ai_event

logger = logging.getLogger(__name__)

_worker_started = False
_worker_lock = threading.Lock()

PRICE_INTERVAL_SEC = 15
TRADE_INTERVAL_SEC = 60


def _bot_loop() -> None:
    logger.info("Botti-worker käynnistyi (kurssit %ss, kaupat %ss)", PRICE_INTERVAL_SEC, TRADE_INTERVAL_SEC)
    last_trade = 0.0

    while True:
        try:
            refresh_prices()
            now = time.time()
            if now - last_trade >= TRADE_INTERVAL_SEC:
                execute_trading_cycle()
                last_trade = now
        except Exception:
            logger.exception("Botti-worker virhe")
        time.sleep(PRICE_INTERVAL_SEC)


def start_bot_worker() -> None:
    global _worker_started

    if os.environ.get("DISABLE_BOT_WORKER") == "1":
        logger.info("Botti-worker pois päältä (DISABLE_BOT_WORKER=1)")
        return

    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    from .state_store import load_state, save_state

    state = load_state()
    if not state.get("running"):
        state["running"] = True
        log_ai_event(
            state,
            "info",
            "Botti",
            "Live-botti käynnissä — kaupankäynti automaattisesti 24/7",
        )
        save_state(state)

    thread = threading.Thread(target=_bot_loop, name="crypto-bot-worker", daemon=True)
    thread.start()
