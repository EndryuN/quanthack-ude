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
from .config import CONFIG, INITIAL_EQUITY, sl_pct_for, trail_pct_for
from .decision_log import DecisionLogger


def _round_to_step(symbol, lots):
    info = mt5.symbol_info(symbol)
    step = (info.volume_step if info else 0.01) or 0.01
    return round(round(lots / step) * step, 4)


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
    return f"  ⚠ FAILED ret={rc}" + (f" ({name})" if name else "")


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
        self.logger = DecisionLogger(cfg.log_dir) if cfg.log_decisions else None
        self._pending_log = None
        self._second_opinion_off = not bool(os.getenv("NVIDIA_API_KEY"))
        self._controls = None   # last-read operator controls (None when gated off)
        self._overrides = None   # last-read per-symbol overrides (None when gated off)
        self._last_positions = {}   # ticket -> {symbol, profit}; for stop-hit detection

    def start(self):
        self.client.connect()
        mode = "DRY-RUN (no orders)" if self.dry_run else "*** LIVE TRADING ***"
        print(f"[engine] connected. mode={mode}")
        if self.cfg.use_second_opinion and self._second_opinion_off:
            print("[engine] NVIDIA_API_KEY not set — second opinion disabled (Claude-only).")
        if not self.dry_run and not self.client.algo_allowed():
            raise RuntimeError("Algo Trading is OFF in the terminal — enable it.")
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

        # 2. state / metrics
        m = self.state.update(acct.equity)

        # 3. refresh AI views on cadence
        views_ts_before = self._views_ts
        if self.cfg.signal_source == "ai":
            now = time.time()
            due = self._views is None or (now - self._views_ts) > self.cfg.ai_refresh_seconds
            if due and (now - self._last_ai_attempt) >= self._ai_backoff:
                self._refresh_ai_views()
        refreshed = self._views_ts != views_ts_before

        # 4. reconcile ONLY when the signal set has changed
        orders = []
        if self._signal_changed():
            targets = self._build_targets(acct)
            paused = bool(self._controls and self._controls["paused"])
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

        # 5. trail stops into profit on meaningful winners (every loop)
        if self.cfg.use_trailing_stop:
            self._update_trailing_stops()

        # operator take-profit (gated): honor per-position TP overrides
        if self._overrides:
            self._apply_take_profits()

        # 6. write the decision record for this refresh (after reconcile)
        if refreshed and self.logger and self._pending_log:
            self._write_decision_record(acct, snap, m, orders)

        # report any broker-side stop/TP fills (positions that vanished without
        # an engine close this tick)
        self._report_stop_hits({o["symbol"] for o in orders if o.get("side") == "close"})

        self._print_status(acct, snap, m, n_orders)

    def _signal_changed(self):
        if self.cfg.signal_source == "ai":
            return self._views_ts != self._last_acted_ts
        return True   # hybrid path evaluated each loop (own hysteresis guards it)

    def _build_targets(self, acct):
        if self.cfg.signal_source == "hybrid":
            held = {p.symbol: p.signed_lots for p in self.client.positions()}
            return self.strategy.target_positions(
                self.client, self.cfg.symbols, acct.equity, held=held)
        if not self._views:
            return {s: 0.0 for s in self.cfg.symbols}
        agg = self.cfg.aggression
        if self._controls is not None:
            from .controls import effective_aggression
            agg = effective_aggression(self._controls, self.cfg.aggression)
        return build_targets(self.client, self._views, acct.equity, agg, self.sizer_cfg)

    # failure backoff: 30s, then doubling up to a 5-min cap between retries
    _AI_BACKOFF_MIN = 30.0
    _AI_BACKOFF_MAX = 300.0

    def _refresh_ai_views(self):
        from .ai_signal import get_ai_views, build_context
        from .second_opinion import apply_consensus
        self._last_ai_attempt = time.time()
        try:
            context = build_context(self.client, self.cfg.symbols)
            claude_views, mv, claude_usage = get_ai_views(
                self.client, self.cfg.symbols, effort=self.cfg.ai_effort,
                context=context)
            claude_rationale = {v.symbol: v.rationale for v in mv.views}

            nemo_views, nemo_regime, nemo_usage = {}, None, None
            if self.cfg.use_second_opinion and not self._second_opinion_off:
                from .second_opinion import get_second_opinion
                try:
                    nemo_views, nemo_regime, nemo_usage = get_second_opinion(
                        context, self.cfg.symbols, self.cfg.nemotron_model,
                        timeout=self.cfg.second_opinion_timeout)
                except Exception as e:
                    print(f"        [2nd] Nemotron second opinion failed: {e}")
                    nemo_views, nemo_regime, nemo_usage = {}, None, None

            sizer_views, view_records = apply_consensus(
                claude_views, claude_rationale, nemo_views,
                gain=self.cfg.nemotron_consensus_gain)

            if self._overrides:
                from .overrides import human_views
                from .second_opinion import apply_human_override
                sizer_views, view_records = apply_human_override(
                    sizer_views, view_records, human_views(self._overrides))

            self._views = sizer_views
            self._views_ts = time.time()
            self._regime = mv.regime
            self._ai_backoff = 0.0
            self._pending_log = {
                "models": {"primary": "claude-opus-4-8",
                           "second_opinion": (self.cfg.nemotron_model
                                              if nemo_usage else None)},
                "regime": {"claude": mv.regime, "nemotron": nemo_regime},
                "cost": {"claude": {"in": claude_usage["in_tokens"],
                                    "out": claude_usage["out_tokens"],
                                    "usd": claude_usage["usd"]},
                         "nemotron": ({"in": nemo_usage["in"],
                                       "out": nemo_usage["out"],
                                       "usd": nemo_usage["usd"]}
                                      if nemo_usage else None)},
                "views": view_records,
            }
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
        blocked = blocked_set(self._controls, self.cfg.symbols)
        if not blocked:
            return targets
        out = dict(targets)
        for s in blocked:
            if s in out:
                pos = self.client.position_for(s)
                cur = pos.signed_lots if pos else 0.0
                out[s] = derisk_clamp(out[s], cur)
        return out

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
                    self._do_close(s, profit=getattr(pos, "profit", 0.0),
                                   reason=self._close_reason(s, cur))
                    orders.append({"symbol": s, "side": "close",
                                   "lots": round(abs(cur), 4), "cur": cur,
                                   "target": 0.0, "sl": 0.0})
                continue
            if target == 0 and cur != 0:
                self._do_close(s, profit=getattr(pos, "profit", 0.0),
                               reason=self._close_reason(s, cur))
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

    def _update_trailing_stops(self):
        """Ratchet each position's broker SL toward profit once it's a meaningful
        winner. Never loosens a stop; only locks gains behind the peak."""
        import MetaTrader5 as _mt5
        from .overrides import pinned_set as _pinned_set
        _pinned = _pinned_set(self._overrides) if self._overrides else set()
        for p in self.client.positions():
            if p.symbol in _pinned:
                continue
            tick = self.client.tick(p.symbol)
            if not tick:
                continue
            tpct = trail_pct_for(p.symbol)
            is_long = p.type == _mt5.POSITION_TYPE_BUY
            price = tick.bid if is_long else tick.ask  # exit-side price
            info = _mt5.symbol_info(p.symbol)
            digits = info.digits if info else 5
            cur_sl = self._pos_sl(p)
            if is_long:
                cand = round(price * (1 - tpct), digits)
                # lock profit (above entry) and only ratchet the stop UP
                better = cand > p.price_open and (cur_sl == 0 or cand > cur_sl + price * 0.001)
            else:
                cand = round(price * (1 + tpct), digits)
                # lock profit (below entry) and only ratchet the stop DOWN
                better = cand < p.price_open and cand > 0 and \
                    (cur_sl == 0 or cand < cur_sl - price * 0.001)
            if not better:
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
        set_sltp (preserving the current SL). Idempotent: skip if already set."""
        from .overrides import take_profits
        tps = take_profits(self._overrides)
        if not tps:
            return
        for p in self.client.positions():
            tp = tps.get(p.symbol)
            if tp is None or abs(getattr(p, "tp", 0.0) - tp) < 1e-9:
                continue
            if self.dry_run:
                print(f"   [DRY] TP {p.symbol} -> {tp}")
                continue
            ok, res = self.client.set_sltp(p.symbol, getattr(p, "sl", 0.0), tp)
            print(f"   [LIVE] TP {p.symbol} -> {tp}{_rc(ok, res)}")

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
            print(f"[{_ts()}]    ⚑ STOP HIT {prev['symbol']}  "
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
        order_dir = 1 if is_buy else -1
        # only attach a stop-loss when this trade establishes/increases the
        # target position (its direction matches the target), not when reducing
        sl = 0.0
        if self.cfg.use_broker_sl and target != 0 and np.sign(target) == order_dir:
            sl = self.client.sl_price(symbol, is_buy, sl_pct_for(symbol))
        if self.dry_run:
            slstr = f" SL={sl}" if sl else ""
            print(f"   [DRY] {side} {lots} {symbol}  (cur={cur:+.2f} -> tgt={target:+.2f}){slstr}")
            return sl
        ok, res = self.client.market_order(symbol, lots, is_buy, comment="ai", sl=sl)
        slstr = f" SL={sl}" if sl else ""
        print(f"   [LIVE] {side} {lots} {symbol}{slstr}{_rc(ok, res)}")
        return sl

    def _do_close(self, symbol, profit=0.0, reason=""):
        pstr = f"  {profit:+,.0f}" if profit else ""
        rstr = f"  ({reason})" if reason else ""
        if self.dry_run:
            print(f"   [DRY] CLOSE {symbol}{pstr}{rstr}")
            return
        ok, res = self.client.close_symbol(symbol)
        print(f"   [LIVE] CLOSE {symbol}{pstr}{rstr}{_rc(ok, res)}")

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
            base = "AI view → flat"
        elif cur_dir and vd != cur_dir:
            base = f"AI flipped {'long' if vd > 0 else 'short'}"
        else:
            base = "de-risked"
        return base + (f" — {rat}" if rat else "")

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
