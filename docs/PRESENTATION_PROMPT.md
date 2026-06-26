# Presentation-Generation Prompt

Paste the block below into an AI slide generator (Gamma, Beautiful.ai, Tome, or
Claude/GPT "make me a deck"). It is self-contained — every fact the generator
needs is included; tell it not to invent numbers.

---

You are an expert pitch-deck designer. Generate a **14-slide presentation** for a **hackathon/competition submission** of an AI trading system called **Quanthack — SIGNAL//LAYER**. Audience: **technical judges** evaluating innovation, AI-nativeness, and engineering quality. Tone: **confident, precise, technical-but-clear**. Visual style: **dark, modern, "fintech terminal"** — near-black background, a single chartreuse/lime accent; **brand colors for the three models: Claude = orange, NVIDIA Nemotron = green, DeepSeek = blue**. Monospace for data/numbers, generous whitespace, minimal text per slide (headline + 3–6 tight bullets + one diagram/visual idea per slide). No clip-art; prefer clean diagrams, timelines, and metrics. Use ONLY these facts (don't invent numbers):

**One-liner:** An AI-native autonomous trading system where a frontier LLM *is* the strategy — wrapped in a deterministic risk engine, cross-checked by a blind multi-model ensemble, supervised by a human, with a hard risk floor no model can override.

**Context:** multi-day simulated trading competition; ~14 instruments across forex, metals, crypto; $1,000,000 capital; 1:30 leverage; **liquidation = instant elimination**; scoring ≈ 70% return rank + 15% drawdown + 10% Sharpe + 5% risk discipline.

**Why AI-native:** classical strategies (momentum, mean-reversion, stat-arb, order-flow) were built and backtested — none had a durable edge after costs — so the LLM became the *sole* signal source. The AI decides **direction + calibrated conviction**; deterministic code does sizing, risk, and execution.

**WHAT WE FEED THE LLMs (one slide):** NOT raw candles — a distilled, token-cheap **multi-timeframe snapshot**, one line per instrument. Per symbol: last price; returns over **1h / 4h / 1d / 3d**; trend up/down on **M15 / H1 / H4** (EMA cross); a **momentum z-score**; **per-bar volatility**; and **price's position in its ~5-day range** (0–100%). Example line: `EURUSD: px=1.084 | ret 1h=+0.03 4h=-0.12 1d=+0.21 3d=+0.55% | trend M15=up H1=up H4=dn | mom_z=+0.8 vol/bar=0.04% | 5d-range=62%`. Plus a **shared system prompt** that states the scoring rules and demands calibrated, timeframe-agreement-weighted, cross-asset-coherent views. **All three models get the identical prompt + snapshot.** They return, per instrument: `direction (long/short/flat)`, `confidence ∈ [0,1]`, a one-line rationale — plus a one-line market-**regime** narrative ("Market Read"). Output is Pydantic-schema-validated, so a malformed/hallucinated reply can never reach trade logic. We do NOT feed: raw ticks, order book, account/position state, news, or one model's answer to another.

**THE TWO CADENCES (one slide — important):** the system runs on two clocks:
- **Every ~5 seconds (the tick loop):** assess risk first (kill-switch can flatten before strategy is consulted) → reconcile positions toward targets ONLY when the signal changed → guarantee a broker-side stop on every open position → ratchet trailing stops → run the integrated scalper last (isolated). Risk + execution are continuous.
- **Every 15 minutes (the AI refresh):** re-query all three models on a fresh snapshot, merge the consensus, and form new target views. (The market's multi-timeframe story doesn't change every 5s, and LLM calls cost money — so opinions refresh slowly while risk/execution stay live.) The operator can also force an **immediate refresh** ("Refresh views"), and the panel itself live-updates **~every 1 second** (price, P/L, positions). On AI-call failure it backs off 30s → doubling → 300s.

