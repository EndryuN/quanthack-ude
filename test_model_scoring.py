"""Per-model directional accuracy scoring (pure). 'Was the model correct?'"""
from bot.model_scoring import directional_correct, hit_rates

# long & price up -> correct; long & down -> wrong
assert directional_correct(1, 1.0000, 1.0050) is True
assert directional_correct(1, 1.0000, 0.9950) is False
# short & down -> correct; short & up -> wrong
assert directional_correct(-1, 1.0000, 0.9950) is True
assert directional_correct(-1, 1.0000, 1.0050) is False
# not scorable: flat call, no move, missing price
assert directional_correct(0, 1.0, 1.1) is None
assert directional_correct(1, 1.0, 1.0) is None
assert directional_correct(1, None, 1.1) is None

# aggregate hit rates per model
samples = [
    ("claude",   1, 1.0, 1.1),   # correct
    ("claude",  -1, 1.0, 1.1),   # wrong
    ("claude",   1, 1.0, 1.2),   # correct
    ("nemotron", 1, 1.0, 0.9),   # wrong
    ("nemotron", 0, 1.0, 2.0),   # flat -> skipped
    ("deepseek", 1, 1.0, 1.0),   # no move -> skipped
]
r = hit_rates(samples)
assert r["claude"]["hits"] == 2 and r["claude"]["total"] == 3 and abs(r["claude"]["rate"] - 0.6667) < 1e-3
assert r["nemotron"]["total"] == 1 and r["nemotron"]["hits"] == 0 and r["nemotron"]["rate"] == 0.0
assert r["nemotron"]["skipped"] == 1
assert r["deepseek"]["total"] == 0 and r["deepseek"]["rate"] is None and r["deepseek"]["skipped"] == 1

# --- ModelScorer: scores the PREVIOUS refresh against the next price snapshot ---
from bot.model_scoring import ModelScorer
sc = ModelScorer()
# refresh 1: record calls + prices; nothing to score yet
sc.record({"claude": {"EURUSD": 1, "GBPUSD": -1}, "deepseek": {"EURUSD": 1}},
          {"EURUSD": 1.0000, "GBPUSD": 1.0000})
assert sc.rates() == {} and sc.n_samples == 0
# refresh 2: EURUSD up (claude long ✓, deepseek long ✓), GBPUSD up (claude short ✗)
sc.record({"claude": {"EURUSD": 1, "GBPUSD": -1}, "deepseek": {"EURUSD": 1}},
          {"EURUSD": 1.0100, "GBPUSD": 1.0100})
r2 = sc.rates()
assert r2["claude"]["total"] == 2 and r2["claude"]["hits"] == 1, r2["claude"]
assert r2["deepseek"]["total"] == 1 and r2["deepseek"]["hits"] == 1, r2["deepseek"]

print("OK")
