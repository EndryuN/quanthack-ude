# Quanthack — An AI-Reasoned Trading Engine

*Technology Award submission — project overview for judges*

---

## In one sentence

**Quanthack is an autonomous trading system that lets a frontier AI model (Claude Opus 4.8) read the market like a human analyst — across multiple time horizons — and then translates that judgment into mathematically-sized, risk-controlled trades that run live, hands-free.**

---

## The 30-second pitch

Most algorithmic trading bots are *rule machines*: "if the 50-day average crosses the 200-day average, buy." They break the moment the market does something the rules didn't anticipate.

Quanthack is different. It separates **judgment** from **execution**:

1. **Judgment** — A frontier LLM (Claude) is shown a compact, multi-timeframe snapshot of 14 instruments (forex, metals, crypto) and asked, like a trading desk analyst, for a directional view and a *calibrated confidence* on each. It reasons about cross-asset stories ("broad USD strength, risk-off") rather than blindly following one indicator. A **second model — NVIDIA Nemotron — gives an independent, blind second opinion**, and a consensus rule boosts conviction where the two agree.
2. **Execution** — A deterministic, math-driven engine takes those views and converts them into actual position sizes, places the orders, manages stops, and enforces hard risk limits that the AI **cannot override**.

This split is the core idea: *the AI is smart but fallible, so it is wrapped in a safety cage of classical quant risk management.* You get the adaptability of AI reasoning with the discipline of a rules-based risk system.

---

## The context: what it's competing in

The system is built for a **multi-day simulated trading competition** (1:30 leverage, $1,000,000 of simulated capital, 14 tradable instruments). Scoring is roughly:

- **~70% total return rank** (the main prize)
- Smaller weights on **drawdown** and **Sharpe ratio**
- **Forced liquidation = instant elimination**

So the strategic problem is: *maximize return rank without ever blowing up.* Quanthack is designed around exactly that trade-off — take meaningful, conviction-weighted risk, but make a blow-up structurally impossible.

---

## How it works — the trading loop

The engine runs a continuous loop, every 5 seconds. The **order of authority each tick** is deliberate and is the heart of the design:

```
   ┌──────────────────────────────────────────────────────────┐
   │  EVERY 5 SECONDS:                                          │
   │                                                            │
   │  1. RISK FIRST  ── kill-switch & guardrails check          │
   │       │           (can flatten everything; nothing         │
   │       │            below it can override this)             │
   │       ▼                                                    │
   │  2. METRICS  ──── update equity curve, drawdown, Sharpe    │
   │       │                                                    │
   │       ▼                                                    │
   │  3. AI VIEWS  ─── refresh Claude + a blind Nemotron        │
   │       │            second opinion, merged by consensus     │
   │       │            (every 15 min, not every tick)          │
   │       ▼                                                    │
   │  4. SIZE & TRADE ─ convert views → target positions →      │
   │       │            reconcile live positions toward them    │
   │       ▼                                                    │
   │  5. TRAIL STOPS ── ratchet stops into profit on winners    │
   │       │                                                    │
   │       ▼                                                    │
   │  6. LOG ───────── persist the decision (both models'       │
   │                   views + rationale, orders, cost) to disk │
   └──────────────────────────────────────────────────────────┘
```

**Why risk runs first:** the strategy can never talk the system out of protecting itself. Even if the AI screams "buy," if the account is near a liquidation threshold, the risk engine flattens everything *before* the strategy is even consulted.

---

## The three things that make it novel

### 1. The AI signal is *multi-timeframe* and *calibrated*

For each instrument, the engine builds a compact snapshot containing:

- **Trend direction on three timeframes** — 15-minute, 1-hour, and 4-hour charts
- **Returns over multiple horizons** — 1h, 4h, 1-day, 3-day
- **Momentum, volatility, and where price sits in its recent range**

Claude is explicitly instructed to **weight setups higher when timeframes agree** (15m + 1h + 4h all trending up = high conviction) and **lower when they conflict**, and to be wary of chasing a move already stretched to a range extreme. Crucially, it must return a **calibrated confidence in [0,1]** — the system asks it to assign *low* confidence to efficient, hard-to-predict markets and reserve *high* confidence for coherent cross-asset macro stories. It is told to prefer saying "flat" over guessing.

This is the difference between "an AI that picks stocks" and "an AI that knows what it doesn't know." The output is validated against a strict schema (Pydantic), so a malformed or hallucinated response can never reach the trading logic.

**A second model, as a disagreement-aware second opinion.** The same market snapshot is sent — *blind*, with no sight of Claude's answer — to an **NVIDIA Nemotron** model (via the OpenAI-compatible NIM endpoint). A consensus rule then merges the two: Claude always sets the direction, and where Nemotron *independently agrees*, conviction is boosted toward 1.0 (so high-agreement trades are sized larger); where they disagree, Claude's view stands — Nemotron never vetoes. The intent is a Sharpe-improving conviction amplifier rather than a raw ensemble average. It's **strictly additive**: with no `NVIDIA_API_KEY`, a timeout, or any failure, the engine degrades to Claude-only with identical behavior. Every refresh — both models' views and rationale, the agreement verdict, the orders, and the token cost — is written to a per-day decision log (`logs/ai_decisions_*.jsonl` / `.md`), which doubles as a Claude-vs-Nemotron audit trail.

