# Demo Runsheet — 3-min demo + 2-min Q&A (first round, in person)

**Mandate:** *show the actual product in action, with backtest data.*
**Judging (4 × 25%):** Future Potential · P&L Placement (does it work) · Creativity/Uniqueness · Pitching Quality. Only 25% is raw P&L — **75% is potential + creativity + pitch**, so lead with the AI-native story and the self-grading/backtest evidence, not a P&L curve.

## Before you walk in (5-min checklist)
- Engine + panel running on the laptop; hard-refresh the browser. Have **`run_panel_offline.ps1`** ready as a no-MT5 fallback (renders from committed logs/state).
- Open tabs/pages ready: **Dashboard**, **Model accuracy**, **Context Control**; `docs/BACKTEST_RESULTS.md` open in an editor.
- `python pnl_by_symbol.py` run once so you can quote your best instruments.
- Repo: be ready to **add the judges' GitHub account** as a collaborator (private repo `EndryuN/quanthack-ude`). Confirm `.env` is gitignored (it is) — no keys in the repo.

## The 3-minute demo (timed)

**0:00–0:20 — Hook + one-liner (Creativity).**
"Most teams bolt an LLM onto a statistical strategy as a narrator. We did the opposite: **the LLM *is* the strategy** — it decides direction and conviction — and we built the architecture that makes that safe, accountable, and self-auditing." Show the live **Dashboard** (equity curve, positions, the instrument chart with projection cones).

**0:20–1:00 — Product in action (P&L Placement / does it work).**
On the Dashboard: point to live equity vs the risk floor, open positions with P&L, the instrument chart (deep multi-timeframe history + per-model projection lines), and the **consensus over-time heatmap**. Hit **Market Read** to show all three models' regime narratives. "This is live, every ~5 seconds; the AI re-reads the market every 15 minutes."

**1:00–1:40 — The AI depth (Creativity + Future Potential).**
"Three frontier models vote **blind** — Claude decides direction; Nemotron and DeepSeek can only *raise* conviction on agreement, never flip or veto." Open **Context Control** — "this is *exactly* what each model sees: a token-cheap multi-timeframe snapshot, the same prompt to all three." (Optional: the human-override bar — additive vs deciding.)

**1:40–2:20 — Self-grading + BACKTEST DATA (Future Potential — the "how do you evaluate quality/consistency" sub-criterion).**
Open **Model accuracy**: "the system grades its *own* models — directional hit-rate **by asset category, over time**, against a 50% baseline. We don't claim edge; we *measure* it — a real lift on metals, near coin-flip on efficient FX." Then the **backtest**: "we backtested the AI signal through a gateway for **$0.29 vs ~$22 live** (98.7% cheaper), and swept aggression 0→1 — drawdown-capped, no blow-up at any level." (Show the `BACKTEST_RESULTS.md` table.)

**2:20–2:50 — Risk cage (P&L Placement / survivability).**
"Liquidation = elimination, so blow-up is *structurally* impossible: a kill-switch flattens everything at 150% margin (stop-out is 30%), graduated drawdown de-risk, a guaranteed broker stop on every position, trailing stops that lock profit. Risk runs *first* every tick — the AI and the human can't override it."

**2:50–3:00 — Close (Future Potential).**
"The loop: **AI decides → blind ensemble cross-checks → human supervises → the system grades itself.** It's broker- and asset-agnostic — a responsible, transparent way to put a frontier model in the driver's seat."

## 2-minute Q&A — have these ready (full answers in JUDGE_QA.md)
- **Where's your edge vs classical stat-arb?** → system not single-alpha; frontier vs cheap model; self-grading shows edge by category; risk cage protects the non-return score.
- **How does it take profit?** → no fixed targets; trailing stops + 15-min thesis re-eval + drawdown de-risk.
- **Are you profitable right now?** → sandbox, regime-dependent; we optimize for survive+rank and *show* our accuracy honestly.
- **What if the AI/connection drops?** → degrades to Claude-only/last views; risk+execution keep running every 5s.
- **Autonomous?** → fully; console is read-only supervision + optional steering.
- **Which instruments made the most?** → `pnl_by_symbol.py` (quote the top names).

## Submission blurb (the "short description of novelty in the AI-native way")
> **SIGNAL//LAYER** — an AI-native autonomous trading system where a frontier LLM (Claude Opus 4.8) *is* the strategy: it reads a compact multi-timeframe market snapshot and emits a directional view + calibrated conviction per instrument. Two further models (NVIDIA Nemotron, DeepSeek-V4-Pro) cross-check every call **blind**, and a disagreement-aware consensus *amplifies conviction on agreement only* — never flips or vetoes. The system **grades its own models** by asset category over time, so trust is earned from evidence. Deterministic code owns sizing, execution, and a hard risk floor the AI can never breach (blow-up is structurally impossible). Unlike setups that use an LLM as an advisory narrator on top of classical stat-arb, here the model is the decision-maker — and the contribution is the architecture that makes that safe, transparent, and self-auditing.
