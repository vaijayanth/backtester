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

Every pending symbol resolves to exactly one of three outcomes, written
back to the sheet so the UI can show it instead of a request just silently
vanishing from "pending":
  - MERGED         genuinely new, has real price data on Yahoo, added.
  - ALREADY_EXISTS requested symbol was already in the universe — no-op,
                    not appended a second time.
  - REJECTED       yfinance has no usable price history for it (typo, bad
                    ticker, delisted) — never gets added to the universe at
                    all, so a bad symbol can't sit around as a permanently
                    FAILED backfill entry with no explanation. Note column
                    carries why.

A newly-MERGED symbol gets picked up as STATUS_PENDING by backfill.py's/
update_ohlc.py's own manifest logic automatically the next time either
runs (load_universe() just reads universe_clean.csv) — no extra backfill
trigger needed here.

Usage:
  python scripts/merge_universe_additions.py
"""

import csv
import sys
import time
from pathlib import Path

import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
import clean_universe  # noqa: E402

ROOT = Path(__file__).parent.parent
MANUAL_ADDITIONS_FILE = ROOT / "data" / "universe_manual_additions.csv"
UNIVERSE_ADDITIONS_URL = "https://script.google.com/macros/s/AKfycbx0DhYyh9aQ74Bi-p6rVUEs_l6ExQD-G7yIss9c2MUkAWpaGF5vqicQb9W-azuEI1pM/exec"


def fetch_pending():
    """Returns the RAW symbol text as the user typed it (e.g. "RELIANCE",
    not normalized to "RELIANCE.NS") — this is what's stored in the sheet's
    Symbol column, and what update_status() must match against to resolve
    the right row. Normalization to a tradeable ticker happens separately,
    per-row, in main()."""
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


def symbol_has_real_data(symbol):
    """Quick existence check — a bad/typo'd/delisted symbol returns an
    empty dataframe from yfinance rather than raising, same signature
    backfill.py's validate_ohlc_structure already relies on. Deliberately
    lightweight (5 days, not 5 years) since this only needs to answer
    "is this a real, currently-tradeable ticker", not fetch real history —
    the actual backfill does that properly once the symbol is merged."""
    try:
        df = yf.Ticker(symbol).history(period="5d")
        return df is not None and not df.empty
    except Exception:
        return False


def update_status(updates):
    """updates: [{symbol (RAW, as stored in the sheet), status, note}, ...]"""
    if not updates:
        return 0
    resp = requests.post(UNIVERSE_ADDITIONS_URL,
                          json={"type": "update_status", "updates": updates},
                          timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(data.get("error", "update_status failed"))
    return data.get("updated", 0)


def main():
    pending_raw = fetch_pending()
    if not pending_raw:
        print("No pending universe additions.")
        return

    existing_manual = set(clean_universe.load_manual_additions())
    existing_clean = set()
    if clean_universe.OUT_CSV.exists():
        with clean_universe.OUT_CSV.open() as f:
            existing_clean = {row["symbol"].strip() for row in csv.DictReader(f) if row.get("symbol")}

    # raw -> normalized, in request order; a duplicate raw request in the
    # same batch is deduped (processed once, same outcome applied to all
    # matching rows via update_status's bulk match).
    pairs = [(raw, normalize_symbol(raw)) for raw in pending_raw]

    to_merge, already_present, rejected = [], [], []
    for raw, norm in pairs:
        if norm in existing_manual or norm in existing_clean:
            already_present.append((raw, norm))
        elif symbol_has_real_data(norm):
            to_merge.append((raw, norm))
        else:
            rejected.append((raw, norm))
        time.sleep(0.3)  # light pacing against the yfinance calls above

    print(f"Pending: {len(pending_raw)}  New & valid: {len(to_merge)}  "
          f"Already in universe: {len(already_present)}  Rejected (no real data): {len(rejected)}")

    if to_merge:
        with MANUAL_ADDITIONS_FILE.open("a", newline="") as f:
            for _, norm in to_merge:
                f.write(norm + "\n")
        print(f"Appended {len(to_merge)} symbol(s) to {MANUAL_ADDITIONS_FILE}: {[n for _, n in to_merge]}")
        clean_universe.main()
    else:
        print("Nothing new and valid to add to the universe file.")

    updates = (
        [{"symbol": raw, "status": "MERGED", "note": f"added as {norm}"} for raw, norm in to_merge]
        + [{"symbol": raw, "status": "ALREADY_EXISTS", "note": f"{norm} already in universe"} for raw, norm in already_present]
        + [{"symbol": raw, "status": "REJECTED", "note": f"no price data found for {norm} on Yahoo — check spelling/exchange"} for raw, norm in rejected]
    )
    updated = update_status(updates)
    print(f"Updated {updated} sheet row(s).")


if __name__ == "__main__":
    main()
