# Trading Bot: Strategy & Execution Reference

*Ground truth: every claim below is cited to the actual source file and line number. This document describes what the bot does, not what it was intended to do.*

---

## 1. Overview / Thesis

The bot is designed for a simulated multi-day FX/metals/crypto trading competition. Scoring is approximately **70% return-rank among surviving accounts**, with smaller weights on drawdown and Sharpe ratio. Forced liquidation is instant elimination (`ai_signal.py:116`).

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

### 2b. The System Prompt (`ai_signal.py:113-131`)

The full `SYSTEM` constant is reproduced here because its calibration instructions directly shape how the model assigns confidence:

> "You are the signal engine for an algorithmic entry in a simulated multi-day trading competition on forex, metals and crypto. Scoring is ~70% return rank among surviving accounts, with smaller weights on drawdown and Sharpe; a forced liquidation means elimination. Capital is simulated — there is no real-world risk, this is a sandboxed contest."

Key behavioural instructions in the system prompt:

1. **Timeframe alignment rule** — Weight setups where M15, H1, and H4 trends all agree more highly; conflicting timeframes should produce lower confidence.
2. **Range-stretch caution** — Be wary of chasing moves already at the extreme of the 5-day range.
3. **Calibrated confidence** — Most liquid-FX pairs deserve *low* confidence because markets are efficient; reserve higher confidence for multi-horizon trend alignment or coherent cross-asset macro stories (e.g. broad USD strength/weakness, risk-on/off).
4. **Prefer flat** — Output `flat` when there is no genuine view, rather than guessing.
5. **Cross-asset consistency** — USD pairs should agree on a single USD direction; metals and crypto should behave as risk barometers.
6. **Coverage** — Return a view for every instrument provided (any omitted symbol is silently assigned flat by the engine, `ai_signal.py:181-182`).

### 2c. The Claude Call: Model and Settings (`ai_signal.py:134-183`)

| Setting | Value | Notes |
|---|---|---|
| Model | `claude-opus-4-8` | `ai_signal.py:24` |
| `max_tokens` | 16,000 | Generous ceiling so adaptive thinking doesn't truncate structured output |
| `thinking` | `{"type": "adaptive"}` | Extended thinking enabled; effort level defaults to `"high"` (`config.py:161`) |
| `output_format` | `MarketView` Pydantic model | Structured output — parse failure raises immediately |
| Cadence | Every 900 seconds (15 min) | `config.py:160`; controlled by `ai_refresh_seconds` |
| Retry / backoff | 30s → doubles → 300s cap | On any failure, stale views are held until the next successful call |

**The user message** sent to Claude (`ai_signal.py:144-146`):

```
Instruments and recent action (competition universe = exactly these {N} symbols):

{context}

Give a directional view + calibrated confidence for each.
```

**What Claude outputs — the `MarketView` schema** (`ai_signal.py:36-46`):

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

### 2d. The Blind Second-Opinion Ensemble (`second_opinion.py`, `gateway_ensemble.py`)

After Claude responds, the bot queries one or more **blind co-equal voters** on the **same market context string** and the **same `SYSTEM` prompt** — none of them ever sees Claude's answer:

- **NVIDIA Nemotron-3-Super-120B** (`nvidia/nemotron-3-super-120b-a12b`, `config.py:177`) via the NVIDIA NIM OpenAI-compatible API (`get_second_opinion`, `second_opinion.py:130-170`). Master switch `use_second_opinion` (`config.py:176`); auto-off if `NVIDIA_API_KEY` is unset.
- **DeepSeek-V4-Pro** via the Pydantic AI Gateway → Doubleword (`gateway_ensemble.py`, model id at `config.py:188`). Master switch `use_ensemble` (`config.py:186`); no-op if the gateway key is unconfigured.

Key differences from the Claude call:

- For Nemotron, thinking is **explicitly disabled** (`"chat_template_kwargs": {"enable_thinking": False}`, `second_opinion.py:156`) — it must return the JSON directly to fit in the 30-second timeout (`config.py:179`) and 4,000-token budget. DeepSeek-V4-Pro instead reasons before answering (~73s typical; `ensemble_timeout=300s`, `config.py:190`).
- The response format is a plain JSON object requested via a prompt instruction, not a structured-output API feature.
- Temperature is 0.2 (deterministic) for both voters.
- If any voter is unavailable or fails for any reason, the bot proceeds on the remaining voters (Claude-only in the worst case) with no degradation in logic.

