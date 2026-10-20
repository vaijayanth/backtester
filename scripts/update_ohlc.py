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

2. FULL REFRESH (one daily "slice" of the universe, so every symbol gets
   one roughly every FULL_REFRESH_CYCLE_DAYS trading days): re-run the
   same full 5yr fetch backfill.py uses, overwriting the stored history
   outright. This exists specifically to correct a real, silent problem
   with incremental appends: yfinance's auto_adjust back-adjusts the
   ENTIRE price history for splits/bonuses/dividends at fetch time. A new
   split announced today changes the correct adjusted price for data from
   years ago — an incremental append only ever touches rows at the tail,
   so it can never fix that retroactively. A periodic full refetch is
   the only way to self-heal that drift, so one runs on a rotation
   instead of trying to detect corporate actions directly.

   Was a 5-trading-day (weekly) cycle originally; widened to
   FULL_REFRESH_CYCLE_DAYS (2026-10-20) — corporate actions needing this
   correction are rare, so a monthly correction pass is still more than
   enough, and quartering the daily full-refresh slice directly cuts both
   the run's typical length and its exposure to a transient data-source
   outage (see the circuit breaker below — confirmed live, 2026-10-19: a
   ~4.5hr run burned almost entirely on retry backoff for 304 symbols
   that failed identically mid-run, strongly pointing to a provider-side
   outage, not 304 individually bad tickers).

Still-PENDING or still-retryable-FAILED symbols (from backfill.py's own
manifest states) are also attempted here with a full fetch — this run
doubles as an opportunistic top-up of backfill coverage, since it's
already iterating the whole universe daily.

THIS SCRIPT DOES NOT TOUCH GIT — same contract as backfill.py. The
calling workflow commits checkpoints/ to the backfill-checkpoints branch.

Usage:
  python scripts/update_ohlc.py
  python scripts/update_ohlc.py --status-out pipeline_status.json
