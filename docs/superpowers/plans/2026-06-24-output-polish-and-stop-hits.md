# Output Polish + Stop-Hit Reporting + Panel Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live engine output legible (hide the confusing success retcode, show profit + reasoning on every close, and surface broker stop-hits the engine is currently blind to), and fix two panel issues (presets apply only on a button press; tidy the layout).

**Architecture:** All engine changes are observability-only — no trading logic changes. A small `_rc()` helper formats retcodes (silent on success, named on failure); closes capture `pos.profit` and a reason before closing; a new `_report_stop_hits()` diffs the open-position set tick-to-tick to detect and report broker-initiated closes (SL/TP). Panel changes are frontend-only.

**Tech Stack:** Python 3.13, MetaTrader5, FastAPI panel backend, Plotly/vanilla-JS frontend.

## Global Constraints

- **No trading-logic changes.** Every engine change here is output/observability only — print formatting, reading `pos.profit`, and read-only position diffing. Order sizing, the AI path, risk, and the trailing math are untouched.
- **Dry-run still sends nothing.** New code must not place/modify orders; stop-hit detection only *reads* `client.positions()`.
- **Never crash the loop.** Stop-hit detection is wrapped so any error is swallowed (it must never take down the trading loop).
- **Develop on branch `feat/output-polish`**, off `master`; the operator pulls + restarts to pick it up. (The control-panel `use_control_panel` flag is irrelevant here — these output changes apply to all runs.)
- **`ret=10009` is success** (`TRADE_RETCODE_DONE`); the engine computes `ok = retcode == TRADE_RETCODE_DONE`. Display nothing extra on success; show the code + name only on failure.
- **Tests:** standalone `test_*.py` at repo root, `python test_x.py`, plain `assert`, print `OK`. No pytest. Engine-wiring tests use in-test fakes (no MT5). Frontend verified via JS validity + the controller's browser check.
- **No new third-party dependencies.**

---

### Task 1: Engine — friendly retcodes + close shows profit & reasoning

**Files:**
- Modify: `bot/engine.py` (`_rc` helper; `_do_order`, `_do_close`, `_update_trailing_stops`, `_apply_take_profits` print lines; `_reconcile` close calls)
- Test: `test_engine_output.py`

**Interfaces:**
- Produces: module-level `_rc(ok, res) -> str` (`""` on success, `"  ⚠ FAILED ret=<code> (<name>)"` on failure). `_do_close(self, symbol, profit=0.0, reason="")`. `_close_reason(self, symbol, cur) -> str`.

- [ ] **Step 1: Write the failing test**

Create `test_engine_output.py`:
```python
"""Output formatting: retcode helper is silent on success, named on failure;
close line shows profit + reason. No MT5."""
from bot.engine import _rc, Engine


class _Res:
    def __init__(self, rc): self.retcode = rc

# success (10009) -> empty string (no noise)
assert _rc(True, _Res(10009)) == ""
# failure -> shows code + a human name when known
out = _rc(False, _Res(10019))
assert "FAILED" in out and "10019" in out and "money" in out.lower()
# unknown failure code still shows the number, no name crash
out2 = _rc(False, _Res(99999))
assert "FAILED" in out2 and "99999" in out2

# _close_reason: AI view flat -> "flat"; flip -> "flipped"; else de-risked
e = Engine(dry_run=True)
e._views = {"EURUSD": (0, 0.0), "USDJPY": (1, 0.6)}
e._pending_log = {"views": [{"symbol": "EURUSD", "claude": {"rationale": "range-bound, no edge"}}]}
r_flat = e._close_reason("EURUSD", cur=2.0)    # held long, AI now flat
assert "flat" in r_flat.lower() and "range-bound" in r_flat
r_flip = e._close_reason("USDJPY", cur=-1.0)   # held short, AI now long -> flip
assert "flip" in r_flip.lower()
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_engine_output.py`
Expected: `ImportError: cannot import name '_rc'`

- [ ] **Step 3: Add the `_rc` helper + retcode table (module level)**

