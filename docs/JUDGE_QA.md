# Judge Q&A — Quanthack / SIGNAL//LAYER

Anticipated questions and crisp, accurate answers. Grouped by theme. All claims map to
code referenced in the [README](../README.md) and [DECISION_FLOW.md](DECISION_FLOW.md).

---

## The pitch & innovation

**Q. What is it, in one sentence?**
An AI-native autonomous trading system where a frontier LLM (Claude Opus 4.8) *is* the strategy — reading the market across timeframes and emitting calibrated directional views — wrapped in a deterministic risk engine, cross-checked by a blind multi-model ensemble (NVIDIA Nemotron + DeepSeek-V4-Pro, live via the Pydantic AI Gateway), and supervised by a human through a live console, with a hard risk floor no model can override.

**Q. Why is this "AI-native" and not just an LLM bolted onto a trading bot?**
The LLM *is* the signal source, not a sentiment add-on. We built and backtested momentum, mean-reversion, stat-arb and order-flow strategies — none had a durable edge after costs — so the model's cross-asset reasoning became the *sole* generator of direction and conviction, flowing directly into capital allocation. Around it we built a true multi-model ensemble, AI self-grading, full decision auditability, and human-in-the-loop overrides. The whole system is organized around the AI being the decision-maker.

**Q. What's genuinely novel here?**
(1) A **blind multi-model ensemble** — Claude direction plus two blind voters (NVIDIA Nemotron + DeepSeek-V4-Pro, live via the Pydantic AI Gateway) that amplify conviction on agreement but can never set, flip, or veto direction. (2) The **"always recorded, influence-gated"** design — both voters are always queried, recorded, and scored, with per-source influence toggles (Nemotron's `second_opinion` default ON, DeepSeek's `ensemble_input` default OFF), so you can watch Claude vs Nemotron vs DeepSeek vs Combined diverge over time before trusting a model. (3) **Self-grading** via per-model directional hit-rates (Claude vs Nemotron vs DeepSeek), persisted and surfaced in the panel. (4) **Trailing-peak graduated drawdown de-risk** that protects rank instead of flatten-and-freeze. (5) A **walk-forward-gated** second strategy (scalper) that can only go live after clearing an out-of-sample backtest.

---

## The AI / LLM specifics

**Q. What exactly do you feed the models?**
Not raw market data — a distilled, token-cheap **multi-timeframe snapshot**, one line per instrument: returns over 1h/4h/1d/3d, trend up/dn on M15/H1/H4 (EMA cross), a momentum z-score, per-bar volatility, and price's position in its ~5-day range. Plus a shared system prompt stating the scoring rules and demanding calibrated, timeframe-agreement-weighted, cross-asset-coherent views. All three models get the *identical* prompt and snapshot. (Full spec: DECISION_FLOW.md appendix.)

**Q. What do the models return?**
Per instrument: `direction ∈ {long, short, flat}`, a `confidence ∈ [0,1]`, and a one-line rationale — plus a market-regime narrative. Claude is forced into a Pydantic-validated schema; Nemotron and DeepSeek return the same shape as strict JSON. A hallucinated/malformed reply can't validate and never reaches trade logic.

**Q. What is the "Market Read" and how is it derived?**
It's each model's one-line **regime narrative** — its top-down read of the macro/risk regime (e.g. *"Risk-off: broad crypto/metals selling, USD firm against EUR/CHF/JPY"*). It's not hand-written or a separate call: when we query a model with the multi-timeframe snapshot, its structured response carries a `regime` field *alongside* the per-instrument views (Claude via the Pydantic `MarketView.regime`; Nemotron/DeepSeek via the same JSON shape). All three models' regime strings are captured into the decision log, normalized to claude/nemotron/deepseek, and the panel's **"Market Read"** button shows them side by side — each in the model's color, or "unavailable" when that voter didn't run. So it's the models' own qualitative context for the *same* snapshot that produced the quantitative views — useful for the operator to sanity-check *why* the book is positioned the way it is, and to spot when the models narratively disagree.

