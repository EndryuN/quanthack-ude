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
    risk_scale: float = 1.0      # target-size multiplier (<1 = graduated de-risk)
    reasons: list = field(default_factory=list)


class RiskEngine:
    def __init__(self, client: MT5Client, limits: RiskLimits):
        self.c = client
        self.lim = limits
        # continuous-duration tripwire timers: name -> epoch when condition began
        self._since = {}
        # trailing equity peak for peak-to-trough drawdown (the scored metric)
        self._peak = INITIAL_EQUITY

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
        # Account-wide (all magics): competition tripwires (leverage, concentration,
        # net-directional) are measured on the WHOLE account, and the margin/equity
        # figures already are. Magic-filtering here understated risk once a second
        # strategy (e.g. the scalper) trades the same account. (E0.11)
        positions = self.c.positions(magic=None)

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
        risk_scale = 1.0

        # --- KILL-SWITCH: anti-liquidation. margin_level==0 means no positions. ---
        ml = acct.margin_level
        if positions and ml > 0 and ml < self.lim.kill_switch_margin_level:
            kill = True
            reasons.append(f"KILL: margin_level {ml:.0f}% < {self.lim.kill_switch_margin_level:.0f}%")

        # --- Drawdown circuit breaker: GRADUATED de-risk on PEAK-to-trough DD ---
        # Measured against the trailing equity peak (the competition's metric), not
        # the round's starting equity. Instead of flatten-and-freeze-for-the-round
        # (which forfeits the dominant return rank), step the book down as DD
        # deepens; flatten only at an extreme backstop. Re-arms automatically as
        # equity recovers and DD shrinks. (E0.4 + E0.5)
        self._peak = max(self._peak, acct.equity)
        dd = (self._peak - acct.equity) / self._peak if self._peak > 0 else 0.0
        L = self.lim.daily_max_drawdown
        if dd >= 2.0 * L:
            kill = True
            reasons.append(f"KILL: drawdown {dd*100:.1f}% >= {2*L*100:.0f}% (2x limit, hard backstop)")
        elif dd >= 1.5 * L:
            risk_scale = 0.25
            reasons.append(f"de-risk to 25%: drawdown {dd*100:.1f}% >= {1.5*L*100:.0f}%")
        elif dd >= L:
            risk_scale = 0.5
            reasons.append(f"de-risk to 50%: drawdown {dd*100:.1f}% >= {L*100:.0f}%")
        if risk_scale < 1.0:
            allow = False   # while de-risking, no new risk — only reductions

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
        # Concentration & net-directional are COMPUTED for display + the watch
        # tripwires below, but NOT used to block new risk. A hard new-risk gate
        # here froze the book: once one symbol drifted past the cap the engine
        # couldn't even add diversifying positions to dilute it, and in a
        # one-sided regime it couldn't express the view at all — so no trades.
        # Real protection stays via the margin kill, leverage/margin gates,
        # drawdown de-risk and per-position stops. (Competition only penalizes
        # concentration >90% / 30min, surfaced as a watch below.)

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
            allow_new_risk=allow and not kill, kill=kill,
            risk_scale=risk_scale, reasons=reasons,
        )

    def enforce(self, snap: RiskSnapshot, dry_run: bool):
        """If kill is signalled, flatten everything. Returns action taken."""
        if not snap.kill:
            return None
        if dry_run:
            return f"[DRY-RUN] would FLATTEN ALL: {'; '.join(snap.reasons)}"
        # account-wide: the kill must flatten EVERY magic (incl. the scalper), B1
        results = self.c.flatten_all(comment="KILL", all_account=True)
        return f"FLATTENED ALL ({len(results)} positions): {'; '.join(snap.reasons)}"
