# AI Engine Improvements — Audit & Prioritized Backlog

**Date:** 2026-06-25
**Status:** Audit complete — prioritized backlog for review
**Scope:** Improve the primary AI trading engine (NOT the scalper, which has its own spec). Derived from a five-agent parallel audit covering signal/consensus, sizing/allocation, risk/rule-alignment, execution/reconciliation, and cost/cadence/robustness.

---

## 1. Method

Five read-only investigations, each on one engine subsystem, with `file:line` evidence and a bug-vs-enhancement classification. This document consolidates their findings into a single prioritized backlog so the fixes can be planned and implemented deliberately.

## 2. Cross-cutting themes

1. **"Declared but not enforced" guardrails.** Several risk limits the operator believes are active do nothing — they are computed and displayed but never gate a decision.
2. **Asymmetric second opinion.** Nemotron can only *raise* confidence (→ bigger size) on agreement; disagreement is ignored. It strictly increases exposure and never corrects a bad call. This is the most likely cause of the observed "dramatic change for worse after introducing Nemotron."
3. **Execution not robust to failure.** Transient order failures, failed closes, and partial fills are not handled; intent and book can diverge for up to a full 15-minute refresh cycle.
4. **The risk floor goes blind during AI calls.** The synchronous 15-min refresh stalls the kill-switch loop for tens of seconds.
5. **Risk is binary where it should be graduated.** Drawdown breach = flatten-and-freeze-for-the-round; tripwire proximity = warn-only. Both are misaligned with a return-weighted competition score.
6. **Sizing ignores correlation and volatility.** The book can become one concentrated macro bet at a fixed gross leverage regardless of regime.
7. **Observability gaps.** No per-symbol realized-PnL logging, Nemotron cost hardcoded to `$0.00`, no running cost total — so we cannot *measure* whether changes (e.g. Nemotron) help.

---

## 3. Prioritized backlog

Severity: **BUG** = incorrect/contrary-to-intent behavior; **ENH** = improvement. Effort: S/M/L.

### Tier 0 — correctness & safety, mostly small fixes (do first)

| ID | Title | Type | Effort | Evidence | Fix |
|----|-------|------|--------|----------|-----|
| E0.1 | `max_net_directional_frac` & `max_single_instrument_frac` never enforced | BUG | S | `config.py:64-65` defined; `risk.py:71-76` computes the values but `allow_new_risk` (`risk.py:95-103`) never checks them; a hardcoded `0.85` print sits at `risk.py:113` | Add net-directional and single-instrument checks to `allow_new_risk`, driven by the config fields (remove the magic numbers). |
| E0.2 | `target_gross_leverage` never read by sizer | BUG | S | `config.py:58` referenced only by panel/monitor display; sizer uses its own `SizerConfig.max_leverage` | Have the sizer aim at `target_gross_leverage` and clamp to the cap. |
| E0.3 | 30-min tripwire timers are warn-only | BUG | M | `risk.py:105-115` `_track()` only `reasons.append("watch:")`; README claims it "warns **and acts**" | Wire duration into a graduated active de-risk that runs independent of the AI-refresh gate (e.g. force reduction at ≥20 min over a competition line). |
| E0.4 | Drawdown breaker measures vs initial equity, not trailing peak | BUG | S-M | `risk.py:89` `(INITIAL_EQUITY-equity)/INITIAL_EQUITY`; `state.py:67-71` already tracks trailing-peak DD (the scored metric) and is ignored | Feed `StateTracker.cur_drawdown` (peak-relative) into the risk decision. |
| E0.5 | Drawdown kill = flatten-all then frozen for the round | BUG | M | `risk.py:88-92,125-132`; after flatten, `dd>=0.20` stays true → `kill` forever → `engine.py:100-104` returns early permanently | Convert DD breach to graduated de-risk (cut gross 50%→25%) with a re-arm/daily reset; keep flatten-all only for the margin-level/liquidation path. |
| E0.6 | No retry on transient order failures; tick marked "acted" anyway | BUG | M | `client.py:258-260,321-322` send once; `engine.py:451-453,462-463` don't react; `engine.py:157` advances `_last_acted_ts` unconditionally → not retried for up to 15 min (`engine.py:178-181`) | Bounded retry on transient retcodes (requote/price-changed/no-quotes) with a re-quoted price; only advance `_last_acted_ts` when all orders succeeded. |
| E0.7 | Failed `close` recorded as closed | BUG | S | `engine.py:320-331` appends the close order regardless of `_do_close` success; flows into the log and `_report_stop_hits` (`engine.py:174,425-426`) | Thread the `ok` result back; only record/flag closed on success. |
| E0.8 | Partial fills treated as full | BUG | S-M | IOC preferred (`client.py:217-221`); `ok` only checks retcode (`client.py:259`); `res.volume` never compared; logged as fully filled (`engine.py:334-336`) | Read `res.volume`; record actual fill; re-enter reconcile for the residual. |
| E0.9 | Positions can be left with no broker stop, silently | BUG | S | `client.py:230-231` returns `sl=0.0` on no-tick; `engine.py:445-451` sends order with no SL, no warning | After fill, if `use_broker_sl` and SL==0, attach via `set_sl` (with retry). |
| E0.10 | Risk/kill-switch loop blind during synchronous AI refresh | BUG (robustness) | M | `engine.py:140` calls `_refresh_ai_views()` inline; Claude (`ai_signal.py:139-147`) + Nemotron (30s timeout) block the tick; kill-switch only runs at tick top (`engine.py:99`) | Run the refresh in a background thread/future; atomically swap `_views/_views_ts/_pending_log` when it returns so the 5s loop and kill-switch keep cycling. |
| E0.11 | Account-wide vs magic-filtered basis inconsistency (latent) | BUG (latent) | S-M | `risk.py:55-56` account-wide acct + magic-filtered positions; gross/concentration on own magic only; margin account-wide; `flatten_all` own-magic only | Assess competition tripwires on `positions(magic=None)`; decouple the kill-eval gate from own-position count. (Becomes live once the scalper trades — also covered by the scalper spec.) |

