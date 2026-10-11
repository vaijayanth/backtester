"""
Universe cleanup — turns the raw StockList export into the clean equity
universe the backtest actually trades.

Input:  scripts/raw_stocklist.txt   (verbatim pull from the user's Google
        Sheet "StockList" tab, one Yahoo-format ticker per line, kept as-is
        for audit trail / reproducibility — this is NOT re-fetched live from
        the Sheet each run; see docs/SPEC.md section 4 for why)
Output: data/universe_clean.csv     (symbol, one row per clean equity)
        data/universe_report.txt    (what got removed and why, for review)

Two cleanup steps, per docs/SPEC.md section 4:
  1. Strip ETFs — by an EXPLICIT curated list of known ETF tickers, not by
     list position or pattern-matching alone. Several real companies
     (RKSWAMY, ALBERTDAVD, PLASTIBLEN, DENORA) sit directly adjacent to the
     ETF block in the source sheet but are NOT ETFs — position-based
     exclusion would have wrongly dropped them.
  2. De-duplicate — exact-symbol duplicates in the source sheet.

Exchange suffix (.NS / .BO) is trusted as given per symbol — never
normalized to one exchange (see docs/SPEC.md section 4, NSDL.BO correction).
"""

import csv
from pathlib import Path

RAW_FILE = Path(__file__).parent / "raw_stocklist.txt"
OUT_DIR = Path(__file__).parent.parent / "data"
OUT_CSV = OUT_DIR / "universe_clean.csv"
OUT_REPORT = OUT_DIR / "universe_report.txt"

# Explicit ETF exclusion list — curated by known fund family / ticker
# convention, not position in the source sheet. Grouped by family for
# auditability; if a future StockList export adds a new ETF, it needs to be
# added here explicitly (never silently excluded by ambiguous guesswork).
KNOWN_ETFS = {
    # "BEES" family (Nippon/ICICI legacy ETF naming)
    "NIFTYBEES", "BANKBEES", "GOLDBEES", "SILVERBEES", "JUNIORBEES",
    "PSUBNKBEES", "CONSUMBEES", "DIVOPPBEES", "AUTOBEES", "HNGSNGBEES",
    "ITBEES", "PHARMABEES",
    # "IETF" family (ICICI Prudential ETFs)
    "PVTBANIETF", "METALIETF", "LOWVOLIETF", "ALPL30IETF", "OILIETF",
    "COMMOIETF", "MOM30IETF", "FMCGIETF", "MIDSELIETF", "BSE500IETF",
    "QUAL30IETF", "VAL30IETF", "NIFTYIETF", "INFRAIETF", "TNIDETF",
    "NV20IETF",
    # Motilal Oswal "MO" family
    "MOM100", "MOQUALITY", "MOVALUE", "MOLOWVOL", "MOHEALTH", "MOREALTY",
    "MAFANG",
    # Other named index/thematic ETFs
    "MIDCAPETF", "SMALLCAP", "MULTICAP", "MIDSMALL", "HEALTHY", "BFSI",
    "MODEFENCE", "MOMENTUM50", "MONIFTY500", "NIFTYQLITY", "MAKEINDIA",
    "HDFCGROWTH", "HDFCSML250", "EVINDIA", "SENSEXETF", "ESG", "EMULTIMQ",
    "TOP100CASE", "MNC", "TOP10ADD", "MIDQ50ADD", "MONQ50", "ICICIB22",
    "CPSEETF", "SBINEQWETF", "ALPHA", "MON100", "MAHKTECH", "MASPTOP50",
    "PVTBANKADD",
}

# Symbols confirmed NOT ETFs despite sitting in/near the ETF block in the
# source sheet — real companies, kept explicitly to document the judgment
# call (not acted on here, just documentation; they're simply absent from
# KNOWN_ETFS above so they pass through untouched).
# DENORA (De Nora India), RKSWAMY (R K Swamy Ltd), ALBERTDAVD (Albert David
# Ltd), PLASTIBLEN (Plastiblends India Ltd)


def strip_suffix(ticker: str) -> str:
    """NSDL.BO -> NSDL, RELIANCE.NS -> RELIANCE. Suffix itself preserved in output."""
    return ticker.rsplit(".", 1)[0]


def main():
    raw_lines = [
        line.strip() for line in RAW_FILE.read_text().splitlines() if line.strip()
    ]

    seen = set()
    clean = []
    removed_etf = []
    removed_dupe = []

    for ticker in raw_lines:
        base = strip_suffix(ticker)
        if base in KNOWN_ETFS:
            removed_etf.append(ticker)
            continue
        if ticker in seen:
            removed_dupe.append(ticker)
            continue
        seen.add(ticker)
        clean.append(ticker)

    OUT_DIR.mkdir(exist_ok=True)

    with OUT_CSV.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol"])
        for ticker in clean:
            writer.writerow([ticker])

    report_lines = [
        f"Universe cleanup report",
        f"========================",
        f"Raw symbols in source:      {len(raw_lines)}",
        f"Removed as ETFs:            {len(removed_etf)}",
        f"Removed as duplicates:      {len(removed_dupe)}",
        f"Clean equities (output):    {len(clean)}",
        "",
        f"--- ETFs removed ({len(removed_etf)}) ---",
        *sorted(removed_etf),
        "",
        f"--- Duplicates removed ({len(removed_dupe)}) ---",
        *sorted(removed_dupe),
    ]
    OUT_REPORT.write_text("\n".join(report_lines) + "\n")

    print(f"Raw: {len(raw_lines)}  ETFs removed: {len(removed_etf)}  "
          f"Dupes removed: {len(removed_dupe)}  Clean: {len(clean)}")
    print(f"Wrote {OUT_CSV} and {OUT_REPORT}")


if __name__ == "__main__":
    main()
