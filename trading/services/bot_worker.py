import logging
import os
import threading
import time
from typing import Any

from .engine import execute_trading_cycle, refresh_prices
from .session_state import log_ai_event

logger = logging.getLogger(__name__)

_worker_started = False
_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_cycle_thread_lock = threading.Lock()
_cycle_thread_running = False

PRICE_INTERVAL_SEC = 15
TRADE_INTERVAL_SEC = 60
LEARNING_CHECK_SEC = 300
BOT_STALE_SEC = 90


def _schedule_analysis_tick() -> None:
    from .state_store import load_state, save_state

    state = load_state()
    state["lastTradeTick"] = int(time.time() * 1000)
    state["running"] = True
    save_state(state)


def _run_trading_cycle_async() -> None:
    global _cycle_thread_running
    try:
        execute_trading_cycle()
    except Exception:
        logger.exception("Kaupankäyntikierros epäonnistui")
    finally:
        with _cycle_thread_lock:
            _cycle_thread_running = False


def _refresh_learning_report_state() -> None:
    from .learning_report import (
        clear_stale_narrative_error,
        narrative_refresh_in_progress,
        needs_learning_report_refresh,
        needs_narrative_refresh,
        refresh_learning_report_if_due,
        refresh_narrative_if_due,
    )
    from .state_store import load_state, save_state

    state = load_state()
    changed = clear_stale_narrative_error(state)
    if needs_learning_report_refresh(state):
        refresh_learning_report_if_due(state)
        if not narrative_refresh_in_progress():
            save_state(state)
        elif changed:
            save_state(state)
    elif needs_narrative_refresh(state) or changed:
        refresh_narrative_if_due(state)
        if not narrative_refresh_in_progress():
            save_state(state)


def _refresh_learning_report_async() -> None:
    try:
        _refresh_learning_report_state()
    except Exception:
        logger.exception("Oppimisraportin taustapäivitys epäonnistui")


def _bot_loop() -> None:
    global _cycle_thread_running
    logger.info("Botti-worker käynnistyi (kurssit %ss, kaupat %ss)", PRICE_INTERVAL_SEC, TRADE_INTERVAL_SEC)
    last_trade = 0.0
    last_learning_check = 0.0

    while True:
        try:
            refresh_prices()
            now = time.time()

            if now - last_learning_check >= LEARNING_CHECK_SEC:
                threading.Thread(
                    target=_refresh_learning_report_async,
                    name="learning-report-refresh",
                    daemon=True,
                ).start()
                last_learning_check = now

            if now - last_trade >= TRADE_INTERVAL_SEC:
                with _cycle_thread_lock:
                    if not _cycle_thread_running:
                        _cycle_thread_running = True
                        last_trade = now
                        _schedule_analysis_tick()
                        threading.Thread(
                            target=_run_trading_cycle_async,
                            name="trading-cycle",
                            daemon=True,
                        ).start()
        except Exception:
            logger.exception("Botti-worker virhe")
        time.sleep(PRICE_INTERVAL_SEC)


def _worker_alive() -> bool:
    return _worker_thread is not None and _worker_thread.is_alive()


def start_bot_worker() -> None:
    global _worker_started, _worker_thread

    if os.environ.get("DISABLE_BOT_WORKER") == "1":
        logger.info("Botti-worker pois päältä (DISABLE_BOT_WORKER=1)")
        return

    with _worker_lock:
        if _worker_started and _worker_alive():
            return
        if _worker_started and not _worker_alive():
            logger.warning("Botti-worker oli kuollut — käynnistetään uudelleen")
        _worker_started = True

        from django.conf import settings
        from django.db import connection

        if connection.settings_dict.get("ENGINE", "").endswith("sqlite3") and not settings.DEBUG:
            logger.warning(
                "VAROITUS: tuotannossa käytössä SQLite (väliaikainen levy) — salkku ja "
                "oppimishistoria NOLLAUTUVAT joka deployssa. Aseta Railwayn MySQL-yhteys "
                "(MYSQL_URL / DATABASE_URL) jotta tila säilyy."
            )

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

        _worker_thread = threading.Thread(target=_bot_loop, name="crypto-bot-worker", daemon=True)
        _worker_thread.start()

        def _kick_learning_narrative() -> None:
            time.sleep(5)
            _refresh_learning_report_async()

        threading.Thread(target=_kick_learning_narrative, name="learning-narrative-kick", daemon=True).start()


def ensure_bot_worker() -> None:
    """Käynnistä worker uudelleen jos se on sammunut (deploy/uudelleenkäynnistys)."""
    start_bot_worker()


def get_worker_status() -> dict[str, Any]:
    """Julkinen worker-tila terveystarkastusta varten."""
    disabled = os.environ.get("DISABLE_BOT_WORKER") == "1"
    alive = _worker_alive()
    return {
        "disabled": disabled,
        "alive": alive,
        "priceIntervalSec": PRICE_INTERVAL_SEC,
        "tradeIntervalSec": TRADE_INTERVAL_SEC,
        "staleThresholdSec": BOT_STALE_SEC,
    }


def bot_stale_seconds(state: dict) -> float:
    last_ms = max(state.get("lastPriceTick") or 0, state.get("lastTradeTick") or 0)
    if not last_ms:
        return 9999.0
    return max(0.0, time.time() - last_ms / 1000)


def bot_is_stale(state: dict) -> bool:
    return bot_stale_seconds(state) >= BOT_STALE_SEC


def maybe_wake_bot(state: dict) -> None:
    """Herätä botti taustalla — ei koskaan blokkaa API-pyyntöä."""
    ensure_bot_worker()
    if not bot_is_stale(state):
        return
    logger.warning("Botti näyttää jumiutuneen (%.0f s) — taustaherätys", bot_stale_seconds(state))
    threading.Thread(target=refresh_prices, name="bot-wake-refresh", daemon=True).start()
    threading.Thread(target=_refresh_learning_report_async, name="bot-wake-learning", daemon=True).start()
