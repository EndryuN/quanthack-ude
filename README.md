# Quanthack — SIGNAL//LAYER

**An AI-native autonomous trading system.** A frontier LLM (Claude Opus 4.8) reads the market across multiple timeframes and *is* the strategy; a deterministic, risk-controlled engine sizes and executes its views; a second AI (NVIDIA Nemotron) cross-checks every call; and a real-time operator console lets a human supervise — all beneath a hard risk floor no model can override.

Built for a multi-day simulated trading competition (forex, metals, crypto · $1,000,000 capital · 1:30 leverage · **liquidation = instant elimination**). Design goal: **maximize return rank without ever blowing up.**

---

## Contents

- [What makes it AI-native (project description)](#what-makes-it-ai-native)
- [Architecture at a glance](#architecture-at-a-glance)
- [How a trade is decided](#how-a-trade-is-decided) → deep dive: **[docs/DECISION_FLOW.md](docs/DECISION_FLOW.md)**
- [Dual-AI consensus](#dual-ai-consensus-claude--nemotron)
- [Position sizing — one dial](#position-sizing--one-aggression-dial)
- [Risk system](#risk-system)
- [The integrated scalper](#the-integrated-scalper)
- [Operator control panel](#operator-control-panel)
- [Decision logging & self-grading](#decision-logging--self-grading)
- [Technology stack](#technology-stack)
- [Project structure](#project-structure)
- [Quickstart](#quickstart)
- [Backtesting the AI signal](#backtesting-the-ai-signal)
- [Configuration](#configuration)

---

## What makes it AI-native

Most trading bots are rigid rule machines with an optional "AI sentiment" bolt-on. Quanthack is the inverse: **the LLM is the decision-maker, and classical quant code is the safety cage around it.**

We arrived here honestly — momentum, mean-reversion, statistical-arbitrage and order-flow strategies were all built and backtested, and **none showed a durable edge after costs**. The frontier model's cross-asset reasoning was the only signal that survived, so it became the engine's sole signal source.

What elevates it from "an LLM that emits signals" to a genuine AI-native architecture:

1. **AI is the strategy.** Claude is shown a purpose-built, multi-timeframe market snapshot and returns, per instrument, a **direction + a calibrated confidence + a one-line rationale**, plus a market-regime narrative. Those structured, schema-validated views flow *directly* into a sizer that puts real capital behind the model's conviction.
2. **A blind multi-model ensemble.** NVIDIA **Nemotron** independently rates the *same* snapshot without seeing Claude's answer. Agreement amplifies conviction; it can never veto or flip direction. Strictly additive and graceful — no key, no problem (Claude-only).
3. **The system grades its own AI.** A directional hit-rate scorer measures whether each model was actually right, so trust is earned from realized results, not assumed.
4. **Full auditability.** Every 15-minute decision — both models' views, rationales, regimes, agreement verdicts, token costs, and the resulting orders — is persisted append-only as JSONL **and** human-readable Markdown.
5. **Human-in-the-loop, AI-respecting.** An operator can refresh the consensus on demand, re-weight risk, pin positions, or even override a single AI view as a deciding "third opinion" — but can never weaken the risk floor.

The result is a closed loop: **AI decides → second AI cross-checks → human supervises → system grades itself**, with deterministic guardrails that run *first*, every tick.

---

## Architecture at a glance

```
                ┌──────────────────────────── every 15 min ───────────────────────────┐
                │                                                                       │
 market (MT5)   │   build multi-timeframe snapshot ──► Claude Opus 4.8 ──► views+regime │
 ──────────────►│                                  └─► NVIDIA Nemotron (blind) ─┐       │
                │                                       consensus merge ◄────────┘       │
                └───────────────────────────────────────────┬───────────────────────────┘
                                                             ▼
   every 5s tick:   RISK (kill-switch first) ─► views ─► SIZER (confidence→lots) ─► RECONCILE
                          │                                                          │
                          │                                          guaranteed stop on every position
                          ▼                                                          ▼
                    graduated de-risk                                       broker (MT5 orders)
                                                                                     │
   integrated SCALPER slot (≤2 operator-assigned crypto symbols, gated + armed) ─────┘
                                                                                     │
   decision log (JSONL + Markdown)  ◄── state files ──►  SIGNAL//LAYER dashboard (read/steer)
```

Two processes, decoupled by files: the **engine** (trades) and the **panel** (reads logs/state + live MT5 read-only, writes operator intents the engine polls). With the panel off, the engine is byte-for-byte unchanged.

---

## How a trade is decided

Short version (full walkthrough in **[docs/DECISION_FLOW.md](docs/DECISION_FLOW.md)**):

```
1. RISK FIRST   → assess account; kill-switch can flatten everything before strategy is consulted
2. CEDE/HANDOFF → remove any operator-assigned scalper symbols from the engine's universe
3. METRICS      → equity curve, trailing-peak drawdown, 15-min Sharpe
4. AI VIEWS     → (every 15 min, or on demand) Claude read + blind Nemotron + consensus merge
5. SIZE & TRADE → views → target lots (confidence × aggression) → reconcile ONLY on signal change
6. ENSURE STOPS → guarantee a broker-side stop-loss on every open position, every tick
7. TRAIL STOPS  → ratchet stops into profit on winners
8. SCALPER      → run the integrated scalper slot LAST (isolated; can never crash the engine)
9. LOG          → persist the full decision (both models, costs, orders, account state)
```

Risk runs first **by design**: the strategy can never talk the system out of protecting itself.

---

## Dual-AI consensus (Claude + Nemotron)

Claude sets **direction**; NVIDIA **Nemotron** gives an independent, *blind* second opinion on the same snapshot. The merge rule is a **disagreement-aware conviction amplifier**:

- **Agree** → confidence is boosted: `combined = c + (1 − c) · gain · n`  (`gain = 0.5`).
- **Disagree / Nemotron flat / unavailable** → Claude's view stands. Nemotron never vetoes or flips.

A deliberate design choice: **Nemotron is *always* queried and recorded; the toggle only gates whether the *sizer acts* on it.** Even with influence off, Claude/Nemotron/Combined are all computed and logged — so you can watch them diverge over time (heatmap, dumbbell, projection cones) and decide whether the second opinion earns its place *before* enabling it. See [`bot/second_opinion.py`](bot/second_opinion.py).

---

## Position sizing — one `aggression` dial

A single `aggression ∈ [0,1]` interpolates the **entire** risk posture, mapped to the competition's incentives:

| | survival (a→0) | finals (a→1) |
|---|---|---|
| gross leverage | ~2× | ~6× |
| concentration | `conf¹` (diversified) | `conf²·⁵` (top-conviction) |
| weighting | inverse-vol (risk-parity) | conviction-chasing |
| single-name cap | ~30% | ~45% |

Weak views (`confidence < 0.15`) are dropped entirely. The sizer sits **below** the risk caps — it can never exceed them. Set live via the panel's presets (Default / Survival / Finals). See [`bot/sizer.py`](bot/sizer.py).

---

## Risk system

Risk is a first-class subsystem that runs **before** strategy every tick ([`bot/risk.py`](bot/risk.py)). It keeps the book comfortably inside every competition red-line — a forced liquidation cannot happen before the engine's own kill-switch fires.

| Protection | Competition red-line | Self-imposed |
|---|---|---|
| Anti-liquidation kill-switch | stop-out at 30% margin level | **flatten all at 150%** |
| Effective leverage | penalty above 28× | hard cap **12×** (targets ~6×) |
| Margin usage | penalty above 90% | hard cap **60%** |
| Drawdown | (affects rank) | **graduated de-risk** 20%→½ · 30%→¼ · 40%→flat |
| Concentration | penalty above 90% | sizer-capped ~45% + a 90%/30-min **watch** clock |

Key mechanisms:

- **Trailing-peak graduated de-risk** — drawdown is measured against the *trailing equity peak* (the scored metric). Instead of flatten-and-freeze (which would forfeit return rank), the whole book is scaled down in steps and **re-arms automatically** as equity recovers.
- **Guaranteed broker-side stop on every position, every tick** — orders are opened **clean** (this broker rejects market orders carrying an inline SL, ret 10006), then the stop is attached the same tick via a separate SLTP action, with every stop price **clamped to the broker's minimum stop distance** so it isn't rejected for being too tight.
- **Trailing stops** that arm only after a meaningful favorable move, then ratchet monotonically and never lock worse than break-even.
- **Account-wide** — risk is measured across *all* order streams (engine + scalper), so a second strategy can't hide exposure, and the kill-switch flattens everything.

Concentration/net-directional are **computed for display and the watch clocks but do not block new trades** — a hard gate there once froze the book mid-regime; real protection comes from the kill-switch, leverage/margin caps, drawdown de-risk, and per-position stops.

---

## The integrated scalper

A **second strategy hosted inside the engine** (own magic `990624`), not a separate process — so two order streams can never race on the netting account. It runs **alongside** the main engine on up to **2 operator-assigned crypto symbols**, which are *ceded* from the engine's universe (the engine flattens its own position on a ceded symbol and stops trading it).

- **Strategy** — crypto fast-momentum: trade *with* breakouts where minute-scale moves dwarf the spread. Alts (ETH/SOL/XRP) require a confirming **BTC lead-lag** move; BTC trades on its own breakout; FX/metals scalp pure momentum.
- **Gated arming** — assigning a symbol auto-runs a **walk-forward backtest** (sweep params on a train split → lock the best → evaluate **out-of-sample**). A symbol can be **armed only if it clears the gate** out-of-sample, or **force-armed** with an explicit confirm. Arming is **per-symbol** — arming/gating one never disarms another.
- **Fast exits** — broker-side SL+TP attached at entry (clamped, anchored to the actual fill); if a valid stop can't attach, the position is closed (a naked few-second scalp is worse than no trade).
- **Anti-churn** — 30s cooldown, 60s min-hold, and a 12-trades/hour cap per symbol.
- **Isolation** — the scalper step is wrapped so an error there can *never* crash the engine tick; it runs last; it has its own daily-loss halt; and the account-wide kill flattens it too. On launch its armed state is **reset**, so a saved live-arm can never auto-fire on restart.

Everything is driven from the **Controls** panel: pick a symbol → Assign (auto-backtests) → Arm. See [`bot/scalper.py`](bot/scalper.py), [`bot/crypto_signal.py`](bot/crypto_signal.py), [`bot/crypto_backtest.py`](bot/crypto_backtest.py).

---

## Operator control panel

A separate, opt-in web dashboard (**SIGNAL//LAYER**) that reads the logs + live MT5 and lets you watch — and optionally steer — the engine. It is the *sole writer* of control files; the engine polls and acks them, and the risk floor runs *before* and is never gated by any control.

- **Watch** — live KPI header with clickable Claude/Nemotron reasoning, equity vs the risk floor, live risk gauges + 30-min tripwire clocks, the consensus **dumbbell** and over-time **heatmap** (Claude / Nemotron / Combined layers), per-symbol **projection cones**, positions + P/L, and a History view (entry/exit markers colored by direction).
- **Steer** — aggression, pause, panic-flatten, **manual consensus refresh** (re-query the AI now, bypassing the 15-min cadence), per-symbol allow/block, named presets, the per-symbol scalper assign → gate → arm flow, and per-position pin / human-override / take-profit.

Steering is **off by default**; the launcher's *With panel* choice enables it (`QUANTHACK_PANEL=1`). Standalone, the dashboard is read-only. Full details: **[docs/CONTROL_PANEL.md](docs/CONTROL_PANEL.md)**.

---

## Decision logging & self-grading

Every 15-minute refresh is persisted to per-day files — `logs/ai_decisions_YYYY-MM-DD.jsonl` (machine) and `.md` (human): the regime, **per-symbol rationale from both models**, the consensus verdict (✅/❌), the orders placed, token cost, and account state. It doubles as a Claude-vs-Nemotron audit trail ([`bot/decision_log.py`](bot/decision_log.py)).

A directional **hit-rate scorer** ([`bot/model_scoring.py`](bot/model_scoring.py)) grades each model's calls against realized price moves — the foundation for trusting (or distrusting) a model from evidence.

---

## Technology stack

| Layer | Technology |
|---|---|
| **Language / runtime** | Python 3.13 · vanilla JS / HTML5 / CSS3 (no build step) |
| **Primary AI** | **Claude Opus 4.8** (`claude-opus-4-8`) via the Anthropic SDK — adaptive extended thinking, `effort: high`, Pydantic structured output |
| **Second-opinion AI** | **NVIDIA Nemotron** (`nvidia/nemotron-3-super-120b-a12b`) via the OpenAI-compatible NVIDIA NIM API (reasoning disabled for latency) |
| **Backtest AI (cost-saving)** | **Doubleword** (`DeepSeek-V4-Flash`) via the **Pydantic AI Gateway** (batch/async tiers) |
| **Schema / validation** | Pydantic v2 (`MarketView` / `InstrumentView`) — malformed AI output can't reach trade logic |
| **Broker / market data** | **MetaTrader 5** Python API (netting account, magic-tagged order streams) |
| **Backend** | **FastAPI** + **Uvicorn** (read-only dashboard API) |
| **Frontend / viz** | **Plotly.js** (equity, projection cones, consensus heatmap/dumbbell) + vanilla JS polling |
| **Numeric / data** | NumPy · pandas · PyArrow (L2 tick Parquet → OHLC bars) |
| **HTTP** | httpx (sync + async OpenAI-compatible calls) |
| **Persistence** | JSON control/state files + JSONL/Markdown decision logs (atomic writes) |
| **Testing** | 50+ script-style `test_*.py` (pure-logic modules are network-free) |

API keys (`ANTHROPIC_API_KEY` required; `NVIDIA_API_KEY`, `PYDANTIC_AI_GATEWAY_API_KEY` optional) live in a **gitignored** `.env`.

---

## Project structure

```
bot/
  engine.py        # the 5s tick loop — orchestrates risk, AI, sizing, reconcile, stops, scalper
  ai_signal.py     # builds the multi-timeframe snapshot, queries Claude, validates output
  second_opinion.py# blind Nemotron (NVIDIA) second opinion + the consensus merge rule
  sizer.py         # turns (direction, confidence) into target lots; one `aggression` dial
  risk.py          # kill-switch, leverage/margin caps, trailing-peak graduated drawdown de-risk
  client.py        # MetaTrader 5 interface — data + orders (clean-open + SLTP, stop clamping)
  scalper.py       # integrated crypto-momentum scalper slot (gated, per-symbol arming, isolated)
  crypto_signal.py # crypto fast-momentum + BTC->alt lead-lag (pure)
  crypto_backtest.py# walk-forward arming gate (train/test split)
  decision_log.py  # per-day JSONL + Markdown log of every AI decision
  model_scoring.py # per-model directional hit-rate scorer (self-grading)
  controls.py      # operator control channel (knobs/presets/scalper) — gated, default off
  overrides.py     # per-position overrides (pin / human view / take-profit) — gated
  state.py         # equity history, drawdown, 15-min Sharpe; crash-resilient persistence
  config.py        # all tunables: symbols, risk limits, sizer dial, cadence, scalper params
  gateway.py / ai_signal_gw.py / ai_backtest.py / ensemble.py   # backtest + ensemble (gateway)
panel/             # opt-in operator dashboard (FastAPI backend + Plotly frontend)
docs/              # DECISION_FLOW · CONTROL_PANEL · STRATEGY · AI_BACKTEST · BACKTEST_RESULTS
run_live.bat / run_live.ps1   # interactive launcher (Live/Dry · With/No panel)
test_*.py          # the test suite (run with pytest)
```

The repo also includes the research/probe scripts used to validate the approach (and to rule out the strategies that showed no edge).

---

## Quickstart

**Requirements:** Python 3.13+, a running [MetaTrader 5](https://www.metatrader5.com/) terminal with Algo Trading enabled, and an Anthropic API key. An NVIDIA `nvapi-…` key (free from [build.nvidia.com](https://build.nvidia.com)) enables the Nemotron second opinion but is **optional**.

```bash
pip install -r requirements.txt
cp .env.example .env     # edit: ANTHROPIC_API_KEY (required), NVIDIA_API_KEY (optional)
```

**Launch** — the simplest path is the interactive launcher: double-click **`run_live.bat`**. It asks:

1. **Mode** — `[L]ive` or `[D]ry-run` (dry-run computes & logs intended trades but sends NO orders).
2. **Panel** — `[W]ith panel` (opens **SIGNAL//LAYER** at `http://127.0.0.1:8800` and enables steering) or `[N]o panel`.

Or directly:

```bash
python -m bot.engine          # dry-run, no panel        (or:  .\run_live.ps1)
python -m bot.engine --live   # LIVE, no panel           (or:  .\run_live.ps1 -Live)
.\panel\run_panel.ps1         # watch-only dashboard (read-only)
```

> **Deploying changes:** the engine runs only the code in its own working directory. After pulling new code, **restart the engine process** (the launcher) — restarting the browser/panel alone does not reload engine logic.

---

## Backtesting the AI signal

The AI signal needs one LLM call per historical decision point — hundreds of latency-insensitive calls. Those run cheaply through **Doubleword's batch/async tiers** via the Pydantic AI Gateway:

```bash
python run_ai_backtest.py --mode online  --cadence-bars 4                 # quick
python run_ai_backtest.py --mode export  --out requests.jsonl --cadence-bars 4   # cheapest: export…
python run_ai_backtest.py --mode replay  --results results.jsonl --cadence-bars 4 # …then replay
```

The first full 425-call backtest cost **≈ $0.29** on Doubleword vs **≈ $21.79** at the live model's rate (**~98.7% cheaper**). Note the backtest runs on a *different, cheaper model* (DeepSeek V4 Flash) — it validates the **architecture and prompt**, not live Claude performance. See **[docs/AI_BACKTEST.md](docs/AI_BACKTEST.md)** and **[docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md)**.

---

## Configuration

Everything lives in [`bot/config.py`](bot/config.py). The dials you'll touch most:

- **`aggression`** (0–1) — interpolates the whole posture from *survival* to *finals* (set live via presets).
- **`signal_source`** — `"ai"` (Claude + sizer, default) or `"hybrid"` (classical fallback).
- **Scalper** — `notional_per_symbol`, `entry_bps`, `tp_bps`/`stop_bps`, `cooldown_secs`, `min_hold_secs`, `max_trades_per_hour`.

`.env` (your real keys) is gitignored and never committed; `.env.example` is a placeholder only.