### 2. Confidence is turned into size by a *variance-aware sizer* with one dial

The AI gives a direction and a confidence. The **sizer** turns those into actual lot sizes using a single, interpretable `aggression` dial from 0 to 1:

- **aggression = 0 (survival mode):** low leverage, diversified across all conviction names, **inverse-volatility weighted** (risk parity) so the equity curve stays smooth — this protects against elimination and drawdown penalties.
- **aggression = 1 (finals mode):** higher leverage, **concentrated into the highest-conviction names** (weight ∝ confidence²·⁵) to maximize return-rank variance when it's time to go for the win.

One number smoothly interpolates the entire risk posture between "don't get eliminated" and "go for the top of the leaderboard." This directly mirrors the competition's incentive structure.

### 3. A safety cage that makes blow-up *structurally impossible*

The risk engine sits **below** the strategy and enforces hard limits the AI cannot touch:

| Protection | Competition red-line | Quanthack's self-imposed limit |
|---|---|---|
| **Kill-switch** (anti-liquidation) | stop-out at 30% margin level | **flatten everything at 150%** — far above danger |
| **Leverage** | penalty above 28× for 30+ min | hard cap at **12×**, targets ~6× |
| **Margin usage** | penalty above 90% for 30+ min | hard cap at **60%** |
| **Concentration** | penalty above 90% in one instrument | capped at **45–50%** of gross |
| **Drawdown** | (affects rank) | own circuit-breaker de-risks at **20%** |

It even tracks *how long* each near-tripwire condition has been continuously true, so it can warn and act before a time-based penalty (e.g. ">28× for 30 minutes") is ever incurred. There's also a broker-side stop-loss failsafe in case the bot itself crashes or goes offline.

The result: even at maximum aggression, the system stays comfortably inside every competition red-line, and a forced liquidation cannot happen before its own kill-switch fires.

---

## Engineering details judges may appreciate

- **Anti-churn design.** A subtle but important fix: positions are only re-reconciled when the *signal set actually changes* (every 15 min when the AI refreshes), not every 5-second loop. Reconciling every tick was silently bleeding money to the bid-ask spread (the "SOL-churn bug"). Risk checks and trailing stops still run every loop.
- **Trailing stops that lock in gains.** Once a position is a *meaningful* winner (not noise), the engine ratchets its stop-loss into profit — and only ever tightens it, never loosens. Thresholds are tuned per asset class (FX 1%, metals 2%, crypto 4%) because trailing too tightly whipsaws and bleeds spread (proven by backtest).
- **Crash-resilient state.** The equity curve is persisted to disk on a 15-minute grid, so a mid-competition restart doesn't lose the history the Sharpe-ratio calculation depends on. The Sharpe formula mirrors the competition's exact (non-annualized, 15-minute) definition.
- **Honest research trail.** The AI signal is the chosen approach *because* the team tested momentum, mean-reversion, statistical arbitrage, and order-flow strategies first and found no durable edge in the data. The philosophy — documented in the code — is: "no easy alpha exists, so size for rank-variance while staying survivable."
- **Backtesting the AI itself, affordably.** Validating an *LLM-driven* signal means one model call per historical decision point — hundreds to thousands of latency-insensitive calls, impractical at interactive pricing. Quanthack routes these through **Doubleword inference** (via the Pydantic AI Gateway, an OpenAI-compatible endpoint). The backtester is split into three decoupled phases — *plan* the prompts, *infer* them in bulk (exported as an OpenAI-format batch JSONL), and *replay* them through the exact live sizer and risk loop — so the expensive AI step is paid once while sizing can be re-tuned for free. Higher timeframes are resampled from M15 with strict no-lookahead.
  - **Concrete payoff:** the first full backtest (425 decision points over a month) cost **≈ $0.29 on Doubleword** vs. **≈ $21.79** for the same workload at the live model's rate — a **~98.7% cost reduction** that turns AI-signal backtesting from a prohibitive job into routine, repeatable analysis (parameter sweeps re-run for free against cached views).
  - **What it found:** the architecture is **survivable but the cheap model has no edge** — across every aggression level the signal lost 2–4%, and even at maximum aggression the risk system capped drawdown at ~21% with no liquidation, confirming the safety design works on real data. (Honest caveat, documented: the backtest runs on a cheaper model than live, so it validates the *architecture, prompt, sizer and risk loop*, not the exact production model.)
  - See [`docs/AI_BACKTEST.md`](docs/AI_BACKTEST.md) and [`docs/BACKTEST_RESULTS.md`](docs/BACKTEST_RESULTS.md).
- **Cost & health observability.** Every AI call logs its token usage and dollar cost, the model's stop reason, and the resulting views, so the operator can watch the system reason in real time.

---

## The operator control panel (opt-in)