"""

import argparse
import json
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
    fetch_one,
    fetch_with_retry,
    is_retryable,
    load_manifest,
    load_universe,
    save_manifest,
    validate_ohlc,
)

FULL_REFRESH_CYCLE_DAYS = 20  # ~one trading month — every symbol gets a correcting full refetch roughly once per cycle, spread one daily slice at a time

# A systemic, provider-side outage (confirmed live 2026-10-19: 303/304
# full-refresh symbols failed with the IDENTICAL "null OHLC" error in one
# run) burns the per-symbol retry backoff (up to RETRY_DELAYS' ~50s) on
# every single failure — turning a few hours of real work into most of a
# day. Once this many full-refresh symbols fail in a row, further retries
# in the SAME run are very unlikely to succeed (an outage doesn't clear in
# 50s), so later symbols in this run get exactly one attempt instead of
# the full retry ladder. They're simply corrected on the NEXT rotation
# pass instead — same self-healing guarantee, just not re-attempted
# pointlessly within a run that's already in a bad window.
OUTAGE_STREAK_THRESHOLD = 15


def full_refresh_today(symbol: str) -> bool:
    """Deterministic daily bucket per symbol (stable across runs, not
    random) — spreads the correcting full refetch evenly across the
    FULL_REFRESH_CYCLE_DAYS-day cycle instead of clustering on one day."""
    bucket = hash(symbol) % FULL_REFRESH_CYCLE_DAYS
    day_index = datetime.now(timezone.utc).toordinal()  # ever-increasing day counter, not tied to weekday
    return bucket == (day_index % FULL_REFRESH_CYCLE_DAYS)


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

    is_valid, note, trimmed = validate_ohlc(merged)
    if not is_valid:
        row["status"] = STATUS_FAILED
        row["error"] = f"post-merge validation failed: {note}"
        return row
    merged = trimmed  # drop any leading/trailing null-OHLC edge rows before saving — see backfill.py's _trim_null_edges

    merged.to_parquet(path)
    row.update({
        "records": len(merged),
        "first_date": str(merged.index.min().date()),
        "last_date": str(merged.index.max().date()),
        "error": note,
    })
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-out", type=str, default=None,
                         help="path to write a pipeline_status.json summary (e.g. for the Dashboard's status footer)")
    args = parser.parse_args()

    run_started = datetime.now(timezone.utc)
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

    incremental_updated = incremental_unchanged = incremental_failed = 0
    for i, symbol in enumerate(incremental_targets):
        print(f"[incr {i + 1}/{len(incremental_targets)}] {symbol}...")
        prior_last_date = manifest[symbol]["last_date"]
        row = incremental_update(symbol, prior_last_date)
        row["attempts"] = manifest[symbol].get("attempts", "0")
        if row["status"] != STATUS_VALIDATED:
            # One transient failure on an incremental update shouldn't
            # discard a symbol that's been fine for years — fall back to
            # keeping the existing manifest row untouched rather than
            # marking it FAILED, so it's simply retried again tomorrow.
            print(f"    incremental update failed ({row['error']}) — keeping prior data, will retry tomorrow")
            incremental_failed += 1
            continue
        manifest[symbol] = row
        if row["last_date"] != prior_last_date:
            incremental_updated += 1
        else:
            incremental_unchanged += 1  # no new trading day since last run (holiday/weekend/not-yet-posted)
        print(f"    last_date now {row['last_date']} ({row['records']} total rows)")
        save_manifest(manifest)
        if i % 50 == 0:
            time.sleep(1)  # light pacing, same spirit as backfill.py's batching

    full_refreshed = full_refresh_failed = 0
    consecutive_full_refresh_failures = 0
    outage_mode = False
    outage_trip_count = 0  # how many times the breaker engaged this run — surfaced in pipeline_status.json
    for i, symbol in enumerate(full_refresh_targets):
        prior_attempts = int(manifest[symbol].get("attempts", 0))
        if outage_mode:
            # See OUTAGE_STREAK_THRESHOLD — skip the retry ladder for the
            # rest of this run's slice; a provider outage won't clear in
            # the ~50s a full retry ladder would burn per symbol anyway.
            print(f"[refresh {i + 1}/{len(full_refresh_targets)}] {symbol} (single attempt — outage suspected, skipping retry backoff)...")
            row = fetch_one(symbol)
            row["attempts"] = str(prior_attempts + 1)
        else:
            print(f"[refresh {i + 1}/{len(full_refresh_targets)}] {symbol} (correcting re-fetch)...")
            row = fetch_with_retry(symbol, prior_attempts)
        if row["status"] != STATUS_VALIDATED:
            print(f"    full refresh failed ({row['error']}) — keeping prior data, will retry on next rotation")
            full_refresh_failed += 1
            consecutive_full_refresh_failures += 1
            if not outage_mode and consecutive_full_refresh_failures >= OUTAGE_STREAK_THRESHOLD:
                outage_mode = True
                outage_trip_count += 1
                print(f"    *** {consecutive_full_refresh_failures} consecutive full-refresh failures — "
                      f"suspecting a provider-side outage, not individually bad tickers. "
                      f"Switching remaining symbols in this run to single-attempt (no retry backoff). ***")
            continue
        consecutive_full_refresh_failures = 0  # a success proves the provider is reachable again
        outage_mode = False
        manifest[symbol] = row
        full_refreshed += 1
        print(f"    refreshed, last_date now {row['last_date']} ({row['records']} total rows)")
        save_manifest(manifest)

    catchup_validated = catchup_failed = 0
    for i, symbol in enumerate(catchup_targets):
        prior_attempts = int(manifest[symbol].get("attempts", 0))
        print(f"[catchup {i + 1}/{len(catchup_targets)}] {symbol}...")
        row = fetch_with_retry(symbol, prior_attempts)
        manifest[symbol] = row
        if row["status"] == STATUS_VALIDATED:
            catchup_validated += 1
            note = "VALIDATED"
        else:
            catchup_failed += 1
            note = f"FAILED — {row['error']}"
        print(f"    {note}")
        save_manifest(manifest)

    validated = sum(1 for s in universe if manifest[s]["status"] == STATUS_VALIDATED)
    failed = sum(1 for s in universe if manifest[s]["status"] == STATUS_FAILED)
    pending = sum(1 for s in universe if manifest[s]["status"] == STATUS_PENDING)
    # Most recent last_date across all VALIDATED symbols — the single
    # number that answers "is today's close actually in the data yet".
    latest_trading_date = max(
        (manifest[s]["last_date"] for s in universe if manifest[s]["status"] == STATUS_VALIDATED and manifest[s]["last_date"]),
        default=None,
    )
    print(f"\nRun complete. Universe totals — VALIDATED: {validated}  FAILED: {failed}  "
          f"PENDING: {pending}  (of {len(universe)})")

    if args.status_out:
        status = {
            "last_run_started_utc": run_started.isoformat(),
            "last_run_finished_utc": datetime.now(timezone.utc).isoformat(),
            "latest_trading_date": latest_trading_date,
            "universe_size": len(universe),
            "validated": validated,
            "failed": failed,
            "pending": pending,
            "incremental": {
                "attempted": len(incremental_targets),
                "updated": incremental_updated,
                "unchanged": incremental_unchanged,
                "failed": incremental_failed,
            },
            "full_refresh": {
                "attempted": len(full_refresh_targets),
                "refreshed": full_refreshed,
                "failed": full_refresh_failed,
                "outage_breaker_tripped": outage_trip_count,
                "ended_in_outage_mode": outage_mode,
            },
            "catchup": {
                "attempted": len(catchup_targets),
                "validated": catchup_validated,
                "failed": catchup_failed,
            },
        }
        Path(args.status_out).write_text(json.dumps(status, indent=2))
        print(f"Status written to {args.status_out}")


if __name__ == "__main__":
    main()
