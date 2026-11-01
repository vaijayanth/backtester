"""Regression tests for the never-regress OHLC guards (scripts/backfill.py).

Incident 2026-10-30: a re-fetch returned a null latest row that replaced the
valid stored bar, then the null-edge trim deleted it (1,246 -> 1,245 rows,
last_date moved BACK a day, for ~1,469 symbols). Run: python tests/test_ohlc_guards.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import numpy as np
import pandas as pd

import backfill as b


def frame(dates, closes, null_last=False):
    df = pd.DataFrame(
        {"Open": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes],
         "Close": closes, "Volume": [100] * len(closes)},
        index=pd.to_datetime(dates),
    )
    if null_last:
        df.iloc[-1, :4] = np.nan
    return df


def reset():
    for k in b.GUARD_STATS:
        b.GUARD_STATS[k] = 0


D = ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
STORED = frame(D, [10, 11, 12, 13])


def test_null_fetched_row_does_not_replace_valid_stored_row():
    reset()
    m = b.merge_preserving_valid(STORED, frame(["2026-10-29", "2026-10-30"], [12, 0], null_last=True))
    assert len(m) == 4 and m.loc["2026-10-30", "Close"] == 13
    assert b.GUARD_STATS["protected_valid_rows"] == 1


def test_trailing_null_for_new_date_is_benign_not_an_alarm():
    reset()
    m = b.merge_preserving_valid(STORED, frame(["2026-11-02"], [0], null_last=True))
    assert m.index.max() == pd.Timestamp("2026-10-30")
    assert b.GUARD_STATS["null_rows_ignored_benign"] == 1 and b.GUARD_STATS["protected_valid_rows"] == 0


def test_interior_invalid_row_is_counted():
    reset()
    new = frame(["2026-11-02", "2026-11-03", "2026-11-04"], [14, 15, 16])
    new.iloc[1, :4] = np.nan
    b.merge_preserving_valid(STORED, new)
    assert b.GUARD_STATS["interior_invalid_dropped"] == 1


def test_valid_fetched_row_still_revises_and_appends():
    reset()
    m = b.merge_preserving_valid(STORED, frame(["2026-10-30", "2026-11-02"], [13.5, 14]))
    assert m.loc["2026-10-30", "Close"] == 13.5 and m.index.max() == pd.Timestamp("2026-11-02")


def test_carry_forward_restores_tail_when_fetch_ends_early():
    reset()
    out = b.carry_forward_newer(frame(D[:3], [10, 11, 12]), STORED)
    assert out.index.max() == pd.Timestamp("2026-10-30") and len(out) == 4
    assert b.GUARD_STATS["stored_rows_restored"] == 1


def test_carry_forward_skips_when_price_basis_differs():
    reset()  # split-style re-adjustment: fetched closes halved vs stored
    out = b.carry_forward_newer(frame(D[:3], [5, 5.5, 6]), STORED)
    assert out.index.max() == pd.Timestamp("2026-10-29")
    assert b.GUARD_STATS["carry_skipped_basis_mismatch"] == 1


def test_carry_forward_never_restores_invalid_or_future_rows():
    reset()
    bad = frame(D, [10, 11, 12, 13], null_last=True)
    assert b.carry_forward_newer(frame(D[:3], [10, 11, 12]), bad).index.max() == pd.Timestamp("2026-10-29")
    future = frame(D + ["2099-01-01"], [10, 11, 12, 13, 14])
    assert b.carry_forward_newer(frame(D[:3], [10, 11, 12]), future).index.max() == pd.Timestamp("2026-10-30")


def test_tz_aware_fetch_merges_with_naive_stored():
    reset()
    tz = frame(["2026-10-29", "2026-10-30"], [12, 13])
    tz.index = tz.index.tz_localize("Asia/Kolkata")
    assert len(b.merge_preserving_valid(STORED, tz)) == 4


def test_save_series_never_shortens_file():
    reset()
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "X_NS.parquet"
        STORED.to_parquet(path)
        out = b.save_series(path, frame(D[:3], [10, 11, 12]))
        assert len(pd.read_parquet(path)) == 4 and len(out) == 4


def test_high_below_low_is_invalid():
    x = frame(["2026-10-30"], [13])
    x.loc[:, "High"] = 5
    assert not b.valid_ohlc_rows(x).any()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} OHLC guard tests passed")