In `bot/engine.py`, near the other module-level helpers (next to `_ts`), add:
```python
# Human names for the MT5 trade retcodes we might actually hit on failure.
# (10009 = TRADE_RETCODE_DONE = success, so it never appears here.)
_RETCODES = {
    10004: "requote", 10006: "request rejected", 10007: "cancelled by trader",
    10010: "only partially filled", 10013: "invalid request", 10014: "invalid volume",
    10015: "invalid price", 10016: "invalid stops", 10018: "market closed",
    10019: "not enough money", 10020: "prices changed", 10021: "no quotes",
    10027: "autotrading disabled (client)", 10026: "autotrading disabled (server)",
}


def _rc(ok, res) -> str:
    """Format a trade result for the log. Empty on success (the bare success
    code 10009 is just noise); on failure, the code + a human name."""
    if ok:
        return ""
    rc = getattr(res, "retcode", res)
    name = _RETCODES.get(rc)
    return f"  ⚠ FAILED ret={rc}" + (f" ({name})" if name else "")
```

- [ ] **Step 4: Use `_rc` in the three live print sites and drop the raw `ret=`**

In `_do_order` (live branch), replace:
```python
        ok, res = self.client.market_order(symbol, lots, is_buy, comment="ai", sl=sl)
        rc = getattr(res, "retcode", res)
        print(f"   [LIVE] {side} {lots} {symbol} SL={sl} -> ok={ok} ret={rc}")
        return sl
```
with:
```python
        ok, res = self.client.market_order(symbol, lots, is_buy, comment="ai", sl=sl)
        slstr = f" SL={sl}" if sl else ""
        print(f"   [LIVE] {side} {lots} {symbol}{slstr}{_rc(ok, res)}")
        return sl
```

In `_update_trailing_stops`, replace:
```python
            rc = getattr(res, "retcode", res)
            print(f"   [LIVE] TRAIL {p.symbol} SL -> {cand} ok={ok} ret={rc}")
```
with:
```python
            print(f"   [LIVE] TRAIL {p.symbol} SL -> {cand}{_rc(ok, res)}")
```

In `_apply_take_profits`, replace:
```python
            ok, res = self.client.set_sltp(p.symbol, getattr(p, "sl", 0.0), tp)
            rc = getattr(res, "retcode", res)
            print(f"   [LIVE] TP {p.symbol} -> {tp} ok={ok} ret={rc}")
```
with:
```python
            ok, res = self.client.set_sltp(p.symbol, getattr(p, "sl", 0.0), tp)
            print(f"   [LIVE] TP {p.symbol} -> {tp}{_rc(ok, res)}")
```

- [ ] **Step 5: Add `_close_reason` and upgrade `_do_close` to show profit + reason**

Add this method to `Engine` (near `_do_close`):
```python
    def _close_reason(self, symbol, cur):
        """Why is this position being closed? Derived from the current AI view
        + the latest decision rationale. cur = current signed lots."""
        v = (self._views or {}).get(symbol)
        vd = v[0] if v else 0
        cur_dir = (1 if cur > 0 else -1) if cur else 0
        rat = ""
        if self._pending_log:
            rec = next((r for r in self._pending_log.get("views", [])
                        if r.get("symbol") == symbol), None)
            if rec:
                rat = (rec.get("claude") or {}).get("rationale", "") or ""
        if vd == 0:
            base = "AI view → flat"
        elif cur_dir and vd != cur_dir:
            base = f"AI flipped {'long' if vd > 0 else 'short'}"
        else:
            base = "de-risked"
        return base + (f" — {rat}" if rat else "")
```

Replace `_do_close` with the profit/reason-aware version:
```python
    def _do_close(self, symbol, profit=0.0, reason=""):
        pstr = f"  {profit:+,.0f}" if profit else ""
        rstr = f"  ({reason})" if reason else ""
        if self.dry_run:
            print(f"   [DRY] CLOSE {symbol}{pstr}{rstr}")
            return
        ok, res = self.client.close_symbol(symbol)
        print(f"   [LIVE] CLOSE {symbol}{pstr}{rstr}{_rc(ok, res)}")
```

- [ ] **Step 6: Pass profit + reason from `_reconcile`'s two close sites**

In `_reconcile`, both close branches currently call `self._do_close(s)`. Replace each `self._do_close(s)` call with:
```python
                    self._do_close(s, profit=getattr(pos, "profit", 0.0),
                                   reason=self._close_reason(s, cur))
```
(There are two `self._do_close(s)` calls in `_reconcile` — the `abs(delta) < vmin` branch and the main `target == 0` branch. Update both. `pos` and `cur` are already in scope at both sites.)