A separate web app — **SIGNAL//LAYER** — turns the decision log into a live monitoring and control surface, without ever being able to compromise the engine. It is built in three layers and is **off by default**: the engine only honours its controls when `use_control_panel` is on, which the launcher's *With panel* choice sets via `QUANTHACK_PANEL=1` (the standalone watch-only launcher never sets it). With the flag off the engine is byte-for-byte unchanged.

- **Watch (read-only):** a header strip with live KPIs (equity, return, leverage, margin level, drawdown, Sharpe), clickable **Claude / Nemotron regime pills** that open each model's full reasoning, a currency-strength ribbon, and a staleness badge on the AI read. The dashboard shows the Claude×Nemotron consensus as a dumbbell (split FX / Crypto / Metals) + an over-time heatmap, the equity curve with the risk-floor line drawn on it, live-reacting risk gauges + 30-minute tripwire clocks, an open-positions/orders feed, and per-symbol price (1m / 15m / 1h) with per-model forward **projection cones** (an honest rendering of direction×confidence, not a fabricated price target). Separate **History** and **Scalper** pages give a per-symbol stance timeline and the dormant scalper's status. It runs as a *separate process* that only reads `logs/`, `state/`, and live MT5 — it cannot place an order.
- **Steer (global):** aggression, pause, panic-flatten, and per-symbol allow/block, saved as named **presets** (`Default` / `Survival` / `Finals` along the existing aggression axis) — written to a one-way control file the engine reads each tick.
- **Weigh in (per-position):** pin/lock a position, **override the AI's view** for a symbol (a deciding "third opinion" that can flip direction), or set a per-position take-profit, plus per-ticker objective notes.

The control channel is deliberately constrained: the panel is the *sole writer* of the control files; the engine only reads them and acknowledges one-shot commands; and **the kill-switch and risk floor run before — and are never gated by — any operator control** (a pinned position is still flattened by the kill-switch). A malformed control file can never crash the trading loop. Full reference: **[docs/CONTROL_PANEL.md](docs/CONTROL_PANEL.md)**.

---

## Technology stack

| Layer | Technology |
|---|---|
| **Reasoning / signal (live)** | Claude Opus 4.8 with adaptive extended thinking, via the Anthropic API |
| **Second opinion** | NVIDIA Nemotron via the OpenAI-compatible NIM endpoint (consensus gate, optional) |
| **Reasoning / signal (backtest)** | Doubleword tiered inference (batch/async) via the Pydantic AI Gateway, OpenAI-compatible |
| **Structured output** | Pydantic schema validation (no free-text parsing of trade instructions) |
| **Market data & execution** | MetaTrader 5 (live tick data, order placement, position management) |
| **Sizing & risk math** | NumPy (inverse-volatility weighting, concentration capping) |
| **Control panel** | FastAPI backend + Plotly.js frontend (opt-in, read-only by default) |
| **Language / runtime** | Python 3.13+ |

The codebase is cleanly separated into single-responsibility modules: `ai_signal` (the AI brain), `second_opinion` (the Nemotron consensus), `sizer` (confidence → size), `risk` (the safety cage), `engine` (the loop that orchestrates them), `decision_log` (the audit trail), `controls`/`overrides` (the gated operator channel), `state` (metrics & persistence), and `client` (the broker interface).

---

## Why this deserves the Technology Award

1. **It applies frontier LLMs to a hard, real-time decision problem** — not as chatbots, but as calibrated, schema-constrained reasoning components inside a live control loop, with **two independent models (Claude + NVIDIA Nemotron) merged by a disagreement-aware consensus**.
2. **It pairs that AI with classical quant discipline** instead of trusting it blindly. The architecture's central insight — *separate fallible judgment from deterministic, un-overridable risk control* — is exactly how you make AI safe to put in charge of money.
3. **Every design decision is traceable to the incentive it optimizes** (return rank vs. survival), and to evidence (backtests that killed the strategies that didn't work).
4. **It actually runs, live and autonomously** — and ships with an **opt-in control panel** that makes the dual-AI reasoning visible and lets an operator steer it, all without any path to weaken the risk floor.

In short: it's a working demonstration of how to combine the *adaptability of AI reasoning* with the *guarantees of engineered risk management* — which is, increasingly, the central question in deploying AI for high-stakes decisions.

---

## How to see it run

The intended entry point is the **interactive launcher** — double-click `run_live.bat`. It asks two questions and starts everything accordingly:

1. **Mode** — `[L]ive trading` or `[D]ry-run` (dry-run computes & logs intended trades but sends nothing).
2. **Panel** — `[W]ith panel` or `[N]o panel`. *With panel* opens the SIGNAL//LAYER dashboard at `http://127.0.0.1:8800` and enables operator steering; *No panel* runs the engine exactly as it would with no panel code at all.

Or invoke the engine directly:

```bash
# dry-run: computes and logs intended trades, sends nothing
python -m bot.engine

# live: actually places orders (requires MetaTrader 5 + ANTHROPIC_API_KEY)
python -m bot.engine --live
```

Each loop prints a one-line health summary (equity, leverage, margin, return, drawdown, Sharpe) and, every 15 minutes, the AI's fresh market read with per-instrument direction and confidence.
