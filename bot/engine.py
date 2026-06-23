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
import time

import numpy as np
import MetaTrader5 as mt5

from .client import MT5Client
from .risk import RiskEngine
from .state import StateTracker
from .strategy import HybridStrategy
from .sizer import build_targets, SizerConfig
from .config import CONFIG, INITIAL_EQUITY, sl_pct_for, trail_pct_for


def _round_to_step(symbol, lots):
    info = mt5.symbol_info(symbol)
    step = (info.volume_step if info else 0.01) or 0.01
    return round(round(lots / step) * step, 4)


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

    def start(self):
        self.client.connect()
        mode = "DRY-RUN (no orders)" if self.dry_run else "*** LIVE TRADING ***"
        print(f"[engine] connected. mode={mode}")
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

        # 2. state / metrics
        m = self.state.update(acct.equity)

        # 3. refresh AI views on cadence (no-op between 15-min refreshes)
        if self.cfg.signal_source == "ai":
            now = time.time()
            if self._views is None or (now - self._views_ts) > self.cfg.ai_refresh_seconds:
                self._refresh_ai_views()

        # 4. reconcile ONLY when the signal set has changed. Targets are stable
        #    between AI refreshes, so reconciling every loop just churns spread
        #    (this was the SOL-churn bug). Risk + trailing still run every loop.
        n_orders = 0
        if self._signal_changed():
            targets = self._build_targets(acct)
            if not snap.allow_new_risk:
                targets = self._derisk_only(targets)
            n_orders = self._reconcile(targets)
            self._last_acted_ts = self._views_ts

        # 5. trail stops into profit on meaningful winners (every loop)
        if self.cfg.use_trailing_stop:
            self._update_trailing_stops()

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
        return build_targets(self.client, self._views, acct.equity,
                             self.cfg.aggression, self.sizer_cfg)

    def _refresh_ai_views(self):
        from .ai_signal import get_ai_views
        try:
            views, mv = get_ai_views(self.client, self.cfg.symbols,
                                     effort=self.cfg.ai_effort)
            self._views = views
            self._views_ts = time.time()
            self._regime = mv.regime
            active = {s: v for s, v in views.items() if v[0] != 0 and v[1] > 0}
            print(f"[{_ts()}] AI views refreshed | regime: {mv.regime}")
            for s, (d, c) in sorted(active.items(), key=lambda x: -x[1][1]):
                side = "LONG" if d > 0 else "SHORT"
                print(f"        {s:7s} {side:5s} conf={c:.2f}")
        except Exception as e:
            print(f"[{_ts()}] AI refresh FAILED: {e}")
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

    def _reconcile(self, targets):
        n = 0
        for s, target in targets.items():
            pos = self.client.position_for(s)
            cur = pos.signed_lots if pos else 0.0
            if not self.strategy.should_rebalance(cur, target, 0.0):
                continue
            delta = _round_to_step(s, target - cur)
            info = mt5.symbol_info(s)
            vmin = info.volume_min if info else 0.01
            if abs(delta) < vmin:
                # delta too small to trade; but if target is 0 force a close
                if target == 0 and cur != 0:
                    self._do_close(s)
                    n += 1
                continue
            if target == 0 and cur != 0:
                self._do_close(s)
            else:
                self._do_order(s, abs(delta), delta > 0, cur, target)
            n += 1
        return n

    def _update_trailing_stops(self):
        """Ratchet each position's broker SL toward profit once it's a meaningful
        winner. Never loosens a stop; only locks gains behind the peak."""
        import MetaTrader5 as _mt5
        for p in self.client.positions():
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
            ok, res = self.client.set_sl(p.symbol, cand)
            rc = getattr(res, "retcode", res)
            print(f"   [LIVE] TRAIL {p.symbol} SL -> {cand} ok={ok} ret={rc}")

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
            return
        ok, res = self.client.market_order(symbol, lots, is_buy, comment="ai", sl=sl)
        rc = getattr(res, "retcode", res)
        print(f"   [LIVE] {side} {lots} {symbol} SL={sl} -> ok={ok} ret={rc}")

    def _do_close(self, symbol):
        if self.dry_run:
            print(f"   [DRY] CLOSE {symbol}")
            return
        ok, res = self.client.close_symbol(symbol)
        rc = getattr(res, "retcode", res)
        print(f"   [LIVE] CLOSE {symbol} -> ok={ok} ret={rc}")

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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually send orders")
    args = ap.parse_args()
    Engine(dry_run=not args.live).start()
