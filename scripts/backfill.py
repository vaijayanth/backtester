"""
Backfill — fetches 5yr split/bonus-adjusted daily OHLC for the clean equity
universe via yfinance, with per-stock chunk+checkpoint+resume reliability.
See docs/SPEC.md section 4 ("Backfill Reliability Design") for the full
rationale — this implements that design exactly.

Resumable by construction: safe to stop and re-run at any point. Reads/
writes a manifest tracking every symbol's status, so a re-run only
processes what's still PENDING or retryable — already-VALIDATED symbols
are always skipped, never re-downloaded.

Checkpoint data (raw per-symbol parquet + the manifest) is written to
checkpoints/ on local disk. THIS SCRIPT DOES NOT TOUCH GIT — committing
checkpoints to the dedicated `backfill-checkpoints` branch after each batch
is the calling GitHub Actions workflow's job (see docs/SPEC.md: Actions'
runner filesystem is ephemeral between separate job runs, so durability
has to come from git, not local disk).

A symbol that keeps failing after MAX_AUTO_RETRY_ATTEMPTS stops being
auto-retried on future runs (it's very likely wrong/delisted, not a
transient issue) — it still shows up in the manifest/report for review,
and can be force-retried with --symbol if you want to try again by hand.

Usage:
  python scripts/backfill.py                      # process all PENDING/retryable
  python scripts/backfill.py --limit 50            # cap this run to 50 symbols (testing)
  python scripts/backfill.py --symbol RELIANCE.NS  # single symbol, forces a retry
"""

import argparse
import csv
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent.parent
UNIVERSE_CSV = ROOT / "data" / "universe_clean.csv"
CHECKPOINT_DIR = ROOT / "checkpoints"
RAW_DIR = CHECKPOINT_DIR / "raw"
MANIFEST_CSV = CHECKPOINT_DIR / "manifest.csv"

BATCH_SIZE = 50
YEARS = 5
RETRY_DELAYS = [5, 15, 30]       # seconds, escalating backoff within one run
MAX_AUTO_RETRY_ATTEMPTS = 3      # across separate runs — beyond this, stop auto-retrying

# Was a flat 100 — rejected real, legitimate stocks whose Yahoo coverage is
# shorter than 5 years (confirmed by hand for HALDYNGL: Yahoo genuinely
# only carries ~43 rows for it, matching what the user independently saw
# on another platform — not broken data, just shorter history). Supertrend
# itself only needs ATR_PERIOD+2 (12) rows to produce a direction; this
# floor gives a little buffer above that bare minimum rather than demanding
# a full 5yr window just to accept a symbol at all. EMA50 still silently
# comes back as unavailable (not wrong) below 50 rows — see supertrend.py.
MIN_ROWS = 20

MANIFEST_FIELDS = [
    "symbol", "status", "records", "first_date", "last_date",
    "error", "attempts", "last_attempt_utc",
]

STATUS_PENDING = "PENDING"
STATUS_VALIDATED = "VALIDATED"
STATUS_PARTIAL = "PARTIAL"       # real, structurally-sound data, but below
                                  # MIN_ROWS — too little for a trustworthy
                                  # Supertrend/EMA read, but still enough for
                                  # a CMP. Saved to parquet exactly like
                                  # VALIDATED; supertrend.py surfaces it as a
                                  # close-only entry (signal fields null)
                                  # instead of dropping the symbol entirely.
                                  # User's instruction (2026-10-19): "don't
                                  # worry if there are not many OHLC days for
                                  # a particular stock, atleast give CMP."
STATUS_FAILED = "FAILED"
STATUS_PUBLISHED = "PUBLISHED"   # set later by the publish step (step 5), not here


def load_universe() -> list[str]:
    with UNIVERSE_CSV.open() as f:
        return [row["symbol"] for row in csv.DictReader(f)]


def load_manifest() -> dict:
    manifest = {}
    if MANIFEST_CSV.exists():
        with MANIFEST_CSV.open() as f:
            for row in csv.DictReader(f):
                manifest[row["symbol"]] = row
    return manifest


def save_manifest(manifest: dict):
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    with MANIFEST_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for symbol in sorted(manifest.keys()):
            writer.writerow(manifest[symbol])


