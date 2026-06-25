# Scalper Integration & Correctness Redesign

**Date:** 2026-06-25
**Status:** Approved design — pending spec review
**Scope:** Redesign the standalone mean-reversion scalper so it (1) reads the right data, (2) decides on a correct cadence, (3) decides take-profit correctly, (4) decides long vs short correctly, and (5) provably cannot interfere with the AI engine's live trades (and vice versa) on the shared netting account.

---

## 1. Background & motivation

The system runs a primary **AI engine** (magic `990623`) that trades many instruments live on a single **netting** MT5 account, plus a standalone **mean-reversion scalper** (magic `990624`) that fades short-term deviations from a short EMA on its own symbols. The scalper currently ships **disabled** (`ScalperConfig.enabled = False`) and is dormant.

An audit (four parallel investigations, evidence cited below) found that while the scalper's *signal core* is correct, its **data basis, decision cadence, and isolation from the engine are broken**. This spec defines the redesign to fix all of it.

### 1.1 Audit findings (current state, with evidence)

**Signal logic — CORRECT (keep as-is).**
- Fade direction is right: `dev_bps = (price - ema)/ema*1e4` (`bot/scalp_signal.py:42`); `dev_bps > entry_bps` → SHORT (`scalp_signal.py:45-46`), `dev_bps < -entry_bps` → LONG (`scalp_signal.py:47-48`).
- Take-profit/stop sign convention holds for both directions: `move_bps = (price-entry)/entry*1e4*position` is positive in profit for long and short (`scalp_signal.py:53`); `tp_bps` closes a winner, `stop_bps` a loser (`scalp_signal.py:54-57`); revert-to-mean and time-stop exits are correct for both sides (`scalp_signal.py:58-63`).

**Data basis — BROKEN.**
- Live reads BID closes including the **still-forming current M1 bar** (`bot/scalper.py:71,74` → `bot/client.py:124-126`, `copy_rates_from_pos(...,0,count)`), while the backtest uses **completed MID closes** (`bot/scalp_backtest.py:36-37`). Live acts on an unclosed bar — intrabar noise the backtest never sees.
- `entry_price` is stored as a **bar close, not the actual fill** (`scalper.py:104,106`), and on restart is reset to the current close (`scalper.py:79-80`), so TP/stop are measured from a wrong/arbitrary baseline.

**Cadence — BROKEN.**
- `bars_held` increments **once per 2s poll, not per bar** (`scalper.py:81`, `poll_secs=2.0` `bot/config.py:86`). With `time_stop_bars=10` (`config.py:85`), the time-stop fires in **~20s live vs the 10 minutes the backtest validated — ~30× too fast**.
- No debounce: the scalper re-decides every poll on the forming bar and resets `_entry` on close, so it can open→exit→re-enter/flip **multiple times inside one M1 bar** → over-trading and spread bleed. The AI engine, by contrast, only acts when its signal set changes (`bot/engine.py:178-181`).

**Risk / isolation — CONDITIONAL, BROKEN on symbol overlap.**
- Magic isolation works for *reads* (`bot/client.py:136-141`, `_filter_by_magic` `client.py:51-56`), but on a netting account that is **blindness, not safety**.
- Symbol exclusivity is computed **at import time only** (`config.py:99,111,140`) with no lock/IPC/runtime check. A stale engine process + a later-enabled scalper → both target EURUSD with different magics → `position_for` returns `None` for each side → **infinite thrash** as each blindly fights the shared net position.
- The engine kill-switch flattens **only its own magic** (`bot/risk.py:131` → `client.py:327`), leaving scalper positions open in a margin crisis.
- The scalper has **no account/margin check**, its **daily-loss kill is dead code** (`_realized_pnl` set to `0.0` at `scalper.py:25` and never updated; only the unit test sets it by hand at `test_scalper.py:49`), and it attaches **no broker stop-loss** (`scalper.py:105`).
- `risk.assess()` mixes bases: gross leverage/concentration use engine-only positions (`risk.py:56`) while margin uses account-wide `account_info()` (`risk.py:55,68-70,83-95`) — so scalper exposure is invisible to the leverage/concentration tripwires but counts toward margin.
- No runtime "another magic already holds my symbol" guard exists anywhere (`positions(magic=None)` is implemented at `client.py:139` but never called in `bot/`).

