"""Is live L2 depth (DOM/market book) available from this broker via MT5?
If yes, the order-flow signal is deployable live; if not, it's backtest-only."""
import time
import MetaTrader5 as mt5
from bot.config import ALL_SYMBOLS

mt5.initialize()
for s in ["EURUSD", "BTCUSD", "XAUUSD"]:
    mt5.symbol_select(s, True)
    added = mt5.market_book_add(s)
    time.sleep(1.0)
    book = mt5.market_book_get(s)
    print(f"{s}: market_book_add={added} book={'None' if book is None else len(book)} entries")
    if book:
        for e in book[:6]:
            # type 1=sell(ask),2=buy(bid)
            print(f"    type={e.type} price={e.price} volume={e.volume}")
    mt5.market_book_release(s)
mt5.shutdown()