def is_retryable(row: dict) -> bool:
    if row["status"] in (STATUS_VALIDATED, STATUS_PARTIAL):
        return False  # PARTIAL is as good as it'll get — Yahoo's history for
                       # this ticker IS that short, retrying won't grow it.
    if row["status"] == STATUS_FAILED and int(row.get("attempts", 0)) >= MAX_AUTO_RETRY_ATTEMPTS:
        return False  # exhausted — needs an explicit --symbol retry, not automatic
    return True


def _trim_null_edges(df: pd.DataFrame) -> pd.DataFrame:
    """Drops leading/trailing rows with ANY null OHLC value. Confirmed live
    (2026-10-19 run, 303 symbols) as a real, recurring Yahoo Finance glitch:
    a transient outage window returns an otherwise-good multi-year series
    with a few edge rows (most often the most recent, incomplete session)
    null — not genuine data corruption. Does NOT touch nulls anywhere in
    the interior of the trimmed series; those still fail hard below, since
    an interior gap is a real integrity problem a boundary artifact is not."""
    valid_mask = df[["Open", "High", "Low", "Close"]].notnull().all(axis=1)
    if not valid_mask.any():
        return df.iloc[0:0]
    first_valid = valid_mask.idxmax()
    last_valid = valid_mask[::-1].idxmax()
    return df.loc[first_valid:last_valid]


def validate_ohlc_structure(df: pd.DataFrame) -> tuple[bool, str, pd.DataFrame | None]:
    """Hard requirements only — data must be real and internally consistent,
    but this does NOT check row count. A stock can structurally pass this
    with just 1-2 rows and still be worth keeping for its CMP.

    Returns (is_valid, message, usable_df). usable_df is df with any
    leading/trailing null-OHLC rows trimmed off (see _trim_null_edges) —
    callers must save THIS, not the original df, so a trimmed edge never
    silently reintroduces nulls into the parquet. message carries a trim
    note on success (empty string if nothing was trimmed) or the failure
    reason."""
    if df is None or df.empty:
        # Ambiguous on purpose: yfinance can swallow a network failure
        # internally and return an empty dataframe without raising, so this
        # does NOT necessarily mean the symbol is delisted — confirmed via a
        # sandboxed test run where every failure was actually a blocked
        # network call, not a bad symbol. Don't over-interpret this message.
        return False, "empty dataframe (symbol may be delisted/wrong, OR the fetch failed silently — check for network errors in the log above, not just this message)", None
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(df.columns):
        return False, f"missing columns: {required - set(df.columns)}", None

    trimmed = _trim_null_edges(df)
    if trimmed.empty:
        return False, "all OHLC rows null", None
    if trimmed[["Open", "High", "Low", "Close"]].isnull().any().any():
        return False, "null OHLC values present in the interior of the series (not just leading/trailing edge rows)", None
    if (trimmed["High"] < trimmed["Low"]).any():
        return False, "High < Low on at least one row", None
    if (trimmed["Close"] <= 0).any() or (trimmed["Open"] <= 0).any():
        return False, "non-positive price present", None

    dropped = len(df) - len(trimmed)
    note = f"trimmed {dropped} leading/trailing null-OHLC row(s)" if dropped else ""
    return True, note, trimmed


def validate_ohlc(df: pd.DataFrame) -> tuple[bool, str, pd.DataFrame | None]:
    """Full validation INCLUDING the MIN_ROWS floor — this is the bar for a
    trustworthy Supertrend/EMA read (STATUS_VALIDATED). A structurally-sound
    but shorter-history symbol fails this and becomes STATUS_PARTIAL instead
    of STATUS_FAILED — see fetch_one(). Returns (is_valid, message, usable_df)
    like validate_ohlc_structure."""
    is_valid, note, trimmed = validate_ohlc_structure(df)
    if not is_valid:
        return False, note, None
    if len(trimmed) < MIN_ROWS:
        return False, f"only {len(trimmed)} rows — below the {MIN_ROWS}-row floor for a usable Supertrend/EMA read", None
    return True, note, trimmed


