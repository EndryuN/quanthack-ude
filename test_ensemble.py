"""N-voter ensemble consensus (pure). Generalizes the Claude+Nemotron rule:
agreement boosts, disagreement shrinks, flat is ignored."""
from bot.ensemble import ensemble_consensus

A = lambda dir, conf, voters: ensemble_consensus(dir, conf, voters, gain=0.5)

# generalization check: ONE agreeing voter reproduces apply_consensus's boost
c, d = A(-1, 0.45, [(-1, 0.60)])
assert abs(c - 0.615) < 1e-9 and d["n_agree"] == 1            # 0.45 + 0.55*0.5*0.60

# ONE disagreeing voter shrinks confidence (symmetric, E1.1)
c, d = A(-1, 0.60, [(1, 0.80)])
assert abs(c - (0.60 + 0.60 * 0.5 * -0.80)) < 1e-9 and d["n_disagree"] == 1   # 0.36

# flat voter -> no change, counted as flat
c, d = A(1, 0.50, [(0, 0.0)])
assert c == 0.50 and d["n_flat"] == 1 and d["agree_score"] == 0.0

# multiple voters: two agree -> stronger boost than one
c2, _ = A(1, 0.40, [(1, 0.6), (1, 0.8)])     # agree_score = 0.7
c1, _ = A(1, 0.40, [(1, 0.6)])               # agree_score = 0.6
assert c2 > c1 > 0.40

# mixed: one agree, one disagree -> net near-neutral (mean of +0.6 and -0.8 = -0.1)
c, d = A(1, 0.50, [(1, 0.6), (-1, 0.8)])
assert d["n_agree"] == 1 and d["n_disagree"] == 1
assert abs(c - (0.50 + 0.50 * 0.5 * -0.1)) < 1e-9            # slight shrink

# unanimous disagreement collapses confidence hard
c, _ = A(1, 0.50, [(-1, 1.0), (-1, 1.0), (-1, 1.0)])         # agree_score = -1
assert abs(c - (0.50 + 0.50 * 0.5 * -1.0)) < 1e-9            # 0.25

# Claude flat -> confidence unchanged, voters ignored
c, d = A(0, 0.30, [(1, 0.9)])
assert c == 0.30 and d["agree_score"] == 0.0

# bounded in [0,1]
c, _ = A(1, 0.95, [(1, 1.0), (1, 1.0)])
assert 0.0 <= c <= 1.0

print("OK")