### Tier 1 — high-value enhancements

| ID | Title | Type | Effort | Evidence | Fix |
|----|-------|------|--------|----------|-----|
| E1.1 | Nemotron consensus is one-sided (only amplifies) | ENH (design flaw) | S | `second_opinion.py:37-44`: agreement boosts `final`; disagree/flat leave `cc` unchanged | Make consensus symmetric: disagreement shrinks confidence (e.g. `final = cc*(1 - gain*nv_conf)`), milder shrink on nemotron-flat. Direction stays Claude's. Add a tunable `nemotron_disagree_shrink`. **(Runtime ON/OFF toggle already shipped — see §4.)** |
| E1.2 | Sizing ignores correlation → one macro bet | ENH | M | `sizer.py:103-119` caps per-symbol only; test fixture `test_sizer.py:13-20` is a pure USD-strength book all under the per-name cap | Group exposure by currency/asset-class factor and cap net factor exposure (cheap), or use a covariance estimate (full). |
| E1.3 | Fixed gross leverage, no volatility targeting | ENH | M | `sizer.py:94` `gross_notional = gross_lev*equity` constant; inverse-vol sets only relative weights | Scale total gross to a portfolio-vol budget; clamp to the leverage cap. Pairs with E1.2. |
| E1.4 | No adaptive refresh cadence/effort | ENH | M | `config.py:118,120` constant `ai_refresh_seconds=900`, `ai_effort="high"`; regime/vol already computed but unused | Refresh faster + higher effort in high-vol regimes, slower + cheaper when calm. (Also see §5 — Doubleword direction.) |
| E1.5 | No stale-views safe mode on extended AI outage | ENH (robustness) | S-M | `engine.py:255-265` keeps prior views; `_signal_changed` won't re-reconcile; no max age | Block new risk after ~2 missed refreshes; de-risk toward flat after a longer outage. |
| E1.6 | Add-order loosens an already-trailed stop | BUG | M | same-direction increase recomputes SL from current price (`engine.py:444-446`, `client.py:256-257`), overwriting a tighter trailed SL on the netting position | Don't send SL on increase orders (let the trail own it post-entry), or re-assert the tighter of {existing, new}. |
| E1.7 | Margin-level kill (150%) over-conservative | ENH | S | `config.py:53,61`: kill at 150% (~67% usage) vs stop-out at 30% (~333% usage); thin band above the 60% soft cap | Lower kill toward ~80-100% margin level (still well above stop-out); widen the gap to the soft cap. Pair with the broker-SL failsafe. |
| E1.8 | Observability: PnL attribution + structured cost | ENH | M | close PnL/reason only `print()`-ed (`engine.py:456-463`); stop-hits only printed (`engine.py:428`); Nemotron cost hardcoded `usd:0.0` (`second_opinion.py:127`); no running total | Log per-symbol realized PnL + close reasons + SL/TP fills to JSONL; price Nemotron tokens; add a running cost total to the panel. The data we need to prove what works. |

