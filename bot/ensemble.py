"""Multi-model ensemble consensus. Pure logic, no network.

Generalizes the Claude + single-Nemotron rule to Claude + N independent voters
(e.g. DeepSeek, Qwen, Nemotron served on Doubleword). Claude remains the
AUTHORITATIVE direction; the voters move the confidence:

  - each voter AGREES (+conf), DISAGREES (-conf), or is FLAT (0)
  - agree_score = mean of the non-flat votes, in [-1, 1]
  - net agreement boosts confidence toward 1; net disagreement shrinks it toward
    0 (symmetric — model conflict reduces size, the E1.1 idea)

This is a strict generalization of bot/second_opinion.apply_consensus: with one
agreeing voter it reproduces `cc + (1-cc)*gain*conf`; with one disagreeing voter
it shrinks by `cc*gain*conf`.
"""
from __future__ import annotations


def ensemble_consensus(claude_dir: int, claude_conf: float, voters, *, gain=0.5):
    """claude_dir in {-1,0,1}; voters: iterable of (direction:int, confidence:float).
    Returns (combined_conf, detail). Direction is always Claude's (callers keep it)."""
    cc = max(0.0, min(1.0, claude_conf))
    votes = []
    n_agree = n_disagree = n_flat = 0
    if claude_dir != 0:
        for d, c in voters:
            c = max(0.0, min(1.0, c))
            if d == 0:
                n_flat += 1
            elif d == claude_dir:
                votes.append(c); n_agree += 1
            else:
                votes.append(-c); n_disagree += 1
    agree_score = (sum(votes) / len(votes)) if votes else 0.0   # mean non-flat vote, [-1,1]
    if agree_score >= 0:
        combined = cc + (1.0 - cc) * gain * agree_score          # toward 1
    else:
        combined = cc + cc * gain * agree_score                  # toward 0 (agree_score<0)
    combined = max(0.0, min(1.0, combined))
    detail = {"agree_score": round(agree_score, 4),
              "n_agree": n_agree, "n_disagree": n_disagree, "n_flat": n_flat}
    return combined, detail
