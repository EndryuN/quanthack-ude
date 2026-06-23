"""CLI for the AI-signal backtester (Pydantic AI Gateway -> Doubleword).

Examples
--------
# quick smoke: 5 hourly decision points, online (realtime) inference
python run_ai_backtest.py --mode online --cadence-bars 4 --limit 5

# full month, online concurrent inference, then deterministic replay + report
python run_ai_backtest.py --mode online --cadence-bars 4

# cheapest: export an OpenAI-style JSONL, run it through Doubleword's batch
# tier / Autobatcher, then replay the returned results
python run_ai_backtest.py --mode export --out requests.jsonl --cadence-bars 4
#   ... submit requests.jsonl to the batch tier, download results.jsonl ...
python run_ai_backtest.py --mode replay --results results.jsonl --cadence-bars 4
"""
from __future__ import annotations

import argparse
import json
import time

from bot.dataloader import load_bars
from bot import ai_backtest as ab


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["online", "export", "replay"],
                    default="online")
    ap.add_argument("--cadence-bars", type=int, default=4,
                    help="decisions every N M15 bars (4 = hourly)")
    ap.add_argument("--warmup-bars", type=int, default=512)
    ap.add_argument("--aggression", type=float, default=None,
                    help="sizer aggression 0..1 (default: CONFIG.aggression)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap number of decision points (cheap smoke test)")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--model", default=None, help="override gateway model")
    ap.add_argument("--service-tier", default="flex",
                    help="realtime: 'priority'; async: 'flex'")
    ap.add_argument("--out", default="requests.jsonl",
                    help="(export) JSONL request file to write")
    ap.add_argument("--results", default=None,
                    help="(replay) batch results JSONL to read")
    ap.add_argument("--save-views", default=None,
                    help="(online) save inferred views to JSON for reuse")
    args = ap.parse_args()

    data, meta = load_bars()
    if not data:
        print("no cached bars found — run build_bars.py first")
        return
    symbols = list(data.keys())
    span = (min(rec["time"][0] for rec in data.values()),
            max(rec["time"][-1] for rec in data.values()))
    import numpy as np
    print(f"loaded {len(symbols)} symbols: {symbols}")
    print(f"period: {np.datetime64(int(span[0]),'s')} -> "
          f"{np.datetime64(int(span[1]),'s')}")

    print(f"\n[plan] building decision points (cadence={args.cadence_bars} bars, "
          f"warmup={args.warmup_bars})...")
    t0 = time.time()
    points, _ = ab.plan_decision_points(
        data, meta, symbols, cadence_bars=args.cadence_bars,
        warmup_bars=args.warmup_bars, limit=args.limit)
    print(f"[plan] {len(points)} decision points in {time.time()-t0:.1f}s")
    if not points:
        print("no decision points — check warmup/cadence vs. data length")
        return

    if args.mode == "export":
        ab.write_requests_jsonl(points, symbols, args.out,
                                model=args.model, service_tier=None)
        print(f"\n[export] wrote {len(points)} requests -> {args.out}")
        print("Submit this file to Doubleword's batch tier (or the Autobatcher),")
        print(f"then: python run_ai_backtest.py --mode replay --results <out> "
              f"--cadence-bars {args.cadence_bars}")
        return

    if args.mode == "replay":
        if not args.results:
            print("--mode replay requires --results <batch_output.jsonl>")
            return
        print(f"\n[replay] loading views from {args.results}...")
        views = ab.load_results_jsonl(args.results, symbols)
        print(f"[replay] {len(views)} views loaded")
    else:  # online
        print(f"\n[infer] online inference via gateway "
              f"(model={args.model or 'default'}, tier={args.service_tier}, "
              f"concurrency={args.concurrency})...")
        t0 = time.time()
        views = ab.run_online(points, symbols, concurrency=args.concurrency,
                              model=args.model, service_tier=args.service_tier)
        print(f"[infer] done in {time.time()-t0:.1f}s")
        if args.save_views:
            with open(args.save_views, "w", encoding="utf-8") as f:
                json.dump({k: {s: list(v) for s, v in vw.items()}
                           for k, vw in views.items()}, f)
            print(f"[infer] saved views -> {args.save_views}")

    print("\n[replay] running deterministic backtest...")
    res = ab.replay(data, meta, symbols, views, cadence_bars=args.cadence_bars,
                    warmup_bars=args.warmup_bars, aggression=args.aggression,
                    verbose=True)
    print("\n=== AI-SIGNAL BACKTEST RESULT ===")
    print(f"  return   : {res.ret*100:+.2f}%")
    print(f"  max DD   : {res.max_dd*100:.2f}%")
    print(f"  sharpe   : {res.sharpe:.3f}")
    print(f"  trades   : {res.n_trades}")
    print(f"  final eq : {res.final_equity:,.0f}")
    print(f"  blew up  : {res.blew_up}")


if __name__ == "__main__":
    main()