### 1.2 Goals

1. The live scalper trades on the **same data and timing the backtest certifies** (so the arming gate is meaningful).
2. Take-profit and long/short decisions are measured from a **correct entry basis**.
3. The scalper **provably cannot** interfere with the AI engine, and vice versa, on the netting account.
4. The scalper's own risk controls (daily-loss limit, stop-loss) **actually function**.
5. Default behavior is **unchanged while dormant** — ships disabled; arms only after the backtest gate passes.

### 1.3 Non-goals

- Changing the fade signal logic (it is correct).
- Re-running or re-tuning the arming gate (separate activity; this spec only makes the gate trustworthy).
- Moving to a hedging account or changing broker setup.
- Sub-bar / tick-level scalping (explicitly decided against in favor of per-closed-bar decisions).

---

## 2. Key design decisions (approved)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Process model | **Integrate the scalper into the engine process** | One process → sequential decisions → the cross-process netting race is structurally impossible. |
| D2 | Decision cadence | **Once per closed M1 bar** | Matches the backtest 1:1; eliminates the forming-bar problem, the `bars_held` bug, and churn. |
| D3 | Exit/SL ownership | **Scalper owns its symbols fully** | Scalper computes target + all exits via its bar-close logic, attaches its own tight stop, and is excluded from AI sizing/trailing/SL. Clean separation, no duelling exit logics. |
| D4 | Magic numbers | **Keep both magics, for attribution only** | Preserves per-strategy P&L in the panel; safety no longer depends on magic. |
| D5 | Standalone process | **Retire `python -m bot.scalper --live`** | Removes the only way to launch a second, racing process. |

---

## 3. Architecture

### 3.1 Process & connection model
One OS process, one MT5 session. The `Engine` owns a `Scalper` instance (constructed only when `SCALPER.enabled`). Each engine loop iteration drives both:

```
Engine.tick():                      # ~5s loop, unchanged cadence
  acct = client.account()
  snap = risk.assess()              # ACCOUNT-WIDE (all positions)
  if snap.kill:
      risk.enforce(snap)            # flatten EVERYTHING (both magics)
      return
  ... operator controls (pause / flatten) ...
  ai_targets = build/reconcile AI universe           # unchanged
  if scalper:
      scalper.step(allow_new_risk = snap.allow_new_risk and not paused)
  ... trailing stops / TP for AI symbols only ...     # excludes scalper symbols
```

The scalper retains its own `MT5Client(magic=990624)` for **order/close attribution**, sharing the same global MT5 session as the engine's client. Because everything is sequential within `tick()`, there is no concurrency between the two order streams.

### 3.2 Symbol partition (single source of truth)
- `engine_symbols(scalper_enabled, scalper_symbols)` continues to cede scalper symbols from the AI universe when enabled (`bot/config.py`).
- **New invariant, asserted at Engine startup:** scalper symbols ⊆ `ALL_SYMBOLS` **and** disjoint from the engine's symbols. If violated, the Engine refuses to start (fail-fast). Since one process reads one `CONFIG`, the partition cannot drift.

### 3.3 Data flow (per scalper symbol, per loop)
```
new closed M1 bar?  --no-->  do nothing this loop
        |yes
   read completed M1 closes (drop the forming bar)
   pos = scalper.client.position_for(sym)         # own magic
   cur = signed(pos);  entry_price = pos.price_open if pos else tracked
   if cur != 0: bars_held += 1                    # per REAL bar
   target, reason = scalp_decision(closes, cur, entry_price, bars_held, params)
   if target != cur and (target==0 or allow_new_risk):  # gate new risk
       execute via scalper.client (open w/ broker SL @ stop_bps, or close)
       on close: realized_pnl += pos.profit       # wire the loss limit
```

"New closed bar" = the latest bar timestamp from MT5 differs from the last-seen timestamp for that symbol, and the strategy then evaluates the **completed** bars (excluding the still-forming one).