**HOW A DECISION IS MADE, START TO FINISH (one slide — the pipeline):**
1. **Risk first** — aggregate all positions; if margin level < 150% → flatten everything and stop (nothing downstream runs).
2. **Snapshot** — build the multi-timeframe context for the live universe.
3. **Claude (primary)** — returns direction + calibrated confidence + rationale + regime; always authoritative on direction.
4. **Two blind voters** — NVIDIA Nemotron and DeepSeek-V4-Pro (live via the Pydantic AI Gateway) rate the *same* snapshot without seeing Claude's answer.
5. **Consensus merge** — agreeing voters only *raise* confidence: `combined = cc + (1−cc)·0.5·mean(agreeing voters)`; they can NEVER set, flip, or veto direction. Per-source influence toggles (Nemotron default ON, DeepSeek default OFF), but both are always queried, recorded, and scored.
6. **Size** — one `aggression` dial maps confidence → target lots (leverage ~2×→6×, concentration conf¹→conf²·⁵, risk-parity → conviction-chasing). Sits below the hard risk caps.
7. **Reconcile** — issue only the delta vs live positions (deadband to avoid churn); open clean then attach the broker stop.
8. **Log** — persist the full decision (all models' views, rationales, regimes, costs, orders, account state) as JSONL + Markdown.

**Multi-model consensus / self-grading:** Claude + two blind voters (Nemotron, DeepSeek-V4-Pro live via the Pydantic AI Gateway → Doubleword). Strictly additive, gracefully degrading (a failing voter just drops out → Claude-only). The system **grades its own AI**: per-model **directional hit-rates** (how often each model's long/short call was right) accumulate across refreshes and show in the panel — so trust is earned from results, not assumed.

**Risk (blow-up is structurally impossible):** kill-switch flattens all at 150% margin level (contest stop-out is 30%); leverage cap 12×; margin cap 60%; **trailing-peak graduated drawdown de-risk** (20%→½, 30%→¼, 40%→flat, auto re-arms); a **guaranteed broker-side stop on every position within one tick**. Risk runs FIRST every tick; AI/human can't override it. Execution reality: this broker rejects inline stop-losses (retcode 10006), so we open clean and attach the stop via a separate action, clamped to the broker's min stop distance.

**Integrated scalper:** crypto fast-momentum (alts confirmed by a BTC lead-lag) running alongside the engine on ≤2 operator-assigned symbols; **gated arming via a walk-forward (train/test) backtest** — only goes live if it clears out-of-sample; per-symbol arming; anti-churn; fully isolated so it can never crash the engine.

**Operator console (SIGNAL//LAYER):** live dashboard — equity vs risk floor, currency-strength ribbon with a model selector, consensus dumbbell (3 colored dots/row; overlaps split into half-circles) + per-model accuracy, over-time heatmap (Claude/Nemotron/DeepSeek/Combined layers), a **"Market Read"** button showing all three models' regime narratives, per-symbol projection direction lines, the open position drawn on the chart with a **draggable take-profit flag**, candle/line + 1m/15m/1h toggles; steering — aggression/presets, pause, panic-flatten, manual consensus refresh, scalper assign→gate→arm, per-position human overrides. Read-only by default; cannot weaken the risk floor.

**Tech stack:** Python 3.13 · Claude Opus 4.8 · NVIDIA Nemotron · **DeepSeek-V4-Pro live via the Pydantic AI Gateway → Doubleword** (the same gateway powers ~98.7%-cheaper backtesting on DeepSeek-V4-Flash) · MetaTrader 5 · FastAPI + Uvicorn · Plotly.js · Pydantic v2 · NumPy/pandas/PyArrow · httpx.

**Honest validation:** architecture is survivable on real data (drawdown capped, no liquidation); the cheap backtest model showed no standalone edge — the live edge rests on the frontier model + ensemble.

**Slide order:** (1) Title + one-liner. (2) The problem & the brutal constraint (liquidation = elimination; the scoring weights). (3) Insight: classical strategies failed → make the LLM the strategy (why AI-native). (4) Architecture diagram (judgment vs execution; engine + panel). (5) **What we feed the LLMs** (the snapshot + example line + the shared prompt). (6) What the models return (direction/confidence/rationale + the Market Read). (7) **The two cadences** (5-second tick loop vs 15-minute AI refresh + manual refresh + 1s panel) — a timeline visual. (8) **How a decision is made, start to finish** (the 8-step pipeline). (9) Blind multi-model consensus + self-grading (Claude + Nemotron + DeepSeek; the boost formula; hit-rates). (10) Risk cage — why blow-up is impossible. (11) The gated scalper. (12) Operator console (screenshot-style mockup). (13) Tech stack + honest validation. (14) Closing: the closed loop "AI decides → blind ensemble cross-checks → human supervises → system grades itself" + impact.

For each slide output: a short title, 3–6 bullets, and a one-line "visual:" suggestion (diagram/chart/timeline/mockup). Keep it tight and judge-ready.
