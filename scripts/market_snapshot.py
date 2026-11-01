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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from common import MANIFEST_CSV, RAW_DIR, ROOT, load_validated_symbols  # noqa: F401
from indicators import compute_ema

EMA_FAST = 50
EMA_SLOW = 200


def _stock_flags(df):
    """Per-bar above-EMA flags as float arrays (1.0 above, 0.0 not above, NaN = EMA not
    warmed up yet, which must not count as 'below' — it is simply not in that day's
    denominator)."""
    closes = df["Close"].tolist()
    out = {}
    for name, period in (("above50", EMA_FAST), ("above200", EMA_SLOW)):
        ema = compute_ema(closes, period)
        out[name] = np.array([np.nan if e is None else float(c > e) for c, e in zip(closes, ema)])
    return out


def build_snapshot():
    symbols = load_validated_symbols()
    flags50, flags200 = {}, {}   # symbol -> Series on the symbol's own dates
    last_state = {}              # symbol -> (last_date, above50 | None, above200 | None)

    for symbol in symbols:
        path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        f = _stock_flags(df)
        flags50[symbol] = pd.Series(f["above50"], index=df.index)
        flags200[symbol] = pd.Series(f["above200"], index=df.index)
        a50, a200 = f["above50"][-1], f["above200"][-1]
        last_state[symbol] = (df.index[-1], None if np.isnan(a50) else bool(a50), None if np.isnan(a200) else bool(a200))

    if not last_state:
        raise RuntimeError("No validated symbol data found in checkpoints/raw/")

    # Breadth trend: one aligned (dates x symbols) frame per flag, then column-wise counts —
    # replaces a per-date x per-symbol DataFrame.loc double loop (~1.9M lookups, ~1 minute).
    # Same semantics: a stock is in a day's denominator only if it has a non-NaN flag that day.
    A50 = pd.concat(flags50, axis=1, sort=True)
    A200 = pd.concat(flags200, axis=1, sort=True).reindex(A50.index)
    tot50, cnt50 = A50.notna().sum(axis=1), (A50 == 1.0).sum(axis=1)
    tot200, cnt200 = A200.notna().sum(axis=1), (A200 == 1.0).sum(axis=1)
    breadth_trend = []
    for date, t50, c50, t200, c200 in zip(A50.index, tot50.tolist(), cnt50.tolist(), tot200.tolist(), cnt200.tolist()):
        breadth_trend.append({
            "date": str(date.date()),
            "above_ema50_pct": round(c50 / t50 * 100, 2) if t50 else None,
            "above_ema200_pct": round(c200 / t200 * 100, 2) if t200 else None,
        })

    # Pulse: as-of the latest bar each stock individually has (a delisted/thin symbol can
    # trail by a day or two, so "latest per stock" rather than one shared date).
    above50 = above200 = above_both = below_both = 0
    counted = 0
    for _, a50, a200 in last_state.values():
        if a50 is None or a200 is None:
            continue
        counted += 1
        above50 += a50
        above200 += a200
        above_both += a50 and a200
        below_both += (not a50) and (not a200)

    # The snapshot's date is the date MOST stocks are on, not the newest date any stock has:
    # the max can be set by a handful of symbols while ~all others lag (2026-10-30 incident),
    # which made a stale snapshot look current. Laggards are counted, not hidden.
    date_counts = Counter(d for d, _, _ in last_state.values())
    as_of = max(date_counts, key=lambda d: (date_counts[d], d))   # most stocks; on a tie the newer date
    newest = max(date_counts)
    behind = sum(c for d, c in date_counts.items() if d < as_of)

    def pct(n):
        return round(n / counted * 100, 2) if counted else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": str(as_of.date()),
        "newest_date_any_symbol": str(newest.date()),
        "symbols_behind_as_of": behind,
        "universe_size": len(last_state),
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
    print(f"As of {snapshot['as_of_date']} — {snapshot['universe_size']} stocks "
          f"({snapshot['symbols_behind_as_of']} behind; newest bar anywhere {snapshot['newest_date_any_symbol']})")
    print(f"Above EMA50: {p['above_ema50']} ({p['above_ema50_pct']}%)")
    print(f"Above EMA200: {p['above_ema200']} ({p['above_ema200_pct']}%)")
    print(f"Above BOTH: {p['above_both']} ({p['above_both_pct']}%)")
    print(f"Below BOTH: {p['below_both']} ({p['below_both_pct']}%)")
    print(f"Breadth trend points: {len(snapshot['breadth_trend'])}")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
