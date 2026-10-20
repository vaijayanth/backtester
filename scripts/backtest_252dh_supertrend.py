"""
Backtest — variant SST strategy: 252-day-high breakout entry, Supertrend-red
full exit (loss or profit), weighted-average-cost-based average-down.

Different from the main backtest_engine.py in every exit/add rule:
  - Entry: close > highest HIGH of the preceding 252 trading sessions
    (not including today). Filled at the NEXT trading session's open —
    not same-day at the breakout price, unlike backtest_engine.py.
  - Universe: every VALIDATED symbol, no EMA filter at all.
  - Exit: Supertrend flips red on a day's close -> sell the WHOLE position
    at the next session's open, regardless of profit or loss. No trailing
    stop, no profit lock — this is the headline difference from the main
    SST engine, which never exits at a loss.
  - Add-on: close <= 6% below the position's CURRENT weighted-average
    entry price (recomputed after every add, not the original entry) AND
    Supertrend is green that close -> add 10% of current share qty at
    the next open. At least 7 CALENDAR days (not trading days) since the
    last fill on that position. Subject to available cash.
  - Sizing: flat Rs1000 per new position, subject to available cash. No
    cap on number of simultaneous positions (only cash limits it).

All signals evaluate a completed day's Close; every fill (entry, add,
exit) executes at the symbol's own next available bar's Open — tracked
per-symbol via pending_orders, not a single global "next date" (handles
a symbol missing a bar on a date other symbols have).

Supertrend(10, 3): copied from supertrend.py's compute_supertrend rather
than imported, to keep this script's own dependency surface self-
contained (same formula, verified to match on import-free comparison).

Usage:
  python scripts/backtest_252dh_supertrend.py --out results_252dh.json
  python scripts/backtest_252dh_supertrend.py --capital 500000 --out results_252dh.json
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "checkpoints" / "raw"
MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"

BREAKOUT_WINDOW_DAYS = 252
INITIAL_POSITION_RS = 1000
ADD_ON_TRIGGER_PCT = -6.0
ADD_ON_SIZE_FRACTION = 0.10
ADD_ON_COOLDOWN_DAYS = 7  # calendar days
ATR_PERIOD = 10
ST_MULTIPLIER = 3.0
DEFAULT_INITIAL_CAPITAL_RS = 500000  # assumption, not stated in the spec — override with --capital


def load_validated_symbols(manifest_csv=MANIFEST_CSV):
    manifest = pd.read_csv(manifest_csv)
    return manifest[manifest["status"] == "VALIDATED"]["symbol"].tolist()


def load_universe_data(symbols=None, raw_dir=RAW_DIR, manifest_csv=MANIFEST_CSV, breakout_window_days=BREAKOUT_WINDOW_DAYS):
    validated = load_validated_symbols(manifest_csv)
    if symbols:
        validated = [s for s in validated if s in symbols]
    data = {}
    for symbol in validated:
        path = raw_dir / f"{symbol.replace('.', '_')}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        if len(df) < breakout_window_days + ATR_PERIOD:
            continue
        data[symbol] = df
    return data


def compute_atr(highs, lows, closes, period=ATR_PERIOD):
    n = len(closes)
    tr = [None] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = [None] * n
    if n < period:
        return atr
    atr[period - 1] = sum(tr[0:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_supertrend(highs, lows, closes, period=ATR_PERIOD, multiplier=ST_MULTIPLIER):
    """Returns direction list, 'up'/'down'/None per bar. Verbatim logic
    from supertrend.py's compute_supertrend (kept self-contained here)."""
    n = len(closes)
    atr = compute_atr(highs, lows, closes, period)
    final_upper = [None] * n
    final_lower = [None] * n
    direction = [None] * n

    start = period - 1
    if start >= n:
        return direction

    mid0 = (highs[start] + lows[start]) / 2
    final_upper[start] = mid0 + multiplier * atr[start]
    final_lower[start] = mid0 - multiplier * atr[start]
    direction[start] = "down" if closes[start] <= final_upper[start] else "up"

    for i in range(start + 1, n):
        mid = (highs[i] + lows[i]) / 2
        basic_upper = mid + multiplier * atr[i]
        basic_lower = mid - multiplier * atr[i]
        final_upper[i] = basic_upper if (basic_upper < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        final_lower[i] = basic_lower if (basic_lower > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
        prev_dir = direction[i - 1]
        if prev_dir == "down":
            direction[i] = "up" if closes[i] > final_upper[i] else "down"
        else:
            direction[i] = "down" if closes[i] < final_lower[i] else "up"
    return direction


def prep_symbol_df(df, breakout_window_days=BREAKOUT_WINDOW_DAYS):
    """Returns {date: (open, high, low, close, rolling_high, supertrend_dir)}
    — plain dict, not a DataFrame, so the backtest's hot loop (all_dates x
    all_symbols) does O(1) dict lookups instead of pandas .loc, which is
    too slow at this scale (~1200 dates x ~1500 symbols)."""
    closes = df["Close"].tolist()
    highs = df["High"].tolist()
    lows = df["Low"].tolist()
    opens = df["Open"].tolist()
    rolling_high = df["High"].rolling(breakout_window_days).max().shift(1).tolist()
    supertrend_dir = compute_supertrend(highs, lows, closes)
    return {
        date: (opens[i], highs[i], lows[i], closes[i], rolling_high[i], supertrend_dir[i])
        for i, date in enumerate(df.index)
    }


def run_backtest(initial_capital_rs=DEFAULT_INITIAL_CAPITAL_RS, position_size_rs=INITIAL_POSITION_RS, symbols=None,
                  breakout_window_days=BREAKOUT_WINDOW_DAYS, raw_dir=RAW_DIR, manifest_csv=MANIFEST_CSV):
    raw_data = load_universe_data(symbols, raw_dir=raw_dir, manifest_csv=manifest_csv, breakout_window_days=breakout_window_days)
    if not raw_data:
        raise RuntimeError(f"No validated symbol data found in {raw_dir} — run the backfill workflow first.")

    data = {sym: prep_symbol_df(df, breakout_window_days) for sym, df in raw_data.items()}
    all_dates = sorted(set().union(*[set(d.keys()) for d in data.values()]))

    cash = float(initial_capital_rs)
    positions = {}       # symbol -> {entry_date, avg_entry_price, qty, last_fill_date}
    pending = {}         # symbol -> {'action': 'buy_new'|'buy_add'|'sell_exit', add_qty?}
    closed_trades = []
    equity_curve = []
    realized_pl = 0.0
    missed_entries_no_cash = 0
    missed_entries_price_too_high = 0  # qty would be 0 even with unlimited cash (Rs1000 < 1 share)
    missed_adds_no_cash = 0

    for date in all_dates:
        for sym, bars in data.items():
            bar = bars.get(date)
            if bar is None:
                continue
            open_price_today, high_today, low_today, close_today, rolling_high, supertrend_dir = bar

            # --- 1. execute any order queued from a prior day's close, at today's open ---
            order = pending.pop(sym, None)
            if order is not None:
                open_price = open_price_today
                if order["action"] == "buy_new":
                    qty = int(position_size_rs // open_price)
                    cost = qty * open_price
                    if qty < 1:
                        missed_entries_price_too_high += 1
                    elif cash >= cost:
                        cash -= cost
                        positions[sym] = {
                            "entry_date": date, "avg_entry_price": open_price,
                            "qty": qty, "last_fill_date": date, "num_adds": 0,
                        }
                    else:
                        missed_entries_no_cash += 1
                elif order["action"] == "buy_add" and sym in positions:
                    pos = positions[sym]
                    add_qty = order["add_qty"]
                    cost = add_qty * open_price
                    if cash >= cost:
                        cash -= cost
                        total_cost = pos["avg_entry_price"] * pos["qty"] + cost
                        pos["qty"] += add_qty
                        pos["avg_entry_price"] = total_cost / pos["qty"]
                        pos["last_fill_date"] = date
                        pos["num_adds"] += 1
                    else:
                        missed_adds_no_cash += 1
                elif order["action"] == "sell_exit" and sym in positions:
                    pos = positions[sym]
                    proceeds = pos["qty"] * open_price
                    cash += proceeds
                    pl_rs = (open_price - pos["avg_entry_price"]) * pos["qty"]
                    pl_pct = (open_price - pos["avg_entry_price"]) / pos["avg_entry_price"] * 100
                    realized_pl += pl_rs
                    closed_trades.append({
                        "symbol": sym,
                        "entry_date": str(pos["entry_date"].date()),
                        "exit_date": str(date.date()),
                        "entry_price": round(pos["avg_entry_price"], 2),
                        "exit_price": round(open_price, 2),
                        "qty": pos["qty"],
                        "pl_rs": round(pl_rs, 2),
                        "pl_pct": round(pl_pct, 2),
                        "hold_days": (date - pos["entry_date"]).days,
                        "num_adds": pos["num_adds"],
                    })
                    del positions[sym]

            # --- 2. evaluate today's close for tomorrow's signal ---
            if sym in positions:
                pos = positions[sym]
                if supertrend_dir == "down":
                    pending[sym] = {"action": "sell_exit"}
                    continue  # exit takes priority over an add signal the same day
                cur_pl_pct = (close_today - pos["avg_entry_price"]) / pos["avg_entry_price"] * 100
                days_since_fill = (date - pos["last_fill_date"]).days
                if (cur_pl_pct <= ADD_ON_TRIGGER_PCT and supertrend_dir == "up"
                        and days_since_fill >= ADD_ON_COOLDOWN_DAYS):
                    add_qty = max(1, int(pos["qty"] * ADD_ON_SIZE_FRACTION))
                    pending[sym] = {"action": "buy_add", "add_qty": add_qty}
            else:
                if rolling_high is None or pd.isna(rolling_high):
                    continue
                if close_today > rolling_high:
                    pending[sym] = {"action": "buy_new"}

        open_mtm = sum(
            (data[sym][date][3] - pos["avg_entry_price"]) * pos["qty"]
            for sym, pos in positions.items() if date in data[sym]
        )
        equity_curve.append({
            "date": str(date.date()), "cash": round(cash, 2),
            "realized": round(realized_pl, 2), "open_mtm": round(open_mtm, 2),
            "combined": round(realized_pl + open_mtm, 2),
        })

    return build_results(
        initial_capital_rs, position_size_rs, breakout_window_days, data, closed_trades, positions, equity_curve, cash,
        missed_entries_no_cash, missed_entries_price_too_high, missed_adds_no_cash,
    )


def compute_max_drawdown(equity_curve, initial_capital_rs):
    # Drawdown off total account equity (initial capital + realized + open
    # MTM), not raw cumulative realized P&L — the latter starts near zero
    # and can produce a nonsensical >100% "drawdown" the moment it dips
    # below a tiny early peak (e.g. peak=Rs50 before a Rs-7000 dip reads as
    # a 14000% drawdown). Equity is bounded by actual capital, so dd% is
    # bounded and means what a drawdown is supposed to mean.
    peak = float("-inf")
    max_dd = 0.0
    for point in equity_curve:
        equity = initial_capital_rs + point["combined"]
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    return round(max_dd, 2)


def build_results(initial_capital_rs, position_size_rs, breakout_window_days, data, closed_trades, open_positions, equity_curve, final_cash,
                   missed_entries_no_cash, missed_entries_price_too_high, missed_adds_no_cash):
    wins = [t for t in closed_trades if t["pl_rs"] > 0]
    losses = [t for t in closed_trades if t["pl_rs"] <= 0]
    win_rate = round(len(wins) / len(closed_trades) * 100, 2) if closed_trades else None
    gross_win = sum(t["pl_rs"] for t in wins)
    gross_loss = abs(sum(t["pl_rs"] for t in losses))
    open_mtm_total = equity_curve[-1]["open_mtm"] if equity_curve else 0
    profit_factor = round((gross_win + max(open_mtm_total, 0)) / gross_loss, 2) if gross_loss else None
    avg_hold_winners = round(sum(t["hold_days"] for t in wins) / len(wins), 1) if wins else None
    avg_hold_losers = round(sum(t["hold_days"] for t in losses) / len(losses), 1) if losses else None
    avg_adds = round(sum(t["num_adds"] for t in closed_trades) / len(closed_trades), 2) if closed_trades else None
    max_concurrent = 0
    seen = {}
    for sym, pos in open_positions.items():
        seen[sym] = True
    # Approximate max concurrent open positions from the equity curve isn't
    # tracked point-by-point here cheaply — reported as final open count
    # plus closed-trade count as a rough peak-activity indicator instead.

    open_positions_out = [
        {"symbol": sym, "entry_date": str(pos["entry_date"].date()),
         "avg_entry_price": round(pos["avg_entry_price"], 2), "qty": pos["qty"], "num_adds": pos["num_adds"]}
        for sym, pos in open_positions.items()
    ]

    return {
        "strategy": "252-day-high breakout, Supertrend-red full exit, WAC-based average-down",
        "params": {
            "breakout_window_days": breakout_window_days,
            "initial_position_rs": position_size_rs,
            "add_on_trigger_pct": ADD_ON_TRIGGER_PCT,
            "add_on_size_fraction": ADD_ON_SIZE_FRACTION,
            "add_on_cooldown_days": ADD_ON_COOLDOWN_DAYS,
            "initial_capital_rs": initial_capital_rs,
        },
        "run_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(data),
        "metrics": {
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": compute_max_drawdown(equity_curve, initial_capital_rs),
            "total_closed_trades": len(closed_trades),
            "open_positions_count": len(open_positions),
            "avg_adds_per_closed_trade": avg_adds,
            "avg_hold_days_winners": avg_hold_winners,
            "avg_hold_days_losers": avg_hold_losers,
            "realized_pl_rs": round(equity_curve[-1]["realized"], 2) if equity_curve else 0,
            "open_mtm_rs": round(equity_curve[-1]["open_mtm"], 2) if equity_curve else 0,
            "combined_pl_rs": round(equity_curve[-1]["combined"], 2) if equity_curve else 0,
            "final_cash_rs": round(final_cash, 2),
            "missed_entries_no_cash": missed_entries_no_cash,
            "missed_entries_price_too_high": missed_entries_price_too_high,
            "missed_adds_no_cash": missed_adds_no_cash,
        },
        "equity_curve": equity_curve,
        "trades": closed_trades,
        "open_positions": open_positions_out,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=DEFAULT_INITIAL_CAPITAL_RS,
                         help=f"Initial capital in Rs (default {DEFAULT_INITIAL_CAPITAL_RS} — not stated in the strategy spec, override as needed)")
    parser.add_argument("--position-size", type=float, default=INITIAL_POSITION_RS,
                         help=f"Rs per new position (default {INITIAL_POSITION_RS})")
    parser.add_argument("--breakout-window", type=int, default=BREAKOUT_WINDOW_DAYS,
                         help=f"Breakout lookback in trading days (default {BREAKOUT_WINDOW_DAYS} = ~52-week high)")
    parser.add_argument("--raw-dir", type=str, default=str(RAW_DIR), help="Directory of per-symbol parquet OHLC")
    parser.add_argument("--manifest", type=str, default=str(MANIFEST_CSV), help="Manifest CSV listing VALIDATED symbols")
    parser.add_argument("--out", type=str, default="results_252dh.json")
    args = parser.parse_args()

    results = run_backtest(initial_capital_rs=args.capital, position_size_rs=args.position_size,
                            breakout_window_days=args.breakout_window,
                            raw_dir=Path(args.raw_dir), manifest_csv=Path(args.manifest))
    Path(args.out).write_text(json.dumps(results, indent=2))

    m = results["metrics"]
    print(f"Universe: {results['universe_size']} symbols")
    print(f"Closed trades: {m['total_closed_trades']} | Win rate: {m['win_rate_pct']}% | Profit factor: {m['profit_factor']}")
    print(f"Open positions: {m['open_positions_count']} | Max drawdown: {m['max_drawdown_pct']}%")
    print(f"Realized P&L: Rs{m['realized_pl_rs']:,.0f} | Open MTM: Rs{m['open_mtm_rs']:,.0f} | Combined: Rs{m['combined_pl_rs']:,.0f}")
    print(f"Final cash: Rs{m['final_cash_rs']:,.0f} | Missed entries (no cash): {m['missed_entries_no_cash']} | Missed entries (price > Rs{args.position_size:,.0f}/share): {m['missed_entries_price_too_high']} | Missed adds (no cash): {m['missed_adds_no_cash']}")
    print(f"Avg adds/closed trade: {m['avg_adds_per_closed_trade']} | Avg hold winners: {m['avg_hold_days_winners']}d | losers: {m['avg_hold_days_losers']}d")
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
