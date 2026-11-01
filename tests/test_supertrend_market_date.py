"""supertrend.py: latest_market_date comes from a reference basket (a stray symbol can't redefine it),
trading-day counts, stale flags, and the clock-based feed lag. Run: python tests/test_supertrend_market_date.py"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import numpy as np
import pandas as pd

import supertrend as st


# ---------- pure helpers ----------
def test_pick_prefers_basket_over_stray_symbol():
    counts = {"2026-10-29": 1490, "2026-10-30": 1}
    assert st.pick_latest_market_date(counts, ["2026-10-29"] * 6, "2026-11-01") == "2026-10-29"


def test_pick_uses_newest_date_two_basket_members_share():
    basket = ["2026-10-30", "2026-10-30", "2026-10-29", "2026-10-29"]
    assert st.pick_latest_market_date({}, basket, "2026-11-01") == "2026-10-30"


def test_pick_ignores_future_dates_and_falls_back_without_basket():
    assert st.pick_latest_market_date({"2026-10-29": 5, "2030-01-01": 2}, [], "2026-11-01") == "2026-10-29"
    assert st.pick_latest_market_date({}, [], "2026-11-01") is None
    assert st.pick_latest_market_date({}, ["2030-01-01", "2030-01-01"], "2026-11-01") is None


def test_weekdays_between():
    assert st.weekdays_between("2026-10-29", "2026-10-30") == 1
    assert st.weekdays_between("2026-10-30", "2026-11-02") == 1     # Fri -> Mon skips the weekend
    assert st.weekdays_between("2026-10-30", "2026-10-30") == 0
    assert st.weekdays_between(None, "2026-10-30") is None


def test_expected_last_session_by_clock():
    utc = lambda y, m, d, h: datetime(y, m, d, h, 0, tzinfo=timezone.utc) - timedelta(hours=5, minutes=30)  # given as IST hour
    assert st.expected_last_session(utc(2026, 11, 1, 12)) == "2026-10-30"   # Sunday -> Friday
    assert st.expected_last_session(utc(2026, 11, 2, 12)) == "2026-10-30"   # Monday noon -> Friday
    assert st.expected_last_session(utc(2026, 11, 2, 22)) == "2026-11-02"   # Monday night -> Monday
    assert st.expected_last_session(utc(2026, 11, 3, 10)) == "2026-11-02"   # Tuesday morning -> Monday


# ---------- end to end on a tiny synthetic universe ----------
def bars(end, n=60, seed=0):
    idx = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1}, index=idx)


def build(ends):
    """ends: {symbol: last bar date}. Returns build_supertrend output."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        for i, (sym, end) in enumerate(ends.items()):
            bars(end, seed=i).to_parquet(raw / f"{sym.replace('.', '_')}.parquet")
        with mock.patch.object(st, "RAW_DIR", raw), mock.patch.object(st, "load_validated_set", lambda: set(ends)), \
                mock.patch.object(st, "load_partial_set", lambda: set()):
            return st.build_supertrend(universe="full")


def last_weekday_before_today(back=0):
    d = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return pd.Timestamp(d) - pd.offsets.BDay(back)


def entry(out, sym):
    return next(e for e in out["entries"] if e["symbol"] == sym)


def test_stray_symbol_does_not_mark_everyone_stale():
    d0 = last_weekday_before_today(2)
    ends = {s: d0 for s in ("ICICIBANK.NS", "SBIN.NS", "RELIANCE.NS", "AAA.NS", "BBB.NS")}
    ends["STRAY.NS"] = d0 + pd.offsets.BDay(1)           # one symbol a day ahead
    out = build(ends)
    assert out["latest_market_date"] == str(d0.date())
    assert out["stale_symbols"] == 0
    assert entry(out, "STRAY.NS")["stale_data"] is False


def test_symbols_behind_the_basket_are_flagged_with_trading_day_lag():
    d0 = last_weekday_before_today(1)
    ends = {s: d0 for s in ("ICICIBANK.NS", "SBIN.NS", "RELIANCE.NS")}
    ends["LAG1.NS"] = d0 - pd.offsets.BDay(1)
    ends["LAG3.NS"] = d0 - pd.offsets.BDay(3)
    out = build(ends)
    assert entry(out, "LAG1.NS")["data_lag_trading_days"] == 1 and entry(out, "LAG1.NS")["stale_data"]
    assert entry(out, "LAG3.NS")["data_lag_trading_days"] == 3
    assert entry(out, "SBIN.NS")["data_lag_trading_days"] == 0 and out["stale_symbols"] == 2


def test_whole_feed_behind_the_clock_is_reported():
    d0 = last_weekday_before_today(4)                      # everyone ~4 sessions old: nobody lags anyone
    ends = {s: d0 for s in ("ICICIBANK.NS", "SBIN.NS", "AAA.NS")}
    out = build(ends)
    assert out["stale_symbols"] == 0                       # relative staleness can't see it ...
    assert out["feed_lag_vs_clock_weekdays"] >= 3          # ... the clock check does
    assert out["expected_last_session"] >= out["latest_market_date"]


def test_old_fields_and_trading_day_fields_present():
    d0 = last_weekday_before_today(1)
    out = build({"ICICIBANK.NS": d0, "SBIN.NS": d0, "AAA.NS": d0})
    e = entry(out, "AAA.NS")
    for k in ("recent_flip_days_ago", "recent_bullish_flip_days_ago", "current_streak_days",
              "recent_flip_trading_days_ago", "current_streak_trading_days", "data_lag_trading_days", "stale_data"):
        assert k in e, k


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} supertrend market-date tests passed")
