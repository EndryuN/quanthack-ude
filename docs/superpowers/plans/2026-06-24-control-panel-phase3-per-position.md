# Control Panel — Phase 3 (Per-Position Control + Per-Ticker Notes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator act on individual positions from the dashboard — pin/lock a position (engine won't rebalance/close/trail it), override the AI's view for a symbol (a deciding third opinion that can flip direction), and set a per-position take-profit — plus a per-ticker objective note, all gated behind the default-off flag.

**Architecture:** The panel (sole writer) writes `state/overrides.json` (`{sym: {pinned, view, take_profit, note}}`); the engine reads it each tick (gated) and applies: pin → skip reconcile + trailing; human view → overrides the consensus in `_refresh_ai_views`; take_profit → a broker-side TP via a new `client.set_sltp` that preserves both SL/TP legs. The risk floor / kill-switch runs first and flattens pinned positions regardless.

**Tech Stack:** Python 3.13, MetaTrader5, FastAPI panel backend, Plotly/vanilla-JS frontend, atomic JSON writes.

## Global Constraints

- **SAFETY — the running competition bot must stay byte-for-byte identical until opted in.** ALL engine integration is gated behind `EngineConfig.use_control_panel` (added in Phase 2, default `False`). With it off, no override file is read and every new branch is a no-op. (Spec §1a.)
- **The risk floor is never gated.** `risk.assess`/`enforce`/`flatten_all` run BEFORE overrides load. A **pinned or human-overridden position is STILL flattened by the kill-switch** — a required, tested invariant (Spec §9a).
- **Per-symbol opt-in:** only symbols with an explicit entry in `overrides.json` get pin/TP/human-view; all others behave exactly as before. Take-profit is opt-in per position — the no-TP / trail-the-winner default is unchanged.
- **Pin = full freeze (Spec §9a):** a pinned symbol is skipped in `_reconcile` AND `_update_trailing_stops` (no rebalance, no close, no SL ratchet).
- **`set_sltp` preserves both legs (Spec §9a):** the existing `set_sl` zeroes TP; `set_sltp(symbol, sl, tp)` sets both, callers pass the current value for the leg they aren't changing. The trailing-stop's switch to `set_sltp` is GATED behind `use_control_panel` so the flag-off trailing path is byte-identical.
- **Single-writer (Spec §9a):** the panel app is the SOLE writer of `overrides.json`; the engine NEVER writes it.
- **Develop on branch `feat/control-panel-p3`; never on the branch the operator runs.**
- **Tests:** standalone `test_*.py` at repo root, `python test_x.py`, plain `assert`, print `OK`. No pytest. Pure-logic tests need no MT5; engine-wiring tests use in-test fakes; the `set_sltp` test is a gated MT5 smoke.
- **No new third-party dependencies.**
- **overrides.json schema (panel writes, engine reads):** `{ "EURUSD": {"pinned": bool, "view": {"direction": "long|short|flat", "confidence": float}|null, "take_profit": float|null, "note": str} }`.

---

### Task 1: `bot/overrides.py` — pure per-symbol override logic

**Files:**
- Create: `bot/overrides.py`
- Test: `test_overrides.py`

**Interfaces:**
- Produces: `load_overrides(state_dir) -> dict` (`{sym: {...}}`; missing/corrupt → `{}`).
- `pinned_set(overrides) -> set` (symbols with `pinned` truthy).
- `human_views(overrides) -> dict` (`{sym: {direction, confidence}}` for non-null `view`).
- `take_profits(overrides) -> dict` (`{sym: float}` for non-null `take_profit`).

- [ ] **Step 1: Write the failing test**

Create `test_overrides.py`:
```python
"""Pure per-symbol override accessors — no MT5, no engine."""
import json, os, tempfile
from bot.overrides import load_overrides, pinned_set, human_views, take_profits

d = tempfile.mkdtemp()
assert load_overrides(d) == {}, "missing file -> {}"

ov = {
    "EURUSD": {"pinned": True, "view": None, "take_profit": None, "note": "pinned only"},
    "USDJPY": {"pinned": False, "view": {"direction": "long", "confidence": 0.8},
               "take_profit": 153.5, "note": "yen proxy"},
    "XAUUSD": {"pinned": False, "view": None, "take_profit": None, "note": ""},
}
with open(os.path.join(d, "overrides.json"), "w", encoding="utf-8") as f:
    json.dump(ov, f)

o = load_overrides(d)
assert o == ov
assert pinned_set(o) == {"EURUSD"}
assert human_views(o) == {"USDJPY": {"direction": "long", "confidence": 0.8}}
assert take_profits(o) == {"USDJPY": 153.5}

# corrupt file -> {}
with open(os.path.join(d, "overrides.json"), "w") as f:
    f.write("{not json")
assert load_overrides(d) == {}
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_overrides.py`
Expected: `ModuleNotFoundError: No module named 'bot.overrides'`

- [ ] **Step 3: Implement `bot/overrides.py`**

