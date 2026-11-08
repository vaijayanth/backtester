"""dh252 Entry Signal Screener — universe-wide "Dhan 52wk High Swing" entries.

Ports the exact entry filter already implemented client-side in index.html's
dh252RenderSignals() (close at/above the trailing 52-week high AND
Supertrend green, excluding the long-term-holdings list) into a standalone
daily script, so a live-broker consumer (outside this repo) can get the
universe-wide candidate list without needing broker data at all.

Deliberately does NOT know about anyone's actual positions or trade
history — "already held" and the real position-size tier (which depends on
closed-trade count and whether the 100-positions/positive-P&L doubling
milestone has fired) are both only knowable by whoever has the live
position/trade data, so this script leaves "already held" unfiltered and
reports qty at the flat ₹1,000 BASE tier only, clearly labeled as such. A
consumer joins this against its own holdings to drop already-held symbols
and recompute the real effective qty.

Inputs (already published daily by this repo's own pipeline, read as-is,
no new computation over OHLC):
  market_screens_full.json  -> week52_all[]  (every validated symbol's
                                 trailing-252-session high, not just the
                                 near-high subset in week52_high)
  supertrend_full.json      -> entries[]     (Supertrend color per symbol)

Usage:
  python scripts/dh252_entry_screener.py
  python scripts/dh252_entry_screener.py --market-screens market_screens_full.json --supertrend supertrend_full.json --out dh252_entry_signals.json
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Exact list from index.html's EQ_LONGTERM_EXCLUDE — kept in sync by hand,
# same as index.html itself; these are long-term holdings outside the swing
# system entirely, never real entry candidates.
EQ_LONGTERM_EXCLUDE = {
    "BEL", "LIQUIDCASE", "LIQUIDBEES", "RECLTD", "BSE", "LTGILTBEES",
    "JUNIORBEES", "MASPTOP50", "MON100", "BANKBEES", "NIFTYBEES",
}

DH252_BASE_POSITION_RS = 1000


def strip_suffix(symbol):
    for suf in (".NS", ".BO"):
        if symbol.endswith(suf):
            return symbol[: -len(suf)]
    return symbol


def load_week52_by_symbol(path):
    data = json.loads(Path(path).read_text())
    by_symbol = {}
    for row in data.get("week52_all", []):
        by_symbol[strip_suffix(row["symbol"])] = row
    return by_symbol, data.get("generated_at")


def load_supertrend_by_symbol(path):
    data = json.loads(Path(path).read_text())
    by_symbol = {}
    for row in data.get("entries", []):
        by_symbol[strip_suffix(row["symbol"])] = row
    return by_symbol, data.get("generated_at"), data.get("latest_market_date")


def build_entry_signals(market_screens_path, supertrend_path):
    week52, week52_generated_at = load_week52_by_symbol(market_screens_path)
    stmap, st_generated_at, latest_market_date = load_supertrend_by_symbol(supertrend_path)

    entries = []
    for symbol, w in week52.items():
        if w.get("pct_from_high") is None or w["pct_from_high"] < 0:
            continue
        if symbol in EQ_LONGTERM_EXCLUDE:
            continue
        st = stmap.get(symbol)
        if not st or not st.get("color") or st["color"].lower() != "green":
            continue
        qty_at_base_tier = max(1, int(DH252_BASE_POSITION_RS // w["close"]))
        entries.append({
            "symbol": symbol,
            "close": w["close"],
            "high_52w": w["high_52w"],
            "pct_from_high": w["pct_from_high"],
            "date": w["date"],
            "supertrend_color": st["color"],
            "flipped_today": st.get("flipped_today"),
            "recent_bullish_flip_days_ago": st.get("recent_bullish_flip_days_ago"),
            "qty_at_base_tier": qty_at_base_tier,
        })

    entries.sort(key=lambda r: r["pct_from_high"], reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_generated_at": {"market_screens": week52_generated_at, "supertrend": st_generated_at},
        "latest_market_date": latest_market_date,
        "rule": "close >= trailing 252-session high AND Supertrend(10,3) green, "
                "excludes EQ_LONGTERM_EXCLUDE. qty_at_base_tier assumes the flat "
                f"Rs{DH252_BASE_POSITION_RS} base tier only — NOT the real effective "
                "tier, which depends on live closed-trade count and the "
                "100-positions/positive-combined-P&L doubling milestone. Does NOT "
                "exclude symbols you already hold — join against your own positions "
                "for that.",
        "base_position_rs": DH252_BASE_POSITION_RS,
        "universe_size": len(week52),
        "entry_count": len(entries),
        "entries": entries,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-screens", default=str(ROOT / "market_screens_full.json"))
    parser.add_argument("--supertrend", default=str(ROOT / "supertrend_full.json"))
    parser.add_argument("--out", default=str(ROOT / "dh252_entry_signals.json"))
    args = parser.parse_args()

    result = build_entry_signals(args.market_screens, args.supertrend)
    Path(args.out).write_text(json.dumps(result, indent=2))

    print(f"Universe: {result['universe_size']} symbols with 52-week-high data")
    print(f"Entry signals today: {result['entry_count']}")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
