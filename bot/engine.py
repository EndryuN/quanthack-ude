"""Main trading loop. Order of authority each tick:

    1. Kill-switch / risk assess  (can flatten everything; strategy can't override)
    2. State + Sharpe sampling
    3. Strategy target positions   (only if new risk is allowed)
    4. Reconcile live -> target via market orders

Runs in dry-run by default: it computes and logs intended orders but sends
nothing until you pass --live.
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np
import MetaTrader5 as mt5

from .client import MT5Client
from .risk import RiskEngine
from .state import StateTracker
from .strategy import HybridStrategy
from .sizer import build_targets, SizerConfig
from .config import (CONFIG, INITIAL_EQUITY, sl_pct_for, trail_pct_for,
                     trail_activate_pct_for)
from .decision_log import DecisionLogger


def _round_to_step(symbol, lots):
    info = mt5.symbol_info(symbol)
    step = (info.volume_step if info else 0.01) or 0.01
    return round(round(lots / step) * step, 4)


def _trail_candidate(is_long, price, price_open, cur_sl, activate_pct, trail_pct, digits):
    """New trailing SL to set, or None if the trail isn't armed or wouldn't improve.

    Arms only after price has moved `activate_pct` in the position's favor; then
    trails `trail_pct` behind the exit-side `price`, ratchets monotonically
    (never loosens), and never locks worse than break-even. Pure function."""
    if is_long:
        if price < price_open * (1 + activate_pct):
            return None                                   # not armed yet
        cand = round(price * (1 - trail_pct), digits)
        if cand <= price_open:
            return None                                   # would lock <= break-even
        if cur_sl == 0 or cand > cur_sl + price * 0.001:  # ratchet up only
            return cand
        return None
    # short
    if price > price_open * (1 - activate_pct):
        return None
    cand = round(price * (1 + trail_pct), digits)
    if cand <= 0 or cand >= price_open:
        return None
    if cur_sl == 0 or cand < cur_sl - price * 0.001:      # ratchet down only
        return cand
    return None


# Human names for the MT5 trade retcodes we might actually hit on failure.
# (10009 = TRADE_RETCODE_DONE = success, so it never appears here.)
_RETCODES = {
    10004: "requote", 10006: "request rejected", 10007: "cancelled by trader",
    10010: "only partially filled", 10013: "invalid request", 10014: "invalid volume",
    10015: "invalid price", 10016: "invalid stops", 10018: "market closed",
    10019: "not enough money", 10020: "prices changed", 10021: "no quotes",
    10027: "autotrading disabled (client)", 10026: "autotrading disabled (server)",
}


def _rc(ok, res) -> str:
    """Format a trade result for the log. Empty on success (the bare success
    code 10009 is just noise); on failure, the code + a human name."""
    if ok:
        return ""
    rc = getattr(res, "retcode", res)
    name = _RETCODES.get(rc)
    return f"  !! FAILED ret={rc}" + (f" ({name})" if name else "")


# Retcodes worth an immediate re-quote: nothing filled, the quote just moved.
# (Partial fill 10010, invalid volume/stops/money, autotrading-off are NOT here —
# retrying those is pointless or unsafe.)
_TRANSIENT_RETCODES = {10004, 10020, 10021}   # requote, prices changed, no quotes
_MAX_ORDER_ATTEMPTS = 3


def _is_transient(res) -> bool:
    return getattr(res, "retcode", res) in _TRANSIENT_RETCODES


class Engine:
    def __init__(self, cfg=CONFIG, dry_run=True):
        self.cfg = cfg
        self.dry_run = dry_run
        self.client = MT5Client(magic=cfg.magic, deviation=cfg.order_deviation_points)
        self.risk = RiskEngine(self.client, cfg.risk)
        self.state = StateTracker(INITIAL_EQUITY, state_dir=cfg.state_dir)
        self.strategy = HybridStrategy()
        self.sizer_cfg = SizerConfig()
        self._views = None          # {symbol: (dir, conf)} from AI
        self._views_ts = 0.0
        self._last_acted_ts = -1.0  # views_ts last reconciled (anti-churn gate)
        self._regime = ""
        self._last_ai_attempt = 0.0 # epoch of last refresh attempt (any outcome)
        self._ai_backoff = 0.0      # grows on failure so we don't hammer a down API
        self._force_refresh = False # operator "refresh now" one-shot (skip cadence)
        from .model_scoring import ModelScorer
        self._scorer = ModelScorer()  # per-model directional hit-rates across refreshes
        self.logger = DecisionLogger(cfg.log_dir) if cfg.log_decisions else None
        self._pending_log = None
        self._second_opinion_off = not bool(os.getenv("NVIDIA_API_KEY"))
        self._controls = None   # last-read operator controls (None when gated off)
        self._overrides = None   # last-read per-symbol overrides (None when gated off)
        self._last_positions = {}   # ticket -> {symbol, profit}; for stop-hit detection
        self._scalper = None        # integrated scalper slot (lazily constructed)
        self._active_symbols = list(cfg.symbols)   # engine universe minus ceded (per tick)

    def start(self):
        # H1: clean-open made use_broker_sl load-bearing — without it, live
        # positions would carry NO stop (liquidation = elimination). Refuse fast.
        if not self.dry_run and not self.cfg.use_broker_sl:
            raise RuntimeError("use_broker_sl is OFF while live — positions would "
                               "run with no stop-loss. Refusing to start.")
        self.client.connect()
        from .config import SCALPER as _SCALPER, assert_symbol_partition
        if _SCALPER.enabled:        # config-seeded assignments must be disjoint (G2)
            assert_symbol_partition(self.cfg.symbols, _SCALPER.symbols)
        mode = "DRY-RUN (no orders)" if self.dry_run else "*** LIVE TRADING ***"
        print(f"[engine] connected. mode={mode}")
        self._reset_scalper_session()   # never auto-resume a saved armed/running scalper
        self._sync_control_ack()        # stale ack must not swallow apply/flatten/refresh
        if self.cfg.use_second_opinion and self._second_opinion_off:
            print("[engine] NVIDIA_API_KEY not set - second opinion disabled (Claude-only).")
        if not self.dry_run and not self.client.algo_allowed():
            raise RuntimeError("Algo Trading is OFF in the terminal - enable it.")
        try:
            while True:
                self.tick()
                time.sleep(self.cfg.loop_seconds)
        except KeyboardInterrupt:
            print("\n[engine] stopped by user.")
        finally:
            self.client.shutdown()

    def tick(self):
        acct = self.client.account()

        # 1. risk first — kill-switch has absolute priority (every loop)
        snap = self.risk.assess()
        if snap.kill:
            action = self.risk.enforce(snap, self.dry_run)
            print(f"[{_ts()}] !!! {action}")
            self._print_status(acct, snap)
            return

        # --- operator control channel (Phase 2; gated, default off) ---
        # NOTE: this sits AFTER the kill-switch on purpose — the risk floor is
        # never gated by any control.
        self._controls = None
        self._overrides = None
        if self.cfg.use_control_panel:
            from .controls import load_controls, load_ack, atomic_write_json
            self._controls = load_controls(self.cfg.state_dir)
            from .overrides import load_overrides
            self._overrides = load_overrides(self.cfg.state_dir)
            ack = load_ack(self.cfg.state_dir)
            ack_path = os.path.join(self.cfg.state_dir, "control_ack.json")
            if self._controls["flatten_seq"] > ack["flatten_seq_done"]:
                self._operator_flatten()
                ack["flatten_seq_done"] = self._controls["flatten_seq"]
                atomic_write_json(ack_path, ack)
                self._print_status(acct, snap)
                return
            if self._controls["apply_seq"] > ack["apply_seq_done"]:
                self._last_acted_ts = -1.0   # force a reconcile next tick
                ack["apply_seq_done"] = self._controls["apply_seq"]
                atomic_write_json(ack_path, ack)
                self._print_status(acct, snap)
                return
            if self._controls.get("refresh_seq", 0) > ack.get("refresh_seq_done", 0):
                # operator "refresh consensus NOW": re-query AI views this tick,
                # bypassing the 15-min cadence and any failure backoff. Does NOT
                # return — the tick continues and reconciles on the fresh views.
                self._force_refresh = True
                self._ai_backoff = 0.0
                ack["refresh_seq_done"] = self._controls["refresh_seq"]
                atomic_write_json(ack_path, ack)

        # --- dynamic scalper assignment: cede assigned symbols from the engine's
        #     live universe and hand off any engine position on them (spec §9.4) ---
        from .config import live_engine_symbols, ALL_SYMBOLS as _ALL, SCALPER as _SCALPER
        from .controls import scalper_assignments
        assigned = (scalper_assignments(self._controls, _ALL) if self._controls is not None
                    else (list(_SCALPER.symbols) if _SCALPER.enabled else []))
        self._active_symbols = live_engine_symbols(assigned)
        self._handoff(assigned)
        paused = bool(self._controls and self._controls.get("paused"))

        # 2. state / metrics
        m = self.state.update(acct.equity)

        # 3. refresh AI views on cadence
        views_ts_before = self._views_ts
        if self.cfg.signal_source == "ai":
            now = time.time()
            due = (self._force_refresh or self._views is None
                   or (now - self._views_ts) > self.cfg.ai_refresh_seconds)
            if due and (self._force_refresh or (now - self._last_ai_attempt) >= self._ai_backoff):
                self._refresh_ai_views()
            self._force_refresh = False   # one-shot
        refreshed = self._views_ts != views_ts_before

        # 4. reconcile ONLY when the signal set has changed
        orders = []
        if self._signal_changed():
            targets = self._build_targets(acct)
            # graduated drawdown de-risk: scale the whole book down (E0.5)
            _scale = getattr(snap, "risk_scale", 1.0)
            if _scale < 1.0:
                targets = {s: t * _scale for s, t in targets.items()}
            if paused or not snap.allow_new_risk:
                targets = self._derisk_only(targets)
            if self._controls:
                targets = self._clamp_blocked(targets)
            if self._overrides:
                from .overrides import pinned_set
                _pinned = pinned_set(self._overrides)
                targets = {s: t for s, t in targets.items() if s not in _pinned}
            orders = self._reconcile(targets)
            self._last_acted_ts = self._views_ts
        n_orders = len(orders)

        # 4.5 GUARANTEE a stop on every position FIRST (liquidation = elimination)
        if self.cfg.use_broker_sl:
            self._ensure_stops()

        # 5. trail stops into profit on meaningful winners (every loop)
        if self.cfg.use_trailing_stop:
            self._update_trailing_stops()

        # operator take-profit / stop-loss (gated): honor per-position overrides
        if self._overrides:
            self._apply_take_profits()
            self._apply_stop_losses()

        # 6. write the decision record for this refresh (after reconcile)
        if refreshed and self.logger and self._pending_log:
            self._write_decision_record(acct, snap, m, orders)

        # report any broker-side stop/TP fills (positions that vanished without
        # an engine close this tick)
        self._report_stop_hits({o["symbol"] for o in orders if o.get("side") == "close"})

        # 7. scalper slot — LAST, so a kill / operator-flatten early-return skips it
        self._run_scalper(assigned, snap, paused)

        self._print_status(acct, snap, m, n_orders)

    def _signal_changed(self):
        if self.cfg.signal_source == "ai":
            return self._views_ts != self._last_acted_ts
        return True   # hybrid path evaluated each loop (own hysteresis guards it)

    def _build_targets(self, acct):
        if self.cfg.signal_source == "hybrid":
            held = {p.symbol: p.signed_lots for p in self.client.positions()}
            return self.strategy.target_positions(
                self.client, self._active_symbols, acct.equity, held=held)
        if not self._views:
            return {s: 0.0 for s in self._active_symbols}
        agg = self.cfg.aggression
        if self._controls is not None:
            from .controls import effective_aggression
            agg = effective_aggression(self._controls, self.cfg.aggression)
        # Only target the engine's LIVE universe — never a symbol ceded to the
        # scalper (else the engine re-opens it from stale views and the scalper
        # stands down on "held by another system").
        active = set(self._active_symbols)
        views = {s: v for s, v in self._views.items() if s in active}
        # Human overrides apply at RECONCILE time (every tick), reflecting the
        # latest overrides.json — so a Force LONG/SHORT/FLAT set in the panel
        # takes effect on the next tick, not only at the 15-min AI refresh.
        if self._overrides:
            from .overrides import human_views
            from .ai_signal import _DIR
            for sym, hv in human_views(self._overrides).items():
                if sym in views:
                    d = _DIR.get(str(hv.get("direction", "flat")).lower(), 0)
                    views[sym] = (d, max(0.0, min(1.0, float(hv.get("confidence", 0.0)))))
        return build_targets(self.client, views, acct.equity, agg, self.sizer_cfg)

    def _score_models(self, claude_views, nemo_views, extra_voters):
        """Record each model's per-symbol direction + a price snapshot; the scorer
        grades the PREVIOUS refresh's calls against the realized move and persists
        Claude/Nemotron/DeepSeek hit-rates to state/model_scores.json for the panel
        (and Logfire Evals). Best-effort — never breaks a refresh."""
        try:
            from .ai_signal import snapshot_prices
            prices = snapshot_prices(self.client, self._active_symbols)
            vbm = {"claude": {s: d for s, (d, _c) in claude_views.items()}}
            if nemo_views:
                vbm["nemotron"] = {s: v[0] for s, v in nemo_views.items()}
            for model, ev in extra_voters.items():
                label = "deepseek" if "deepseek" in model.lower() else model
                vbm[label] = {s: v[0] for s, v in ev.items()}
            self._scorer.record(vbm, prices)
            # write only once we actually have scored samples, so a restart (which
            # resets the in-memory scorer) doesn't clobber the file with empty {}
            if self.cfg.use_control_panel and self._scorer.n_samples > 0:
                from .controls import atomic_write_json
                atomic_write_json(
                    os.path.join(self.cfg.state_dir, "model_scores.json"),
                    {"rates": self._scorer.rates(), "n": self._scorer.n_samples,
                     "ts": _iso()})
        except Exception as e:
            print(f"[{_ts()}] [score] model scoring failed: {e}")

    # failure backoff: 30s, then doubling up to a 5-min cap between retries
    _AI_BACKOFF_MIN = 30.0
    _AI_BACKOFF_MAX = 300.0

    def _refresh_ai_views(self):
        from .ai_signal import get_ai_views, build_context
        from .second_opinion import apply_consensus
        self._last_ai_attempt = time.time()
        try:
            context = build_context(self.client, self._active_symbols)
            claude_views, mv, claude_usage = get_ai_views(
                self.client, self._active_symbols, effort=self.cfg.ai_effort,
                context=context)
            claude_rationale = {v.symbol: v.rationale for v in mv.views}

            # Nemotron is ALWAYS queried (so it's always recorded for the panel);
            # the runtime toggle only controls whether the ALGORITHM uses its input.
            nemo_views, nemo_regime, nemo_usage = {}, None, None
            if self.cfg.use_second_opinion and not self._second_opinion_off:
                from .second_opinion import get_second_opinion
                try:
                    nemo_views, nemo_regime, nemo_usage = get_second_opinion(
                        context, self._active_symbols, self.cfg.nemotron_model,
                        timeout=self.cfg.second_opinion_timeout)
                except Exception as e:
                    print(f"        [2nd] Nemotron second opinion failed: {e}")
                    nemo_views, nemo_regime, nemo_usage = {}, None, None

            # Gateway ensemble voters (e.g. DeepSeek) — ALWAYS queried + recorded
            # + scored when enabled; their INFLUENCE on sizing is gated separately
            # and OFF by default. Additive only: they never set/flip direction.
            ens_raw = {}
            if self.cfg.use_ensemble and self.cfg.ensemble_models:
                from .gateway_ensemble import get_ensemble_views
                try:
                    ens_raw = get_ensemble_views(
                        context, self._active_symbols, self.cfg.ensemble_models,
                        timeout=self.cfg.ensemble_timeout)
                except Exception as e:
                    print(f"        [ensemble] failed: {e}")
                    ens_raw = {}
            extra_voters = {m: vr[0] for m, vr in ens_raw.items()}   # {model: {sym:(d,c,r)}}

            from .controls import control_flag
            influence = control_flag(self._controls, "second_opinion", True)
            extra_influence = control_flag(self._controls, "ensemble_input", False)
            sizer_views, view_records = apply_consensus(
                claude_views, claude_rationale, nemo_views,
                gain=self.cfg.nemotron_consensus_gain, influence=influence,
                extra_voters=extra_voters, extra_influence=extra_influence)

            if self._overrides:
                from .overrides import human_views
                from .second_opinion import apply_human_override
                sizer_views, view_records = apply_human_override(
                    sizer_views, view_records, human_views(self._overrides))

            self._views = sizer_views
            self._views_ts = time.time()
            self._regime = mv.regime
            self._ai_backoff = 0.0
            ens_regime = {m: vr[1] for m, vr in ens_raw.items()}     # {model: regime str}
            self._pending_log = {
                "models": {"primary": "claude-opus-4-8",
                           "second_opinion": (self.cfg.nemotron_model
                                              if nemo_usage else None),
                           "ensemble": list(ens_raw.keys()) or None},
                "regime": {"claude": mv.regime, "nemotron": nemo_regime,
                           **ens_regime},
                "cost": {"claude": {"in": claude_usage["in_tokens"],
                                    "out": claude_usage["out_tokens"],
                                    "usd": claude_usage["usd"]},
                         "nemotron": ({"in": nemo_usage["in"],
                                       "out": nemo_usage["out"],
                                       "usd": nemo_usage["usd"]}
                                      if nemo_usage else None)},
                "views": view_records,
            }
            self._score_models(claude_views, nemo_views, extra_voters)
            active = {s: v for s, v in sizer_views.items() if v[0] != 0 and v[1] > 0}
            print(f"[{_ts()}] AI views refreshed | regime: {mv.regime}")
            for s, (d, c) in sorted(active.items(), key=lambda x: -x[1][1]):
                side = "LONG" if d > 0 else "SHORT"
                print(f"        {s:7s} {side:5s} conf={c:.2f}")
        except Exception as e:
            self._ai_backoff = min(max(self._ai_backoff * 2, self._AI_BACKOFF_MIN),
                                   self._AI_BACKOFF_MAX)
            age = time.time() - self._views_ts if self._views_ts else -1
            print(f"[{_ts()}] AI refresh FAILED: {e} | retry in "
                  f"{self._ai_backoff:.0f}s (views age={age:.0f}s)")
            if self.logger:
                self.logger.log_decision({"ts": _iso(), "event": "refresh_failed",
                                          "error": str(e)})
            if self._views is None:
                self._views = {}

    def _derisk_only(self, targets):
        """When new risk is blocked, never increase/flip exposure — only allow
        moves toward flat."""
        out = {}
        for s, tgt in targets.items():
            pos = self.client.position_for(s)
            cur = pos.signed_lots if pos else 0.0
            if cur == 0:
                out[s] = 0.0                              # don't open
            elif np.sign(tgt) != np.sign(cur):
                out[s] = 0.0                              # flip -> just close
            elif abs(tgt) > abs(cur):
                out[s] = cur                              # don't increase
            else:
                out[s] = tgt                              # reduction allowed
        return out

    def _clamp_blocked(self, targets):
        """Clamp blocked / not-allowed symbols toward flat (de-risk only).
        Never closes a position outright — just forbids new/increased risk."""
        from .controls import blocked_set, derisk_clamp
        blocked = blocked_set(self._controls, self._active_symbols)
        if not blocked:
            return targets
        out = dict(targets)
        for s in blocked:
            if s in out:
                pos = self.client.position_for(s)
                cur = pos.signed_lots if pos else 0.0
                out[s] = derisk_clamp(out[s], cur)
        return out

    def _handoff(self, assigned):
        """Flatten the ENGINE's own position on any scalper-assigned symbol so the
        scalper takes over cleanly (spec §9.4). position_for is magic-filtered to
        the engine, so this NEVER touches the scalper's own position. Runs every
        tick (cheap) so a ceded symbol can't linger if it ever reappears — once
        flat, position_for returns None and this is a no-op."""
        for s in assigned:
            pos = self.client.position_for(s)
            if pos and pos.signed_lots != 0:
                if self.dry_run:
                    print(f"[{_ts()}] [DRY] handoff {s}: flatten engine position -> scalper")
                else:
                    ok, res = self.client.close_symbol(s, comment="handoff")
                    print(f"[{_ts()}] handoff {s}: flattened engine position{_rc(ok, res)}")

    def _reset_scalper_session(self):
        """On launch the scalper starts the assign->gate->arm process OVER rather
        than resuming a persisted 'armed/running' state — a saved live-arm must
        never auto-fire on restart. Clears assignments, gate verdicts, and arm
        flags in controls.json plus the stale state/scalper.json."""
        if not self.cfg.use_control_panel:
            return
        from .controls import load_controls, atomic_write_json
        c = load_controls(self.cfg.state_dir)
        if (c.get("scalper_symbols") or c.get("scalper_armed")
                or c.get("scalper_armed_force") or c.get("scalper_gate")):
            c["scalper_symbols"] = []
            c["scalper_armed"] = []
            c["scalper_armed_force"] = []
            c["scalper_gate"] = {}
            atomic_write_json(os.path.join(self.cfg.state_dir, "controls.json"), c)
            print("[engine] scalper session reset on launch "
                  "(re-assign + re-gate + re-arm to scalp)")
        try:
            atomic_write_json(os.path.join(self.cfg.state_dir, "scalper.json"),
                              {"symbols": []})
        except Exception:
            pass

    def _sync_control_ack(self):
        """On launch, set the control-ack counters to match controls.json. The ack
        file persists across restarts but controls.json's *_seq can reset lower
        (fresh file / churn); if ack ends up AHEAD, the handlers (apply/flatten/
        refresh use `seq > seq_done`) silently swallow every future operator
        action — e.g. 'Apply preset' does nothing. Re-syncing guarantees the next
        bump is seen, without firing a phantom flatten on startup."""
        if not self.cfg.use_control_panel:
            return
        from .controls import load_controls, atomic_write_json
        c = load_controls(self.cfg.state_dir)
        ack = {"flatten_seq_done": int(c.get("flatten_seq", 0)),
               "apply_seq_done": int(c.get("apply_seq", 0)),
               "refresh_seq_done": int(c.get("refresh_seq", 0))}
        atomic_write_json(os.path.join(self.cfg.state_dir, "control_ack.json"), ack)

    def _run_scalper(self, assigned, snap, paused):
        """Tick the integrated scalper over its assigned symbols. Runs LAST in
        tick() so a kill / operator-flatten early-return skips it. Stays dry-run
        until the operator explicitly arms it (spec §9.5)."""
        from .config import SCALPER as _SCALPER
        if not assigned and not _SCALPER.enabled:
            return
        if self._scalper is None:
            from .scalper import Scalper
            self._scalper = Scalper(cfg=_SCALPER, dry_run=self.dry_run)
            try:
                self._scalper.connect()
            except Exception as e:
                print(f"[{_ts()}] [scalper] connect failed: {e}")
                self._scalper = None
                return
        self._scalper.cfg.symbols = list(assigned)
        self._scalper.state_dir = self.cfg.state_dir   # emit state/scalper.json for the panel
        self._scalper.dry_run = self.dry_run
        # Engine is the FINAL arming boundary (spec §3.3): a symbol trades LIVE
        # only when it is individually armed AND gate-cleared (or force-armed) —
        # never on the toggle alone. Arming is PER SYMBOL so adding/gating one
        # symbol never disarms another. Lock the gate's params.
        def _as_set(v):   # tolerate a stale boolean controls.json without crashing
            return set(v) if isinstance(v, (list, tuple, set)) else set()
        armed = _as_set((self._controls or {}).get("scalper_armed"))
        force = _as_set((self._controls or {}).get("scalper_armed_force"))
        gates = (self._controls or {}).get("scalper_gate") or {}
        live, params = set(), {}
        for s in assigned:
            if s not in armed:
                continue
            g = gates.get(s) or {}
            if g.get("cleared") or s in force:
                live.add(s)
                if g.get("params"):
                    params[s] = g["params"]
        self._scalper.live_symbols = live
        self._scalper.params_by_symbol = params
        try:
            self._scalper.step(allow_new_risk=snap.allow_new_risk and not paused)
        except Exception as e:   # the scalper must NEVER take down the engine tick
            print(f"[{_ts()}] [scalper] step failed (isolated): {e}")

    def _operator_flatten(self):
        """Operator-initiated flatten-all (panel 'Flatten now'). Additive to the
        kill-switch — never weakens it."""
        if self.dry_run:
            print(f"[{_ts()}] [DRY] OPERATOR FLATTEN ALL")
            return
        results = self.client.flatten_all(comment="PANEL")
        print(f"[{_ts()}] OPERATOR FLATTEN ALL ({len(results)} positions)")

    def _reconcile(self, targets):
        orders = []
        for s, target in targets.items():
            pos = self.client.position_for(s)
            cur = pos.signed_lots if pos else 0.0
            if not self.strategy.should_rebalance(cur, target, 0.0):
                continue
            delta = _round_to_step(s, target - cur)
            info = mt5.symbol_info(s)
            vmin = info.volume_min if info else 0.01
            if abs(delta) < vmin:
                if target == 0 and cur != 0:
                    if self._do_close(s, profit=getattr(pos, "profit", 0.0),
                                      reason=self._close_reason(s, cur)):
                        orders.append({"symbol": s, "side": "close",
                                       "lots": round(abs(cur), 4), "cur": cur,
                                       "target": 0.0, "sl": 0.0})
                continue
            if target == 0 and cur != 0:
                if self._do_close(s, profit=getattr(pos, "profit", 0.0),
                                  reason=self._close_reason(s, cur)):
                    orders.append({"symbol": s, "side": "close",
                                   "lots": round(abs(cur), 4), "cur": cur,
                                   "target": 0.0, "sl": 0.0})
            else:
                sl = self._do_order(s, abs(delta), delta > 0, cur, target)
                orders.append({"symbol": s, "side": "buy" if delta > 0 else "sell",
                               "lots": abs(delta), "cur": cur, "target": target,
                               "sl": sl})
        return orders

    def _write_decision_record(self, acct, snap, m, orders):
        parts = self._pending_log
        record = {
            "ts": _iso(),
            "mode": "live" if not self.dry_run else "dry-run",
            "models": parts["models"],
            "regime": parts["regime"],
            "account": {
                "equity": round(acct.equity, 2),
                "gross_leverage": round(snap.gross_leverage, 2),
                "margin_usage": round(snap.margin_usage, 4),
                "margin_level": round(acct.margin_level, 1),
                "return_pct": round(m.return_pct, 4) if m else 0.0,
                "drawdown": round(m.max_drawdown, 4) if m else 0.0,
                "sharpe": round(m.sharpe_15m, 3) if m else 0.0,
            },
            "cost": parts["cost"],
            "views": parts["views"],
            "orders": orders,
        }
        self.logger.log_decision(record)
        self._pending_log = None

    def _ensure_stops(self):
        """GUARANTEE every open position has a broker stop. Liquidation = instant
        elimination (competition red-line), so a position must never run naked.
        Order-time SL attach can be silently dropped (broker ignores sl on the
        DEAL, invalid-stops, or no quote); the trailing loop only ratchets stops
        on already-profitable positions and never establishes a base one. This
        sweep reads each position's ACTUAL sl and, if missing, attaches the
        SL_PCT failsafe via the reliable SLTP action."""
        import MetaTrader5 as _mt5
        from .overrides import pinned_set as _pinned_set, stop_losses as _stop_losses
        _pinned = _pinned_set(self._overrides) if self._overrides else set()
        _manual = set(_stop_losses(self._overrides)) if self._overrides else set()
        for p in self.client.positions():
            try:
                if getattr(p, "sl", 0.0) or p.symbol in _pinned or p.symbol in _manual:
                    continue                              # already protected (or operator-controlled)
                is_long = p.type == _mt5.POSITION_TYPE_BUY
                sl = self.client.sl_price(p.symbol, is_long, sl_pct_for(p.symbol))
                if not sl:
                    print(f"[{_ts()}]    !! {p.symbol} has NO stop and no quote to set one")
                    continue
                if self.dry_run:
                    print(f"   [DRY] ENSURE-SL {p.symbol} -> {sl}")
                    continue
                def _attach():
                    if self.cfg.use_control_panel:
                        return self.client.set_sltp(p.symbol, sl, getattr(p, "tp", 0.0))
                    return self.client.set_sl(p.symbol, sl)
                ok, res = _attach()
                if not ok and _is_transient(res):
                    ok, res = _attach()          # H2: one in-tick retry on a transient reject
                print(f"[{_ts()}]    ENSURE-SL {p.symbol} -> {sl}{_rc(ok, res)}")
            except Exception as e:   # never let an SL-ensure error stall the loop
                print(f"[{_ts()}]    !! ensure-stop {getattr(p, 'symbol', '?')} failed: {e}")

    def _update_trailing_stops(self):
        """Ratchet each position's broker SL toward profit once it's a meaningful
        winner. Never loosens a stop; only locks gains behind the peak."""
        import MetaTrader5 as _mt5
        from .overrides import pinned_set as _pinned_set, stop_losses as _stop_losses
        _pinned = _pinned_set(self._overrides) if self._overrides else set()
        _manual = set(_stop_losses(self._overrides)) if self._overrides else set()
        for p in self.client.positions():
            if p.symbol in _pinned or p.symbol in _manual:
                continue                                  # manual SL -> engine stops auto-trailing it
            tick = self.client.tick(p.symbol)
            if not tick:
                continue
            is_long = p.type == _mt5.POSITION_TYPE_BUY
            price = tick.bid if is_long else tick.ask  # exit-side price
            info = _mt5.symbol_info(p.symbol)
            digits = info.digits if info else 5
            cand = _trail_candidate(
                is_long, price, p.price_open, self._pos_sl(p),
                trail_activate_pct_for(p.symbol), trail_pct_for(p.symbol), digits)
            if cand is None:
                continue
            if self.dry_run:
                print(f"   [DRY] TRAIL {p.symbol} SL -> {cand}")
                continue
            if self.cfg.use_control_panel:
                ok, res = self.client.set_sltp(p.symbol, cand, getattr(p, "tp", 0.0))
            else:
                ok, res = self.client.set_sl(p.symbol, cand)
            print(f"   [LIVE] TRAIL {p.symbol} SL -> {cand}{_rc(ok, res)}")

    def _apply_take_profits(self):
        """For each symbol with a take_profit override, set a broker-side TP via
        set_sltp (preserving the current SL). The desired TP is CLAMPED to the
        symbol's digits + min stop distance on the profit side, so it matches what
        the broker stores — otherwise an unrounded value never equals the stored
        TP and we'd re-send it every tick (ret 10025 'no changes' spam). Idempotent:
        skip when already set, and treat 10025 as already-applied."""
        from .overrides import take_profits
        tps = take_profits(self._overrides)
        if not tps:
            return
        for p in self.client.positions():
            raw = tps.get(p.symbol)
            if raw is None:
                continue
            long = p.type == 0                              # 0=buy -> TP above; sell -> below
            ref = getattr(p, "price_current", 0.0) or getattr(p, "price_open", 0.0)
            tp = self.client.clamp_stop_price(p.symbol, ref, raw, "above" if long else "below")
            if not tp or abs(getattr(p, "tp", 0.0) - tp) < 1e-9:
                continue                                    # invalid, or already set
            if self.dry_run:
                print(f"   [DRY] TP {p.symbol} -> {tp}")
                continue
            ok, res = self.client.set_sltp(p.symbol, getattr(p, "sl", 0.0), tp)
            if ok or getattr(res, "retcode", None) == 10025:   # 10025 = no changes
                continue                                    # applied (or already there)
            print(f"   [LIVE] TP {p.symbol} -> {tp}{_rc(ok, res)}")

    def _apply_stop_losses(self):
        """For each symbol with a MANUAL stop_loss override, set the broker SL to it.
        Clamped to the symbol's digits + min distance, and NEVER looser than the
        catastrophe failsafe (so anti-liquidation always holds). The engine skips
        auto-trailing/ensure for these symbols — clearing the override resumes auto.
        Idempotent: skip when already set, treat 10025 as already-applied."""
        from .overrides import stop_losses
        sls = stop_losses(self._overrides)
        if not sls:
            return
        import MetaTrader5 as _mt5
        for p in self.client.positions():
            raw = sls.get(p.symbol)
            if raw is None:
                continue
            is_long = p.type == _mt5.POSITION_TYPE_BUY
            ref = getattr(p, "price_current", 0.0) or getattr(p, "price_open", 0.0)
            sl = self.client.clamp_stop_price(p.symbol, ref, raw, "below" if is_long else "above")
            floor = self.client.sl_price(p.symbol, is_long, sl_pct_for(p.symbol))  # loosest allowed
            if sl and floor:
                sl = max(sl, floor) if is_long else min(sl, floor)   # never looser than the DQ failsafe
            if not sl or abs(getattr(p, "sl", 0.0) - sl) < 1e-9:
                continue                                    # invalid, or already set
            if self.dry_run:
                print(f"   [DRY] SL {p.symbol} -> {sl}")
                continue
            ok, res = self.client.set_sltp(p.symbol, sl, getattr(p, "tp", 0.0))
            if ok or getattr(res, "retcode", None) == 10025:   # 10025 = no changes
                continue
            print(f"   [LIVE] SL {p.symbol} -> {sl}{_rc(ok, res)}")

    def _report_stop_hits(self, engine_closed):
        """Detect positions that vanished since the last tick WITHOUT the engine
        closing them — i.e. a broker-side stop-loss or take-profit fired — and
        report them with the last-observed P&L. Read-only; never raises."""
        try:
            current = {p.ticket: p for p in self.client.positions()}
        except Exception:
            return
        for tk, prev in self._last_positions.items():
            if tk in current or prev["symbol"] in engine_closed:
                continue
            print(f"[{_ts()}]    ** STOP HIT {prev['symbol']}  "
                  f"{prev['profit']:+,.0f}  (broker SL/TP)")
        self._last_positions = {tk: {"symbol": p.symbol,
                                     "profit": getattr(p, "profit", 0.0)}
                                for tk, p in current.items()}

    def _pos_sl(self, p):
        import MetaTrader5 as _mt5
        raw = _mt5.positions_get(ticket=p.ticket)
        return float(raw[0].sl) if raw else 0.0

    def _do_order(self, symbol, lots, is_buy, cur, target):
        side = "BUY" if is_buy else "SELL"
        if self.dry_run:
            print(f"   [DRY] {side} {lots} {symbol}  (cur={cur:+.2f} -> tgt={target:+.2f})")
            return 0.0
        # Open WITHOUT an inline SL: this broker REJECTS market DEALs that carry
        # one (ret 10006 — observed live on metals). The stop is attached via the
        # SLTP action by _ensure_stops in THIS SAME tick (step 4.5), which the
        # broker accepts; ensure also never loosens an existing stop (only sets
        # one when sl==0), so a same-direction add can't widen a trailed stop.
        ok, res = self.client.market_order(symbol, lots, is_buy, comment="ai")
        attempts = 1
        while (not ok) and _is_transient(res) and attempts < _MAX_ORDER_ATTEMPTS:
            attempts += 1   # re-quote: market_order pulls a fresh tick each call (E0.6)
            ok, res = self.client.market_order(symbol, lots, is_buy, comment="ai")
        astr = f" (attempt {attempts})" if attempts > 1 else ""
        print(f"   [LIVE] {side} {lots} {symbol}{astr}{_rc(ok, res)}")
        return 0.0   # SL attached by _ensure_stops this same tick

    def _do_close(self, symbol, profit=0.0, reason=""):
        """Close `symbol`. Returns True if the close happened (or dry-run), False
        on a live failure — the caller must NOT record a failed close as done
        (spec E0.7), or the book/log diverges from reality."""
        pstr = f"  {profit:+,.0f}" if profit else ""
        rstr = f"  ({reason})" if reason else ""
        if self.dry_run:
            print(f"   [DRY] CLOSE {symbol}{pstr}{rstr}")
            return True
        ok, res = self.client.close_symbol(symbol)
        attempts = 1
        while (not ok) and _is_transient(res) and attempts < _MAX_ORDER_ATTEMPTS:
            attempts += 1   # re-quote and retry a transient close failure (E0.6)
            ok, res = self.client.close_symbol(symbol)
        astr = f" (attempt {attempts})" if attempts > 1 else ""
        print(f"   [LIVE] CLOSE {symbol}{pstr}{rstr}{astr}{_rc(ok, res)}")
        return ok

    def _close_reason(self, symbol, cur):
        """Why is this position being closed? Derived from the current AI view
        + the latest decision rationale. cur = current signed lots."""
        v = (self._views or {}).get(symbol)
        vd = v[0] if v else 0
        cur_dir = (1 if cur > 0 else -1) if cur else 0
        rat = ""
        if self._pending_log:
            rec = next((r for r in self._pending_log.get("views", [])
                        if r.get("symbol") == symbol), None)
            if rec:
                rat = (rec.get("claude") or {}).get("rationale", "") or ""
        if vd == 0:
            base = "AI view -> flat"
        elif cur_dir and vd != cur_dir:
            base = f"AI flipped {'long' if vd > 0 else 'short'}"
        else:
            base = "de-risked"
        return base + (f" - {rat}" if rat else "")

    def _print_status(self, acct, snap, m=None, n_orders=0):
        line = (f"[{_ts()}] eq={acct.equity:,.0f} "
                f"lev={snap.gross_leverage:.1f}x marg={snap.margin_usage*100:.0f}% "
                f"ml={acct.margin_level:.0f}% ")
        if m is not None:
            line += (f"ret={m.return_pct*100:+.2f}% dd={m.max_drawdown*100:.1f}% "
                     f"sharpe={m.sharpe_15m:.2f} n={m.n_samples} ")
        line += f"orders={n_orders} new_risk={'Y' if snap.allow_new_risk else 'N'}"
        print(line)
        for r in snap.reasons:
            print(f"        - {r}")


def _ts():
    return time.strftime("%H:%M:%S")


def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually send orders")
    args = ap.parse_args()
    Engine(dry_run=not args.live).start()
