# Control Panel — Phase 2 (Knobs + Presets + Engine Control Channel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator steer the live engine from the dashboard — aggression, global pause, panic flatten, per-symbol allow/block, and named presets (Default/Survival/Finals) — through a one-way control-file channel that the engine reads, all gated behind a default-off flag so the running competition bot is byte-for-byte unchanged until opted in.

**Architecture:** The panel (sole writer) writes `state/controls.json` + `state/presets.json`; the engine reads them at the decide→execute seam each tick and acknowledges one-shot commands in an engine-owned `state/control_ack.json`. A new pure `bot/controls.py` holds all the logic (load, de-risk clamp, blocked-set, aggression resolution) so it is fully unit-testable without MT5. The risk floor / kill-switch runs BEFORE and INDEPENDENT of every control.

**Tech Stack:** Python 3.13, FastAPI (existing panel backend), Plotly/vanilla-JS frontend, atomic JSON writes (the `.tmp`+`os.replace` pattern from `bot/state.py`).

## Global Constraints

- **SAFETY — the running competition bot must stay byte-for-byte identical until opted in.** ALL engine integration is gated by a new `EngineConfig.use_control_panel: bool = False`. With it False (the default), the engine reads no control files and behaves exactly as today. (Spec §1a.)
- **The risk floor is never gated.** `risk.assess`/`enforce`/`flatten_all` and the soft `_derisk_only` path run BEFORE and INDEPENDENT of every control. A paused/blocked position is STILL flattened by the kill-switch. This is a required, tested invariant (Spec §9a "Risk floor ignores all overrides").
- **Single-writer + one-shot (Spec §9a).** The panel app is the SOLE writer of `controls.json` and `presets.json`; the engine NEVER writes them. One-shot commands (`flatten_seq`, `apply_seq`) are monotonic counters; the engine records the processed value in engine-owned `state/control_ack.json`. All writes use atomic `.tmp`+`os.replace`.
- **Control-read cadence (Spec §9a).** `flatten_seq` and `apply_seq` are evaluated EVERY tick right after the kill-switch. `pause`, `block`/`allow`, and `aggression` apply when the reconcile runs; bumping `apply_seq` forces a reconcile next tick so knob changes take effect within one 5s loop.
- **Block/allow = de-risk clamp, NOT close (Spec §9a).** Block clamps the target toward flat with `_derisk_only` semantics (forbid open/increase/flip, allow reduction). It never actively closes a position.
- **Develop on a branch `feat/control-panel-p2`; never on the branch the operator runs.** Engine changes reach the bot only when the operator pulls, restarts, AND sets `use_control_panel=True`.
- **Tests:** standalone `test_*.py` at repo root, run with `python test_x.py`, plain `assert`, print `OK`. No pytest. Pure-logic tests need no MT5; the engine-wiring test uses in-test fakes (no MT5, no API cost).
- **No new third-party dependencies.**
- **controls.json schema (panel writes, engine reads):** `{"paused": bool, "aggression": float|null, "block": [str], "allow": [str]|null, "active_preset": str, "flatten_seq": int, "apply_seq": int}`. `aggression:null` ⇒ use `CONFIG.aggression`.
- **control_ack.json (engine writes):** `{"flatten_seq_done": int, "apply_seq_done": int}`.

---

### Task 1: `bot/controls.py` — pure control logic + config flag

**Files:**
- Create: `bot/controls.py`
- Modify: `bot/config.py` (add `use_control_panel` field)
- Test: `test_controls.py`

**Interfaces:**
- Produces: `DEFAULT_CONTROLS: dict`, `DEFAULT_PRESETS: dict`.
- `load_controls(state_dir: str) -> dict` (defaults merged with `state/controls.json`; missing/corrupt → defaults).
- `load_ack(state_dir: str) -> dict` (`state/control_ack.json` → `{"flatten_seq_done","apply_seq_done"}`; missing → zeros).
- `atomic_write_json(path: str, obj) -> None` (`.tmp`+`os.replace`).
- `derisk_clamp(target: float, cur: float) -> float` (mirrors `engine._derisk_only` per symbol).
- `blocked_set(controls: dict, all_symbols) -> set` (block list ∪ not-in-allow).
- `effective_aggression(controls: dict, default: float) -> float`.

- [ ] **Step 1: Write the failing test**

