# Judge Q&A — Quanthack / SIGNAL//LAYER

Anticipated questions and crisp, accurate answers. Grouped by theme. All claims map to
code referenced in the [README](../README.md) and [DECISION_FLOW.md](DECISION_FLOW.md).

---

## The pitch & innovation

**Q. What is it, in one sentence?**
An AI-native autonomous trading system where a frontier LLM (Claude Opus 4.8) *is* the strategy — reading the market across timeframes and emitting calibrated directional views — wrapped in a deterministic risk engine, cross-checked by a blind second AI (NVIDIA Nemotron), and supervised by a human through a live console, with a hard risk floor no model can override.

**Q. Why is this "AI-native" and not just an LLM bolted onto a trading bot?**
The LLM *is* the signal source, not a sentiment add-on. We built and backtested momentum, mean-reversion, stat-arb and order-flow strategies — none had a durable edge after costs — so the model's cross-asset reasoning became the *sole* generator of direction and conviction, flowing directly into capital allocation. Around it we built a true multi-model ensemble, AI self-grading, full decision auditability, and human-in-the-loop overrides. The whole system is organized around the AI being the decision-maker.

**Q. What's genuinely novel here?**
(1) A **blind multi-model ensemble** that amplifies conviction on agreement but never lets the second model veto. (2) The **"always recorded, influence-gated"** design — you can watch Claude vs Nemotron vs Combined diverge over time before trusting the second model. (3) **Self-grading** via per-model directional hit-rates. (4) **Trailing-peak graduated drawdown de-risk** that protects rank instead of flatten-and-freeze. (5) A **walk-forward-gated** second strategy (scalper) that can only go live after clearing an out-of-sample backtest.

---

## The AI / LLM specifics

**Q. What exactly do you feed the models?**
Not raw market data — a distilled, token-cheap **multi-timeframe snapshot**, one line per instrument: returns over 1h/4h/1d/3d, trend up/dn on M15/H1/H4 (EMA cross), a momentum z-score, per-bar volatility, and price's position in its ~5-day range. Plus a shared system prompt stating the scoring rules and demanding calibrated, timeframe-agreement-weighted, cross-asset-coherent views. Both models get the *identical* prompt and snapshot. (Full spec: DECISION_FLOW.md appendix.)

**Q. What do the models return?**
Per instrument: `direction ∈ {long, short, flat}`, a `confidence ∈ [0,1]`, and a one-line rationale — plus a market-regime narrative. Claude is forced into a Pydantic-validated schema; Nemotron returns the same shape as strict JSON. A hallucinated/malformed reply can't validate and never reaches trade logic.

**Q. How is Nemotron used — does it ever override Claude?**
No. Claude is authoritative on direction. Nemotron rates the same snapshot *blind* (it never sees Claude's answer). On agreement it *raises* confidence (`combined = c + (1−c)·0.5·n`); on disagreement Claude stands. It's strictly additive — no NVIDIA key, or any failure → identical Claude-only behavior.

**Q. Why query Nemotron even when its influence is toggled off?**
So we can *measure* whether it adds value before trusting it. It's always queried and recorded; the toggle only decides whether the *sizer acts* on the combined confidence. The panel plots Claude / Nemotron / Combined over time, so the operator makes an evidence-based call.

**Q. How do you know the AI is actually any good / right?**
A directional hit-rate scorer (`model_scoring.py`) grades each model's non-flat calls against realized price moves, per model. Every decision is also logged append-only (both models' views, rationales, regimes, costs, orders) so performance is fully auditable after the fact.

**Q. How often does it call the LLM, and what does that cost?**
Every 15 minutes (or on a manual "Refresh"), ~14 instruments in one structured call. Each Claude call is on the order of a few cents (logged live with token counts + USD). Backtesting — which needs hundreds of calls — runs on a cheaper model via the Pydantic AI Gateway (~98.7% cheaper).

**Q. What if the model hallucinates or returns garbage?**
Output is schema-constrained (Pydantic). Invalid output raises and the engine degrades gracefully (Claude-only if Nemotron fails; skip-and-retry-with-backoff if Claude fails). Bad data can never become a trade.

---

## Architecture & execution

**Q. How is a single trade decided, start to finish?**
Risk-assessed first (kill-switch can flatten before strategy runs) → snapshot built → Claude + blind Nemotron → consensus merge → sizer maps confidence×aggression to target lots → reconcile issues only the delta → a broker-side stop is guaranteed on every position → trailing stops ratchet → the decision is logged. (Full trace: DECISION_FLOW.md.)

**Q. Why separate "judgment" from "execution"?**
LLMs are great at reading context, bad at disciplined risk arithmetic and never to be trusted with hard limits. So the AI decides *what* and *how strongly*; deterministic code decides *how much* and enforces *what it must never do*. The risk layer runs first every tick and the AI cannot override it.

