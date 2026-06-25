"""Risk engine. Two jobs:

1. KILL-SWITCH (anti-liquidation): sits below the strategy and can flatten
   everything. Forced liquidation = instant elimination from the competition,
   so this is the single most important safety in the system.

2. GUARDS: report margin%, effective leverage, concentration, drawdown and
   whether new risk is allowed. Tracks how long each competition tripwire
   condition has been continuously true so we can stay inside the red-lines.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .client import MT5Client
from .config import RiskLimits, INITIAL_EQUITY


@dataclass
class RiskSnapshot:
    equity: float
    used_margin: float
    margin_level: float          # %
    margin_usage: float          # used_margin / equity
    gross_notional: float
    gross_leverage: float        # gross_notional / equity
    net_notional: float          # signed sum
    net_directional_frac: float  # |net| / gross
    largest_symbol: str
    largest_frac: float          # largest single-instrument / gross
    allow_new_risk: bool
    kill: bool                   # flatten-everything signal
    reasons: list = field(default_factory=list)


class RiskEngine:
    def __init__(self, client: MT5Client, limits: RiskLimits):
        self.c = client
        self.lim = limits
        # continuous-duration tripwire timers: name -> epoch when condition began
        self._since = {}

    def _track(self, name: str, active: bool, now: float) -> float:
        """Return seconds the named condition has been continuously true."""
        if active:
            self._since.setdefault(name, now)
            return now - self._since[name]
        else:
            self._since.pop(name, None)
            return 0.0

    def assess(self, now: float | None = None) -> RiskSnapshot:
        now = now if now is not None else time.time()
        acct = self.c.account()
        positions = self.c.positions()

        gross = 0.0
        net = 0.0
        by_symbol = {}
        for p in positions:
            n = self.c.notional(p.symbol, p.volume)
            gross += n
            signed = n if p.type == 0 else -n
            net += signed
            by_symbol[p.symbol] = by_symbol.get(p.symbol, 0.0) + n

        eq = acct.equity if acct.equity > 0 else 1e-9
        gross_lev = gross / eq
        margin_usage = acct.margin / eq
        net_frac = abs(net) / gross if gross > 0 else 0.0
        if by_symbol:
            largest_symbol = max(by_symbol, key=by_symbol.get)
            largest_frac = by_symbol[largest_symbol] / gross if gross > 0 else 0.0
        else:
            largest_symbol, largest_frac = "", 0.0

        reasons = []
        kill = False
        allow = True

        # --- KILL-SWITCH: anti-liquidation. margin_level==0 means no positions. ---
        ml = acct.margin_level
        if positions and ml > 0 and ml < self.lim.kill_switch_margin_level:
            kill = True
            reasons.append(f"KILL: margin_level {ml:.0f}% < {self.lim.kill_switch_margin_level:.0f}%")

        # --- Drawdown circuit breaker ---
        dd = (INITIAL_EQUITY - acct.equity) / INITIAL_EQUITY
        if dd >= self.lim.daily_max_drawdown:
            kill = True
            reasons.append(f"KILL: round drawdown {dd*100:.1f}% >= {self.lim.daily_max_drawdown*100:.0f}%")

        # --- Block new risk (soft) ---
        if positions and 0 < ml < self.lim.warn_margin_level:
            allow = False
            reasons.append(f"no-new-risk: margin_level {ml:.0f}% < {self.lim.warn_margin_level:.0f}%")
        if gross_lev > self.lim.max_gross_leverage:
            allow = False
            reasons.append(f"no-new-risk: gross lev {gross_lev:.1f}x > {self.lim.max_gross_leverage:.1f}x")
        if margin_usage > self.lim.max_margin_usage:
            allow = False
            reasons.append(f"no-new-risk: margin usage {margin_usage*100:.0f}% > {self.lim.max_margin_usage*100:.0f}%")
        # Concentration & net-directional caps (previously computed but never
        # enforced — see engine-improvements spec E0.1). Block NEW risk when the
        # book is too concentrated in one instrument or too one-sided overall.
        if gross > 0 and largest_frac > self.lim.max_single_instrument_frac:
            allow = False
            reasons.append(f"no-new-risk: {largest_symbol} {largest_frac*100:.0f}% of gross "
                           f"> {self.lim.max_single_instrument_frac*100:.0f}%")
        if gross > 0 and net_frac > self.lim.max_net_directional_frac:
            allow = False
            reasons.append(f"no-new-risk: net directional {net_frac*100:.0f}% "
                           f"> {self.lim.max_net_directional_frac*100:.0f}%")

        # --- Competition tripwire proximity (track duration, warn early) ---
        # >28x for >=30min = -20pts. Warn the moment we cross 25x.
        secs = self._track("lev>25", gross_lev > 25, now)
        if secs > 0:
            reasons.append(f"watch: lev>25x for {secs/60:.0f}min (penalty at 28x/30min)")
        secs = self._track("marg>85", margin_usage > 0.85, now)
        if secs > 0:
            reasons.append(f"watch: margin>85% for {secs/60:.0f}min (penalty at 90%/30min)")
        secs = self._track("conc>85", largest_frac > 0.85, now)
        if secs > 0:
            reasons.append(f"watch: {largest_symbol} {largest_frac*100:.0f}% of gross for {secs/60:.0f}min")

        return RiskSnapshot(
            equity=acct.equity, used_margin=acct.margin, margin_level=ml,
            margin_usage=margin_usage, gross_notional=gross, gross_leverage=gross_lev,
            net_notional=net, net_directional_frac=net_frac,
            largest_symbol=largest_symbol, largest_frac=largest_frac,
            allow_new_risk=allow and not kill, kill=kill, reasons=reasons,
        )

    def enforce(self, snap: RiskSnapshot, dry_run: bool):
        """If kill is signalled, flatten everything. Returns action taken."""
        if not snap.kill:
            return None
        if dry_run:
            return f"[DRY-RUN] would FLATTEN ALL: {'; '.join(snap.reasons)}"
        results = self.c.flatten_all(comment="KILL")
        return f"FLATTENED ALL ({len(results)} positions): {'; '.join(snap.reasons)}"
