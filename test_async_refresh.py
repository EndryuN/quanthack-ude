"""Non-blocking AI refresh: the slow LLM work runs in a daemon worker so tick()
never blocks the 5s risk loop; the result is applied on a later tick. Uses fakes
(no MT5, no network) and stubs _refresh_prep/_refresh_worker."""
import threading
import time

from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1_000_000.0; margin = 0.0; margin_level = 900.0


class FakeClient:
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self, magic=None): return []
    def position_for(self, s): return None
    def flatten_all(self, comment="", **k): return []


class FakeRisk:
    def __init__(self, kill=False): self._kill = kill; self.assessed = 0
    def assess(self, now=None):
        self.assessed += 1
        return RiskSnapshot(equity=1e6, used_margin=0, margin_level=900, margin_usage=0,
            gross_notional=0, gross_leverage=0, net_notional=0, net_directional_frac=0,
            largest_symbol="", largest_frac=0, allow_new_risk=True, kill=self._kill, reasons=[])
    def enforce(self, snap, dry): return None


def _engine():
    e = Engine(dry_run=True)
    e.client = FakeClient(); e.risk = FakeRisk(); e.logger = None
    e.cfg.use_trailing_stop = False; e.cfg.use_broker_sl = False
    e._active_symbols = ["EURUSD"]
    e._build_targets = lambda acct: {"EURUSD": 0.0}
    e._score_models = lambda *a, **k: None       # would otherwise read MT5 prices
    e._refresh_prep = (lambda self=e: {"context": "x", "symbols": ["EURUSD"]}).__get__(e)
    return e


def _ok_result():
    return {"ok": True, "sizer_views": {"EURUSD": (1, 0.5)},
            "view_records": [{"symbol": "EURUSD"}], "regime": "test",
            "claude_views": {"EURUSD": (1, 0.5)}, "nemo_views": {}, "extra_voters": {},
            "claude_usage": {"in_tokens": 1, "out_tokens": 1, "usd": 0.0},
            "nemo_usage": None, "nemo_regime": None, "ens_raw": {}}


# --- Test 1: refresh runs off-thread; result applied on a LATER tick ---
e = _engine()
e._refresh_worker = (lambda prep, self=e: _ok_result()).__get__(e)
assert e._views is None
e.tick()                                       # due -> launches the daemon worker
assert e._refresh_thread is not None, "tick should launch a refresh thread"
e._refresh_thread.join(timeout=10)             # wait for the (fast) fake worker
e.tick()                                        # applies the finished result
assert e._views == {"EURUSD": (1, 0.5)}, f"views not applied: {e._views}"
assert e._regime == "test" and e._views_ts > 0
print("OK test1: async refresh applied on a later tick")

# --- Test 2: the tick loop is NOT blocked while a refresh is in flight ---
started = threading.Event(); release = threading.Event()
def _slow(prep):
    started.set(); release.wait(10)            # simulate DeepSeek reasoning
    return _ok_result()
e2 = _engine()
e2._refresh_worker = (lambda prep, self=e2: _slow(prep)).__get__(e2)
e2.tick()                                       # launches slow worker; must return immediately
assert started.wait(5), "worker never started"
before = e2.risk.assessed
t0 = time.time(); e2.tick(); dt = time.time() - t0
assert dt < 2.0, f"tick blocked on the in-flight refresh ({dt:.1f}s)"
assert e2.risk.assessed > before, "risk.assess must run every tick, even mid-refresh"
assert e2._refresh_thread is not None and e2._refresh_thread.is_alive(), "still in flight"
release.set(); e2._refresh_thread.join(timeout=10)
print("OK test2: risk loop runs every tick while a refresh is in flight")

# --- Test 3: worker failure -> backoff grows, no crash, views default to {} ---
e3 = _engine()
e3._refresh_worker = (lambda prep, self=e3: {"ok": False, "error": "boom"}).__get__(e3)
e3.tick(); e3._refresh_thread.join(timeout=10); e3.tick()
assert e3._ai_backoff > 0, "a failed refresh should back off"
assert e3._views == {}, "views should default to {} after failure with no prior views"
print("OK test3: failure path backs off cleanly")

# --- Test 4: only ONE refresh in flight at a time (no double-launch) ---
e4 = _engine()
started4 = threading.Event(); release4 = threading.Event(); calls = []
def _slow4(prep):
    calls.append(1); started4.set(); release4.wait(10); return _ok_result()
e4._refresh_worker = (lambda prep, self=e4: _slow4(prep)).__get__(e4)
e4.tick(); assert started4.wait(5)
e4.tick(); e4.tick()                             # in flight -> must not launch more
assert len(calls) == 1, f"launched {len(calls)} refreshes; expected 1"
release4.set(); e4._refresh_thread.join(timeout=10)
print("OK test4: single in-flight refresh (no double-launch)")

print("OK")
