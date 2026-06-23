# AI-Signal Backtest — Results

First full backtest of the AI signal, run through **Doubleword** (via the
Pydantic AI Gateway) using the batch-export workflow. See
[`AI_BACKTEST.md`](AI_BACKTEST.md) for how the tool works.

---

## Cost — why this was run on Doubleword

Backtesting an LLM-driven signal means one model call per historical decision
point. This run was **425 calls**. Routing them through Doubleword instead of the
live model is what makes it affordable:

| | Cost for this 425-call backtest |
|---|---|
| **Doubleword** (DeepSeek V4 Flash, via gateway) — *measured* | **≈ $0.29** |
| Same workload at the **live model's rate** ($5/M in, $25/M out, per `bot/ai_signal.py`) | ≈ $21.79 |
| **Savings** | **≈ $21.50 — a 98.7% reduction** |

> 425 calls used 501,341 input + 771,245 output tokens. Doubleword's own
> cost estimate totalled **$0.286**; the identical token workload priced at the
> live model's published rate is **$21.79**. Doubleword's *batch* tier (not
> reachable through the current gateway — see below) would cut this further still.
>
> Token counts differ between models, so the live-rate figure is the standard
> "what this workload would have cost on the expensive model" approximation, not
> an exact Opus run.

This is the whole point: **Doubleword turns AI-signal backtesting from a ~$22+
job into loose change**, so it can be run routinely (and re-run for parameter
sweeps — see below — for free, since the inference is cached).

---

## Methodology

| | |
|---|---|
| **Period** | 2026-05-11 → 2026-06-10 (~31 days) |
| **Universe** | 10 FX + metals (`AUDUSD EURCHF EURGBP EURUSD GBPUSD USDCAD USDCHF USDJPY XAGUSD XAUUSD`) — crypto not in the cached dataset |
| **Bars** | M15, with H1/H4 resampled on the fly (closed buckets only — no lookahead) |
| **Decisions** | every 4 M15 bars (hourly) after a 512-bar warmup → **425 decision points** |
| **Model (backtest)** | `deepseek-ai/DeepSeek-V4-Flash` via Doubleword — **not** the live Claude Opus 4.8 |
| **Signal/sizer/risk** | the **live** prompt, `MarketView` schema, `build_targets()` sizer, and risk loop, unchanged |
| **Start capital** | $1,000,000 |

---

## Results

Because inference is decoupled from replay, the `aggression` dial was swept over
the **same cached views** at zero extra API cost:

| aggression | return | max DD | Sharpe | trades | final equity | blew up |
|---:|---:|---:|---:|---:|---:|:---:|
| 0.00 | −2.40% | 3.94% | −0.029 | 2,316 | 975,988 | No |
| 0.15 | −3.09% | 5.63% | −0.026 | 2,346 | 969,142 | No |
| **0.30** *(live default)* | **−3.47%** | **7.69%** | **−0.020** | **2,363** | **965,291** | **No** |
| 0.50 | −3.81% | 11.60% | −0.013 | 2,424 | 961,893 | No |
| 0.75 | −4.26% | 19.26% | −0.008 | 2,495 | 957,432 | No |
| 1.00 | −20.23% | 20.95% | −0.055 | 1,000 | 797,673 | No |

---

## What the backtest tells us

1. **No edge on this model/period.** DeepSeek V4 Flash's directional calls lost
   money at every aggression level (−2% to −4% in the survival-to-moderate
   range). This is a useful negative result: the cheap model is **not** a viable
   signal source, and it argues for keeping the live signal on the stronger model.
2. **Heavy churn.** ~2,300+ trades over a month means the signal flips often;
   much of the loss is spread cost from that churn rather than large directional
   bets. A natural next experiment: slower cadence and/or a higher rebalance
   threshold.
3. **The risk system works as designed.** Even at maximum aggression with a
   *losing* signal, the account never came close to liquidation — the drawdown
   circuit-breaker capped the worst case at ~21% and flattened (note the trade
   count collapses to 1,000 at aggression 1.0 as the kill-switch halts trading).
   Survival is structural, exactly as intended.
4. **Aggression behaves monotonically.** Higher aggression → larger drawdown,
   confirming the dial scales risk as designed; with a *positive*-edge signal the
   same mechanism would scale return.

> ⚠️ This validates the **architecture, prompt, sizer, and risk loop** end-to-end
> on real historical data. It is **not** a verdict on the live signal, which runs
> a different (stronger) model. To judge the live signal, re-run with a gateway
> model matching production.

---

## Reproduce

```bash
# 1) export the 425-call batch request file
python run_ai_backtest.py --mode export --out batch_requests.jsonl --cadence-bars 4

# 2) run it through Doubleword (realtime tier via the gateway; ~18 min, ~$0.29)
python submit_batch.py batch_requests.jsonl results.jsonl --concurrency 8 --tier realtime

# 3) deterministic replay + report (free; re-run for any --aggression)
python run_ai_backtest.py --mode replay --results results.jsonl --cadence-bars 4
```

> **Gateway note:** the Pydantic AI Gateway proxies `/chat/completions`,
> `/models`, and `/responses`, but **not** the OpenAI Files/Batches endpoints, so
> Doubleword's native *batch* tier isn't reachable through it. We therefore run
> the exported requests through the gateway's `/responses` endpoint (the
> `/chat/completions` path injects a `safety_identifier` parameter the Doubleword
> backend rejects). The exported `batch_requests.jsonl` is still in OpenAI batch
> format and ready for the native batch tier given direct Doubleword access.
