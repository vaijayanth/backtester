"""backtest_sensitivity.py: direction-aware worst/median/best and a deterministic grid.
Run: python tests/test_backtest_sensitivity.py"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import numpy as np
import pandas as pd

import backtest_engine as e
import backtest_sensitivity as s


def test_summarise_higher_is_better():
    r = s.summarise([50, 70, 60], True)
    assert (r["worst"], r["median"], r["best"], r["n"]) == (50, 60, 70, 3)


def test_summarise_drawdown_worst_is_the_largest():
    r = s.summarise([8.0, 16.0, 12.0], False)
    assert (r["worst"], r["median"], r["best"]) == (16.0, 12.0, 8.0)


def test_summarise_ignores_none_and_handles_all_none():
    assert s.summarise([None, 2.0, None, 4.0], True)["median"] == 3.0
    assert s.summarise([None, None], True) == {"worst": None, "median": None, "best": None, "n": 0}


def test_aggregate_uses_metric_direction():
    runs = [{"metrics": {"win_rate_pct": 80, "profit_factor": None, "max_drawdown_pct": 16, "combined_pl_rs": 100, "total_closed_trades": 5}},
            {"metrics": {"win_rate_pct": 90, "profit_factor": 3.0, "max_drawdown_pct": 7, "combined_pl_rs": 300, "total_closed_trades": 7}}]
    a = s.aggregate(runs)
    assert a["win_rate_pct"]["worst"] == 80 and a["max_drawdown_pct"]["worst"] == 16 and a["max_drawdown_pct"]["best"] == 7
    assert a["profit_factor"]["n"] == 1 and a["combined_pl_rs"]["median"] == 200


def _frames():
    out = {}
    for i in range(6):
        idx = pd.bdate_range("2025-01-01", periods=260)
        c = 100 + np.arange(260) * 0.3 + i
        out[f"S{i}.NS"] = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1}, index=idx)
    return out


def run(seeds=2):
    frames = _frames()
    with mock.patch.object(e, "load_universe_data", lambda symbols=None: frames):
        return s.run_grid({"max_open_positions": 2, "initial_capital_rs": 1e7}, seeds)


def test_grid_runs_every_combination_and_restores_loader():
    original = e.load_universe_data
    runs, through, universe = run(2)
    assert [(r["intrabar_order"], r["random_seed"]) for r in runs] == [("high_first", 0), ("high_first", 1), ("low_first", 0), ("low_first", 1)]
    assert through is not None and universe == 6
    assert e.load_universe_data is original


def test_grid_is_deterministic():
    a, _, _ = run(2)
    b, _, _ = run(2)
    assert a == b


def test_summary_shape_and_forced_params_stripped():
    runs, through, universe = run(2)
    summ = s.build_summary(runs, through, universe, {"max_open_positions": 2}, 2)
    assert summ["grid"]["runs"] == 4 and summ["grid"]["gap_aware_fills"] is True
    assert set(summ["range"]) == {"win_rate_pct", "profit_factor", "max_drawdown_pct", "combined_pl_rs", "total_closed_trades"}
    assert set(summ["by_intrabar_order"]) == {"high_first", "low_first"}
    json.dumps(summ, default=float)   # serialisable


def test_main_writes_atomically_and_ignores_forced_params():
    frames = _frames()
    with tempfile.TemporaryDirectory() as tmp:
        params = Path(tmp) / "p.json"
        params.write_text(json.dumps({"gap_aware_fills": False, "intrabar_order": "low_first", "max_open_positions": 3, "initial_capital_rs": 1e7}))
        out = Path(tmp) / "r" / "sens.json"
        with mock.patch.object(e, "load_universe_data", lambda symbols=None: frames), \
                mock.patch.object(sys, "argv", ["x", "--params", str(params), "--seeds", "1", "--out", str(out)]):
            s.main()
        data = json.loads(out.read_text())
        assert data["grid"]["gap_aware_fills"] is True and "intrabar_order" not in data["base_params_overrides"]
        assert data["base_params_overrides"]["max_open_positions"] == 3 and not list(out.parent.glob("*.tmp"))


def _main_with(args):
    with mock.patch.object(sys, "argv", ["x"] + args):
        try:
            s.main()
        except SystemExit as err:
            return err.code
    return 0


def test_bad_inputs_fail_cleanly():
    with tempfile.TemporaryDirectory() as tmp:
        arr, bad, unk = Path(tmp) / "a.json", Path(tmp) / "b.json", Path(tmp) / "u.json"
        arr.write_text("[1]"); bad.write_text("{nope"); unk.write_text('{"bogus_key": 1}')
        for argv in (["--params", str(arr)], ["--params", str(bad)], ["--params", str(Path(tmp) / "missing.json")],
                     ["--params", str(unk)], ["--seeds", "0"], ["--seeds", "21"]):
            assert _main_with(argv) == 2, argv


def test_medians_are_rounded():
    assert s.summarise([12.26, 12.26, 12.26000000000001], False)["median"] == 12.26


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} backtest sensitivity tests passed")
