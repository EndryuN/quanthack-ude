# AI Decision Log + Nemotron Second Opinion — Design

**Date:** 2026-06-23
**Status:** Draft for review
**Author:** Andrew Nguyen (with Claude)

## 1. Goal

Two coupled capabilities for the live trading engine:

1. **Decision logging** — persist every AI refresh and the orders it produced to
   durable files, capturing the *what*, the *why* (per-symbol rationale, which is
   currently computed by the model but discarded), the resulting trades, and the
   account state at decision time. Output both a machine-readable record (JSONL)
   and a human-readable narrative (Markdown).

2. **Nemotron second opinion** — an independent confirmation signal from an
   NVIDIA Nemotron model (hosted, OpenAI-compatible API) that runs on the same
   market snapshot as Claude. Claude always sets direction; Nemotron can only
   *raise* conviction when it independently agrees. This is a consensus-aware
   conviction amplifier intended to improve Sharpe by sizing high-agreement
   trades larger, and it qualifies the project for the "Nemotron / NVIDIA open
   source ecosystem" award criterion.

The decision log doubles as the evaluation artifact: every record contains both
models' views and the agreement verdict, giving an audit trail and a
Claude-vs-Nemotron comparison.

## 2. Scope

**In scope**
- Decision logging in the **live engine** path (`bot/engine.py`).
- Nemotron second opinion as a parallel signal in the live path.
- Consensus combination logic (pure function).
- Config + env for the Nemotron provider.
- Graceful degradation: if Nemotron is unavailable, trade exactly as today
  (Claude-only), and note the failure in the log.

**Out of scope (separate future spec)**
- Running the Claude-vs-Nemotron comparison across the full backtest month
  (touches the separate `bot/ai_backtest.py` gateway/replay path).
- NemoClaw-on-Brev agentic second opinion.
- Any change to the sizer, risk engine, or trailing-stop logic.

## 3. Architecture

Two new self-contained modules plus thin wiring into the engine.

### 3.1 `bot/decision_log.py` — `DecisionLogger`
One job: take a decision record (a plain `dict`) and append it to today's two
files. Knows nothing about MT5 or the engine — hand it a dict, it writes.
Trivially unit-testable without a broker.

```
DecisionLogger(log_dir="logs")
  .log_decision(record: dict) -> None      # append JSONL line + MD section to today's files
```

- Files are per-day: `logs/ai_decisions_YYYY-MM-DD.jsonl` and `…/ai_decisions_YYYY-MM-DD.md`.
- Day is derived from the record's `ts` (not wall clock at write), so a record is
  always filed under its own date.
- Appends; never rewrites. Creates `logs/` on first write.
- A new `.md` file gets a one-line header (`# AI decisions — YYYY-MM-DD`); each
  record is appended as a section ending in `---`.

### 3.2 `bot/second_opinion.py` — Nemotron provider + consensus
- `get_second_opinion(context: str, symbols, model, *, timeout) -> (views, regime, usage)`
  - Sends the **same market context** Claude received to the Nemotron model via
    the OpenAI-compatible chat endpoint (`integrate.api.nvidia.com/v1`).
  - Returns `views = {symbol: (direction_int, confidence, rationale)}`, the
    Nemotron `regime` string, and `usage = {in_tokens, out_tokens, usd}`.
  - **Blind**: it is NOT shown Claude's answer, so it is a genuine independent
    opinion (no anchoring).
  - Uses `httpx` directly (consistent with `bot/gateway.py`'s no-OpenAI-SDK
    choice). Reuses `bot.ai_backtest.parse_views` for the symbol/direction
    parse where possible, extended to capture rationale.
- `apply_consensus(claude_views, claude_rationale, nemotron_views, *, gain) -> combined`
  - Pure function. For each symbol produces the final view + the metadata needed
    by the log (see §5).

### 3.3 Engine wiring (`bot/engine.py`)
Three small changes, as agreed:

1. **`bot.ai_signal.get_ai_views`** → returns `(views, mv, usage)` instead of
   `(views, mv)`, where `usage = {stop_reason, in_tokens, out_tokens, usd}`.
   This data is currently computed only to `print` (ai_signal.py:140-142).
   Callers updated: `engine._refresh_ai_views`, `test_ai.py`,
   `test_engine_ai.py` (one line each).
2. **`_reconcile`** → also return the list of orders placed (currently returns
   just a count). Each order: `{symbol, side, lots, cur, target, sl}`.
3. **`tick()`** → after reconcile, if a refresh happened this tick, assemble the
   record and call `self.logger.log_decision(...)`.

`_refresh_ai_views` gains the second-opinion call and the consensus merge:
- Get Claude views + `mv` (regime, rationale) + usage.
- If Nemotron enabled: get the second opinion on the same `context`.
- `apply_consensus(...)` → the final `self._views` fed to the sizer.
- Stash the per-symbol comparison + both regimes + both usages on the engine so
  `tick()` can build the record after reconcile.

## 4. Data flow

```
tick()
  └─ _refresh_ai_views()                       [every ~15 min]
       ├─ context = build_context(client, symbols)
       ├─ claude_views, mv, claude_usage = get_ai_views(...)        # primary
       ├─ nemo_views, nemo_regime, nemo_usage = get_second_opinion(context, …)  # blind, optional
       ├─ self._views = apply_consensus(claude_views, mv, nemo_views, gain)     # final, sized
       └─ stash comparison + regimes + usages
  └─ if _signal_changed():
       orders = _reconcile(targets)            # list of placed orders
  └─ if refreshed_this_tick:
       logger.log_decision(assemble_record(regimes, account, usages, comparison, orders))
```

## 5. Consensus rule

Asymmetric and additive — **Claude always sets direction; Nemotron only boosts on
agreement, never vetoes.**

Per symbol, given Claude `(dir_c, conf_c)` and Nemotron `(dir_n, conf_n)`:

- **Agree** (`dir_n == dir_c`, both non-flat):
  `final_conf = conf_c + (1 − conf_c) · gain · conf_n`
  → bounded in `[0,1]`, scaled by Nemotron's own confidence and remaining headroom.
- **Disagree, or Nemotron flat / missing:** `final_conf = conf_c` (unchanged).
- **Direction is always `dir_c`.**

`gain` default **0.5** (config `nemotron_consensus_gain`), tunable by backtest later.

Worked example: Claude AUDUSD short @0.45, Nemotron short @0.60, gain 0.5 →
`0.45 + 0.55·0.5·0.60 = 0.615`. Claude short @0.45, Nemotron long → stays `0.45`.

## 6. Record schema

```jsonc
{
  "ts": "2026-06-23T18:46:51",          // ISO; drives the day-file name
  "mode": "dry-run" | "live",
  "models": { "primary": "claude-opus-4-8",
              "second_opinion": "nvidia/nemotron-3-super-120b-a12b" | null },
  "regime": { "claude": "…", "nemotron": "…" | null },
  "account": { "equity", "gross_leverage", "margin_usage",
               "margin_level", "return_pct", "drawdown", "sharpe" },
  "cost": { "claude":   { "in", "out", "usd" },
            "nemotron": { "in", "out", "usd" } | null },
  "views": [ {
      "symbol", "direction",            // Claude's direction (the traded one)
      "confidence",                     // FINAL post-consensus confidence
      "claude":   { "direction", "confidence", "rationale" },
      "nemotron": { "direction", "confidence", "rationale" } | null,
      "agreement": "agree" | "disagree" | "nemotron_flat" | "second_opinion_unavailable",
      "boosted": true | false
  } ],                                  // includes flats
  "orders": [ { "symbol", "side", "lots", "cur", "target", "sl" } ]  // may be empty
}
```

A **failed Claude refresh** is logged as a minimal line:
`{ "ts", "event": "refresh_failed", "error": "…" }` (mirrors the existing
backoff print) plus a brief `.md` note.

## 7. File formats

