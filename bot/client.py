"""Thin, defensive wrapper around the MetaTrader5 Python API.

Everything that talks to the terminal goes through here so the rest of the
system never touches raw mt5 calls. All sizing math is derived from the live
per-symbol contract size, never hardcoded.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import MetaTrader5 as mt5

from .config import ALL_SYMBOLS


@dataclass
class Account:
    login: int
    server: str
    currency: str
    balance: float
    equity: float
    margin: float            # used margin
    margin_free: float
    margin_level: float      # % ; 0 when no positions
    leverage: int
    profit: float


@dataclass
class Position:
    ticket: int
    symbol: str
    volume: float            # lots (signed convention handled separately)
    type: int                # 0=buy, 1=sell
    price_open: float
    price_current: float
    profit: float
    sl: float = 0.0
    tp: float = 0.0
    magic: int = 0
    @property
    def signed_lots(self) -> float:
        return self.volume if self.type == mt5.POSITION_TYPE_BUY else -self.volume


_OWN = object()   # sentinel: positions() default == filter to this client's magic


def _filter_by_magic(raw, magic, own):
    """Keep raw positions matching `magic`. magic=None -> keep all.
    `own` is unused here but kept for caller symmetry."""
    if magic is None:
        return list(raw)
    return [p for p in raw if getattr(p, "magic", None) == magic]


class MT5Client:
    def __init__(self, magic: int = 990623, deviation: int = 50):
        self.magic = magic
        self.deviation = deviation
        self._filling = {}   # symbol -> chosen ORDER_FILLING_*

    # ---- lifecycle -----------------------------------------------------
    def connect(self) -> None:
        if not mt5.initialize():
            raise RuntimeError(f"mt5.initialize() failed: {mt5.last_error()}")
        ti = mt5.terminal_info()
        if ti is None:
            raise RuntimeError("terminal_info() returned None")
        if not ti.connected:
            raise RuntimeError("terminal is not connected to broker")
        self.ensure_symbols(ALL_SYMBOLS)

    def shutdown(self) -> None:
        mt5.shutdown()

    def algo_allowed(self) -> bool:
        """True only when the 'Algo Trading' toolbar button is enabled AND the
        account permits trading. Orders silently fail otherwise."""
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        return bool(ti and ti.trade_allowed and ai and ai.trade_allowed)

    # ---- symbols / market data ----------------------------------------
    def ensure_symbols(self, symbols, warmup_timeout: float = 8.0) -> None:
        for s in symbols:
            info = mt5.symbol_info(s)
            if info is None:
                continue
            if not info.visible:
                mt5.symbol_select(s, True)
        # Newly-selected symbols stream quotes after a short delay; wait until
        # every symbol has a live bid so the engine never reads zeros.
        deadline = time.time() + warmup_timeout
        while time.time() < deadline:
            missing = [s for s in symbols
                       if not (self.tick(s) and self.tick(s).bid > 0)]
            if not missing:
                return
            time.sleep(0.5)

    def tick(self, symbol: str):
        return mt5.symbol_info_tick(symbol)

    def mid(self, symbol: str):
        t = self.tick(symbol)
        if not t or t.bid == 0 or t.ask == 0:
            return None
        return (t.bid + t.ask) / 2.0

    def spread_points(self, symbol: str):
        info = mt5.symbol_info(symbol)
        t = self.tick(symbol)
        if not info or not t or t.bid == 0:
            return None
        return (t.ask - t.bid) / info.point

    def contract_size(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        return info.trade_contract_size if info else 0.0

    def bars(self, symbol: str, timeframe, count: int):
        """Return recent OHLC bars (numpy structured array) or None."""
        return mt5.copy_rates_from_pos(symbol, timeframe, 0, count)

    # ---- account / positions ------------------------------------------
    def account(self) -> Account:
        a = mt5.account_info()
        if a is None:
            raise RuntimeError(f"account_info() failed: {mt5.last_error()}")
        return Account(a.login, a.server, a.currency, a.balance, a.equity,
                       a.margin, a.margin_free, a.margin_level, a.leverage, a.profit)

    def positions(self, magic=_OWN):
        """Open positions. By default returns only THIS client's own (by magic)
        so two systems on one account don't see each other — a no-op today since
        only one magic trades. Pass magic=None for all account positions."""
        m = self.magic if magic is _OWN else magic
        raw = _filter_by_magic(mt5.positions_get() or (), m, self.magic)
        out = []
        for p in raw:
            out.append(Position(p.ticket, p.symbol, p.volume, p.type,
                                p.price_open, p.price_current, p.profit,
                                sl=getattr(p, "sl", 0.0), tp=getattr(p, "tp", 0.0),
                                magic=getattr(p, "magic", 0)))
        return out

    def position_for(self, symbol: str):
        for p in self.positions():
            if p.symbol == symbol:
                return p
        return None

    def realized_profit(self, magic=_OWN, days: float | None = None) -> float:
        """Net realized P&L (profit + swap) of CLOSED deals for this client's
        magic. `days` limits the lookback window; None == all history.
        Open-position floating P&L is NOT included — see positions()."""
        import datetime as _dt
        m = self.magic if magic is _OWN else magic
        frm = (_dt.datetime.now() - _dt.timedelta(days=days)) if days \
            else _dt.datetime(2000, 1, 1)
        to = _dt.datetime.now() + _dt.timedelta(days=1)
        deals = mt5.history_deals_get(frm, to) or ()
        total = 0.0
        for d in deals:
            if m is not None and getattr(d, "magic", None) != m:
                continue
            total += getattr(d, "profit", 0.0) + getattr(d, "swap", 0.0)
        return total

    # ---- notional / exposure helpers ----------------------------------
    def notional(self, symbol: str, lots: float) -> float:
        """USD notional of `lots` lots. Assumes USD-quoted or *USD pair; for
        cross/JPY pairs this is an approximation good enough for risk caps."""
        info = mt5.symbol_info(symbol)
        t = self.tick(symbol)
        if not info or not t:
            return 0.0
        price = (t.bid + t.ask) / 2.0
        # contract_size * price = value of 1 lot in quote currency.
        # For *USD symbols quote==USD. For USD*** the base is USD so notional
        # ~= contract_size * lots (price cancels). We approximate with price
        # form, which is exact for the *USD majors and metals/crypto.
        if symbol.startswith("USD") and not symbol.endswith("USD"):
            return info.trade_contract_size * lots  # base is USD
        return info.trade_contract_size * price * lots

    def lots_for_notional(self, symbol: str, usd_notional: float) -> float:
        """Inverse of notional(): lots needed to hit a USD notional target,
        rounded to the symbol's volume step and clamped to min/max."""
        info = mt5.symbol_info(symbol)
        t = self.tick(symbol)
        if not info or not t or usd_notional <= 0:
            return 0.0
        price = (t.bid + t.ask) / 2.0
        if symbol.startswith("USD") and not symbol.endswith("USD"):
            per_lot = info.trade_contract_size
        else:
            per_lot = info.trade_contract_size * price
        if per_lot <= 0:
            return 0.0
        lots = usd_notional / per_lot
        step = info.volume_step or 0.01
        lots = round(lots / step) * step
        lots = max(info.volume_min, min(info.volume_max, lots))
        return round(lots, 2)

    # ---- order execution ----------------------------------------------
    def _filling_mode(self, symbol: str):
        if symbol in self._filling:
            return self._filling[symbol]
        info = mt5.symbol_info(symbol)
        mode = mt5.ORDER_FILLING_RETURN
        if info is not None:
            fm = info.filling_mode  # bitmask: 1=FOK, 2=IOC
            if fm & 2:
                mode = mt5.ORDER_FILLING_IOC
            elif fm & 1:
                mode = mt5.ORDER_FILLING_FOK
        self._filling[symbol] = mode
        return mode

    def sl_price(self, symbol: str, is_buy: bool, pct: float):
        """Stop-loss price `pct` away from current price, adverse direction,
        rounded to the symbol's digits."""
        info = mt5.symbol_info(symbol)
        t = self.tick(symbol)
        if not info or not t or pct <= 0:
            return 0.0
        ref = t.ask if is_buy else t.bid
        raw = ref * (1 - pct) if is_buy else ref * (1 + pct)
        return round(raw, info.digits)

    def market_order(self, symbol: str, lots: float, is_buy: bool,
                     comment: str = "bot", sl: float = 0.0):
        """Send a market order, optionally with a broker-side stop-loss.
        Returns (ok, result)."""
        t = self.tick(symbol)
        if not t:
            return False, "no tick"
        price = t.ask if is_buy else t.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lots),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(symbol),
        }
        if sl and sl > 0:
            req["sl"] = float(sl)
        res = mt5.order_send(req)
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        return ok, res

    def set_sl(self, symbol: str, sl_price: float):
        """Set/modify the stop-loss on an existing net position (netting)."""
        pos = self.position_for(symbol)
        if pos is None:
            return False, "no position"
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": pos.ticket,
            "sl": float(sl_price),
            "tp": 0.0,
        }
        res = mt5.order_send(req)
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        return ok, res

    def set_sltp(self, symbol: str, sl: float, tp: float):
        """Set BOTH stop-loss and take-profit on the net position. The caller
        passes the CURRENT value for whichever leg it isn't changing, so neither
        leg is clobbered (unlike set_sl, which zeroes tp)."""
        pos = self.position_for(symbol)
        if pos is None:
            return False, "no position"
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": pos.ticket,
            "sl": float(sl),
            "tp": float(tp),
        }
        res = mt5.order_send(req)
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        return ok, res

    def close_symbol(self, symbol: str, comment: str = "flatten"):
        """Fully close the net position on a symbol (netting account)."""
        pos = self.position_for(symbol)
        if pos is None or pos.volume == 0:
            return True, "flat"
        # opposite side of the existing position
        is_buy = pos.type == mt5.POSITION_TYPE_SELL
        t = self.tick(symbol)
        if not t:
            return False, "no tick"
        price = t.ask if is_buy else t.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(pos.volume),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "position": pos.ticket,
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(symbol),
        }
        res = mt5.order_send(req)
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        return ok, res

    def flatten_all(self, comment: str = "KILL"):
        """Close every open position. Returns list of (symbol, ok, result)."""
        results = []
        for p in self.positions():
            ok, res = self.close_symbol(p.symbol, comment=comment)
            results.append((p.symbol, ok, res))
            time.sleep(0.05)
        return results
