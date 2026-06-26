"""Per-asset-category directional ACCURACY OVER TIME for each model.

Pure grading (no I/O): given decision records + per-symbol bar series, score each
model's raw long/short call against the realized move to the next refresh, bucket by
asset class + time, and roll up into trailing-window + cumulative hit rates. The
decision logs carry no prices, so the caller supplies bars (history.py) for lookup.

A call is graded with bot.model_scoring.directional_correct (long & up / short & down
= hit; flat, missing price, or zero move = skipped). We grade the RAW per-model call
(claude / nemotron / deepseek), never the sized/combined result — so influence-off
models are still measured on what they actually said."""
import bisect
import datetime as _dt

from bot.model_scoring import directional_correct
from bot.config import asset_class
from . import data as _data

_DIR = {"long": 1, "short": -1, "flat": 0}
_MODELS = ("claude", "nemotron", "deepseek")
_CATS = ("FX", "METAL", "CRYPTO")


def _epoch(ts: str):
    """Naive ISO decision ts -> UTC epoch seconds (matches the epoch bars)."""
    try:
        return int(_dt.datetime.fromisoformat(ts).replace(tzinfo=_dt.timezone.utc).timestamp())
    except Exception:
        return None


def _price_at(epochs, closes, t):
    """Close of the last bar at-or-before t (None if t precedes the series)."""
    if not epochs or t is None:
        return None
    i = bisect.bisect_right(epochs, t) - 1
    return closes[i] if i >= 0 else None


def _rollup(results, tslist, window):
    """[bool] + parallel [ts] -> [{ts, rate(trailing N), n, cum_rate, cum_n}]."""
    out, hits = [], 0
    for k, ok in enumerate(results):
        hits += 1 if ok else 0
        w = results[max(0, k - window + 1): k + 1]
        out.append({"ts": tslist[k], "rate": round(sum(w) / len(w), 4), "n": len(w),
                    "cum_rate": round(hits / (k + 1), 4), "cum_n": k + 1})
    return out


def grade(decisions, series, window: int = 20) -> dict:
    """decisions: log records (any order); series: {sym: [(epoch,o,h,l,c)]}.
    Returns {window, categories:{CAT|ALL:{model:[points]}}, summary:{...}, skipped_symbols}."""
    decisions = sorted([d for d in decisions if d.get("ts")], key=lambda d: d["ts"])
    ec = {s: ([b[0] for b in bars], [b[4] for b in bars]) for s, bars in series.items() if bars}
    res = {c: {m: [] for m in _MODELS} for c in _CATS}      # ordered bool results
    res_ts = {c: {m: [] for m in _MODELS} for c in _CATS}   # parallel timestamps
    skipped = set()
    for i in range(len(decisions) - 1):
        cur, nxt = decisions[i], decisions[i + 1]
        t0, t1 = _epoch(cur["ts"]), _epoch(nxt["ts"])
        for v in cur.get("views", []):
            sym = v.get("symbol")
            if not sym:
                continue
            if sym not in ec:
                skipped.add(sym)
                continue
            cat = asset_class(sym)
            if cat not in res:
                continue
            ep, cl = ec[sym]
            entry, later = _price_at(ep, cl, t0), _price_at(ep, cl, t1)
            sides = {"claude": v.get("claude"), "nemotron": v.get("nemotron"),
                     "deepseek": _data._deepseek_of(v)}
            for m, side in sides.items():
                if not side:
                    continue
                d = _DIR.get(side.get("direction"))
                if d is None:
                    continue
                r = directional_correct(d, entry, later)
                if r is None:        # flat / missing / zero move -> skipped, not a miss
                    continue
                res[cat][m].append(bool(r))
                res_ts[cat][m].append(cur["ts"])

    categories, summary = {}, {}
    for c in _CATS:
        categories[c], summary[c] = {}, {}
        for m in _MODELS:
            rr, tt = res[c][m], res_ts[c][m]
            categories[c][m] = _rollup(rr, tt, window) if rr else []
            summary[c][m] = {"hits": sum(rr), "total": len(rr),
                             "rate": round(sum(rr) / len(rr), 4) if rr else None}
    # ALL = every category merged per model, in ts order
    categories["ALL"], summary["ALL"] = {}, {}
    for m in _MODELS:
        merged = sorted((t, ok) for c in _CATS for t, ok in zip(res_ts[c][m], res[c][m]))
        rr = [ok for _, ok in merged]
        tt = [t for t, _ in merged]
        categories["ALL"][m] = _rollup(rr, tt, window) if rr else []
        summary["ALL"][m] = {"hits": sum(rr), "total": len(rr),
                             "rate": round(sum(rr) / len(rr), 4) if rr else None}
    return {"window": window, "categories": categories, "summary": summary,
            "skipped_symbols": sorted(skipped)}
