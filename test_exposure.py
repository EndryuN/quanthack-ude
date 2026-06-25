"""Currency-factor exposure + block-only cap (spec E1.2). Pure, no MT5."""
from bot.exposure import currency_exposure, blocked_adds, legs

# decomposition: long USDCHF + long USDCAD stack into a big USD long
assert legs("EURUSD") == ("EUR", "USD")
assert legs("XAUUSD") == ("XAU", "USD")
exp = currency_exposure({"USDCHF": +500_000, "USDCAD": +400_000, "EURUSD": -100_000})
assert exp["USD"] == 500_000 + 400_000 + 100_000        # +USD from both USD-base longs and the EURUSD short
assert exp["CHF"] == -500_000 and exp["CAD"] == -400_000 and exp["EUR"] == -100_000

EQ = 1_000_000.0
CAP = 1.0          # cap each currency leg at 1.0x equity
STRONG = 0.7


def blocked(tgt, cur, conf):
    return blocked_adds(tgt, cur, conf, equity=EQ, cap_frac=CAP, strong_conf=STRONG)

# A) weak-consensus add that pushes USD past the cap -> BLOCKED
cur = {"USDCHF": +900_000}                       # USD already +900k
tgt = {"USDCHF": +900_000, "USDCAD": +300_000}   # adding USDCAD -> USD would hit +1.2M (>1.0x)
assert blocked(tgt, cur, {"USDCAD": 0.3}) == {"USDCAD"}

# B) same add but STRONG consensus -> allowed (don't fight a good signal)
assert blocked(tgt, cur, {"USDCAD": 0.9}) == set()

# C) a REDUCTION is never blocked, even when over-exposed
cur2 = {"USDCHF": +1_500_000}
tgt2 = {"USDCHF": +500_000}                       # cutting USD exposure
assert blocked(tgt2, cur2, {"USDCHF": 0.1}) == set()

# D) a DIVERSIFYING add within the cap is allowed
cur3 = {"USDCHF": +300_000}
tgt3 = {"USDCHF": +300_000, "EURGBP": +200_000}   # EUR/GBP legs, USD untouched, all under cap
assert blocked(tgt3, cur3, {"EURGBP": 0.2}) == set()

# E) opening from flat past the cap with weak consensus -> blocked
assert blocked({"EURUSD": +1_200_000}, {}, {"EURUSD": 0.2}) == {"EURUSD"}

print("OK")
