# AI-Signal Backtester (Doubleword via Pydantic AI Gateway)

This document explains how Quanthack backtests its **actual AI signal** over
historical data, and how it uses **Doubleword's tiered inference** (through the
Pydantic AI Gateway) to make that affordable.

> 📊 **Real results from the first full run** (425 calls, ~$0.29, a 98.7% cost
> reduction vs. the live-model rate) are in **[BACKTEST_RESULTS.md](BACKTEST_RESULTS.md)**.

---

## Why this exists

The live engine's signal is produced by a large language model that reads a
multi-timeframe market snapshot and returns a directional view per instrument
(see [`bot/ai_signal.py`](../bot/ai_signal.py)). Until now there was **no way to
backtest that signal**: the classical backtester (`bot/backtest.py`) only replays
the rule-based `HybridStrategy`. Backtesting the AI signal means making one LLM
call per historical decision point — **hundreds to thousands of calls** — which
is far too slow and expensive at realtime, interactive pricing.

**That is exactly the workload Doubleword's batch / async tiers are designed
for:** large numbers of independent, latency-insensitive LLM calls at a steep
discount. This backtester turns "validate the AI signal on a month of data" from
impractical into a routine, cheap job.

---

## ⚠️ Important: which model is being tested

The live signal runs on **Claude Opus 4.8** (Anthropic API). The backtester runs
through the **Pydantic AI Gateway → Doubleword**, whose catalog currently serves
**`deepseek-ai/DeepSeek-V4-Flash`** (and others), *not* Claude Opus 4.8.

So this tool validates the **prompt design, the multi-timeframe context, the
sizer, and the risk loop** on a fast, cheap model — it is **not** an
apples-to-apples backtest of the exact live signal. Read the results as
"does the *architecture* produce sane, survivable behaviour on historical data,
and how sensitive is it to the model" rather than "this is exactly what live
would have returned."

To make it apples-to-apples you have two options:
1. Point the backtester at a gateway model that matches your live model
   (set `DOUBLEWORD_MODEL`), if/when one is available; or
2. Switch the live signal to the same gateway model for consistency across
   backtest and production.

Everything *except the model* is shared with live: the same `SYSTEM` prompt, the
same `build_context()` snapshot, the same `MarketView` schema, and the same
`build_targets()` sizer.

---

## Architecture — three decoupled phases

The expensive LLM step is deliberately separated from the cheap, deterministic
backtest math, so you can run inference once and replay the math for free as many
times as you like (e.g. to sweep the `aggression` dial).

```
 ┌─────────┐      ┌──────────┐      ┌──────────┐
 │  PLAN   │ ───► │  INFER   │ ───► │  REPLAY  │
 └─────────┘      └──────────┘      └──────────┘
 walk bar grid    prompt → view     view → sizer →
 build one        via gateway:      mark-to-market →
 snapshot prompt  online OR batch   risk loop → result
 per decision     (Doubleword tier) (deterministic,
 point            of your choice     reproducible, free)
```

1. **PLAN** (`plan_decision_points`) — steps through the cached bar grid and, at
   each decision point, builds the live `build_context()` market snapshot. Pure;
   no API calls. Each point gets a stable `custom_id` (`dp-<grid_index>`).
2. **INFER** — converts each snapshot into a directional view. Two paths:
   - **online**: concurrent realtime/async calls through the gateway (works
     today, good for quick runs);
   - **batch**: export an OpenAI-style JSONL, run it through Doubleword's batch
     tier (or the Autobatcher), and load the results back — **cheapest**.
3. **REPLAY** (`replay`) — feeds the views through the live sizer and a
   deterministic mark-to-market + risk loop identical in convention to
   `bot/backtest.py`. Reproducible and free; re-run it to tune sizing without
   touching the API.

### No lookahead

