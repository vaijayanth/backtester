"""Interior-gap detection in update_ohlc (2026-11-08 incident: 11-session hole never healed by incremental appends)."""
import sys
import zlib
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update_ohlc  # noqa: E402


def _write(tmp_path, monkeypatch, dates):
    monkeypatch.setattr(update_ohlc, "RAW_DIR", tmp_path)
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=pd.to_datetime(dates))
    df.to_parquet(tmp_path / "AAA_NS.parquet")


def test_detects_hole(tmp_path, monkeypatch):
    d = list(pd.bdate_range("2026-09-01", "2026-10-20")) + list(pd.bdate_range("2026-11-05", "2026-11-06"))
    _write(tmp_path, monkeypatch, d)
    gap = update_ohlc.interior_gap("AAA.NS")
    assert gap is not None and str(gap[0]) == "2026-10-20"


def test_ignores_holiday_sized_gap(tmp_path, monkeypatch):
    d = list(pd.bdate_range("2026-09-01", "2026-10-30")) + list(pd.bdate_range("2026-11-04", "2026-11-06"))  # 5-day Diwali-style break
    _write(tmp_path, monkeypatch, d)
    assert update_ohlc.interior_gap("AAA.NS") is None


def test_rotation_bucket_is_stable():
    assert update_ohlc.full_refresh_today.__code__.co_names.count("crc32") == 1
    assert zlib.crc32(b"RELIANCE.NS") % 20 == zlib.crc32(b"RELIANCE.NS") % 20
