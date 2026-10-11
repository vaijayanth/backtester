"""
Daily OHLC Update — keeps the 5-year backfilled checkpoint data current
through today's EOD close, without re-downloading each symbol's full
5-year history every day (backfill.py's fetch_one does a full refetch —
fine for a one-time backfill, far too slow/wasteful to run on 1,466+
symbols every single day).

Two modes, chosen per symbol automatically:

1. INCREMENTAL (the common case, every VALIDATED symbol most days):
   fetch only the days since that symbol's last stored date, append to
   the existing parquet. Fast — a handful of rows per symbol instead of
   ~1,250.

2. FULL REFRESH (one weekday "slice" of the universe per day, so every
   symbol gets one roughly every 5 trading days): re-run the same full
   5yr fetch backfill.py uses, overwriting the stored history outright.
   This exists specifically to correct a real, silent problem with
   incremental appends: yfinance's auto_adjust back-adjusts the ENTIRE
   price history for splits/bonuses/dividends at fetch time. A new split
   announced today changes the correct adjusted price for data from
   years ago — an incremental append only ever touches rows at the tail,
   so it can never fix that retroactively. A periodic full refetch is
   the only way to self-heal that drift, so one runs on a rotation
   instead of trying to detect corporate actions directly.

Still-PENDING or still-retryable-FAILED symbols (from backfill.py's own
manifest states) are also attempted here with a full fetch — this run
doubles as an opportunistic top-up of backfill coverage, since it's
already iterating the whole universe daily.

THIS SCRIPT DOES NOT TOUCH GIT — same contract as backfill.py. The
calling workflow commits checkpoints/ to the backfill-checkpoints branch.

Usage:
  python scripts/update_ohlc.py
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from backfill import (  # noqa: E402
    RAW_DIR,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_VALIDATED,
    fetch_with_retry,
    is_retryable,
    load_manifest,
    load_universe,
    save_manifest,
    validate_ohlc,
)

FULL_REFRESH_WEEKDAY_SLICES = 5  # Mon=0 .. Fri=4 — one slice of the universe per weekday


def full_refresh_today(symbol: str) -> bool:
    """Deterministic weekday bucket per symbol (stable across runs, not
    random) — spreads the correcting full refetch evenly across the week
    instead of clustering on one day."""
    bucket = hash(symbol) % FULL_REFRESH_WEEKDAY_SLICES
    today_weekday = datetime.now(timezone.utc).weekday()  # 0=Mon
    return bucket == (today_weekday % FULL_REFRESH_WEEKDAY_SLICES)


def incremental_update(symbol: str, last_date_str: str) -> dict:
    """Fetch only new rows since last_date_str (inclusive, to pick up any
    late revision of that day's own values) and merge into the existing
    parquet. Returns a manifest-row dict, same shape as backfill.py's."""
    now = datetime.now(timezone.utc).isoformat()
    path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
    row = {
        "symbol": symbol, "status": STATUS_VALIDATED, "records": 0,
        "first_date": "", "last_date": "", "error": "", "last_attempt_utc": now,
    }

    existing = pd.read_parquet(path)
    existing.index = pd.to_datetime(existing.index).tz_localize(None)

    last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
    fetch_start = last_date - timedelta(days=1)  # one day of overlap, belt-and-suspenders

    try:
        ticker = yf.Ticker(symbol)
        new_df = ticker.history(start=str(fetch_start), auto_adjust=True)
    except Exception as e:
        row["status"] = STATUS_FAILED
        row["error"] = f"incremental fetch exception: {type(e).__name__}: {e}"
        return row

    if new_df is None or new_df.empty:
        # No new trading days since last_date (weekend/holiday run, or
        # today's close not posted to the feed yet) — not an error, the
        # existing data is simply still current.
        row.update({
            "records": len(existing),
            "first_date": str(existing.index.min().date()),
            "last_date": str(existing.index.max().date()),
        })
        return row

    new_df.index = pd.to_datetime(new_df.index).tz_localize(None)
    merged = pd.concat([existing, new_df])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()

    is_valid, err = validate_ohlc(merged)
    if not is_valid:
        row["status"] = STATUS_FAILED
        row["error"] = f"post-merge validation failed: {err}"
        return row

    merged.to_parquet(path)
    row.update({
        "records": len(merged),
        "first_date": str(merged.index.min().date()),
        "last_date": str(merged.index.max().date()),
    })
    return row


def main():
    universe = load_universe()
    manifest = load_manifest()

    for symbol in universe:
        if symbol not in manifest:
            manifest[symbol] = {
                "symbol": symbol, "status": STATUS_PENDING, "records": "0",
                "first_date": "", "last_date": "", "error": "", "attempts": "0",
                "last_attempt_utc": "",
            }

    incremental_targets, full_refresh_targets, catchup_targets = [], [], []
    for symbol in universe:
        row = manifest[symbol]
        if row["status"] == STATUS_VALIDATED:
            if full_refresh_today(symbol):
                full_refresh_targets.append(symbol)
            else:
                incremental_targets.append(symbol)
        elif is_retryable(row):
            catchup_targets.append(symbol)

    print(f"Universe: {len(universe)} symbols. "
          f"Incremental: {len(incremental_targets)}  "
          f"Full refresh (today's rotation slice): {len(full_refresh_targets)}  "
          f"Catch-up (still PENDING/retryable): {len(catchup_targets)}.")

    for i, symbol in enumerate(incremental_targets):
        print(f"[incr {i + 1}/{len(incremental_targets)}] {symbol}...")
        row = incremental_update(symbol, manifest[symbol]["last_date"])
        row["attempts"] = manifest[symbol].get("attempts", "0")
        if row["status"] != STATUS_VALIDATED:
            # One transient failure on an incremental update shouldn't
            # discard a symbol that's been fine for years — fall back to
            # keeping the existing manifest row untouched rather than
            # marking it FAILED, so it's simply retried again tomorrow.
            print(f"    incremental update failed ({row['error']}) — keeping prior data, will retry tomorrow")
            continue
        manifest[symbol] = row
        print(f"    last_date now {row['last_date']} ({row['records']} total rows)")
        save_manifest(manifest)
        if i % 50 == 0:
            time.sleep(1)  # light pacing, same spirit as backfill.py's batching

    for i, symbol in enumerate(full_refresh_targets):
        prior_attempts = int(manifest[symbol].get("attempts", 0))
        print(f"[refresh {i + 1}/{len(full_refresh_targets)}] {symbol} (weekly correcting re-fetch)...")
        row = fetch_with_retry(symbol, prior_attempts)
        if row["status"] != STATUS_VALIDATED:
            print(f"    full refresh failed ({row['error']}) — keeping prior data, will retry on next rotation")
            continue
        manifest[symbol] = row
        print(f"    refreshed, last_date now {row['last_date']} ({row['records']} total rows)")
        save_manifest(manifest)

    for i, symbol in enumerate(catchup_targets):
        prior_attempts = int(manifest[symbol].get("attempts", 0))
        print(f"[catchup {i + 1}/{len(catchup_targets)}] {symbol}...")
        row = fetch_with_retry(symbol, prior_attempts)
        manifest[symbol] = row
        note = "VALIDATED" if row["status"] == STATUS_VALIDATED else f"FAILED — {row['error']}"
        print(f"    {note}")
        save_manifest(manifest)

    validated = sum(1 for s in universe if manifest[s]["status"] == STATUS_VALIDATED)
    failed = sum(1 for s in universe if manifest[s]["status"] == STATUS_FAILED)
    pending = sum(1 for s in universe if manifest[s]["status"] == STATUS_PENDING)
    print(f"\nRun complete. Universe totals — VALIDATED: {validated}  FAILED: {failed}  "
          f"PENDING: {pending}  (of {len(universe)})")


if __name__ == "__main__":
    main()
