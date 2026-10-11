"""
Parse Sensibull Snapshot — converts a manually-exported Sensibull options
screener dump (data/sensibull_latest.tsv) into two outputs the Dashboard
and IVP Screener pages read:
  1. earnings_calendar.json — per-stock upcoming results dates
  2. options_snapshot.json — per-stock IVP/ATM-IV/PCR/Max Pain, the input
     to the IVP entry screener (SST Options strategy's "Morning Scan":
     IVP >= threshold + no imminent earnings conflict, per CLAUDE.md)

Why manual, not automated: NSE's own corporate-announcements API blocks
requests from GitHub Actions' IP range outright (confirmed via a real
failed run, 403 on the bare homepage) — see docs/SPEC.md. Sensibull
(Zerodha) has no such restriction for a logged-in user's own browser
session, but no public unauthenticated API either, so this stays a manual
export: the user downloads it periodically (weekly is the agreed cadence —
fine for the monthly entry-screening decision this data drives; explicitly
NOT fresh enough for live position monitoring of already-open strangles,
which needs a different, near-real-time data path) and pastes it in.

Input format: tab-separated, one row per instrument (stocks + indices).
Indices (NIFTY, BANKNIFTY, etc.) are excluded from both outputs — this
screener is specifically for the F&O stock universe.

Usage:
  python scripts/parse_sensibull.py --in data/sensibull_latest.tsv \
      --earnings-out earnings_calendar.json --options-out options_snapshot.json
"""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
FNO_UNIVERSE_CSV = ROOT / "data" / "fno_universe.csv"

# Non-stock instruments in a Sensibull dump (indices, currency futures) —
# excluded from both per-stock outputs.
NON_STOCK_INSTRUMENTS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "BANKEX",
    "SENSEX", "NIFTYNXT50", "USDINR", "FOCIT",
}


def load_fno_symbols():
    if not FNO_UNIVERSE_CSV.exists():
        return set()
    with open(FNO_UNIVERSE_CSV) as f:
        return {row["symbol"] for row in csv.DictReader(f)}


def to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def load_rows(in_path):
    with open(in_path) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def build_earnings_calendar(rows, fno_bare):
    entries = []
    unmatched = []
    for row in rows:
        instrument = row.get("Instrument", "").strip()
        event = row.get("Event", "").strip()
        if not instrument or instrument in NON_STOCK_INSTRUMENTS or event in ("", "-"):
            continue
        try:
            event_date = datetime.strptime(event, "%d-%m-%Y").date()
        except ValueError:
            continue
        if instrument not in fno_bare:
            unmatched.append(instrument)
        entries.append({
            "symbol": f"{instrument}.NS",
            "date": str(event_date),
            "status": "scheduled",
        })
    entries.sort(key=lambda e: e["date"])
    return entries, unmatched


def build_options_snapshot(rows, fno_bare):
    entries = []
    for row in rows:
        instrument = row.get("Instrument", "").strip()
        if not instrument or instrument in NON_STOCK_INSTRUMENTS or instrument not in fno_bare:
            continue
        event = row.get("Event", "").strip()
        event_date = None
        if event and event != "-":
            try:
                event_date = str(datetime.strptime(event, "%d-%m-%Y").date())
            except ValueError:
                pass
        entries.append({
            "symbol": f"{instrument}.NS",
            "future_price": to_float(row.get("FuturePrice")),
            "future_pct_change": to_float(row.get("FuturePercentChange")),
            "atm_iv": to_float(row.get("ATMIV")),
            "atm_iv_change": to_float(row.get("ATMIVChange")),
            "iv_percentile": to_float(row.get("IVPercentile")),
            "event_date": event_date,
            "volume_multiple": to_float(row.get("VolumeMultiple")),
            "future_oi_pct_change": to_float(row.get("FutureOIPercentChange")),
            "pcr": to_float(row.get("PCR")),
            "max_pain": to_float(row.get("MaxPain")),
        })
    entries.sort(key=lambda e: (e["iv_percentile"] if e["iv_percentile"] is not None else -1), reverse=True)
    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", type=str, default="data/sensibull_latest.tsv")
    parser.add_argument("--earnings-out", type=str, default="earnings_calendar.json")
    parser.add_argument("--options-out", type=str, default="options_snapshot.json")
    args = parser.parse_args()

    fno_bare = {s.replace(".NS", "").replace(".BO", "") for s in load_fno_symbols()}
    rows = load_rows(args.in_path)
    generated_at = datetime.now(timezone.utc).isoformat()

    cal_entries, unmatched = build_earnings_calendar(rows, fno_bare)
    calendar = {
        "generated_at": generated_at,
        "source": "sensibull_manual_upload",
        "source_rows": len(rows),
        "entries": cal_entries,
    }
    Path(args.earnings_out).write_text(json.dumps(calendar, indent=2))

    opt_entries = build_options_snapshot(rows, fno_bare)
    snapshot = {
        "generated_at": generated_at,
        "source": "sensibull_manual_upload",
        "universe_size": len(opt_entries),
        "entries": opt_entries,
    }
    Path(args.options_out).write_text(json.dumps(snapshot, indent=2))

    print(f"Parsed {len(rows)} instruments from {args.in_path}")
    print(f"Earnings entries: {len(cal_entries)}  ->  {args.earnings_out}")
    if unmatched:
        print(f"Unmatched against fno_universe.csv ({len(unmatched)}): {unmatched[:10]}")
    print(f"Options snapshot entries: {len(opt_entries)}  ->  {args.options_out}")


if __name__ == "__main__":
    main()
