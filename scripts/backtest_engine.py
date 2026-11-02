"""
SST Backtest Engine — walks available historical OHLC day by day, applies
the fully-parameterized SST swing strategy (docs/SPEC.md section 7),
produces a results JSON matching docs/SPEC.md section 8.

Mechanics, exactly as locked in the spec:
  - Entry: mechanical N-Day-High breakout ONLY (breakout_window_days).
    No Pullback/EMA-pullback setups simulated (not mechanically well-defined
    from OHLC alone — see SPEC.md "Entry signal scope").
  - Universe filter: price above BOTH ema_filter_fast and ema_filter_slow.
  - Exit: ONLY via the profit trailing-stop ratchet (trail_checkpoints).
    NO loss-exit at all — matches live SST design intent exactly, not a
    simplification. A losing position rides to the end of the backtest
    window if it never recovers into profit.
  - Add-on: fires on a FRESH N-Day-High breakout (same mechanical signal as
    a new entry) while the position is in loss beyond add_on_trigger_pct
    AND price is above both EMAs — not an unconditional add the instant the
    loss threshold is crossed. This matches the canonical system description
    ("place a NEW GTT at 21DH for add-on qty") over a looser reading of the
    condensed parameter table. Capped at add_on_max_per_week per stock.
  - Position sizing: tiers up by position_size_increment_rs every
    position_size_increment_trades CLOSED trades (portfolio-wide count),
    capped at position_size_cap_rs.
  - EMA formula: manually seeded (SMA of first N) + recursive multiplier,
    NOT pandas' .ewm() default — matches the exact formula already used in
    Stock Detail's JS and the Options Terminal Python backend, so results
    are comparable across the whole system.

Usage:
  python scripts/backtest_engine.py                        # all defaults
  python scripts/backtest_engine.py --params myparams.json  # override params
  python scripts/backtest_engine.py --out results/run1.json
"""

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import common
from common import MANIFEST_CSV, RAW_DIR, ROOT  # noqa: F401  (module attrs stay patchable)
from indicators import compute_ema

DEFAULT_PARAMS = {
    "breakout_window_days": 21,
    "ema_filter_fast": 50,
    "ema_filter_slow": 200,
    # Confirmed via direct user spec + manual trade-by-trade validation against
    # 360ONE.NS (docs/SPEC.md section 7): activation at 7% peak profit, then a
    # FLAT 6% trail thereafter — not the earlier placeholder 4-tier progressive
    # ratchet (6/10/15/25% -> 6/5/4/3%), which produced same-day activate+exit
    # whipsaws because it armed at only 6%. A single checkpoint at profit_pct=0
    # makes trail_distance_for_profit() return trail_pct=6 for any profit level
    # once armed, i.e. a constant distance — trail_checkpoints still supports
    # multiple tiers if ever reintroduced, just unused by this confirmed default.
    "profit_lock_trigger_pct": 7.0,
    "trail_checkpoints": [
        {"profit_pct": 0, "trail_pct": 6},
    ],
    "add_on_trigger_pct": -7.0,
    "add_on_size_fraction": 0.1,
    "add_on_max_per_week": 1,
    "base_position_rs": 20000,
    "position_size_increment_rs": 5000,
    "position_size_increment_trades": 100,
    "position_size_cap_rs": 100000,
    "max_open_positions": 200,
    "initial_capital_rs": 5000000,
    # Realism switches (added 2026-11-01; set to the legacy value to reproduce
    # runs made before this date — those results are NOT comparable):
    #  - gap_aware_fills: a buy-stop at level L fills at max(Open, L) and a sell-stop
    #    at S fills at min(Open, S). Legacy (False) filled at L / S even when the
    #    day gapped through them, i.e. at a price that never traded.
    #  - entry_priority: when more breakouts fire on a day than max_open_positions
    #    allows: "breakout_strength" (default, user's choice 2026-11-02) takes the biggest breakout
    #    first (size of the opening gap above the breakout level, in %: the only strength known when stops
    #    trigger), all non-gap triggers tie and follow the seeded shuffle;
    #    "random" is the seeded per-date shuffle (reproducible, unbiased); legacy "symbol" took them
    #    in manifest order (alphabetical bias).
    "gap_aware_fills": True,
    "entry_priority": "breakout_strength",
    "random_seed": 0,
    #  - intrabar_order: daily OHLC can't say whether the High or the Low came first.
    #    "high_first" (default, legacy): today's High can arm/raise the trailing stop and the same
    #    day's Low can hit it. "low_first": only a stop set at a PRIOR close can be hit by today's
    #    Low; today's High raises the stop for tomorrow. Neither is "conservative": on 350 real
    #    symbols low_first gave HIGHER P&L (see docs/SPEC.md section 27) — treat results as a range.
    #    To reproduce a pre-2026-11-01 run use gap_aware_fills=False, entry_priority="symbol".
    "intrabar_order": "high_first",
    # Fit-to-live-trading switches (added 2026-11-02; ALL OFF by default so existing results are unchanged —
    # measured one by one in docs/SPEC.md section 31; "REALISTIC_PROFILE" below turns them all on):
    #  - cash_limited: only buy while cash is available (account = initial_capital_rs, default Rs 50,00,000);
    #    off = up to max_open_positions with no cash check.
    #  - fee_buy_pct / fee_sell_pct: statutory charges per side as a fraction of trade value (delivery, free-brokerage
    #    broker: buy ~0.12% = STT 0.1 + stamp 0.015 + exchange/SEBI; sell ~0.11% = STT 0.1 + exchange/SEBI/GST);
    #    slippage_pct: adverse fill vs the modelled price, per side. All 0 = trades for free at exact prices.
    #  - min_price / min_avg_traded_value_rs: skip stocks you would not really trade — price below min_price, or
    #    20-day average traded value (Close x Volume, up to yesterday) below the floor. 0 = no filter.
    #  - stop_peak: "high" (legacy) ratchets the trailing stop off each day's intraday High; "close" off the highest
    #    CLOSE only (you move your GTT by hand after the close, so it lags the intraday peak).
    "cash_limited": False,
    "fee_buy_pct": 0.0,
    "fee_sell_pct": 0.0,
    "slippage_pct": 0.0,
    "min_price": 0.0,
    "min_avg_traded_value_rs": 0.0,
    "stop_peak": "high",
}

