"""Model-accuracy grader: per-category directional hit-rate from logged per-model
calls graded against supplied bars. Pure (no I/O) — synthetic decisions + bars."""
import datetime as dt
from panel.backend.model_accuracy import grade


def ep(s):
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp())


T = ["2026-06-23T10:00:00", "2026-06-23T11:00:00", "2026-06-23T12:00:00"]
eu = [(ep(T[0]), 1, 1, 1, 1.00), (ep(T[1]), 1, 1, 1, 1.01), (ep(T[2]), 1, 1, 1, 1.02)]  # rising
bt = [(ep(T[0]), 1, 1, 1, 100.0), (ep(T[1]), 1, 1, 1, 101.0), (ep(T[2]), 1, 1, 1, 102.0)]  # rising
series = {"EURUSD": eu, "BTCUSD": bt}

_v = lambda sym, d: {"symbol": sym, "claude": {"direction": d, "confidence": 0.5}}
decisions = [{"ts": t, "views": [_v("EURUSD", "long"), _v("BTCUSD", "short")]} for t in T]

g = grade(decisions, series, window=5)

# FX claude: 2 graded pairs (10->11, 11->12), long & price up -> 2 hits
assert g["summary"]["FX"]["claude"] == {"hits": 2, "total": 2, "rate": 1.0}, g["summary"]["FX"]
# CRYPTO claude: short & price up -> 2 misses
assert g["summary"]["CRYPTO"]["claude"]["rate"] == 0.0, g["summary"]["CRYPTO"]
# ALL claude: 4 calls, 2 hits
assert g["summary"]["ALL"]["claude"]["rate"] == 0.5, g["summary"]["ALL"]
# time series present, with trailing-window points
assert len(g["categories"]["FX"]["claude"]) == 2
pt = g["categories"]["FX"]["claude"][-1]
assert pt["rate"] == 1.0 and pt["n"] == 2 and pt["cum_n"] == 2
# absent models (no nemotron/deepseek in views) -> no samples, not misses
assert g["summary"]["FX"]["nemotron"]["total"] == 0
print("OK grader: FX claude 100%, CRYPTO claude 0%, ALL 50%; absent models skipped")

# flat calls + missing symbol are skipped, no crash
g2 = grade([{"ts": T[0], "views": [_v("EURUSD", "flat"), _v("ZZZ", "long")]},
            {"ts": T[1], "views": [_v("EURUSD", "flat")]}], series)
assert g2["summary"]["FX"]["claude"]["total"] == 0 and "ZZZ" in g2["skipped_symbols"]
print("OK flat skipped; unknown symbol listed in skipped_symbols")
print("OK")
