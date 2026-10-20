"""
Fetch each universe symbol's company display name (yfinance's longName,
same data source already trusted for sector in fetch_equity_sectors.py)
so a broker transaction report that lists "Shriram Finance" or "CG Power
& Industrial Solutions" instead of the NSE ticker (SHRIRAMFIN, CGPOWER)
can be resolved back to a real symbol — needed for the 52-Week High
Forward Journal's Dhan transaction-report import, which never guesses a
name-to-ticker match without this reference.

Written to data/equity_names.json as {symbol_without_suffix: longName}.

Incremental by construction, same as fetch_equity_sectors.py: an existing
entry (including a cached "" for a symbol yfinance has no name for) is
never re-fetched — only symbols newly appearing in the manifest since the
last run cost an API call. Safe to re-run any time.

Usage:
  python scripts/fetch_equity_names.py
"""

import json
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent.parent
OUT_PATH = ROOT / "data" / "equity_names.json"
MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"


def load_validated_symbols():
    manifest = pd.read_csv(MANIFEST_CSV)
    return sorted(manifest[manifest["status"] == "VALIDATED"]["symbol"].str.replace(r"\.NS$", "", regex=True).tolist())


def fetch_name(symbol):
    for suffix in (".NS", ".BO"):
        try:
            info = yf.Ticker(symbol + suffix).get_info()
            name = info.get("longName") or info.get("shortName")
            if name:
                return name
        except Exception:
            continue
    return None


def main():
    symbols = load_validated_symbols()
    print(f"{len(symbols)} VALIDATED symbols in the universe")

    cache = {}
    if OUT_PATH.exists():
        cache = json.loads(OUT_PATH.read_text())

    todo = [s for s in symbols if s not in cache]
    print(f"{len(todo)} symbols not yet cached — fetching")

    for i, sym in enumerate(todo):
        name = fetch_name(sym)
        cache[sym] = name or ""
        print(f"  [{i+1}/{len(todo)}] {sym}: {cache[sym]}")
        time.sleep(0.3)  # light rate-limit courtesy to Yahoo's endpoint
        if (i + 1) % 100 == 0:
            OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            OUT_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
            print(f"  --- checkpointed at {i + 1}/{len(todo)} ---")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
    print(f"Wrote {len(cache)} entries to {OUT_PATH}")


if __name__ == "__main__":
    main()
