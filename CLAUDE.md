# CLAUDE.md

Guidance for agents working in this repository (Claude Code / Cursor). Read before changing trading logic.

## Project

Krypto Simulaattori — paper-trading crypto bot (Bitfinex prices, €1000 start, no real money). Technical analysis (RSI/EMA/momentum) + periodic Gemini. Django on Railway. UI Finnish (`/` + `/eng/`); match the file’s existing comment language when editing.

- Live: https://hiekkalaatikko.pro
- Repo: https://github.com/marraskuu/kryptolaatikko
- Path: `C:\Users\chris\crypto-trader-sim`
- Bot runs 24/7 — **no** start/stop/reset in live UI. Do **not** reset the portfolio unless the user explicitly asks.

## Commands

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver   # http://127.0.0.1:8000
```

Tests (same as CI):
```powershell
python manage.py check
python manage.py test trading.tests -v2
python manage.py test trading.tests.test_profit_bleed_fixes -v2
```
CI (`.github/workflows/ci.yml`): `check` + tests with `DEBUG=true` and `SECRET_KEY` set.

No JS build — `trading/static/trading/js/app.js` and `i18n.js` are vanilla.

## Architecture

**Live code only:** `trading/` + `config/`. Root `index.html` / `app.js` / `start.ps1` etc. are **legacy** — do not extend.

### State (`BotState.data` JSON blobs)

- `pk=1` — portfolio/bot — only via `trading/services/state_store.py` (`load_state` / `save_state` / `patch_state_keys`). Concurrent merge is defensive; never mutate a cached dict across threads.
- `pk=2` — market shadow learning (`market_learning.py`)
- `pk=3` — setup historical backfill
- `pk=4` — exit/peak-sell learning

`PageVisit` is the only relational model (`/stats/`).

### Bot loop (in-process daemon)

`bot_worker.py`: 15 s prices, 60 s trades, ~300 s learning report. `DISABLE_BOT_WORKER=1` for one-off commands. Production DB must be MySQL (SQLite wipes on Railway redeploy).

### Pipeline (`trading/services/`)

- `engine.py` — cycle orchestration; **daily policy live gates** (buy block + discretionary sell block)
- `ai_trader.py` — entries/exits, regime, ranking, size caps, bear freeze
- `gemini.py` — periodic second opinion (`GEMINI_INTERVAL_SEC`, model via `GEMINI_MODEL`)
- `sell_strategy.py` / `exit_learning.py` — profit-take (peak → wait → sell)
- `daily_policy_shadow.py` — shadow metrics **and** live rules via env flags (see below)
- `market_microstructure.py` — book/flow gates (`microBlocked`)
- `bitfinex.py` — always `normalize_symbol` before dict keys
- Tax: **30% on net** year gains (not per-trade gross) — keep UI + `export_excel.py` aligned

### Changelog / build

- User-visible deploy → entry in `trading/changelog.py` (FI+EN), newest first. **Not** `/stats/` changes.
- Bump `APP_BUILD` in `config/settings.py` on notable deploys.

### Secrets

Only `.env` / Railway Variables — never commit `GEMINI_API_KEY`, `SECRET_KEY`, DSN, DB URLs.

---

## Lessons learned (do not re-break)

Live evidence ~Jun–Aug 2026: start €1000 → ~€840 (−16%). **Bull sells ~+€300, bear sells ~−€380.** Profit-take works (+€708 lifetime); stops + churn destroy it. Fees are irrelevant (~€6).

### Root causes that already burned money

1. **Bounce-buy in official bear** — `entry_regime` / anticipated bull while `regime=bear` → full buy → −1.2% ATR stop.  
   **Fix (2026-08-13):** `_bear_buy_freeze_active` freezes when **official** `regime=bear` *or* risk-regime bear, even if phase anticipates bull. Exception only `condAdjust ≥ BEAR_FREEZE_EXCEPTION_MIN_ADJUST` (default 3.5).

2. **All-in sizing** — empty book deployed ~100% cash into one coin; one stop ≈ −€8–10.  
   **Fix:** `MAX_SINGLE_BUY_PORTFOLIO_PCT` default **0.30** in `_plan_initial_allocation` and buy loop (`_cap_buy_eur`).

3. **Ignoring Gemini “hold cash” / micro** — buy reason said `micro_blocked` / keep cash, bot still bought.  
   **Fix:** `_gemini_prefers_cash` + require non-hold action; `microBlocked` on analysis blocks even if microstructure module flag is off.

4. **Idle empty-book starvation then over-fix** — bleed-stop removed Gemini/score bypass; Gemini 0 picks → never bought; then ranked fallback helped but must **not** bypass blocked_buys / micro / bear freeze.  
   **Fix (2026-08-08):** idle + Gemini 0 picks → **one** ranked buy through normal gates only.

5. **Trusting shadow CF euros as ROI** — `netCounterfactualEur` ~+€700–800 sums overlapping estimates (path-dependent). Daily policy is a **brake**, not proven alpha. Keep live gates on; do not promise +€800 from “turning shadow on” (much of it is already live).

6. **Churn death** — “estetty vapautus”, “ei valinnoissa”, concentration, time-stop under ~3 h: many small losses. Prefer cash in bear over “fixing” idle with weak entries.

### What to do when portfolio is losing

Priority order (proven):

1. Stop bear/bounce buys and all-in size (done — keep).
2. Respect cash/micro Gemini intent (done — keep).
3. Accept idle cash in bear — idle is not a bug.
4. Keep `DAILY_POLICY_LIVE_*` on as risk control; do **not** loosen stops or buy_scale to “get back in”.
5. Do **not**: reset portfolio, chase new Gemini models, force setup-model live under AUC gate, or re-enable broad idle Gemini/score bypass.

### Live feature flags (defaults — see `.env.example`)

| Flag | Default | Role |
|------|---------|------|
| `BEAR_BUY_FREEZE` | on | No new buys in bear (official or risk) |
| `BEAR_FREEZE_EXCEPTION_MIN_ADJUST` | 3.5 | Only very strong shadow edge bypasses freeze |
| `MAX_SINGLE_BUY_PORTFOLIO_PCT` | 0.30 | Max fraction of book per buy/position add |
| `DAILY_POLICY_LIVE_ENABLED` | on | Block discretionary sells on daily stop / firm lock |
| `DAILY_POLICY_LIVE_BUY_BLOCK` | on | Block new buys on daily stop / firm lock |
| `DAILY_POLICY_LIVE_ROLLING_DD` | on | Also block buys on 3d −2% rolling DD |
| `GEMINI_SELL_ENABLED` | off | No Gemini-driven partial sells |
| `SETUP_MODEL_LIVE_ENABLED` | off | Opt-in; AUC gate ~0.58 |

Learning often tightens further live (`buy_scale` 0.5, `entry_score_min` 4, rotation off) — do not fight that in bear without evidence.

### Diagnosis tips

- Full state: `GET https://hiekkalaatikko.pro/api/state/` (large). Health: `/api/health/`.
- Sell P/L: use trade `profitLoss` / `costBasis` (list is newest-first). FIFO-from-scratch without those fields misleads.
- Regime at **sell** time matters more than narrative; compare bull vs bear sell aggregates before changing entries.