```python
"""Per-symbol operator overrides — pure logic, no MT5. The panel writes
state/overrides.json; the engine reads it (gated behind use_control_panel).
Schema per symbol: {pinned: bool, view: {direction, confidence}|null,
take_profit: float|null, note: str}."""
from __future__ import annotations

import json
import os


def load_overrides(state_dir: str) -> dict:
    """{sym: {...}} from state/overrides.json. Missing/corrupt -> {}."""
    path = os.path.join(state_dir, "overrides.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def pinned_set(overrides: dict) -> set:
    return {s for s, o in overrides.items() if isinstance(o, dict) and o.get("pinned")}


def human_views(overrides: dict) -> dict:
    """{sym: {direction, confidence}} for symbols with a non-null view."""
    out = {}
    for s, o in overrides.items():
        if isinstance(o, dict) and o.get("view"):
            out[s] = o["view"]
    return out


def take_profits(overrides: dict) -> dict:
    """{sym: tp_price} for symbols with a non-null take_profit."""
    out = {}
    for s, o in overrides.items():
        if isinstance(o, dict) and o.get("take_profit") is not None:
            out[s] = float(o["take_profit"])
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_overrides.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add bot/overrides.py test_overrides.py
git commit -m "feat(overrides): pure per-symbol override accessors"
```

---

### Task 2: `apply_human_override` — the deciding third opinion

**Files:**
- Modify: `bot/second_opinion.py` (append `apply_human_override`)
- Test: `test_human_override.py`

**Interfaces:**
- Consumes: `apply_consensus`'s output shapes — `sizer_views: {sym:(dir_int,conf)}`, `records: [view-record dict]`; plus `human_views` from Task 1.
- Produces: `apply_human_override(sizer_views, records, overrides) -> (sizer_views, records)` — for each `sym` in `overrides`, overwrites `sizer_views[sym]=(dir,conf)` (can flip direction) and rewrites that record: `direction`, `confidence`, adds `human:{direction,confidence}`, `agreement:"human_override"`, `boosted:false`.

- [ ] **Step 1: Write the failing test**

Create `test_human_override.py`:
```python
"""Human override flips/replaces a symbol's final view (the deciding voice)."""
from bot.second_opinion import apply_consensus, apply_human_override

# start from a real consensus result: Claude short AUDUSD
cv = {"AUDUSD": (-1, 0.45)}
nv = {"AUDUSD": (-1, 0.6, "agree")}
sizer, recs = apply_consensus(cv, {"AUDUSD": "x"}, nv, gain=0.5)
assert sizer["AUDUSD"][0] == -1   # short before override

# human overrides AUDUSD to LONG @0.9
sizer, recs = apply_human_override(sizer, recs, {"AUDUSD": {"direction": "long", "confidence": 0.9}})
assert sizer["AUDUSD"] == (1, 0.9), "human flipped to long 0.9"
r = next(r for r in recs if r["symbol"] == "AUDUSD")
assert r["direction"] == "long" and r["confidence"] == 0.9
assert r["human"] == {"direction": "long", "confidence": 0.9}
assert r["agreement"] == "human_override" and r["boosted"] is False

# a flat human override -> direction flat, confidence clamped
sizer2, recs2 = apply_consensus({"EURUSD": (1, 0.5)}, {"EURUSD": "y"}, {}, gain=0.5)
sizer2, recs2 = apply_human_override(sizer2, recs2, {"EURUSD": {"direction": "flat", "confidence": 2.0}})
assert sizer2["EURUSD"] == (0, 1.0)   # clamped to 1.0
assert recs2[0]["agreement"] == "human_override"

# no override for a symbol -> untouched
sizer3, recs3 = apply_consensus({"GBPUSD": (-1, 0.4)}, {"GBPUSD": "z"}, {}, gain=0.5)
sizer3, recs3 = apply_human_override(sizer3, recs3, {})
assert sizer3["GBPUSD"] == (-1, 0.4) and recs3[0]["agreement"] == "second_opinion_unavailable"
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_human_override.py`
Expected: `ImportError: cannot import name 'apply_human_override'`

- [ ] **Step 3: Implement `apply_human_override`**

Append to `bot/second_opinion.py` (it already imports `_DIR` from `.ai_signal` and defines `_DIR_STR`):
```python
def apply_human_override(sizer_views, records, overrides):
    """After apply_consensus, let a human view be the DECIDING voice for a symbol
    — it can flip the AI's direction. `overrides` is {sym: {direction, confidence}}
    (already filtered to non-null human views). Mutates sizer_views + records in
    place and returns them."""
    by_sym = {r["symbol"]: r for r in records}
    for sym, hv in overrides.items():
        d = _DIR.get(str(hv.get("direction", "flat")).lower(), 0)
        c = max(0.0, min(1.0, float(hv.get("confidence", 0.0))))
        if sym in sizer_views:
            sizer_views[sym] = (d, c)
        rec = by_sym.get(sym)
        if rec is not None:
            rec["direction"] = _DIR_STR[d]
            rec["confidence"] = round(c, 4)
            rec["human"] = {"direction": _DIR_STR[d], "confidence": c}
            rec["agreement"] = "human_override"
            rec["boosted"] = False
    return sizer_views, records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_human_override.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add bot/second_opinion.py test_human_override.py
git commit -m "feat(second_opinion): apply_human_override — deciding third opinion"
```

