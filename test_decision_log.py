"""DecisionLogger round-trips a record to JSONL and renders readable Markdown,
appends across calls, routes by day, and never crashes the caller."""
import json
import os
import tempfile

from bot.decision_log import DecisionLogger

d = tempfile.mkdtemp()
log = DecisionLogger(log_dir=d)

rec = {
    "ts": "2026-06-23T18:46:51", "mode": "dry-run",
    "models": {"primary": "claude-opus-4-8",
               "second_opinion": "nvidia/nemotron-3-super-120b-a12b"},
    "regime": {"claude": "USD strength", "nemotron": "USD bid"},
    "account": {"equity": 1019620, "gross_leverage": 3.1, "margin_usage": 0.11,
                "margin_level": 937, "return_pct": 0.0196, "drawdown": 0.001,
                "sharpe": -0.19},
    "cost": {"claude": {"in": 2494, "out": 1270, "usd": 0.044},
             "nemotron": {"in": 2510, "out": 880, "usd": 0.0}},
    "views": [
        {"symbol": "AUDUSD", "direction": "short", "confidence": 0.62,
         "claude": {"direction": "short", "confidence": 0.45, "rationale": "x"},
         "nemotron": {"direction": "short", "confidence": 0.60, "rationale": "y"},
         "agreement": "agree", "boosted": True},
        {"symbol": "EURCHF", "direction": "flat", "confidence": 0.0,
         "claude": {"direction": "flat", "confidence": 0.0, "rationale": "x"},
         "nemotron": {"direction": "flat", "confidence": 0.0, "rationale": "y"},
         "agreement": "nemotron_flat", "boosted": False},
    ],
    "orders": [{"symbol": "USDJPY", "side": "buy", "lots": 1.56,
                "cur": 2.86, "target": 4.42, "sl": 153.492}],
}
log.log_decision(rec)

jpath = os.path.join(d, "ai_decisions_2026-06-23.jsonl")
mpath = os.path.join(d, "ai_decisions_2026-06-23.md")
assert os.path.exists(jpath) and os.path.exists(mpath)

back = [json.loads(l) for l in open(jpath, encoding="utf-8")]
assert len(back) == 1 and back[0] == rec, "JSONL round-trips the exact record"

md = open(mpath, encoding="utf-8").read()
assert "## 18:46:51 · DRY-RUN" in md
assert "AUDUSD" in md and "agree" in md
assert "EURCHF" not in md, "flats omitted from MD table"
assert "BUY 1.56 USDJPY" in md

# failed-refresh event appends to same day file; header written once
log.log_decision({"ts": "2026-06-23T19:00:00", "event": "refresh_failed",
                  "error": "API down"})
back = [json.loads(l) for l in open(jpath, encoding="utf-8")]
assert len(back) == 2, "appends, never overwrites"
md = open(mpath, encoding="utf-8").read()
assert "REFRESH FAILED" in md and "API down" in md
assert md.count("# AI decisions — 2026-06-23") == 1

print("OK")