# GitHub Actions' schedule trigger has been observed, repeatedly, to fire
# a run 5-5.5 hours later than its cron time (checked via the Actions API
# across the last several runs — not a one-off blip). That turns the
# "08:00 IST, market hasn't opened yet" safety-net run into one that
# actually executes mid-session (~13:30 IST), and the "18:00 IST, after
# close" run into one landing even later in the evening (still safely
# after close, so harmless there). yfinance's daily-interval history()
# queried WHILE NSE is open returns today's row as a live/partial
# snapshot, not a settled close - and once merged into the parquet it is
# indistinguishable from a real EOD bar, silently corrupting every signal
# that assumes "today's row = today's close" (252-day-high breakout,
# Supertrend, etc). Trimming it here, at the single choke point every
# fetch path shares, makes the pipeline correct regardless of when
# GitHub Actions actually decides to run it - a run that lands mid-
# session simply finds nothing new to do for today, same as the
# workflow's own comments already assumed.
IST_OFFSET = timedelta(hours=5, minutes=30)  # NSE has no DST — fixed offset is safe
NSE_OPEN_HHMM = (9, 15)
NSE_CLOSE_HHMM = (15, 30)


def nse_session_open_now() -> bool:
    now_ist = datetime.now(timezone.utc) + IST_OFFSET
    if now_ist.weekday() >= 5:  # Sat/Sun — NSE doesn't trade
        return False
    open_t = now_ist.replace(hour=NSE_OPEN_HHMM[0], minute=NSE_OPEN_HHMM[1], second=0, microsecond=0)
    close_t = now_ist.replace(hour=NSE_CLOSE_HHMM[0], minute=NSE_CLOSE_HHMM[1], second=0, microsecond=0)
    return open_t <= now_ist <= close_t


def drop_intraday_today_row(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """If NSE is open right now, drop any row dated today (IST) from a
    freshly-fetched df - it can only be a partial/live snapshot, never a
    settled close. Returns (df, was_trimmed). A no-op (and cheap) outside
    market hours, which is the overwhelming majority of runs."""
    if df is None or df.empty or not nse_session_open_now():
        return df, False
    idx = pd.to_datetime(df.index).tz_localize(None)
    today_ist = (datetime.now(timezone.utc) + IST_OFFSET).date()
    keep = idx.date != today_ist
    if keep.all():
        return df, False
    return df.loc[keep].copy(), True


def fetch_one(symbol: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "symbol": symbol, "status": STATUS_FAILED, "records": 0,
        "first_date": "", "last_date": "", "error": "", "last_attempt_utc": now,
    }
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=f"{YEARS}y", auto_adjust=True)
    except Exception as e:
        row["error"] = f"fetch exception: {type(e).__name__}: {e}"
        return row
    df, _ = drop_intraday_today_row(df)

    is_valid, note, trimmed = validate_ohlc(df)
    save_note = note  # trim note (if any) for whichever df we end up saving
    if not is_valid:
        err = note
        # A handful of real, actively-traded NSE small/micro-caps come back
        # with only a few dozen rows under period="5y" — not delisted, not
        # a bad symbol, just a yfinance quirk for thinly-covered tickers
        # (confirmed by hand for GSPL/HALDYNGL/VELJAN: all three are real,
        # correctly-ticketed, currently trading). period="max" sometimes
        # returns the ticker's full available history where "5y" truncates
        # it — worth one extra attempt before giving up on the symbol.
        try:
            df_max = ticker.history(period="max", auto_adjust=True)
        except Exception as e:
            row["error"] = f"{err}; max-period retry also failed: {type(e).__name__}: {e}"
            return row
        df_max, _ = drop_intraday_today_row(df_max)
        is_valid_max, note_max, trimmed_max = validate_ohlc(df_max)
        err_max = note_max
        if is_valid_max:
            df = trimmed_max
            save_note = note_max
        else:
            # Full validation (row-count floor) failed on both attempts —
            # before giving up entirely, check if the LONGER of the two
            # (max-period, since it can only be >= the 5y pull) is at least
            # structurally sound. If so, keep it as PARTIAL (CMP-only)
            # rather than discarding real, usable price data just because
            # there's too little of it for a Supertrend/EMA read. Prefer
            # whichever pull actually passes the structural check, and
            # between two structurally-sound candidates prefer the longer.
            s_valid, s_err, s_trim = validate_ohlc_structure(df)
            s_valid_max, s_err_max, s_trim_max = validate_ohlc_structure(df_max)
            candidates = []
            if s_valid:
                candidates.append((s_trim, s_err))
            else:
                err = s_err
            if s_valid_max:
                candidates.append((s_trim_max, s_err_max))
            else:
                err_max = s_err_max
            if not candidates:
                row["error"] = f"{err} (period=5y); {err_max} (period=max)"
                return row
            df, save_note = max(candidates, key=lambda c: len(c[0]))
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            out_path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
            df.to_parquet(out_path)
            row.update({
                "status": STATUS_PARTIAL,
                "records": len(df),
                "first_date": str(df.index.min().date()),
                "last_date": str(df.index.max().date()),
                "error": f"PARTIAL — {err_max}" + (f" ({save_note})" if save_note else ""),
            })
            return row
    else:
        df = trimmed

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
    df.to_parquet(out_path)

    row.update({
        "status": STATUS_VALIDATED,
        "records": len(df),
        "first_date": str(df.index.min().date()),
        "last_date": str(df.index.max().date()),
        "error": save_note,
    })
    return row