---

### Task 3: `client.set_sltp` + `Position.sl/tp`

**Files:**
- Modify: `bot/client.py` (`Position` dataclass; add `set_sltp`; populate sl/tp wherever `Position(...)` is built)
- Test: `test_client_sltp.py`

**Interfaces:**
- Produces: `Position` gains `sl: float = 0.0`, `tp: float = 0.0`. `MT5Client.set_sltp(symbol, sl, tp) -> (ok, res)` — sets BOTH legs on the net position (preserves whichever the caller passes through). Mirrors `set_sl` but never zeroes the other leg.

- [ ] **Step 1: Find where `Position(...)` is constructed**

Run: `git grep -n "Position(" bot/client.py`
Note every construction site (the `positions()` / `position_for` builders). You will add `sl=`, `tp=` to each from the raw MT5 position's `.sl`/`.tp` fields in Step 4.

- [ ] **Step 2: Write the failing test**

Create `test_client_sltp.py`:
```python
"""Position carries sl/tp; set_sltp exists and sets both legs. The live broker
call is a gated smoke (needs MT5 + an open position)."""
import os
from bot.client import Position, MT5Client

# dataclass carries sl/tp with defaults
p = Position(ticket=1, symbol="EURUSD", volume=1.0, type=0, price_open=1.0,
             price_current=1.0, profit=0.0)
assert p.sl == 0.0 and p.tp == 0.0, "Position has sl/tp defaulting to 0.0"
p2 = Position(ticket=1, symbol="EURUSD", volume=1.0, type=0, price_open=1.0,
              price_current=1.0, profit=0.0, sl=1.09, tp=1.11)
assert p2.sl == 1.09 and p2.tp == 1.11

# set_sltp is present and callable (no-position path returns cleanly, needs MT5)
assert hasattr(MT5Client, "set_sltp")
if os.getenv("PANEL_LIVE_SLTP"):  # opt-in live smoke only
    import bot  # noqa: F401
    c = MT5Client(); c.connect()
    ok, res = c.set_sltp("EURUSD", 0.0, 0.0)   # no-op SLTP; safe
    print("live set_sltp:", ok, res)
    c.shutdown()
print("OK")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python test_client_sltp.py`
Expected: `TypeError` (Position has no `sl`) or `AssertionError` on `hasattr(... "set_sltp")`.

- [ ] **Step 4: Add `sl`/`tp` to `Position` and populate them**

In `bot/client.py`, add to the `Position` dataclass (after `profit: float`):
```python
    sl: float = 0.0
    tp: float = 0.0
```
Then, at EVERY `Position(...)` construction site found in Step 1 (the position builders), add `sl=getattr(p, "sl", 0.0), tp=getattr(p, "tp", 0.0)` where `p` is the raw MT5 position struct (MT5 positions expose `.sl` and `.tp`). Example shape:
```python
Position(ticket=p.ticket, symbol=p.symbol, volume=p.volume, type=p.type,
         price_open=p.price_open, price_current=p.price_current, profit=p.profit,
         sl=getattr(p, "sl", 0.0), tp=getattr(p, "tp", 0.0))
```

- [ ] **Step 5: Add `set_sltp`**

After `set_sl` in `bot/client.py`:
```python
    def set_sltp(self, symbol: str, sl: float, tp: float):
        """Set BOTH stop-loss and take-profit on the net position. The caller
        passes the CURRENT value for whichever leg it isn't changing, so neither
        leg is clobbered (unlike set_sl, which zeroes tp)."""
        pos = self.position_for(symbol)
        if pos is None:
            return False, "no position"
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": pos.ticket,
            "sl": float(sl),
            "tp": float(tp),
        }
        res = mt5.order_send(req)
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        return ok, res
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python test_client_sltp.py`
Expected: `OK` (the live smoke is skipped unless `PANEL_LIVE_SLTP` is set).

- [ ] **Step 7: Commit**

```bash
git add bot/client.py test_client_sltp.py
git commit -m "feat(client): set_sltp preserves both legs; Position carries sl/tp"
```

---

### Task 4: Engine — load overrides + human override + PIN freeze (gated)

**Files:**
- Modify: `bot/engine.py` (`__init__`; the control block in `tick`; `_refresh_ai_views`; `_reconcile`; `_update_trailing_stops`)
- Test: `test_engine_overrides.py`

**Interfaces:**
- Consumes: `bot.overrides.{load_overrides,pinned_set,human_views}`, `bot.second_opinion.apply_human_override`.
- Produces: gated behavior — overrides loaded each tick (when flag on); human view overrides the consensus at refresh; pinned symbols skipped in reconcile + trailing. Flag off → `self._overrides` is `None`, all no-ops.

- [ ] **Step 1: Write the failing test (in-test fakes — no MT5)**

