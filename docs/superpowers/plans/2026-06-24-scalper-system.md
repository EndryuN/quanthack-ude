# Scalper System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A separate, isolated mean-reversion scalping process that trades configurable symbols to lift Sharpe, disabled + dry-run by default, with a backtest evidence gate — leaving the live AI engine byte-for-byte unchanged while off.

**Architecture:** A pure, testable fade signal (`scalp_signal.py`) drives a standalone loop (`scalper.py`, own magic 990624, own daily-loss kill-switch). Isolation on the netting account is by symbol-exclusivity (the scalper's symbols leave the AI engine's universe — but ONLY when the scalper is enabled) plus a no-op-today magic filter on `client.positions()`. A backtest (`scalp_backtest.py`) on local FX tick data must show the edge clears costs before the system is armed.

**Tech Stack:** Python 3.13, MetaTrader5, pandas/pyarrow (backtest), NumPy.

## Global Constraints

- **The live AI engine must stay byte-for-byte identical while the scalper is OFF.** `ScalperConfig.enabled=False` by default; symbol-exclusivity applies ONLY when enabled; the magic filter on `positions()` is a no-op while only the engine's own (magic 990623) positions exist.
- **Account is `RETAIL_NETTING`** → the scalper and AI engine must never trade the same symbol (one net position per symbol broker-wide).
- **Separate process, magic 990624; engine magic stays 990623.** Scalper is `python -m bot.scalper [--live]`, dry-run unless `--live`.
- **ASCII-only console output** (engine/scalper stdout is piped → cp1252 on Windows; non-ASCII would `UnicodeEncodeError`). No `→ ⚠ ⚑ —` etc. in printed strings.
- **Never crash / never threaten the account.** Scalper has its own daily-loss kill-switch; the account-wide kill-switch still governs.
- **No new third-party dependencies.**
- **Tests:** standalone `test_*.py` at repo root, run `python test_x.py`, plain `assert`, print `OK`. No pytest. Pure-logic tests use no MT5; MT5-dependent checks are called out.
- **Develop on branch `feat/scalper`** off master.

---

### Task 1: `bot/scalp_signal.py` — pure mean-reversion fade logic

**Files:**
- Create: `bot/scalp_signal.py`
- Test: `test_scalp_signal.py`

**Interfaces:**
- Produces: `ScalpParams` dataclass (`ema_period:int, entry_bps:float, exit_bps:float, tp_bps:float, stop_bps:float, time_stop_bars:int`); `_ema(closes:list[float], period:int) -> float`; `scalp_decision(closes:list[float], position:int, entry_price:float, bars_held:int, p:ScalpParams) -> tuple[int,str]` where `position`/return-target ∈ {-1,0,+1}.

- [ ] **Step 1: Write the failing test**

Create `test_scalp_signal.py`:
```python
"""Pure mean-reversion fade decisions. No MT5."""
from bot.scalp_signal import ScalpParams, scalp_decision, _ema

p = ScalpParams(ema_period=3, entry_bps=10.0, exit_bps=2.0, tp_bps=8.0,
                stop_bps=15.0, time_stop_bars=5)

# _ema of a flat series is that value
assert abs(_ema([100.0, 100.0, 100.0], 3) - 100.0) < 1e-9

# FLAT + price stretched ABOVE the mean -> fade SHORT (-1)
closes_up = [100.0, 100.0, 100.0, 100.2]   # last is ~20bps over the ~100 ema
tgt, why = scalp_decision(closes_up, position=0, entry_price=0.0, bars_held=0, p=p)
assert tgt == -1, (tgt, why)

# FLAT + price stretched BELOW the mean -> fade LONG (+1)
closes_dn = [100.0, 100.0, 100.0, 99.8]
tgt, _ = scalp_decision(closes_dn, position=0, entry_price=0.0, bars_held=0, p=p)
assert tgt == +1

# FLAT + price near the mean -> stay flat
tgt, _ = scalp_decision([100.0, 100.0, 100.0, 100.01], 0, 0.0, 0, p)
assert tgt == 0

# SHORT in profit past tp_bps -> exit (0). entry 100.2, now 100.1 => +10bps for a short
tgt, why = scalp_decision([100.0, 100.0, 100.0, 100.1], position=-1,
                          entry_price=100.2, bars_held=1, p=p)
assert tgt == 0 and "profit" in why.lower()

# LONG hit stop (adverse) -> exit. entry 100.0, now 99.8 => -20bps for a long
tgt, why = scalp_decision([100.0, 100.0, 100.0, 99.8], position=+1,
                          entry_price=100.0, bars_held=1, p=p)
assert tgt == 0 and "stop" in why.lower()

# time stop forces exit even when otherwise holding
tgt, why = scalp_decision([100.0, 100.0, 100.0, 100.05], position=-1,
                          entry_price=100.06, bars_held=5, p=p)
assert tgt == 0 and "time" in why.lower()

# revert-to-mean exit: short, deviation back below exit_bps -> exit
tgt, why = scalp_decision([100.0, 100.0, 100.0, 100.0], position=-1,
                          entry_price=100.2, bars_held=1, p=p)
assert tgt == 0 and "revert" in why.lower()

# insufficient data -> hold current position
assert scalp_decision([100.0], position=0, entry_price=0.0, bars_held=0, p=p)[0] == 0
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_scalp_signal.py`
Expected: `ModuleNotFoundError: No module named 'bot.scalp_signal'`

