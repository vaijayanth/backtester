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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from common import (  # noqa: E402  shared paths + universe loaders
    FNO_UNIVERSE_CSV, MANIFEST_CSV, RAW_DIR, ROOT, load_fno_symbols, load_partial_set, load_validated_set,
)
from indicators import ATR_PERIOD, MULTIPLIER, compute_atr, compute_ema, compute_supertrend  # noqa: E402,F401
EMA_FAST = 50  # matches market_snapshot.py's EMA_FAST exactly — same formula, same period
RECENT_FLIP_LOOKBACK_DAYS = 7  # catches a flip a day or two of inattention would otherwise miss


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


def _current_streak(direction, dates):
    """How long has the CURRENT colour held, uncapped — unlike
    _recent_flip/_recent_bullish_flip (both hard-capped at
    RECENT_FLIP_LOOKBACK_DAYS so they only ever answer "did it flip
    recently"). A stock that's been green for 45 days gets a null from
    both of those; this answers "since when" regardless of how long ago.
    Scans backward from today to the earliest day still matching today's
    direction. Returns (streak_days, start_date_str) — streak_days is 0
    when today itself is the start (i.e. flipped today, same day
    _recent_flip would report days_ago=0). None/None if today's
    direction is unknown or there's no history to walk."""
    n = len(direction)
    if n == 0 or direction[-1] is None:
        return None, None
    today_dir = direction[-1]
    start_idx = n - 1
    for idx in range(n - 2, -1, -1):
        if direction[idx] != today_dir:
            break
        start_idx = idx
    streak_days = (n - 1) - start_idx
    return streak_days, str(dates[start_idx].date())


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
        "current_streak_days": None, "current_streak_start_date": None,
    }


def _trading_days_between(calendar, after_date, up_to_date):
    """Trading days strictly after `after_date` and up to (inclusive)
    `up_to_date`, counted on the market calendar (dates the exchange
    actually traded) — never weekends or holidays. 0 means same day."""
    return sum(1 for d in calendar if after_date < d <= up_to_date)


def _market_calendar(date_counts, n_symbols, latest_market_date):
    """The set of dates the market traded, taken from the data itself (so
    NSE holidays drop out automatically, no hard-coded holiday list to go
    stale). A date counts when at least 20% of symbols have a bar on it —
    that filters a stray one-symbol artefact — and the latest market date
    is always included so a freshly-closed day is never miscounted."""
    floor = max(3, int(n_symbols * 0.2))
    cal = {d for d, c in date_counts.items() if c >= floor}
    if latest_market_date:
        cal.add(latest_market_date)
    return sorted(cal)


def build_supertrend(universe="fno"):
    validated = load_validated_set()
    partial = load_partial_set()
    available = validated | partial
    target_symbols = load_fno_symbols() if universe == "fno" else sorted(available)

    from collections import Counter
    date_counts = Counter()  # date -> number of symbols with a bar that day (recent window only)
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
        date_counts.update(str(d.date()) for d in df.index[-90:])

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
        streak_days, streak_start_date = _current_streak(direction, df.index)

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
            "current_streak_days": streak_days,
            "current_streak_start_date": streak_start_date,
        }
        entries.append(entry)
        if flipped:
            (bullish_flips if today_dir == "up" else bearish_flips).append(entry)


    # --- Market calendar + staleness (added 2026-11-01) --------------------
    # Each entry's own last bar can lag the market's latest trading day when a
    # fetch regresses or a provider is late (seen 2026-10-30: 1,492 of 1,499
    # symbols silently lost their last bar). Counting "days since flip" from
    # the symbol's own last bar then understates it and hides the problem.
    # So: express flip age in TRADING days up to the market's latest date,
    # and mark any entry whose bar is behind it.
    today_ist = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()
    seen = [d for d in date_counts if d <= today_ist]
    latest_market_date = max(seen) if seen else None
    calendar = _market_calendar({d: c for d, c in date_counts.items() if d <= today_ist}, len(entries), latest_market_date)
    stale_count = 0
    for e in entries:
        lag = _trading_days_between(calendar, e["date"], latest_market_date) if latest_market_date and e.get("date") else None
        e["data_lag_trading_days"] = lag
        e["stale_data"] = bool(lag)
        stale_count += 1 if lag else 0
        for src, dst in (("recent_flip_date", "recent_flip_trading_days_ago"),
                         ("recent_bullish_flip_date", "recent_bullish_flip_trading_days_ago")):
            fd = e.get(src)
            e[dst] = _trading_days_between(calendar, fd, latest_market_date) if fd and latest_market_date else None
        sd = e.get("current_streak_start_date")
        e["current_streak_trading_days"] = _trading_days_between(calendar, sd, latest_market_date) if sd and latest_market_date else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_market_date": latest_market_date,
        "stale_symbols": stale_count,
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