- [ ] **Step 7: Run test to verify it passes**

Run: `python test_engine_output.py`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add bot/engine.py test_engine_output.py
git commit -m "feat(engine): friendly retcodes; close shows profit + reasoning"
```

---

### Task 2: Engine — stop-hit detection (report broker-initiated closes)

**Files:**
- Modify: `bot/engine.py` (`__init__`; add `_report_stop_hits`; call it in `tick`)
- Test: `test_engine_stop_hits.py`

**Interfaces:**
- Consumes: `client.positions()` (each position has `.ticket`, `.symbol`, `.profit`).
- Produces: `self._last_positions: dict` (ticket → {symbol, profit}); `_report_stop_hits(self, engine_closed: set)` — prints a `⚑ STOP HIT` line for each position that vanished without the engine closing it, then refreshes the snapshot.

- [ ] **Step 1: Write the failing test (fakes — no MT5)**

Create `test_engine_stop_hits.py`:
```python
"""A position that vanishes between ticks WITHOUT an engine close is reported as
a broker stop hit; an engine-closed symbol is not. Fakes, no MT5."""
import io
from contextlib import redirect_stdout
from bot.engine import Engine


class P:
    def __init__(self, ticket, symbol, profit):
        self.ticket = ticket; self.symbol = symbol; self.profit = profit


class FakeClient:
    def __init__(self, positions): self._p = positions
    def positions(self): return self._p


def _cap(fn):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn()
    return buf.getvalue()

e = Engine(dry_run=True)
# tick 1: two open positions get recorded
e.client = FakeClient([P(1, "XAGUSD", 300.0), P(2, "BTCUSD", -50.0)])
e._report_stop_hits(set())
assert e._last_positions[1]["symbol"] == "XAGUSD"

# tick 2: XAGUSD vanished (stop hit), BTCUSD still open -> report XAGUSD only
e.client = FakeClient([P(2, "BTCUSD", -40.0)])
out = _cap(lambda: e._report_stop_hits(set()))
assert "STOP HIT" in out and "XAGUSD" in out and "BTCUSD" not in out
assert "+300" in out.replace(",", "")   # the last-known profit is reported

# engine-closed symbol that vanishes is NOT a stop hit
e.client = FakeClient([P(3, "EURUSD", 10.0)])
e._report_stop_hits(set())                       # record EURUSD
e.client = FakeClient([])                         # EURUSD gone
out2 = _cap(lambda: e._report_stop_hits({"EURUSD"}))  # engine closed it
assert "STOP HIT" not in out2

# never raises if the client blows up
class Boom:
    def positions(self): raise RuntimeError("mt5 down")
e.client = Boom()
e._report_stop_hits(set())   # must not raise
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_engine_stop_hits.py`
Expected: `AttributeError: 'Engine' object has no attribute '_report_stop_hits'`

- [ ] **Step 3: Add `__init__` state**

In `Engine.__init__`, after `self._overrides = None`, add:
```python
        self._last_positions = {}   # ticket -> {symbol, profit}; for stop-hit detection
```

- [ ] **Step 4: Add `_report_stop_hits`**

Add this method to `Engine` (e.g. after `_apply_take_profits`):
```python
    def _report_stop_hits(self, engine_closed):
        """Detect positions that vanished since the last tick WITHOUT the engine
        closing them — i.e. a broker-side stop-loss or take-profit fired — and
        report them with the last-observed P&L. Read-only; never raises."""
        try:
            current = {p.ticket: p for p in self.client.positions()}
        except Exception:
            return
        for tk, prev in self._last_positions.items():
            if tk in current or prev["symbol"] in engine_closed:
                continue
            print(f"[{_ts()}]    ⚑ STOP HIT {prev['symbol']}  "
                  f"{prev['profit']:+,.0f}  (broker SL/TP)")
        self._last_positions = {tk: {"symbol": p.symbol,
                                     "profit": getattr(p, "profit", 0.0)}
                                for tk, p in current.items()}
