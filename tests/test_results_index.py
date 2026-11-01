"""update_results_index.py: re-running for the same results file must not duplicate the entry.
Run: python tests/test_results_index.py"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import update_results_index as u


def results(tag="2026-11-01_run1", wr=55.0):
    return {"run_at": "2026-11-01T00:00:00Z", "universe_size": 1495,
            "metrics": {"win_rate_pct": wr, "profit_factor": 1.9, "max_drawdown_pct": 12.0, "total_closed_trades": 10,
                        "open_positions_count": 2, "combined_pl_rs": 1000.0}}


def run_cli(tmp, name, res):
    rp = Path(tmp) / name
    rp.write_text(json.dumps(res))
    idx = Path(tmp) / "results" / "index.json"
    with mock.patch.object(u, "INDEX_PATH", idx), mock.patch.object(sys, "argv", ["x", str(rp)]):
        u.main()
    return json.loads(idx.read_text())


def test_new_entry_appended():
    with tempfile.TemporaryDirectory() as tmp:
        idx = run_cli(tmp, "2026-11-01_runA.json", results())
        assert [e["filename"] for e in idx] == ["2026-11-01_runA.json"] and idx[0]["run_label"] == "runA"


def test_rerun_same_file_upserts_not_duplicates_and_keeps_order():
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(tmp, "2026-11-01_a.json", results(wr=50.0))
        run_cli(tmp, "2026-11-01_b.json", results(wr=60.0))
        idx = run_cli(tmp, "2026-11-01_a.json", results(wr=77.0))   # retry of the first run
        assert [e["filename"] for e in idx] == ["2026-11-01_a.json", "2026-11-01_b.json"]
        assert idx[0]["win_rate_pct"] == 77.0


def test_non_dict_items_in_index_do_not_crash_upsert():
    idx = u.upsert_entry([1, None, {"filename": "a.json", "x": 1}], {"filename": "a.json", "x": 2})
    assert idx == [1, None, {"filename": "a.json", "x": 2}]


def test_corrupt_index_is_not_overwritten():
    with tempfile.TemporaryDirectory() as tmp:
        idx = Path(tmp) / "results" / "index.json"
        idx.parent.mkdir()
        idx.write_text("{not json")
        try:
            u.load_index(idx)
        except SystemExit:
            assert idx.read_text() == "{not json"
            return
        raise AssertionError("expected SystemExit")


def test_non_list_index_rejected_and_missing_ok():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "i.json"
        assert u.load_index(p) == []
        p.write_text("{}")
        try:
            u.load_index(p)
        except SystemExit:
            return
        raise AssertionError("expected SystemExit")


def test_atomic_write_leaves_no_temp_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "results" / "index.json"
        u.write_atomic(p, [{"a": 1}])
        assert json.loads(p.read_text()) == [{"a": 1}] and not list(p.parent.glob("*.tmp"))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} results index tests passed")