Create `test_engine_overrides.py`:
```python
"""Gated per-position overrides: PIN freeze + kill-switch-flattens-pinned
invariant + flag-off no-op. Uses fakes (no MT5)."""
import json, os, tempfile, time
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1_000_000.0; margin = 0.0; margin_level = 900.0


class FakePos:
    def __init__(self, symbol): self.symbol = symbol; self.signed_lots = 2.0
    type = 0; ticket = 1; sl = 0.0; tp = 0.0; price_open = 1.0; price_current = 1.0


class FakeClient:
    def __init__(self): self.flattened = 0; self.reconciled = []
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self): return [FakePos("EURUSD")]
    def position_for(self, s): return FakePos(s)
    def flatten_all(self, comment=""): self.flattened += 1; return []


def _snap(kill=False):
    return RiskSnapshot(equity=1e6, used_margin=0.0, margin_level=900.0,
        margin_usage=0.0, gross_notional=0.0, gross_leverage=0.0, net_notional=0.0,
        net_directional_frac=0.0, largest_symbol="", largest_frac=0.0,
        allow_new_risk=True, kill=kill, reasons=[])


class FakeRisk:
    def __init__(self, kill=False): self._kill = kill; self.enforced = 0
    def assess(self, now=None): return _snap(self._kill)
    def enforce(self, snap, dry): self.enforced += 1; return "FLATTENED"


def _engine(state_dir, kill=False):
    e = Engine(dry_run=True)
    e.cfg.use_control_panel = True
    e.cfg.state_dir = state_dir
    e.client = FakeClient()
    e.risk = FakeRisk(kill=kill)
    e._views = {"EURUSD": (1, 0.5)}; e._views_ts = time.time()
    e._last_acted_ts = -1.0          # force reconcile
    e.cfg.use_trailing_stop = False
    e.logger = None
    return e


def _write_ov(d, ov):
    with open(os.path.join(d, "overrides.json"), "w") as f: json.dump(ov, f)


# PIN: a pinned symbol is skipped in _reconcile (we capture what reconcile sees).
d = tempfile.mkdtemp(); _write_ov(d, {"EURUSD": {"pinned": True}})
e = _engine(d)
seen = []
orig = e._reconcile
e._build_targets = lambda acct: {"EURUSD": 9.0}   # wants to trade EURUSD
e._reconcile = lambda targets: (seen.append(dict(targets)) or [])
e.tick()
assert "EURUSD" not in seen[0], "pinned EURUSD is skipped before reconcile"

# KILL-SWITCH FLATTENS PINNED: even pinned, a kill tick flattens via risk path.
d = tempfile.mkdtemp(); _write_ov(d, {"EURUSD": {"pinned": True}})
e = _engine(d, kill=True); e.tick()
assert e.risk.enforced == 1, "kill-switch flattens even a pinned position"
assert e._overrides is None, "overrides never loaded on a kill tick"

# FLAG OFF: overrides present but ignored.
d = tempfile.mkdtemp(); _write_ov(d, {"EURUSD": {"pinned": True}})
e = _engine(d); e.cfg.use_control_panel = False
e._build_targets = lambda acct: {"EURUSD": 9.0}
seen = []; e._reconcile = lambda targets: (seen.append(dict(targets)) or [])
e.tick()
assert "EURUSD" in seen[0] and e._overrides is None, "flag off: pin ignored"
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_engine_overrides.py`
Expected: `AttributeError`/`AssertionError` (`_overrides` undefined or pin not applied).

- [ ] **Step 3: Add `__init__` state**

In `Engine.__init__`, after `self._controls = None`, add:
```python
        self._overrides = None   # last-read per-symbol overrides (None when gated off)
```

- [ ] **Step 4: Load overrides in the control block**

In `tick`, inside the existing `if self.cfg.use_control_panel:` block (the Phase-2 control block, after `self._controls = load_controls(...)`), add the overrides load. Right after `self._controls = load_controls(self.cfg.state_dir)`:
```python
            from .overrides import load_overrides
            self._overrides = load_overrides(self.cfg.state_dir)
```
And at the unconditional reset line just above the gate (where `self._controls = None`), add next to it:
```python
        self._overrides = None
```

- [ ] **Step 5: Apply the human override in `_refresh_ai_views`**

In `_refresh_ai_views`, find where `apply_consensus(...)` returns `sizer_views, view_records`. Immediately AFTER that line, add:
```python
            if self._overrides:
                from .overrides import human_views
                from .second_opinion import apply_human_override
                sizer_views, view_records = apply_human_override(
                    sizer_views, view_records, human_views(self._overrides))
```

- [ ] **Step 6: Skip pinned symbols in `_reconcile`**

In `_reconcile`, at the very top of the `for s, target in targets.items():` loop body, add:
```python
            if self._overrides:
                from .overrides import pinned_set
                if s in pinned_set(self._overrides):
                    continue
```
(For efficiency the implementer may compute `pinned = pinned_set(self._overrides) if self._overrides else set()` once before the loop and check `if s in pinned:` — either is acceptable as long as behavior matches.)

