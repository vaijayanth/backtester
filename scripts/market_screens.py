"""
Market Screens — 52-Week High, 5-Year High, and RSI Overbought/Oversold
lists, for either the F&O universe (options screening) or the full
backtester universe (SST equity swing screening — "which stocks are near
a breakout right now").

--universe fno (default): 213-stock NSE F&O-eligible universe
  (data/fno_universe.csv) — feeds the IVP Screener / Strangle Screen pages.
--universe full: all 1,466 validated stocks — feeds the Dashboard's SST
  position-screening panels. SST trades the full equity universe, not
  just F&O-eligible names, so this is deliberately NOT the same list.

Honest framing, not "ATH": this repo only has 5 years of backfilled OHLC,
not a company's full trading history. A stock at its highest close in that
5-year window is NOT necessarily its genuine all-time high — many names
have traded for decades. Labeled "5-Year High" everywhere, including in
this script's own output keys, to avoid that misleading claim.

RSI14 formula matches Stock Detail's existing JS exactly (Wilder's
smoothing: avg = (avg*13 + new)/14 after a simple-average seed) so results
are comparable across the whole system, same principle as the EMA formula
parity already enforced in backtest_engine.py.

Usage:
  python scripts/market_screens.py --universe fno --out market_screens.json
  python scripts/market_screens.py --universe full --out market_screens_full.json
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "checkpoints" / "raw"
MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"
FNO_UNIVERSE_CSV = ROOT / "data" / "fno_universe.csv"

FIFTY_TWO_WEEK_DAYS = 252
NEAR_52WH_TOLERANCE_PCT = 1.0  # "near" 52-week high = within 1% of it


def compute_rsi14(closes):
    out = [None] * len(closes)
    if len(closes) < 15:
        return out
    gains = losses = 0.0
    for i in range(1, 15):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0)
        losses += max(-diff, 0)
    avg_gain, avg_loss = gains / 14, losses / 14
    out[14] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(15, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain, loss = max(diff, 0), max(-diff, 0)
        avg_gain = (avg_gain * 13 + gain) / 14
        avg_loss = (avg_loss * 13 + loss) / 14
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def load_fno_symbols():
    if not FNO_UNIVERSE_CSV.exists():
        return []
    df = pd.read_csv(FNO_UNIVERSE_CSV)
    return df["symbol"].tolist()


def load_validated_set():
    if not MANIFEST_CSV.exists():
        return set()
    manifest = pd.read_csv(MANIFEST_CSV)
    return set(manifest[manifest["status"] == "VALIDATED"]["symbol"].tolist())


def build_screens(universe="fno"):
    validated = load_validated_set()
    target_symbols = load_fno_symbols() if universe == "fno" else sorted(validated)

    week52_high = []
    week52_all = []
    five_year_high = []
    rsi_overbought = []
    rsi_oversold = []
    rsi_all = []
    skipped = []

    for symbol in target_symbols:
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
        closes = df["Close"].tolist()
        if len(closes) < 15:
            skipped.append(symbol)
            continue

        latest_close = closes[-1]
        latest_date = str(df.index[-1].date())

        # 52-week high
        window = closes[-FIFTY_TWO_WEEK_DAYS:] if len(closes) >= FIFTY_TWO_WEEK_DAYS else closes
        high_52w = max(window)
        pct_from_52wh = (latest_close - high_52w) / high_52w * 100
        week52_entry = {
            "symbol": symbol, "close": round(latest_close, 2),
            "high_52w": round(high_52w, 2), "pct_from_high": round(pct_from_52wh, 2),
            "date": latest_date,
        }
        week52_all.append(week52_entry)
        if pct_from_52wh >= -NEAR_52WH_TOLERANCE_PCT:
            week52_high.append(week52_entry)

        # 5-year high (full available window — NOT a genuine all-time high)
        high_5y = max(closes)
        if latest_close >= high_5y:
            five_year_high.append({
                "symbol": symbol, "close": round(latest_close, 2),
                "high_5y": round(high_5y, 2), "date": latest_date,
            })

        # RSI14
        rsi = compute_rsi14(closes)
        latest_rsi = rsi[-1]
        if latest_rsi is not None:
            entry = {"symbol": symbol, "close": round(latest_close, 2),
                      "rsi14": round(latest_rsi, 1), "date": latest_date}
            rsi_all.append(entry)
            if latest_rsi >= 70:
                rsi_overbought.append(entry)
            elif latest_rsi <= 30:
                rsi_oversold.append(entry)

    week52_high.sort(key=lambda r: r["pct_from_high"], reverse=True)
    rsi_overbought.sort(key=lambda r: r["rsi14"], reverse=True)
    rsi_oversold.sort(key=lambda r: r["rsi14"])

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": "nse_fno" if universe == "fno" else "full_backtester_universe",
        "universe_size": len(target_symbols),
        "skipped": skipped,
        "week52_high": week52_high,
        # Full per-stock 52WH, every validated symbol regardless of how far
        # from its high it currently sits — week52_high above only carries
        # the subset already within NEAR_52WH_TOLERANCE_PCT. The Equity
        # Dashboard/Alerts pages join THIS against the user's own open
        # positions (which can be anywhere relative to their 52WH, not just
        # near it), same join pattern as rsi_all below.
        "week52_all": week52_all,
        "five_year_high": five_year_high,
        "rsi_overbought": rsi_overbought,
        "rsi_oversold": rsi_oversold,
        # Full per-stock RSI14, every symbol with >=15 bars of history (not
        # just the >=70/<=30 extremes above) — the IVP Screener joins RSI
        # for ALL 213 F&O stocks against this, not just the ones already
        # flagged overbought/oversold.
        "rsi_all": rsi_all,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", choices=["fno", "full"], default="fno")
    parser.add_argument("--out", type=str, default="market_screens.json")
    args = parser.parse_args()

    screens = build_screens(universe=args.universe)
    Path(args.out).write_text(json.dumps(screens, indent=2))

    print(f"Universe ({args.universe}): {screens['universe_size']} symbols ({len(screens['skipped'])} skipped)")
    print(f"52-Week High (within {NEAR_52WH_TOLERANCE_PCT}%): {len(screens['week52_high'])}")
    print(f"5-Year High: {len(screens['five_year_high'])}")
    print(f"RSI Overbought (>=70): {len(screens['rsi_overbought'])}")
    print(f"RSI Oversold (<=30): {len(screens['rsi_oversold'])}")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
