"""Was each model right? Pure scoring of directional calls against realized price
moves — per-model hit rates so we can see whether Claude / Nemotron / each
ensemble voter actually adds edge (and feed it to Logfire Evals later).

A call is CORRECT when the price moved the way the model predicted between when it
was made and a later observation: long & price up, or short & price down. Flat
calls and zero/missing moves are skipped (no directional bet to score).
"""
from __future__ import annotations


def directional_correct(direction: int, entry_price, later_price):
    """True/False if a directional call was right; None when not scorable
    (flat call, missing price, or no move)."""
    if direction == 0 or entry_price is None or later_price is None:
        return None
    move = later_price - entry_price
    if move == 0:
        return None
    return (move > 0) == (direction > 0)


def hit_rates(samples) -> dict:
    """samples: iterable of (model, direction, entry_price, later_price).
    Returns {model: {hits, total, skipped, rate}} where rate = hits/total
    (None when nothing scorable)."""
    out: dict = {}
    for model, direction, entry, later in samples:
        rec = out.setdefault(model, {"hits": 0, "total": 0, "skipped": 0})
        res = directional_correct(direction, entry, later)
        if res is None:
            rec["skipped"] += 1
        else:
            rec["total"] += 1
            rec["hits"] += 1 if res else 0
    for rec in out.values():
        rec["rate"] = round(rec["hits"] / rec["total"], 4) if rec["total"] else None
    return out


class ModelScorer:
    """Accumulates per-model directional accuracy across AI refreshes.

    Each refresh, call `record(views_by_model, prices)` with every model's
    per-symbol direction and a price snapshot. The PREVIOUS refresh's calls are
    scored against the new prices (entry = price then, later = price now), appended
    to a rolling sample buffer, and `rates()` gives Claude vs Nemotron vs DeepSeek
    hit rates — the readout we surface to the panel + Logfire Evals.
    """

    def __init__(self, max_samples: int = 2000):
        self._pending = None      # (views_by_model, prices) from the last refresh
        self._samples: list = []
        self._max = max_samples

    def record(self, views_by_model: dict, prices: dict):
        """views_by_model: {model: {sym: direction_int}}; prices: {sym: price}."""
        if self._pending is not None:
            prev_views, prev_prices = self._pending
            for model, sym_dir in prev_views.items():
                for sym, d in sym_dir.items():
                    self._samples.append(
                        (model, d, prev_prices.get(sym), prices.get(sym)))
            if len(self._samples) > self._max:
                self._samples = self._samples[-self._max:]
        self._pending = ({m: dict(sd) for m, sd in views_by_model.items()},
                         dict(prices))

    def rates(self) -> dict:
        return hit_rates(self._samples)

    @property
    def n_samples(self) -> int:
        return len(self._samples)