- [ ] **Step 7: Skip pinned symbols in `_update_trailing_stops`**

In `_update_trailing_stops`, at the top of the `for p in self.client.positions():` loop, add:
```python
            if self._overrides:
                from .overrides import pinned_set
                if p.symbol in pinned_set(self._overrides):
                    continue
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python test_engine_overrides.py`
Expected: `OK`

- [ ] **Step 9: Regression — flag-off path unchanged**

Run: `python test_churn.py` and `python test_engine_ai.py`
Expected: both pass (these make ~$0.04 Claude calls each, need MT5; confirm the default-off path is identical).

- [ ] **Step 10: Commit**

```bash
git add bot/engine.py test_engine_overrides.py
git commit -m "feat(engine): gated per-position overrides — human view + pin freeze"
```

---

### Task 5: Engine — user take-profit + trailing TP-preserve (gated)

**Files:**
- Modify: `bot/engine.py` (`tick` adds a TP-apply call; `_update_trailing_stops` preserves TP when gated; add `_apply_take_profits`)
- Test: `test_engine_takeprofit.py`

**Interfaces:**
- Consumes: `bot.overrides.take_profits`, `client.set_sltp`, `Position.sl/tp` (Task 3).
- Produces: gated — each loop, for symbols with a `take_profit` override whose broker TP differs, the engine calls `set_sltp(symbol, pos.sl, tp)`; the trailing stop, when gated, ratchets via `set_sltp(symbol, cand, pos.tp)` (preserving TP) instead of `set_sl`. Flag off → unchanged.

- [ ] **Step 1: Write the failing test (fakes)**

Create `test_engine_takeprofit.py`:
```python
"""Gated take-profit application + flag-off no-op. Fakes, no MT5."""
import json, os, tempfile, time
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1e6; margin = 0.0; margin_level = 900.0


class FakePos:
    def __init__(self, symbol, sl=1.05, tp=0.0):
        self.symbol = symbol; self.signed_lots = 2.0; self.sl = sl; self.tp = tp
    type = 0; ticket = 1; price_open = 1.0; price_current = 1.0


class FakeClient:
    def __init__(self, pos_tp=0.0): self._tp = pos_tp; self.sltp_calls = []
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self): return [FakePos("USDJPY", sl=153.0, tp=self._tp)]
    def position_for(self, s): return FakePos(s, sl=153.0, tp=self._tp)
    def set_sltp(self, symbol, sl, tp): self.sltp_calls.append((symbol, sl, tp)); return True, None


def _snap():
    return RiskSnapshot(equity=1e6, used_margin=0.0, margin_level=900.0, margin_usage=0.0,
        gross_notional=0.0, gross_leverage=0.0, net_notional=0.0, net_directional_frac=0.0,
        largest_symbol="", largest_frac=0.0, allow_new_risk=True, kill=False, reasons=[])


class FakeRisk:
    def assess(self, now=None): return _snap()
    def enforce(self, snap, dry): return None


def _engine(d, flag=True, dry_run=True):
    e = Engine(dry_run=dry_run)
    e.cfg.use_control_panel = flag
    e.cfg.state_dir = d
    e.client = FakeClient()
    e.risk = FakeRisk()
    e._views = {}; e._views_ts = time.time(); e._last_acted_ts = e._views_ts  # no reconcile
    e.cfg.use_trailing_stop = False
    e.logger = None
    return e


# TP applied: a symbol with a take_profit override whose broker tp differs gets
# set_sltp. Run dry_run=False so the broker path is exercised (FakeClient sends nothing).
d = tempfile.mkdtemp()
json.dump({"USDJPY": {"take_profit": 154.5}}, open(os.path.join(d, "overrides.json"), "w"))
e = _engine(d, dry_run=False); e.tick()
assert ("USDJPY", 153.0, 154.5) in e.client.sltp_calls, "TP applied via set_sltp, SL preserved"

# Idempotent: if broker tp already equals the override, no redundant call.
d = tempfile.mkdtemp()
json.dump({"USDJPY": {"take_profit": 154.5}}, open(os.path.join(d, "overrides.json"), "w"))
e = _engine(d, dry_run=False); e.client = FakeClient(pos_tp=154.5); e.tick()
assert e.client.sltp_calls == [], "no redundant set_sltp when tp already set"

# Flag off: override present, no TP applied (dry-run default; flag off short-circuits).
d = tempfile.mkdtemp()
json.dump({"USDJPY": {"take_profit": 154.5}}, open(os.path.join(d, "overrides.json"), "w"))
e = _engine(d, flag=False); e.tick()
assert e.client.sltp_calls == [], "flag off: no TP application"
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_engine_takeprofit.py`
Expected: `AssertionError` (no `_apply_take_profits` yet).

- [ ] **Step 3: Add the TP-apply call in `tick`**

In `tick`, right after the trailing-stop block (`if self.cfg.use_trailing_stop: self._update_trailing_stops()`), add:
```python
        # operator take-profit (gated): honor per-position TP overrides
        if self._overrides:
            self._apply_take_profits()
```

