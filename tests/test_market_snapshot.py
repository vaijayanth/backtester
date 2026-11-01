"""market_snapshot.build_snapshot: vectorised breadth == the old per-date loop; as-of date = majority.
Run: python tests/test_market_snapshot.py"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import numpy as np
import pandas as pd

import market_snapshot as ms
from indicators import compute_ema


def frame(dates, seed, n=None):
    rng = np.random.default_rng(seed)
    idx = pd.to_datetime(dates)
    close = 100 + np.cumsum(rng.normal(0, 1.5, len(idx)))
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1}, index=idx)


def old_reference(frames):
    """The pre-2026-11-01 implementation (per-date x per-symbol loop) as an oracle."""
    per = {}
    for sym, df in frames.items():
        closes = df["Close"].tolist()
        e50, e200 = compute_ema(closes, 50), compute_ema(closes, 200)
        per[sym] = pd.DataFrame({"above50": [None if e is None else (c > e) for c, e in zip(closes, e50)],
                                 "above200": [None if e is None else (c > e) for c, e in zip(closes, e200)]}, index=df.index)
    dates = sorted(set().union(*[set(d.index) for d in per.values()]))
    out = []
    for date in dates:
        n50 = t50 = n200 = t200 = 0
        for d in per.values():
            if date not in d.index:
                continue
            r = d.loc[date]
            if pd.notna(r["above50"]):
                t50 += 1; n50 += bool(r["above50"])
            if pd.notna(r["above200"]):
                t200 += 1; n200 += bool(r["above200"])
        out.append({"date": str(date.date()), "above_ema50_pct": round(n50 / t50 * 100, 2) if t50 else None,
                    "above_ema200_pct": round(n200 / t200 * 100, 2) if t200 else None})
    return out


def snapshot_of(frames):
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        for sym, df in frames.items():
            df.to_parquet(raw / f"{sym.replace('.', '_')}.parquet")
        with mock.patch.object(ms, "RAW_DIR", raw), mock.patch.object(ms, "load_validated_symbols", lambda: list(frames)):
            return ms.build_snapshot()


def test_breadth_matches_old_loop_on_ragged_universe():
    days = pd.bdate_range("2024-01-01", periods=320)
    frames = {
        "A.NS": frame(days, 1), "B.NS": frame(days, 2),
        "C.NS": frame(days[60:], 3),                       # listed later: NaN warm-up differs
        "D.NS": frame(days[:300], 4),                       # stops early
        "E.NS": frame(days.delete([100, 101, 200]), 5),    # missing days
    }
    snap = snapshot_of(frames)
    assert snap["breadth_trend"] == old_reference(frames)
    assert len(snap["breadth_trend"]) == 320


def test_as_of_is_majority_date_and_laggards_counted():
    days = pd.bdate_range("2025-01-01", periods=260)
    frames = {f"S{i}.NS": frame(days[:-1], i) for i in range(8)}     # 8 symbols end one day early
    frames["NEW.NS"] = frame(days, 99)                               # 1 symbol has the newest day
    snap = snapshot_of(frames)
    assert snap["as_of_date"] == str(days[-2].date())               # majority, not max
    assert snap["newest_date_any_symbol"] == str(days[-1].date())
    assert snap["symbols_behind_as_of"] == 0                         # laggards are relative to majority
    # reverse: majority newest, a few lag
    frames2 = {f"S{i}.NS": frame(days, i) for i in range(8)}
    frames2["OLD.NS"] = frame(days[:-3], 77)
    snap2 = snapshot_of(frames2)
    assert snap2["as_of_date"] == str(days[-1].date()) and snap2["symbols_behind_as_of"] == 1


def test_as_of_tie_prefers_the_newer_date():
    days = pd.bdate_range("2025-01-01", periods=260)
    frames = {f"A{i}.NS": frame(days, i) for i in range(3)}
    frames.update({f"B{i}.NS": frame(days[:-1], 10 + i) for i in range(3)})   # 3 vs 3
    assert snapshot_of(frames)["as_of_date"] == str(days[-1].date())


def test_pulse_counts_are_consistent():
    days = pd.bdate_range("2024-01-01", periods=320)
    snap = snapshot_of({f"S{i}.NS": frame(days, i) for i in range(10)})
    p = snap["pulse"]
    assert p["counted"] == 10 and p["above_both"] <= min(p["above_ema50"], p["above_ema200"])
    assert p["below_both"] + p["above_both"] <= p["counted"]


def test_no_data_raises():
    with mock.patch.object(ms, "load_validated_symbols", lambda: []):
        try:
            ms.build_snapshot()
        except RuntimeError:
            return
    raise AssertionError("expected RuntimeError")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} market snapshot tests passed")
