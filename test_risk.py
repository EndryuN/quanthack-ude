"""RiskEngine.assess boundaries — first tests for the most important safety.
Covers the concentration / net-directional caps wired in by spec E0.1.
No MT5: a fake client supplies account + positions + notional."""
from bot.risk import RiskEngine
from bot.config import RiskLimits, INITIAL_EQUITY


class FakePos:
    def __init__(self, symbol, volume, typ):  # typ: 0=buy(+), 1=sell(-)
        self.symbol = symbol; self.volume = volume; self.type = typ


class FakeAcct:
    def __init__(self, equity, margin, margin_level):
        self.equity = equity; self.margin = margin; self.margin_level = margin_level


class FakeClient:
    """equity=INITIAL so drawdown=0; margin_level high so no margin block —
    isolates the concentration / net-directional logic under test."""
    def __init__(self, notionals):
        self._n = notionals
    def account(self):
        return FakeAcct(INITIAL_EQUITY, margin=100_000.0, margin_level=900.0)
    def positions(self):
        return [FakePos(s, abs(n), 0 if n > 0 else 1) for s, n in self._n.items()]
    def notional(self, symbol, volume):
        return abs(self._n[symbol])


lim = RiskLimits()  # single=0.50, net=0.85


def assess(notionals):
    return RiskEngine(FakeClient(notionals), lim).assess(now=0.0)


# A) Concentration: one name = 60% of gross (>0.50), net only 20% -> block new risk
a = assess({"XAUUSD": +360_000, "EURUSD": -240_000})
assert not a.allow_new_risk and not a.kill
assert any("of gross" in r for r in a.reasons), a.reasons

# B) Net-directional: two longs -> net 100% (>0.85), largest only 50% -> block
b = assess({"EURUSD": +300_000, "GBPUSD": +300_000})
assert not b.allow_new_risk and not b.kill
assert any("net directional" in r for r in b.reasons), b.reasons

# C) Diversified & balanced: largest 33%, net 33% -> new risk allowed
c = assess({"EURUSD": +200_000, "GBPUSD": -200_000, "USDJPY": +200_000})
assert c.allow_new_risk and not c.kill and c.risk_scale == 1.0, c.reasons


# --- Drawdown breaker: peak-to-trough + graduated de-risk (E0.4 + E0.5) ---
class DDClient:
    """No positions -> drawdown is the only thing under test. Equity is mutable."""
    def __init__(self): self.eq = INITIAL_EQUITY
    def account(self): return FakeAcct(self.eq, 0.0, 0.0)
    def positions(self): return []
    def notional(self, s, v): return 0.0


re = RiskEngine(DDClient(), lim)   # lim.daily_max_drawdown = L = 0.20 (tiers: L, 1.5L, 2L)
re.c.eq = 1_300_000; s = re.assess(now=0.0)              # set trailing peak = 1.3M
assert s.risk_scale == 1.0 and not s.kill and s.allow_new_risk

re.c.eq = 1_000_000; s = re.assess(now=0.0)              # dd = 0.231 in [L,1.5L) -> 50%
assert abs(s.risk_scale - 0.5) < 1e-9 and not s.kill and not s.allow_new_risk

re.c.eq = 900_000; s = re.assess(now=0.0)                # dd = 0.308 in [1.5L,2L) -> 25%
assert abs(s.risk_scale - 0.25) < 1e-9 and not s.kill and not s.allow_new_risk

re.c.eq = 750_000; s = re.assess(now=0.0)                # dd = 0.423 >= 2L -> hard kill
assert s.kill

# trailing peak holds; a recovery shrinks DD and RE-ARMS to full size (no freeze)
re.c.eq = 1_300_000; s = re.assess(now=0.0)
assert s.risk_scale == 1.0 and not s.kill and s.allow_new_risk

# measured vs PEAK, not initial equity: peaked at 1.3M then back to 1.0M (= start)
# still de-risks, whereas the old (vs INITIAL_EQUITY) code would see 0 drawdown.
re.c.eq = 1_000_000; s = re.assess(now=0.0)
assert abs(s.risk_scale - 0.5) < 1e-9

print("OK")