- [ ] **Step 3: Write the implementation**

Create `bot/scalp_signal.py`:
```python
"""Pure mean-reversion ('fade') scalp logic. No MT5, no I/O — fully testable.

The idea: when price stretches away from a short EMA by more than `entry_bps`,
fade it (bet on the snap-back). Exit on revert-to-mean, a small take-profit, a
tight adverse stop, or a time stop. `position` and the returned target are the
desired NET direction for the symbol: -1 short, 0 flat, +1 long.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScalpParams:
    ema_period: int = 20
    entry_bps: float = 5.0      # deviation from EMA to enter a fade
    exit_bps: float = 1.0       # deviation band around the mean to exit into
    tp_bps: float = 4.0         # favorable move that takes profit
    stop_bps: float = 8.0       # adverse move that stops out
    time_stop_bars: int = 10    # max bars to hold


def _ema(closes, period: int) -> float:
    if not closes:
        return 0.0
    k = 2.0 / (period + 1)
    e = closes[0]
    for c in closes[1:]:
        e = c * k + e * (1 - k)
    return e


def scalp_decision(closes, position: int, entry_price: float,
                   bars_held: int, p: ScalpParams):
    """Return (target_direction, reason). target/position in {-1, 0, +1}."""
    if len(closes) < 2:
        return position, "insufficient data"
    price = closes[-1]
    ema = _ema(closes[-p.ema_period:], p.ema_period)
    if ema <= 0:
        return position, "no ema"
    dev_bps = (price - ema) / ema * 1e4

    if position == 0:
        if dev_bps > p.entry_bps:
            return -1, f"fade short dev={dev_bps:.1f}bps"
        if dev_bps < -p.entry_bps:
            return 1, f"fade long dev={dev_bps:.1f}bps"
        return 0, "flat"

    # in a position -> check exits. move_bps>0 means the trade is in profit.
    move_bps = (price - entry_price) / entry_price * 1e4 * position
    if move_bps >= p.tp_bps:
        return 0, f"take-profit {move_bps:.1f}bps"
    if move_bps <= -p.stop_bps:
        return 0, f"stop {move_bps:.1f}bps"
    if bars_held >= p.time_stop_bars:
        return 0, f"time stop {bars_held}b"
    if position == -1 and dev_bps <= p.exit_bps:
        return 0, f"reverted dev={dev_bps:.1f}bps"
    if position == 1 and dev_bps >= -p.exit_bps:
        return 0, f"reverted dev={dev_bps:.1f}bps"
    return position, "hold"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_scalp_signal.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add bot/scalp_signal.py test_scalp_signal.py
git commit -m "feat(scalper): pure mean-reversion fade signal"
```

---

### Task 2: `ScalperConfig` + conditional symbol-exclusivity in `config.py`

**Files:**
- Modify: `bot/config.py`
- Test: `test_scalper_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ScalperConfig` dataclass + module-level `SCALPER = ScalperConfig()`. `EngineConfig.symbols` excludes `SCALPER.symbols` ONLY when `SCALPER.enabled`.

- [ ] **Step 1: Write the failing test**