Create `test_controls.py`:
```python
"""Pure control-channel logic — no MT5, no engine."""
import json, os, tempfile
from bot.controls import (DEFAULT_CONTROLS, DEFAULT_PRESETS, load_controls, load_ack,
                          atomic_write_json, derisk_clamp, blocked_set, effective_aggression)

d = tempfile.mkdtemp()
# missing file -> defaults
c = load_controls(d)
assert c["paused"] is False and c["aggression"] is None and c["flatten_seq"] == 0
assert c["block"] == [] and c["allow"] is None
assert load_ack(d) == {"flatten_seq_done": 0, "apply_seq_done": 0}

# atomic write round-trips and partial files merge over defaults
atomic_write_json(os.path.join(d, "controls.json"), {"paused": True, "aggression": 0.7})
c = load_controls(d)
assert c["paused"] is True and c["aggression"] == 0.7 and c["flatten_seq"] == 0  # missing keys defaulted
assert os.path.exists(os.path.join(d, "controls.json")) and not os.path.exists(os.path.join(d, "controls.json.tmp"))

# corrupt file -> defaults, no crash
open(os.path.join(d, "controls.json"), "w").write("{not json")
assert load_controls(d)["paused"] is False

# derisk_clamp mirrors _derisk_only: forbid open/increase/flip, allow reduction
assert derisk_clamp(5.0, 0.0) == 0.0      # cur flat -> don't open
assert derisk_clamp(-3.0, 2.0) == 0.0     # flip -> close to flat
assert derisk_clamp(0.0, 2.0) == 0.0      # to-flat -> close
assert derisk_clamp(5.0, 2.0) == 2.0      # don't increase -> hold cur
assert derisk_clamp(1.0, 2.0) == 1.0      # reduction allowed

# blocked_set: block list, plus everything not in allow when allow is set
syms = ["AUDUSD", "EURUSD", "USDJPY"]
assert blocked_set({"block": ["EURUSD"], "allow": None}, syms) == {"EURUSD"}
assert blocked_set({"block": [], "allow": ["AUDUSD"]}, syms) == {"EURUSD", "USDJPY"}
assert blocked_set({"block": [], "allow": None}, syms) == set()

# effective_aggression: null -> default, else the override
assert effective_aggression({"aggression": None}, 0.30) == 0.30
assert effective_aggression({"aggression": 0.9}, 0.30) == 0.9

# presets shipped
assert set(DEFAULT_PRESETS) == {"Default", "Survival", "Finals"}
assert DEFAULT_PRESETS["Default"]["aggression"] == 0.30
assert DEFAULT_PRESETS["Survival"]["aggression"] == 0.0
assert DEFAULT_PRESETS["Finals"]["aggression"] == 1.0
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_controls.py`
Expected: `ModuleNotFoundError: No module named 'bot.controls'`

- [ ] **Step 3: Implement `bot/controls.py`**

```python
"""Operator control channel — pure logic, no MT5. The panel writes
state/controls.json + state/presets.json; the engine reads controls and writes
state/control_ack.json. Everything here is gated by EngineConfig.use_control_panel
at the call sites; this module itself is side-effect-free except atomic_write_json."""
from __future__ import annotations

import json
import os

DEFAULT_CONTROLS = {
    "paused": False,        # True -> reconcile opens no new risk (de-risk still runs)
    "aggression": None,     # None -> use CONFIG.aggression; else override the sizer dial
    "block": [],            # symbols clamped toward flat (no new/increased risk)
    "allow": None,          # if a list, every symbol NOT listed is clamped toward flat
    "active_preset": "Default",
    "flatten_seq": 0,       # bump -> engine flattens all once
    "apply_seq": 0,         # bump -> engine forces a reconcile next tick (apply knobs now)
}

DEFAULT_ACK = {"flatten_seq_done": 0, "apply_seq_done": 0}

# Named risk postures along the existing sizer aggression axis (survival<->finals).
DEFAULT_PRESETS = {
    "Default":  {"aggression": 0.30, "block": [], "allow": None},
    "Survival": {"aggression": 0.0,  "block": [], "allow": None},
    "Finals":   {"aggression": 1.0,  "block": [], "allow": None},
}


def _load_json(path: str, default: dict) -> dict:
    out = dict(default)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out.update(data)
    except (OSError, ValueError):
        pass
    return out


def load_controls(state_dir: str) -> dict:
    return _load_json(os.path.join(state_dir, "controls.json"), DEFAULT_CONTROLS)


def load_ack(state_dir: str) -> dict:
    return _load_json(os.path.join(state_dir, "control_ack.json"), DEFAULT_ACK)


def atomic_write_json(path: str, obj) -> None:
    """Write JSON via a temp file + atomic rename (the bot/state.py pattern)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def derisk_clamp(target: float, cur: float) -> float:
    """Clamp `target` toward flat: forbid opening/increasing/flipping, allow
    reduction. Byte-identical in behavior to engine._derisk_only per symbol."""
    if cur == 0:
        return 0.0
    if _sign(target) != _sign(cur):
        return 0.0            # flip or to-flat -> close
    if abs(target) > abs(cur):
        return cur            # don't increase
    return target             # reduction allowed


def blocked_set(controls: dict, all_symbols) -> set:
    """Symbols that should be de-risk-clamped: the block list, plus (when an
    allow-list is set) every symbol not on it."""
    s = set(controls.get("block") or [])
    allow = controls.get("allow")
    if allow is not None:
        allow = set(allow)
        s |= {sym for sym in all_symbols if sym not in allow}
    return s


def effective_aggression(controls: dict, default: float) -> float:
    a = controls.get("aggression")
    return default if a is None else float(a)
```

- [ ] **Step 4: Add the config flag**