**Consensus rule — `apply_consensus()` (`second_opinion.py:19-99`):**

Claude always sets the direction. A voter can only **raise** confidence when it independently agrees with Claude's non-flat direction — it can never veto, reverse, or shrink. The boost uses the **mean of all agreeing voters' confidences** (`second_opinion.py:76`):

```
combined = claude_conf + (1 - claude_conf) × gain × mean(agreeing voters' confidences)
```

| Voter outcome | Effect |
|---|---|
| Agrees (same non-flat direction as Claude) | its confidence joins the mean; combined boosted as above |
| Disagrees (opposite direction) | excluded from the mean — no boost from it |
| Returns flat | excluded — no boost |
| Unavailable / failed | excluded — no boost |

The `gain` is `nemotron_consensus_gain = 0.5` (`config.py:178`); the gateway voters use `ensemble_gain = 0.5` (`config.py:189`). With a single agreeing voter this reduces to the original rule: if Claude says LONG at 0.50 and one voter says LONG at 0.80, combined `= 0.50 + (1 - 0.50) × 0.5 × 0.80 = 0.70`. Boosts are additive and clamped to [0, 1].

**Influence is gated PER SOURCE.** Every voter is always recorded and scored, but whether it actually moves the value the sizer uses is controlled separately: Nemotron influence defaults **on** (`second_opinion`/`influence`), the gateway voters (DeepSeek) default **off** (`ensemble_input`/`extra_influence`, `config.py:184`). `apply_consensus` therefore computes two numbers (`second_opinion.py:78-79`): `combined` (all agreeing voters, for display/logs) and `used` (only influence-enabled agreeing voters, what the sizer acts on). So by default DeepSeek is queried, logged, and hit-rate-scored but does **not** change sizing. The `boosted` flag is influence-aware — it is true only when an **influence-enabled** voter agreed (`second_opinion.py:80-82`).

---

## 3. From Decision to Order: The Execution Workflow

The engine runs a **5-second loop** (`loop_seconds`, `config.py:148`). Each tick executes in strict order of authority (`tick()`, `engine.py:151-283`):

```
Every 5 seconds (tick):
  │
  ├─ 1. RISK ASSESS  ──────────────────────────────────────── engine.py:154-160
  │        RiskEngine.assess() evaluates margin level, drawdown, leverage.
  │        If kill=True: flatten ALL positions, print, RETURN immediately.
  │        Nothing below this line runs while kill is active.
  │
  ├─ 2. STATE / METRICS  ──────────────────────────────────── engine.py:206
  │        StateTracker.update(equity): update return%, max drawdown, Sharpe.
  │
  ├─ 3. AI REFRESH — NON-BLOCKING (every 15 min, not every tick)  engine.py:208-237
  │        The LLM calls are SLOW (DeepSeek reasons ~73s), so the tick NEVER
  │        blocks on them. The refresh is split across ticks:
  │          a. _refresh_prep() (engine.py:353-373) — MAIN thread, fast: the one
  │             MT5 read (build_context → snapshot string) + control/override snap.
  │          b. _refresh_worker_into() (engine.py:430-432) — spawned as a DAEMON
  │             thread: get_ai_views() Claude call, get_second_opinion() Nemotron,
  │             get_ensemble_views() DeepSeek, then apply_consensus(). Network +
  │             pure logic only; touches no MT5 and no shared state.
  │          c. _apply_refresh_result() (engine.py:434-474) — installed on a LATER
  │             main-thread tick once the worker finishes: sets _views/_views_ts,
  │             regime, model scoring. On failure: doubling backoff (30s→300s).
  │        While the call is in flight, risk/reconcile/ensure-stops/trailing keep
  │        running every 5s on the latest installed views.
  │
  ├─ 4. RECONCILE (ONLY if the signal set changed)  ─────────── engine.py:241-256
  │        Anti-churn gate _signal_changed(): views must EXIST and views_ts must
  │        differ from last_acted_ts. Fires at most once per applied refresh.
  │          a. _build_targets() → sizer converts views → target signed lots
  │          b. graduated-drawdown risk_scale applied to every target (if <1.0)
  │          c. _derisk_only() (engine.py:482-497) if paused or allow_new_risk=False
  │          d. _reconcile() (engine.py:622-651) → diff current vs target per symbol
  │             • target=0 and held: _do_close() → market order to close
  │             • delta ≥ volume_min: _do_order() → market order BUY/SELL,
  │               opened CLEAN (no inline SL — see below), engine.py:823-840
  │
  ├─ 4.5 ENSURE STOPS (every tick)  ────────────────────────── engine.py:259-261
  │        _ensure_stops() (engine.py:676-709): for every open position with no
  │        broker SL, attach the SL_PCT failsafe via the SLTP action. This is what
  │        actually puts the catastrophe stop on each fresh fill — a naked position
  │        means possible liquidation = elimination.
  │        FX ±5%, METAL ±8%, CRYPTO ±15%  (SL_PCT, config.py:20)
  │
  └─ 5. TRAILING STOPS (every tick)  ───────────────────────── engine.py:264-265
           _update_trailing_stops() (engine.py:711-740): once a position is up by
           the ARM distance, ratchet the broker SL to trail behind price. SL only
           ever tightens, never below break-even.
           ARM  (TRAIL_ACTIVATE_PCT, config.py:24): FX 1.0%, METAL 2.0%, CRYPTO 4.0%
           TRAIL behind (TRAIL_PCT, config.py:28):   FX 0.5%, METAL 1.0%, CRYPTO 2.0%
```