### Tests that guard these lessons

- `trading/tests/test_profit_bleed_fixes.py` — bear freeze vs anticipated bull, size cap, cash/micro gates
- `trading/tests/test_idle_empty_deploy.py` — idle ranked path does not bypass blocks
- `trading/tests/test_profitability_path.py` / `test_daily_policy_live_gate.py` — daily policy gates

### Recent related builds (see `trading/changelog.py`)

- `20260913a` — Profit Pack v1: chase ≤6% 24h, breadth ≥35%, majors only, no setup −1.5% exit, PT 3.5%/20%
- `20260813a` — bear freeze + 30% size cap + cash/micro gate
- `20260808a` — idle ranked buy when Gemini picks none
- `20260805b` — path-to-profit: daily buy-block, Gemini sells off, conf floor

### Profit Pack v1 flags (2026-09-13)

| Flag | Default | Role |
|------|---------|------|
| `MAX_ENTRY_CHANGE_24H_PCT` | 6.0 | Block chase entries already extended |
| `MIN_BREADTH_UP_PCT_FOR_BUY` | 35.0 | No buys in weak breadth (even if “bull”) |
| `BUY_MAJORS_ONLY` | on | BTC/ETH/SOL/XRP/LTC/LINK only |
| `SETUP_FAST_EXIT_ENABLED` | off | No −1.5% “bad setup” full sells |
| `PARTIAL_TAKE_TRIGGER_PCT` | 3.5 | Later first profit tier |
| `PARTIAL_TAKE_FRACTION` | 0.20 | Smaller first harvest — let winners run |

When breadth is low (e.g. 16% bear), expect **cash** — that is intended, not a bug.

---

## Agent workflow notes

- Continue project chats via **this folder** (File → Open Folder); Home window loses history.
- User-visible deploy: changelog + `APP_BUILD` + push when user asks to ship (“tee” / “push”).
- Do not commit secrets. **This file is the agent memory** for trading mistakes — update the Lessons section when shipping behavioral fixes.
