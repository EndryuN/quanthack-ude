# Presentation-Generation Prompt

Paste the block below into an AI slide generator (Gamma, Beautiful.ai, Tome, or
Claude/GPT "make me a deck"). It is self-contained — all facts the generator needs
are included.

---

You are an expert pitch-deck designer. Generate a **12-slide presentation** for a **hackathon/competition submission** of an AI trading system called **Quanthack — SIGNAL//LAYER**. Audience: **technical judges** evaluating innovation, AI-nativeness, and engineering quality. Tone: **confident, precise, technical-but-clear**. Visual style: **dark, modern, "fintech terminal"** — near-black background, a single chartreuse/lime accent, teal + violet as secondary (for the two AI models), monospace for data, generous whitespace, minimal text per slide (headline + 3-5 tight bullets + one diagram/visual idea per slide). No clip-art; prefer clean diagrams and metrics.

Use ONLY these facts (don't invent numbers):

**One-liner:** An AI-native autonomous trading system where a frontier LLM *is* the strategy — wrapped in a deterministic risk engine, cross-checked by a blind second AI, supervised by a human, with a hard risk floor no model can override.

**Context:** multi-day simulated trading competition; forex, metals, crypto; $1,000,000 capital; 1:30 leverage; liquidation = instant elimination; scoring ≈ 70% return rank + 15% drawdown + 10% Sharpe + 5% risk discipline.

**Why AI-native:** classical strategies (momentum, mean-reversion, stat-arb, order-flow) were built and backtested — none had a durable edge after costs — so the LLM became the *sole* signal source. The AI decides direction + calibrated conviction; deterministic code does sizing, risk, and execution.

**What we feed the LLMs:** a distilled multi-timeframe snapshot per instrument (returns 1h/4h/1d/3d; trend up/dn on M15/H1/H4; momentum z-score; volatility; 5-day range position) — NOT raw candles. Both models get the identical prompt + snapshot.

**Dual-AI consensus:** Claude Opus 4.8 is primary (schema-validated direction + confidence + rationale + regime). NVIDIA Nemotron rates the same snapshot *blind* (never sees Claude's answer). Agreement boosts confidence (`combined = c + (1−c)·0.5·n`); it can NEVER veto or flip direction. It's always queried + recorded even when its influence is off, so you can watch Claude/Nemotron/Combined diverge over time before trusting it. Strictly additive — no key → Claude-only.

**Sizing:** one `aggression` dial interpolates the whole posture — leverage ~2×→6×, concentration conf¹→conf²·⁵, inverse-vol risk-parity → conviction-chasing.

**Risk (blow-up is structurally impossible):** kill-switch flattens all at 150% margin level (contest stop-out is 30%); leverage cap 12×; margin cap 60%; trailing-peak graduated drawdown de-risk (20%→½, 30%→¼, 40%→flat, auto re-arms); a guaranteed broker-side stop on every position within one tick. Risk runs FIRST every tick; AI/human can't override it.

**Execution reality:** broker rejects inline stop-loss (retcode 10006) → open clean, attach SL via separate action, clamped to broker min-distance; retry only transient errors.

**Integrated scalper:** crypto fast-momentum (alts confirmed by BTC lead-lag) running alongside the engine on ≤2 operator-assigned symbols; **gated arming via a walk-forward (train/test) backtest** — only goes live if it clears out-of-sample; per-symbol arming; anti-churn; fully isolated so it can never crash the engine.

**Self-grading & auditability:** per-model directional hit-rate scoring; every 15-min decision persisted append-only (both models' views, rationales, regimes, token costs, orders) as JSONL + Markdown.

**Operator console (SIGNAL//LAYER):** live dashboard — equity vs risk floor, consensus heatmap (Claude/Nemotron/Combined layers), per-symbol AI projection cones, risk gauges + tripwire clocks; steering — aggression/presets, pause, panic-flatten, manual consensus refresh, scalper assign→gate→arm, per-position human overrides. Read-only by default; cannot weaken the risk floor.

**Tech stack:** Python 3.13 · Claude Opus 4.8 · NVIDIA Nemotron · Doubleword/DeepSeek-V4-Flash via Pydantic AI Gateway (backtesting ~98.7% cheaper) · MetaTrader 5 · FastAPI + Uvicorn · Plotly.js · Pydantic v2 · NumPy/pandas/PyArrow · httpx · 50+ tests.

**Honest validation:** architecture is survivable on real data (drawdown capped, no liquidation); the *cheap backtest model* showed no standalone edge — the live edge rests on the frontier model + consensus.

**Slide order:** (1) Title + one-liner. (2) The problem & the contest's brutal constraint (liquidation = elimination). (3) Insight: classical strategies failed → make the LLM the strategy (why AI-native). (4) Architecture diagram (judgment vs execution; two processes). (5) How a decision is made (the pipeline, start to finish). (6) What we feed the LLMs (the snapshot). (7) Dual-AI blind consensus (the differentiator). (8) Risk cage — why blow-up is impossible. (9) The gated scalper. (10) Self-grading + auditability + operator console. (11) Tech stack + honest validation. (12) Closing: the closed loop "AI decides → second AI cross-checks → human supervises → system grades itself" + impact.

For each slide output: a short title, 3-5 bullets, and a one-line "visual:" suggestion (diagram/chart/mockup). Keep it tight and judge-ready.