- [ ] **Step 4: Add `_apply_take_profits`**

Add this method to `Engine` (e.g. after `_update_trailing_stops`):
```python
    def _apply_take_profits(self):
        """For each symbol with a take_profit override, set a broker-side TP via
        set_sltp (preserving the current SL). Idempotent: skip if already set."""
        from .overrides import take_profits
        tps = take_profits(self._overrides)
        if not tps:
            return
        for p in self.client.positions():
            tp = tps.get(p.symbol)
            if tp is None or abs(getattr(p, "tp", 0.0) - tp) < 1e-9:
                continue
            if self.dry_run:
                print(f"   [DRY] TP {p.symbol} -> {tp}")
                continue
            self.client.set_sltp(p.symbol, getattr(p, "sl", 0.0), tp)
```
NOTE: in dry-run the engine prints `[DRY] TP …` and does NOT call the broker (the dry-run-sends-nothing rule). The Step-1 test therefore runs the TP-applied cases with `dry_run=False` against the FakeClient (which records the call but sends nothing real).

- [ ] **Step 5: Trailing stop preserves TP when gated**

In `_update_trailing_stops`, find the live SL-ratchet call (currently `ok, res = self.client.set_sl(p.symbol, cand)`). Replace it with a gated branch that preserves TP under the control panel:
```python
            if self.cfg.use_control_panel:
                ok, res = self.client.set_sltp(p.symbol, cand, getattr(p, "tp", 0.0))
            else:
                ok, res = self.client.set_sl(p.symbol, cand)
```
(Flag off → original `set_sl` path, byte-identical. Flag on → `set_sltp` preserves any user TP while ratcheting the SL.)

- [ ] **Step 6: Run test to verify it passes**

Run: `python test_engine_takeprofit.py`
Expected: `OK`

- [ ] **Step 7: Regression — flag-off trailing path unchanged**

Run: `python test_churn.py`
Expected: passes (flag off → trailing still uses `set_sl`; reconcile cadence unchanged).

- [ ] **Step 8: Commit**

```bash
git add bot/engine.py test_engine_takeprofit.py
git commit -m "feat(engine): gated per-position take-profit + TP-preserving trailing"
```

---

### Task 6: Panel backend — overrides read/write API

**Files:**
- Modify: `panel/backend/app.py` (overrides endpoints)
- Test: `test_panel_overrides_api.py`

**Interfaces:**
- Consumes: `bot.overrides.load_overrides`, `bot.controls.atomic_write_json`.
- Produces (HTTP): `GET /api/overrides` → the full overrides dict; `POST /api/overrides/{symbol}` (body: partial `{pinned?, view?, take_profit?, note?}`) → merges into that symbol's entry and writes `overrides.json` atomically. App is SOLE writer.

- [ ] **Step 1: Write the failing test**

Create `test_panel_overrides_api.py`:
```python
"""Panel overrides API — per-symbol merge, app sole writer."""
import json, os, tempfile
from fastapi.testclient import TestClient
from panel.backend import app as appmod

d = tempfile.mkdtemp()
appmod.LOG_DIR = os.path.join("panel", "backend", "_fixtures")
appmod.STATE_DIR = d
client = TestClient(appmod.app)

assert client.get("/api/overrides").json() == {}, "empty by default"

# pin EURUSD
r = client.post("/api/overrides/EURUSD", json={"pinned": True})
assert r.status_code == 200 and r.json()["EURUSD"]["pinned"] is True
# add a view + tp to USDJPY; EURUSD pin preserved
client.post("/api/overrides/USDJPY", json={"view": {"direction": "long", "confidence": 0.7},
                                           "take_profit": 153.5, "note": "yen"})
o = client.get("/api/overrides").json()
assert o["EURUSD"]["pinned"] is True
assert o["USDJPY"]["view"]["direction"] == "long" and o["USDJPY"]["take_profit"] == 153.5
# partial update merges (unpin EURUSD without dropping other keys)
client.post("/api/overrides/USDJPY", json={"pinned": True})
o = client.get("/api/overrides").json()
assert o["USDJPY"]["pinned"] is True and o["USDJPY"]["take_profit"] == 153.5  # tp preserved
# persisted on disk
on_disk = json.load(open(os.path.join(d, "overrides.json")))
assert on_disk["EURUSD"]["pinned"] is True
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_panel_overrides_api.py`
Expected: 404 on `/api/overrides`.

- [ ] **Step 3: Add the overrides endpoints to `panel/backend/app.py`**

Insert before the index route (`ctrl` is already imported from Task-2 work):
```python
from bot import overrides as ov_mod   # add near the top imports


def _overrides_path():
    return os.path.join(STATE_DIR, "overrides.json")


@app.get("/api/overrides")
def get_overrides():
    return ov_mod.load_overrides(STATE_DIR)


@app.post("/api/overrides/{symbol}")
def post_override(symbol: str, body: dict):
    o = ov_mod.load_overrides(STATE_DIR)
    entry = dict(o.get(symbol) or {"pinned": False, "view": None,
                                   "take_profit": None, "note": ""})
    for k in ("pinned", "view", "take_profit", "note"):
        if k in body:
            entry[k] = body[k]
    o[symbol] = entry
    ctrl.atomic_write_json(_overrides_path(), o)
    return o
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_panel_overrides_api.py`
Expected: `OK`

