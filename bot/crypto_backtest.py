"""Backtest + arming gate for the Phase-2 crypto momentum signal.

Replays crypto_decision over M1 closes. Exits model the BROKER TP/SL set at entry
(checked at each bar close — an approximation, since M1 closes don't show the
intrabar path) or a reverse-breakout flip. Each round-trip pays `cost_frac` once.

The gate is WALK-FORWARD: sweep params on a train split, lock the best, and report
its OUT-OF-SAMPLE result on the held-out test split — so we don't repeat the
fade's in-sample-argmax overfit. Arm only if the test split clears net-positive
after cost AND beats the AI engine's Sharpe with a sane trade count.
"""
from __future__ import annotations

from .crypto_signal import CryptoParams, crypto_decision

SWEEP_ENTRY = (15.0, 25.0, 40.0)     # breakout entry threshold (bps)
SWEEP_LOOKBACK = (3, 5, 8)           # momentum window (bars)
SWEEP_TP = (20.0, 40.0)              # broker take-profit (bps)
SWEEP_STOP = (20.0, 40.0)            # broker stop-loss (bps)
MIN_TRADES = 30                      # ignore overfit cells with too few trades


def backtest_crypto(closes, params: CryptoParams, cost_frac: float, *,
                    tp_bps: float, stop_bps: float, btc_closes=None) -> dict:
    """Replay; return summary stats in bps. Exits: broker TP/SL (checked at close)
    or a reverse-breakout flip. `cost_frac` charged once per closed round-trip."""
    pos, entry = 0, 0.0
    rets = []
    for i in range(params.lookback + 1, len(closes) + 1):
        window = closes[:i]
        price = window[-1]
        btc_w = btc_closes[:i] if btc_closes is not None else None
        if pos != 0:                                   # broker TP/SL first
            move_bps = (price - entry) / entry * 1e4 * pos
            if move_bps >= tp_bps or move_bps <= -stop_bps:
                capped = min(move_bps, tp_bps) if move_bps >= tp_bps else max(move_bps, -stop_bps)
                rets.append(capped / 1e4 - cost_frac)
                pos, entry = 0, 0.0
                continue
        target, _ = crypto_decision(window, pos, btc_closes=btc_w, p=params)
        if pos == 0 and target != 0:
            pos, entry = target, price
        elif pos != 0 and target != 0 and target != pos:   # reverse-breakout flip
            rets.append((price - entry) / entry * pos - cost_frac)
            pos, entry = target, price

    n = len(rets)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "net_pnl_bps": 0.0, "sharpe": 0.0, "max_dd_bps": 0.0}
    wins = sum(1 for r in rets if r > 0)
    net = sum(rets); mean = net / n
    std = (sum((r - mean) ** 2 for r in rets) / n) ** 0.5
    cum, peak, mdd = 0.0, 0.0, 0.0
    for r in rets:
        cum += r; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    return {"trades": n, "win_rate": round(wins / n, 4),
            "net_pnl_bps": round(net * 1e4, 2),
            "sharpe": round((mean / std) if std > 0 else 0.0, 4),
            "max_dd_bps": round(mdd * 1e4, 2)}


def sweep_crypto(closes, cost_frac, *, btc_closes=None) -> dict:
    """Grid over entry/lookback/tp/stop on one close series. Returns
    {rows, best} where best = highest-Sharpe row with >= MIN_TRADES."""
    rows, best = [], None
    for entry in SWEEP_ENTRY:
        for lb in SWEEP_LOOKBACK:
            for tp in SWEEP_TP:
                for stop in SWEEP_STOP:
                    p = CryptoParams(lookback=lb, entry_bps=entry)
                    r = backtest_crypto(closes, p, cost_frac, tp_bps=tp, stop_bps=stop,
                                        btc_closes=btc_closes)
                    row = {"entry": entry, "lookback": lb, "tp": tp, "stop": stop, **r}
                    rows.append(row)
                    if r["trades"] >= MIN_TRADES and (best is None or r["sharpe"] > best["sharpe"]):
                        best = row
    return {"rows": rows, "best": best}


def walk_forward(closes, cost_frac, *, btc_closes=None, train_frac=0.6,
                 engine_sharpe=0.1) -> dict:
    """Sweep on the first `train_frac` of the series, lock the best params, and
    evaluate them OUT-OF-SAMPLE on the rest. Returns the train pick, the test
    result, and an arm verdict (test net>0 AND test Sharpe>engine AND enough
    trades). Guards against the fade's in-sample overfit."""
    n = len(closes)
    cut = int(n * train_frac)
    train, test = closes[:cut], closes[cut:]
    btc_tr = btc_closes[:cut] if btc_closes is not None else None
    btc_te = btc_closes[cut:] if btc_closes is not None else None
    swept = sweep_crypto(train, cost_frac, btc_closes=btc_tr)
    best = swept["best"]
    if best is None:
        return {"available": True, "arm": False, "reason": "no train cell cleared MIN_TRADES",
                "train": None, "test": None}
    p = CryptoParams(lookback=best["lookback"], entry_bps=best["entry"])
    test_stats = backtest_crypto(test, p, cost_frac, tp_bps=best["tp"], stop_bps=best["stop"],
                                 btc_closes=btc_te)
    arm = (test_stats["trades"] >= MIN_TRADES and test_stats["net_pnl_bps"] > 0
           and test_stats["sharpe"] > engine_sharpe)
    reason = ("clears gate" if arm else
              "fails gate: " + ("few test trades" if test_stats["trades"] < MIN_TRADES
                                else "net<=0" if test_stats["net_pnl_bps"] <= 0
                                else f"sharpe {test_stats['sharpe']} <= engine {engine_sharpe}"))
    return {"available": True, "arm": arm, "reason": reason,
            "params": {"entry": best["entry"], "lookback": best["lookback"],
                       "tp": best["tp"], "stop": best["stop"]},
            "train": best, "test": test_stats}


def gate_for_symbol(symbol: str, *, engine_sharpe=0.1) -> dict:
    """Pull M1 for `symbol` (and BTCUSD for the lead-lag on alts) from MT5 and run
    the walk-forward gate. {available: False} when no data."""
    from .scalp_backtest import m1_bars_mt5
    from .config import is_crypto_alt
    closes, cost_frac = m1_bars_mt5(symbol)
    if not closes:
        return {"available": False, "symbol": symbol}
    btc_closes = None
    if is_crypto_alt(symbol):   # lead-lag only for crypto alts; FX/metals scalp pure momentum
        btc, _ = m1_bars_mt5("BTCUSD")
        btc_closes = btc or None
        if btc_closes and len(btc_closes) != len(closes):   # align lengths conservatively
            m = min(len(btc_closes), len(closes))
            closes, btc_closes = closes[-m:], btc_closes[-m:]
    out = walk_forward(closes, cost_frac, btc_closes=btc_closes, engine_sharpe=engine_sharpe)
    out.update({"symbol": symbol, "bars": len(closes), "cost_bps": round(cost_frac * 1e4, 2)})
    return out
