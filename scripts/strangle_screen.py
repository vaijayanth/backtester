"""
Strangle Breakeven Screen — monthly Win/Loose grid for the SST Options
strangle strategy's stated breakeven target (">10% on each side", per
CLAUDE.md). A month is a WIN if the stock's open->close return stayed
within +/-threshold% (a strangle sold with that breakeven would not have
been breached); a LOOSE if it moved beyond that band either direction.

Mirrors the existing Threshold Breach Analysis logic on the Stock Detail
page EXACTLY (same period grouping: calendar month, same OC-return formula:
(lastClose - firstOpen) / firstOpen * 100) so a user who already trusts
that single-stock chart gets the same math here, just across the whole
F&O universe and in a grid instead of a chart.

Scoped to the 213-stock NSE F&O universe (data/fno_universe.csv) — this is
specifically a screen for the options strangle strategy, which only trades
F&O-eligible names.

Usage:
  python scripts/strangle_screen.py --out strangle_screen.json --threshold 10
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "checkpoints" / "raw"
MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"
FNO_UNIVERSE_CSV = ROOT / "data" / "fno_universe.csv"


def load_fno_symbols():
    if not FNO_UNIVERSE_CSV.exists():
        return []
    return pd.read_csv(FNO_UNIVERSE_CSV)["symbol"].tolist()


def load_validated_set():
    if not MANIFEST_CSV.exists():
        return set()
    manifest = pd.read_csv(MANIFEST_CSV)
    return set(manifest[manifest["status"] == "VALIDATED"]["symbol"].tolist())


def monthly_returns(df):
    """Group by calendar month, return [(month_key, open_to_close_pct), ...]
    sorted by month — exact same grouping + formula as the JS
    groupIntoPeriods()/computePeriodReturns() on the Stock Detail page."""
    df = df.copy()
    df["month_key"] = df.index.to_series().dt.strftime("%Y-%m")
    out = []
    for month_key, g in df.groupby("month_key", sort=True):
        g = g.sort_index()
        open_px = g["Open"].iloc[0]
        close_px = g["Close"].iloc[-1]
        ret = (close_px - open_px) / open_px * 100
        out.append((month_key, round(ret, 2)))
    return out


def build_screen(threshold):
    fno_symbols = load_fno_symbols()
    validated = load_validated_set()

    monthly = []       # flat list: {symbol, month, return_pct, status}
    summary = []        # per-symbol: {symbol, win, loose, total, win_pct}
    skipped = []

    for symbol in fno_symbols:
        if symbol not in validated:
            skipped.append(symbol)
            continue
        path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
        if not path.exists():
            skipped.append(symbol)
            continue

        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        if len(df) < 20:
            skipped.append(symbol)
            continue

        rets = monthly_returns(df)
        win = loose = 0
        for month_key, ret in rets:
            status = "Win" if abs(ret) <= threshold else "Loose"
            if status == "Win":
                win += 1
            else:
                loose += 1
            monthly.append({
                "symbol": symbol, "month": month_key,
                "return_pct": ret, "status": status,
            })

        total = win + loose
        summary.append({
            "symbol": symbol, "win": win, "loose": loose, "total": total,
            "win_pct": round(win / total * 100, 1) if total else None,
        })

    summary.sort(key=lambda r: (r["win_pct"] if r["win_pct"] is not None else -1), reverse=True)

    # Aggregate per-month Win/Loose counts across ALL screened stocks —
    # the "WIN Count / Loose Count / Final Impression" columns in the
    # reference spreadsheet, computed over the full universe rather than
    # just whichever stocks a user later picks as grid columns (the
    # frontend recomputes this subset-specific version client-side for
    # whatever stocks are actually selected).
    by_month = defaultdict(lambda: {"win": 0, "loose": 0})
    for row in monthly:
        by_month[row["month"]][row["status"].lower()] += 1
    monthly_aggregate = [
        {"month": m, "win_count": v["win"], "loose_count": v["loose"],
         "final_impression": v["win"] - v["loose"]}
        for m, v in sorted(by_month.items())
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "threshold_pct": threshold,
        "universe": "nse_fno",
        "universe_size": len(fno_symbols),
        "skipped": skipped,
        "monthly": monthly,
        "summary": summary,
        "monthly_aggregate": monthly_aggregate,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default="strangle_screen.json")
    parser.add_argument("--threshold", type=float, default=10.0)
    args = parser.parse_args()

    screen = build_screen(args.threshold)
    Path(args.out).write_text(json.dumps(screen, indent=2))

    print(f"F&O universe: {screen['universe_size']} symbols ({len(screen['skipped'])} skipped)")
    print(f"Threshold: +/-{screen['threshold_pct']}%")
    print(f"Monthly data points: {len(screen['monthly'])}")
    print(f"Top 5 by win rate:")
    for r in screen['summary'][:5]:
        print(f"  {r['symbol']:<15} {r['win_pct']}% ({r['win']}W / {r['loose']}L / {r['total']} months)")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