Create `test_scalper_config.py`:
```python
"""ScalperConfig exists; symbol-exclusivity is conditional on enabled."""
import importlib
import bot.config as cfg

# defaults: disabled, EURUSD default scalp symbol, magic 990624
assert cfg.SCALPER.enabled is False
assert "EURUSD" in cfg.SCALPER.symbols
assert cfg.SCALPER.magic == 990624 and cfg.SCALPER.magic != cfg.CONFIG.magic

# scalper OFF (default) -> engine universe is UNCHANGED (still trades EURUSD)
assert "EURUSD" in cfg.CONFIG.symbols, "engine must be unchanged while scalper off"

# the helper that builds the engine universe cedes scalper symbols when enabled
on = cfg.engine_symbols(scalper_enabled=True, scalper_symbols=["EURUSD"])
off = cfg.engine_symbols(scalper_enabled=False, scalper_symbols=["EURUSD"])
assert "EURUSD" not in on and "EURUSD" in off
assert "BARUSD" not in on and "BARUSD" not in off   # BARUSD always excluded
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_scalper_config.py`
Expected: `AttributeError: module 'bot.config' has no attribute 'SCALPER'`

- [ ] **Step 3: Add `ScalperConfig`, `SCALPER`, and the `engine_symbols` helper**

In `bot/config.py`, AFTER the `RiskLimits` dataclass and BEFORE `EngineConfig`, add:
```python
@dataclass
class ScalperConfig:
    """Separate mean-reversion scalper (own process, own magic). OFF by default.
    Its `symbols` are EXCLUSIVE: when enabled they are removed from the AI
    engine's universe so the two never net into each other on this netting
    account. Signal params are starting values, refined by the backtest gate."""
    enabled: bool = False
    symbols: list = field(default_factory=lambda: ["EURUSD"])
    lots: dict = field(default_factory=lambda: {"EURUSD": 0.5})
    ema_period: int = 20
    entry_bps: float = 5.0
    exit_bps: float = 1.0
    tp_bps: float = 4.0
    stop_bps: float = 8.0
    time_stop_bars: int = 10
    poll_secs: float = 2.0
    max_concurrent: int = 2
    daily_loss_limit: float = 2000.0     # account currency
    magic: int = 990624


SCALPER = ScalperConfig()


def engine_symbols(scalper_enabled: bool, scalper_symbols) -> list:
    """The AI engine's tradable universe: all symbols minus BARUSD, and minus
    the scalper's symbols ONLY when the scalper is enabled (so flag-off behavior
    is identical to before the scalper existed)."""
    ceded = set(scalper_symbols) if scalper_enabled else set()
    return [s for s in ALL_SYMBOLS if s != "BARUSD" and s not in ceded]
```

Then change `EngineConfig.symbols` (currently the `field(default_factory=lambda: [s for s in ALL_SYMBOLS if s != "BARUSD"])`) to:
```python
    symbols: list = field(default_factory=lambda: engine_symbols(SCALPER.enabled, SCALPER.symbols))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_scalper_config.py`
Expected: `OK`

- [ ] **Step 5: Confirm the engine is unchanged while the scalper is off**

Run: `python -c "from bot.config import CONFIG, SCALPER; assert SCALPER.enabled is False; assert sorted(CONFIG.symbols)==sorted([s for s in __import__('bot.config',fromlist=['ALL_SYMBOLS']).ALL_SYMBOLS if s!='BARUSD']); print('engine universe unchanged:', len(CONFIG.symbols), 'symbols')"`
Expected: prints the unchanged symbol count (14).

- [ ] **Step 6: Commit**

```bash
git add bot/config.py test_scalper_config.py
git commit -m "feat(scalper): ScalperConfig + conditional symbol-exclusivity (no-op while off)"
```

---

### Task 3: magic filter on `client.positions()` (no-op today)

**Files:**
- Modify: `bot/client.py` (`positions`; add a pure `_filter_by_magic` helper)
- Test: `test_client_magic_filter.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `positions(self, magic=_OWN)` — default filters to `self.magic`; `magic=None` returns all. Pure helper `_filter_by_magic(raw, magic, own) -> list` for testability.

- [ ] **Step 1: Write the failing test**

Create `test_client_magic_filter.py`:
```python
"""positions() filters to the client's own magic by default; magic=None = all.
Tested via the pure helper with fake position objects (no MT5)."""
from bot.client import _filter_by_magic


class R:
    def __init__(self, magic): self.magic = magic

