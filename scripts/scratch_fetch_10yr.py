"""
SCRATCH / ONE-OFF — fetches 10-year OHLC for the current VALIDATED universe,
for a backtest sensitivity comparison (5yr vs 10yr) the user asked for.
Deliberately NOT part of the production backfill pipeline:
  - Writes to checkpoints_10yr/ (separate from checkpoints/, which stays
    on its own 5yr cadence via backfill.py/update_ohlc.py — untouched).
  - Committed by its workflow to a throwaway branch (scratch-10yr-checkpoints),
    never to backfill-checkpoints or main.
  - No resumability/retry-ladder complexity — single pass, log failures,
    rerun by hand if something's missing. This is a test fetch, not a
    permanent reliability-engineered pipeline.
Safe to delete this script + its workflow once the 10yr comparison is done,
if the user doesn't want them hanging around.

Usage:
  python scripts/scratch_fetch_10yr.py
  python scripts/scratch_fetch_10yr.py --limit 50
"""

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent.parent
SRC_MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"
OUT_DIR = ROOT / "checkpoints_10yr"
RAW_DIR = OUT_DIR / "raw"
OUT_MANIFEST_CSV = OUT_DIR / "manifest_10yr.csv"

YEARS = 10
MIN_ROWS = 20
RETRY_DELAYS = [5, 15]


def load_validated_symbols():
    manifest = pd.read_csv(SRC_MANIFEST_CSV)
    return manifest[manifest["status"] == "VALIDATED"]["symbol"].tolist()


def load_done_symbols():
    """Already-fetched symbols from a prior partial run — resumable in the
    cheap sense of not refetching what's already on disk, even though this
    script has none of the full pipeline's reliability engineering."""
    if not OUT_MANIFEST_CSV.exists():
        return set()
    df = pd.read_csv(OUT_MANIFEST_CSV)
    return set(df[df["status"] == "VALIDATED"]["symbol"].tolist())


def validate(df):
    if df.empty:
        return False, "empty"
    df = df.dropna(how="all", subset=["Open", "High", "Low", "Close"])
    if df.empty:
        return False, "all OHLC null"
    first_valid = df[["Open", "High", "Low", "Close"]].notna().all(axis=1).idxmax()
    df = df.loc[first_valid:]
    if df[["Open", "High", "Low", "Close"]].isnull().any().any():
        return False, "null OHLC in interior"
    if (df["High"] < df["Low"]).any():
        return False, "High < Low"
    if (df["Close"] <= 0).any() or (df["Open"] <= 0).any():
        return False, "non-positive price"
    if len(df) < MIN_ROWS:
        return False, f"only {len(df)} rows"
    return True, df


def fetch_one(symbol):
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=f"{YEARS}y", auto_adjust=True)
        except Exception as e:
            last_err = f"fetch exception: {type(e).__name__}: {e}"
            continue
        ok, result = validate(df)
        if ok:
            return True, result, ""
        last_err = result
    return False, None, last_err


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    symbols = load_validated_symbols()
    done = load_done_symbols()
    targets = [s for s in symbols if s not in done]
    if args.limit:
        targets = targets[: args.limit]

    print(f"Universe: {len(symbols)} VALIDATED (5yr) symbols. Already fetched (10yr): {len(done)}. To process: {len(targets)}.")

    rows = []
    if OUT_MANIFEST_CSV.exists():
        rows = pd.read_csv(OUT_MANIFEST_CSV).to_dict("records")

    for i, symbol in enumerate(targets):
        print(f"[{i + 1}/{len(targets)}] {symbol}...", end=" ")
        ok, df, err = fetch_one(symbol)
        now = datetime.now(timezone.utc).isoformat()
        if ok:
            out_path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
            df.to_parquet(out_path)
            rows.append({
                "symbol": symbol, "status": "VALIDATED", "records": len(df),
                "first_date": str(df.index.min().date()), "last_date": str(df.index.max().date()),
                "error": "", "last_attempt_utc": now,
            })
            print(f"OK — {len(df)} rows, {df.index.min().date()} to {df.index.max().date()}")
        else:
            rows.append({
                "symbol": symbol, "status": "FAILED", "records": 0,
                "first_date": "", "last_date": "", "error": err, "last_attempt_utc": now,
            })
            print(f"FAILED — {err}")

        if (i + 1) % 25 == 0:
            pd.DataFrame(rows).to_csv(OUT_MANIFEST_CSV, index=False)
            print(f"  --- checkpointed at {i + 1}/{len(targets)} ---")

    pd.DataFrame(rows).to_csv(OUT_MANIFEST_CSV, index=False)
    validated = sum(1 for r in rows if r["status"] == "VALIDATED")
    failed = sum(1 for r in rows if r["status"] == "FAILED")
    print(f"\nDone. VALIDATED: {validated}  FAILED: {failed}  (of {len(rows)} total attempted across all runs)")


if __name__ == "__main__":
    main()
