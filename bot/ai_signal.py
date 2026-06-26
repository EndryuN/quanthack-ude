"""Claude-based directional signal layer.

Builds a compact market snapshot from recent bars, asks Claude for a per-
instrument directional view + calibrated confidence (Pydantic-validated
structured output), and returns it in the {symbol: (direction, confidence)}
form the sizer consumes.

Requires ANTHROPIC_API_KEY in the environment. Uses claude-opus-4-8 with
adaptive thinking. This is the one signal source left after momentum / mean-
reversion / stat-arb / order-flow all showed no edge on the data.
"""
from __future__ import annotations

import os
from typing import List, Literal

import numpy as np
import MetaTrader5 as mt5
from pydantic import BaseModel, Field

from .client import MT5Client
from .indicators import ema, ret_vol, momentum_z

MODEL = "claude-opus-4-8"

# Claude Opus pricing (USD per token): $5 / 1M input, $25 / 1M output
_USD_PER_IN = 5 / 1e6
_USD_PER_OUT = 25 / 1e6


def cost_usd(in_tokens: int, out_tokens: int) -> float:
    """Approximate USD cost of a Claude Opus call from token counts."""
    return in_tokens * _USD_PER_IN + out_tokens * _USD_PER_OUT


class InstrumentView(BaseModel):
    symbol: str
    direction: Literal["long", "short", "flat"]
    confidence: float = Field(ge=0.0, le=1.0,
                              description="0=no edge, 1=high conviction")
    rationale: str = Field(description="one concise sentence")


class MarketView(BaseModel):
    regime: str = Field(description="one line on the overall macro/risk regime")
    views: List[InstrumentView]


_DIR = {"long": 1, "short": -1, "flat": 0}


def _trend(close, fast, slow):
    if close is None or len(close) < slow + 2:
        return "?"
    return "up" if ema(close, fast)[-1] > ema(close, slow)[-1] else "dn"


def _closes(rates):
    return np.asarray(rates["close"], dtype=float) if rates is not None else None


def build_context(client: MT5Client, symbols, tf=mt5.TIMEFRAME_M15) -> str:
    """Compact per-instrument snapshot with MULTI-TIMEFRAME trend context:
    M15/H1/H4 trend alignment, multi-horizon returns (1h..3d), momentum, vol,
    and where price sits in its recent range. Gives Claude the bigger-picture
    trend, not just the last ~30h."""
    lines = []
    for s in symbols:
        m15 = client.bars(s, mt5.TIMEFRAME_M15, 120)
        if m15 is None or len(m15) < 100:
            continue
        h1 = client.bars(s, mt5.TIMEFRAME_H1, 240)
        h4 = client.bars(s, mt5.TIMEFRAME_H4, 120)
        c15, c1, c4 = _closes(m15), _closes(h1), _closes(h4)
        price = c15[-1]

        def ret(c, n):
            return (c[-1] / c[-1 - n] - 1.0) * 100 if c is not None and len(c) > n else 0.0

        r1h, r4h, r1d = ret(c15, 4), ret(c15, 16), ret(c15, 96)
        r3d = ret(c1, 72)                       # ~3 days from H1
        vol = ret_vol(c15, 48) * 100
        z = momentum_z(c15, 32)
        tm, th1, th4 = _trend(c15, 20, 80), _trend(c1, 10, 40), _trend(c4, 6, 24)

        # position within the last ~5 days' H1 range (0=low, 100=high)
        pos = 50.0
        if h1 is not None and len(h1) > 120:
            hi = np.max(np.asarray(h1["high"], float)[-120:])
            lo = np.min(np.asarray(h1["low"], float)[-120:])
            if hi > lo:
                pos = (price - lo) / (hi - lo) * 100

        line = (
            f"{s}: px={price:.6g} | ret 1h={r1h:+.2f} 4h={r4h:+.2f} 1d={r1d:+.2f} "
            f"3d={r3d:+.2f}% | trend M15={tm} H1={th1} H4={th4} | mom_z={z:+.2f} "
            f"vol/bar={vol:.2f}% | 5d-range={pos:.0f}%")
        if os.environ.get("AI_LONG_HORIZON") == "1":
            # EXPERIMENT (off by default): longer-horizon context from the H4 window
            # already fetched — a ~10-day return + 10-day trend + 10-day range
            # position (sized to fit the ~31-day backtest cache). A/B via AI_LONG_HORIZON=1.
            r10 = ret(c4, 60)               # ~10 days (60 H4 bars)
            t10 = _trend(c4, 12, 48)        # ~10-day trend (H4 EMA cross)
            pos10 = 50.0
            if h4 is not None and len(h4) > 60:
                hi4 = float(np.max(np.asarray(h4["high"], float)[-60:]))
                lo4 = float(np.min(np.asarray(h4["low"], float)[-60:]))
                if hi4 > lo4:
                    pos10 = (price - lo4) / (hi4 - lo4) * 100
            line += f" | 10d={r10:+.2f}% trend10d={t10} 10d-range={pos10:.0f}%"
        lines.append(line)
    return "\n".join(lines)


