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
