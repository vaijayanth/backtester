"""
Merge Universe Additions — pulls symbols queued via the Universe page's
"ADD STOCK" UI (stored in the "Backtester Universe Additions" Google Sheet,
ADDITIONS tab, via UniverseAdditions_AppsScript.gs) into
data/universe_manual_additions.csv, then regenerates data/universe_clean.csv
via clean_universe.py so the new symbol shows up in the Universe page and
flows into the normal backfill/Supertrend/screens pipeline with no other
manual step.

Why a Sheet + this merge step instead of writing straight from the browser
to the repo: GitHub Pages is static — there's no server-side code to accept
a commit from client JS, and embedding a GitHub token in public client-side
JS to commit directly was explicitly rejected elsewhere in this project
(docs/SPEC.md section 4b) as a bigger liability than a leaked app password.
Same durable-Sheet-backend pattern as EQ_SHEET_URL/IVP_SHEET_URL.

A newly-added symbol gets picked up as STATUS_PENDING by backfill.py's/
update_ohlc.py's own manifest logic automatically the next time either
runs (load_universe() just reads universe_clean.csv) — no extra backfill
trigger needed here.

Usage:
  python scripts/merge_universe_additions.py
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import clean_universe  # noqa: E402

ROOT = Path(__file__).parent.parent
MANUAL_ADDITIONS_FILE = ROOT / "data" / "universe_manual_additions.csv"
UNIVERSE_ADDITIONS_URL = "https://script.google.com/macros/s/AKfycbx0DhYyh9aQ74Bi-p6rVUEs_l6ExQD-G7yIss9c2MUkAWpaGF5vqicQb9W-azuEI1pM/exec"


def fetch_pending():
    resp = requests.get(UNIVERSE_ADDITIONS_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(data.get("error", "sheet returned an error"))
    return [r["symbol"].strip().upper() for r in data.get("rows", [])
            if r.get("status") == "PENDING" and r.get("symbol", "").strip()]


def normalize_symbol(symbol):
    """Bare symbols (no exchange suffix) default to NSE, matching the
    Yahoo-format convention used throughout this repo (RELIANCE.NS)."""
    symbol = symbol.strip().upper()
    if "." not in symbol:
        symbol = f"{symbol}.NS"
    return symbol


def mark_merged(symbols):
    if not symbols:
        return
    resp = requests.post(UNIVERSE_ADDITIONS_URL,
                          json={"type": "mark_merged", "symbols": symbols},
                          timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(data.get("error", "mark_merged failed"))
    return data.get("marked", 0)


def main():
    pending = fetch_pending()
    if not pending:
        print("No pending universe additions.")
        return

    existing_manual = set(clean_universe.load_manual_additions())
    existing_clean = set()
    if clean_universe.OUT_CSV.exists():
        import csv
        with clean_universe.OUT_CSV.open() as f:
            existing_clean = {row["symbol"].strip() for row in csv.DictReader(f) if row.get("symbol")}

    normalized = [normalize_symbol(s) for s in pending]
    truly_new = [s for s in normalized if s not in existing_manual and s not in existing_clean]
    already_present = [s for s in normalized if s not in truly_new]

    print(f"Pending: {len(pending)}  New: {len(truly_new)}  Already in universe: {len(already_present)}")

    if truly_new:
        with MANUAL_ADDITIONS_FILE.open("a", newline="") as f:
            for symbol in truly_new:
                f.write(symbol + "\n")
        print(f"Appended {len(truly_new)} symbol(s) to {MANUAL_ADDITIONS_FILE}: {truly_new}")

        clean_universe.main()
    else:
        print("Nothing new to add to the universe file — all pending symbols already present.")

    # Mark EVERY pending symbol processed this run as MERGED, including ones
    # that turned out to already be in the universe — otherwise a duplicate
    # add request would stay PENDING forever and keep showing up unresolved.
    marked = mark_merged(normalized)
    print(f"Marked {marked} sheet row(s) as MERGED.")


if __name__ == "__main__":
    main()