In `bot/config.py`, inside `@dataclass class EngineConfig`, after `use_trailing_stop` (and before the AI-logging block added earlier), add:
```python
    # --- operator control panel (Phase 2; OFF by default = identical to today) ---
    use_control_panel: bool = False           # read state/controls.json each tick
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python test_controls.py`
Expected: `OK`

- [ ] **Step 6: Confirm the flag defaults off and the engine is unaffected by import**

Run: `python -c "from bot.config import CONFIG; print('use_control_panel =', CONFIG.use_control_panel)"`
Expected: `use_control_panel = False`

- [ ] **Step 7: Commit**

```bash
git add bot/controls.py bot/config.py test_controls.py
git commit -m "feat(controls): pure control-channel logic + default-off config flag"
```

---

### Task 2: Engine wiring (gated; default off)

**Files:**
- Modify: `bot/engine.py` (`__init__`, `tick`, `_build_targets`; add `_clamp_blocked`, `_operator_flatten`)
- Test: `test_engine_controls.py`

**Interfaces:**
- Consumes: `bot.controls.{load_controls,load_ack,atomic_write_json,blocked_set,derisk_clamp,effective_aggression}`.
- Produces: gated behavior — with `use_control_panel=True`, the engine reads controls each tick (after the kill-switch), honors flatten/apply one-shots, pause + block/allow de-risk, and aggression override. With it False, `self._controls` stays `None` and nothing changes.

- [ ] **Step 1: Write the failing test (in-test fakes — no MT5, no API cost)**

