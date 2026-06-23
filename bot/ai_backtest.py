"""AI-signal backtester.

Backtests the REAL AI signal (same prompt, same MarketView schema, same sizer
as live) over cached historical bars, routed through the Pydantic AI Gateway ->
Doubleword. This is the workload Doubleword's batch tier is built for: thousands
of independent, latency-insensitive LLM calls.

Design — three decoupled phases so the expensive LLM step is separable from the
cheap, deterministic backtest math:

  1. PLAN    walk the bar grid, build one market-snapshot prompt per decision
             point (using the live build_context()). Pure, no API calls.
  2. INFER   turn each prompt into a directional view. Either:
               * online  — concurrent realtime/flex calls through the gateway, or
               * batch   — export an OpenAI-style JSONL, run it through
                           Doubleword's batch tier / Autobatcher, get results back.
  3. REPLAY  feed the views through the live sizer (build_targets) and a
             deterministic mark-to-market + risk loop. Reproducible & free.

Higher timeframes: the cache is M15-only, so we resample M15 -> H1/H4 on the
fly (closed buckets only — no lookahead) and feed them to build_context exactly
as the live MT5 client would.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import numpy as np
import httpx
import MetaTrader5 as mt5

from .ai_signal import build_context
from .ai_signal_gw import build_request_body, parse_views
from .backtest import BacktestClient, BacktestResult, _sharpe, _max_dd
from .sizer import build_targets, SizerConfig
from .config import INITIAL_EQUITY, ACCOUNT_LEVERAGE, RiskLimits
from . import gateway

# timeframe int -> resample bucket seconds (mt5 constants are opaque tags)
_TF_SECONDS = {mt5.TIMEFRAME_H1: 3600, mt5.TIMEFRAME_H4: 4 * 3600}


def _resample(rec: np.ndarray, bucket: int):
    """M15 structured array -> (resampled rec, last_m15_index_per_bucket).
    Groups consecutive bars by floor(time / bucket). Vectorized OHLC."""
    t = rec["time"]
    bid = t // bucket
    change = np.empty(len(bid), dtype=bool)
    change[0] = True
    change[1:] = bid[1:] != bid[:-1]
    starts = np.flatnonzero(change)
    ends = np.empty_like(starts)
    ends[:-1] = starts[1:] - 1
    ends[-1] = len(bid) - 1
    out = np.empty(len(starts), dtype=rec.dtype)
    out["time"] = t[starts]
    out["open"] = rec["open"][starts]
    out["close"] = rec["close"][ends]
    out["high"] = np.maximum.reduceat(rec["high"], starts)
    out["low"] = np.minimum.reduceat(rec["low"], starts)
    return out, ends            # `ends` are M15-bar indices (symbol-local)


class AIBacktestClient(BacktestClient):
    """BacktestClient that also serves resampled H1/H4 bars, so the live
    build_context() (which asks for M15/H1/H4) works unchanged."""

    def __init__(self, data, meta):
        super().__init__(data, meta)
        self._h = {}            # symbol -> {tf: (rec, last_idx)}
        for s, rec in data.items():
            self._h[s] = {tf: _resample(rec, sec)
                          for tf, sec in _TF_SECONDS.items()}

    def bars(self, symbol, timeframe, count):
        i = self._idx[symbol][self.t]
        if i < 0 or i < 5:
            return None
        if timeframe not in _TF_SECONDS:                 # M15 / anything else
            lo = max(0, i - count + 1)
            return self.data[symbol][lo:i + 1]
        rec, last_idx = self._h[symbol][timeframe]
        # only buckets whose final M15 bar has already occurred (no lookahead)
        k = int(np.searchsorted(last_idx, i, side="right"))
        if k <= 0:
            return None
        lo = max(0, k - count)
        return rec[lo:k]


# ----------------------------------------------------------------------------
# phase 1 — plan
# ----------------------------------------------------------------------------
@dataclass
class DecisionPoint:
    custom_id: str
    t: int                 # global grid index
    time: int              # epoch seconds
    context: str           # market snapshot prompt


def _build_grid_client(data, meta, symbols):
    client = AIBacktestClient(data, meta)
    all_times = sorted(set().union(*[set(data[s]["time"].tolist()) for s in symbols]))
    grid = np.asarray(all_times)
    client.grid = grid
    for s in symbols:
        ts = data[s]["time"]
        pos = np.searchsorted(ts, grid, side="right") - 1
        pos = np.where(grid < ts[0], -1, pos)
        client._idx[s] = pos
    return client, grid


def plan_decision_points(data, meta, symbols, cadence_bars=4, warmup_bars=512,
                         limit=None) -> tuple[list, np.ndarray]:
    """One DecisionPoint per cadence step after warmup. cadence_bars=4 -> hourly
    decisions on M15 data (live refreshes every 15min = every 1 bar)."""
    client, grid = _build_grid_client(data, meta, symbols)
    points = []
    for t in range(warmup_bars, len(grid), cadence_bars):
        client.t = t
        ctx = build_context(client, symbols)
        if not ctx.strip():
            continue
        points.append(DecisionPoint(f"dp-{t}", t, int(grid[t]), ctx))
        if limit and len(points) >= limit:
            break
    return points, grid


# ----------------------------------------------------------------------------
# phase 2 — infer
# ----------------------------------------------------------------------------
def write_requests_jsonl(points, symbols, path, model=None, service_tier=None):
    """OpenAI-style batch input file: one request per decision point. Feed this
    to Doubleword's batch tier (or the Autobatcher) for the cheapest pricing."""
    with open(path, "w", encoding="utf-8") as f:
        for dp in points:
            line = {
                "custom_id": dp.custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": build_request_body(symbols, dp.context, model=model,
                                           service_tier=service_tier),
            }
            f.write(json.dumps(line) + "\n")
    return path