---

## 4. Components

### 4.1 `bot/scalper.py` — `Scalper` (refactored to a step-driven module)
- **Remove** `run()` and `main()` (the standalone loop and process entry). The scalper is now ticked by the Engine.
- **`step(allow_new_risk: bool)`** replaces `tick_once()`'s self-looping role: one pass over `cfg.symbols`, acting only on newly closed bars.
- **New-bar gating:** track `self._last_bar_ts[sym]`; only evaluate/act when the latest completed-bar timestamp advances.
- **`bars_held`:** incremented once per newly closed bar (not per poll).
- **`entry_price`:** sourced from the live position's `price_open` (actual fill); the in-memory `_entry` is a cache, but the broker position is the source of truth on restart.
- **Realized P&L:** `_apply()` adds the closed position's `profit` to `self._realized_pnl` on every close (live path), making `daily_loss_limit` functional.
- **Own broker stop:** entries are sent with a broker-side SL derived from `stop_bps` (catastrophe backstop if the process dies).
- **Account/risk gating:** `step()` opens new positions only when `allow_new_risk` is true; it may always close/reduce. (Account-wide margin protection now comes from the engine kill-switch — see 4.3 — so the scalper no longer needs its own margin poll, though its daily-loss halt remains a softer, scalper-only stop.)
- **Foreign-symbol guard:** before opening, if `client.positions(magic=None)` shows a position on the symbol under a different magic, stand down (defensive; should never fire given the partition).

### 4.2 `bot/scalp_signal.py` — unchanged
The fade entry, take-profit, stop, revert, and time-stop logic is correct and stays as-is. Its correctness now holds in live because it is fed a correct `entry_price` and a per-bar `bars_held`.

### 4.3 `bot/risk.py` — `RiskEngine` (account-wide)
- `assess()` reads `client.positions(magic=None)` so gross/net/leverage/concentration are computed account-wide and consistent with the account-wide margin figures. Fixes the mixed-basis inconsistency.
- `enforce()` on kill flattens **all account positions** (both magics).

### 4.4 `bot/client.py` — `MT5Client`
- `Position` gains a `magic` field (already added) so account-wide views can attribute positions.
- `flatten_all()` supports flattening **all account positions** (magic-agnostic) for the kill/operator-flatten path, while normal magic-filtered reads are unchanged.
- Closing a position not owned by the client's magic (the kill path) closes by symbol/net regardless of magic.

### 4.5 `bot/engine.py` — `Engine` (integration host)
- Constructs and owns a `Scalper` when `SCALPER.enabled`; connects its client.
- Calls `scalper.step(...)` at the end of `tick()`, after risk and AI reconcile, passing the current `allow_new_risk`/pause state.
- Excludes scalper symbols from AI sizing, trailing-stop, and AI broker-SL loops (they already iterate `client.positions()` filtered to the engine magic, so scalper positions are naturally excluded; add an explicit symbol-set guard for clarity).
- Startup partition assertion (3.2).

### 4.6 `bot/config.py`
- `ScalperConfig` unchanged in spirit; `daily_loss_limit` now functional. (Any margin-related knob is optional since the engine kill-switch is the account-wide floor.)
- `EngineConfig.symbols` continues to come from `engine_symbols(...)`.

### 4.7 Panel (`panel/`)
- P&L attribution by magic continues to work (engine vs scalper). No backend changes required for correctness; optionally surface scalper armed/halted status. The scalper backtest endpoints (`/api/scalp/*`) remain read-only and unchanged.

---

## 5. Behavior guarantees (how each goal is met)

- **Right data:** decisions use completed M1 bars only; `entry_price` is the real fill. Live and backtest share the same price/timing semantics.
- **Correct cadence:** one decision per closed M1 bar; `time_stop_bars` is real minutes; no intrabar churn.
- **Correct take-profit & long/short:** unchanged correct signal logic, now fed a correct entry basis.
- **No interference (scalper → engine):** disjoint symbols (asserted) + one sequential process; scalper exposure is visible to the account-wide risk engine; scalper is gated by `allow_new_risk`/pause; account kill flattens it.
- **No interference (engine → scalper):** the engine never targets scalper symbols (partition); scalper positions are excluded from AI sizing/trailing/SL.
- **Functional scalper risk:** realized P&L is accumulated so `daily_loss_limit` fires; entries carry a broker SL; the account kill-switch is the hard floor.