### Tier 2 — lower priority / polish

| ID | Title | Type | Effort | Evidence |
|----|-------|------|--------|----------|
| E2.1 | `min_conviction` hard 0.15 cliff vs low-confidence prompt | ENH | S | `sizer.py:44,59` binary drop; prompt compresses FX confidence low (`ai_signal.py:113-116`) → soft ramp |
| E2.2 | Shared SYSTEM prompt makes Nemotron correlated | ENH | S | `second_opinion.py:14,105` reuses Claude's `SYSTEM` → neutral, differently-framed prompt |
| E2.3 | Regime computed but never used in decisions | ENH | M | `engine.py:234` stored, only logged → use cross-model regime disagreement as a global confidence dampener |
| E2.4 | Fixed slippage deviation across all asset classes | ENH | S | `config.py:107` single `order_deviation_points=50` → per-class deviation |
| E2.5 | Trailing activation vs trail-distance conflated | ENH | S | `engine.py:381-388` uses `TRAIL_PCT` as offset only; docstring claims activation gate → split into `activation_pct`/`trail_pct` |
| E2.6 | Min-volume clamp inflates small allocations into dust | BUG | S | `client.py:204-208` forces sub-min lots up to `volume_min` → drop & renormalize |
| E2.7 | Cross-pair notional misvaluation (EURGBP/EURCHF) | BUG | M | `client.py:186-188,198-201` treats quote ccy as USD → convert via the USD leg |
| E2.8 | Restart loses in-memory AI/risk state | ENH | M | `engine.py:65-76`, `risk.py:42` → persist `_views`+age, careful first reconcile |
| E2.9 | Risk engine has no unit tests | ENH (process) | S-M | no `test_risk*.py`; engine tests use `FakeRisk` → table-driven boundary tests |
| E2.10 | False "STOP HIT" log lines on flips | BUG (cosmetic) | S | `engine.py:174,425-432` ticket-based detection misreads netting flips |
| E2.11 | Override/pinned ergonomics inconsistencies | ENH | S | pinned skipped by trailing (`engine.py:367`) but not TP (`engine.py:407`); human view under 0.15 zeroed |

---

## 4. Already shipped: runtime Nemotron toggle

A runtime ON/OFF control for the Nemotron second opinion is implemented (independent of E1.1):
- `controls.py`: `second_opinion` control key (None = use config default) + pure `second_opinion_on(controls, config_default, key_present)` helper.
- `engine.py`: `_refresh_ai_views` gates the Nemotron call on `second_opinion_on(...)`.
- `panel/backend/app.py`: `/api/controls` accepts `second_opinion`.
- Panel UI: "Nemotron: ON/OFF" button in the Controls card.
- Test: `test_second_opinion_toggle.py` (override beats config; never on without a key).

Applies at the next AI refresh (≤15 min). The principled fix (E1.1 symmetric consensus) supersedes this as the long-term answer.

**Currency-factor exposure cap (E1.2) — shipped (block-only, with toggle).** Net per-currency exposure is computed by decomposing each position into base/quote legs (`bot/exposure.py`); a **block-only soft cap** holds any exposure-worsening, weak-consensus add at its current size — never reducing a position, always letting strong consensus through ("only if it's a good thing").
- `exposure.py`: `currency_exposure()` + `blocked_adds()` (pure, tested in `test_exposure.py`).
- `engine.py`: `_clamp_currency_exposure()` runs in the target pipeline before reconcile.
- `config.py`: `use_exposure_cap` (default off), `exposure_cap_frac` (1.0×equity), `exposure_strong_conf` (0.70).
- Runtime toggle: `controls.py` `exposure_cap` key + shared `control_flag()` helper; `/api/controls` accepts it; "Exposure cap: ON/OFF" button in the Controls card.