raw = [R(990623), R(990623), R(990624), R(0)]
own = 990623

# default (magic == own sentinel handled by caller): keep only own
assert len(_filter_by_magic(raw, 990623, own)) == 2
# scalper's view
assert len(_filter_by_magic(raw, 990624, own)) == 1
# magic=None -> all
assert len(_filter_by_magic(raw, None, own)) == 4
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_client_magic_filter.py`
Expected: `ImportError: cannot import name '_filter_by_magic'`

- [ ] **Step 3: Add the helper + thread it through `positions()`**

In `bot/client.py`, add a module-level sentinel + pure helper near the top (after imports):
```python
_OWN = object()   # sentinel: positions() default == filter to this client's magic


def _filter_by_magic(raw, magic, own):
    """Keep raw positions matching `magic`. magic=None -> keep all.
    `own` is unused here but kept for caller symmetry."""
    if magic is None:
        return list(raw)
    return [p for p in raw if getattr(p, "magic", None) == magic]
```

Replace `positions`:
```python
    def positions(self, magic=_OWN):
        """Open positions. By default returns only THIS client's own (by magic)
        so two systems on one account don't see each other — a no-op today since
        only one magic trades. Pass magic=None for all account positions."""
        m = self.magic if magic is _OWN else magic
        raw = _filter_by_magic(mt5.positions_get() or (), m, self.magic)
        out = []
        for p in raw:
            out.append(Position(p.ticket, p.symbol, p.volume, p.type,
                                p.price_open, p.price_current, p.profit,
                                sl=getattr(p, "sl", 0.0), tp=getattr(p, "tp", 0.0)))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_client_magic_filter.py`
Expected: `OK`

- [ ] **Step 5: Regression — engine behavior unchanged (live, ~$0.04)**

Run: `python test_churn.py`
Expected: passes. With only engine (magic 990623) positions on the account, `positions()` (now filtered to 990623) returns exactly what it did before, so reconcile/risk/trailing are unchanged. If it FAILS, STOP and report BLOCKED.

- [ ] **Step 6: Commit**

```bash
git add bot/client.py test_client_magic_filter.py
git commit -m "feat(client): positions() filters to own magic by default (no-op today; isolates the scalper)"
```

---

### Task 4: `bot/scalper.py` — loop, execution, scalper risk, dry-run

**Files:**
- Create: `bot/scalper.py`
- Test: `test_scalper.py`

**Interfaces:**
- Consumes: `scalp_decision`/`ScalpParams` (Task 1); `SCALPER` (Task 2); `client.positions(magic=...)`, `market_order`, `close_symbol`, `bars`, `tick` (Task 3 + existing). MT5 timeframe `mt5.TIMEFRAME_M1`.
- Produces: `Scalper` class with `tick_once()` (one decision pass) and `_risk_ok()`; `python -m bot.scalper [--live]` entrypoint.

- [ ] **Step 1: Write the failing test (FakeClient, no MT5)**

Create `test_scalper.py`:
```python
"""Scalper risk gates + dry-run, with a FakeClient. No MT5."""
import io
from contextlib import redirect_stdout
from bot.scalper import Scalper
from bot.config import ScalperConfig


class P:
    def __init__(self, symbol, vol, typ, profit=0.0):
        self.ticket = id(self); self.symbol = symbol; self.volume = vol
        self.type = typ; self.profit = profit; self.signed_lots = vol if typ == 0 else -vol


class FakeClient:
    def __init__(self):
        self._pos = []; self.orders = []; self.closed = []
        self._closes = {}
    def positions(self, magic=None): return list(self._pos)
    def position_for(self, s): return next((p for p in self._pos if p.symbol == s), None)
    def bars(self, s, tf, n):
        import numpy as np
        c = self._closes.get(s, [100.0] * n)
        rec = np.empty(len(c), dtype=[("close", "f8")]); rec["close"] = c
        return rec
    def market_order(self, s, lots, is_buy, comment="", sl=0.0):
        self.orders.append((s, lots, is_buy)); return True, None
    def close_symbol(self, s, comment=""):
        self.closed.append(s); return True, None

cfg = ScalperConfig(enabled=True, symbols=["EURUSD"], lots={"EURUSD": 0.5},
                    ema_period=3, entry_bps=10.0, max_concurrent=1, daily_loss_limit=500.0)

