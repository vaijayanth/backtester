"""Backtest engines: gap-aware fills and unbiased same-day priority (fixed 2026-11-01).
Run: python tests/test_backtest_engines.py
"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import numpy as np
import pandas as pd

import backtest_engine as e
import backtest_252dh_supertrend as d252


def series(n=260, start=100.0, step=0.3):
    idx = pd.bdate_range("2025-01-01", periods=n)
    c = start + np.arange(n) * step
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 100}, index=idx)


def run_sst(frames, **over):
    with mock.patch.object(e, "load_universe_data", lambda symbols=None: frames):
        return e.run_backtest({"initial_capital_rs": 1e7, **over})


def first_entry_index(df):
    r = run_sst({"A.NS": df})
    return df.index.get_loc(pd.Timestamp(r["open_positions"][0]["entry_date"]))


# ---------------- fills ----------------
def test_fill_helpers():
    gap, legacy = {"gap_aware_fills": True}, {"gap_aware_fills": False}
    assert e.buy_stop_fill(110, 100, gap) == 110 and e.buy_stop_fill(95, 100, gap) == 100
    assert e.buy_stop_fill(110, 100, legacy) == 100
    assert e.sell_stop_fill(80, 90, gap) == 80 and e.sell_stop_fill(95, 90, gap) == 90
    assert e.sell_stop_fill(80, 90, legacy) == 90


def test_gap_up_entry_fills_at_open_not_at_unreachable_level():
    df = series()
    i0 = first_entry_index(df)
    rh = df["High"].iloc[i0 - 21:i0].max()
    df.iloc[i0, df.columns.get_loc("Open")] = rh + 10       # opens 10 above the breakout level
    df.iloc[i0, df.columns.get_loc("High")] = rh + 12
    df.iloc[i0, df.columns.get_loc("Close")] = rh + 11
    new = run_sst({"A.NS": df})["open_positions"][0]["avg_entry_price"]
    old = run_sst({"A.NS": df}, gap_aware_fills=False)["open_positions"][0]["avg_entry_price"]
    assert abs(new - (rh + 10)) < 1e-9, (new, rh)
    assert abs(old - rh) < 1e-9, (old, rh)


def test_non_gap_entry_still_fills_at_breakout_level():
    df = series()
    i0 = first_entry_index(df)
    rh = df["High"].iloc[i0 - 21:i0].max()
    assert df["Open"].iloc[i0] <= rh  # ordinary day
    assert abs(run_sst({"A.NS": df})["open_positions"][0]["avg_entry_price"] - rh) < 1e-9


def test_gap_down_stop_exits_at_open_not_at_stop():
    df = series()
    i0 = first_entry_index(df)
    entry = run_sst({"A.NS": df})["open_positions"][0]["avg_entry_price"]
    c = df.columns.get_loc
    # day after entry: +10% peak arms the 7% profit lock, but Low stays above the stop
    df.iloc[i0 + 1, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 1.05, entry * 1.10, entry * 1.05, entry * 1.09]
    stop = entry * 1.10 * 0.94
    # next day gaps far below the stop
    df.iloc[i0 + 2, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 0.70, entry * 0.71, entry * 0.69, entry * 0.70]
    df = df.iloc[: i0 + 3]
    new = [t for t in run_sst({"A.NS": df})["trades"]][0]
    old = [t for t in run_sst({"A.NS": df}, gap_aware_fills=False)["trades"]][0]
    assert abs(old["exit_price"] - round(stop, 2)) < 0.011
    assert abs(new["exit_price"] - round(entry * 0.70, 2)) < 0.011
    assert new["pl_rs"] < old["pl_rs"]  # the realistic fill is worse than the unreachable one


# ---------------- intrabar order ----------------
def _armed_same_day_scenario(order):
    df = series()
    i0 = first_entry_index(df)
    entry = run_sst({"A.NS": df})["open_positions"][0]["avg_entry_price"]
    c = df.columns.get_loc
    # day 1: High +12% arms the 7% lock; Low dips below the freshly-armed stop (peak*0.94 = 1.053*entry)
    df.iloc[i0 + 1, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 1.02, entry * 1.12, entry * 0.99, entry * 1.08]
    # day 2: calm, stays above the stop
    df.iloc[i0 + 2, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 1.09, entry * 1.10, entry * 1.08, entry * 1.09]
    df = df.iloc[: i0 + 3]
    return run_sst({"A.NS": df}, intrabar_order=order), entry


def test_high_first_can_arm_and_hit_stop_on_the_same_day():
    r, entry = _armed_same_day_scenario("high_first")
    assert len(r["trades"]) == 1
    assert abs(r["trades"][0]["exit_price"] - round(entry * 1.02, 2)) < 0.011   # gap-aware: fills at Open (below stop)


def test_low_first_cannot_hit_a_stop_that_did_not_exist_yet():
    r, entry = _armed_same_day_scenario("low_first")
    assert r["trades"] == [] and len(r["open_positions"]) == 1


def test_low_first_exits_on_a_stop_set_at_a_prior_close():
    df = series()
    i0 = first_entry_index(df)
    entry = run_sst({"A.NS": df})["open_positions"][0]["avg_entry_price"]
    c = df.columns.get_loc
    df.iloc[i0 + 1, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 1.08, entry * 1.12, entry * 1.07, entry * 1.10]
    df.iloc[i0 + 2, [c("Open"), c("High"), c("Low"), c("Close")]] = [entry * 1.06, entry * 1.07, entry * 1.00, entry * 1.01]
    df = df.iloc[: i0 + 3]
    for order in ("high_first", "low_first"):
        t = run_sst({"A.NS": df}, intrabar_order=order)["trades"]
        assert len(t) == 1, order
        assert abs(t[0]["exit_price"] - round(entry * 1.12 * 0.94, 2)) < 0.011, order   # stop = peak*0.94 from day 1


def test_default_intrabar_order_is_legacy_high_first():
    assert e.DEFAULT_PARAMS["intrabar_order"] == "high_first"


# ---------------- priority ----------------
def test_symbol_priority_is_alphabetical_legacy():
    frames = {f"{ch}.NS": series() for ch in "ABCDEF"}
    r = run_sst(frames, max_open_positions=1, entry_priority="symbol")
    assert [p["symbol"] for p in r["open_positions"]] == ["A.NS"]


def test_random_priority_is_reproducible_and_not_always_first():
    frames = {f"{ch}.NS": series() for ch in "ABCDEF"}
    pick = lambda seed: run_sst(frames, max_open_positions=1, random_seed=seed)["open_positions"][0]["symbol"]
    assert pick(3) == pick(3)
    picks = {pick(s) for s in range(12)}
    assert len(picks) >= 3 and picks != {"A.NS"}, picks


def test_entry_order_is_a_permutation_and_varies_by_date():
    syms = [f"S{i}" for i in range(50)]
    p = {"entry_priority": "random", "random_seed": 0}
    a = e.entry_order(syms, pd.Timestamp("2026-01-05"), p)
    b = e.entry_order(syms, pd.Timestamp("2026-01-06"), p)
    assert sorted(a) == sorted(syms) and a != syms and a != b
    assert e.entry_order(syms, pd.Timestamp("2026-01-05"), {"entry_priority": "symbol"}) == syms


def test_entry_order_is_unbiased_over_many_dates():
    syms = list("ABCD")
    firsts = [e.entry_order(syms, d, {"random_seed": 0})[0] for d in pd.bdate_range("2024-01-01", periods=400)]
    for s in syms:
        assert 60 < firsts.count(s) < 140, (s, firsts.count(s))  # ~100 each


def test_252dh_symbol_order():
    syms = list("ABCD")
    day = pd.Timestamp("2026-03-02")
    assert d252.symbol_order(syms, day, "symbol") == syms
    assert d252.symbol_order(syms, day, "random", 7) == d252.symbol_order(syms, day, "random", 7)
    firsts = [d252.symbol_order(syms, x, "random", 0)[0] for x in pd.bdate_range("2024-01-01", periods=400)]
    assert all(40 < firsts.count(s) < 160 for s in syms)


def test_252dh_sells_are_served_before_buys():
    syms = list("ABCDEF")
    day = pd.Timestamp("2026-03-02")
    for seed in range(20):
        order = d252.symbol_order(syms, day, "random", seed, first=["E", "C"])
        assert set(order[:2]) == {"E", "C"} and sorted(order) == syms
    assert d252.symbol_order(syms, day, "symbol", first=["E", "C"]) == syms  # legacy mode is exact manifest order


def test_252dh_cash_goes_to_random_not_first_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw"
        raw.mkdir()
        rows = []
        for ch in "ABCDEF":
            series(300, step=2.0).to_parquet(raw / f"{ch}_NS.parquet")
            rows.append({"symbol": f"{ch}.NS", "status": "VALIDATED"})
        man = Path(tmp) / "manifest.csv"
        pd.DataFrame(rows).to_csv(man, index=False)

        def first_buy(priority, seed=0):
            r = d252.run_backtest(initial_capital_rs=700, position_size_rs=1000, raw_dir=raw, manifest_csv=man,
                                  entry_priority=priority, random_seed=seed)
            return sorted(p["symbol"] for p in r["open_positions"])

        assert first_buy("symbol") == ["A.NS"]
        assert first_buy("random", 4) == first_buy("random", 4)
        assert len({tuple(first_buy("random", s)) for s in range(10)}) >= 3


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} backtest engine tests passed")
