#!/usr/bin/env python3
"""
Oppimisroadmap — tarkista milloin seuraava oppimisvaihe kannattaa toteuttaa.

Käyttö: python scripts/learning_roadmap_check.py
Tuotanto-API: https://hiekkalaatikko.pro/api/state/
Ajastus: Cursor-automaatio päivittäin (klo 9).
"""

from __future__ import annotations

import json
import sys
import urllib.request

API_URL = "https://hiekkalaatikko.pro/api/state/"

# Kynnykset (synkassa agentin suosituksen kanssa)
THRESHOLDS = {
    "setup_active": {
        "label": "Setup-oppiminen (jo koodissa — odota aktivoitumista)",
        "ready_if_any": [
            ("setup_memory_keys", 1, "ge"),
            ("regime_tagged_sells", 4, "ge"),
        ],
        "action": "Odota — ei uutta koodia. Chip: 📐 N setuppia / regiimi 4/4.",
    },
    "gemini_confidence": {
        "label": "Gemini-confidence oppiminen (koodissa — odota tagattuja myyntejä)",
        "ready_if_all": [
            ("gemini_sell_trades", 6, "ge"),
            ("gemini_tagged_confidence", 6, "ge"),
        ],
        "action": "Aktiivinen kun ≥6 tagattua: estää tappiolliset conf-tasot learning.py + ai_trader.",
    },
    "richer_buckets": {
        "label": "Richer market-learning ämpärit (toteutettava)",
        "ready_if_all": [
            ("buckets_learned", 18, "ge"),
            ("buckets_tracked", 20, "ge"),
        ],
        "action": "Toteuta: setup-avain +mtf (+ myöhemmin atr) market_learning.py.",
    },
    "hold_time": {
        "label": "Pitoajan optimointi (viimeiseksi)",
        "ready_if_all": [
            ("profit_take_trades", 15, "ge"),
        ],
        "ready_if_any": [],
        "block_if": [("profit_take_trades", 10, "lt")],
        "action": "Toteuta: ATR/regiimi-pohjaiset trailing + partial-take sell_strategy.py.",
    },
}


def _fetch_state() -> dict:
    req = urllib.request.Request(API_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _cmp(actual: float, target: float, op: str) -> bool:
    if op == "ge":
        return actual >= target
    if op == "lt":
        return actual < target
    raise ValueError(op)


def _metrics(state: dict) -> dict[str, float]:
    learning = state.get("learning") or {}
    stats = learning.get("stats") or {}
    ml = state.get("marketLearning") or {}
    setup = learning.get("setup_memory") or {}

    gemini_sells = stats.get("gemini_sell", {})
    profit_take = stats.get("profit_take", {})

    trades = state.get("portfolio", {}).get("trades") or []
    gemini_conf_count = sum(
        1
        for t in trades
        if t.get("type") == "sell"
        and "gemini" in (t.get("reason") or "").lower()
        and t.get("geminiConfidence") is not None
    )

    return {
        "setup_memory_keys": len(setup),
        "regime_tagged_sells": float(learning.get("regime_tagged_sells") or 0),
        "gemini_sell_trades": float(gemini_sells.get("trades") or 0),
        "gemini_tagged_confidence": float(gemini_conf_count),
        "buckets_learned": float(ml.get("bucketsLearned") or 0),
        "buckets_tracked": float(ml.get("bucketsTracked") or 0),
        "profit_take_trades": float(profit_take.get("trades") or 0),
        "learning_samples": float(learning.get("samples") or 0),
    }


def _check_item(key: str, cfg: dict, m: dict[str, float]) -> dict:
    blocked = False
    for field, target, op in cfg.get("block_if") or []:
        if _cmp(m.get(field, 0), target, op):
            blocked = True

    ready = False
    if not blocked:
        all_ok = all(_cmp(m.get(f, 0), t, op) for f, t, op in cfg.get("ready_if_all") or [])
        any_ok = any(_cmp(m.get(f, 0), t, op) for f, t, op in cfg.get("ready_if_any") or [])
        if cfg.get("ready_if_all"):
            ready = all_ok
        elif cfg.get("ready_if_any"):
            ready = any_ok

    return {"key": key, "label": cfg["label"], "ready": ready, "blocked": blocked, "action": cfg["action"]}


def main() -> int:
    state = _fetch_state()
    m = _metrics(state)
    results = [_check_item(k, cfg, m) for k, cfg in THRESHOLDS.items()]

    print("=== Oppimisroadmap ===\n")
    for r in results:
        status = "VALMIS TOTEUTETTAVAKSI" if r["ready"] else ("ODOTA (liian vähän profit_take)" if r["blocked"] else "kerätään dataa")
        print(f"{r['label']}")
        print(f"  Tila: {status}")
        print(f"  Seuraava: {r['action']}\n")

    print("Metriikat:", json.dumps(m, ensure_ascii=False))
    ready = [r["key"] for r in results if r["ready"]]
    if ready:
        print("\n>>> Muistutus: toteuta nyt:", ", ".join(ready))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
