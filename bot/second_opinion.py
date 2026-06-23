"""Nemotron second opinion via NVIDIA's hosted OpenAI-compatible API, plus the
pure consensus rule that merges it with Claude's primary view.

Claude always sets direction; Nemotron can only RAISE confidence when it
independently agrees — it never vetoes. The second opinion is strictly
additive: if it is unavailable, trading proceeds exactly as Claude-only."""
from __future__ import annotations

_DIR_STR = {1: "long", -1: "short", 0: "flat"}


def apply_consensus(claude_views, claude_rationale, nemotron_views, *, gain=0.5):
    """Merge Claude (primary, authoritative direction) with Nemotron (booster).

    Returns (sizer_views, view_records):
      sizer_views:  {sym: (dir_int, final_conf)}  -- fed to the sizer
      view_records: list of dicts in the decision-log `views[]` shape
    """
    available = bool(nemotron_views)
    sizer_views = {}
    records = []
    for sym, (dc, cc) in claude_views.items():
        nv = nemotron_views.get(sym) if available else None
        final = cc
        boosted = False
        if not available:
            agreement = "second_opinion_unavailable"
        elif nv is None or nv[0] == 0:
            agreement = "nemotron_flat"
        elif nv[0] == dc and dc != 0:
            agreement = "agree"
            final = cc + (1.0 - cc) * gain * nv[1]
            boosted = True
        else:
            agreement = "disagree"

        final = max(0.0, min(1.0, final))
        sizer_views[sym] = (dc, final)
        records.append({
            "symbol": sym,
            "direction": _DIR_STR[dc],
            "confidence": round(final, 4),
            "claude": {"direction": _DIR_STR[dc], "confidence": cc,
                       "rationale": claude_rationale.get(sym, "")},
            "nemotron": ({"direction": _DIR_STR[nv[0]], "confidence": nv[1],
                          "rationale": nv[2]} if nv is not None else None),
            "agreement": agreement,
            "boosted": boosted,
        })
    return sizer_views, records
