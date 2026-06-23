# Quanthack — An AI-Reasoned Trading Engine

*Technology Award submission — project overview for judges*

---

## In one sentence

**Quanthack is an autonomous trading system that lets a frontier AI model (Claude Opus 4.8) read the market like a human analyst — across multiple time horizons — and then translates that judgment into mathematically-sized, risk-controlled trades that run live, hands-free.**

---

## The 30-second pitch

Most algorithmic trading bots are *rule machines*: "if the 50-day average crosses the 200-day average, buy." They break the moment the market does something the rules didn't anticipate.

Quanthack is different. It separates **judgment** from **execution**:

1. **Judgment** — A large language model is shown a compact, multi-timeframe snapshot of 14 instruments (forex, metals, crypto) and asked, like a trading desk analyst, for a directional view and a *calibrated confidence* on each. It reasons about cross-asset stories ("broad USD strength, risk-off") rather than blindly following one indicator.
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
   │  3. AI VIEWS  ─── refresh Claude's market read (every      │
   │       │            15 min, not every tick)                 │
   │       ▼                                                    │
   │  4. SIZE & TRADE ─ convert views → target positions →      │
   │       │            reconcile live positions toward them    │
   │       ▼                                                    │
   │  5. TRAIL STOPS ── ratchet stops into profit on winners    │
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

## Technology stack

| Layer | Technology |
|---|---|
| **Reasoning / signal (live)** | Claude Opus 4.8 with adaptive extended thinking, via the Anthropic API |
| **Reasoning / signal (backtest)** | Doubleword tiered inference (batch/async) via the Pydantic AI Gateway, OpenAI-compatible |
| **Structured output** | Pydantic schema validation (no free-text parsing of trade instructions) |
| **Market data & execution** | MetaTrader 5 (live tick data, order placement, position management) |
| **Sizing & risk math** | NumPy (inverse-volatility weighting, concentration capping) |
| **Language / runtime** | Python 3.14 |

The codebase is cleanly separated into single-responsibility modules: `ai_signal` (the AI brain), `sizer` (confidence → size), `risk` (the safety cage), `engine` (the loop that orchestrates them), `state` (metrics & persistence), and `client` (the broker interface).

---

## Why this deserves the Technology Award

1. **It applies a frontier LLM to a hard, real-time decision problem** — not as a chatbot, but as a calibrated, schema-constrained reasoning component inside a live control loop.
2. **It pairs that AI with classical quant discipline** instead of trusting it blindly. The architecture's central insight — *separate fallible judgment from deterministic, un-overridable risk control* — is exactly how you make AI safe to put in charge of money.
3. **Every design decision is traceable to the incentive it optimizes** (return rank vs. survival), and to evidence (backtests that killed the strategies that didn't work).
4. **It actually runs, live and autonomously**, reasoning across multiple timeframes and managing its own risk, right now.

In short: it's a working demonstration of how to combine the *adaptability of AI reasoning* with the *guarantees of engineered risk management* — which is, increasingly, the central question in deploying AI for high-stakes decisions.

---

## How to see it run

```bash
# dry-run: computes and logs intended trades, sends nothing
python -m bot.engine

# live: actually places orders (requires MetaTrader 5 + ANTHROPIC_API_KEY)
python -m bot.engine --live
```

Each loop prints a one-line health summary (equity, leverage, margin, return, drawdown, Sharpe) and, every 15 minutes, the AI's fresh market read with per-instrument direction and confidence.
