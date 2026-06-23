import os, glob, re
import pyarrow.parquet as pq

ROOT = r"C:\Users\ipode\Desktop\quanthack\pricer-output-2026-05-11_2026-06-10"
from bot.config import ALL_SYMBOLS

files = glob.glob(os.path.join(ROOT, "*.parquet"))
print(f"total parquet files: {len(files)}")

# unique symbols present
syms = sorted(set(re.match(r"([A-Z]+)_\d{4}_\d{2}_\d{2}", os.path.basename(f)).group(1)
                  for f in files))
print(f"symbols in dataset ({len(syms)}): {syms}")
print(f"\ncompetition symbols present: {[s for s in ALL_SYMBOLS if s in syms]}")
print(f"competition symbols MISSING: {[s for s in ALL_SYMBOLS if s not in syms]}")

# schema + sample of one competition file
sample = next(f for f in files if os.path.basename(f).startswith("EURUSD"))
print(f"\n--- schema of {os.path.basename(sample)} ---")
pf = pq.ParquetFile(sample)
print(pf.schema_arrow)
print(f"rows: {pf.metadata.num_rows:,}")
tbl = pf.read_row_group(0).slice(0, 5).to_pandas()
import pandas as pd
pd.set_option("display.width", 200); pd.set_option("display.max_columns", 30)
print(tbl)
