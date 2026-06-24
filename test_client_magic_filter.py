"""positions() filters to the client's own magic by default; magic=None = all.
Tested via the pure helper with fake position objects (no MT5)."""
from bot.client import _filter_by_magic


class R:
    def __init__(self, magic): self.magic = magic

raw = [R(990623), R(990623), R(990624), R(0)]
own = 990623

# keep only own (990623)
assert len(_filter_by_magic(raw, 990623, own)) == 2
# scalper's view (990624)
assert len(_filter_by_magic(raw, 990624, own)) == 1
# magic=None -> all
assert len(_filter_by_magic(raw, None, own)) == 4
print("OK")
