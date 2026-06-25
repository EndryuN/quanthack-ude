"""Net currency-factor exposure + a BLOCK-ONLY soft cap. Pure logic, no MT5.

Many separate USD-quoted positions (long USDCHF, long USDCAD, ...) stack into
one big USD bet that per-symbol concentration caps never see. This module
decomposes each position into its base/quote currency legs by notional (a long
EURUSD is +EUR / -USD) and nets them, so the engine can measure currency-factor
exposure and compare it to the consensus.

The cap is BLOCK-ONLY ("only if it's a good thing", spec E1.2): it can hold a
NEW or INCREASING trade at its current size when that trade would push a currency
leg past the cap AND the consensus for that symbol is weak. It NEVER forces a
reduction of an existing position, and a strong consensus is always allowed
through — so it can only ever REDUCE redundant risk, never fight a good signal.
"""
from __future__ import annotations


def legs(symbol: str):
    """(base, quote) currencies for a 6-char instrument:
    EURUSD->(EUR,USD), USDCHF->(USD,CHF), XAUUSD->(XAU,USD), BTCUSD->(BTC,USD)."""
    return symbol[:3], symbol[3:]


def currency_exposure(signed_notional: dict) -> dict:
    """signed_notional: {symbol: signed USD notional (long>0, short<0)}.
    Returns {currency: net signed notional}. A long base pair is +base / -quote."""
    net: dict = {}
    for sym, n in signed_notional.items():
        b, q = legs(sym)
        net[b] = net.get(b, 0.0) + n
        net[q] = net.get(q, 0.0) - n
    return net


def _increasing(tgt: float, cur: float) -> bool:
    """True if moving to `tgt` opens or adds in the SAME direction as `cur`
    (i.e. increases exposure). Reductions, flips, and closes are not increasing."""
    if cur == 0:
        return tgt != 0
    return abs(tgt) > abs(cur) and (tgt > 0) == (cur > 0)


def blocked_adds(target_notional: dict, current_notional: dict, conf: dict, *,
                 equity: float, cap_frac: float, strong_conf: float) -> set:
    """Return the set of symbols whose exposure-INCREASING target should be held
    at current size, because executing it would push a currency leg's |net| past
    `cap_frac * equity` while the symbol's consensus confidence is below
    `strong_conf`. Reductions/flips/closes are never blocked; strong-consensus
    adds are never blocked. Decisions are applied greedily so an earlier block is
    reflected in later projections.

    All notionals are signed USD (long>0, short<0)."""
    cap = abs(cap_frac) * equity
    held = dict(current_notional)   # the running book; allowed adds are applied
    blocked: set = set()
    for sym, tgt in target_notional.items():
        cur = current_notional.get(sym, 0.0)
        if not _increasing(tgt, cur):
            held[sym] = tgt                       # reduction/flip/close — always allowed
            continue
        if conf.get(sym, 0.0) >= strong_conf:
            held[sym] = tgt                       # strong consensus — don't fight it
            continue
        proj = currency_exposure({**held, sym: tgt})
        b, q = legs(sym)
        if cap > 0 and (abs(proj.get(b, 0.0)) > cap or abs(proj.get(q, 0.0)) > cap):
            blocked.add(sym)                      # held[sym] stays at current
        else:
            held[sym] = tgt                       # diversifying / within cap — allow
    return blocked
