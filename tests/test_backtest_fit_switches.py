"""SST backtest fit-to-live-trading switches: cash limit, costs + slippage, liquidity filter, close-based stop.
All default OFF (results unchanged). Run: python tests/test_backtest_fit_switches.py"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import numpy as np
import pandas as pd

import backtest_engine as e


def series(n=260, start=100.0, step=0.3, vol=1_000_000):
    idx = pd.bdate_range("2025-01-01", periods=n)
    c = start + np.arange(n) * step
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": vol}, index=idx)


def run(frames, **over):
    with mock.patch.object(e, "load_universe_data", lambda symbols=None: frames):
        return e.run_backtest({"initial_capital_rs": 1e7, **over})


def first_entry(df):
    r = run({"A.NS": df})
    return df.index.get_loc(pd.Timestamp(r["open_positions"][0]["entry_date"])), r["open_positions"][0]


def trade_scenario(df, i0, entry, peak_mult=1.12, exit_open_mult=0.70):
    """Entry, a +12% day that arms the lock (Low stays above the stop), then a gap far below the stop."""
    c = df.columns.get_loc
    df.iloc[i0 + 1, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 1.05, entry * peak_mult, entry * 1.05, entry * 1.09]
    df.iloc[i0 + 2, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * exit_open_mult, entry * 0.71, entry * 0.69, entry * 0.70]
    return df.iloc[: i0 + 3]


def test_defaults_are_off():
    d = e.DEFAULT_PARAMS
    assert d["cash_limited"] is False and d["fee_buy_pct"] == d["fee_sell_pct"] == d["slippage_pct"] == 0
    assert d["min_price"] == d["min_avg_traded_value_rs"] == 0 and d["stop_peak"] == "high"


def test_cash_limit_blocks_buys_beyond_cash():
    frames = {f"S{i}.NS": series(start=100 + i) for i in range(10)}
    free = run(frames, base_position_rs=100_000)
    capped = run(frames, base_position_rs=100_000, cash_limited=True, initial_capital_rs=350_000)
    assert len(free["open_positions"]) == 10
    assert 1 <= len(capped["open_positions"]) <= 3        # Rs 3.5L buys at most 3 positions of ~Rs 1L
    spent = sum(p["avg_entry_price"] * p["qty"] for p in capped["open_positions"])
    assert spent <= 350_000


def test_cash_limit_frees_cash_when_a_position_exits():
    df = series()
    i0, pos = first_entry(df)
    df = trade_scenario(df, i0, pos["avg_entry_price"])
    r = run({"A.NS": df}, cash_limited=True, initial_capital_rs=100_000, base_position_rs=90_000)
    assert len(r["trades"]) == 1       # bought with 90k of 100k, exit returned the cash


def test_costs_reduce_pl_by_exactly_fees_and_slippage():
    df = series()
    i0, pos = first_entry(df)
    df = trade_scenario(df, i0, pos["avg_entry_price"])
    g = run({"A.NS": df.copy()}, gap_aware_fills=True)["trades"][0]
    c = run({"A.NS": df.copy()}, fee_buy_pct=0.0012, fee_sell_pct=0.0011, slippage_pct=0.001)["trades"][0]
    assert c["pl_rs"] < g["pl_rs"]
    qty = c["qty"]
    buy_px, sell_px = g["entry_price"] * 1.001, g["exit_price"] * 0.999
    expected = (sell_px - buy_px) * qty - buy_px * qty * 0.0012 - sell_px * qty * 0.0011
    assert abs(c["pl_rs"] - expected) < max(5.0, qty * 0.02), (c["pl_rs"], expected)   # price rounding in the trade record


def test_costs_flow_into_open_mtm():
    df = series()
    base = run({"A.NS": df})["metrics"]["combined_pl_rs"]
    costly = run({"A.NS": df}, fee_buy_pct=0.01)["metrics"]["combined_pl_rs"]
    assert costly < base


def test_liquidity_filter_skips_penny_and_illiquid_names():
    frames = {"PENNY.NS": series(start=20, step=0.05, vol=5_000_000), "THIN.NS": series(start=300, vol=100), "GOOD.NS": series(start=300, vol=1_000_000)}
    assert {p["symbol"] for p in run(frames)["open_positions"]} == {"PENNY.NS", "THIN.NS", "GOOD.NS"}
    kept = {p["symbol"] for p in run(frames, min_price=50, min_avg_traded_value_rs=1e7)["open_positions"]}
    assert kept == {"GOOD.NS"}


def test_liquidity_uses_yesterdays_average_not_today():
    df = series(start=300, vol=100)
    i0, _ = first_entry(df)
    df.iloc[i0, df.columns.get_loc("Volume")] = 10**9        # a huge volume spike ON the breakout day
    df = df.iloc[: i0 + 1]                                    # data ends that day (later days would legitimately see the spike)
    assert run({"A.NS": df.copy()}, min_avg_traded_value_rs=1e7)["open_positions"] == []   # not visible at the open


def test_close_based_stop_lags_the_intraday_peak():
    df = series()
    i0, pos = first_entry(df)
    entry = pos["avg_entry_price"]
    c = df.columns.get_loc
    # day 1: intraday spike to +20% but it closes only +8%; day 2 opens at +14% then drops to the entry price
    df.iloc[i0 + 1, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 1.05, entry * 1.20, entry * 1.05, entry * 1.08]
    df.iloc[i0 + 2, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 1.14, entry * 1.15, entry * 1.00, entry * 1.02]
    df = df.iloc[: i0 + 3]
    high = run({"A.NS": df.copy()}, intrabar_order="low_first")["trades"]
    close = run({"A.NS": df.copy()}, intrabar_order="low_first", stop_peak="close")["trades"]
    assert len(high) == 1 and abs(high[0]["exit_price"] - round(entry * 1.20 * 0.94, 2)) < 0.011   # stop off the +20% High
    assert len(close) == 1 and abs(close[0]["exit_price"] - round(entry * 1.08 * 0.94, 2)) < 0.011  # stop off the +8% close


def test_realistic_profile_runs_and_is_never_better_than_free_trading_on_costs():
    frames = {f"S{i}.NS": series(start=200 + i, step=0.3 + i * 0.01) for i in range(6)}
    free = run(frames)["metrics"]["combined_pl_rs"]
    real = run(frames, **e.REALISTIC_PROFILE)["metrics"]["combined_pl_rs"]
    assert real < free


def test_bad_stop_peak_rejected():
    try:
        run({"A.NS": series()}, stop_peak="High")
    except ValueError as err:
        assert "must be one of" in str(err)
    else:
        raise AssertionError


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} backtest fit-switch tests passed")