```

- [ ] **Step 5: Call it each tick (after reconcile, before the status line)**

In `tick`, just before `self._print_status(acct, snap, m, n_orders)`, add:
```python
        # report any broker-side stop/TP fills (positions that vanished without
        # an engine close this tick)
        self._report_stop_hits({o["symbol"] for o in orders if o.get("side") == "close"})
```
(`orders` is the list returned by `_reconcile`; on ticks where no reconcile ran it's `[]`, so `engine_closed` is empty and any vanished position is reported as a stop hit — correct.)

- [ ] **Step 6: Run test to verify it passes**

Run: `python test_engine_stop_hits.py`
Expected: `OK`

- [ ] **Step 7: Regression — the live path still runs cleanly**

Run: `python test_churn.py`
Expected: passes (reconcile only on tick 1; ticks 2-4 `orders=0`). The new `_report_stop_hits` call runs each tick against the live account read-only and must not change the churn behavior or raise. (~$0.04 Claude call, needs MT5.)

- [ ] **Step 8: Commit**

```bash
git add bot/engine.py test_engine_stop_hits.py
git commit -m "feat(engine): report broker stop/TP hits (positions closed outside the engine)"
```

---

### Task 3: Panel — presets apply only on an explicit "Apply" button

**Files:**
- Modify: `panel/frontend/index.html` (add an Apply button beside the preset select)
- Modify: `panel/frontend/app.js` (the preset wiring)

**Interfaces:**
- Consumes: `POST /api/presets/apply {name}` (unchanged). The `<select>` no longer applies on change; a new `#preset-apply` button does.

- [ ] **Step 1: Add the Apply button in `index.html`**

In the Controls card, the preset row currently is:
```html
        <div class="ctl-row"><label>Preset</label>
          <select id="preset-select"></select>
          <button id="preset-save" class="ctl-btn">Save</button></div>
```
Replace it with (add `#preset-apply` before Save):
```html
        <div class="ctl-row"><label>Preset</label>
          <select id="preset-select"></select>
          <button id="preset-apply" class="ctl-btn">Apply</button>
          <button id="preset-save" class="ctl-btn">Save</button></div>
```

- [ ] **Step 2: Change the wiring in `app.js` — select stages, Apply applies**

In `wireControls`, the preset `<select>` currently applies on change:
```javascript
  const ps = document.getElementById("preset-select");
  if(ps && !ps.dataset.w){ ps.dataset.w="1"; ps.onchange = async ()=>{
    await fetch("/api/presets/apply", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name: ps.value})}); await loadControls(); setStatus("preset "+ps.value+" applied"); }; }
```
Replace it with: the select only stages (a hint), and a new Apply button does the POST:
```javascript
  const ps = document.getElementById("preset-select");
  if(ps && !ps.dataset.w){ ps.dataset.w="1"; ps.onchange = ()=>{
    setStatus("preset "+ps.value+" staged — press Apply"); }; }
  const pa = document.getElementById("preset-apply");
  if(pa && !pa.dataset.w){ pa.dataset.w="1"; pa.onclick = async ()=>{
    const ps2 = document.getElementById("preset-select");
    await fetch("/api/presets/apply", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name: ps2.value})}); await loadControls(); setStatus("preset "+ps2.value+" applied"); }; }
```

- [ ] **Step 3: Verify (JS valid + endpoint reachable)**

Run: `node --check panel/frontend/app.js` → no output.
Start the backend on a temp state dir: `PANEL_LOG_DIR=panel/backend/_fixtures PANEL_STATE_DIR=$(mktemp -d) python -m uvicorn panel.backend.app:app --port 8840 &` then `sleep 3`.
- `curl -s http://127.0.0.1:8840/ | grep -c 'id="preset-apply"'` → 1
- `curl -s -X POST http://127.0.0.1:8840/api/presets/apply -H "Content-Type: application/json" -d '{"name":"Finals"}' | grep -c aggression` → 1
Kill the server. (Controller does the browser check: selecting a preset must NOT change aggression until Apply is pressed.)

- [ ] **Step 4: Commit**

```bash
git add panel/frontend/index.html panel/frontend/app.js
git commit -m "feat(panel): presets apply only on explicit Apply press, not on select"
```

---

### Task 4: Panel — layout polish (tighten the RISK gauges + vertical rhythm)