---

## 6. Testing strategy

All unit tests run without live MT5 (FakeClient pattern). New/updated cases:

1. **New-bar gating:** repeated `step()` calls on the same bar timestamp do not advance `bars_held` and do not re-trade; a new timestamp advances it once.
2. **Per-bar time-stop:** a held position is closed after exactly `time_stop_bars` newly closed bars.
3. **Realized P&L accumulation:** closing real (faked) positions accumulates `_realized_pnl`; breaching `daily_loss_limit` flattens the scalper's symbols and halts scalping — driven by real closes, not a hand-set value.
4. **Account kill flattens scalper:** `risk.enforce()` on kill closes scalper-magic positions too.
5. **Account-wide assess:** `assess()` includes scalper positions in gross/leverage/concentration.
6. **Partition assertion:** Engine startup rejects overlapping engine/scalper symbol sets.
7. **New-risk gating:** scalper does not open when `allow_new_risk` is false; it still closes/reduces.
8. **Foreign-symbol guard:** scalper stands down if a foreign-magic position exists on its symbol.

Existing tests to update: `test_scalper.py` (step-driven API, real P&L accumulation), `test_scalper_config.py` (partition invariant), `test_client_magic_filter.py` (unaffected), engine tests (scalper hosted, AI symbols exclude scalper symbols).

---

## 7. Rollout & risk

