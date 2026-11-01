"""strangle_screen.monthly_returns: the month still in progress must not be scored.
Run: python tests/test_strangle_screen.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import numpy as np
import pandas as pd

import strangle_screen as s


def bars(start, periods, open0=100.0, step=0.5):
    idx = pd.bdate_range(start, periods=periods)
    close = open0 + np.arange(periods) * step
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 100}, index=idx)


def test_in_progress_month_is_excluded():
    df = bars("2026-09-01", 45)  # Sep through ~Nov 2
    months = [m for m, *_ in s.monthly_returns(df, today=date(2026, 11, 1))]
    assert months == ["2026-09", "2026-10"]
    assert "2026-11" not in [m for m, *_ in s.monthly_returns(df, today=date(2026, 11, 2))]


def test_month_appears_once_calendar_rolls_over():
    df = bars("2026-09-01", 45)
    assert "2026-10" not in [m for m, *_ in s.monthly_returns(df, today=date(2026, 10, 31))]
    assert "2026-10" in [m for m, *_ in s.monthly_returns(df, today=date(2026, 11, 1))]


def test_partial_month_no_longer_scored_as_win():
    # a month that is mid-way with a tiny move would have been a "Win"; now absent
    df = bars("2026-10-01", 3)
    assert s.monthly_returns(df, today=date(2026, 10, 5)) == []


def test_completed_month_return_unchanged():
    df = bars("2026-09-01", 45)
    sep = dict((m, r) for m, r, _ in s.monthly_returns(df, today=date(2026, 11, 1)))["2026-09"]
    g = df[df.index.strftime("%Y-%m") == "2026-09"]
    assert abs(sep - round((g["Close"].iloc[-1] - g["Open"].iloc[0]) / g["Open"].iloc[0] * 100, 2)) < 1e-9


def test_month_not_scored_when_data_stops_before_month_end():
    # calendar has passed October (today = Nov 1) but this symbol's last bar is Oct 29;
    # Oct 30 (a Friday) is missing, so October is incomplete and must not be scored
    df = bars("2026-09-01", 43)
    assert df.index[-1] == pd.Timestamp("2026-10-29")
    months = [m for m, *_ in s.monthly_returns(df, today=date(2026, 11, 1))]
    assert months == ["2026-09"]


def test_month_scored_when_last_bar_is_last_weekday():
    df = bars("2026-09-01", 44)  # through Fri Oct 30
    assert df.index[-1] == pd.Timestamp("2026-10-30")
    months = [m for m, *_ in s.monthly_returns(df, today=date(2026, 11, 1))]
    assert months == ["2026-09", "2026-10"]


def test_month_covered_by_later_bars_even_if_last_weekday_was_a_holiday():
    assert s.month_covered("2026-10", ["2026-10", "2026-11"], pd.Timestamp("2026-11-03"))
    assert not s.month_covered("2026-10", ["2026-10"], pd.Timestamp("2026-10-29"))


def test_current_month_key_uses_ist():
    assert s.current_month_key(date(2026, 12, 31)) == "2026-12"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} strangle screen tests passed")