def snapshot_prices(client: MT5Client, symbols, tf=mt5.TIMEFRAME_M15) -> dict:
    """{symbol: last close} — the entry-price reference used to score each model's
    directional call against the realized move at the next refresh."""
    out = {}
    for s in symbols:
        bars = client.bars(s, tf, 2)
        c = _closes(bars)
        if c is not None and len(c):
            out[s] = float(c[-1])
    return out


SYSTEM = """You are the signal engine for an algorithmic entry in a simulated \
multi-day trading competition on forex, metals and crypto. Scoring is ~70% \
return rank among surviving accounts, with smaller weights on drawdown and \
Sharpe; a forced liquidation means elimination. Capital is simulated — there is \
no real-world risk, this is a sandboxed contest.

You are given a compact MULTI-TIMEFRAME snapshot for each instrument: returns \
over 1h-3d, trend direction on the M15 / H1 / H4 timeframes, momentum, \
volatility, and where price sits in its recent range. Weight setups where the \
timeframes AGREE more highly (aligned M15+H1+H4 trend = higher conviction; \
conflicting timeframes = lower). Be wary of chasing a move that is already \
stretched to the top/bottom of its range. Produce a directional view \
(long / short / flat) and a CALIBRATED confidence in [0,1] for each. Confidence should reflect genuine edge: most \
liquid-FX views deserve low confidence (markets are efficient); reserve higher \
confidence for clearer multi-horizon trends or coherent cross-asset macro \
stories (e.g. broad USD strength/weakness, risk-on/off). Prefer 'flat' when \
you have no view rather than guessing. Think about cross-asset consistency \
(USD pairs should agree on a USD direction; metals and crypto as risk \
barometers). Return a view for every instrument provided."""


def get_ai_views(client: MT5Client, symbols, model: str = MODEL,
                 effort: str = "high", context: str | None = None) -> tuple[dict, MarketView, dict]:
    """Returns ({symbol: (direction_int, confidence)}, raw MarketView, usage)."""
    import anthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    if context is None:
        context = build_context(client, symbols)
    anthropic_client = anthropic.Anthropic()
    user = (f"Instruments and recent action (competition universe = exactly "
            f"these {len(symbols)} symbols):\n\n{context}\n\n"
            f"Give a directional view + calibrated confidence for each.")

    # max_tokens generous so adaptive thinking can't truncate the structured
    # output (truncation -> stop_reason "max_tokens" -> parse fails). Still well
    # under the SDK's non-streaming guard, so no streaming needed.
    resp = anthropic_client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=MarketView,
    )

    # surface stop reason + token usage for cost/health visibility
    u = resp.usage
    usage = {"stop_reason": resp.stop_reason, "in_tokens": u.input_tokens,
             "out_tokens": u.output_tokens,
             "usd": cost_usd(u.input_tokens, u.output_tokens)}
    print(f"        [api] stop={usage['stop_reason']} in={usage['in_tokens']} "
          f"out={usage['out_tokens']} (~${usage['usd']:.3f})")
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"AI refused: {getattr(resp, 'stop_details', None)}")

    mv = resp.parsed_output
    if mv is None:
        raise RuntimeError(f"AI returned no parsable view (stop={resp.stop_reason})")

    views = {}
    requested = set(symbols)
    for v in mv.views:
        if v.symbol in requested:
            views[v.symbol] = (_DIR[v.direction], v.confidence)
    # any symbol Claude omitted -> flat
    for s in symbols:
        views.setdefault(s, (0, 0.0))
    return views, mv, usage
