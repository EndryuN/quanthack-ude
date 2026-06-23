"""Append-only per-day logger for AI decisions. One record per AI refresh,
written as a compact JSONL line and a readable Markdown section. Pure I/O:
hand it a record dict, it writes. No MT5, no network dependency."""
from __future__ import annotations

import json
import os


class DecisionLogger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir

    def _paths(self, ts: str):
        day = ts[:10]  # 'YYYY-MM-DD' from an ISO timestamp
        base = os.path.join(self.log_dir, f"ai_decisions_{day}")
        return base + ".jsonl", base + ".md", day

    def log_decision(self, record: dict) -> None:
        """Append `record` to today's JSONL + Markdown files. Swallows I/O
        errors (prints a note) so logging can never crash the trading loop."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            jsonl_path, md_path, day = self._paths(record["ts"])
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            new_md = not os.path.exists(md_path)
            with open(md_path, "a", encoding="utf-8") as f:
                if new_md:
                    f.write(f"# AI decisions — {day}\n\n")
                f.write(self._render_md(record))
        except OSError as e:
            print(f"   [log] decision-log write failed: {e}")

    def _render_md(self, record: dict) -> str:
        t = record["ts"][11:]  # HH:MM:SS
        if record.get("event") == "refresh_failed":
            return (f"## {t} · REFRESH FAILED\n"
                    f"{record.get('error', '')}\n\n---\n\n")

        reg = record["regime"]
        acct = record["account"]
        cost = record["cost"]
        lines = [f"## {t} · {record['mode'].upper()}"]
        lines.append(f"**Regime (Claude):** {reg.get('claude', '')}")
        if reg.get("nemotron"):
            lines.append(f"**Regime (Nemotron):** {reg['nemotron']}")
        lines.append(
            f"**Account:** eq={acct['equity']:,.0f} · lev={acct['gross_leverage']:.1f}x · "
            f"margin={acct['margin_usage'] * 100:.0f}% · ML={acct['margin_level']:.0f}% · "
            f"sharpe={acct['sharpe']:.2f}")
        cc = cost.get("claude") or {}
        costline = (f"**Cost:** Claude in={cc.get('in', 0)} out={cc.get('out', 0)} "
                    f"~${cc.get('usd', 0):.3f}")
        cn = cost.get("nemotron")
        if cn:
            costline += (f" · Nemotron in={cn.get('in', 0)} out={cn.get('out', 0)} "
                         f"~${cn.get('usd', 0):.3f}")
        else:
            costline += " · Nemotron unavailable"
        lines.append(costline)
        lines.append("")

        rows = [v for v in record["views"] if v["direction"] != "flat"]
        rows.sort(key=lambda v: -v["confidence"])
        if rows:
            lines.append("| Symbol | Final | Claude | Nemotron | Consensus |")
            lines.append("|--------|-------|--------|----------|-----------|")
            verdicts = {
                "disagree": "❌ disagree → Claude stands",
                "nemotron_flat": "⚪ no boost",
                "second_opinion_unavailable": "⚪ no second opinion",
            }
            for v in rows:
                final = f"{v['direction'].upper()} {v['confidence']:.2f}"
                c = v["claude"]
                cl = f"{c['direction']} {c['confidence']:.2f}"
                n = v.get("nemotron")
                ns = f"{n['direction']} {n['confidence']:.2f}" if n else "—"
                if v["agreement"] == "agree":
                    verdict = "✅ agree → boosted" if v["boosted"] else "✅ agree"
                else:
                    verdict = verdicts.get(v["agreement"], v["agreement"])
                lines.append(f"| {v['symbol']} | {final} | {cl} | {ns} | {verdict} |")
            lines.append("")

        lines.append("**Orders placed:**")
        orders = record.get("orders", [])
        if orders:
            for o in orders:
                if o["side"] == "close":
                    lines.append(f"- CLOSE {o['symbol']}")
                else:
                    sl = f"  SL={o['sl']}" if o.get("sl") else ""
                    lines.append(f"- {o['side'].upper()} {o['lots']} {o['symbol']}  "
                                 f"({o['cur']:+.2f} → {o['target']:+.2f}){sl}")
        else:
            lines.append("- (none)")
        lines += ["", "---", ""]
        return "\n".join(lines)