The cache is **M15-only**, so the backtester resamples M15 → H1/H4 on the fly to
feed `build_context` exactly the shape the live MT5 client provides. Resampled
higher-timeframe bars are exposed **only once their final M15 bar has occurred**
(`searchsorted` on each bucket's last index), so no future information leaks into
a decision.

---

## Mapping to Doubleword's three tiers

| Tier | How to use it here | When |
|---|---|---|
| **Realtime** | `--mode online --service-tier priority` | quick smoke tests, a handful of points |
| **Async (flex)** | `--mode online --service-tier flex` (default) | medium runs; reduced cost, minutes of latency |
| **Batch** | `--mode export` → submit JSONL to Doubleword batch / Autobatcher → `--mode replay` | full-month runs, prompt evals, sweeps — **lowest cost**, 24h SLA |

---

## Setup

Add your gateway key to `.env` (see `.env.example`). Get it from the gateway's
**Connect** tab.

```
PYDANTIC_AI_GATEWAY_API_KEY=pylf_v2_eu_...your-key...
```

Optional overrides (env vars):

- `DOUBLEWORD_MODEL` — model id (default `deepseek-ai/DeepSeek-V4-Flash`)
- `PYDANTIC_AI_GATEWAY_URL` — proxy root (default the EU doubleword proxy)

> 🔐 The key is a bearer token. It is loaded from `.env` (gitignored) and never
> hardcoded. If it has ever been shown in plaintext, rotate it.

---

## Usage

```bash
# 1) Quick smoke — 5 hourly decision points, online inference
python run_ai_backtest.py --mode online --cadence-bars 4 --limit 5

# 2) Full run, online concurrent inference + deterministic replay + report
python run_ai_backtest.py --mode online --cadence-bars 4 --concurrency 8

# 3) Recommended — export a batch JSONL, run it through the gateway, then replay
python run_ai_backtest.py --mode export --out batch_requests.jsonl --cadence-bars 4
python submit_batch.py batch_requests.jsonl results.jsonl --concurrency 8 --tier realtime
python run_ai_backtest.py --mode replay --results results.jsonl --cadence-bars 4
```

`submit_batch.py` reads the exported JSONL, runs each request through the gateway
concurrently (with retries + resume), and writes a `results.jsonl` the replay
consumes. Re-running it skips already-fetched points.

### Key flags

| Flag | Default | Meaning |
|---|---|---|
| `--mode` | `online` | `online` \| `export` \| `replay` |
| `--cadence-bars` | `4` | decisions every N M15 bars (`4` = hourly; `1` = every 15 min, like live) |
| `--warmup-bars` | `512` | bars to skip so H1/H4/3-day context is fully formed (~5.3 days) |
| `--aggression` | `CONFIG.aggression` | sizer dial 0..1 (replay-only; re-run replay to sweep) |
| `--limit` | none | cap decision points for a cheap smoke test |
| `--concurrency` | `8` | parallel in-flight calls (online) |
| `--model` | gateway default | override the gateway model |
| `--service-tier` | `flex` | `priority` (realtime) or `flex` (async) |
| `--save-views` | none | (online) cache inferred views to JSON for free re-replays |

> **Note on `--limit`:** it caps how many points are *inferred*, but replay runs
> over the whole period and holds the last known view between decision points.
> A small `--limit` therefore holds one view for a long stretch — fine for a
> plumbing smoke test, **not** a representative result. Use a full run (no limit)
> for real numbers.

### The JSONL files

- **Request file** (`--mode export`) is the standard OpenAI batch input shape:
  one line per decision point, `{custom_id, method, url, body}`. The `body` is the
  exact same chat-completions request the online path sends.
- **Results loader** (`--mode replay`) accepts both the OpenAI/Doubleword batch
  *output* shape (`{custom_id, response:{body:{choices:[...]}}}`) and a simple
  `{custom_id, content}` shape, matched back to decision points by `custom_id`.

---

## Reading the output

```
=== AI-SIGNAL BACKTEST RESULT ===
  return   : +X.XX%     # total return over the test window
  max DD   : X.XX%      # worst peak-to-trough drawdown
  sharpe   : X.XXX      # non-annualized, same convention as live state.py
  trades   : N          # reconciliation trades (incl. kill-switch flattens)
  final eq : 1,0XX,XXX  # from the $1,000,000 start
  blew up  : False      # True if equity hit zero (would be elimination)
```

Because PLAN/INFER/REPLAY are decoupled, the honest way to tune sizing is:
infer **once** (save the views), then re-run **replay** across several
`--aggression` values — the LLM cost is paid a single time.

---

## Files

| File | Role |
|---|---|
| [`bot/gateway.py`](../bot/gateway.py) | OpenAI-compatible HTTP client for the gateway (httpx, no SDK dep) |
| [`bot/ai_signal_gw.py`](../bot/ai_signal_gw.py) | gateway variant of the signal — reuses live prompt/schema; builds request bodies; parses views |
| [`bot/ai_backtest.py`](../bot/ai_backtest.py) | M15→H1/H4 resampling client + the three phases (plan/infer/replay) |
| [`run_ai_backtest.py`](../run_ai_backtest.py) | CLI that wires the phases together |
| [`submit_batch.py`](../submit_batch.py) | runs an exported JSONL through the gateway (concurrent, retries, resume) → `results.jsonl` |

## Gateway specifics

The Pydantic AI Gateway proxies `/chat/completions`, `/models`, and `/responses`,
but **not** the OpenAI Files/Batches endpoints — so Doubleword's native *batch*
tier can't be driven through it. Two more wrinkles handled in code:

- The `/chat/completions` proxy injects a `safety_identifier` parameter that the
  Doubleword backend rejects with a 400. We therefore route all calls through
  **`/responses`** (`bot/gateway.py` converts our chat bodies automatically).
- The async (`flex`) tier has minutes-per-call latency, impractical for a
  one-off backtest, so the default is the realtime tier (cost is negligible — see
  the results doc).