### 7.1 JSONL (`logs/ai_decisions_YYYY-MM-DD.jsonl`)
One compact JSON object per line, schema as §6. Machine-readable; load with
`pandas.read_json(lines=True)` or line-by-line.

### 7.2 Markdown (`logs/ai_decisions_YYYY-MM-DD.md`)
One section per refresh:

```markdown
## 18:46:51 · DRY-RUN
**Regime (Claude):** Broad USD strength, risk-off.
**Regime (Nemotron):** USD bid; commodities heavy.
**Account:** eq=1,019,620 · lev=3.1x · margin=11% · ML=937% · sharpe=-0.19
**Cost:** Claude in=2494 out=1270 ~$0.044 · Nemotron in=2510 out=880 ~$0.000

| Symbol | Final | Claude | Nemotron | Consensus |
|--------|-------|--------|----------|-----------|
| AUDUSD | SHORT 0.62 | short 0.45 | short 0.60 | ✅ agree → boosted |
| USDJPY | LONG 0.40  | long 0.40  | flat —     | ⚪ no boost |
| EURUSD | SHORT 0.42 | short 0.42 | long 0.30  | ❌ disagree → Claude stands |

**Orders placed:**
- BUY 1.56 USDJPY  (+2.86 → +4.42)  SL=153.492

---
```

- Table sorted by final confidence, highest first.
- Flats **omitted** from the `.md` table (readability) but **kept** in the JSONL
  (completeness).

## 8. Configuration

Add to `EngineConfig` (`bot/config.py`):

```python
use_second_opinion: bool = True                 # master switch
nemotron_model: str = "nvidia/nemotron-3-super-120b-a12b"
nemotron_consensus_gain: float = 0.5            # agreement boost strength
second_opinion_timeout: float = 30.0            # seconds; failure → Claude-only
log_decisions: bool = True                      # master switch for the logger
log_dir: str = "logs"
```

Env (loaded by `bot/__init__.py`, like the existing keys):
- `NVIDIA_API_KEY` — the `nvapi-…` key from build.nvidia.com.
- `NVIDIA_BASE_URL` — defaults to `https://integrate.api.nvidia.com/v1`.

`.gitignore` gains `logs/`.

## 9. Error handling

The second opinion is **strictly additive** — its failure must never block or
change live trading beyond removing the boost:

- Nemotron call fails / times out / unparseable → `apply_consensus` runs with
  `nemotron_views = {}`; every symbol falls to `agreement:"second_opinion_unavailable"`,
  `boosted:false`, `final = conf_c`. The failure is recorded in the log
  (`cost.nemotron = null`, a note in `.md`).
- `NVIDIA_API_KEY` missing → second opinion silently disabled for the run
  (logged once at startup); behaves like `use_second_opinion = False`.
- Logger I/O error → caught and printed; must never crash the trading loop.
- Existing Claude backoff (ai refresh failure → exponential backoff) is unchanged.

## 10. Testing

- **`decision_log`** — unit test: feed crafted records (with/without Nemotron,
  with/without orders, a `refresh_failed` event), assert JSONL parses back to the
  input and the `.md` contains expected lines. Per-day file routing by `ts`. No
  MT5, no network.
- **`apply_consensus`** — unit test the pure function across all branches:
  agree (boost math, bound at 1.0), disagree, nemotron-flat, nemotron-missing.
- **`second_opinion`** — test the parser against a captured Nemotron JSON
  response (fixture); network call itself validated by a manual smoke script
  (`test_second_opinion.py`) gated on `NVIDIA_API_KEY`, mirroring `test_ai.py`.
- **Engine integration** — extend the existing dry-run smoke (`test_engine_ai.py`)
  to confirm a record is written and a second opinion is requested; with the key
  unset, confirm graceful Claude-only behavior.

## 11. Open items / future

- Backtest-month Claude-vs-Nemotron A/B (separate spec).
- Optional NemoClaw-on-Brev agentic second opinion / report generator.
- Tuning `nemotron_consensus_gain` from logged outcomes once enough data exists.
