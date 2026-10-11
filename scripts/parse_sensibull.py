"""
Parse Sensibull Snapshot — converts a manually-exported Sensibull options
screener dump (data/sensibull_latest.tsv) into earnings_calendar.json for
the Dashboard's Upcoming Results card.

Why manual, not automated: NSE's own corporate-announcements API blocks
requests from GitHub Actions' IP range outright (confirmed via a real
failed run, 403 on the bare homepage) — see docs/SPEC.md. Sensibull
(Zerodha) has no such restriction for a logged-in user's own browser
session, but no public unauthenticated API either, so this stays a manual
export: the user downloads it from Sensibull periodically (weekly is
plenty — earnings dates don't move often) and pastes it in; this script
turns that into the committed JSON the Dashboard reads.

Input format: tab-separated, one row per instrument, with columns
including Instrument, Event (DD-MM-YYYY or "-" for none), plus IV/PCR/
MaxPain fields not used by this script but preserved for a possible future
options screener (see docs/SPEC.md Options Terminal section) — not built
yet, this script only extracts the earnings calendar for now.

Usage:
  python scripts/parse_sensibull.py --in data/sensibull_latest.tsv --out earnings_calendar.json
"""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
FNO_UNIVERSE_CSV = ROOT / "data" / "fno_universe.csv"

# Non-stock instruments in a Sensibull dump (indices, currency futures) —
# excluded from the per-stock earnings calendar, which only makes sense
# for individual companies.
NON_STOCK_INSTRUMENTS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "BANKEX",
    "SENSEX", "NIFTYNXT50", "USDINR", "FOCIT",
}


def load_fno_symbols():
    if not FNO_UNIVERSE_CSV.exists():
        return set()
    import csv as _csv
    with open(FNO_UNIVERSE_CSV) as f:
        return {row["symbol"] for row in _csv.DictReader(f)}


def parse_sensibull(in_path):
    fno_bare = {s.replace(".NS", "").replace(".BO", "") for s in load_fno_symbols()}

    with open(in_path) as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

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
        symbol = f"{instrument}.NS"
        if instrument not in fno_bare:
            unmatched.append(instrument)
        entries.append({
            "symbol": symbol,
            "date": str(event_date),
            "status": "scheduled",
        })

    entries.sort(key=lambda e: e["date"])
    return entries, unmatched, len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", type=str, default="data/sensibull_latest.tsv")
    parser.add_argument("--out", type=str, default="earnings_calendar.json")
    args = parser.parse_args()

    entries, unmatched, total_rows = parse_sensibull(args.in_path)

    calendar = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "sensibull_manual_upload",
        "source_rows": total_rows,
        "entries": entries,
    }
    Path(args.out).write_text(json.dumps(calendar, indent=2))

    print(f"Parsed {total_rows} instruments from {args.in_path}")
    print(f"Earnings entries: {len(entries)}")
    if unmatched:
        print(f"Unmatched against fno_universe.csv ({len(unmatched)}): {unmatched[:10]}")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
