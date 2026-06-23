# Quanthack

**An autonomous trading engine that uses a frontier AI model (Claude Opus 4.8) to read the market across multiple timeframes, then sizes and executes the resulting trades through a deterministic, risk-controlled engine.**

Built for a multi-day simulated trading competition (forex, metals, crypto; $1M simulated capital, 1:30 leverage). The design goal: **maximize return rank without ever blowing up.**

> 📄 For the full, judge-friendly walkthrough of how and why it works, see **[DOCUMENTATION.md](DOCUMENTATION.md)**.

---

## The core idea

Most trading bots are rigid rule machines. Quanthack instead **separates judgment from execution**:

- **Judgment** — Claude is shown a compact, multi-timeframe snapshot (M15 / H1 / H4 trends, multi-horizon returns, momentum, volatility, range position) of each instrument and returns a directional view + a *calibrated confidence*. It reasons about cross-asset macro stories, not a single indicator.
- **Execution** — A deterministic engine converts those views into mathematically-sized positions, places the orders, trails stops into profit, and enforces hard risk limits the AI **cannot override**.

The result: the adaptability of AI reasoning, wrapped in the discipline of a classical quant risk system.

---

## The loop (every 5 seconds)

```
1. RISK FIRST   → kill-switch & guardrails (can flatten everything)
2. METRICS      → equity curve, drawdown, 15-min Sharpe
3. AI VIEWS     → refresh Claude's market read (every 15 min)
4. SIZE & TRADE → views → target positions → reconcile
5. TRAIL STOPS  → ratchet stops into profit on winners
```

Risk runs first by design: the strategy can never talk the system out of protecting itself.

---

## Project structure

```
bot/
  ai_signal.py    # builds the multi-timeframe snapshot, queries Claude, validates output
  sizer.py        # turns (direction, confidence) into target lots; one `aggression` dial
  risk.py         # kill-switch + leverage/margin/concentration/drawdown guardrails
  engine.py       # the main loop that orchestrates everything
  state.py        # equity history, drawdown, Sharpe; crash-resilient persistence
  client.py       # MetaTrader 5 broker interface (data + orders)
  strategy.py     # alternative "hybrid" signal source (the AI path is default)
  indicators.py   # EMA, volatility, momentum helpers
  config.py       # all tunables: symbols, risk limits, sizer dial, cadence
  gateway.py      # OpenAI-compatible client for the Pydantic AI Gateway -> Doubleword
  ai_signal_gw.py # gateway variant of the signal (reuses the live prompt + schema)
  ai_backtest.py  # backtests the AI signal over history using Doubleword inference
run_ai_backtest.py  # CLI for the AI-signal backtester
```

The repository also includes the research/probe scripts and backtests used to validate the approach (and to rule out momentum, mean-reversion, stat-arb, and order-flow strategies that showed no durable edge).

## Backtesting the AI signal

The classical backtester replays the rule-based strategy, but the *AI* signal needs one LLM call per historical decision point — hundreds to thousands of latency-insensitive calls. That's run cheaply through **Doubleword's batch/async tiers** (via the Pydantic AI Gateway):

```bash
# online (quick): infer through the gateway, then replay deterministically
python run_ai_backtest.py --mode online --cadence-bars 4

# batch (cheapest): export JSONL → run through Doubleword's batch tier → replay
python run_ai_backtest.py --mode export --out requests.jsonl --cadence-bars 4
python run_ai_backtest.py --mode replay --results results.jsonl --cadence-bars 4
```

Running it on Doubleword was **~98.7% cheaper** than the live model: the first full 425-call backtest cost **≈ $0.29** on Doubleword vs. **≈ $21.79** at the live model's rate — letting AI-signal backtests (and free parameter sweeps over cached views) run routinely instead of being cost-prohibitive.

- **[docs/AI_BACKTEST.md](docs/AI_BACKTEST.md)** — how the tool works, the tier mapping, gateway specifics, and the note that the backtest runs on a *different model* than live (DeepSeek V4 Flash vs. Claude Opus 4.8)
- **[docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md)** — the actual first-run results + the cost breakdown

---

## Quickstart

**Requirements:** Python 3.14, a running [MetaTrader 5](https://www.metatrader5.com/) terminal with Algo Trading enabled, and an Anthropic API key.

```bash
# 1. install dependencies
pip install MetaTrader5 anthropic pydantic numpy

# 2. configure your key
cp .env.example .env        # then edit .env and paste your real ANTHROPIC_API_KEY

# 3. run in dry-run (computes & logs intended trades, sends NO orders)
python -m bot.engine

# 4. run live (actually places orders)
python -m bot.engine --live
```

Each loop prints a one-line health summary (equity, leverage, margin, return, drawdown, Sharpe). Every 15 minutes it prints the AI's fresh market read with per-instrument direction and confidence, plus the API token cost of that call.

---

## Safety

The risk engine keeps the system comfortably inside every competition red-line — even at maximum aggression a forced liquidation cannot happen before its own kill-switch fires:

| Protection | Competition red-line | Self-imposed limit |
|---|---|---|
| Anti-liquidation kill-switch | stop-out at 30% margin level | flatten everything at **150%** |
| Effective leverage | penalty above 28× | hard cap **12×** (targets ~6×) |
| Margin usage | penalty above 90% | hard cap **60%** |
| Single-instrument concentration | penalty above 90% | capped at **45–50%** |
| Drawdown | (affects rank) | own circuit-breaker at **20%** |

---

## Configuration

Everything lives in [`bot/config.py`](bot/config.py). The two dials you'll touch most:

- **`aggression`** (0–1) — interpolates the entire risk posture from *survival* (low leverage, diversified, risk-parity weighted) to *finals* (higher leverage, concentrated into top-conviction names).
- **`signal_source`** — `"ai"` (Claude + sizer, default) or `"hybrid"` (the classical strategy fallback).

---

## Security note

`.env` (your real API key) is gitignored and never committed. `.env.example` is a placeholder template only. **Never commit a real key.**

---

## Tech stack

Claude Opus 4.8 (adaptive extended thinking) · Anthropic API · Pydantic (structured-output validation) · MetaTrader 5 · NumPy · Python 3.14
