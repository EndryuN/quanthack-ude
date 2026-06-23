# AI Decision Log + Nemotron Second Opinion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every live AI refresh + resulting orders to per-day JSONL/Markdown logs, and add a blind Nemotron second opinion that boosts (never vetoes) Claude's confidence on agreement.

**Architecture:** Two new self-contained modules — `bot/decision_log.py` (pure file I/O) and `bot/second_opinion.py` (Nemotron HTTP call + pure consensus merge) — wired into `bot/engine.py`. `get_ai_views` is extended to also return token/cost usage and accept a precomputed context (so Claude and Nemotron see the same snapshot). The second opinion is strictly additive: if it's unavailable, trading behaves exactly as today.

**Tech Stack:** Python 3.13, httpx (already a dep), pydantic, MetaTrader5, Anthropic SDK, NVIDIA hosted OpenAI-compatible API (`integrate.api.nvidia.com/v1`).

## Global Constraints

- **No new third-party dependencies.** Use only what's in `requirements.txt` (httpx, numpy, pandas, pyarrow, pydantic, anthropic, MetaTrader5).
- **Test convention:** standalone scripts named `test_*.py` at repo root, run with `python test_x.py`, using plain `assert` and printing `OK` on success. No pytest (it is not a dependency).
- **Second opinion is strictly additive:** a missing `NVIDIA_API_KEY`, a network/parse failure, or a timeout must degrade to Claude-only and must never crash the trading loop.
- **Direction authority:** Claude always sets direction; Nemotron can only raise confidence on agreement (`final = conf_c + (1−conf_c)·gain·conf_n`), never veto. `gain` default `0.5`.
- **Files:** per-day `logs/ai_decisions_YYYY-MM-DD.jsonl` and `…/.md`; append-only; `logs/` is gitignored.
- **Platform:** Windows / PowerShell. Engine smoke tests require MetaTrader 5 running and connected. Network smokes are gated on the relevant API key being set.
- **Model id:** `nvidia/nemotron-3-super-120b-a12b` (configurable).

---

### Task 1: Config fields + gitignore

**Files:**
- Modify: `bot/config.py` (EngineConfig, before `CONFIG = EngineConfig()`)
- Modify: `.gitignore`
- Test: `test_config_fields.py` (create)

**Interfaces:**
- Produces: `CONFIG.log_decisions: bool`, `CONFIG.log_dir: str`, `CONFIG.use_second_opinion: bool`, `CONFIG.nemotron_model: str`, `CONFIG.nemotron_consensus_gain: float`, `CONFIG.second_opinion_timeout: float`.

- [ ] **Step 1: Write the failing test**

Create `test_config_fields.py`:
```python
"""Config carries the new logging + second-opinion knobs with sane defaults."""
from bot.config import CONFIG

assert CONFIG.log_decisions is True
assert CONFIG.log_dir == "logs"
assert CONFIG.use_second_opinion is True
assert CONFIG.nemotron_model.startswith("nvidia/")
assert 0.0 <= CONFIG.nemotron_consensus_gain <= 1.0
assert CONFIG.second_opinion_timeout > 0
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_config_fields.py`
Expected: `AttributeError: 'EngineConfig' object has no attribute 'log_decisions'`

- [ ] **Step 3: Add the config fields**

In `bot/config.py`, inside `@dataclass class EngineConfig`, after the `use_trailing_stop` line and before the class ends:
```python
    # --- AI decision logging ---
    log_decisions: bool = True                # write per-day decision logs
    log_dir: str = "logs"                     # directory for ai_decisions_*.{jsonl,md}

    # --- Nemotron second opinion (NVIDIA hosted, OpenAI-compatible API) ---
    use_second_opinion: bool = True           # master switch (auto-off if no key)
    nemotron_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nemotron_consensus_gain: float = 0.5      # agreement boost strength, [0,1]
    second_opinion_timeout: float = 30.0      # seconds; failure -> Claude-only
```

- [ ] **Step 4: Add `logs/` to .gitignore**

In `.gitignore`, under the `# runtime state` section (after `state/`), add:
```
logs/
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python test_config_fields.py`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add bot/config.py .gitignore test_config_fields.py
git commit -m "feat: config knobs for decision logging + Nemotron second opinion"
```

---

### Task 2: `cost_usd` helper + `get_ai_views` returns usage and accepts context

**Files:**
- Modify: `bot/ai_signal.py` (add `cost_usd`; change `get_ai_views`)
- Modify: `test_ai.py:22` (unpack 3-tuple)
- Modify: `bot/engine.py:128` (unpack 3-tuple — minimal, full wiring in Task 6)
- Test: `test_cost_usd.py` (create)

**Interfaces:**
- Produces: `cost_usd(in_tokens: int, out_tokens: int) -> float`.
- Produces: `get_ai_views(client, symbols, model=MODEL, effort="high", context=None) -> tuple[dict, MarketView, dict]` where the third element is `usage = {"stop_reason": str, "in_tokens": int, "out_tokens": int, "usd": float}`.

- [ ] **Step 1: Write the failing test**

Create `test_cost_usd.py`:
```python
"""Pure pricing helper: Claude Opus $5/M in, $25/M out."""
from bot.ai_signal import cost_usd