- [ ] **Step 5: Confirm earlier routes still work**

Run: `python test_panel_api.py` and `python test_panel_controls_api.py`
Expected: both `OK`.

- [ ] **Step 6: Commit**

```bash
git add panel/backend/app.py test_panel_overrides_api.py
git commit -m "feat(panel): per-symbol overrides read/write API"
```

---

### Task 7: Frontend — per-position drill-in + per-ticker note

**Files:**
- Modify: `panel/frontend/index.html` (a drill-in panel under the instrument chart)
- Modify: `panel/frontend/styles.css` (drill-in styling)
- Modify: `panel/frontend/app.js` (load/render/wire the per-symbol override + note)

**Interfaces:**
- Consumes: `GET /api/overrides`, `POST /api/overrides/{symbol}`, plus `_ctl`/`curSymbol`/`getJSON` from earlier phases, and the AI rationale already in `/api/state` consensus rows.

- [ ] **Step 1: Add the drill-in panel to `index.html`**

Inside `#symbol-card`, AFTER the `<div id="symbol-chart"></div>`, add:
```html
        <div id="symbol-override" class="ov-panel">
          <div class="ov-row">
            <button id="ov-pin" class="ctl-btn">Pin</button>
            <select id="ov-dir"><option value="">AI view</option>
              <option value="long">Force LONG</option>
              <option value="short">Force SHORT</option>
              <option value="flat">Force FLAT</option></select>
            <input id="ov-conf" type="number" min="0" max="1" step="0.05" placeholder="conf">
            <input id="ov-tp" type="number" step="0.0001" placeholder="take-profit">
            <button id="ov-save" class="ctl-btn">Apply</button>
          </div>
          <div class="ov-note"><label>Goal</label>
            <input id="ov-note" type="text" placeholder="your objective for this instrument…">
            <button id="ov-note-save" class="ctl-btn">Save</button></div>
          <div id="ov-ai" class="ov-ai"></div>
        </div>
```

- [ ] **Step 2: Add drill-in styling to `styles.css`**

Append:
```css
.ov-panel{margin-top:10px;border-top:1px solid var(--lift);padding-top:8px}
.ov-row{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.ov-row select,.ov-row input,.ov-note input{background:var(--lift);color:var(--ink);
  border:1px solid var(--lift);border-radius:6px;padding:4px 6px;
  font-family:"JetBrains Mono";font-size:12px}
.ov-row input{width:90px}
#ov-pin.on{background:var(--human);color:var(--canvas);border-color:var(--human);font-weight:600}
.ov-note{display:flex;gap:6px;align-items:center;margin-bottom:6px}
.ov-note label{font-size:10px;letter-spacing:.1em;color:var(--muted);width:40px}
.ov-note input{flex:1}
.ov-ai{font-size:11px;color:var(--muted);line-height:1.5}
.ov-ai .claude{color:var(--claude)} .ov-ai .nemo{color:var(--nemo)}
```

- [ ] **Step 3: Add the override logic to `app.js`**

Append:
```javascript
let _ov = {};   // last-known overrides {sym:{pinned,view,take_profit,note}}

async function loadOverrides(){ _ov = (await getJSON("/api/overrides")) || {}; renderOverride(); }

function renderOverride(){
  const sym = curSymbol; if(!sym) return;
  const o = _ov[sym] || {};
  const pin = document.getElementById("ov-pin");
  if(pin){ pin.classList.toggle("on", !!o.pinned); pin.textContent = o.pinned ? "Pinned" : "Pin"; }
  const dir = document.getElementById("ov-dir");
  if(dir && document.activeElement !== dir) dir.value = (o.view && o.view.direction) || "";
  const conf = document.getElementById("ov-conf");
  if(conf && document.activeElement !== conf) conf.value = (o.view && o.view.confidence != null) ? o.view.confidence : "";
  const tp = document.getElementById("ov-tp");
  if(tp && document.activeElement !== tp) tp.value = (o.take_profit != null) ? o.take_profit : "";
  const note = document.getElementById("ov-note");
  if(note && document.activeElement !== note) note.value = o.note || "";
  // AI rationale for this symbol from the latest state
  const ai = document.getElementById("ov-ai");
  if(ai && window._lastState){
    const v = (window._lastState.consensus || []).find(r=>r.symbol===sym);
    ai.innerHTML = v ? `<span class="claude">Claude:</span> ${v.claude ? v.claude.confidence : "—"} `+
      `· <span class="nemo">Nemotron:</span> ${v.nemotron ? v.nemotron.confidence : "—"} · ${v.agreement}` : "";
  }
}

async function postOverride(sym, patch){
  await fetch(`/api/overrides/${sym}`, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(patch)});
  await loadOverrides();
}

function wireOverride(){
  const pin = document.getElementById("ov-pin");
  if(pin && !pin.dataset.w){ pin.dataset.w="1"; pin.onclick=()=> postOverride(curSymbol, {pinned: !(_ov[curSymbol] && _ov[curSymbol].pinned)}); }
  const save = document.getElementById("ov-save");
  if(save && !save.dataset.w){ save.dataset.w="1"; save.onclick=()=>{
    const dir = document.getElementById("ov-dir").value;
    const conf = parseFloat(document.getElementById("ov-conf").value);
    const tpv = document.getElementById("ov-tp").value;
    const patch = {};
    patch.view = dir ? {direction: dir, confidence: isNaN(conf)?0.5:conf} : null;
    patch.take_profit = tpv === "" ? null : parseFloat(tpv);
    postOverride(curSymbol, patch);
  }; }
  const ns = document.getElementById("ov-note-save");
  if(ns && !ns.dataset.w){ ns.dataset.w="1"; ns.onclick=()=> postOverride(curSymbol, {note: document.getElementById("ov-note").value}); }
}

// capture last state for the AI-rationale line, and hook into the loop
const _origRenderStrip = renderStrip;
renderStrip = function(s){ window._lastState = s; _origRenderStrip(s); };
const _tick2 = tick;
tick = async function(){ await _tick2(); await loadOverrides(); wireOverride(); };
loadOverrides(); wireOverride();
```

