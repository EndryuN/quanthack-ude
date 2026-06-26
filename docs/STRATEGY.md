# Trading Bot: Strategy & Execution Reference

*Ground truth: every claim below is cited to the actual source file and line number. This document describes what the bot does, not what it was intended to do.*

---

## 1. Overview / Thesis

The bot is designed for a simulated multi-day FX/metals/crypto trading competition. Scoring is approximately **70% return-rank among surviving accounts**, with smaller weights on drawdown and Sharpe ratio. Forced liquidation is instant elimination (`ai_signal.py:105`).

The core philosophy, stated directly in `sizer.py:17`:

> "No easy alpha, so size for rank variance while staying survivable."

This means:

- Traditional quantitative edges (momentum, mean-reversion, stat-arb, order-flow) showed no reliable signal on the available data. Claude was the signal source that remained after all of them were ruled out (`ai_signal.py:8-10`).
- Because the competition rewards relative rank, the bot deliberately accepts higher risk than a conventional hedge fund would, but stays well away from the liquidation level that would trigger elimination.
- The bot uses a single `aggression` dial (default **0.30**) to interpolate between a low-leverage, diversified survival posture and a high-leverage, concentrated finals posture.

---

## 2. What the LLM Is Given to Decide

### 2a. The Market Context: `build_context()` (`ai_signal.py:62-98`)

Once every 15 minutes, the bot assembles a **compact multi-timeframe snapshot** for each traded instrument and passes it as plain text to the model. This is the sole market data input — no price charts, no order book, no news.

**Per-instrument fields computed:**

| Field | How computed | Source timeframe |
|---|---|---|
| `px` | Last close price of the most recent M15 bar | M15 (120 bars) |
| `ret 1h` | `(close[-1] / close[-4] - 1) * 100` — 4 × 15-min bars | M15 |
| `ret 4h` | `(close[-1] / close[-16] - 1) * 100` — 16 × 15-min bars | M15 |
| `ret 1d` | `(close[-1] / close[-96] - 1) * 100` — 96 × 15-min bars | M15 |
| `ret 3d` | `(close[-1] / close[-72] - 1) * 100` — 72 H1 bars | H1 (240 bars) |
| `trend M15` | `"up"` if EMA(20) > EMA(80) on M15 closes, else `"dn"` | M15 |
| `trend H1` | `"up"` if EMA(10) > EMA(40) on H1 closes, else `"dn"` | H1 |
| `trend H4` | `"up"` if EMA(6) > EMA(24) on H4 closes, else `"dn"` | H4 (120 bars) |
| `mom_z` | Momentum z-score over 32 M15 bars (`indicators.momentum_z`) | M15 |
| `vol/bar` | Rolling return volatility over 48 M15 bars × 100 | M15 |
| `5d-range` | Price position in last 120 H1 bars' high-low range, 0%=low, 100%=high | H1 |

**Exact format string** (`ai_signal.py:94-97`):

```
{symbol}: px={price:.6g} | ret 1h={r1h:+.2f} 4h={r4h:+.2f} 1d={r1d:+.2f} 3d={r3d:+.2f}% | trend M15={tm} H1={th1} H4={th4} | mom_z={z:+.2f} vol/bar={vol:.2f}% | 5d-range={pos:.0f}%
```

**Realistic example for one instrument:**

```
EURUSD: px=1.08423 | ret 1h=-0.04 4h=+0.11 1d=-0.23 3d=+0.87% | trend M15=up H1=up H4=dn | mom_z=+0.41 vol/bar=0.06% | 5d-range=61%
```

All instruments are concatenated line-by-line with no separator. The entire block is placed verbatim in the user message.

**What is deliberately excluded:** bid/ask spread, open interest, news, fundamentals, individual bar OHLC data, volume, tick data. The snapshot is intentionally terse to fit the token budget.

### 2b. The System Prompt (`ai_signal.py:101-119`)

The full `SYSTEM` constant is reproduced here because its calibration instructions directly shape how the model assigns confidence:

> "You are the signal engine for an algorithmic entry in a simulated multi-day trading competition on forex, metals and crypto. Scoring is ~70% return rank among surviving accounts, with smaller weights on drawdown and Sharpe; a forced liquidation means elimination. Capital is simulated — there is no real-world risk, this is a sandboxed contest."

Key behavioural instructions in the system prompt:

1. **Timeframe alignment rule** — Weight setups where M15, H1, and H4 trends all agree more highly; conflicting timeframes should produce lower confidence.
2. **Range-stretch caution** — Be wary of chasing moves already at the extreme of the 5-day range.
3. **Calibrated confidence** — Most liquid-FX pairs deserve *low* confidence because markets are efficient; reserve higher confidence for multi-horizon trend alignment or coherent cross-asset macro stories (e.g. broad USD strength/weakness, risk-on/off).
4. **Prefer flat** — Output `flat` when there is no genuine view, rather than guessing.
5. **Cross-asset consistency** — USD pairs should agree on a single USD direction; metals and crypto should behave as risk barometers.
6. **Coverage** — Return a view for every instrument provided (any omitted symbol is silently assigned flat by the engine, `ai_signal.py:168-170`).

### 2c. The Claude Call: Model and Settings (`ai_signal.py:122-171`)

| Setting | Value | Notes |
|---|---|---|
| Model | `claude-opus-4-8` | `ai_signal.py:24` |
| `max_tokens` | 16,000 | Generous ceiling so adaptive thinking doesn't truncate structured output |
| `thinking` | `{"type": "adaptive"}` | Extended thinking enabled; effort level defaults to `"high"` (`config.py:86`) |
| `output_format` | `MarketView` Pydantic model | Structured output — parse failure raises immediately |
| Cadence | Every 900 seconds (15 min) | `config.py:85`; controlled by `ai_refresh_seconds` |
| Retry / backoff | 30s → doubles → 300s cap | On any failure, stale views are held until the next successful call |

**The user message** sent to Claude (`ai_signal.py:132-134`):

```
Instruments and recent action (competition universe = exactly these {N} symbols):

{context}

Give a directional view + calibrated confidence for each.
```

**What Claude outputs — the `MarketView` schema** (`ai_signal.py:36-48`):

```python
class InstrumentView(BaseModel):
    symbol: str
    direction: Literal["long", "short", "flat"]
    confidence: float   # [0.0, 1.0]
    rationale: str      # one concise sentence

class MarketView(BaseModel):
    regime: str         # one line on the overall macro/risk regime
    views: List[InstrumentView]
```

Claude does **not** output price targets, entry levels, stop distances, or holding periods. It outputs only: direction + calibrated confidence + one-sentence rationale + one regime line. The engine converts these directly to position sizes.

### 2d. The Nemotron Second Opinion (`second_opinion.py`)

After Claude responds, the bot optionally queries **NVIDIA's Nemotron-3-Super-120B** model via the NVIDIA NIM OpenAI-compatible API. This is a "blind" second opinion: Nemotron sees the **same market context string** and the **same `SYSTEM` prompt**, but it never sees Claude's answer (`second_opinion.py:88-128`).

Key differences from the Claude call:

- Thinking is **explicitly disabled** (`"chat_template_kwargs": {"enable_thinking": False}`, `second_opinion.py:113`) — Nemotron must return the JSON directly to fit in the 30-second timeout and 4,000-token budget.
- The response format is a plain JSON object requested via a prompt instruction, not a structured-output API feature.
- Temperature is 0.2 (deterministic).
- If Nemotron is unavailable or fails for any reason, the bot proceeds on Claude-only with no degradation in logic.

**Consensus rule — `apply_consensus()` (`second_opinion.py:19-57`):**

Claude always sets the direction. Nemotron can only raise confidence when it independently agrees — it can never veto or reverse Claude's direction.

| Nemotron outcome | Effect |
|---|---|
| Agrees (same non-flat direction as Claude) | `final_conf = claude_conf + (1.0 - claude_conf) × 0.5 × nemotron_conf` |
| Disagrees (opposite direction) | No change — Claude's confidence is unchanged |
| Returns flat | No change |
| Unavailable / failed | No change |

The `0.5` multiplier is the `nemotron_consensus_gain` parameter (`config.py:97`). With `gain=0.5`, if Claude says LONG at 0.50 and Nemotron says LONG at 0.80, the final confidence is `0.50 + (0.50 × 0.5 × 0.80) = 0.70`. The boost is additive and capped at 1.0.

---

## 3. From Decision to Order: The Execution Workflow

The engine runs a **5-second loop** (`config.py:74`). Each tick executes in strict order of authority (`engine.py:71-112`):

