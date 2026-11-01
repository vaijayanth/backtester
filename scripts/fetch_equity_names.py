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

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent.parent
OUT_PATH = ROOT / "data" / "equity_names.json"
MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"


def split_symbol(ticker):
    """'NSDL.BO' -> ('NSDL', '.BO'); 'RELIANCE.NS' -> ('RELIANCE', '.NS'); bare -> (bare, '.NS')."""
    for suffix in (".NS", ".BO"):
        if ticker.endswith(suffix):
            return ticker[: -len(suffix)], suffix
    return ticker, ".NS"


def load_validated_symbols():
    """{bare_symbol: listing_suffix} for every VALIDATED symbol. Both exchange
    suffixes are stripped for the cache key (the app looks names up by bare
    symbol); the suffix is kept so a BSE-only listing like NSDL.BO is queried
    on .BO first. (Was .NS-only: NSDL.BO kept its suffix, got queried as
    'NSDL.BO.NS'/'NSDL.BO.BO', and was cached as '' forever.)"""
    manifest = pd.read_csv(MANIFEST_CSV)
    tickers = manifest[manifest["status"] == "VALIDATED"]["symbol"].tolist()
    return dict(sorted(split_symbol(t) for t in tickers))


def fetch_name(symbol, prefer_suffix=".NS"):
    """Returns (name, definitive). definitive=False means a lookup RAISED
    (rate limit, network, Yahoo outage) and nothing was found, so the empty
    result is not evidence the symbol has no name and must not be cached."""
    suffixes = [prefer_suffix] + [x for x in (".NS", ".BO") if x != prefer_suffix]
    errored = False
    for suffix in suffixes:
        try:
            info = yf.Ticker(symbol + suffix).get_info() or {}
        except Exception:
            errored = True
            continue
        name = info.get("longName") or info.get("shortName")
        if name:
            return name, True
    return None, not errored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="max symbols to process this run (testing)")
    parser.add_argument("--retry-empty", action="store_true", help="also re-attempt symbols cached as '' (a past transient failure looks the same as 'no name')")
    args = parser.parse_args()

    suffix_by_symbol = load_validated_symbols()
    symbols = list(suffix_by_symbol)
    print(f"{len(symbols)} VALIDATED symbols in the universe")

    cache = {}
    if OUT_PATH.exists():
        cache = json.loads(OUT_PATH.read_text())

    todo = [s for s in symbols if s not in cache or (args.retry_empty and cache[s] == "")]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} symbols not yet cached — fetching")

    for i, sym in enumerate(todo):
        name, definitive = fetch_name(sym, suffix_by_symbol[sym])
        if name or definitive:
            cache[sym] = name or ""   # a real name, or a definitive "Yahoo has none"
            print(f"  [{i+1}/{len(todo)}] {sym}: {cache[sym]}")
        else:
            print(f"  [{i+1}/{len(todo)}] {sym}: lookup failed (transient) — not cached, will retry next run")
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