def fetch_with_retry(symbol: str, prior_attempts: int) -> dict:
    row = None
    for attempt_offset, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            print(f"    retry in {delay}s...")
            time.sleep(delay)
        row = fetch_one(symbol)
        row["attempts"] = str(prior_attempts + attempt_offset + 1)
        if row["status"] in (STATUS_VALIDATED, STATUS_PARTIAL):
            return row  # PARTIAL won't improve with a retry — Yahoo's history is just that short
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="max symbols to process this run")
    parser.add_argument("--symbol", type=str, default=None, help="process one or more comma-separated symbols, forcing a retry even if exhausted (e.g. RELIANCE.NS or RELIANCE.NS,TCS.NS)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    universe = load_universe()
    manifest = load_manifest()

    for symbol in universe:
        if symbol not in manifest:
            manifest[symbol] = {
                "symbol": symbol, "status": STATUS_PENDING, "records": "0",
                "first_date": "", "last_date": "", "error": "", "attempts": "0",
                "last_attempt_utc": "",
            }

    if args.symbol:
        targets = [s.strip() for s in args.symbol.split(",") if s.strip()]
    else:
        targets = [s for s in universe if is_retryable(manifest[s])]
        if args.limit:
            targets = targets[: args.limit]

    already_validated = sum(1 for s in universe if manifest[s]["status"] == STATUS_VALIDATED)
    exhausted = sum(
        1 for s in universe
        if manifest[s]["status"] == STATUS_FAILED
        and int(manifest[s].get("attempts", 0)) >= MAX_AUTO_RETRY_ATTEMPTS
    )
    print(f"Universe: {len(universe)} symbols. Already VALIDATED: {already_validated}. "
          f"Exhausted (needs manual --symbol retry): {exhausted}. "
          f"To process this run: {len(targets)}.")

    for i, symbol in enumerate(targets):
        prior_attempts = int(manifest[symbol].get("attempts", 0)) if not args.symbol else 0
        print(f"[{i + 1}/{len(targets)}] {symbol}...")
        row = fetch_with_retry(symbol, prior_attempts)
        manifest[symbol] = row
        if row["status"] == STATUS_VALIDATED:
            note = "VALIDATED"
        elif row["status"] == STATUS_PARTIAL:
            note = f"PARTIAL (CMP only) — {row['records']} rows"
        else:
            note = f"FAILED — {row['error']}"
        print(f"    {note}")

        # Checkpoint after EVERY symbol — a crash mid-run loses at most the
        # in-flight symbol, never already-completed work.
        save_manifest(manifest)

        if (i + 1) % args.batch_size == 0:
            print(f"  --- batch of {args.batch_size} complete, manifest checkpointed ---")

    validated = sum(1 for s in universe if manifest[s]["status"] == STATUS_VALIDATED)
    partial = sum(1 for s in universe if manifest[s]["status"] == STATUS_PARTIAL)
    failed = sum(1 for s in universe if manifest[s]["status"] == STATUS_FAILED)
    pending = sum(1 for s in universe if manifest[s]["status"] == STATUS_PENDING)
    print(f"\nRun complete. Universe totals — VALIDATED: {validated}  PARTIAL (CMP only): {partial}  "
          f"FAILED: {failed}  PENDING: {pending}  (of {len(universe)})")


if __name__ == "__main__":
    main()