```
Every 5 seconds (tick):
  │
  ├─ 1. RISK ASSESS  ──────────────────────────────────────── engine.py:75-81
  │        RiskEngine.assess() evaluates margin level, drawdown, leverage.
  │        If kill=True: flatten ALL positions, print, RETURN immediately.
  │        Nothing below this line runs while kill is active.
  │
  ├─ 2. STATE / METRICS  ──────────────────────────────────── engine.py:83
  │        StateTracker.update(equity): update return%, max drawdown, Sharpe.
  │
  ├─ 3. AI REFRESH (every 15 min, not every tick)  ─────────── engine.py:86-92
  │        If views are stale (>900s old) and no backoff:
  │          a. build_context()  → market snapshot string
  │          b. get_ai_views()   → Claude call → MarketView
  │          c. get_second_opinion() → Nemotron call (if enabled)
  │          d. apply_consensus() → merge → sizer_views
  │          Views timestamp updated. `refreshed` flag set.
  │
  ├─ 4. RECONCILE (ONLY if views changed since last reconcile)  engine.py:96-101
  │        Anti-churn gate: compares views_ts vs last_acted_ts.
  │        Fires at most once per AI refresh (not every 5s tick).
  │          a. build_targets() → sizer converts views → target signed lots
  │          b. _derisk_only() if allow_new_risk=False
  │          c. _reconcile() → diff current vs target per symbol
  │             • target=0 and held: _do_close() → market order to close
  │             • delta ≥ volume_min: _do_order() → market order BUY/SELL
  │               Attaches broker-side SL (catastrophe backstop):
  │               FX ±5%, METAL ±8%, CRYPTO ±15%  (config.py:19)
  │
  └─ 5. TRAILING STOPS (every tick)  ───────────────────────── engine.py:105-107
           For each open position: if price has moved past entry by trail_pct,
           ratchet the broker SL toward profit. SL only ever tightens.
           FX: trail 1.0%, METAL: 2.0%, CRYPTO: 4.0%  (config.py:25)
```

**Key anti-churn gate detail** (`engine.py:114-117`, `engine.py:101`): `_signal_changed()` returns `True` only when `views_ts != last_acted_ts`. After reconcile runs, `last_acted_ts` is set to `views_ts`. So reconcile fires **exactly once** after each successful AI refresh, regardless of how many 5-second ticks occur before the next refresh.

**Orders are always market orders** with immediate fill. The broker-side SL (`use_broker_sl=True`, `config.py:87`) is a catastrophe backstop for when the process is offline — it is set loose enough not to trip on normal volatility. The live kill-switch is the primary liquidation guard.

---

## 4. Sizing: From Views to Lots (`sizer.py`)

`build_targets()` (`sizer.py:51-100`) takes `{symbol: (direction_int, confidence)}` and returns `{symbol: target_signed_lots}`.

### The Aggression Dial

A single parameter `aggression` in `[0, 1]` (default **0.30**, `config.py:84`) interpolates the entire risk posture:

| Parameter | `aggression=0` (Survival) | `aggression=1` (Finals) | Default (0.30) |
|---|---|---|---|
| Gross leverage target | 2× equity | 6× equity | ~3.2× |
| Concentration power | 1.0 (flat = diversified) | 2.5 (conviction^2.5 = concentrated) | ~1.45 |
| Single-instrument cap | 30% of gross notional | 45% | ~34% |
| Inverse-vol weighting | Full 1/vol (risk parity) | Off (pure conviction) | Partially on |

(`sizer.py:35-44`, `sizer.py:63-65`)

### Sizing Algorithm (step by step)

1. **Filter** — Drop any symbol where `direction == 0` or `confidence < min_conviction` (threshold: **0.15**, `sizer.py:44`).
2. **Raw weight** — `w = confidence ^ power`, where `power` is interpolated by aggression.
3. **Inverse-vol adjustment** — If `use_inverse_vol=True` (default on): multiply by `(1/vol)^(1-aggression)`. At `aggression=0` this gives full risk-parity weighting; at `aggression=1` the exponent is zero so vol is ignored entirely. Vol is computed over the last 48 closed M15 bars (the forming bar is excluded to prevent intra-15-min jitter, `sizer.py:76`).
4. **Normalize** — Weights summed to 1.0.
5. **Concentration cap** — Iteratively redistribute weight above `single_cap` to other instruments (up to 6 redistribution passes, `sizer.py:103-119`).
6. **Lots** — `target_lots = lots_for_notional(symbol, weight × gross_leverage × equity) × direction`

---

## 5. The Risk Floor (`risk.py`, `config.py`)

The risk engine sits **below the strategy** and cannot be overridden by AI decisions or the sizer. It evaluates on every 5-second tick.

### Kill-Switch (flatten everything)

| Condition | Threshold | Source |
|---|---|---|
| Margin level too low | `margin_level < 150%` | `config.py:52`, `risk.py:84-86` |
| Round drawdown breaker | `(initial_equity - equity) / initial_equity >= 20%` | `config.py:67`, `risk.py:89-92` |

When either kill condition fires: all positions are closed immediately via `flatten_all()`. The tick returns without executing reconcile, sizer, or trailing-stop logic.