**Why open clean, then attach the SL separately:** this broker REJECTS a market DEAL that carries an inline SL (ret 10006, observed live on metals; `engine.py:828-840`). So `_do_order()` opens with no stop and returns `0.0`, and `_ensure_stops()` (step 4.5, every tick) attaches the SL_PCT failsafe via the reliable SLTP action. Ensure only sets a stop when `sl == 0`, so it never loosens an already-trailed stop.

**Key anti-churn gate detail** (`_signal_changed()`, `engine.py:285-293`): returns `True` only when `self._views is not None and self._views_ts != self._last_acted_ts`. The `_views is not None` guard matters with the non-blocking refresh — `_views` is `None` during the first async refresh after a restart, and without the guard an empty target set would flatten the whole open book before the views land. After reconcile, `last_acted_ts` is set to `views_ts`, so reconcile fires **exactly once** per applied refresh regardless of how many 5-second ticks pass.

**Orders are always market orders** with immediate fill. The broker-side SL (`use_broker_sl=True`, `config.py:162`) is a catastrophe backstop set loose enough not to trip on normal volatility; the live kill-switch is the primary liquidation guard. `use_broker_sl` is load-bearing — the engine refuses to start live with it off (`engine.py:127-129`).

---

## 4. Sizing: From Views to Lots (`sizer.py`)

`build_targets()` (`sizer.py:51-100`) takes `{symbol: (direction_int, confidence)}` and returns `{symbol: target_signed_lots}`.

### The Aggression Dial

A single parameter `aggression` in `[0, 1]` (default **0.30**, `config.py:159`) interpolates the entire risk posture:

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
| Margin level too low | `margin_level < 150%` | `config.py:66`, `risk.py:92-94` |
| Drawdown hard backstop | peak-to-trough DD `≥ 40%` (2× `daily_max_drawdown`) | `config.py:81`, `risk.py:105-107` |

When either kill condition fires: all positions are closed immediately via `flatten_all()`. The tick returns without executing reconcile, sizer, or trailing-stop logic.

The competition stop-out is at `margin_level=30%`. The bot's kill-switch fires at **150%** — far above the competition's liquidation level, giving substantial buffer.

### Graduated Drawdown De-Risk (not a binary kill)

Drawdown is measured against the **trailing equity peak** (the scored metric), not the round's starting equity, and it scales the whole book down as DD deepens rather than flatten-and-freeze. It **re-arms automatically** as equity recovers and DD shrinks (`risk.py:96-115`; base limit `daily_max_drawdown = 0.20`, `config.py:81`):

| Peak-to-trough drawdown | Effect |
|---|---|
| `≥ 20%` (1× limit) | `risk_scale = 0.5` — every target halved; no new risk |
| `≥ 30%` (1.5× limit) | `risk_scale = 0.25` — every target quartered; no new risk |
| `≥ 40%` (2× limit) | KILL — flatten everything (hard backstop) |

`risk_scale < 1.0` is applied to every target in reconcile (`engine.py:244-246`) and also sets `allow_new_risk = False` (`risk.py:114-115`).

