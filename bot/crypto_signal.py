"""Phase-2 crypto fast-momentum signal (pure, no MT5/IO).

The fade scalper died because tiny mean-reversion edges drowned in spread. This
is the opposite thesis, on the instruments where it fits: crypto's minute-scale
moves dwarf the spread, so trade WITH momentum/breakouts, not against noise.

  - ENTRY: a momentum breakout — return over `lookback` bars beyond `entry_bps`
    (set well above crypto round-trip cost) -> go that direction.
  - LEAD-LAG: alts (ETH/SOL/XRP) require BTC's recent move to AGREE (confirmation);
    BTC itself trades on its own breakout (no lead).
  - EXIT: positions ride until the broker TP/SL (set at entry by the slot) or a
    REVERSE breakout flips them. Momentum fading just HOLDS — no mean-revert exit.

`position` and the returned target are the desired NET direction: -1/0/+1.
Tunable params are starting values; the backtest sweep + arming gate pick finals.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CryptoParams:
    lookback: int = 5          # bars in the momentum/breakout window
    entry_bps: float = 20.0    # min move over lookback to enter (must clear cost)
    lead_lookback: int = 3     # BTC lookback for the lead-lag confirmation
    lead_bps: float = 10.0     # min BTC move to count as a lead
    require_lead: bool = True  # alts must be confirmed by BTC


def _move_bps(closes, lookback) -> float:
    base = closes[-1 - lookback]
    if base == 0:
        return 0.0
    return (closes[-1] - base) / base * 1e4


def crypto_decision(closes, position: int, *, btc_closes=None, p: CryptoParams = None):
    """Return (target_direction, reason). target/position in {-1, 0, +1}.
    `btc_closes` provided -> this is an ALT and the move must be BTC-confirmed."""
    p = p or CryptoParams()
    if len(closes) < p.lookback + 1:
        return position, "insufficient data"
    ret_bps = _move_bps(closes, p.lookback)
    direction = 1 if ret_bps >= p.entry_bps else -1 if ret_bps <= -p.entry_bps else 0
    if direction == 0:
        return position, f"no breakout {ret_bps:.0f}bps"   # hold/stay flat

    # BTC lead-lag confirmation for alts
    if p.require_lead and btc_closes is not None:
        if len(btc_closes) < p.lead_lookback + 1:
            return position, "no BTC data"
        btc_bps = _move_bps(btc_closes, p.lead_lookback)
        lead = 1 if btc_bps >= p.lead_bps else -1 if btc_bps <= -p.lead_bps else 0
        if lead != direction:
            return position, f"unconfirmed by BTC ({btc_bps:.0f}bps)"

    if position == direction:
        return position, "hold"                            # ride; broker TP/SL exits
    return direction, f"breakout {ret_bps:.0f}bps" + (" +BTC" if btc_closes is not None else "")