**Q. How are positions sized?**
One `aggression ∈ [0,1]` dial interpolates the entire posture: leverage (~2×→6×), concentration (`conf¹`→`conf²·⁵`), inverse-vol risk-parity→conviction-chasing, single-name cap (~30%→45%). Weak views (<0.15) are dropped. The sizer sits below the hard risk caps. Presets (Survival/Default/Finals) set the dial live.

**Q. You're on MetaTrader 5 — any execution gotchas you hit?**
Yes. This broker **rejects market orders carrying an inline stop-loss** (retcode 10006), so we open *clean* and attach the SL the same tick via a separate SLTP action, with every stop price **clamped to the broker's minimum stop distance** so it isn't rejected for being too tight (10016). We retry only *transient* retcodes (requote/prices-changed/no-quotes), never partial-fill/no-money/autotrading-off.

---

## Risk & safety

**Q. The rule is "liquidation = elimination." How do you guarantee you don't blow up?**
Layered: an **account-wide kill-switch flattens everything at 150% margin level** (the competition stop-out is 30%, so it fires far above danger); hard caps on leverage (12×) and margin usage (60%); **a guaranteed broker-side stop on every position within one tick of opening**; trailing stops; and a graduated drawdown de-risk. Even at max aggression, a forced liquidation can't happen before our own kill-switch fires.

**Q. What's the drawdown logic?**
Drawdown is measured against the *trailing equity peak* (the scored metric). Instead of flatten-and-freeze (which forfeits return rank), we scale the whole book down in steps — 20%→½, 30%→¼, 40%→flat — and **re-arm automatically** as equity recovers.

**Q. Don't concentration limits help? Why don't you hard-block on them?**
We learned this the hard way: a hard new-risk gate on concentration/net-direction *froze the book* in a one-sided regime and even blocked diversifying trades. We now **compute** concentration/net for display and a 90%/30-min watch clock, but don't use them to block trades. Real protection is the kill-switch, leverage/margin caps, drawdown de-risk, and per-position stops.

**Q. Can the human or the AI disable the safety?**
No. The risk floor runs *before* strategy and operator controls every tick and is never gated by any of them. The panel physically cannot place an order or weaken the floor.

---

## The scalper

**Q. What's the second strategy and why add it?**
An integrated crypto fast-momentum scalper that runs *alongside* the main engine on up to 2 operator-assigned symbols (ceded from the engine). Crypto's minute-scale moves dwarf the spread, so it trades *with* breakouts (alts confirmed by a BTC lead-lag).

**Q. How do you stop it from trading garbage?**
**Gated arming via a walk-forward backtest**: assigning a symbol sweeps params on a train split, locks the best, and evaluates *out-of-sample*; a symbol can be armed only if it clears the gate (or is explicitly force-armed). Plus anti-churn (cooldown, min-hold, max-trades/hour) and broker-side fast exits.

**Q. Can the scalper interfere with the main engine?**
No. It's hosted *inside* the engine (own magic, no order races on the netting account), arming is per-symbol, ceded symbols are flattened and handed over cleanly, the account-wide kill flattens it too, and the whole step is wrapped so an error there can never crash the engine. On restart its armed state resets so nothing auto-fires.

---

## Tech, validation, limits

**Q. What's the stack?**
Python 3.13; Claude Opus 4.8 (Anthropic SDK, structured output); NVIDIA Nemotron (OpenAI-compatible NIM); Doubleword/DeepSeek-V4-Flash via the Pydantic AI Gateway (cheap backtesting); MetaTrader 5; FastAPI + Uvicorn; Plotly.js; Pydantic v2; NumPy/pandas/PyArrow; httpx. 50+ script-style tests.

**Q. How did you validate it?**
Unit tests across engine/risk/sizer/consensus/scalper (pure-logic modules are network-free). The AI signal is backtested over historical bars via the gateway. The honest finding: the *architecture* is survivable (drawdown capped, no liquidation on real data), and the cheap *backtest* model showed no standalone edge — which is why the live edge rests on the frontier model + consensus, not on the backtest model.

**Q. Biggest limitation / what would you do with more time?**
The backtest runs on a cheaper proxy model, so it validates the architecture, not live Claude performance. Next: wire the self-grading hit-rates into a live trust signal that auto-adjusts the consensus gain, and broaden the gated-scalper universe. Also, crypto backtest history is short, so the gate is conservative.

**Q. Is it secure? Any secrets exposed?**
Keys live in a gitignored `.env`, never committed. The dashboard is read-only over logs/state and live MT5; it cannot place orders. Capital is simulated (sandbox contest), no real-world risk.

**Q. Could this generalize beyond this contest?**
Yes — the judgment/execution split, schema-constrained multi-model consensus, self-grading, and the deterministic risk cage are broker- and asset-agnostic patterns. Swapping MT5 or the instrument universe is localized to the client/config.

**Q. What's the single most impressive thing to remember?**
Frontier-LLM judgment driving real (simulated) capital, **cross-checked by a second AI and graded by the system itself**, inside a risk cage that makes blow-up structurally impossible — with every decision fully auditable.