The full active versions (E1.2 correlation-weighted sizing, E1.3 vol-targeting) remain as larger follow-ups.

---

## 5. AI refresh frequency & Doubleword inference (direction)

The team wants to **refresh more frequently**, **feed the model more information**, and use **Doubleword (formerly TitanML)** inference — also as a hackathon "technology award" angle. Research brief findings (cite sources in the brief; some claims are Doubleword's own marketing — flagged):

**What it is.** Doubleword = rebranded TitanML; self-hosted/enterprise LLM inference. Three pillars: a **hosted Inference API** (`api.doubleword.ai/v1`, **OpenAI-compatible**, Bearer auth, **free tier, no card**), a **Control Layer** model gateway (routing/RBAC/logging), and **Private Infrastructure** (self-host on your own GPUs, OpenAI-compatible endpoints).

**Fit & caveats.**
- **Drop-in for the open-weights leg.** `second_opinion.py` is already raw OpenAI-compatible HTTP, and Doubleword serves **Nemotron** plus DeepSeek/Qwen/GLM/Kimi/Gemma — swapping `base_url`/`api_key`/model is near-zero code. It **cannot** serve the **Claude primary** (`ai_signal.py`), so this augments/replaces the second-opinion/ensemble layer, not the primary.
- **Cost.** ~5–7x cheaper than Anthropic per their marketing (~$4.1K vs ~$30K per 1B tokens) — makes a larger context (more instruments/history) and a faster cadence affordable.
- **Cadence caveat (important).** The cheap tiers are **Async (~25–50% off)** and **Overnight/Batch (~50–80% off, 24h SLA)** — the 24h batch tiers are **useless for a 15-min loop**. Must use the **Real-Time tier**, whose latency/throughput SLAs and rate limits we **could not verify** from public docs — needs a quick live latency test before relying on it.

**Recommended direction (award-worthy framing):** a **self-hosted multi-model ensemble consensus on one platform** — run N open-weights models (e.g. DeepSeek + Qwen + Nemotron) on the same market snapshot via Doubleword's gateway and merge through a generalized `apply_consensus` (extend from 1 booster to N voters). This is the strongest demo: on-prem privacy for proprietary alpha + low-latency local inference + visible multi-model voting. It pairs naturally with the symmetric-consensus fix (E1.1) — N independent voters disagreeing should *shrink* size, agreeing should *raise* it.

**Minimal integration sketch.** Add `DOUBLEWORD_BASE_URL` (default `https://api.doubleword.ai/v1`) + `DOUBLEWORD_API_KEY` env (mirroring the NVIDIA pattern); reuse the existing request body; for the ensemble, loop a list of Doubleword model IDs and feed each into a generalized consensus merge. Keep the tolerant JSON parser.

**Open items before committing:** verify Real-Time latency/throughput with a live probe; confirm exact model IDs (and whether their "Nemotron" matches NVIDIA's `nemotron-3-super`); decide hosted vs self-hosted for the demo. If pursued, this becomes its own implementation plan (generalized N-model consensus + provider config + E1.4 adaptive cadence).

---

## 6. Suggested sequencing

1. **Tier 0 quick wins first** (E0.1, E0.2, E0.7, E0.9) — small, high-confidence correctness fixes.
2. **Risk realignment** (E0.3, E0.4, E0.5, E1.7) — graduated de-risk + correct DD metric; the biggest competition-rank levers.
3. **Execution robustness** (E0.6, E0.8, E1.6) — stop intent/book divergence.
4. **Signal quality** (E1.1 symmetric consensus, then E1.2/E1.3 sizing).
5. **Refresh/Doubleword** (E1.4 + §5) once the research brief lands.
6. **Observability** (E1.8) early-ish — it makes everything else measurable.

Each tier can be its own implementation plan via the writing-plans flow.
