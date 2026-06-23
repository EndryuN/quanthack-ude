"""Execute an exported batch-request JSONL through the Pydantic AI Gateway.

The gateway proxies /chat/completions + /models but NOT the OpenAI Files/Batches
endpoints, so true server-side batch submission isn't reachable through it. This
runs the same exported requests concurrently on the async (service_tier="flex")
tier — Doubleword's reduced-cost async path — and writes a results JSONL in the
{custom_id, content} shape that run_ai_backtest.py --mode replay consumes.

When direct Doubleword API / Autobatcher access is available, submit
batch_requests.jsonl to the batch tier instead for the deepest discount; the
results file is interchangeable.

Usage:
  python submit_batch.py batch_requests.jsonl results.jsonl --concurrency 16
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

import httpx

from bot import gateway


MAX_ATTEMPTS = 7

async def _worker(client, sem, line, out, stats, tier):
    req = json.loads(line)
    cid = req["custom_id"]
    body = dict(req["body"])
    # flex = async (cheap, minutes latency); realtime = immediate. Async through
    # the gateway is too slow for a one-off backtest, so default to realtime.
    if tier == "flex":
        body["service_tier"] = "flex"
    else:
        body.pop("service_tier", None)
    async with sem:
        delay = 1.5
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await gateway.complete_responses_async(client, body)
                content = gateway.content_of_responses(resp)
                u = gateway.usage_of_responses(resp)
                stats["in"] += u["in"]
                stats["out"] += u["out"]
                stats["cost"] += u["cost"]
                out[cid] = content
                stats["done"] += 1
                if stats["done"] % 25 == 0:
                    print(f"  {stats['done']}/{stats['total']} done "
                          f"(~${stats['cost']:.3f})", flush=True)
                return
            except Exception as e:
                if attempt == MAX_ATTEMPTS:
                    print(f"  {cid} FAILED after {attempt}: {e}", flush=True)
                    stats["fail"] += 1
                else:
                    # exponential backoff with jitter (provider throttles bursts)
                    await asyncio.sleep(delay + 0.3 * attempt)
                    delay = min(delay * 2, 30.0)


def _load_existing(path):
    """Resume: return {custom_id: content} already present in a results file."""
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("custom_id") and d.get("content"):
                out[d["custom_id"]] = d["content"]
    return out


async def run(in_path, out_path, concurrency, tier):
    lines = [l for l in open(in_path, encoding="utf-8") if l.strip()]
    out = _load_existing(out_path)
    todo = [l for l in lines if json.loads(l)["custom_id"] not in out]
    stats = {"total": len(todo), "done": 0, "fail": 0, "in": 0, "out": 0,
             "cost": 0.0}
    print(f"{len(lines)} requests, {len(out)} already done -> {len(todo)} to fetch "
          f"(concurrency={concurrency}, tier={tier})...", flush=True)
    sem = asyncio.Semaphore(concurrency)
    t0 = time.time()
    async with httpx.AsyncClient(timeout=180.0) as client:
        await asyncio.gather(
            *(_worker(client, sem, l, out, stats, tier) for l in todo))
    with open(out_path, "w", encoding="utf-8") as f:
        for cid, content in out.items():
            f.write(json.dumps({"custom_id": cid, "content": content}) + "\n")
    dt = time.time() - t0
    print(f"done: {stats['done']} ok, {stats['fail']} failed in {dt/60:.1f} min")
    print(f"tokens: in={stats['in']:,} out={stats['out']:,} "
          f"cost=~${stats['cost']:.4f}")
    print(f"wrote {len(out)} results -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("in_path")
    ap.add_argument("out_path")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--tier", choices=["realtime", "flex"], default="realtime",
                    help="realtime=immediate; flex=async (cheap but slow)")
    args = ap.parse_args()
    asyncio.run(run(args.in_path, args.out_path, args.concurrency, args.tier))


if __name__ == "__main__":
    main()
