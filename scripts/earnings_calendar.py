"""
Earnings Calendar — fetches NSE's board-meeting intimations (companies
announcing when their board will meet to consider quarterly results) for
the next N days, filtered to the backtester's own universe.

NSE's unofficial API blocks direct scripted requests without a prior
same-session page visit (sets anti-bot cookies via the homepage first).
Standard pattern, same one used by community tools like `jugaad-data`:
  1. GET https://www.nseindia.com/ with a browser-like User-Agent to
     collect session cookies.
  2. GET the API endpoint in the SAME session, with an Accept: application/json
     header and a Referer pointing at the real board-meetings page.

Endpoint used: /api/corporate-board-meetings — board meeting intimations
are the standard forward-looking NSE announcement that a company will
"consider" (i.e. is about to declare) quarterly results, usually posted
1-2 weeks ahead of the actual results date. This is NOT the same as
/api/corporates-financial-results, which lists results already declared
(backward-looking, not useful for an "upcoming" card).

Could not be verified from the sandboxed dev environment used to write
this script — proxy blocked the test outright (403 on CONNECT tunnel),
same category of sandbox limitation already documented for yfinance in
docs/SPEC.md. Must be validated empirically via a real GitHub Actions run
before being trusted, exactly like the yfinance backfill was.

Usage:
  python scripts/earnings_calendar.py --out earnings_calendar.json --days 7
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"

NSE_HOME = "https://www.nseindia.com/"
NSE_BOARD_MEETINGS_API = "https://www.nseindia.com/api/corporate-board-meetings"
NSE_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-board-meetings"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_universe_symbols():
    """Returns bare NSE symbols (no .NS/.BO suffix) for VALIDATED stocks,
    since NSE's own API uses bare symbols, not Yahoo-style tickers."""
    if not MANIFEST_CSV.exists():
        return set()
    manifest = pd.read_csv(MANIFEST_CSV)
    validated = manifest[manifest["status"] == "VALIDATED"]["symbol"].tolist()
    return {s.replace(".NS", "").replace(".BO", "") for s in validated}


def fetch_board_meetings(days=7):
    session = requests.Session()
    session.headers.update(HEADERS)

    # Step 1: visit the homepage to collect anti-bot session cookies —
    # a direct API hit without this is reliably rejected by NSE.
    resp = session.get(NSE_HOME, timeout=15)
    resp.raise_for_status()

    today = datetime.now().date()
    to_date = today + timedelta(days=days)
    params = {
        "index": "equities",
        "from_date": today.strftime("%d-%m-%Y"),
        "to_date": to_date.strftime("%d-%m-%Y"),
    }
    api_headers = {"Accept": "application/json", "Referer": NSE_REFERER}
    resp = session.get(NSE_BOARD_MEETINGS_API, params=params, headers=api_headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def build_calendar(days=7):
    universe = load_universe_symbols()
    raw = fetch_board_meetings(days=days)

    # NSE's response is a list of dicts; the symbol field is commonly
    # "bm_symbol" or "symbol" depending on API version — try both.
    entries = []
    for row in raw:
        symbol = row.get("bm_symbol") or row.get("symbol")
        if not symbol or symbol not in universe:
            continue
        entries.append({
            "symbol": f"{symbol}.NS",
            "date": row.get("bm_date") or row.get("date"),
            "purpose": row.get("bm_purpose") or row.get("purpose") or "",
            "status": "scheduled",
        })

    entries.sort(key=lambda e: e["date"] or "")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "universe_matched": len(entries),
        "entries": entries,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default="earnings_calendar.json")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    calendar = build_calendar(days=args.days)
    Path(args.out).write_text(json.dumps(calendar, indent=2))

    print(f"Window: next {calendar['window_days']} days")
    print(f"Matched {calendar['universe_matched']} universe stocks with upcoming board meetings")
    for e in calendar["entries"][:10]:
        print(f"  {e['date']}  {e['symbol']:<15} {e['purpose'][:60]}")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