assert cost_usd(0, 0) == 0.0
assert abs(cost_usd(1_000_000, 0) - 5.0) < 1e-9
assert abs(cost_usd(0, 1_000_000) - 25.0) < 1e-9
assert abs(cost_usd(2494, 1357) - (2494 * 5 + 1357 * 25) / 1e6) < 1e-12
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_cost_usd.py`
Expected: `ImportError: cannot import name 'cost_usd'`

- [ ] **Step 3: Add `cost_usd` to `bot/ai_signal.py`**

After the `MODEL = "claude-opus-4-8"` line:
```python
# Claude Opus pricing (USD per token): $5 / 1M input, $25 / 1M output
_USD_PER_IN = 5 / 1e6
_USD_PER_OUT = 25 / 1e6


def cost_usd(in_tokens: int, out_tokens: int) -> float:
    """Approximate USD cost of a Claude Opus call from token counts."""
    return in_tokens * _USD_PER_IN + out_tokens * _USD_PER_OUT
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_cost_usd.py`
Expected: `OK`

- [ ] **Step 5: Change `get_ai_views` signature, context param, and return**

In `bot/ai_signal.py`, change the signature line:
```python
def get_ai_views(client: MT5Client, symbols, model: str = MODEL,
                 effort: str = "high", context: str | None = None) -> tuple[dict, MarketView, dict]:
    """Returns ({symbol: (direction_int, confidence)}, raw MarketView, usage)."""
```

Replace the `context = build_context(client, symbols)` line with:
```python
    if context is None:
        context = build_context(client, symbols)
```

Replace the usage print block:
```python
    # surface stop reason + token usage for cost/health visibility
    u = resp.usage
    print(f"        [api] stop={resp.stop_reason} in={u.input_tokens} "
          f"out={u.output_tokens} (~${(u.input_tokens*5 + u.output_tokens*25)/1e6:.3f})")
```
with:
```python
    # surface stop reason + token usage for cost/health visibility
    u = resp.usage
    usage = {"stop_reason": resp.stop_reason, "in_tokens": u.input_tokens,
             "out_tokens": u.output_tokens,
             "usd": cost_usd(u.input_tokens, u.output_tokens)}
    print(f"        [api] stop={usage['stop_reason']} in={usage['in_tokens']} "
          f"out={usage['out_tokens']} (~${usage['usd']:.3f})")
```

Change the final `return views, mv` to:
```python
    return views, mv, usage
```

- [ ] **Step 6: Update the two call sites to unpack 3 values**

In `test_ai.py` line 22:
```python
views, mv, _usage = get_ai_views(c, ALL_SYMBOLS, effort=CONFIG.ai_effort)
```

In `bot/engine.py` (currently lines 128-129), change:
```python
            views, mv = get_ai_views(self.client, self.cfg.symbols,
                                     effort=self.cfg.ai_effort)
```
to:
```python
            views, mv, _usage = get_ai_views(self.client, self.cfg.symbols,
                                              effort=self.cfg.ai_effort)
```

- [ ] **Step 7: Verify no other callers**

Run: `git grep -n "get_ai_views"`
Expected: matches only in `bot/ai_signal.py` (def), `bot/engine.py`, `test_ai.py`. If any other file unpacks 2 values, update it to 3.

- [ ] **Step 8: Smoke the engine AI path (requires MT5 + ANTHROPIC_API_KEY)**

Run: `python test_engine_ai.py`
Expected: prints `AI views refreshed | regime: …` then `OK — production AI path validated, no orders sent.` (Confirms the 3-tuple change didn't break the live path.)

- [ ] **Step 9: Commit**

```bash
git add bot/ai_signal.py bot/engine.py test_ai.py test_cost_usd.py
git commit -m "feat: get_ai_views returns usage + accepts precomputed context"
```

---

### Task 3: `DecisionLogger` (pure per-day file writer)

**Files:**
- Create: `bot/decision_log.py`
- Test: `test_decision_log.py` (create)

**Interfaces:**
- Produces: `DecisionLogger(log_dir="logs")` with `.log_decision(record: dict) -> None`.
- Consumes the record schema produced by Task 6 (see code below for exact keys).

- [ ] **Step 1: Write the failing test**

Create `test_decision_log.py`:
```python
"""DecisionLogger round-trips a record to JSONL and renders readable Markdown,
appends across calls, routes by day, and never crashes the caller."""
import json
import os
import tempfile

from bot.decision_log import DecisionLogger

d = tempfile.mkdtemp()
log = DecisionLogger(log_dir=d)