Create `test_engine_controls.py`:
```python
"""Gated control-channel wiring. Uses fakes so it needs no MT5 and sends nothing.
Proves: (A) kill-switch runs BEFORE controls (risk floor is never gated),
(B) flatten one-shot, (C) apply one-shot forces reconcile, (D) flag-off = no-op."""
import json, os, tempfile, time
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1_000_000.0; margin = 0.0; margin_level = 900.0


class FakeClient:
    def __init__(self): self.flattened = 0
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self): return []
    def position_for(self, s): return None
    def flatten_all(self, comment=""): self.flattened += 1; return []


def _snap(kill=False):
    return RiskSnapshot(equity=1_000_000.0, used_margin=0.0, margin_level=900.0,
        margin_usage=0.0, gross_notional=0.0, gross_leverage=0.0, net_notional=0.0,
        net_directional_frac=0.0, largest_symbol="", largest_frac=0.0,
        allow_new_risk=True, kill=kill, reasons=[])


class FakeRisk:
    def __init__(self, kill=False): self._kill = kill; self.enforced = 0
    def assess(self, now=None): return _snap(self._kill)
    def enforce(self, snap, dry): self.enforced += 1; return "FLATTENED (kill)"


def _engine(state_dir, kill=False):
    e = Engine(dry_run=True)
    e.cfg.use_control_panel = True
    e.cfg.state_dir = state_dir
    e.client = FakeClient()
    e.risk = FakeRisk(kill=kill)
    # neutralize the AI path so tick() doesn't call out: fresh views, already acted
    e._views = {}; e._views_ts = time.time(); e._last_acted_ts = e._views_ts
    e.cfg.use_trailing_stop = False
    e.logger = None
    return e


def _write(d, **kw):
    base = {"paused": False, "aggression": None, "block": [], "allow": None,
            "active_preset": "Default", "flatten_seq": 0, "apply_seq": 0}
    base.update(kw)
    json.dump(base, open(os.path.join(d, "controls.json"), "w"))


# A) kill-switch precedes controls: even with a paused controls file, a kill tick
#    flattens via risk.enforce and NEVER loads controls.
d = tempfile.mkdtemp(); _write(d, paused=True)
e = _engine(d, kill=True); e.tick()
assert e.risk.enforced == 1, "kill-switch enforced"
assert e._controls is None, "controls never loaded on a kill tick"

# B) operator flatten one-shot: flatten_seq bump -> flatten_all + ack updated
d = tempfile.mkdtemp(); _write(d, flatten_seq=1)
e = _engine(d); e.tick()
assert e.client.flattened == 1, "operator flatten ran"
ack = json.load(open(os.path.join(d, "control_ack.json")))
assert ack["flatten_seq_done"] == 1
e.tick()  # same seq -> must NOT flatten again
assert e.client.flattened == 1, "one-shot: flatten does not repeat"

# C) apply one-shot: apply_seq bump -> forces reconcile (resets _last_acted_ts) + ack
d = tempfile.mkdtemp(); _write(d, apply_seq=1)
e = _engine(d); before = e._last_acted_ts; e.tick()
ack = json.load(open(os.path.join(d, "control_ack.json")))
assert ack["apply_seq_done"] == 1
assert e._last_acted_ts != before, "apply forced a reconcile (last_acted reset)"

# D) flag OFF: controls.json present but ignored, _controls stays None
d = tempfile.mkdtemp(); _write(d, paused=True, flatten_seq=9)
e = _engine(d); e.cfg.use_control_panel = False; e.tick()
assert e._controls is None and e.client.flattened == 0, "flag off = no control reads"

print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_engine_controls.py`
Expected: `AttributeError`/`AssertionError` (the wiring doesn't exist yet — `e._controls` undefined or behavior wrong).

- [ ] **Step 3: Add `__init__` state**

In `Engine.__init__`, after `self._second_opinion_off = ...`, add:
```python
        self._controls = None   # last-read operator controls (None when gated off)
```

- [ ] **Step 4: Insert the control-channel block into `tick`**

In `tick`, immediately AFTER the kill-switch block (the `if snap.kill: ... return`) and BEFORE `# 2. state / metrics`, insert:
```python
        # --- operator control channel (Phase 2; gated, default off) ---
        # NOTE: this sits AFTER the kill-switch on purpose — the risk floor is
        # never gated by any control.
        self._controls = None
        if self.cfg.use_control_panel:
            from .controls import load_controls, load_ack, atomic_write_json
            self._controls = load_controls(self.cfg.state_dir)
            ack = load_ack(self.cfg.state_dir)
            ack_path = os.path.join(self.cfg.state_dir, "control_ack.json")
            if self._controls["flatten_seq"] > ack["flatten_seq_done"]:
                self._operator_flatten()
                ack["flatten_seq_done"] = self._controls["flatten_seq"]
                atomic_write_json(ack_path, ack)
                self._print_status(acct, snap)
                return
            if self._controls["apply_seq"] > ack["apply_seq_done"]:
                self._last_acted_ts = -1.0   # force a reconcile this tick
                ack["apply_seq_done"] = self._controls["apply_seq"]
                atomic_write_json(ack_path, ack)
```

- [ ] **Step 5: Apply pause + block/allow in the reconcile block**

In `tick`, replace the reconcile block:
```python
        orders = []
        if self._signal_changed():
            targets = self._build_targets(acct)
            if not snap.allow_new_risk:
                targets = self._derisk_only(targets)
            orders = self._reconcile(targets)
            self._last_acted_ts = self._views_ts
```
with:
```python
        orders = []
        if self._signal_changed():
            targets = self._build_targets(acct)
            paused = bool(self._controls and self._controls["paused"])
            if paused or not snap.allow_new_risk:
                targets = self._derisk_only(targets)
            if self._controls:
                targets = self._clamp_blocked(targets)
            orders = self._reconcile(targets)
            self._last_acted_ts = self._views_ts
```

- [ ] **Step 6: Make `_build_targets` honor the aggression override**

In `_build_targets`, replace the final AI-path return:
```python
        return build_targets(self.client, self._views, acct.equity,
                             self.cfg.aggression, self.sizer_cfg)
```
with:
```python
        agg = self.cfg.aggression
        if self._controls is not None:
            from .controls import effective_aggression
            agg = effective_aggression(self._controls, self.cfg.aggression)
        return build_targets(self.client, self._views, acct.equity, agg, self.sizer_cfg)
```

- [ ] **Step 7: Add `_clamp_blocked` and `_operator_flatten`**

Add these methods to `Engine` (e.g. after `_derisk_only`):
```python
    def _clamp_blocked(self, targets):
        """Clamp blocked / not-allowed symbols toward flat (de-risk only).
        Never closes a position outright — just forbids new/increased risk."""
        from .controls import blocked_set, derisk_clamp
        blocked = blocked_set(self._controls, self.cfg.symbols)
        if not blocked:
            return targets
        out = dict(targets)
        for s in blocked:
            if s in out:
                pos = self.client.position_for(s)
                cur = pos.signed_lots if pos else 0.0
                out[s] = derisk_clamp(out[s], cur)
        return out

    def _operator_flatten(self):
        """Operator-initiated flatten-all (panel 'Flatten now'). Additive to the
        kill-switch — never weakens it."""
        if self.dry_run:
            print(f"[{_ts()}] [DRY] OPERATOR FLATTEN ALL")
            return
        results = self.client.flatten_all(comment="PANEL")
        print(f"[{_ts()}] OPERATOR FLATTEN ALL ({len(results)} positions)")
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python test_engine_controls.py`
Expected: `OK`

- [ ] **Step 9: Regression — the default-off live path is unchanged**

Run: `python test_churn.py` and `python test_engine_ai.py`
Expected: both still pass (`use_control_panel` defaults False, so the AI/churn behavior is identical). These make real Claude calls (~$0.04 each) and need MT5 — run them to confirm no regression in the live path.

- [ ] **Step 10: Commit**

```bash
git add bot/engine.py test_engine_controls.py
git commit -m "feat(engine): gated operator control channel (flatten/pause/block/aggression)"
```

---

### Task 3: Panel backend — controls read/write API (app is sole writer)

**Files:**
- Modify: `panel/backend/app.py` (add control endpoints + CONTROLS path)
- Test: `test_panel_controls_api.py`

**Interfaces:**
- Consumes: `bot.controls.{load_controls,atomic_write_json,DEFAULT_CONTROLS}`.
- Produces (HTTP): `GET /api/controls` → current controls; `POST /api/controls` (body: partial knobs `{paused?,aggression?,block?,allow?}`) → merges + bumps `apply_seq` + writes; `POST /api/flatten` → bumps `flatten_seq`. The app is the SOLE writer of `controls.json`.

- [ ] **Step 1: Write the failing test**

Create `test_panel_controls_api.py`:
```python
"""Panel control-write API. The app is the sole writer of controls.json."""
import json, os, tempfile
from fastapi.testclient import TestClient
from panel.backend import app as appmod

d = tempfile.mkdtemp()
appmod.LOG_DIR = os.path.join("panel", "backend", "_fixtures")
appmod.STATE_DIR = d
client = TestClient(appmod.app)

# defaults when no file yet
r = client.get("/api/controls"); assert r.status_code == 200
c = r.json(); assert c["paused"] is False and c["aggression"] is None and c["flatten_seq"] == 0

# POST merges knobs and bumps apply_seq, writes the file
r = client.post("/api/controls", json={"paused": True, "aggression": 0.5, "block": ["XAGUSD"]})
assert r.status_code == 200
c = r.json(); assert c["paused"] is True and c["aggression"] == 0.5 and c["block"] == ["XAGUSD"]
assert c["apply_seq"] == 1, "knob change bumped apply_seq"
on_disk = json.load(open(os.path.join(d, "controls.json")))
assert on_disk["paused"] is True and on_disk["apply_seq"] == 1

# another POST bumps apply_seq again and preserves prior knobs not sent
r = client.post("/api/controls", json={"aggression": 0.2})
c = r.json(); assert c["apply_seq"] == 2 and c["paused"] is True and c["aggression"] == 0.2

# flatten bumps flatten_seq (and not apply_seq)
r = client.post("/api/flatten"); assert r.status_code == 200
c = r.json(); assert c["flatten_seq"] == 1 and c["apply_seq"] == 2
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_panel_controls_api.py`
Expected: `assert r.status_code == 200` fails with 404 (routes don't exist).

- [ ] **Step 3: Add the control endpoints to `panel/backend/app.py`**

Add after the `CONTROLS`/state path setup (near `LOG_DIR`/`STATE_DIR`), a helper to locate the file, then the routes. Insert these route functions before the `@app.get("/")` index route:
```python
from bot import controls as ctrl   # add near the top imports


def _controls_path():
    return os.path.join(STATE_DIR, "controls.json")


@app.get("/api/controls")
def get_controls():
    return ctrl.load_controls(STATE_DIR)


@app.post("/api/controls")
def post_controls(body: dict):
    c = ctrl.load_controls(STATE_DIR)
    for k in ("paused", "aggression", "block", "allow", "active_preset"):
        if k in body:
            c[k] = body[k]
    c["apply_seq"] = int(c.get("apply_seq", 0)) + 1   # apply knobs within one tick
    ctrl.atomic_write_json(_controls_path(), c)
    return c


@app.post("/api/flatten")
def post_flatten():
    c = ctrl.load_controls(STATE_DIR)
    c["flatten_seq"] = int(c.get("flatten_seq", 0)) + 1
    ctrl.atomic_write_json(_controls_path(), c)
    return c
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_panel_controls_api.py`
Expected: `OK`

- [ ] **Step 5: Confirm read-only Phase-1 routes still work**

Run: `python test_panel_api.py`
Expected: `OK` (the new routes are additive).

- [ ] **Step 6: Commit**

```bash
git add panel/backend/app.py test_panel_controls_api.py
git commit -m "feat(panel): controls read/write API (sole-writer controls.json)"
```

---

### Task 4: Panel backend — presets API

**Files:**
- Modify: `panel/backend/app.py` (presets endpoints)
- Test: `test_panel_presets_api.py`

**Interfaces:**
- Consumes: `bot.controls.{DEFAULT_PRESETS,atomic_write_json}` + the controls helpers from Task 3.
- Produces (HTTP): `GET /api/presets` → `{presets:{name:{aggression,block,allow}}, active:str}` (seeds DEFAULT_PRESETS if no file); `POST /api/presets/apply` (body `{name}`) → loads that preset's knobs into controls.json (+ bumps apply_seq, sets active_preset); `POST /api/presets/save` (body `{name}`) → saves the CURRENT controls knobs as a named preset.

- [ ] **Step 1: Write the failing test**

Create `test_panel_presets_api.py`:
```python
"""Presets: seed defaults, apply into controls, save current as a preset."""
import json, os, tempfile
from fastapi.testclient import TestClient
from panel.backend import app as appmod

d = tempfile.mkdtemp()
appmod.LOG_DIR = os.path.join("panel", "backend", "_fixtures")
appmod.STATE_DIR = d
client = TestClient(appmod.app)

# seeds the three default presets
p = client.get("/api/presets").json()
assert set(p["presets"]) == {"Default", "Survival", "Finals"}
assert p["presets"]["Survival"]["aggression"] == 0.0

# applying Finals writes its knobs into controls + bumps apply_seq + sets active
c = client.post("/api/presets/apply", json={"name": "Finals"}).json()
assert c["aggression"] == 1.0 and c["active_preset"] == "Finals" and c["apply_seq"] >= 1
assert json.load(open(os.path.join(d, "controls.json")))["aggression"] == 1.0

# set a custom knob, then save it as "MyMix"
client.post("/api/controls", json={"aggression": 0.45, "block": ["BTCUSD"]})
client.post("/api/presets/save", json={"name": "MyMix"})
p = client.get("/api/presets").json()
assert p["presets"]["MyMix"]["aggression"] == 0.45 and p["presets"]["MyMix"]["block"] == ["BTCUSD"]
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_panel_presets_api.py`
Expected: 404 on `/api/presets`.

- [ ] **Step 3: Add the presets endpoints to `panel/backend/app.py`**

Insert before the index route:
```python
def _presets_path():
    return os.path.join(STATE_DIR, "presets.json")


def _load_presets():
    import json as _json
    try:
        with open(_presets_path(), encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict) and data:
            return data
    except (OSError, ValueError):
        pass
    return {k: dict(v) for k, v in ctrl.DEFAULT_PRESETS.items()}


@app.get("/api/presets")
def get_presets():
    return {"presets": _load_presets(),
            "active": ctrl.load_controls(STATE_DIR).get("active_preset", "Default")}


@app.post("/api/presets/apply")
def apply_preset(body: dict):
    name = body.get("name")
    presets = _load_presets()
    if name not in presets:
        return {"error": f"unknown preset {name}"}
    p = presets[name]
    c = ctrl.load_controls(STATE_DIR)
    c["aggression"] = p.get("aggression")
    c["block"] = list(p.get("block") or [])
    c["allow"] = p.get("allow")
    c["active_preset"] = name
    c["apply_seq"] = int(c.get("apply_seq", 0)) + 1
    ctrl.atomic_write_json(_controls_path(), c)
    return c


@app.post("/api/presets/save")
def save_preset(body: dict):
    name = body.get("name")
    c = ctrl.load_controls(STATE_DIR)
    presets = _load_presets()
    presets[name] = {"aggression": c.get("aggression"),
                     "block": list(c.get("block") or []), "allow": c.get("allow")}
    ctrl.atomic_write_json(_presets_path(), presets)
    return {"presets": presets, "active": c.get("active_preset", "Default")}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_panel_presets_api.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add panel/backend/app.py test_panel_presets_api.py
git commit -m "feat(panel): presets API (seed defaults, apply, save)"
```

---

### Task 5: Frontend — Controls card (aggression, pause, flatten, presets)

**Files:**
- Modify: `panel/frontend/index.html` (add a Controls card at the top of the left column)
- Modify: `panel/frontend/styles.css` (controls styling)
- Modify: `panel/frontend/app.js` (render + wire the controls)

**Interfaces:**
- Consumes: `GET/POST /api/controls`, `POST /api/flatten`, `GET /api/presets`, `POST /api/presets/apply`, `POST /api/presets/save`.

- [ ] **Step 1: Add the Controls card to `index.html`**

In `index.html`, insert as the FIRST card in `.col-left` (before the equity card):
```html
      <div class="card" id="controls-card"><h2>Controls</h2>
        <div class="ctl-row"><label>Preset</label>
          <select id="preset-select"></select>
          <button id="preset-save" class="ctl-btn">Save</button></div>
        <div class="ctl-row"><label>Aggression</label>
          <input id="aggr" type="range" min="0" max="1" step="0.05">
          <span id="aggr-val" class="mono">—</span></div>
        <div class="ctl-row">
          <button id="pause-btn" class="ctl-btn">Pause</button>
          <button id="flatten-btn" class="ctl-btn danger">Flatten now</button></div>
        <div id="ctl-status" class="ctl-status"></div>
      </div>
```

- [ ] **Step 2: Add controls styling to `styles.css`**

Append:
```css
/* Operator controls */
.ctl-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.ctl-row label{font-size:10px;letter-spacing:.1em;color:var(--muted);width:74px;flex:none}
.ctl-row input[type=range]{flex:1;accent-color:var(--chartreuse)}
#aggr-val{width:34px;text-align:right;color:var(--chartreuse)}
#preset-select{flex:1;background:var(--lift);color:var(--ink);border:1px solid var(--lift);
  border-radius:6px;padding:4px 8px;font-family:"JetBrains Mono";font-size:12px}
.ctl-btn{background:var(--lift);color:var(--ink);border:1px solid var(--muted);
  border-radius:6px;padding:5px 10px;font-family:"JetBrains Mono";font-size:12px;cursor:pointer}
.ctl-btn:hover{border-color:var(--ink)}
.ctl-btn.danger{color:var(--short);border-color:var(--short)}
.ctl-btn.on{background:var(--chartreuse);color:var(--canvas);border-color:var(--chartreuse);font-weight:600}
.ctl-status{font-family:"JetBrains Mono";font-size:11px;color:var(--muted);min-height:14px}
```

- [ ] **Step 3: Add the controls logic to `app.js`**

Append:
```javascript
let _ctl = null;   // last-known controls

function setStatus(msg){ const e=document.getElementById("ctl-status"); if(e) e.textContent = msg; }

async function loadControls(){
  _ctl = await getJSON("/api/controls");
  if(!_ctl) return;
  const aggr = document.getElementById("aggr");
  const a = _ctl.aggression == null ? 0.30 : _ctl.aggression;
  if(aggr && document.activeElement !== aggr){ aggr.value = a; }
  const av = document.getElementById("aggr-val"); if(av) av.textContent = Number(a).toFixed(2);
  const pb = document.getElementById("pause-btn");
  if(pb){ pb.classList.toggle("on", !!_ctl.paused); pb.textContent = _ctl.paused ? "Paused" : "Pause"; }
}

async function postControls(patch){
  _ctl = await getJSON("/api/controls");  // refresh base first
  const r = await fetch("/api/controls", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(patch)});
  _ctl = await r.json(); await loadControls(); setStatus("applied · "+new Date().toLocaleTimeString());
}

async function loadPresets(){
  const p = await getJSON("/api/presets"); if(!p) return;
  const sel = document.getElementById("preset-select"); if(!sel) return;
  const names = Object.keys(p.presets);
  sel.innerHTML = names.map(n=>`<option${n===p.active?" selected":""}>${n}</option>`).join("");
}

function wireControls(){
  const aggr = document.getElementById("aggr");
  if(aggr && !aggr.dataset.w){ aggr.dataset.w="1";
    aggr.oninput = ()=>{ document.getElementById("aggr-val").textContent = Number(aggr.value).toFixed(2); };
    aggr.onchange = ()=> postControls({aggression: parseFloat(aggr.value)});
  }
  const pb = document.getElementById("pause-btn");
  if(pb && !pb.dataset.w){ pb.dataset.w="1"; pb.onclick = ()=> postControls({paused: !(_ctl && _ctl.paused)}); }
  const fb = document.getElementById("flatten-btn");
  if(fb && !fb.dataset.w){ fb.dataset.w="1"; fb.onclick = async ()=>{
    if(!confirm("Flatten ALL positions now?")) return;
    await fetch("/api/flatten", {method:"POST"}); setStatus("FLATTEN sent · "+new Date().toLocaleTimeString());
  }; }
  const ps = document.getElementById("preset-select");
  if(ps && !ps.dataset.w){ ps.dataset.w="1"; ps.onchange = async ()=>{
    await fetch("/api/presets/apply", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name: ps.value})}); await loadControls(); setStatus("preset "+ps.value+" applied"); }; }
  const sv = document.getElementById("preset-save");
  if(sv && !sv.dataset.w){ sv.dataset.w="1"; sv.onclick = async ()=>{
    const name = prompt("Save current knobs as preset:"); if(!name) return;
    await fetch("/api/presets/save", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name})}); await loadPresets(); setStatus("saved preset "+name); }; }
}

// hook controls into the existing 15s loop
const _origTick = tick;
tick = async function(){ await _origTick(); wireControls(); await loadControls(); await loadPresets(); };
loadControls(); loadPresets(); wireControls();
```

- [ ] **Step 4: Verify (JS valid + served + endpoints reachable)**

Run: `node --check panel/frontend/app.js` → expect no output.
Start the backend against a temp state dir so writes don't touch the live engine:
`PANEL_LOG_DIR=panel/backend/_fixtures PANEL_STATE_DIR=$(mktemp -d) python -m uvicorn panel.backend.app:app --port 8820 &` then `sleep 3`.
- `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8820/static/app.js` → 200
- `curl -s -X POST http://127.0.0.1:8820/api/controls -H "Content-Type: application/json" -d '{"aggression":0.6}' | grep -c apply_seq` → 1
- `curl -s http://127.0.0.1:8820/api/presets | grep -c Survival` → 1
Kill the server. Confirm `python test_panel_api.py` still prints `OK`.

(The controller will do the visual browser check after the task.)

- [ ] **Step 5: Commit**

```bash
git add panel/frontend/index.html panel/frontend/styles.css panel/frontend/app.js
git commit -m "feat(panel): Controls card — aggression, pause, flatten, presets"
```

---

### Task 6: Frontend — per-symbol allow/block toggles

**Files:**
- Modify: `panel/frontend/app.js` (block toggles on the symbol list)
- Modify: `panel/frontend/styles.css` (blocked-state styling)

**Interfaces:**
- Consumes: `_ctl.block` (from `GET /api/controls`), `POST /api/controls` with an updated `block` list. Reuses the symbol list built in `buildSymbolList` (Phase 1).

- [ ] **Step 1: Add a blocked indicator + toggle to the symbol list**

In `app.js`, replace the `buildSymbolList` body's item rendering and click handler so each row gets a block toggle. Replace the `el.innerHTML = symbols.map(...)` line and the following `forEach` click wiring inside `buildSymbolList` with:
```javascript
    const blocked = new Set((_ctl && _ctl.block) || []);
    el.innerHTML = symbols.map(s=>
      `<div class="sym${s===curSymbol?" active":""}${blocked.has(s)?" blocked":""}" data-sym="${s}">`+
      `<span class="sym-name">${s}</span>`+
      `<span class="sym-block" data-block="${s}" title="block / allow">${blocked.has(s)?"⊘":"·"}</span></div>`
    ).join("");
    el.querySelectorAll(".sym").forEach(node=> node.onclick = (ev)=>{
      if(ev.target.classList.contains("sym-block")){
        const sym = ev.target.dataset.block;
        const cur = new Set((_ctl && _ctl.block) || []);
        cur.has(sym) ? cur.delete(sym) : cur.add(sym);
        postControls({block: [...cur]});
        return;
      }
      el.querySelectorAll(".sym").forEach(x=>x.classList.toggle("active", x===node));
      plotSymbol(node.dataset.sym, curTF);
    });
```

- [ ] **Step 2: Add blocked styling to `styles.css`**

Append:
```css
#symbol-list .sym{display:flex;justify-content:space-between;align-items:center}
#symbol-list .sym.blocked .sym-name{color:var(--short);text-decoration:line-through}
.sym-block{color:var(--muted);width:16px;text-align:center}
.sym-block:hover{color:var(--ink)}
#symbol-list .sym.blocked .sym-block{color:var(--short)}
```

- [ ] **Step 3: Re-render the list when controls change**

In `buildSymbolList`, the existing `dataset.sig` guard caches the list by symbol set; blocked-state changes won't re-render. Change the signature to include the blocked set so a block toggle re-renders. Replace the `const sig = symbols.join(",");` style guard line in `buildSymbolList`:
```javascript
  const sig = symbols.join(",") + "|" + ((_ctl && _ctl.block) || []).join(",");
```
(If `buildSymbolList` uses `el.dataset.sig !== symbols.join(",")` inline, change that comparison to use the new `sig` variable instead.)

- [ ] **Step 4: Verify**

Run: `node --check panel/frontend/app.js` → no output.
Start the backend on a temp state dir (as Task 5 Step 4), then:
- `curl -s -X POST http://127.0.0.1:8821/api/controls -H "Content-Type: application/json" -d '{"block":["XAGUSD"]}' | grep -c XAGUSD` → 1
Kill the server. Confirm `python test_panel_api.py` still `OK`.

- [ ] **Step 5: Commit**

```bash
git add panel/frontend/app.js panel/frontend/styles.css
git commit -m "feat(panel): per-symbol allow/block toggles on the symbol list"
```

---

## Self-Review

**Spec coverage (Phase 2):**
- §6 Phase-2 knobs (aggression / pause / flatten / allow-block) → engine Task 2, backend Task 3, frontend Tasks 5-6. ✅
- §6 presets (Default/Survival/Finals, apply/save/switch, persisted in state/presets.json) → controls Task 1 (DEFAULT_PRESETS), backend Task 4, frontend Task 5. ✅
- §7 controls.json schema + engine reads each tick → Task 1 (schema) + Task 2 (reads). ✅
- §9a control-read cadence (flatten/apply per-tick; aggression at reconcile; force-apply) → Task 2 Steps 4-6. ✅
- §9a single-writer + ack (app sole writer; engine writes only control_ack.json; monotonic seq) → Task 2 (ack) + Task 3/4 (app writes controls/presets). ✅
- §9a risk floor ignores controls (tested invariant) → Task 2 Step 1 test A (kill precedes controls). ✅
- §9a block/allow = de-risk clamp not close → Task 1 `derisk_clamp` + Task 2 `_clamp_blocked`. ✅
- §1a default-off → `use_control_panel=False` (Task 1) gates all of Task 2; Task 2 test D + Step 6 config check. ✅
- Phase 3 (pin / human override / take-profit / per-ticker notes) intentionally NOT in this plan.

**Placeholder scan:** No TBD/"add error handling"/"similar to". Every code step is complete.

**Type consistency:** `controls.json` keys (`paused/aggression/block/allow/active_preset/flatten_seq/apply_seq`) are produced by `DEFAULT_CONTROLS` (Task 1) and consumed identically in engine (Task 2), backend (Tasks 3-4), and frontend (Tasks 5-6). `load_controls/load_ack/atomic_write_json/derisk_clamp/blocked_set/effective_aggression` signatures defined in Task 1 are called with matching args in Task 2. Backend `apply_seq`/`flatten_seq` bump semantics (Tasks 3-4) match the engine's one-shot reads (Task 2). `_ctl.block` (frontend) ↔ controls `block` list (backend) consistent.

## Notes for the implementer
- **Branch first:** `git checkout -b feat/control-panel-p2` off master before Task 1.
- **The engine is gated.** Every change in `bot/engine.py` runs only under `if self.cfg.use_control_panel` (default False). If any edit would change behavior with the flag OFF, STOP and report — the live competition bot must stay identical.
- **Never point a test's `STATE_DIR` at the real `state/`** — control-write tests use `tempfile.mkdtemp()` so they never write the live engine's control files.
