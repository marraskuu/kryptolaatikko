#!/usr/bin/env python3
"""
Oppimisroadmap — tarkista milloin seuraava oppimisvaihe kannattaa toteuttaa.

Käyttö: python scripts/learning_roadmap_check.py
Tuotanto-API: https://hiekkalaatikko.pro/api/state/
Ajastus: Cursor-automaatio päivittäin (klo 9).

Synkassa trading/services/learning_report.py ROADMAP_ITEMS -listan kanssa.
"""

from __future__ import annotations

import json
import sys
import urllib.request

API_URL = "https://hiekkalaatikko.pro/api/state/"

# mode=active → jo tuotannossa, ei uutta deploya odoteta
# mode=collect → dataa kerätään, ei vielä koodattavaa
# mode=implement → kynnys ylittynyt → seuraava koodityö
ROADMAP_ITEMS = (
    {
        "key": "setup_learning",
        "label": "Setup-oppiminen (omat sisäänostot + Gemini C)",
        "metric": "regime_tagged_sells",
        "target": 4,
        "mode": "active",
        "action": "Setup-muisti + blocked_setups Geminissa — käytössä",
    },
    {
        "key": "richer_buckets",
        "label": "Richer markkina-ämpärit (book/crowd/flow)",
        "metric": "buckets_learned",
        "target": 18,
        "mode": "active",
        "action": "Regiimi×24h×MTF×RSI×vol×deep×book×crowd×flow — käytössä",
    },
    {
        "key": "gemini_confidence",
        "label": "Gemini-confidence oppiminen",
        "metric": "gemini_confidence_tagged",
        "target": 6,
        "mode": "active",
        "action": "Estää tappiolliset conf-tasot — käytössä kun ≥6 tagattua",
    },
    {
        "key": "profit_take_light",
        "label": "Voitto-otto (kevyt viritys)",
        "metric": "profit_take_trades",
        "target": 6,
        "mode": "collect",
        "action": "Kevyt profit-take -viritys learning.py:ssä",
    },
    {
        "key": "profit_take_full",
        "label": "Voitto-otto (täysi optimointi / hold-time)",
        "metric": "profit_take_trades",
        "target": 15,
        "mode": "collect",
        "action": "ATR/regiimi-pohjainen trailing sell_strategy.py",
    },
    {
        "key": "trade_flow_learning",
        "label": "Trade flow entry -tuning (seuraava koodi)",
        "metric": "closed_trades_with_flow",
        "target": 8,
        "mode": "implement",
        "action": "Toteuta: flow-bucket → entry score/block ai_trader.py (Deploy E)",
    },
    {
        "key": "shadow_portfolio",
        "label": "Varjopolitiikka → live (Deploy D)",
        "metric": "shadow_mirror_trades",
        "target": 3,
        "mode": "implement",
        "action": "Arvioi counterfactual → DAILY_POLICY_LIVE feature flag",
    },
    {
        "key": "bull_satellite_tuning",
        "label": "Bull satellite auto-säätö",
        "metric": "bull_satellite_splits",
        "target": 3,
        "mode": "collect",
        "action": "Automaattinen 65/35 paino/kynnys kun ≥3 split-eventtiä",
    },
)