rec = {
    "ts": "2026-06-23T18:46:51", "mode": "dry-run",
    "models": {"primary": "claude-opus-4-8",
               "second_opinion": "nvidia/nemotron-3-super-120b-a12b"},
    "regime": {"claude": "USD strength", "nemotron": "USD bid"},
    "account": {"equity": 1019620, "gross_leverage": 3.1, "margin_usage": 0.11,
                "margin_level": 937, "return_pct": 0.0196, "drawdown": 0.001,
                "sharpe": -0.19},
    "cost": {"claude": {"in": 2494, "out": 1270, "usd": 0.044},
             "nemotron": {"in": 2510, "out": 880, "usd": 0.0}},
    "views": [
        {"symbol": "AUDUSD", "direction": "short", "confidence": 0.62,
         "claude": {"direction": "short", "confidence": 0.45, "rationale": "x"},
         "nemotron": {"direction": "short", "confidence": 0.60, "rationale": "y"},
         "agreement": "agree", "boosted": True},
        {"symbol": "EURCHF", "direction": "flat", "confidence": 0.0,
         "claude": {"direction": "flat", "confidence": 0.0, "rationale": "x"},
         "nemotron": {"direction": "flat", "confidence": 0.0, "rationale": "y"},
         "agreement": "nemotron_flat", "boosted": False},
    ],
    "orders": [{"symbol": "USDJPY", "side": "buy", "lots": 1.56,
                "cur": 2.86, "target": 4.42, "sl": 153.492}],
}
log.log_decision(rec)

jpath = os.path.join(d, "ai_decisions_2026-06-23.jsonl")
mpath = os.path.join(d, "ai_decisions_2026-06-23.md")
assert os.path.exists(jpath) and os.path.exists(mpath)

back = [json.loads(l) for l in open(jpath, encoding="utf-8")]
assert len(back) == 1 and back[0] == rec, "JSONL round-trips the exact record"

md = open(mpath, encoding="utf-8").read()
assert "## 18:46:51 · DRY-RUN" in md
assert "AUDUSD" in md and "agree" in md
assert "EURCHF" not in md, "flats omitted from MD table"
assert "BUY 1.56 USDJPY" in md

# failed-refresh event appends to same day file; header written once
log.log_decision({"ts": "2026-06-23T19:00:00", "event": "refresh_failed",
                  "error": "API down"})
back = [json.loads(l) for l in open(jpath, encoding="utf-8")]
assert len(back) == 2, "appends, never overwrites"
md = open(mpath, encoding="utf-8").read()
assert "REFRESH FAILED" in md and "API down" in md
assert md.count("# AI decisions — 2026-06-23") == 1

print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_decision_log.py`
Expected: `ModuleNotFoundError: No module named 'bot.decision_log'`

- [ ] **Step 3: Implement `bot/decision_log.py`**

```python
"""Append-only per-day logger for AI decisions. One record per AI refresh,
written as a compact JSONL line and a readable Markdown section. Pure I/O:
hand it a record dict, it writes. No MT5, no network dependency."""
from __future__ import annotations

import json
import os


