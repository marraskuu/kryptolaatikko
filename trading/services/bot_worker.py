import logging
import os
import threading
import time

from .engine import execute_trading_cycle, refresh_prices
from .session_state import log_ai_event

logger = logging.getLogger(__name__)

_worker_started = False
_worker_lock = threading.Lock()
_cycle_thread_lock = threading.Lock()
_cycle_thread_running = False

PRICE_INTERVAL_SEC = 15
TRADE_INTERVAL_SEC = 60
LEARNING_CHECK_SEC = 300


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
        needs_learning_report_refresh,
        refresh_learning_report_if_due,
    )
    from .state_store import load_state, save_state

    state = load_state()
    changed = clear_stale_narrative_error(state)
    if not needs_learning_report_refresh(state):
        if changed:
            save_state(state)
        return
    refresh_learning_report_if_due(state)
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


def start_bot_worker() -> None:
    global _worker_started

    if os.environ.get("DISABLE_BOT_WORKER") == "1":
        logger.info("Botti-worker pois päältä (DISABLE_BOT_WORKER=1)")
        return

    with _worker_lock:
        if _worker_started:
            return
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

    thread = threading.Thread(target=_bot_loop, name="crypto-bot-worker", daemon=True)
    thread.start()

    def _kick_learning_narrative() -> None:
        time.sleep(5)
        _refresh_learning_report_async()

    threading.Thread(target=_kick_learning_narrative, name="learning-narrative-kick", daemon=True).start()
