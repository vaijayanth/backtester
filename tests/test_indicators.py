"""scripts/indicators.py + common.py: hand-computed values (the single definition of each formula).
Run: python tests/test_indicators.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pandas as pd

import common
import indicators as ind


def test_ema_seed_and_recursion():
    out = ind.compute_ema([1, 2, 3, 4, 5, 6], 3)
    assert out[:2] == [None, None] and out[2] == 2.0       # SMA seed
    assert out[3] == 3.0 and out[4] == 4.0 and out[5] == 5.0  # mult 0.5: (4-2)*.5+2 ...


def test_ema_short_input_is_all_none():
    assert ind.compute_ema([1, 2], 3) == [None, None]


def test_rsi_needs_15_bars_and_saturates():
    assert ind.compute_rsi14(list(range(14))) == [None] * 14
    rising = ind.compute_rsi14([float(i) for i in range(30)])
    assert rising[14] == 100.0 and rising[-1] == 100.0
    falling = ind.compute_rsi14([float(30 - i) for i in range(30)])
    assert falling[14] == 0.0 and abs(falling[-1]) < 1e-9


def test_rsi_wilder_smoothing_value():
    # 14 alternating +1/-1 moves => avg_gain == avg_loss => RSI 50 at the seed bar
    closes = [10.0]
    for i in range(14):
        closes.append(closes[-1] + (1 if i % 2 == 0 else -1))
    assert abs(ind.compute_rsi14(closes)[14] - 50.0) < 1e-9


def test_atr_constant_range():
    h, l, c = [11.0] * 12, [9.0] * 12, [10.0] * 12
    atr = ind.compute_atr(h, l, c, 10)
    assert atr[8] is None and atr[9] == 2.0 and atr[11] == 2.0


def test_supertrend_flips_up_then_down():
    n = 40
    base = [100.0] * 15 + [100 + 4 * i for i in range(1, 15)] + [160 - 6 * i for i in range(1, 12)]
    h = [b + 1 for b in base]
    l = [b - 1 for b in base]
    direction, upper, lower = ind.compute_supertrend(h, l, base)
    assert direction[:9] == [None] * 9 and direction[9] in ("up", "down")
    assert "up" in direction and direction[-1] == "down"
    assert upper[20] is not None and lower[20] is not None


def test_common_loaders_read_manifest_by_status():
    with tempfile.TemporaryDirectory() as d:
        m = Path(d) / "manifest.csv"
        pd.DataFrame({"symbol": ["A.NS", "B.NS", "C.NS"], "status": ["VALIDATED", "PARTIAL", "VALIDATED"]}).to_csv(m, index=False)
        assert common.load_validated_symbols(m) == ["A.NS", "C.NS"]
        assert common.load_validated_set(m) == {"A.NS", "C.NS"}
        assert common.load_partial_set(m) == {"B.NS"}
        assert common.load_validated_symbols(Path(d) / "missing.csv") == []
        assert common.load_fno_symbols(Path(d) / "missing.csv") == []


def test_paths_resolve_from_repo_root():
    assert common.RAW_DIR == common.ROOT / "checkpoints" / "raw"
    assert (common.ROOT / "scripts" / "common.py").exists()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} indicator/common tests passed")