**Q. How are the second opinions used — do they ever override Claude?**
No. Claude is **always authoritative on direction**. Two blind voters — NVIDIA Nemotron (OpenAI-compatible NIM) and DeepSeek-V4-Pro (live via the Pydantic AI Gateway) — rate the same snapshot *blind* (neither sees Claude's answer). On agreement they can only *raise* confidence (`combined = cc + (1−cc)·0.5·mean(agreeing voters' confidence)`); on disagreement Claude's confidence stands. Strictly additive — no key, or any failure → identical Claude-only behavior.

**Q. Why query the second opinions even when their influence is toggled off?**
So we can *measure* whether each adds value before trusting it. Both are always queried, recorded, and scored; per-source influence toggles only decide whether the *sizer acts* on the combined confidence — Nemotron via `second_opinion` (default ON), DeepSeek via a separate `ensemble_input` (default OFF). The panel plots Claude / Nemotron / DeepSeek / Combined over time, so the operator makes an evidence-based call.

**Q. How do you know the AI is actually any good / right?**
A directional hit-rate scorer (`model_scoring.py`) grades each model's non-flat calls (Claude vs Nemotron vs DeepSeek) against realized price moves, persisted to `state/model_scores.json` and surfaced in the panel's consensus header. Every decision is also logged append-only (all models' views, rationales, regimes, costs, orders) so performance is fully auditable after the fact.

**Q. How often does it call the LLM, and what does that cost?**
Every 15 minutes (or on a manual "Refresh"), ~14 instruments in one structured call (per model). Each Claude call is on the order of a few cents (logged live with token counts + USD). Backtesting — which needs hundreds of calls — runs on the cheaper DeepSeek-V4-Flash via the Pydantic AI Gateway (~98.7% cheaper), the same gateway that hosts the live DeepSeek-V4-Pro voter.

**Q. What if the model hallucinates or returns garbage?**
Output is schema-constrained (Pydantic). Invalid output raises and the engine degrades gracefully (a failing blind voter simply drops out, leaving Claude-only; skip-and-retry-with-backoff if Claude fails). Bad data can never become a trade.

---

## Architecture & execution

**Q. How is a single trade decided, start to finish?**
Risk-assessed first (kill-switch can flatten before strategy runs) → snapshot built → Claude + blind Nemotron + blind DeepSeek → consensus merge → sizer maps confidence×aggression to target lots → reconcile issues only the delta → a broker-side stop is guaranteed on every position → trailing stops ratchet → the decision is logged. (Full trace: DECISION_FLOW.md.)

**Q. Why separate "judgment" from "execution"?**
LLMs are great at reading context, bad at disciplined risk arithmetic and never to be trusted with hard limits. So the AI decides *what* and *how strongly*; deterministic code decides *how much* and enforces *what it must never do*. The risk layer runs first every tick and the AI cannot override it.

**Q. How are positions sized?**
One `aggression ∈ [0,1]` dial interpolates the entire posture: leverage (~2×→6×), concentration (`conf¹`→`conf²·⁵`), inverse-vol risk-parity→conviction-chasing, single-name cap (~30%→45%). Weak views (<0.15) are dropped. The sizer sits below the hard risk caps. Presets (Survival/Default/Finals) set the dial live.

**Q. What is "aggression," and how does a human change the risk posture (presets)?**
**Aggression** (`∈ [0,1]`, default 0.30) is the sizer's master dial — the bridge from the AI's *direction + confidence* to actual position size. One number interpolates the *whole* posture at once (`sizer.py:35-44`): gross leverage **~2×→6×**, concentration **`conf¹` (spread evenly) → `conf²·⁵` (pile into top-conviction)**, **inverse-vol risk-parity → pure conviction**, and the single-name cap **~30%→45%**. So low = small, diversified, smooth-equity (*survive elimination*); high = large, concentrated (*chase return-rank*). It only scales **size** — direction is always the AI's, sub-0.15 views are dropped, and even at 1.0 it stays inside the `risk.py` floor it cannot override. **Presets** are saved points on that dial bundled with the block/allow lists, mapped to competition phases — **Survival (0.0) / Default (0.30) / Finals (1.0)**. Applying one writes the dial + lists into the gated controls channel and bumps `apply_seq`, so the engine **re-sizes the entire book live on the next tick** (no restart); *Save* snapshots the current live posture as a new named preset. Net: a human steers *how hard* the bot pushes within the cage — never *what* protection it can weaken.

**Q. Can a human override the AI's direction on a specific instrument?**
Yes — through the console's human-input bar — and it's treated like any other view, not a backdoor. Two modes: **Apply (additive)** is a human *second opinion* that only *raises* confidence when it agrees with the current direction (never flips or vetoes, exactly like a Nemotron/DeepSeek voter); **Override (deciding)** *forces* direction + confidence (defaults to 1.0) and can flip the AI, with the superseded models struck through in the readout. Either way the human view is **sized and risk-capped exactly like an AI view**: it flows through the same `build_targets` sizer at the active aggression and under the same `risk.py` floor, with a guaranteed broker stop — it sets *conviction/direction*, never size, leverage, or the safety floor. (`apply_human_input` / `apply_human_override`, `bot/second_opinion.py`.)

**Q. You're on MetaTrader 5 — any execution gotchas you hit?**
Yes. This broker **rejects market orders carrying an inline stop-loss** (retcode 10006), so we open *clean* and attach the SL the same tick via a separate SLTP action, with every stop price **clamped to the broker's minimum stop distance** so it isn't rejected for being too tight (10016). We retry only *transient* retcodes (requote/prices-changed/no-quotes), never partial-fill/no-money/autotrading-off.

---

## Risk & safety

**Q. The rule is "liquidation = elimination." How do you guarantee you don't blow up?**
Layered: an **account-wide kill-switch flattens everything at 150% margin level** (the competition stop-out is 30%, so it fires far above danger); hard caps on leverage (12×) and margin usage (60%); **a guaranteed broker-side stop on every position within one tick of opening**; trailing stops; and a graduated drawdown de-risk. Even at max aggression, a forced liquidation can't happen before our own kill-switch fires.

**Q. What's the drawdown logic?**
Drawdown is measured against the *trailing equity peak* (the scored metric). Instead of flatten-and-freeze (which forfeits return rank), we scale the whole book down in steps — 20%→½, 30%→¼, 40%→flat — and **re-arm automatically** as equity recovers.

**Q. How does the system take profit?**
Deliberately **not** with fixed price targets — the LLM outputs *direction + conviction*, not price targets, so profit-taking is conviction- and trail-driven, not target-driven. Three layers: (1) **Trailing stops are the primary profit-locker** — once a winner is up by the arm distance (FX 1% / metals 2% / crypto 4%) a stop activates and ratchets behind price at a *tighter* trail (FX 0.5% / metals 1% / crypto 2%), so arming immediately locks real profit and the stop only moves in your favor (`_update_trailing_stops`, `engine.py`; `TRAIL_ACTIVATE_PCT`/`TRAIL_PCT`, `config.py:24-28`). A pullback to the trail realizes the gain. (2) **The 15-min AI re-evaluation is the thesis-based exit** — when a refresh weakens, flattens, or flips a view, the sizer issues new targets and reconcile trims/closes/reverses the position, banking P&L when the setup is done rather than at an arbitrary number. (3) **Graduated drawdown de-risk** scales the whole book down off the equity peak, protecting gains at the portfolio level. On top of that, the operator can set an **explicit take-profit** (a draggable TP line / Take Profit button) which the engine applies at the broker — but that's a human tool, not the default behavior.

**Q. Don't concentration limits help? Why don't you hard-block on them?**
We learned this the hard way: a hard new-risk gate on concentration/net-direction *froze the book* in a one-sided regime and even blocked diversifying trades. We now **compute** concentration/net for display and an 85% watch clock (contest penalty at 90% for ≥30 min), but don't use them to block trades. Real protection is the kill-switch, leverage/margin caps, drawdown de-risk, and per-position stops.

**Q. Can the human or the AI disable the safety?**
No. The risk floor runs *before* strategy and operator controls every tick and is never gated by any of them. The panel physically cannot place an order or weaken the floor.

**Q. What can the operator actually change from the console, and how is it gated?**
The console is **read-only over the broker** — it can never place or modify an order directly. Every action is a *gated write* to a controls file the engine reads each tick, then applied through the sizer + risk floor: switch **preset / aggression**; force or boost a **direction** (human input); **block** symbols (clamp toward flat) or set an **allow**-list (everything else clamped flat); toggle each **second opinion's influence** on sizing (Nemotron `second_opinion` default on, DeepSeek `ensemble_input` default off); **assign up to 2 symbols to the scalper** (ceded from the engine, and only live after clearing a walk-forward backtest gate); **flatten now**; or **force an AI refresh** off-cadence. None of it can weaken the floor or exceed the caps — the human steers *posture and conviction*, the engine still owns *execution and safety* (`bot/controls.py`).

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
Python 3.13; Claude Opus 4.8 (`claude-opus-4-8`, Anthropic SDK, structured output); NVIDIA Nemotron (`nvidia/nemotron-3-super-120b-a12b`, OpenAI-compatible NIM); DeepSeek-V4-Pro (`deepseek-ai/DeepSeek-V4-Pro`) as a LIVE blind ensemble voter via the Pydantic AI Gateway → Doubleword (`/responses` endpoint), with the same gateway also running cheap backtesting on DeepSeek-V4-Flash; MetaTrader 5; FastAPI + Uvicorn; Plotly.js; Pydantic v2; NumPy/pandas/PyArrow; httpx. 50+ script-style tests.

**Q. How did you validate it?**
Unit tests across engine/risk/sizer/consensus/scalper (pure-logic modules are network-free). The AI signal is backtested over historical bars via the gateway. The honest finding: the *architecture* is survivable (drawdown capped, no liquidation on real data), and the cheap *backtest* model showed no standalone edge — which is why the live edge rests on the frontier model + consensus, not on the backtest model.

**Q. How does this compare to a classical stat-arb / cointegration approach — and where's your edge?**
Different bet, on purpose. A classical entry trades a *statistical* relationship (cointegrated pairs, conformal bands, Kalman hedge ratios) and keeps the LLM as an advisory *narrator*. We do the opposite: the **LLM is the decision-maker**, and the contribution is the architecture that makes that safe and accountable — a **blind three-model ensemble** that cross-checks every call, **self-grading by asset category over time**, and a **hard risk floor the AI can't breach**. On edge specifically, four honest points: (1) the **live** signal is the frontier model (Claude Opus 4.8), not the cheap backtest proxy (DeepSeek-Flash) we measured at ~coin-flip — we *tested* that gap rather than hiding it; (2) edge is **visible and falsifiable** — the Model Accuracy page shows a measurable lift on metals (~54%) while efficient FX sits near coin-flip, so trust is evidence-based; (3) we **did the classical work too** — momentum, mean-reversion, stat-arb and order-flow were built and backtested (no durable edge after costs), plus a controlled longer-horizon-context A/B — so the LLM-as-strategy is a *reasoned* choice; (4) whatever the signal's edge, the **risk cage makes blow-up structurally impossible**, protecting the drawdown/Sharpe/discipline 30% of the score. Net: theirs is a proven-narrow statistical alpha; ours is a transparent, self-auditing framework for putting a frontier model responsibly in the driver's seat — the AI-native answer to the same problem.

**Q. Biggest limitation / what would you do with more time?**
The backtest runs on a cheaper proxy model, so it validates the architecture, not live Claude performance. Next: wire the self-grading hit-rates into a live trust signal that auto-adjusts the consensus gain, and broaden the gated-scalper universe. Also, crypto backtest history is short, so the gate is conservative.

**Q. Is it secure? Any secrets exposed?**
Keys live in a gitignored `.env`, never committed. The dashboard is read-only over logs/state and live MT5; it cannot place orders. Capital is simulated (sandbox contest), no real-world risk.

**Q. Could this generalize beyond this contest?**
Yes — the judgment/execution split, schema-constrained multi-model consensus, self-grading, and the deterministic risk cage are broker- and asset-agnostic patterns. Swapping MT5 or the instrument universe is localized to the client/config.

**Q. What's the single most impressive thing to remember?**
Frontier-LLM judgment driving real (simulated) capital, **cross-checked by a blind multi-model ensemble (Nemotron + DeepSeek) and graded by the system itself**, inside a risk cage that makes blow-up structurally impossible — with every decision fully auditable.