### Soft Guards (block new risk, don't close)

| Condition | Threshold | Effect | Source |
|---|---|---|---|
| Margin level warning | `margin_level < 250%` | `allow_new_risk = False` | `config.py:67`, `risk.py:118-120` |
| Gross leverage cap | `gross_leverage > 12×` | `allow_new_risk = False` | `config.py:70`, `risk.py:121-123` |
| Margin usage cap | `margin_usage > 60%` | `allow_new_risk = False` | `config.py:74`, `risk.py:124-126` |

(Concentration and net-directional are computed for display + the watch tripwires below but deliberately do **not** block new risk — `risk.py:127-134`.)

When `allow_new_risk=False`, `_derisk_only()` (`engine.py:482-497`) is applied: position increases and flips are suppressed; only reductions toward flat are allowed.

### Competition Tripwire Trackers (30-minute timers)

The risk engine tracks how long specific conditions have been continuously true. These are **warnings only** — they do not trigger the kill-switch but print to the console so a human operator can intervene (`risk.py:138-146`):

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

**BARUSD is explicitly excluded from trading** (`config.py:151-152`), with the reason documented in the config:

> "BARUSD excluded: max notional ~$786 (tiny contract + 100-lot cap) makes it untradeable in size — pure spread cost."

BARUSD remains in `ALL_SYMBOLS` for data-collection purposes but is filtered out of `EngineConfig.symbols` at startup. It is not sent to Claude and receives no AI view.

---

## 7. Strategies in the Code vs. in the Live Algorithm

Several classical strategies are **implemented and backtestable**, but in the default config only the AI path trades live. The integrated momentum scalper exists but is **OFF by default** (`ScalperConfig.enabled=False`, `config.py:91`) and only goes live after the operator explicitly assigns symbols, clears the arming gate, and arms it per symbol (`engine.py:569-611`). This is deliberate: the rule-based edges (momentum, mean-reversion) were built, then ruled out on the data, so the LLM became the live signal — but the code is kept for backtesting/validation.

| Strategy | Where it lives | Live? |
|---|---|---|
| **AI (Claude + ensemble) → sizer** | `ai_signal.py`, `sizer.py` | **Yes — the main engine** (`signal_source="ai"`, `config.py:158`; engine takes the AI branch at `engine.py:213`, sizing via `_build_targets`, `engine.py:295-321`) |
| **Momentum (rules)** | `strategy.py` `HybridStrategy`, `mode="momentum"` (`strategy.py:73-78`) | **No** — only the `"hybrid"` branch (`engine.py:296-299`) uses it; never reached with the default config |
| **Mean reversion (rules)** | `strategy.py` `mode="meanrev"` — flips the trend signal: `direction = -direction` ("fade the move", `strategy.py:79-80`) | **No** — hybrid/backtest only |
| **Crypto momentum / breakout** | `crypto_signal.py`, integrated scalper slot | **Off by default** — operator must assign + gate + arm before any live order (alts confirmed by BTC lead-lag) |
| **Mean-reversion "fade" scalper** | `scalp_signal.py`, `scalp_backtest.py` | **No** — dormant/reference only |

**Key facts to remember:**

- The default `signal_source = "ai"` (`config.py:158`) means the engine **never** calls `HybridStrategy.target_positions()` in normal operation — `HybridStrategy` is instantiated (`engine.py:102`) but only drives targets in the `"hybrid"` branch and in the backtester (`backtest.py:111`).
- So **mean reversion is NOT part of the live algorithm.** It exists as a flag (`StrategyConfig.mode="meanrev"`) for research only.
- The live scalper is explicitly momentum, *not* mean reversion. Its own docstring (`crypto_signal.py:3-5`): *"The fade scalper died because tiny mean-reversion edges drowned in spread… trade WITH momentum/breakouts, not against noise."*
- To test the rule-based strategies live you would set `signal_source="hybrid"` (and `StrategyConfig.mode`); nothing in the default config does this.

---

## Appendix: Cost Tracking

Every Claude call records token usage and an approximate USD cost (`ai_signal.py:26-33`): `$5/M input tokens, $25/M output tokens`. At the 15-minute cadence with 14 instruments, a single competition session incurs roughly one call every 900 seconds. Cost is written to the per-day decision log (`_write_decision_record`, `engine.py:653-673`) and printed to the console after each call (`ai_signal.py:166-167`).
