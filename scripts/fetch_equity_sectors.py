"""
Fetch GICS sector classification (via yfinance's assetProfile, same data
source already trusted for OHLC in backfill.py) for every symbol that has
ever appeared in the Equity Trading Journal's Tradebook fills. Written to
data/equity_sectors.json as {symbol_without_suffix: sector}, which
index.html's Journal/Closed-trades tables read directly for their Sector
column.

Incremental by construction: an existing entry (including a cached "—" for
a symbol yfinance has no sector for, e.g. an ETF slipping past
EQ_LONGTERM_EXCLUDE) is never re-fetched — only symbols newly appearing in
the fills since the last run cost an API call. Safe to re-run any time.

Usage:
  python scripts/fetch_equity_sectors.py
"""

import json
import time
from pathlib import Path

import requests
import yfinance as yf

ROOT = Path(__file__).parent.parent
OUT_PATH = ROOT / "data" / "equity_sectors.json"
EQ_SHEET_URL = "https://script.google.com/macros/s/AKfycbyMNI8hj7lfWQLLEfcBYL3CgCFCdca7gJsOoaiR6KWC5vq_EiyyahYTyrRmrw0uAfk/exec"

# Mirrors EQ_LONGTERM_EXCLUDE in index.html — these are ETFs/liquid funds,
# not equities, so they have no real GICS sector and would just waste an
# API call every run if not skipped.
EQ_LONGTERM_EXCLUDE = {
    "BEL", "LIQUIDCASE", "LIQUIDBEES", "RECLTD", "BSE", "LTGILTBEES",
    "JUNIORBEES", "MASPTOP50", "MON100", "BANKBEES", "NIFTYBEES",
}


def fetch_distinct_symbols():
    resp = requests.get(EQ_SHEET_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(data.get("error", "sheet returned an error"))
    symbols = {r["symbol"].strip() for r in data.get("rows", []) if r.get("symbol")}
    return sorted(symbols - EQ_LONGTERM_EXCLUDE)


def fetch_sector(symbol):
    for suffix in (".NS", ".BO"):
        try:
            info = yf.Ticker(symbol + suffix).get_info()
            sector = info.get("sector")
            if sector:
                return sector
        except Exception:
            continue
    return None


def main():
    symbols = fetch_distinct_symbols()
    print(f"{len(symbols)} distinct equity symbols in the Tradebook")

    cache = {}
    if OUT_PATH.exists():
        cache = json.loads(OUT_PATH.read_text())

    todo = [s for s in symbols if s not in cache]
    print(f"{len(todo)} symbols not yet cached — fetching")

    for i, sym in enumerate(todo):
        sector = fetch_sector(sym)
        cache[sym] = sector or "—"
        print(f"  [{i+1}/{len(todo)}] {sym}: {cache[sym]}")
        time.sleep(0.3)  # light rate-limit courtesy to Yahoo's endpoint

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
    print(f"Wrote {len(cache)} entries to {OUT_PATH}")


if __name__ == "__main__":
    main()