# dry-run: a fade signal must NOT place a real order
s = Scalper(cfg, dry_run=True); s.client = FakeClient()
s.client._closes["EURUSD"] = [100.0, 100.0, 100.0, 100.3]   # stretched up -> fade short
s.tick_once()
assert s.client.orders == [], "dry-run must send nothing"

# live: same signal opens a (short) position
s2 = Scalper(cfg, dry_run=False); s2.client = FakeClient()
s2.client._closes["EURUSD"] = [100.0, 100.0, 100.0, 100.3]
s2.tick_once()
assert len(s2.client.orders) == 1 and s2.client.orders[0][2] is False  # is_buy False = short

# daily-loss kill-switch: realized loss past the limit -> flatten + halt
s3 = Scalper(cfg, dry_run=False); s3.client = FakeClient()
s3._realized_pnl = -600.0
s3.client._pos = [P("EURUSD", 0.5, 1)]
out = io.StringIO()
with redirect_stdout(out):
    s3.tick_once()
assert "EURUSD" in s3.client.closed, "kill-switch should flatten"
assert s3._halted is True
assert "HALT" in out.getvalue().upper()
# once halted, no new orders even on a signal
s3.client.orders.clear(); s3.client._closes["EURUSD"] = [100.0,100.0,100.0,100.3]
s3.tick_once()
assert s3.client.orders == []
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_scalper.py`
Expected: `ModuleNotFoundError: No module named 'bot.scalper'`

- [ ] **Step 3: Write `bot/scalper.py`**

Create `bot/scalper.py`:
```python
"""Standalone mean-reversion scalper. Separate process, own magic (990624),
own daily-loss kill-switch. Trades ONLY its configured (exclusive) symbols.
Dry-run unless --live. ASCII-only console output (stdout is piped)."""
from __future__ import annotations

import sys
import time

from .config import SCALPER
from .scalp_signal import ScalpParams, scalp_decision


def _ts():
    return time.strftime("%H:%M:%S")


class Scalper:
    def __init__(self, cfg=SCALPER, dry_run: bool = True):
        self.cfg = cfg
        self.dry_run = dry_run
        self.client = None
        self._params = ScalpParams(cfg.ema_period, cfg.entry_bps, cfg.exit_bps,
                                    cfg.tp_bps, cfg.stop_bps, cfg.time_stop_bars)
        self._entry = {}        # symbol -> (entry_price, bars_held)
        self._realized_pnl = 0.0
        self._halted = False

    def connect(self):
        from .client import MT5Client
        self.client = MT5Client(magic=self.cfg.magic)
        self.client.connect()

    def _open_count(self):
        return len([p for p in self.client.positions() if p.symbol in self.cfg.symbols])

    def _risk_ok(self):
        """Scalper-level gate. Returns False (and flattens+halts) if the daily
        loss limit is breached."""
        if self._halted:
            return False
        if self._realized_pnl <= -abs(self.cfg.daily_loss_limit):
            print(f"[{_ts()}] [scalper] DAILY-LOSS HALT pnl={self._realized_pnl:+,.0f} "
                  f"<= -{self.cfg.daily_loss_limit:,.0f} -- flattening + halting")
            for p in self.client.positions():
                if p.symbol in self.cfg.symbols:
                    if self.dry_run:
                        print(f"   [DRY] scalp flatten {p.symbol}")
                    else:
                        self.client.close_symbol(p.symbol, comment="scalp-halt")
            self._halted = True
            return False
        return True

    def _signed(self, pos):
        # POSITION_TYPE_BUY == 0 -> long(+1); SELL -> short(-1)
        if pos is None or pos.volume == 0:
            return 0
        return 1 if pos.type == 0 else -1

    def tick_once(self):
        """One decision pass over all configured symbols. Read-only on failure."""
        try:
            import MetaTrader5 as mt5
            tf = mt5.TIMEFRAME_M1
        except Exception:
            tf = 1
        if not self._risk_ok():
            return
        for sym in self.cfg.symbols:
            try:
                rates = self.client.bars(sym, tf, max(self._params.ema_period + 2, 30))
                if rates is None or len(rates) < 2:
                    continue
                closes = [float(x) for x in rates["close"]]
                pos = self.client.position_for(sym)
                cur = self._signed(pos)
                entry_price, bars_held = self._entry.get(sym, (0.0, 0))
                if cur != 0:
                    bars_held += 1
                    self._entry[sym] = (entry_price or closes[-1], bars_held)
                target, reason = scalp_decision(closes, cur, entry_price, bars_held, self._params)
                if target == cur:
                    continue
                self._apply(sym, cur, target, closes[-1], reason)
            except Exception as e:
                print(f"[{_ts()}] [scalper] {sym} error: {e}")

    def _apply(self, sym, cur, target, price, reason):
        lots = self.cfg.lots.get(sym, 0.0)
        if target == 0:                       # close
            if self.dry_run:
                print(f"   [DRY] scalp CLOSE {sym} ({reason})"); self._entry.pop(sym, None); return
            self.client.close_symbol(sym, comment="scalp")
            self._entry.pop(sym, None)
        elif cur == 0:                        # open (respect max_concurrent)
            if self._open_count() >= self.cfg.max_concurrent:
                return
            if lots <= 0:
                return
            if self.dry_run:
                print(f"   [DRY] scalp {'BUY' if target>0 else 'SELL'} {lots} {sym} ({reason})")
                self._entry[sym] = (price, 0); return
            self.client.market_order(sym, lots, target > 0, comment="scalp")
            self._entry[sym] = (price, 0)
        # cur!=0 and target flips: on netting, close first; reopen next tick
        else:
            if self.dry_run:
                print(f"   [DRY] scalp FLIP-CLOSE {sym} ({reason})"); self._entry.pop(sym, None); return
            self.client.close_symbol(sym, comment="scalp-flip")
            self._entry.pop(sym, None)

    def run(self):
        self.connect()
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        print(f"[{_ts()}] [scalper] {mode} | symbols={self.cfg.symbols} magic={self.cfg.magic}")
        while True:
            self.tick_once()
            time.sleep(self.cfg.poll_secs)


