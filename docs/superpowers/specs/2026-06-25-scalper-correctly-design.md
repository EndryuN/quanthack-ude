# Scalper, Correctly — execution, arming, consolidation, progress

**Date:** 2026-06-25
**Status:** Approved design — pending spec review
**Supersedes/extends:** `2026-06-25-scalper-integration-design.md` (§9) where they overlap.
**Goal:** Run the AI engine normally while the operator assigns **up to 2 symbols** to a scalper alongside it — with safe order/stop execution, gated arming (no runaway), a single consolidated dashboard (no separate Scalper page), and an **inline per-symbol progress display** showing what the scalper is doing.

Derived from a three-agent audit (order/SL execution, scalper safety/arming/lifecycle, dashboard/UX) plus live findings: broker rejects inline-SL DEALs (10006), positions ran naked, and the scalper churned on XRPUSD when armed ungated.

---

## 1. Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Arming | **Gated + explicit override** — Arm disabled until the symbol clears the walk-forward gate; a force-arm (unvalidated) confirm overrides. |
| D2 | Progress UI | **Inline under Positions** — each scalped (`·s`) row expands to show state · last action/reason · gate verdict. |
| D3 | Max scalped symbols | **2** (already enforced in `controls.scalper_assignments`). |
| D4 | Sizing | **Per-symbol USD risk budget** → lots via `lots_for_notional`; `lots<=0` → not-sized → dry-run (surfaced, never silent). |
| D5 | Page | **Delete** the separate Scalper page; fold the backtest/gate runner into a dashboard collapsible. |

---

## 2. Safe order & stop execution

**Already done (hotfix):** engine `_do_order` and `scalper._apply` open **clean** (no inline SL/TP — the broker rejects those DEALs with 10006); the stop is attached via the SLTP action (engine: `_ensure_stops` same tick; scalper: `set_sltp` post-fill).

