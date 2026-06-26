# Technical Notes — How a Trading Decision Is Made, Beginning to End

This document traces a single decision from raw market data to a placed order (and its
audit record), and explains every gate it passes through. It is the engineering companion
to the high-level [README](../README.md).

Two cadences run concurrently:

- **The tick loop — every `loop_seconds = 5s`** (`bot/engine.py::Engine.tick`). Risk, reconciliation, stops, and the scalper run here.
- **The AI refresh — every `ai_refresh_seconds = 900s` (15 min)**, checked on the tick (`_refresh_ai_views`). This is when new *opinions* are formed; the tick loop *acts* on the latest opinion.

The separation matters: the LLM is queried infrequently (cost + the market doesn't change its multi-timeframe story every 5s), while risk and execution stay continuous.

---

## The pipeline, in order

Every field below maps to real code in `bot/engine.py::tick` unless noted. The **order is the design** — each step assumes the ones before it have run.

### 0. Account snapshot
The engine reads the live MT5 account (equity, margin, margin level) via `client.account()`.

### 1. RISK FIRST — `risk.assess()` → maybe `risk.enforce()`
Before strategy is even consulted, `bot/risk.py::RiskEngine.assess()` builds a `RiskSnapshot`:

- Aggregates **all** open positions across *every* magic (engine + scalper) into gross/net notional, gross leverage, margin usage, per-symbol concentration (`largest_frac`), net-directional fraction.
- **Kill-switch:** if `margin_level < 150%` (competition stop-out is 30%, so this fires far above danger) → `kill = True`. The engine calls `risk.enforce()` which **flattens the entire account** and returns immediately. Nothing downstream can run.
- **Trailing-peak graduated de-risk:** drawdown is measured against `self._peak` (the running equity high — the *scored* metric, not round-start). It returns a `risk_scale`: `≥20% → 0.5`, `≥30% → 0.25`, `≥40% → kill`. This scales the whole book down later (step 5) and **re-arms** as equity recovers.
- **Soft new-risk gates** (`allow_new_risk`): leverage > 12×, margin > 60%, or margin level < 250% block *new* risk but still permit reductions.
- Concentration / net-directional are **computed for display and the watch clocks, but do NOT block new risk** (a hard gate there once froze the book mid-regime).
- **Tripwire watch clocks** (`_track`): record how long each near-penalty condition (lev>25×, margin>85%, conc>85%) has been continuously true, to warn before the competition's 30-minute time-based penalties.

### 2. Scalper cede + handoff
`live_engine_symbols(assigned)` removes any operator-assigned scalper symbols from the engine's tradable universe (`self._active_symbols`). `_handoff()` flattens the **engine's own** position on a ceded symbol (magic-filtered, so it never touches the scalper's book) — a clean handover. With nothing assigned, the universe is the full instrument set.

### 3. Metrics / state
`state.update(equity)` appends to the equity curve and recomputes drawdown and the **15-minute Sharpe** (mirroring the competition's exact non-annualized definition). Persisted atomically so a crash/restart keeps history.

### 4. AI VIEWS — formed every 15 min (or on demand)
`_signal_changed()` is true when `_views_ts != _last_acted_ts`. The refresh itself (`_refresh_ai_views`) runs on cadence, or immediately when the operator presses **Refresh views** (which bumps `refresh_seq`; the engine sets `_force_refresh`, zeroes the failure backoff, and re-queries that tick). On API failure it backs off 30s → doubling → 300s cap.

The refresh is a multi-stage merge:

**4a. Build the context** — `bot/ai_signal.py::build_context()` assembles a compact, token-efficient, genuinely **multi-timeframe** snapshot per instrument: multi-horizon returns (1h/4h/1d/3d), trend direction on M15/H1/H4 EMAs, a momentum z-score, per-bar volatility, and where price sits in its 5-day range (0–100%).

**4b. Claude (primary)** — `get_ai_views()` calls `claude-opus-4-8` with adaptive extended thinking, `effort: high`, and **Pydantic structured output** (`MarketView`). The system prompt frames Claude as *the* signal engine, states the scoring rules (≈70% return rank; drawdown/Sharpe secondary; liquidation = elimination), and instructs it to weight setups where timeframes *agree*, stay calibrated ("liquid FX deserves low confidence"), prefer `flat` over guessing, and keep cross-asset macro coherent. Output per instrument: `direction ∈ {long, short, flat}`, `confidence ∈ [0,1]`, one-line `rationale`, plus a market-`regime` narrative. A refusal or malformed body raises (graceful degrade); the result is normalized to `{symbol: (dir_int, confidence)}`.

**4c. Nemotron (blind second opinion)** — `bot/second_opinion.py::get_second_opinion()` sends the **same** context (never Claude's answer) to `nvidia/nemotron-3-super-120b-a12b` over the OpenAI-compatible NVIDIA API, asking for the identical JSON schema. Reasoning is disabled (`enable_thinking: False`) for latency. Any failure raises → that voter is simply absent.

**4c′. DeepSeek (blind ensemble voter)** — a second, independent blind opinion sends the **same** context (never Claude's answer) to `deepseek-ai/DeepSeek-V4-Pro` **live** via the Pydantic AI Gateway → Doubleword, routed through the gateway's `/responses` endpoint (the `/chat/completions` path 400s on an injected `safety_identifier`), asking for the identical schema. Any failure raises → that voter is simply absent.

**4d. Consensus merge** — `apply_consensus()`:
- Claude is **always authoritative on direction** — neither second opinion can set, flip, or veto it.
- Each blind voter (Nemotron, DeepSeek) that independently agrees (same non-flat direction) can only *raise* confidence: `combined = cc + (1 − cc) · gain · mean(agreeing voters' confidence)` with `gain = 0.5`; Claude's confidence is used as-is when no one agrees.
- Otherwise each voter's relationship is tagged `disagree` / `<model>_flat` / `<model>_unavailable` and Claude stands.
- Per-source influence is gated independently: the `second_opinion` toggle (default ON) gates whether the sizer acts on Nemotron, and a separate `ensemble_input` toggle (default OFF) gates DeepSeek/ensemble influence. **Both voters are *always queried, recorded, and scored* regardless of their toggle** (so the panel can show and grade Claude / Nemotron / DeepSeek / Combined over time).

**4e. Human override (optional)** — `apply_human_override()` lets a pinned human view become the *deciding* voice that can flip direction, tagged `human_override` in the log.

The merged `{symbol: (direction, confidence)}` becomes `self._views`, stamped with `_views_ts`.

### 5. SIZE — `_build_targets()` → `sizer.build_targets()`
Runs only when `_signal_changed()`. Views are filtered to `_active_symbols` (never a ceded symbol). The sizer turns conviction into target lots:

- **Effective aggression** = the operator's preset value (`effective_aggression`), default 0.30.
- **Concentration:** raw weight = `confidence ** power`, where `power` interpolates 1.0 (diversified) → 2.5 (concentrated) with aggression.
- **Leverage:** gross target interpolates ~2× → ~6× with aggression.
- **Risk normalization:** inverse-volatility weighting `(1/vol)^(1−a)` — full risk-parity at a=0, faded toward conviction-chasing at a=1.
- **Floors/caps:** views below `min_conviction = 0.15` are dropped; single-instrument share capped ~30% → 45%.

Then the target vector is adjusted in place:
- **Drawdown scale:** if `risk_scale < 1.0`, multiply all targets (graduated de-risk).
- **De-risk-only:** if paused or `not allow_new_risk`, clamp so positions can only *reduce*, never open/increase/flip (`_derisk_only`).
- **Blocked/allow:** operator-blocked symbols are clamped toward flat (`_clamp_blocked`) — de-risk only, never forced flips.
- **Pins:** human-pinned symbols are dropped from the target set so reconcile won't touch them.

### 6. RECONCILE — `_reconcile(targets)`
For each symbol, compare target lots to the live position and issue **only the delta**, gated by a `should_rebalance` deadband and a `volume_min` clamp (so tiny diffs don't churn the spread). Order placement (`_do_order`):

- Open **clean** — no inline SL. This broker rejects market DEALs carrying a stop (`ret 10006`, observed live on metals).
- Only **transient** retcodes (requote 10004 / prices-changed 10020 / no-quotes 10021) are retried; partial-fill / invalid-volume / no-money / autotrading-off are **not**.
- After a fill, `_last_acted_ts = _views_ts` so reconcile won't fire again until the next view refresh.

### 7. ENSURE STOPS — `_ensure_stops()` (every tick)
Independent of reconcile, every open position is checked for a broker-side SL. If missing, the `SL_PCT` failsafe (FX 5% / metal 8% / crypto 15%) is attached via a separate `TRADE_ACTION_SLTP` — the reliable path given the inline-SL rejection. Every stop price passes through `client.clamp_stop_price()`, which enforces the broker's `min_stop_distance` (`trade_stops_level × point`) plus a buffer so it isn't rejected with `10016`. This is the anti-liquidation guarantee: **no position runs naked, ever.**

### 8. TRAILING STOPS
`_trail_candidate()` (a pure function) arms only after a meaningful favorable move (FX 1% / metal 2% / crypto 4%), then ratchets the stop monotonically toward profit — never loosening, never locking worse than break-even.

### 9. SCALPER — runs LAST, fully isolated
`_run_scalper()` ticks the integrated slot over operator-assigned symbols. It runs after everything else so a kill / operator-flatten early-return skips it, and the whole step is wrapped in `try/except` — **a scalper error can never crash the engine tick.** See [Scalper decision path](#scalper-decision-path).

### 10. LOG — `_write_decision_record()`
On each refresh, one append-only record is written as JSONL **and** Markdown: all models' identities and regimes, per-view direction/confidence/rationale for each model, the combined value + agreement verdict + `boosted`/`influence` flags, token costs in USD, the account snapshot, and the orders actually placed. Failed refreshes are logged too. The panel reads these read-only.

---

## Scalper decision path

The scalper (`bot/scalper.py`) is a per-tick momentum slot with its own magic (`990624`).

**Arming (operator → gate → engine):**
1. Operator **assigns** a symbol in the panel → it's ceded from the engine.
2. The panel auto-runs the **walk-forward gate** (`bot/crypto_backtest.py::walk_forward` via `/api/scalper/gate`): sweep entry/lookback/tp/stop on a 60% **train** split, lock the best by Sharpe (≥30 trades to reject overfit), then evaluate **out-of-sample** on the 40% **test** split. Arm-eligible only if the test split is net-positive after cost **and** beats the engine's Sharpe with enough trades. The verdict + locked params are persisted.
3. Operator **arms** the symbol. The engine is the final boundary: a symbol trades **live** only if it is individually armed **AND** (gate-cleared **OR** force-armed) **AND** sized. Arming is per-symbol — one never disarms another.

**Per-tick decision (`crypto_signal.py::crypto_decision`):**
- **Entry:** a momentum breakout — the return over `lookback` bars exceeds `entry_bps` → trade that direction.
- **Lead-lag:** crypto alts (ETH/SOL/XRP) require BTC's recent move to *agree* (confirmation); BTC trades on its own breakout; FX/metals scalp pure momentum (`config.is_crypto_alt`).
- **Exit:** ride until the broker TP/SL (set at entry) or a *reverse* breakout flip — momentum never mean-reverts to exit.

**Execution + safety:** opens clean, attaches clamped SL+TP anchored to the actual fill; if no valid stop can attach, it **closes** the position. Anti-churn: 30s cooldown, 60s min-hold, 12 trades/hour cap. Its own broker-deal-sourced daily-loss halt; the account-wide kill flattens it too. On launch, armed state is **reset** so a saved live-arm can't auto-fire.

---

## How operator actions enter the flow

The panel never trades. It writes intents to `state/controls.json`; the engine reads and **acks** them via `state/control_ack.json` (one-shot commands use monotonic `*_seq` counters; the engine syncs the ack to the controls on startup so a stale ack can't silently swallow a command):

| Action | Mechanism | Where it lands |
|---|---|---|
| Pause / Flatten | `paused` flag / `flatten_seq` | step 1 / step 5–6 |
| Aggression / Preset apply | `aggression` + `apply_seq` (forces a reconcile next tick) | step 5 |
| Refresh views | `refresh_seq` → `_force_refresh` | step 4 (bypasses cadence + backoff) |
| Nemotron influence | `second_opinion` flag (default ON) | step 4d (gates sizer use only) |
| DeepSeek / ensemble influence | `ensemble_input` flag (default OFF) | step 4d (gates sizer use only) |
| Block / Allow | `block` / `allow` lists | step 5 (`_clamp_blocked`, de-risk only) |
| Scalper assign / gate / arm | `scalper_symbols` / `scalper_gate` / `scalper_armed[_force]` | steps 2 & 9 |
| Pin / human view / take-profit | `overrides.json` | step 4e / step 5 (pins) |

The risk floor (step 1) runs **before** any of this and is never gated by a control.

---

## Appendix — exactly what we feed the LLMs

All three models receive the **identical** system prompt and the **identical** market
snapshot (`second_opinion.py` imports `SYSTEM` from `ai_signal.py`); Nemotron and
DeepSeek-V4-Pro are blind peers — same inputs, no sight of Claude's answer.

**The data payload** (`build_context`, one line per instrument, ~14 lines/call,
refreshed every ~15 min): a token-cheap multi-timeframe *digest* — not raw bars.
Per symbol: `px` (M15 close); returns over 1h/4h/1d (4/16/96 M15 bars) and 3d (72
H1 bars); trend up/dn from fast/slow EMAs on M15(20/80), H1(10/40), H4(6/24);
`mom_z` (32-bar momentum z-score); `vol/bar` (48-bar volatility %); `5d-range`
(price's position in its ~5-day H1 high/low band, 0–100). Example line:

```
EURUSD: px=1.08423 | ret 1h=+0.03 4h=-0.12 1d=+0.21 3d=+0.55% | trend M15=up H1=up H4=dn | mom_z=+0.84 vol/bar=0.04% | 5d-range=62%
```

**The system prompt** frames the model as the signal engine, states the scoring
rules (≈70% return rank; liquidation = elimination), and instructs: weight
timeframe-aligned setups higher, stay calibrated (liquid FX deserves low
confidence), don't chase stretched moves, prefer `flat` over guessing, keep
cross-asset macro coherent (USD pairs agree on USD; metals/crypto as risk
barometers), and rate every instrument.

**The user message** wraps the context and asks for a directional view +
calibrated confidence per instrument. **Output contract:** Claude is forced into
the Pydantic `MarketView` schema (`regime` + `{symbol, direction, confidence∈[0,1],
rationale}`); Nemotron and DeepSeek are asked for the same shape as a strict JSON
object. A malformed/hallucinated reply can't validate and can't reach trade logic.

**Not fed:** raw ticks/candles, order book, account/equity/position state,
Claude's answer (to the blind voters), or any news/text feed. The models decide
*direction + conviction*; sizing, risk, and execution are the engine's job.

## Invariants (the things that must always hold)

1. **Risk runs first.** The kill-switch can flatten the account before strategy is consulted; no model or control can override it.
2. **No naked positions.** Every open position has a broker-side stop within one tick of opening.
3. **The scalper can never crash the engine.** Isolated, runs last, resets on launch.
4. **The panel never trades.** It only reads, and writes intents the engine chooses to honor.
5. **Every AI decision is auditable.** All models, costs, rationale, and orders are persisted append-only.
6. **AI output is schema-constrained.** A hallucinated/malformed response can't reach trade logic.