def main():
    if not SCALPER.enabled:
        print("[scalper] ScalperConfig.enabled is False -- nothing to do. "
              "Enable it in bot/config.py after the backtest gate passes.")
        return
    live = "--live" in sys.argv
    Scalper(SCALPER, dry_run=not live).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_scalper.py`
Expected: `OK`

- [ ] **Step 5: Smoke-test the entrypoint is safe while disabled**

Run: `python -m bot.scalper`
Expected: prints the "enabled is False -- nothing to do" line and exits (no MT5 connection, no orders).

- [ ] **Step 6: Commit**

```bash
git add bot/scalper.py test_scalper.py
git commit -m "feat(scalper): standalone loop + execution + daily-loss kill-switch (dry-run default)"
```

---

### Task 5: `bot/scalp_backtest.py` + `run_scalp_backtest.py` — the evidence gate

**Files:**
- Create: `bot/scalp_backtest.py`
- Create: `run_scalp_backtest.py`
- Test: `test_scalp_backtest.py`

**Interfaces:**
- Consumes: `scalp_decision`/`ScalpParams` (Task 1); the tick parquet via `pyarrow`/`pandas` (resampled to M1).
- Produces: `m1_bars(symbol) -> (closes:list[float], cost_frac:float)`; `backtest_scalp(closes, params, cost_frac) -> dict` with keys `trades, win_rate, net_pnl_bps, sharpe, max_dd_bps`.

- [ ] **Step 1: Write the failing test**

Create `test_scalp_backtest.py`:
```python
"""Backtest accounting is deterministic; zero cost vs real cost behaves sanely."""
from bot.scalp_signal import ScalpParams
from bot.scalp_backtest import backtest_scalp

p = ScalpParams(ema_period=3, entry_bps=10.0, exit_bps=2.0, tp_bps=8.0,
                stop_bps=15.0, time_stop_bars=5)
# a mean-reverting saw-tooth: fade entries should net positive at zero cost
closes = ([100.0, 100.0, 100.0, 100.3, 100.0, 99.7, 100.0] * 20)

r0 = backtest_scalp(closes, p, cost_frac=0.0)
assert r0["trades"] > 0
assert set(r0) == {"trades", "win_rate", "net_pnl_bps", "sharpe", "max_dd_bps"}

# determinism
assert backtest_scalp(closes, p, cost_frac=0.0) == r0