def _fetch_state() -> dict:
    req = urllib.request.Request(API_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _metrics(state: dict) -> dict[str, float]:
    learning = state.get("learning") or {}
    stats = learning.get("stats") or {}
    ml = state.get("marketLearning") or {}
    setup = learning.get("setup_memory") or {}
    shadow = state.get("dailyPolicyShadow") or {}
    shadow_metrics = shadow.get("portfolioMetrics") or {}
    shadow_mirror = int(shadow_metrics.get("tradesMirrored") or 0) + int(
        shadow_metrics.get("tradesSkipped") or 0
    )

    bull_splits = 0
    closed_with_micro = 0.0
    closed_with_flow = 0.0
    for section in (state.get("learningReport") or {}).get("sections") or []:
        sid = section.get("id")
        for txt in section.get("lines") or []:
            s = str(txt)
            if sid == "microstructure" and "Suljetut kaupat micro-datalla:" in s:
                try:
                    closed_with_micro = float(s.split("Suljetut kaupat micro-datalla:")[1].split("kpl")[0].strip())
                except (IndexError, ValueError):
                    pass
            if sid == "bull_satellite" and "Split-jakoja:" in s:
                try:
                    bull_splits = int(s.split("Split-jakoja:")[1].split()[0])
                except (IndexError, ValueError):
                    pass

    # flow-kaupoille riittää micro-suljetut (flowBucket tallennetaan entry-metaan)
    closed_with_flow = closed_with_micro

    gemini_sells = stats.get("gemini_sell", {})
    profit_take = stats.get("profit_take", {})
    setup_backfill = learning.get("setup_backfill") or {}

    return {
        "app_build": state.get("appBuild"),
        "setup_memory_keys": float(len(setup)),
        "blocked_setups": float(len(learning.get("blocked_setups") or [])),
        "regime_tagged_sells": float(learning.get("regime_tagged_sells") or 0),
        "gemini_confidence_tagged": float(learning.get("gemini_confidence_tagged") or 0),
        "gemini_sell_trades": float(gemini_sells.get("trades") or 0),
        "profit_take_trades": float(profit_take.get("trades") or 0),
        "buckets_learned": float(ml.get("bucketsLearned") or 0),
        "buckets_tracked": float(ml.get("bucketsTracked") or 0),
        "history_buckets_learned": float(ml.get("historyBucketsLearned") or 0),
        "setup_history_ready": float(setup_backfill.get("setupHistorySetupsReady") or 0),
        "closed_trades_with_flow": float(closed_with_flow),
        "closed_trades_with_micro": float(closed_with_micro),
        "shadow_mirror_trades": float(shadow_mirror),
        "exit_setups_ready": float((state.get("exitLearning") or {}).get("setupsReady") or 0),
        "bull_satellite_splits": float(bull_splits),
        "learning_samples": float(learning.get("samples") or 0),
    }


def _progress(current: float, target: float) -> str:
    if current >= target:
        return "käytössä" if current >= target else f"{int(current)}/{int(target)}"
    if current >= target * 0.5:
        return f"{int(current)}/{int(target)}"
    return f"{int(current)}/{int(target)}"


def _status(cfg: dict, current: float, target: float) -> str:
    mode = cfg.get("mode", "collect")
    if mode == "active":
        return "aktiivinen"
    if current >= target:
        return "valmis toteutettavaksi" if mode == "implement" else "aktiivinen"
    if current >= target * 0.5:
        return "tulossa"
    return "kerätään"


def main() -> int:
    state = _fetch_state()
    m = _metrics(state)
    results = []
    for cfg in ROADMAP_ITEMS:
        current = m.get(cfg["metric"], 0)
        target = float(cfg["target"])
        results.append(
            {
                **cfg,
                "current": current,
                "target": target,
                "progress": _progress(current, target),
                "status": _status(cfg, current, target),
            }
        )

    print("=== Oppimisroadmap (live) ===")
    print(f"appBuild: {m.get('app_build')}\n")

    for group, label in (
        ("aktiivinen", "KÄYTÖSSÄ"),
        ("tulossa", "TULOSSA"),
        ("kerätään", "KERÄTÄÄN"),
        ("valmis toteutettavaksi", "VALMIS TOTEUTETTAVAKSI"),
    ):
        items = [r for r in results if r["status"] == group]
        if not items:
            continue
        print(f"--- {label} ---")
        for r in items:
            print(f"  {r['label']}")
            print(f"    {r['metric']}: {int(r['current'])}/{int(r['target'])} ({r['progress']})")
            print(f"    → {r['action']}")
        print()

    print("--- Yhteenveto-metriikat ---")
    summary = {
        k: v
        for k, v in m.items()
        if k
        not in (
            "gemini_sell_trades",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    ready_impl = [r["key"] for r in results if r["status"] == "valmis toteutettavaksi"]
    if ready_impl:
        print("\n>>> Seuraava koodityö:", ", ".join(ready_impl))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