async def _run_online(points, symbols, concurrency, model, service_tier):
    sem = asyncio.Semaphore(concurrency)
    out, usage = {}, {"in": 0, "out": 0}
    async with httpx.AsyncClient(timeout=120.0) as client:
        async def one(dp):
            body = build_request_body(symbols, dp.context, model=model,
                                      service_tier=service_tier)
            async with sem:
                try:
                    # /responses, not /chat/completions: the latter injects a
                    # safety_identifier param the Doubleword backend rejects.
                    resp = await gateway.complete_responses_async(client, body)
                except Exception as e:
                    print(f"  {dp.custom_id} FAILED: {e}")
                    out[dp.custom_id] = {s: (0, 0.0) for s in symbols}
                    return
            views, _ = parse_views(gateway.content_of_responses(resp), symbols)
            u = gateway.usage_of_responses(resp)
            usage["in"] += u["in"]
            usage["out"] += u["out"]
            out[dp.custom_id] = views
        await asyncio.gather(*(one(dp) for dp in points))
    return out, usage


def run_online(points, symbols, concurrency=8, model=None, service_tier="flex"):
    """Concurrent realtime/flex calls through the gateway. Returns
    {custom_id: views}. Works today; for the cheapest run use export+batch."""
    views, usage = asyncio.run(
        _run_online(points, symbols, concurrency, model, service_tier))
    print(f"  inferred {len(views)} points | tokens in={usage['in']} "
          f"out={usage['out']}")
    return views