# adding cost can only reduce net P&L
r1 = backtest_scalp(closes, p, cost_frac=0.0010)   # 10 bps round-trip
assert r1["net_pnl_bps"] <= r0["net_pnl_bps"]
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_scalp_backtest.py`
Expected: `ModuleNotFoundError: No module named 'bot.scalp_backtest'`

- [ ] **Step 3: Write `bot/scalp_backtest.py`**

Create `bot/scalp_backtest.py`:
```python
"""Backtest the mean-reversion scalp on M1 bars resampled from the local tick
parquet. Costs are charged per round-trip as `cost_frac` (spread/price). The
evidence gate: only arm the scalper live if this clears costs out-of-sample."""
from __future__ import annotations

import glob
import os
import re

from .scalp_signal import scalp_decision


def m1_bars(symbol: str):
    """Resample the symbol's tick parquet to M1 mid closes. Returns
    (closes:list[float], cost_frac:float) where cost_frac = mean spread/price."""
    import pandas as pd
    import pyarrow.parquet as pq
    from .dataloader import DATA_ROOT
    files = sorted(f for f in glob.glob(os.path.join(DATA_ROOT, "*.parquet"))
                   if re.match(rf"{symbol}_\d{{4}}", os.path.basename(f)))
    parts = []
    for f in files:
        df = pq.read_table(f, columns=["time", "bid", "ask"]).to_pandas()
        df["time"] = pd.to_datetime(df["time"]); df = df.set_index("time")
        mid = (df["bid"] + df["ask"]) / 2.0
        spread = (df["ask"] - df["bid"])
        b = pd.DataFrame({"close": mid.resample("1min").last(),
                          "spread": spread.resample("1min").mean()}).dropna()
        parts.append(b)
    if not parts:
        return [], 0.0
    bars = pd.concat(parts).sort_index()
    bars = bars[~bars.index.duplicated(keep="last")]
    closes = [float(x) for x in bars["close"].to_numpy()]
    price = closes[-1] if closes else 1.0
    cost_frac = float(bars["spread"].mean() / price) if price > 0 else 0.0
    return closes, cost_frac