- [ ] **Step 4: Verify (JS valid + endpoints reachable)**

Run: `node --check panel/frontend/app.js` → no output.
Start the backend on a temp state dir: `PANEL_LOG_DIR=panel/backend/_fixtures PANEL_STATE_DIR=$(mktemp -d) python -m uvicorn panel.backend.app:app --port 8830 &` then `sleep 3`.
- `curl -s http://127.0.0.1:8830/ | grep -c "symbol-override"` → 1
- `curl -s -X POST http://127.0.0.1:8830/api/overrides/EURUSD -H "Content-Type: application/json" -d '{"pinned":true}' | grep -c pinned` → 1
- `curl -s http://127.0.0.1:8830/api/overrides | grep -c EURUSD` → 1
Kill the server. Confirm `python test_panel_api.py` still `OK`.
(The controller will do the visual browser check after the task.)

- [ ] **Step 5: Commit**

```bash
git add panel/frontend/index.html panel/frontend/styles.css panel/frontend/app.js
git commit -m "feat(panel): per-position drill-in (pin/override/TP) + per-ticker note"
```

---

## Self-Review

**Spec coverage (Phase 3):**
- §6 pin/lock → Task 4 (reconcile + trailing skip). §6 override-as-view → Task 2 (`apply_human_override`) + Task 4 (wiring). §6 take-profit → Task 3 (`set_sltp`) + Task 5 (apply + trailing-preserve). §6 per-ticker note → Task 6 (stored) + Task 7 (edit + AI rationale display). ✅
- §7 overrides.json schema → Task 1 (`load_overrides`) + Task 6 (write). ✅
- §9a pin = full freeze (reconcile + trailing) → Task 4 Steps 6-7. ✅
- §9a risk floor ignores overrides; kill flattens pinned (tested) → Task 4 Step 1 (kill-flattens-pinned). ✅
- §9a human override layer (after apply_consensus, `human_override` agreement) → Task 2. ✅
- §9a `set_sltp` preserves both legs; trailing change gated → Task 3 + Task 5 Step 5. ✅
- §1a default-off → `use_control_panel` gates all engine wiring; Task 4 Step 1 + Task 5 flag-off cases. ✅
- Out of scope: nothing from Phase 3 deferred.

**Placeholder scan:** No TBD/"add error handling"/"similar to". Every code step is complete. The Task-5 TP-applied test cases correctly run with `dry_run=False` (dry-run prints instead of calling the broker).

**Type consistency:** `load_overrides/pinned_set/human_views/take_profits` (Task 1) consumed with matching names in Tasks 4-6. `apply_human_override(sizer_views, records, overrides)` (Task 2) consumed in Task 4 Step 5 with `human_views(self._overrides)`. `set_sltp(symbol, sl, tp)` + `Position.sl/tp` (Task 3) consumed in Task 5. overrides.json schema keys (`pinned/view/take_profit/note`) consistent across Task 1 (read), Task 6 (write whitelist), Task 7 (frontend). `self._overrides` set in Task 4, read in Tasks 4-5.

## Notes for the implementer
- **Branch first:** `git checkout -b feat/control-panel-p3` off master before Task 1.
- **Every engine change is gated** behind `self.cfg.use_control_panel` or conditioned on `self._overrides` being truthy. If any edit would change flag-off behavior, STOP and report.
- **Dry-run sends nothing:** `_apply_take_profits` and the trailing path must not hit the broker in dry-run (print instead). The kill-switch always precedes the override load.
- **Never point a test's `STATE_DIR` at the real `state/`.**
