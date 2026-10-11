"""
Market Snapshot — computes the Dashboard's Market Pulse counts and the
Market Breadth Trend history from the same validated universe used by the
backtest engine. Self-contained: no external data source, just the
checkpoints/raw parquet already produced by backfill.py.

Output: a single small JSON (market_snapshot.json) with:
  - pulse: as-of-latest-date counts/percentages of stocks above EMA50,
    above EMA200, above both, below both
  - breadth_trend: one point per trading day across the full 5yr history,
    % of (stocks with enough history that day) above EMA50 / above EMA200

EMA formula matches backtest_engine.py exactly (manually seeded SMA +
recursive multiplier, not pandas .ewm()) so "above EMA" here means the
same thing it means everywhere else in this system.

Usage:
  python scripts/market_snapshot.py --out market_snapshot.json
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "checkpoints" / "raw"
MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"

EMA_FAST = 50
EMA_SLOW = 200


def compute_ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    ema = sum(values[:period]) / period
    out[period - 1] = ema
    mult = 2 / (period + 1)
    for i in range(period, len(values)):
        ema = (values[i] - ema) * mult + ema
        out[i] = ema
    return out


def load_validated_symbols():
    if not MANIFEST_CSV.exists():
        return []
    manifest = pd.read_csv(MANIFEST_CSV)
    return manifest[manifest["status"] == "VALIDATED"]["symbol"].tolist()


def build_snapshot():
    symbols = load_validated_symbols()
    per_stock = {}  # symbol -> DataFrame with Close, above50, above200

    for symbol in symbols:
        path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        closes = df["Close"].tolist()
        ema50 = compute_ema(closes, EMA_FAST)
        ema200 = compute_ema(closes, EMA_SLOW)
        # NaN EMA rows (not enough warm-up history yet) must not count as
        # "below" — they're simply not in the denominator for that day.
        # Built as plain Python lists of True/False/None (object dtype)
        # rather than a bool column + NA-assignment, which pandas refuses
        # (bool dtype can't hold NA without an explicit nullable dtype).
        above50 = [None if e is None else (c > e) for c, e in zip(closes, ema50)]
        above200 = [None if e is None else (c > e) for c, e in zip(closes, ema200)]
        out = pd.DataFrame({
            "close": closes,
            "above50": above50,
            "above200": above200,
        }, index=df.index)
        per_stock[symbol] = out

    if not per_stock:
        raise RuntimeError("No validated symbol data found in checkpoints/raw/")

    all_dates = sorted(set().union(*[set(df.index) for df in per_stock.values()]))

    breadth_trend = []
    for date in all_dates:
        above50_count = 0
        above50_total = 0
        above200_count = 0
        above200_total = 0
        for df in per_stock.values():
            if date not in df.index:
                continue
            row = df.loc[date]
            if pd.notna(row["above50"]):
                above50_total += 1
                if row["above50"]:
                    above50_count += 1
            if pd.notna(row["above200"]):
                above200_total += 1
                if row["above200"]:
                    above200_count += 1
        breadth_trend.append({
            "date": str(date.date()),
            "above_ema50_pct": round(above50_count / above50_total * 100, 2) if above50_total else None,
            "above_ema200_pct": round(above200_count / above200_total * 100, 2) if above200_total else None,
        })

    # Pulse: as-of the latest date each stock individually has (not a single
    # shared date — backfill runs can leave a few symbols a day or two
    # behind if delisted/thin, so "latest available per stock" is correct,
    # not "latest shared date across all 1,466").
    above50 = above200 = above_both = below_both = 0
    counted = 0
    latest_dates = []
    for df in per_stock.values():
        last = df.iloc[-1]
        latest_dates.append(df.index[-1])
        if pd.isna(last["above50"]) or pd.isna(last["above200"]):
            continue
        counted += 1
        a50, a200 = bool(last["above50"]), bool(last["above200"])
        if a50:
            above50 += 1
        if a200:
            above200 += 1
        if a50 and a200:
            above_both += 1
        if not a50 and not a200:
            below_both += 1

    as_of_date = str(max(latest_dates).date()) if latest_dates else None

    def pct(n):
        return round(n / counted * 100, 2) if counted else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": as_of_date,
        "universe_size": len(per_stock),
        "pulse": {
            "counted": counted,
            "above_ema50": above50,
            "above_ema50_pct": pct(above50),
            "above_ema200": above200,
            "above_ema200_pct": pct(above200),
            "above_both": above_both,
            "above_both_pct": pct(above_both),
            "below_both": below_both,
            "below_both_pct": pct(below_both),
        },
        "breadth_trend": breadth_trend,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default="market_snapshot.json")
    args = parser.parse_args()

    snapshot = build_snapshot()
    Path(args.out).write_text(json.dumps(snapshot, indent=2))

    p = snapshot["pulse"]
    print(f"As of {snapshot['as_of_date']} — {snapshot['universe_size']} stocks")
    print(f"Above EMA50: {p['above_ema50']} ({p['above_ema50_pct']}%)")
    print(f"Above EMA200: {p['above_ema200']} ({p['above_ema200_pct']}%)")
    print(f"Above BOTH: {p['above_both']} ({p['above_both_pct']}%)")
    print(f"Below BOTH: {p['below_both']} ({p['below_both_pct']}%)")
    print(f"Breadth trend points: {len(snapshot['breadth_trend'])}")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
