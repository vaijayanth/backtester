"""
Supertrend(10, 3) — daily colour (bullish/bearish) and today's flips, for
either the F&O universe (options screening) or the full backtester
universe (SST equity swing screening). Same structure as market_screens.py
so the two run side by side in the daily pipeline.

Standard Supertrend algorithm (period=10 ATR, multiplier=3):
  mid = (High + Low) / 2
  basic_upper = mid + multiplier * ATR
  basic_lower = mid - multiplier * ATR
  final_upper[i] = basic_upper[i] if (basic_upper[i] < final_upper[i-1] or
                                       close[i-1] > final_upper[i-1])
                   else final_upper[i-1]
  final_lower[i] = basic_lower[i] if (basic_lower[i] > final_lower[i-1] or
                                       close[i-1] < final_lower[i-1])
                   else final_lower[i-1]
  direction flips to bearish (red) when close crosses below final_upper;
  flips to bullish (green) when close crosses above final_lower; otherwise
  holds the previous direction and follows the matching band.

ATR(10) uses the same Wilder smoothing as RSI14 in market_screens.py (simple
average to seed, then avg = (avg*9 + new)/10) — same principle as the EMA/
RSI formula parity already enforced elsewhere in this repo.

Usage:
  python scripts/supertrend.py --universe fno --out supertrend.json
  python scripts/supertrend.py --universe full --out supertrend_full.json
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

ATR_PERIOD = 10
MULTIPLIER = 3.0
EMA_FAST = 50  # matches market_snapshot.py's EMA_FAST exactly — same formula, same period
RECENT_FLIP_LOOKBACK_DAYS = 7  # catches a flip a day or two of inattention would otherwise miss


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


def compute_atr(highs, lows, closes, period=ATR_PERIOD):
    n = len(closes)
    tr = [None] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = [None] * n
    if n < period:
        return atr
    atr[period - 1] = sum(tr[0:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_supertrend(highs, lows, closes, period=ATR_PERIOD, multiplier=MULTIPLIER):
    """Returns (direction, final_upper, final_lower) lists, 'up'/'down'/None per bar."""
    n = len(closes)
    atr = compute_atr(highs, lows, closes, period)
    final_upper = [None] * n
    final_lower = [None] * n
    direction = [None] * n

    start = period - 1
    if start >= n:
        return direction, final_upper, final_lower

    mid0 = (highs[start] + lows[start]) / 2
    final_upper[start] = mid0 + multiplier * atr[start]
    final_lower[start] = mid0 - multiplier * atr[start]
    direction[start] = "down" if closes[start] <= final_upper[start] else "up"

    for i in range(start + 1, n):
        mid = (highs[i] + lows[i]) / 2
        basic_upper = mid + multiplier * atr[i]
        basic_lower = mid - multiplier * atr[i]

        final_upper[i] = (
            basic_upper
            if (basic_upper < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            basic_lower
            if (basic_lower > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )

        prev_dir = direction[i - 1]
        if prev_dir == "down":
            direction[i] = "up" if closes[i] > final_upper[i] else "down"
        else:
            direction[i] = "down" if closes[i] < final_lower[i] else "up"

    return direction, final_upper, final_lower


def load_validated_set():
    if not MANIFEST_CSV.exists():
        return set()
    manifest = pd.read_csv(MANIFEST_CSV)
    return set(manifest[manifest["status"] == "VALIDATED"]["symbol"].tolist())


def load_partial_set():
    """PARTIAL = real, structurally-sound OHLC but below backfill.py's
    MIN_ROWS floor — not enough for a trustworthy Supertrend/EMA read, but
    still enough for a CMP. Surfaced as a close-only entry below rather
    than skipped outright."""
    if not MANIFEST_CSV.exists():
        return set()
    manifest = pd.read_csv(MANIFEST_CSV)
    return set(manifest[manifest["status"] == "PARTIAL"]["symbol"].tolist())


def load_fno_symbols():
    if not FNO_UNIVERSE_CSV.exists():
        return []
    df = pd.read_csv(FNO_UNIVERSE_CSV)
    return df["symbol"].tolist()


def _recent_bullish_flip(direction, dates):
    """Was there a red/unknown -> green flip in the last
    RECENT_FLIP_LOOKBACK_DAYS trading days (not just yesterday->today)? A
    user checking every day never misses a flip, but a day or two of
    inattention would — this lets the Equity Alerts page catch it late
    rather than silently never. Scans backwards from today so a more
    recent flip always wins if there happened to be more than one."""
    n = len(direction)
    lookback = min(RECENT_FLIP_LOOKBACK_DAYS, n - 1)
    for days_ago in range(lookback):
        idx = n - 1 - days_ago
        prev_idx = idx - 1
        if prev_idx < 0:
            break
        cur, prev = direction[idx], direction[prev_idx]
        if cur == "up" and prev is not None and prev != "up":
            return days_ago, str(dates[idx].date())
    return None, None


def _recent_flip(direction, dates):
    """Same lookback as _recent_bullish_flip, but either direction — a
    red->green OR a green->red flip in the last RECENT_FLIP_LOOKBACK_DAYS
    trading days. Used for the generic "Flipped to GREEN/RED (Nd)" column
    shown wherever a Supertrend column appears, as distinct from the
    bullish-only re-entry signal _recent_bullish_flip feeds (that one
    stays untouched — this is additive, not a replacement). Returns
    (days_ago, date_str, direction_flipped_to) or (None, None, None)."""
    n = len(direction)
    lookback = min(RECENT_FLIP_LOOKBACK_DAYS, n - 1)
    for days_ago in range(lookback):
        idx = n - 1 - days_ago
        prev_idx = idx - 1
        if prev_idx < 0:
            break
        cur, prev = direction[idx], direction[prev_idx]
        if cur is not None and prev is not None and cur != prev:
            return days_ago, str(dates[idx].date()), cur
    return None, None, None


def _cmp_only_entry(symbol, latest_close, latest_date):
    """Not enough history for a trustworthy Supertrend/EMA read, but still
    enough for a CMP — surface it rather than dropping the symbol entirely.
    Null signal fields already render correctly downstream as "not in price
    universe" / unknown (not a fake 0% P&L) — see TradingJournal's
    index.html eqComputeSignal, which has handled a null color since before
    any backend ever actually emitted one."""
    return {
        "symbol": symbol, "close": round(latest_close, 2),
        "color": None, "flipped_today": False, "above_ema50": None, "date": latest_date,
        "recent_bullish_flip_days_ago": None, "recent_bullish_flip_date": None,
        "recent_flip_days_ago": None, "recent_flip_date": None, "recent_flip_direction": None,
    }


def build_supertrend(universe="fno"):
    validated = load_validated_set()
    partial = load_partial_set()
    available = validated | partial
    target_symbols = load_fno_symbols() if universe == "fno" else sorted(available)

    entries = []
    bullish_flips = []
    bearish_flips = []
    skipped = []

    for symbol in target_symbols:
        if symbol not in available:
            skipped.append(symbol)
            continue
        path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
        if not path.exists():
            skipped.append(symbol)
            continue

        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        highs, lows, closes = df["High"].tolist(), df["Low"].tolist(), df["Close"].tolist()
        if not closes:
            skipped.append(symbol)
            continue

        latest_close = closes[-1]
        latest_date = str(df.index[-1].date())

        if len(closes) < ATR_PERIOD + 2:
            entries.append(_cmp_only_entry(symbol, latest_close, latest_date))
            continue

        direction, _, _ = compute_supertrend(highs, lows, closes)
        today_dir, yesterday_dir = direction[-1], direction[-2]
        if today_dir is None:
            entries.append(_cmp_only_entry(symbol, latest_close, latest_date))
            continue

        flipped = yesterday_dir is not None and today_dir != yesterday_dir
        recent_flip_days_ago, recent_flip_date = _recent_bullish_flip(direction, df.index)
        any_flip_days_ago, any_flip_date, any_flip_dir = _recent_flip(direction, df.index)

        ema50 = compute_ema(closes, EMA_FAST)
        # None (not False) when there isn't enough history for a real 50
        # EMA read — a stock with, say, 43 rows previously got silently
        # reported as "below the 50 EMA" when the true answer is "unknown,
        # insufficient data" — those are not the same thing to a consumer
        # making an add-on/exit decision off this field.
        above_ema50 = (latest_close > ema50[-1]) if ema50[-1] is not None else None

        entry = {
            "symbol": symbol, "close": round(latest_close, 2),
            "color": "green" if today_dir == "up" else "red",
            "flipped_today": flipped, "above_ema50": above_ema50, "date": latest_date,
            "recent_bullish_flip_days_ago": recent_flip_days_ago,
            "recent_bullish_flip_date": recent_flip_date,
            "recent_flip_days_ago": any_flip_days_ago,
            "recent_flip_date": any_flip_date,
            "recent_flip_direction": any_flip_dir,
        }
        entries.append(entry)
        if flipped:
            (bullish_flips if today_dir == "up" else bearish_flips).append(entry)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": "nse_fno" if universe == "fno" else "full_backtester_universe",
        "universe_size": len(target_symbols),
        "params": {"atr_period": ATR_PERIOD, "multiplier": MULTIPLIER, "ema_fast": EMA_FAST},
        "skipped": skipped,
        "entries": entries,
        # Every flip entry also carries above_ema50 — your entry rule is
        # "flip + above 50 EMA", shown as a column, not a hard filter, so a
        # fresh flip still below the 50 EMA stays visible rather than
        # disappearing.
        "bullish_flips": bullish_flips,
        "bearish_flips": bearish_flips,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", choices=["fno", "full"], default="fno")
    parser.add_argument("--out", type=str, default="supertrend.json")
    args = parser.parse_args()

    result = build_supertrend(universe=args.universe)
    Path(args.out).write_text(json.dumps(result, indent=2))

    print(f"Universe ({args.universe}): {result['universe_size']} symbols ({len(result['skipped'])} skipped)")
    print(f"Entries: {len(result['entries'])}")
    print(f"Bullish flips today: {len(result['bullish_flips'])}")
    print(f"Bearish flips today: {len(result['bearish_flips'])}")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