The competition stop-out is at `margin_level=30%`. The bot's kill-switch fires at **150%** — far above the competition's liquidation level, giving substantial buffer.

### Soft Guards (block new risk, don't close)

| Condition | Threshold | Effect |
|---|---|---|
| Margin level warning | `margin_level < 250%` | `allow_new_risk = False` |
| Gross leverage cap | `gross_leverage > 12×` | `allow_new_risk = False` |
| Margin usage cap | `margin_usage > 60%` | `allow_new_risk = False` |

When `allow_new_risk=False`, `_derisk_only()` (`engine.py:194-209`) is applied: position increases and flips are suppressed; only reductions toward flat are allowed.

### Competition Tripwire Trackers (30-minute timers)

The risk engine tracks how long specific conditions have been continuously true. These are **warnings only** — they do not trigger the kill-switch but print to the console so a human operator can intervene (`risk.py:107-115`):

| Condition tracked | Warn threshold | Competition penalty threshold |
|---|---|---|
| Gross leverage | > 25× for N minutes | > 28× for ≥ 30 min |
| Margin usage | > 85% for N minutes | > 90% for ≥ 30 min |
| Single-instrument concentration | > 85% of gross for N minutes | > 90% for ≥ 30 min |

---

## 6. Instruments

The bot trades **14 instruments** (15 defined minus 1 excluded):

| Category | Symbols |
|---|---|
| FX (8) | AUDUSD, EURCHF, EURGBP, EURUSD, GBPUSD, USDCAD, USDCHF, USDJPY |
| Metals (2) | XAGUSD (silver), XAUUSD (gold) |
| Crypto (4) | BTCUSD, ETHUSD, SOLUSD, XRPUSD |

**BARUSD is explicitly excluded from trading** (`config.py:78`), with the reason documented in the config:

> "BARUSD excluded: max notional ~$786 (tiny contract + 100-lot cap) makes it untradeable in size — pure spread cost."

BARUSD remains in `ALL_SYMBOLS` for data-collection purposes but is filtered out of `EngineConfig.symbols` at startup. It is not sent to Claude and receives no AI view.

---

## 7. Strategies in the Code vs. in the Live Algorithm

Several classical strategies are **implemented and backtestable**, but only the AI path and the momentum scalper actually trade live. This is deliberate: the rule-based edges (momentum, mean-reversion) were built, then ruled out on the data, so the LLM became the live signal — but the code is kept for backtesting/validation.

| Strategy | Where it lives | Live? |
|---|---|---|
| **AI (Claude + ensemble) → sizer** | `ai_signal.py`, `sizer.py` | **Yes — the main engine** (`signal_source="ai"`, `config.py:158`; engine takes the AI branch at `engine.py:207, 263`) |
| **Momentum (rules)** | `strategy.py` `HybridStrategy`, `mode="momentum"` (`strategy.py:73-78`) | **No** — only the `"hybrid"` branch (`engine.py:268-270`) uses it; never reached with the default config |
| **Mean reversion (rules)** | `strategy.py` `mode="meanrev"` — flips the trend signal: `direction = -direction` ("fade the move", `strategy.py:79-80`) | **No** — hybrid/backtest only |
| **Crypto momentum / breakout** | `crypto_signal.py` | **Yes — the optional scalper only** (alts confirmed by BTC lead-lag) |
| **Mean-reversion "fade" scalper** | `scalp_signal.py`, `scalp_backtest.py` | **No** — dormant/reference only |

**Key facts to remember:**

- The default `signal_source = "ai"` (`config.py:158`) means the engine **never** calls `HybridStrategy.target_positions()` in normal operation — `HybridStrategy` is instantiated (`engine.py:101`) but only drives targets in the `"hybrid"` branch and in the backtester (`backtest.py:111`).
- So **mean reversion is NOT part of the live algorithm.** It exists as a flag (`StrategyConfig.mode="meanrev"`) for research only.
- The live scalper is explicitly momentum, *not* mean reversion. Its own docstring (`crypto_signal.py:3-5`): *"The fade scalper died because tiny mean-reversion edges drowned in spread… trade WITH momentum/breakouts, not against noise."*
- To test the rule-based strategies live you would set `signal_source="hybrid"` (and `StrategyConfig.mode`); nothing in the default config does this.

---

## Appendix: Cost Tracking

Every Claude call records token usage and an approximate USD cost (`ai_signal.py:27-33`): `$5/M input tokens, $25/M output tokens`. At the 15-minute cadence with 14 instruments, a single competition session incurs roughly one call every 900 seconds. Cost is written to the per-day decision log (`engine.py:168-174`) and printed to the console after each call (`ai_signal.py:154-155`).