**Remaining gaps to fix:**
- **C2 — min-stop-distance clamp (critical).** `client.sl_price` and `scalper._sl_tp_for` round to digits but ignore `symbol_info.trade_stops_level`. The scalper's 20bps TP/SL are inside the broker's minimum distance → `set_sltp` rejected (10016) → position naked. **Fix:** clamp every SL/TP to at least `trade_stops_level * point` (with margin) from the *current* price, rounded to digits; centralize in `client.sl_price` (return a broker-valid level) and reuse in the scalper.
- **C1 — scalper can run naked (critical).** `scalper._apply` ignores the `set_sltp` result. **Fix:** check the retcode; retry once on a fresh tick; if a stop still cannot be attached, **close the just-opened position** (a naked few-second scalp is worse than no trade). Add a **scalper-side stop-sweep** over its own magic each `step()` (the engine's `_ensure_stops` is magic-filtered and can't see scalper positions).
- **M1 — anchor SL/TP to the actual fill price** (the position's `price_open` / a fresh tick), not the stale last bar close.
- **H2 — in-tick retry on transient** in `_ensure_stops` (today it only logs and waits a tick); escalate (close) if a stop genuinely can't be placed.
- **H3 — partial fills.** `market_order` ignores `res.volume`; `10010` (DONE_PARTIAL) is mislabeled FAILED. **Fix:** read `res.volume`; record the true fill; treat `10010` as a real partial; top up on the next reconcile.
- **H1 — `use_broker_sl` is now load-bearing** (clean-open removed the inline fallback). Guard it on whenever live, or refuse to open with it off.
- **R4 — remove the now-unused `sl`/`tp` params from `market_order`** (foot-gun; the broker rejects them) once all paths are clean.

---

## 3. Safe arming — gated + override (D1)

The walk-forward gate (`crypto_backtest.walk_forward` / `gate_for_symbol`) exists but **is wired nowhere** — that is why an unvalidated symbol armed and churned. Defense-in-depth:

1. **Eligibility (automated).** New `GET /api/scalper/gate` runs `gate_for_symbol(sym, engine_sharpe=<live 15m Sharpe>)` for each assigned symbol, **cached** (it pulls M1 history — recompute on assignment change / slow timer, not per poll). Returns `{available, cleared, reason, test, params, cost_bps, bars}` per symbol.
2. **Arm (operator).** Panel arm toggle is **disabled unless every assigned symbol is gate-cleared**; a **force-arm (unvalidated) confirm** overrides. `POST /api/controls` **rejects `scalper_armed=True`** when an assigned symbol isn't cleared, unless a `force` flag is set.
3. **Live (engine = final boundary).** `_run_scalper` does **not** trust the toggle alone: it runs live only on the intersection **{armed} ∩ {gate-cleared or forced} ∩ {sized}**; any other assigned symbol stays **dry-run** and is reported as such. Holds even if `controls.json` is hand-edited.
4. **Lock the gate's params on arm.** Copy the gate's winning `{entry, lookback, tp, stop}` into the per-symbol live config so live behavior == what was certified (today the slot uses defaults regardless of the gate).

---

## 4. Anti-churn guards (the runaway)

The new `cooldown_secs=30` helps but is insufficient. Root cause: decisions run **per ~5s tick on the still-forming bar**, so `crypto_decision` flips around the threshold intrabar; broker TP/SL fills trigger immediate re-entry. Add, in priority order:
1. **Decide on closed bars only** (or require the breakout to persist ≥N consecutive ticks — flip debounce). Root-cause fix; aligns live with the backtest/gate.
2. **Max trades/hour per symbol** (rolling-timestamp cap, e.g. 6–12/hr) — the only guard that directly bounds turnover.
3. **Minimum hold time** before any flip/close-initiated re-entry.
4. **Cooldown on the close path too** (today only open/flip), and a **longer re-entry cooldown after a stop fill** than after a TP fill.

---

## 5. Per-symbol sizing (D4)

Assignment and lots are decoupled today (`cfg.lots` is static; assigning XRPUSD → `lots=0` → silent no-op). Fix: a **per-symbol USD risk budget** (`scalper_notional_per_symbol`, or a fraction of equity) → `client.lots_for_notional(sym, budget)` each tick. This auto-sizes BTC/ETH/XRP consistently, keeps the footprint bounded and visible to the account-wide risk engine, and matches the gate's per-symbol cost basis. The engine live-eligibility check treats `lots<=0` as **not-sized → dry-run + surfaced**, never a silent return.

---

## 6. Consolidation + inline progress

### 6.1 Remove the Scalper page (D5)
- `index.html`: delete the `data-view="scalper"` nav button and the entire `#view-scalper` section; re-home the backtest runner controls (`#scalp-sym/#scalp-go/#scalp-clear/#scalp-status/#scalp-results`) into a **collapsible `<details>` "Arming gate (backtest)"** on the dashboard (closed by default).
- `app.js`: drop `"scalper"` from `showView`; remove the view-switch `initScalper()` call (call it lazily on `<details>` open); the backtest JS (`initScalper/runScalp/renderScalp/_scalpRuns`) is reused verbatim.
- Delete stale content: the hardcoded DORMANT badge, the standalone-process description, the hardcoded gate/config boxes, the CLI note.
- `/api/scalp/symbols` and `/api/scalp/backtest` stay (feed the gate runner).

### 6.2 Scalper state emission
The scalper emits only `print()` today. Add: `Scalper.step()` writes an atomic `state/scalper.json` each tick via `controls.atomic_write_json`, and a new **`GET /api/scalper`** serves it (read-only). Schema (per the audit):
```
{ ts, armed, dry_run, halted, realized_today, daily_loss_limit, max_concurrent,
  open_count, cooldown_secs,
  symbols: [ { symbol, state, gate{cleared,reason,test,params,cost_bps},
              lots, position{dir,lots,profit}|null,
              last_signal{target,reason,ret_bps}, last_action{kind,at,price},
              cooldown_remaining_s, trades_last_hour, blocked_reason } ] }
```
`state ∈ {eligible, armed, flat, in_position, cooldown, halted, blocked, no_lots, gate_failed, dry_run}`. Most fields come straight from data `_step_symbol`/`_apply` already compute — captured into `self._state[sym]` instead of (only) printed; `gate` is the cached eligibility; written atomically so the panel never reads a torn file.

### 6.3 Inline progress under Positions (D2)
Each scalped (`·s`) row in the Positions panel expands to a sub-line: **`state · last action/reason · gate verdict`**, sourced from `/api/scalper` on the existing **1s positions cadence**. Position + P&L already come from `/api/positions` (free). The `·s` hook, bottom-pinning, and hover-preview are unchanged.

---

## 7. Testing strategy

Pure/fakeable units (no MT5): min-stop-distance clamp; close-if-cant-stop + scalper stop-sweep; partial-fill handling; gate-eligibility + live-eligibility intersection; arm-rejection in `POST /api/controls`; gate-param lock-on-arm; turnover/min-hold/closed-bar guards; lots-from-budget (incl. `lots<=0`→dry-run); `state/scalper.json` emit + `/api/scalper`. Plus the panel teardown (no broken `showView`/nav). Reuse the existing FakeClient/FakeMT5 patterns.

---

## 8. Sequencing (each a tested increment)

1. **Execution safety** (§2): min-distance clamp, scalper retcode-check/close-if-naked + scalper sweep, fill-price anchor, partial fills. *Highest risk — live money.*
2. **Sizing** (§5): per-symbol budget → lots; `lots<=0`→dry-run.
3. **Gated arming** (§3): gate endpoint, arm rejection, engine live-eligibility intersection, param lock-on-arm.
4. **Anti-churn** (§4): closed-bar/debounce, max-trades/hr, min-hold, cooldown-on-close.
5. **State emission** (§6.2) + `/api/scalper`.
6. **Consolidation + inline progress** (§6.1, §6.3).

Ships dry-run/disabled; a symbol only trades live after it clears the gate (or is force-armed) and is sized.

---

## 9. Open items / residual risks

- **Crypto backtest data is thin** (~34 days live M1) — the gate may rarely "clear," so force-arm will be the common path early; surface the gate stats prominently so an override is an informed choice.
- **Min-stop-distance vs the 20bps strategy:** if the broker's min distance exceeds the strategy's intended TP/SL, the clamp widens them — which changes the strategy's economics. Surface the effective (clamped) TP/SL so the operator sees the real exit levels, and re-run the gate with the clamped values.
- **Account-wide liquidation:** the engine kill-switch already flattens all magics; confirm the scalper's footprint stays within the account-wide risk budget once sized.
