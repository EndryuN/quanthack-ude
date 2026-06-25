"""flatten_all(all_account=True) closes EVERY magic's positions, so the kill can
flatten the scalper's book too (spec B1). Stubs the mt5 module."""
import bot.client as clientmod
from bot.client import MT5Client


class RawPos:
    def __init__(self, ticket, symbol, volume, type_, magic):
        self.ticket = ticket; self.symbol = symbol; self.volume = volume
        self.type = type_; self.magic = magic
        self.price_open = 1.0; self.price_current = 1.0; self.profit = 0.0
        self.sl = 0.0; self.tp = 0.0


class Tick:
    ask = 1.1; bid = 1.0


class Res:
    retcode = 10009  # TRADE_RETCODE_DONE


class Info:
    filling_mode = 2; point = 0.0001; digits = 5; trade_contract_size = 100000


class FakeMT5:
    TRADE_ACTION_DEAL = 1; ORDER_TYPE_BUY = 0; ORDER_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0; POSITION_TYPE_SELL = 1; ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2; ORDER_FILLING_IOC = 1; ORDER_FILLING_FOK = 0
    TRADE_RETCODE_DONE = 10009

    def __init__(self, positions): self._p = positions; self.sent = []
    def positions_get(self): return tuple(self._p)
    def symbol_info_tick(self, s): return Tick()
    def symbol_info(self, s): return Info()
    def order_send(self, req): self.sent.append(req); return Res()


# engine magic 990623 holds EURUSD (long); scalper magic 990624 holds BTCUSD (short)
fake = FakeMT5([RawPos(1, "EURUSD", 1.0, 0, 990623),
                RawPos(2, "BTCUSD", 0.5, 1, 990624)])
clientmod.mt5 = fake
c = MT5Client(magic=990623)

# own-magic flatten touches only the engine's EURUSD
fake.sent.clear(); c.flatten_all(comment="KILL")
assert [r["symbol"] for r in fake.sent] == ["EURUSD"], fake.sent

# account-wide flatten closes BOTH, including the scalper's BTCUSD (different magic)
fake.sent.clear(); c.flatten_all(comment="KILL", all_account=True)
assert sorted(r["symbol"] for r in fake.sent) == ["BTCUSD", "EURUSD"]
# closes are by ticket, opposite side: EURUSD long -> sell, BTCUSD short -> buy
byx = {r["symbol"]: r for r in fake.sent}
assert byx["EURUSD"]["type"] == fake.ORDER_TYPE_SELL and byx["EURUSD"]["position"] == 1
assert byx["BTCUSD"]["type"] == fake.ORDER_TYPE_BUY and byx["BTCUSD"]["position"] == 2

print("OK")