- Ships with `ScalperConfig.enabled = False` → **dormant; behavior identical to today** when off (the Engine simply never constructs a Scalper).
- Because cadence and data now match the backtest, the existing arming gate (`sweep_over_closes`: net-positive-after-cost AND Sharpe above the AI engine's, ≥30 trades) genuinely certifies live behavior. Current EURUSD/ETHUSD/BTCUSD runs show no durable edge → stays dormant.
- Retiring the standalone process is a breaking change to any external launch script that ran `python -m bot.scalper`; update launch docs/scripts accordingly.

---

## 8. Open items / residual risks

- **Bid vs mid:** live M1 bars are bid-based; the backtest uses mid. The deviation signal is largely invariant to a constant half-spread shift, and the cost model already charges a full round-trip spread, so this is a minor residual; the implementation should pick one definition and apply it consistently in both paths and document the choice.
- **One MT5 session, two client wrappers:** confirm `mt5.initialize()` called by both `MT5Client.connect()` instances in one process is idempotent (expected) and that symbol warmup isn't duplicated wastefully.

---

## 9. Revision 2 — strategy-agnostic slot, dynamic selection, fast exits

A three-agent review (viability / alternatives / architecture) reframed this work. The **integration mechanism stays; the strategy changes.** This section supersedes the parts of §1–§8 it touches.

### 9.1 Strategy pivot (why)
- **The mean-reversion fade has no edge** and is scrapped as a live strategy: on **zero-spread EURUSD** it still loses **11–17 bps** over ~540 trades — a *signal* problem, not a cost problem. The lone "profitable" cell (+14 bps, 34 trades) is in-sample argmax overfit at the gate floor. It is also same-universe with the AI engine, so it *cannibalizes* rather than diversifies.
- The build splits into two phases:
  - **Phase 1 (this spec): a strategy-agnostic, cadence-agnostic "scalper slot"** — the integration mechanism + UI, shipped **dry-run/disabled**, running no edged strategy.
  - **Phase 2 (separate plan): crypto fast-momentum** — 5–10s cadence, volatility-breakout + BTC→alt lead-lag, where minute-scale moves dwarf spread. Backtest harness (max MT5 crypto history) → walk-forward → arming gate; arm only if it beats the engine's Sharpe net of cost (observed target ≈ **0.88** for ~5–10s, 1-BTC BTCUSD trades).

### 9.2 Cadence-agnostic slot
The engine calls `scalper.step()` **every engine tick (~5s)**; the **strategy decides whether to act** — per-closed-bar (reversion-like) or **per-tick (~5s, fast crypto)**. The per-M1-bar rule (D2) becomes a *strategy* choice, not a slot constraint. (A dedicated ~1s scalper poll can be added in Phase 2 if entry timing needs to be finer than 5s.)

### 9.3 Fast exits are broker-side (the key change for short trades)
A 5–10s trade cannot wait for the loop to detect its exit. The scalper attaches **both a take-profit AND a stop-loss order at entry** (`tp_bps` / `stop_bps` from the fill, via `set_sltp` / the order request). The broker fills them **server-side, instantly, at the exact level**, independent of loop cadence — this is how P/L is captured on a few-second trade. The loop-based time-stop/revert remain only as backstops.

### 9.4 Dynamic symbol selection + live handoff (replaces static config partition)
Symbol assignment is **runtime**, via a panel control written to `state/controls.json` (a `scalper_symbols` assignment list, same mechanism as the other controls):
- The engine computes its **effective universe each tick** = `ALL_SYMBOLS − assigned` (no longer import-time only), so a newly assigned symbol is **ceded live**.
- **Handoff:** when a symbol is assigned to the scalper, the engine **flattens its own position** on that symbol (it's now out of the engine's universe → de-risked to flat), then the scalper takes it. Reversible: unassigning returns it to the engine.
- The startup partition assertion (§3.2) still holds for any config-seeded assignments.

### 9.5 Explicit, visible arming (fixes "I waited for arm and it never went off")
Arming must never be an opaque passive wait:
- The panel shows the slot's **status** (dormant / dry-run / armed) and, per symbol, whether the strategy has **cleared the arming gate** and its current backtest stats — so the user can see *why* it hasn't armed.
- Arming is an **explicit panel action** (dry-run ↔ live), **guarded** by the gate (can't arm a strategy that hasn't cleared net-positive-after-cost + Sharpe-above-engine). The automated gate decides *eligibility*; the operator decides *go-live*. No more silent waiting.

### 9.6 Positions-panel hook
Scalped symbols are **pinned at the bottom** of the Positions panel, below the engine's, with a **marker appended to the symbol name** (e.g. `EURUSD ·s`) so they're visually distinct.

### 9.7 Required safety fixes (from the architecture review — before arming)
- **B1 (critical):** account-wide kill must flatten the scalper. `flatten_all` gains an all-account mode (`positions(magic=None)`) and `close_symbol` closes by symbol/ticket **regardless of the client's magic** (today the kill triggers on scalper-inflated exposure but cannot clean it up).
- **B2:** source the scalper's realized P&L from **broker deal history** (`client.realized_profit(magic=..., days=1)`), not self-accumulation — so the daily-loss halt counts **broker SL stop-outs** (its main case).
- **B4:** post-fill SL/TP retry + loud warning if a stop can't be attached (no silent stop-less open).
- **B6:** fail **closed** (skip the symbol) when a bar timestamp is unavailable, so a malformed feed can't silently revert to per-loop over-trading.
- **G1:** engine hosts the scalper — construct when assigned/enabled, `step(allow_new_risk=…)` at the **end of tick**, **skipped on the kill / operator-flatten early-returns** (or it re-opens right after a kill).
- **G2:** startup partition assertion (`SCALPER`/assigned ⊆ `ALL_SYMBOLS`, disjoint from the engine).
- **G3:** report scalper broker-SL/TP fills (today neither engine nor scalper logs them).
- **G4:** rewrite `test_scalper.py` to the new step API + the §6 cases (new-bar gating, per-bar time-stop, realized-P&L-from-deals, account-kill-flattens-scalper, partition assertion, new-risk gating, foreign-symbol guard, broker TP/SL on entry).

### 9.8 What's already correct in the rewrite (keep)
New-bar gating, per-bar `bars_held`, entry basis from `pos.price_open` (restart-safe), foreign-symbol guard, new-risk gating, and the one-process/two-magic structure (D1/D5) were all reviewed as sound.