REALISTIC_PROFILE = {
    "cash_limited": True,
    "fee_buy_pct": 0.0012,
    "fee_sell_pct": 0.0011,
    "slippage_pct": 0.001,
    "min_price": 50.0,
    "min_avg_traded_value_rs": 1e7,   # Rs 1 crore a day
    "stop_peak": "close",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_validated_symbols():
    """VALIDATED symbols from MANIFEST_CSV (read at call time so tests/tools can repoint it)."""
    return common.load_validated_symbols(MANIFEST_CSV)


def load_universe_data(symbols=None):
    """Returns {symbol: DataFrame} for VALIDATED symbols with a parquet
    checkpoint on disk. symbols=None loads everything validated."""
    validated = load_validated_symbols()
    if symbols:
        validated = [s for s in validated if s in symbols]

    data = {}
    for symbol in validated:
        path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        data[symbol] = df
    return data


# ---------------------------------------------------------------------------
# Indicators — manually seeded EMA, matching Stock Detail's JS exactly
# ---------------------------------------------------------------------------
def prep_symbol_df(df, params):
    df = df.copy()
    closes = df["Close"].tolist()
    df["ema_fast"] = compute_ema(closes, params["ema_filter_fast"])
    df["ema_slow"] = compute_ema(closes, params["ema_filter_slow"])
    # N-day high = highest HIGH of the preceding N days, NOT including today
    # — standard breakout definition, matches "GTT at the 21DH price."
    df["rolling_high"] = df["High"].rolling(params["breakout_window_days"]).max().shift(1)
    # 20-day average traded value up to YESTERDAY (shift(1): no look-ahead) for the liquidity filter
    df["avg_value"] = (df["Close"] * df["Volume"]).rolling(20).mean().shift(1)
    return df


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------
def buy_stop_fill(day_open, level, params):
    """Fill price of a buy-stop resting at `level` on a day that triggered it."""
    return max(day_open, level) if params.get("gap_aware_fills", True) else level


def sell_stop_fill(day_open, stop, params):
    """Fill price of a sell-stop resting at `stop` on a day whose Low breached it."""
    return min(day_open, stop) if params.get("gap_aware_fills", True) else stop


def entry_order(symbols, date, params):
    """Order in which same-day entry candidates are considered when the position
    cap can bind. Seeded per date so a run is exactly reproducible."""
    syms = list(symbols)
    if params.get("entry_priority", "random") == "symbol":
        return syms
    rng = random.Random(f"{params.get('random_seed', 0)}|{date.date()}")
    rng.shuffle(syms)
    return syms


def trail_distance_for_profit(profit_pct, params):
    checkpoints = sorted(params["trail_checkpoints"], key=lambda c: c["profit_pct"])
    dist = checkpoints[0]["trail_pct"] if checkpoints else 6.0
    for cp in checkpoints:
        if profit_pct >= cp["profit_pct"]:
            dist = cp["trail_pct"]
    return dist


def current_tier_size(closed_trades_count, params):
    size = params["base_position_rs"] + (
        closed_trades_count // params["position_size_increment_trades"]
    ) * params["position_size_increment_rs"]
    return min(size, params["position_size_cap_rs"])


_BAR_COLS = ["Open", "High", "Low", "Close", "ema_fast", "ema_slow", "rolling_high", "avg_value"]
_O, _H, _L, _C, _EF, _ES, _RH, _AV = range(8)


VALID_CHOICES = {"intrabar_order": ("high_first", "low_first"), "entry_priority": ("breakout_strength", "random", "symbol"),
                 "stop_peak": ("high", "close")}


def run_backtest(params=None, symbols=None):
    params = {**DEFAULT_PARAMS, **(params or {})}
    for key, allowed in VALID_CHOICES.items():   # a typo must not silently run the legacy mode
        if params[key] not in allowed:
            raise ValueError(f"params['{key}'] must be one of {allowed}, got {params[key]!r}")
    raw_data = load_universe_data(symbols)
    if not raw_data:
        raise RuntimeError(
            "No validated symbol data found in checkpoints/raw/ — "
            "run the backfill workflow first."
        )

    # One {date: (O, H, L, C, ema_fast, ema_slow, rolling_high)} dict per symbol: the hot loop
    # below does O(1) dict lookups instead of pandas `date in df.index` + `df.loc[date]`
    # (~1.9M lookups on the full universe; same approach as backtest_252dh_supertrend.py).
    data = {}
    for sym, df in raw_data.items():
        pdf = prep_symbol_df(df, params)
        data[sym] = dict(zip(pdf.index, pdf[_BAR_COLS].itertuples(index=False, name=None)))
    del raw_data
    all_dates = sorted(set().union(*[set(bars) for bars in data.values()]))

    positions = {}       # symbol -> position dict
    closed_trades = []   # list of closed-trade dicts
    equity_curve = []    # [{date, realized, open_mtm, combined}]
    realized_pl = 0.0
    cash_limited = params["cash_limited"]
    cash = float(params["initial_capital_rs"])          # only enforced when cash_limited
    fee_buy, fee_sell, slip = params["fee_buy_pct"], params["fee_sell_pct"], params["slippage_pct"]
    costs_on = bool(fee_buy or fee_sell or slip)
    min_price, min_value = params["min_price"], params["min_avg_traded_value_rs"]
    peak_from_close = params["stop_peak"] == "close"

    def tradable(row):
        """Liquidity filter: skip stocks you would not really trade (known at the open: yesterday's data)."""
        if min_price and row[_O] < min_price:
            return False
        if min_value and not (row[_AV] >= min_value):    # NaN (not enough history) fails too
            return False
        return True

    for date in all_dates:
        # --- 1. manage existing positions: trailing-stop exits, add-ons ---
        for sym in list(positions.keys()):
            row = data[sym].get(date)
            if row is None:
                continue
            pos = positions[sym]

            # Intrabar order: OHLC can't say whether the day's High or Low came first.
            #  "high_first" (default, legacy): the High is reached first, so today's High
            #     can arm/raise the stop and the same day's Low can hit it.
            #  "low_first": the Low comes first, so only a stop that already
            #     existed at yesterday's close can be hit today; today's High only raises
            #     the stop for tomorrow.
            low_first = params.get("intrabar_order", "high_first") == "low_first"
            stop_level = None
            if low_first:
                prior_stop = pos.get("stop_price", 0)
                if prior_stop > 0 and row[_L] <= prior_stop:
                    stop_level = prior_stop
            pos["peak_price"] = max(pos["peak_price"], row[_C] if peak_from_close else row[_H])
            profit_pct = (pos["peak_price"] - pos["avg_entry_price"]) / pos["avg_entry_price"] * 100
            cur_pl_pct = (row[_C] - pos["avg_entry_price"]) / pos["avg_entry_price"] * 100

            if profit_pct >= params["profit_lock_trigger_pct"]:
                dist = trail_distance_for_profit(profit_pct, params)
                candidate_stop = pos["peak_price"] * (1 - dist / 100)
                pos["stop_price"] = max(pos.get("stop_price", 0), candidate_stop)  # ratchet never loosens
                if not low_first and row[_L] <= pos["stop_price"]:
                    stop_level = pos["stop_price"]

            if stop_level is not None:
                exit_price = sell_stop_fill(row[_O], stop_level, params) * (1 - slip)
                proceeds = exit_price * pos["qty"]
                sell_fee = proceeds * fee_sell
                pl_rs = (exit_price - pos["avg_entry_price"]) * pos["qty"] - pos["fees"] - sell_fee
                cash += proceeds - sell_fee
                # Computed from exit_price (the actual stop fill), NOT
                # cur_pl_pct (which is based on the day's Close) — those
                # two can diverge significantly on the exit day itself,
                # since the stop fires on Low breaching the stop level,
                # not at the day's closing price. Confirmed as a real
                # bug via manual trade inspection before this fix.
                exit_pl_pct = (pl_rs / (pos["avg_entry_price"] * pos["qty"]) * 100 if costs_on
                               else (exit_price - pos["avg_entry_price"]) / pos["avg_entry_price"] * 100)
                realized_pl += pl_rs
                closed_trades.append({
                    "symbol": sym,
                    "entry_date": str(pos["entry_date"].date()),
                    "exit_date": str(date.date()),
                    "entry_price": round(pos["avg_entry_price"], 2),
                    "exit_price": round(exit_price, 2),
                    "qty": pos["qty"],
                    "pl_rs": round(pl_rs, 2),
                    "pl_pct": round(exit_pl_pct, 2),
                    "hold_days": (date - pos["entry_date"]).days,
                    "had_addon": pos["last_addon_date"] is not None,
                })
                del positions[sym]
                continue  # no add-on check on a day we just exited

            if cur_pl_pct <= params["add_on_trigger_pct"]:
                above_emas = (
                    pd.notna(row[_EF]) and pd.notna(row[_ES])
                    and row[_C] > row[_EF] and row[_C] > row[_ES]
                )
                fresh_breakout = pd.notna(row[_RH]) and row[_H] >= row[_RH]
                days_since_addon = (
                    (date - pos["last_addon_date"]).days if pos["last_addon_date"] else 999
                )
                if above_emas and fresh_breakout and days_since_addon >= 7 and tradable(row):
                    add_qty = max(1, int(pos["qty"] * params["add_on_size_fraction"]))
                    add_price = buy_stop_fill(row[_O], row[_RH], params) * (1 + slip)
                    add_value = add_price * add_qty
                    add_fee = add_value * fee_buy
                    if not cash_limited or cash >= add_value + add_fee:
                        cash -= add_value + add_fee
                        total_cost = pos["avg_entry_price"] * pos["qty"] + add_value
                        pos["qty"] += add_qty
                        pos["avg_entry_price"] = total_cost / pos["qty"]
                        pos["fees"] += add_fee
                        pos["last_addon_date"] = date

        # --- 2. new entries ---
        if len(positions) < params["max_open_positions"]:
            # Eligible breakouts first, then ranked, then opened until the position cap. Eligibility does
            # not depend on positions opened earlier the same day, so this equals the old one-pass loop
            # for "random" / "symbol" priority.
            candidates = []
            for sym in entry_order(data.keys(), date, params):
                if sym in positions:
                    continue
                row = data[sym].get(date)
                if row is None:
                    continue
                if pd.isna(row[_RH]) or pd.isna(row[_EF]) or pd.isna(row[_ES]):
                    continue
                above_emas = row[_C] > row[_EF] and row[_C] > row[_ES]
                breakout = row[_H] >= row[_RH]
                if above_emas and breakout and tradable(row):
                    candidates.append((sym, row))
            if params.get("entry_priority", "breakout_strength") == "breakout_strength":
                # Biggest breakout first, using ONLY what is known when the stops trigger: the size of the
                # opening gap above the breakout level (a gap-up triggers at the open, ahead of every stop
                # that triggers later in the session). NOT the day's High: that is only known at the close,
                # so ranking by it picks the day's biggest winners (look-ahead; it inflated P&L ~5x in a
                # first version). Non-gap triggers all tie at 0 and fall to the seeded shuffle, because the
                # intraday order in which they trigger cannot be known from daily bars.
                # sort is stable: ties keep the seeded-shuffle order, never alphabetical.
                candidates.sort(key=lambda c: -max(0.0, c[1][_O] / c[1][_RH] - 1))
            for sym, row in candidates:
                entry_price = buy_stop_fill(row[_O], row[_RH], params) * (1 + slip)
                size_rs = current_tier_size(len(closed_trades), params)
                qty = int(size_rs // entry_price)
                if qty < 1:
                    continue
                entry_value = entry_price * qty
                entry_fee = entry_value * fee_buy
                if cash_limited and cash < entry_value + entry_fee:
                    continue          # not enough cash for this one; a cheaper candidate may still fit
                cash -= entry_value + entry_fee
                positions[sym] = {
                    "entry_date": date,
                    "avg_entry_price": entry_price,
                    "qty": qty,
                    "peak_price": entry_price,
                    "stop_price": 0,
                    "last_addon_date": None,
                    "fees": entry_fee,
                }
                if len(positions) >= params["max_open_positions"]:
                    break

        # --- 3. equity curve point ---
        open_mtm = 0.0
        for sym, pos in positions.items():
            bar = data[sym].get(date)
            if bar is not None:
                open_mtm += (bar[_C] - pos["avg_entry_price"]) * pos["qty"] - pos["fees"]
        equity_curve.append({
            "date": str(date.date()),
            "realized": round(realized_pl, 2),
            "open_mtm": round(open_mtm, 2),
            "combined": round(realized_pl + open_mtm, 2),
        })

    return build_results(params, data, closed_trades, positions, equity_curve)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_max_drawdown(equity_curve):
    peak = float("-inf")
    max_dd = 0.0
    for point in equity_curve:
        val = point["realized"]  # closed-trades-only equity path, per spec (drawdown excludes open MTM)
        peak = max(peak, val)
        if peak > 0:
            dd = (peak - val) / peak * 100
            max_dd = max(max_dd, dd)
    return round(max_dd, 2)


def build_results(params, data, closed_trades, open_positions, equity_curve):
    wins = [t for t in closed_trades if t["pl_rs"] > 0]
    losses = [t for t in closed_trades if t["pl_rs"] <= 0]
    win_rate = round(len(wins) / len(closed_trades) * 100, 2) if closed_trades else None

    gross_win = sum(t["pl_rs"] for t in wins)
    gross_loss = abs(sum(t["pl_rs"] for t in losses))
    open_mtm_total = equity_curve[-1]["open_mtm"] if equity_curve else 0
    profit_factor = round((gross_win + max(open_mtm_total, 0)) / gross_loss, 2) if gross_loss else None

    avg_hold_winners = round(sum(t["hold_days"] for t in wins) / len(wins), 1) if wins else None
    avg_hold_losers = round(sum(t["hold_days"] for t in losses) / len(losses), 1) if losses else None

    open_positions_out = [
        {
            "symbol": sym,
            "entry_date": str(pos["entry_date"].date()),
            "avg_entry_price": round(pos["avg_entry_price"], 2),
            "qty": pos["qty"],
            "had_addon": pos["last_addon_date"] is not None,
        }
        for sym, pos in open_positions.items()
    ]

    return {
        "params": params,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(data),
        "metrics": {
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": compute_max_drawdown(equity_curve),
            "total_closed_trades": len(closed_trades),
            "open_positions_count": len(open_positions),
            "avg_hold_days_winners": avg_hold_winners,
            "avg_hold_days_losers": avg_hold_losers,
            "realized_pl_rs": round(equity_curve[-1]["realized"], 2) if equity_curve else 0,
            "open_mtm_rs": round(equity_curve[-1]["open_mtm"], 2) if equity_curve else 0,
            "combined_pl_rs": round(equity_curve[-1]["combined"], 2) if equity_curve else 0,
        },
        "equity_curve": equity_curve,
        "trades": closed_trades,
        "open_positions": open_positions_out,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", type=str, default=None, help="path to a JSON file overriding default params")
    parser.add_argument("--out", type=str, default="results.json")
    args = parser.parse_args()

    overrides = {}
    if args.params:
        overrides = json.loads(Path(args.params).read_text())

    results = run_backtest(params=overrides)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, indent=2))

    m = results["metrics"]
    print(f"Universe: {results['universe_size']} symbols")
    print(f"Closed trades: {m['total_closed_trades']}  Win rate: {m['win_rate_pct']}%  "
          f"Profit factor: {m['profit_factor']}")
    print(f"Open positions: {m['open_positions_count']}  Max drawdown: {m['max_drawdown_pct']}%")
    print(f"Realized P&L: ₹{m['realized_pl_rs']:,.0f}  Open MTM: ₹{m['open_mtm_rs']:,.0f}  "
          f"Combined: ₹{m['combined_pl_rs']:,.0f}")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