def backtest_scalp(closes, params, cost_frac: float) -> dict:
    """Replay scalp_decision over `closes`. Each closed round-trip pays
    `cost_frac` (in price terms) once. Returns summary stats in bps."""
    position, entry_price, bars_held = 0, 0.0, 0
    trade_returns = []      # net return (fraction) per closed trade
    for i in range(2, len(closes) + 1):
        window = closes[:i]
        price = window[-1]
        if position != 0:
            bars_held += 1
        target, _ = scalp_decision(window, position, entry_price, bars_held, params)
        if position == 0 and target != 0:
            position, entry_price, bars_held = target, price, 0
        elif position != 0 and target != position:
            gross = (price - entry_price) / entry_price * position
            trade_returns.append(gross - cost_frac)   # charge round-trip cost
            position, entry_price, bars_held = 0, 0.0, 0

    n = len(trade_returns)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "net_pnl_bps": 0.0,
                "sharpe": 0.0, "max_dd_bps": 0.0}
    wins = sum(1 for r in trade_returns if r > 0)
    net = sum(trade_returns)
    mean = net / n
    var = sum((r - mean) ** 2 for r in trade_returns) / n
    std = var ** 0.5
    sharpe = (mean / std) if std > 0 else 0.0
    # max drawdown of the cumulative trade-return curve
    cum, peak, mdd = 0.0, 0.0, 0.0
    for r in trade_returns:
        cum += r; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    return {"trades": n, "win_rate": round(wins / n, 4),
            "net_pnl_bps": round(net * 1e4, 2), "sharpe": round(sharpe, 4),
            "max_dd_bps": round(mdd * 1e4, 2)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_scalp_backtest.py`
Expected: `OK`

- [ ] **Step 5: Write the CLI `run_scalp_backtest.py`**

Create `run_scalp_backtest.py`:
```python
"""CLI: backtest the scalp on local FX tick data + sweep the entry/tp/stop grid.
Usage: python run_scalp_backtest.py [SYMBOL]   (default EURUSD)"""
import sys

from bot.scalp_signal import ScalpParams
from bot.scalp_backtest import m1_bars, backtest_scalp

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"


def main():
    closes, cost_frac = m1_bars(SYMBOL)
    if not closes:
        print(f"no tick data for {SYMBOL} (local set is FX+metals+oil only). "
              f"For crypto, pull M1 from MT5.")
        return
    print(f"{SYMBOL}: {len(closes)} M1 bars, cost_frac={cost_frac*1e4:.2f}bps/round-trip")
    print(f"{'entry':>6}{'tp':>6}{'stop':>6}{'trades':>8}{'win%':>7}{'net_bps':>9}{'sharpe':>8}{'maxDD':>8}")
    best = None
    for entry in (3.0, 5.0, 8.0):
        for tp in (3.0, 5.0, 8.0):
            for stop in (8.0, 12.0):
                p = ScalpParams(ema_period=20, entry_bps=entry, exit_bps=1.0,
                                tp_bps=tp, stop_bps=stop, time_stop_bars=10)
                r = backtest_scalp(closes, p, cost_frac)
                print(f"{entry:6.0f}{tp:6.0f}{stop:6.0f}{r['trades']:8d}"
                      f"{r['win_rate']*100:7.1f}{r['net_pnl_bps']:9.1f}"
                      f"{r['sharpe']:8.3f}{r['max_dd_bps']:8.1f}")
                if r["trades"] >= 30 and (best is None or r["sharpe"] > best[1]["sharpe"]):
                    best = ((entry, tp, stop), r)
    if best:
        (e, tp, st), r = best
        print(f"\nBEST by Sharpe (>=30 trades): entry={e} tp={tp} stop={st} -> "
              f"net={r['net_pnl_bps']}bps sharpe={r['sharpe']} win={r['win_rate']*100:.1f}%")
        print("GATE: arm live only if net_pnl_bps > 0 after cost AND sharpe clears the AI engine's.")
    else:
        print("\nno parameter set produced >=30 trades — no edge to arm.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the real backtest (needs the local tick parquet)**

Run: `python run_scalp_backtest.py EURUSD`
Expected: prints the sweep table + a BEST line. (This is the evidence gate — the numbers decide whether the scalper is ever armed. No assertion; it's an analysis run.)

- [ ] **Step 7: Commit**

```bash
git add bot/scalp_backtest.py run_scalp_backtest.py test_scalp_backtest.py
git commit -m "feat(scalper): M1 backtest + sweep CLI (the evidence gate)"
```

---

## Self-Review

**Spec coverage:**
- Separate process / own magic / dry-run default → Task 4 (`scalper.py`, `--live`, `enabled` guard). ✅
- Netting-safe isolation: symbol-exclusivity → Task 2 (`engine_symbols`, conditional on enabled); magic filter → Task 3. ✅
- Mean-reversion fade signal (pure/testable) → Task 1. ✅
- Risk: fixed lots + max-concurrent + daily-loss kill-switch → Task 4. ✅
- Backtest evidence gate on FX tick data, real cost → Task 5. ✅
- Default EURUSD, configurable → Task 2 (`ScalperConfig.symbols`). ✅
- ASCII-only output → Tasks 4 (printed strings are ASCII). ✅
- AI engine unchanged while off → Task 2 (conditional exclusivity) + Task 3 (no-op filter) + Task 3 Step 5 churn regression. ✅

**Placeholder scan:** No TBD/"handle errors"/"similar to". `tick_once` wraps per-symbol work in try/except (never-crash). Signal params are concrete starting values (Task 2), refined by Task 5's sweep — that refinement is the gate, not a placeholder.

**Type consistency:** `scalp_decision(closes, position, entry_price, bars_held, p)->(int,str)` defined in Task 1, consumed identically in Tasks 4 & 5. `ScalpParams` fields match across Tasks 1/2/4/5. `SCALPER`/`ScalperConfig` (Task 2) consumed in Task 4. `positions(magic=...)` (Task 3) consumed in Task 4's FakeClient signature (`positions(self, magic=None)`). `backtest_scalp` returns exactly `{trades, win_rate, net_pnl_bps, sharpe, max_dd_bps}` — asserted in Task 5's test.

## Notes for the implementer
- **Branch first:** `git checkout -b feat/scalper` off master.
- **Tasks 2 & 3 touch shared code** but are no-ops while the scalper is off (Task 2 cedes symbols only when `enabled`; Task 3's filter is identity while only magic 990623 trades). Task 3 Step 5 runs the live churn regression to prove it. If either changes engine behavior with the scalper off, STOP and report.
- **The scalper is never armed by this plan.** It ships `enabled=False`. Arming is a separate, explicit decision after Task 5's backtest clears the gate.