class DecisionLogger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir

    def _paths(self, ts: str):
        day = ts[:10]  # 'YYYY-MM-DD' from an ISO timestamp
        base = os.path.join(self.log_dir, f"ai_decisions_{day}")
        return base + ".jsonl", base + ".md", day

    def log_decision(self, record: dict) -> None:
        """Append `record` to today's JSONL + Markdown files. Swallows I/O
        errors (prints a note) so logging can never crash the trading loop."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            jsonl_path, md_path, day = self._paths(record["ts"])
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            new_md = not os.path.exists(md_path)
            with open(md_path, "a", encoding="utf-8") as f:
                if new_md:
                    f.write(f"# AI decisions — {day}\n\n")
                f.write(self._render_md(record))
        except OSError as e:
            print(f"   [log] decision-log write failed: {e}")

    def _render_md(self, record: dict) -> str:
        t = record["ts"][11:]  # HH:MM:SS
        if record.get("event") == "refresh_failed":
            return (f"## {t} · REFRESH FAILED\n"
                    f"{record.get('error', '')}\n\n---\n\n")

        reg = record["regime"]
        acct = record["account"]
        cost = record["cost"]
        lines = [f"## {t} · {record['mode'].upper()}"]
        lines.append(f"**Regime (Claude):** {reg.get('claude', '')}")
        if reg.get("nemotron"):
            lines.append(f"**Regime (Nemotron):** {reg['nemotron']}")
        lines.append(
            f"**Account:** eq={acct['equity']:,.0f} · lev={acct['gross_leverage']:.1f}x · "
            f"margin={acct['margin_usage'] * 100:.0f}% · ML={acct['margin_level']:.0f}% · "
            f"sharpe={acct['sharpe']:.2f}")
        cc = cost.get("claude") or {}
        costline = (f"**Cost:** Claude in={cc.get('in', 0)} out={cc.get('out', 0)} "
                    f"~${cc.get('usd', 0):.3f}")
        cn = cost.get("nemotron")
        if cn:
            costline += (f" · Nemotron in={cn.get('in', 0)} out={cn.get('out', 0)} "
                         f"~${cn.get('usd', 0):.3f}")
        else:
            costline += " · Nemotron unavailable"
        lines.append(costline)
        lines.append("")

        rows = [v for v in record["views"] if v["direction"] != "flat"]
        rows.sort(key=lambda v: -v["confidence"])
        if rows:
            lines.append("| Symbol | Final | Claude | Nemotron | Consensus |")
            lines.append("|--------|-------|--------|----------|-----------|")
            verdicts = {
                "disagree": "❌ disagree → Claude stands",
                "nemotron_flat": "⚪ no boost",
                "second_opinion_unavailable": "⚪ no second opinion",
            }
            for v in rows:
                final = f"{v['direction'].upper()} {v['confidence']:.2f}"
                c = v["claude"]
                cl = f"{c['direction']} {c['confidence']:.2f}"
                n = v.get("nemotron")
                ns = f"{n['direction']} {n['confidence']:.2f}" if n else "—"
                if v["agreement"] == "agree":
                    verdict = "✅ agree → boosted" if v["boosted"] else "✅ agree"
                else:
                    verdict = verdicts.get(v["agreement"], v["agreement"])
                lines.append(f"| {v['symbol']} | {final} | {cl} | {ns} | {verdict} |")
            lines.append("")

        lines.append("**Orders placed:**")
        orders = record.get("orders", [])
        if orders:
            for o in orders:
                if o["side"] == "close":
                    lines.append(f"- CLOSE {o['symbol']}")
                else:
                    sl = f"  SL={o['sl']}" if o.get("sl") else ""
                    lines.append(f"- {o['side'].upper()} {o['lots']} {o['symbol']}  "
                                 f"({o['cur']:+.2f} → {o['target']:+.2f}){sl}")
        else:
            lines.append("- (none)")
        lines += ["", "---", ""]
        return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_decision_log.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add bot/decision_log.py test_decision_log.py
git commit -m "feat: DecisionLogger — per-day JSONL + Markdown decision records"
```

---

### Task 4: `apply_consensus` (pure merge rule)

**Files:**
- Create: `bot/second_opinion.py` (consensus function only this task)
- Test: `test_consensus.py` (create)

**Interfaces:**
- Produces: `apply_consensus(claude_views, claude_rationale, nemotron_views, *, gain=0.5) -> tuple[dict, list]`
  - `claude_views: {sym: (dir_int, conf)}`
  - `claude_rationale: {sym: str}`
  - `nemotron_views: {sym: (dir_int, conf, rationale)}` or `{}` if unavailable
  - returns `(sizer_views: {sym: (dir_int, final_conf)}, view_records: list[dict])`
  - each `view_record` matches the decision-log `views[]` schema.

- [ ] **Step 1: Write the failing test**

Create `test_consensus.py`:
```python
"""Consensus: Claude sets direction; Nemotron only boosts on agreement."""
from bot.second_opinion import apply_consensus

cr = {"AUDUSD": "a", "USDJPY": "b", "EURUSD": "c", "GBPUSD": "d"}

# agree -> boost: 0.45 + (1-0.45)*0.5*0.60 = 0.615
sv, recs = apply_consensus({"AUDUSD": (-1, 0.45)}, cr,
                           {"AUDUSD": (-1, 0.60, "agree")}, gain=0.5)
assert abs(sv["AUDUSD"][1] - 0.615) < 1e-9 and sv["AUDUSD"][0] == -1
assert recs[0]["agreement"] == "agree" and recs[0]["boosted"] is True
assert recs[0]["nemotron"]["direction"] == "short"

# disagree -> unchanged
sv, recs = apply_consensus({"EURUSD": (-1, 0.42)}, cr,
                           {"EURUSD": (1, 0.30, "x")}, gain=0.5)
assert sv["EURUSD"] == (-1, 0.42)
assert recs[0]["agreement"] == "disagree" and recs[0]["boosted"] is False

# nemotron flat -> unchanged
sv, recs = apply_consensus({"USDJPY": (1, 0.40)}, cr,
                           {"USDJPY": (0, 0.0, "x")}, gain=0.5)
assert sv["USDJPY"] == (1, 0.40) and recs[0]["agreement"] == "nemotron_flat"

# second opinion unavailable -> unchanged, nemotron None
sv, recs = apply_consensus({"GBPUSD": (-1, 0.38)}, cr, {}, gain=0.5)
assert sv["GBPUSD"] == (-1, 0.38)
assert recs[0]["agreement"] == "second_opinion_unavailable"
assert recs[0]["nemotron"] is None

# boost stays bounded at 1.0
sv, _ = apply_consensus({"AUDUSD": (1, 0.95)}, cr,
                        {"AUDUSD": (1, 1.0, "x")}, gain=0.5)
assert sv["AUDUSD"][1] <= 1.0

print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_consensus.py`
Expected: `ModuleNotFoundError: No module named 'bot.second_opinion'`

- [ ] **Step 3: Implement `apply_consensus` in `bot/second_opinion.py`**

Create `bot/second_opinion.py`:
```python
"""Nemotron second opinion via NVIDIA's hosted OpenAI-compatible API, plus the
pure consensus rule that merges it with Claude's primary view.

