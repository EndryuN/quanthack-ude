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
1. RISK FIRST    → kill-switch & guardrails (can flatten everything)
2. METRICS       → equity curve, drawdown, 15-min Sharpe
3. AI VIEWS      → refresh Claude's read + a blind Nemotron second opinion,
                   merged by consensus (every 15 min)
4. SIZE & TRADE  → views → target positions → reconcile
5. TRAIL STOPS   → ratchet stops into profit on winners
6. LOG           → write the decision (views, rationale, orders, cost) to disk
```

Risk runs first by design: the strategy can never talk the system out of protecting itself.

---

## Dual-AI consensus (Claude + Nemotron)

Claude sets the **direction**; an NVIDIA **Nemotron** model gives an independent, *blind* second opinion on the same market snapshot. A consensus rule merges them: when they **agree**, conviction is boosted (sizing up the high-agreement trades); when they don't, Claude's view stands — Nemotron never vetoes. This is a disagreement-aware conviction amplifier aimed at improving Sharpe, and it's strictly additive: with no `NVIDIA_API_KEY` (or on any failure) the engine runs Claude-only, unchanged. See [`bot/second_opinion.py`](bot/second_opinion.py).

## Decision log

Every 15-min refresh is persisted to per-day files — `logs/ai_decisions_YYYY-MM-DD.jsonl` (machine-readable) and `.md` (human-readable): the regime, **per-symbol rationale from both models**, the consensus verdict, the orders placed, token cost, and account state. It doubles as a Claude-vs-Nemotron audit trail. See [`bot/decision_log.py`](bot/decision_log.py).

## Operator control panel

A separate, opt-in web dashboard (**SIGNAL//LAYER**) reads the logs + live MT5 and lets you watch — and optionally steer — the engine: a live KPI header with clickable Claude/Nemotron reasoning, the consensus dumbbell/heatmap, equity vs the risk floor, live risk gauges, per-symbol projection cones, History + Scalper pages, global knobs + presets, and per-position pin / human-override / take-profit. Steering is **off by default**; the launcher's *With panel* choice enables it (via `QUANTHACK_PANEL=1`) and otherwise the engine is byte-for-byte unchanged. Full details: **[docs/CONTROL_PANEL.md](docs/CONTROL_PANEL.md)**.

---

## Project structure

```
bot/
  ai_signal.py     # builds the multi-timeframe snapshot, queries Claude, validates output
  second_opinion.py# blind Nemotron (NVIDIA) second opinion + the consensus merge rule
  sizer.py         # turns (direction, confidence) into target lots; one `aggression` dial
  risk.py          # kill-switch + leverage/margin/concentration/drawdown guardrails
  engine.py        # the main loop that orchestrates everything
  decision_log.py  # per-day JSONL + Markdown log of every AI decision
  controls.py      # operator control channel (knobs/presets) — gated, default off
  overrides.py     # per-position overrides (pin / human view / take-profit) — gated
  state.py         # equity history, drawdown, Sharpe; crash-resilient persistence
  client.py        # MetaTrader 5 broker interface (data + orders)
  strategy.py      # alternative "hybrid" signal source (the AI path is default)
  indicators.py    # EMA, volatility, momentum helpers
  config.py        # all tunables: symbols, risk limits, sizer dial, cadence
  gateway.py       # OpenAI-compatible client for the Pydantic AI Gateway -> Doubleword
  ai_signal_gw.py  # gateway variant of the signal (reuses the live prompt + schema)
  ai_backtest.py   # backtests the AI signal over history using Doubleword inference
panel/             # opt-in operator dashboard (FastAPI backend + Plotly frontend)
  run_panel.ps1    # launch the dashboard (reads logs/state/MT5; never touches the engine)
run_ai_backtest.py # CLI for the AI-signal backtester
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

**Requirements:** Python 3.13+, a running [MetaTrader 5](https://www.metatrader5.com/) terminal with Algo Trading enabled, and an Anthropic API key. An NVIDIA `nvapi-…` key (free from [build.nvidia.com](https://build.nvidia.com)) enables the Nemotron second opinion but is **optional** — without it the engine runs Claude-only.

```bash
# 1. install dependencies
pip install -r requirements.txt

# 2. configure your keys
cp .env.example .env        # then edit .env: ANTHROPIC_API_KEY (required),
                            # NVIDIA_API_KEY (optional, enables the second opinion)
```

**3. launch.** The simplest path is the interactive launcher — double-click **`run_live.bat`** (or run it from a terminal). It asks two questions:

1. **Mode** — `[L]ive trading` or `[D]ry-run` (dry-run computes & logs intended trades but sends NO orders).
2. **Panel** — `[W]ith panel` or `[N]o panel`. *With panel* opens the **SIGNAL//LAYER** dashboard at `http://127.0.0.1:8800` in its own window and enables operator steering (it sets `QUANTHACK_PANEL=1`, which flips `use_control_panel` on); *No panel* runs the engine exactly as it would with no panel code at all.

Prefer the CLI / PowerShell directly:

```bash
python -m bot.engine          # dry-run, no panel   (or:  .\run_live.ps1)
python -m bot.engine --live   # LIVE, no panel      (or:  .\run_live.ps1 -Live)
.\panel\run_panel.ps1         # watch-only dashboard (read-only; never steers the engine)
```

Each loop prints a one-line health summary (equity, leverage, margin, return, drawdown, Sharpe). Every 15 minutes it prints the fresh market read with per-instrument direction and confidence (Claude + the Nemotron consensus), plus the API token cost, and writes the full decision to `logs/`.

The dashboard is **read-only unless launched with `QUANTHACK_PANEL=1`** (which the launcher's *With panel* choice sets for you). Standalone `run_panel.ps1` never sets it, so it can only watch. Full details: [docs/CONTROL_PANEL.md](docs/CONTROL_PANEL.md).

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

Claude Opus 4.8 (adaptive extended thinking) · NVIDIA Nemotron (second opinion, OpenAI-compatible NIM) · Anthropic API · Pydantic (structured-output validation) · MetaTrader 5 · NumPy · FastAPI + Plotly (control panel) · Python 3.13+