**Files:**
- Modify: `panel/frontend/app.js` (`plotGauges` height/margins)
- Modify: `panel/frontend/styles.css` (card spacing)

**Interfaces:**
- Consumes: nothing new. Pure presentation.

The visible problem (from the live screenshot): the RISK card's four half-doughnut gauges sit in a 2×2 grid at `height:360`, which leaves a large dead vertical gap between the top row and the bottom row (each half-gauge only uses the top ~60% of its cell). The left column then ends well short of the right column.

- [ ] **Step 1: Tighten the gauge grid**

In `app.js` `plotGauges`, the final `Plotly.react("gauges", ...)` currently uses `height:360` with `margin:{l:6,r:6,t:14,b:6}`. Replace that call's layout with a tighter one that reduces the inter-row dead space:
```javascript
  Plotly.react("gauges", data, {...PLOT_BG, height:300,
    grid:{rows:2, columns:2, pattern:"independent", ygap:0.05},
    margin:{l:8,r:8,t:18,b:0}}, {displayModeBar:false});
```
(Drop from 360→300 and pull the bottom margin to 0; the half-doughnuts then sit closer together.)

- [ ] **Step 2: Tighten card vertical rhythm**

In `styles.css`, the cards use `.card{… padding:12px}` and the columns use `gap:14px`. Reduce the column gap slightly and the card padding so the left column packs tighter. Change:
```css
main{display:grid;grid-template-columns:38% 62%;gap:14px;padding:14px}
.col-left,.col-main{display:flex;flex-direction:column;gap:14px;min-width:0}
```
to:
```css
main{display:grid;grid-template-columns:34% 66%;gap:12px;padding:12px;align-items:start}
.col-left,.col-main{display:flex;flex-direction:column;gap:12px;min-width:0}
```
(Narrowing the left column to 34% gives the dense right-hand charts more room and reduces the left-column whitespace; `align-items:start` stops the columns stretching to match each other's height.)

- [ ] **Step 3: Verify (JS valid + served)**

Run: `node --check panel/frontend/app.js` → no output.
Start the backend against real data (`python -m uvicorn panel.backend.app:app --port 8841`), confirm `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8841/` → 200, kill it. The controller will screenshot the result and confirm the gauge gap is gone and the columns are better balanced (iterating on the numbers if needed).

- [ ] **Step 4: Commit**

```bash
git add panel/frontend/app.js panel/frontend/styles.css
git commit -m "feat(panel): tighten RISK gauges + column layout"
```

---

## Self-Review

**Spec coverage:**
- "ret-10009 is confusing / hide it" → Task 1 (`_rc` silent on success). ✅
- "closing positions show profit + reasoning" → Task 1 (`_do_close` + `_close_reason`). ✅
- "know when it hits" (stop hits) → Task 2 (`_report_stop_hits`). ✅
- "preset applies only on Apply" → Task 3. ✅
- "work more on the layout" → Task 4 (gauge gap + columns; screenshot-verified, iterative). ✅

**Placeholder scan:** No TBD/"add error handling"/"similar to". `_report_stop_hits` is explicitly wrapped in try/except (the "never crash the loop" constraint). Every code step is complete.

**Type consistency:** `_rc(ok, res)` (Task 1) used in `_do_order`/`_update_trailing_stops`/`_apply_take_profits`/`_do_close`. `_do_close(symbol, profit, reason)` (Task 1) called from `_reconcile`'s two close sites with `pos.profit`/`_close_reason(s, cur)`. `_report_stop_hits(engine_closed)` (Task 2) called from `tick` with the close-symbol set from `orders`. `self._last_positions` set in `__init__`, read/written only in `_report_stop_hits`. Frontend `#preset-apply` (Task 3) added in HTML and wired in `app.js`.

## Notes for the implementer
- **Branch first:** `git checkout -b feat/output-polish` off master before Task 1.
- **No trading-logic edits.** Tasks 1–2 only touch print formatting, `pos.profit` reads, and read-only position diffing. If any edit would change what/whether the engine trades, STOP and report.
- **Tasks 1–2 change the live output for *all* runs** (not gated) — that's intended; the operator wants better live output. They reach the running bot only on pull + restart.