Claude always sets direction; Nemotron can only RAISE confidence when it
independently agrees — it never vetoes. The second opinion is strictly
additive: if it is unavailable, trading proceeds exactly as Claude-only."""
from __future__ import annotations

_DIR_STR = {1: "long", -1: "short", 0: "flat"}


def apply_consensus(claude_views, claude_rationale, nemotron_views, *, gain=0.5):
    """Merge Claude (primary, authoritative direction) with Nemotron (booster).

    Returns (sizer_views, view_records):
      sizer_views:  {sym: (dir_int, final_conf)}  -- fed to the sizer
      view_records: list of dicts in the decision-log `views[]` shape
    """
    available = bool(nemotron_views)
    sizer_views = {}
    records = []
    for sym, (dc, cc) in claude_views.items():
        nv = nemotron_views.get(sym) if available else None
        final = cc
        boosted = False
        if not available:
            agreement = "second_opinion_unavailable"
        elif nv is None or nv[0] == 0:
            agreement = "nemotron_flat"
        elif nv[0] == dc and dc != 0:
            agreement = "agree"
            final = cc + (1.0 - cc) * gain * nv[1]
            boosted = True
        else:
            agreement = "disagree"

        final = max(0.0, min(1.0, final))
        sizer_views[sym] = (dc, final)
        records.append({
            "symbol": sym,
            "direction": _DIR_STR[dc],
            "confidence": round(final, 4),
            "claude": {"direction": _DIR_STR[dc], "confidence": cc,
                       "rationale": claude_rationale.get(sym, "")},
            "nemotron": ({"direction": _DIR_STR[nv[0]], "confidence": nv[1],
                          "rationale": nv[2]} if nv is not None else None),
            "agreement": agreement,
            "boosted": boosted,
        })
    return sizer_views, records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_consensus.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add bot/second_opinion.py test_consensus.py
git commit -m "feat: apply_consensus — Claude-primary, Nemotron-boosts merge rule"
```

---

### Task 5: `get_second_opinion` + parser (Nemotron HTTP call)

**Files:**
- Modify: `bot/second_opinion.py` (add parser + network call)
- Test: `test_second_opinion.py` (create — pure parser always; network smoke gated on `NVIDIA_API_KEY`)

**Interfaces:**
- Produces: `parse_second_opinion(content: str, symbols) -> tuple[dict, str]` → `({sym: (dir_int, conf, rationale)}, regime)`.
- Produces: `get_second_opinion(context: str, symbols, model: str, *, timeout=30.0) -> tuple[dict, str, dict]` → `(views, regime, usage)` where `usage = {"in": int, "out": int, "usd": float}`; raises on any failure so the engine can degrade to Claude-only.

- [ ] **Step 1: Write the failing test**

Create `test_second_opinion.py`:
```python
"""Parser tolerates code fences/extra prose, clamps confidence, drops unknown
symbols. Network smoke runs only when NVIDIA_API_KEY is set."""
import os

from bot.second_opinion import parse_second_opinion

syms = ["EURUSD", "AUDUSD", "USDJPY"]
content = '''```json
{"regime":"USD strength","views":[
  {"symbol":"EURUSD","direction":"short","confidence":0.45,"rationale":"x"},
  {"symbol":"AUDUSD","direction":"SHORT","confidence":1.4,"rationale":"y"},
  {"symbol":"ZZZ","direction":"long","confidence":0.9,"rationale":"ignored"}]}
```'''
views, regime = parse_second_opinion(content, syms)
assert regime == "USD strength"
assert views["EURUSD"] == (-1, 0.45, "x")
assert views["AUDUSD"][0] == -1 and views["AUDUSD"][1] == 1.0   # clamped to 1.0
assert "ZZZ" not in views                                       # not requested
assert "USDJPY" not in views                                    # omitted by model
print("parser OK")

if os.getenv("NVIDIA_API_KEY"):
    import bot  # noqa: F401  -- .env load
    from bot.client import MT5Client
    from bot.ai_signal import build_context
    from bot.config import CONFIG
    from bot.second_opinion import get_second_opinion

    c = MT5Client(magic=CONFIG.magic)
    c.connect()
    ctx = build_context(c, CONFIG.symbols)
    v, reg, usage = get_second_opinion(ctx, CONFIG.symbols, CONFIG.nemotron_model)
    print(f"regime: {reg}")
    print(f"rated {len(v)} symbols | tokens in={usage['in']} out={usage['out']}")
    assert isinstance(v, dict)
    c.shutdown()
    print("network smoke OK")
else:
    print("(NVIDIA_API_KEY unset — skipped network smoke)")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_second_opinion.py`
Expected: `ImportError: cannot import name 'parse_second_opinion'`

- [ ] **Step 3: Add the parser and network call to `bot/second_opinion.py`**

Add these imports at the top of `bot/second_opinion.py` (below the docstring):
```python
import json
import os

import httpx

from .ai_signal import SYSTEM, _DIR
```

Append to `bot/second_opinion.py`:
```python
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL",
                            "https://integrate.api.nvidia.com/v1")


def parse_second_opinion(content: str, symbols):
    """Parse a Nemotron JSON response into ({sym:(dir,conf,rationale)}, regime).
    Tolerant of ```code fences``` and surrounding prose."""
    requested = set(symbols)
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("no JSON object in second-opinion response")
    obj = json.loads(text[i:j + 1])
    regime = str(obj.get("regime", ""))
    views = {}
    for v in obj.get("views", []):
        sym = v.get("symbol")
        if sym in requested:
            d = _DIR.get(str(v.get("direction", "flat")).lower(), 0)
            conf = max(0.0, min(1.0, float(v.get("confidence", 0.0))))
            views[sym] = (d, conf, str(v.get("rationale", "")))
    return views, regime


def get_second_opinion(context: str, symbols, model: str, *, timeout: float = 30.0):
    """Blind Nemotron second opinion on the same market context Claude saw.
    Returns (views, regime, usage). Raises on any failure (missing key, HTTP
    error, unparseable body) so the caller degrades to Claude-only."""
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY not set")
    user = (
        f"Instruments (rate exactly these {len(symbols)}): {', '.join(symbols)}\n\n"
        f"Recent multi-timeframe action:\n{context}\n\n"
        "Respond with ONLY a JSON object of this exact shape, no prose:\n"
        '{"regime": "<one line>", "views": [{"symbol": "EURUSD", '
        '"direction": "long|short|flat", "confidence": 0.0, '
        '"rationale": "<one concise sentence>"}]}'
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{NVIDIA_BASE_URL.rstrip('/')}/chat/completions",
                   headers={"Authorization": f"Bearer {key}",
                            "Content-Type": "application/json"},
                   json=body)
        r.raise_for_status()
        resp = r.json()
    content = resp["choices"][0]["message"]["content"]
    views, regime = parse_second_opinion(content, symbols)
    u = resp.get("usage", {}) or {}
    usage = {"in": u.get("prompt_tokens", 0),
             "out": u.get("completion_tokens", 0), "usd": 0.0}
    return views, regime, usage
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_second_opinion.py`
Expected: `parser OK` then either `network smoke OK` (if `NVIDIA_API_KEY` set) or `(NVIDIA_API_KEY unset — skipped network smoke)`.

- [ ] **Step 5: Commit**

```bash
git add bot/second_opinion.py test_second_opinion.py
git commit -m "feat: get_second_opinion — Nemotron call via NVIDIA hosted API"
```

---

### Task 6: Engine wiring (second opinion + logging)

**Files:**
- Modify: `bot/engine.py` (imports, `__init__`, `start`, `tick`, `_refresh_ai_views`, `_reconcile`, `_do_order`, add `_write_decision_record`, `_iso`)
- Modify: `test_engine_ai.py` (assert a record is written)

**Interfaces:**
- Consumes: `DecisionLogger.log_decision` (Task 3), `apply_consensus` + `get_second_opinion` (Tasks 4-5), `get_ai_views` 3-tuple + `build_context` (Task 2).
- Produces: a decision record per successful refresh, written via the logger.

- [ ] **Step 1: Add imports and `__init__` state**

In `bot/engine.py`, add to the top imports (after `import time`):
```python
import os
```
Add to the `from .config import …` line area, a new import:
```python
from .decision_log import DecisionLogger
```

In `Engine.__init__`, after `self._ai_backoff = 0.0` add:
```python
        self.logger = DecisionLogger(cfg.log_dir) if cfg.log_decisions else None
        self._pending_log = None
        self._second_opinion_off = not bool(os.getenv("NVIDIA_API_KEY"))
```

- [ ] **Step 2: Announce second-opinion status in `start`**

In `Engine.start`, after the `print(f"[engine] connected. mode={mode}")` line:
```python
        if self.cfg.use_second_opinion and self._second_opinion_off:
            print("[engine] NVIDIA_API_KEY not set — second opinion disabled (Claude-only).")
```

- [ ] **Step 3: Replace `_refresh_ai_views`**

Replace the entire `_refresh_ai_views` method with:
```python
    def _refresh_ai_views(self):
        from .ai_signal import get_ai_views, build_context
        from .second_opinion import apply_consensus
        self._last_ai_attempt = time.time()
        try:
            context = build_context(self.client, self.cfg.symbols)
            claude_views, mv, claude_usage = get_ai_views(
                self.client, self.cfg.symbols, effort=self.cfg.ai_effort,
                context=context)
            claude_rationale = {v.symbol: v.rationale for v in mv.views}

            nemo_views, nemo_regime, nemo_usage = {}, None, None
            if self.cfg.use_second_opinion and not self._second_opinion_off:
                from .second_opinion import get_second_opinion
                try:
                    nemo_views, nemo_regime, nemo_usage = get_second_opinion(
                        context, self.cfg.symbols, self.cfg.nemotron_model,
                        timeout=self.cfg.second_opinion_timeout)
                except Exception as e:
                    print(f"        [2nd] Nemotron second opinion failed: {e}")
                    nemo_views, nemo_regime, nemo_usage = {}, None, None

            sizer_views, view_records = apply_consensus(
                claude_views, claude_rationale, nemo_views,
                gain=self.cfg.nemotron_consensus_gain)

            self._views = sizer_views
            self._views_ts = time.time()
            self._regime = mv.regime
            self._ai_backoff = 0.0
            self._pending_log = {
                "models": {"primary": "claude-opus-4-8",
                           "second_opinion": (self.cfg.nemotron_model
                                              if nemo_usage else None)},
                "regime": {"claude": mv.regime, "nemotron": nemo_regime},
                "cost": {"claude": {"in": claude_usage["in_tokens"],
                                    "out": claude_usage["out_tokens"],
                                    "usd": claude_usage["usd"]},
                         "nemotron": ({"in": nemo_usage["in"],
                                       "out": nemo_usage["out"],
                                       "usd": nemo_usage["usd"]}
                                      if nemo_usage else None)},
                "views": view_records,
            }
            active = {s: v for s, v in sizer_views.items() if v[0] != 0 and v[1] > 0}
            print(f"[{_ts()}] AI views refreshed | regime: {mv.regime}")
            for s, (d, c) in sorted(active.items(), key=lambda x: -x[1][1]):
                side = "LONG" if d > 0 else "SHORT"
                print(f"        {s:7s} {side:5s} conf={c:.2f}")
        except Exception as e:
            self._ai_backoff = min(max(self._ai_backoff * 2, self._AI_BACKOFF_MIN),
                                   self._AI_BACKOFF_MAX)
            age = time.time() - self._views_ts if self._views_ts else -1
            print(f"[{_ts()}] AI refresh FAILED: {e} | retry in "
                  f"{self._ai_backoff:.0f}s (views age={age:.0f}s)")
            if self.logger:
                self.logger.log_decision({"ts": _iso(), "event": "refresh_failed",
                                          "error": str(e)})
            if self._views is None:
                self._views = {}
```

- [ ] **Step 4: Make `_reconcile` collect orders; make `_do_order` return its SL**

Replace the `_reconcile` method with:
```python
    def _reconcile(self, targets):
        orders = []
        for s, target in targets.items():
            pos = self.client.position_for(s)
            cur = pos.signed_lots if pos else 0.0
            if not self.strategy.should_rebalance(cur, target, 0.0):
                continue
            delta = _round_to_step(s, target - cur)
            info = mt5.symbol_info(s)
            vmin = info.volume_min if info else 0.01
            if abs(delta) < vmin:
                if target == 0 and cur != 0:
                    self._do_close(s)
                    orders.append({"symbol": s, "side": "close",
                                   "lots": round(abs(cur), 4), "cur": cur,
                                   "target": 0.0, "sl": 0.0})
                continue
            if target == 0 and cur != 0:
                self._do_close(s)
                orders.append({"symbol": s, "side": "close",
                               "lots": round(abs(cur), 4), "cur": cur,
                               "target": 0.0, "sl": 0.0})
            else:
                sl = self._do_order(s, abs(delta), delta > 0, cur, target)
                orders.append({"symbol": s, "side": "buy" if delta > 0 else "sell",
                               "lots": abs(delta), "cur": cur, "target": target,
                               "sl": sl})
        return orders
```

In `_do_order`, change both return points to return the SL used. After the dry-run branch's `print(...)` line, change `return` to:
```python
            return sl
```
and at the very end of `_do_order` (after the live `print(...)`), add:
```python
        return sl
```

- [ ] **Step 5: Update `tick` to use the orders list and write the record**

In `tick`, replace the AI-refresh + reconcile + status section (current lines ~78-103) so it reads:
```python
        # 3. refresh AI views on cadence
        views_ts_before = self._views_ts
        if self.cfg.signal_source == "ai":
            now = time.time()
            due = self._views is None or (now - self._views_ts) > self.cfg.ai_refresh_seconds
            if due and (now - self._last_ai_attempt) >= self._ai_backoff:
                self._refresh_ai_views()
        refreshed = self._views_ts != views_ts_before

        # 4. reconcile ONLY when the signal set has changed
        orders = []
        if self._signal_changed():
            targets = self._build_targets(acct)
            if not snap.allow_new_risk:
                targets = self._derisk_only(targets)
            orders = self._reconcile(targets)
            self._last_acted_ts = self._views_ts
        n_orders = len(orders)

        # 5. trail stops into profit on meaningful winners (every loop)
        if self.cfg.use_trailing_stop:
            self._update_trailing_stops()

        # 6. write the decision record for this refresh (after reconcile)
        if refreshed and self.logger and self._pending_log:
            self._write_decision_record(acct, snap, m, orders)

        self._print_status(acct, snap, m, n_orders)
```

- [ ] **Step 6: Add `_write_decision_record` and `_iso`**

Add this method to `Engine` (e.g. after `_reconcile`):
```python
    def _write_decision_record(self, acct, snap, m, orders):
        parts = self._pending_log
        record = {
            "ts": _iso(),
            "mode": "live" if not self.dry_run else "dry-run",
            "models": parts["models"],
            "regime": parts["regime"],
            "account": {
                "equity": round(acct.equity, 2),
                "gross_leverage": round(snap.gross_leverage, 2),
                "margin_usage": round(snap.margin_usage, 4),
                "margin_level": round(acct.margin_level, 1),
                "return_pct": round(m.return_pct, 4) if m else 0.0,
                "drawdown": round(m.max_drawdown, 4) if m else 0.0,
                "sharpe": round(m.sharpe_15m, 3) if m else 0.0,
            },
            "cost": parts["cost"],
            "views": parts["views"],
            "orders": orders,
        }
        self.logger.log_decision(record)
        self._pending_log = None
```

Add this module-level helper next to `_ts`:
```python
def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")
```

- [ ] **Step 7: Update `test_engine_ai.py` to assert a record is written**

Replace the body of `test_engine_ai.py` with:
```python
"""Validate the production engine AI path end-to-end in DRY-RUN: real AI refresh
-> second opinion -> consensus -> sizer -> risk -> reconcile -> decision log."""
import json
import os
import time

import bot  # noqa: F401  (.env load)
from bot.engine import Engine

e = Engine(dry_run=True)          # signal_source defaults to "ai"
e.client.connect()
print(f"algo_allowed={e.client.algo_allowed()} signal={e.cfg.signal_source} "
      f"second_opinion={'off' if e._second_opinion_off else 'on'}")
e.tick()                          # refreshes, sizes, dry-run reconciles, logs
e.client.shutdown()

day = time.strftime("%Y-%m-%d")
jpath = os.path.join(e.cfg.log_dir, f"ai_decisions_{day}.jsonl")
assert os.path.exists(jpath), f"expected decision log at {jpath}"
last = [json.loads(l) for l in open(jpath, encoding="utf-8")][-1]
assert "views" in last and last["views"], "record has views"
assert "account" in last and "cost" in last
print(f"\nOK — AI path validated, decision record written to {jpath}")
```

- [ ] **Step 8: Run the engine smoke (requires MT5 + ANTHROPIC_API_KEY)**

Run: `python test_engine_ai.py`
Expected: prints `AI views refreshed | regime: …`, possibly a `[2nd]` line, then `OK — AI path validated, decision record written to logs/ai_decisions_<today>.jsonl`.

- [ ] **Step 9: Confirm the no-regression churn behavior still holds**

Run: `python test_churn.py`
Expected: reconcile on tick 1 only; ticks 2-4 `reconciled=False`, `orders=0`.

- [ ] **Step 10: Commit**

```bash
git add bot/engine.py test_engine_ai.py
git commit -m "feat: wire Nemotron second opinion + decision logging into engine"
```

---

## Self-Review

**Spec coverage:**
- §3.1 DecisionLogger → Task 3. §3.2 second_opinion (get_second_opinion + apply_consensus) → Tasks 4-5. §3.3 wiring (get_ai_views 3-tuple, _reconcile orders, tick) → Tasks 2 + 6.
- §4 data flow → Task 6 `_refresh_ai_views` + `tick`. §5 consensus rule → Task 4. §6 record schema → produced in Task 6 `_write_decision_record`, consumed/validated in Task 3 test. §7 formats → Task 3 (`_render_md`) + JSONL. §8 config + env → Task 1 (config), Task 5/6 (env reads). §9 error handling → Task 5 (raises), Task 6 (try/except degrade, failed-refresh log, logger swallows OSError). §10 testing → each task's test. §11 out-of-scope → not implemented (correct).
- All spec sections map to a task. No gaps.

**Placeholder scan:** No TBD/TODO/"add error handling"/"similar to". Every code step shows complete code.

**Type consistency:** `get_ai_views` usage keys `in_tokens/out_tokens/usd` (Task 2) are read as `claude_usage["in_tokens"]` etc. in Task 6. `get_second_opinion` usage keys `in/out/usd` (Task 5) read as `nemo_usage["in"]` in Task 6. `apply_consensus` returns `(sizer_views, view_records)` (Task 4) consumed as such in Task 6. `view_records` fields (`symbol/direction/confidence/claude/nemotron/agreement/boosted`) match `_render_md` reads (Task 3) and the round-trip test record (Task 3). `_reconcile` now returns a list; `tick` uses `len(orders)`. Order dict fields (`symbol/side/lots/cur/target/sl`) match `_render_md` order rendering. Consistent.

## Execution Handoff

(see message)