def load_results_jsonl(path, symbols) -> dict:
    """Parse a results JSONL into {custom_id: views}. Accepts both the OpenAI/
    Doubleword batch OUTPUT shape ({custom_id, response:{body:{choices:[...]}}})
    and our own simple {custom_id, content} shape."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            cid = d.get("custom_id")
            content = None
            if "content" in d:
                content = d["content"]
            elif "response" in d:
                body = d["response"].get("body", d["response"])
                try:
                    content = body["choices"][0]["message"]["content"]
                except Exception:
                    content = None
            if cid is None or content is None:
                continue
            out[cid], _ = parse_views(content, symbols)
    return out


# ----------------------------------------------------------------------------
# phase 3 — replay (deterministic; mirrors bot.backtest marking + risk)
# ----------------------------------------------------------------------------
def _should_trade(cur, tgt, vmin):
    return abs(tgt - cur) >= max(vmin, 0.20 * abs(cur))


def replay(data, meta, symbols, views_by_cid, cadence_bars=4, warmup_bars=512,
           aggression=None, risk=None, verbose=False) -> BacktestResult:
    from .config import CONFIG
    aggression = CONFIG.aggression if aggression is None else aggression
    risk = risk or RiskLimits()
    sizer_cfg = SizerConfig()
    client, grid = _build_grid_client(data, meta, symbols)

    equity, peak = INITIAL_EQUITY, INITIAL_EQUITY
    lots = {s: 0.0 for s in symbols}
    prev_price = {s: None for s in symbols}
    curve, times = [], []
    n_trades, blew_up = 0, False
    cur_views = {s: (0, 0.0) for s in symbols}

    for t in range(len(grid)):
        client.t = t

        # 1. mark-to-market on close-to-close (same convention as bot.backtest)
        for s in symbols:
            p = client.price(s)
            pp = prev_price[s]
            if p is not None and pp is not None and pp != 0 and lots[s] != 0:
                m = meta[s]
                per_lot_prev = m.contract_size if m.usd_base else m.contract_size * pp
                equity += lots[s] * per_lot_prev * ((p - pp) / pp)
            if p is not None:
                prev_price[s] = p

        peak = max(peak, equity)
        round_dd = (INITIAL_EQUITY - equity) / INITIAL_EQUITY

        if t < warmup_bars:
            curve.append(equity); times.append(int(grid[t])); continue

        # 2. risk: leverage / margin / kill-switch
        gross = sum(abs(lots[s]) * client._per_lot_usd(s)
                    for s in symbols if client.price(s) is not None)
        used_margin = gross / ACCOUNT_LEVERAGE
        margin_level = (equity / used_margin * 100) if used_margin > 0 else 1e9
        kill = (equity <= 0 or round_dd >= risk.daily_max_drawdown or
                (used_margin > 0 and margin_level < risk.kill_switch_margin_level))
        if equity <= 0:
            blew_up = True
        if kill:
            for s in symbols:
                if lots[s] != 0:
                    n_trades += 1
                    p = client.price(s)
                    if p is not None:
                        equity -= abs(lots[s]) * client._per_lot_usd(s) * meta[s].cost_frac
                    lots[s] = 0.0
            curve.append(equity); times.append(int(grid[t]))
            if blew_up:
                break
            continue

        # 3. refresh AI views on the decision cadence
        if (t - warmup_bars) % cadence_bars == 0:
            v = views_by_cid.get(f"dp-{t}")
            if v is not None:
                cur_views = v

        targets = build_targets(client, cur_views, equity, aggression, sizer_cfg)
        allow_new = (gross / equity <= risk.max_gross_leverage and
                     used_margin / equity <= risk.max_margin_usage)

        # 4. reconcile toward targets
        for s in symbols:
            if client.price(s) is None:
                continue
            tgt = targets.get(s, 0.0)
            cur = lots[s]
            if not allow_new and abs(tgt) > abs(cur) and np.sign(tgt) == np.sign(cur):
                continue
            vmin = meta[s].volume_min
            if not _should_trade(cur, tgt, vmin):
                continue
            equity -= abs(tgt - cur) * client._per_lot_usd(s) * meta[s].cost_frac
            lots[s] = tgt
            n_trades += 1

        curve.append(equity); times.append(int(grid[t]))

    res = BacktestResult(
        equity_curve=curve, times=times, n_trades=n_trades,
        ret=(equity - INITIAL_EQUITY) / INITIAL_EQUITY,
        max_dd=_max_dd(curve) if curve else 0.0,
        sharpe=_sharpe(curve), final_equity=equity, blew_up=blew_up,
    )
    if verbose:
        print(f"  ret={res.ret*100:+.2f}% dd={res.max_dd*100:.2f}% "
              f"sharpe={res.sharpe:.3f} trades={res.n_trades} "
              f"final={res.final_equity:,.0f} blewup={res.blew_up}")
    return res
