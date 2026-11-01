"""fetch_equity_names.py / fetch_equity_sectors.py: a transient Yahoo failure must not be
cached as 'no name' / 'no sector' forever, and .BO listings must be queried correctly.
Run: python tests/test_equity_lookups.py
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import fetch_equity_names as n
import fetch_equity_sectors as sec


class FakeYF:
    """yf.Ticker stand-in: behaviour keyed by the full ticker string."""
    def __init__(self, table):
        self.table, self.calls = table, []

    def Ticker(self, t):
        self.calls.append(t)
        outcome = self.table.get(t, {})
        fake = type("T", (), {})()
        def get_info():
            if outcome == "raise":
                raise RuntimeError("429 Too Many Requests")
            return outcome
        fake.get_info = get_info
        return fake


def fake(mod, table):
    """Patch `mod.yf` for the duration of a with-block (restored afterwards)."""
    return mock.patch.object(mod, "yf", FakeYF(table))


def test_split_symbol_handles_both_suffixes():
    assert n.split_symbol("NSDL.BO") == ("NSDL", ".BO")
    assert n.split_symbol("RELIANCE.NS") == ("RELIANCE", ".NS")
    assert n.split_symbol("PLAIN") == ("PLAIN", ".NS")


def test_bo_listing_is_queried_on_bo_first_with_bare_symbol():
    f = FakeYF({"NSDL.BO": {"longName": "NSDL Ltd"}})
    with mock.patch.object(n, "yf", f):
        assert n.fetch_name("NSDL", ".BO") == ("NSDL Ltd", True)
    assert f.calls == ["NSDL.BO"]  # never 'NSDL.BO.NS'


def test_transient_failure_is_not_definitive():
    with fake(n, {"X.NS": "raise", "X.BO": "raise"}):
        assert n.fetch_name("X") == (None, False)


def test_genuinely_empty_answer_is_definitive():
    with fake(n, {"X.NS": {}, "X.BO": {}}):
        assert n.fetch_name("X") == (None, True)


def test_one_exchange_failing_but_other_answering_empty_is_still_not_definitive():
    with fake(n, {"X.NS": "raise", "X.BO": {}}):
        assert n.fetch_name("X") == (None, False)


def test_found_name_wins_even_after_an_error():
    with fake(n, {"X.NS": "raise", "X.BO": {"shortName": "X Co"}}):
        assert n.fetch_name("X") == ("X Co", True)


def test_sector_transient_vs_definitive():
    with fake(sec, {"A.NS": "raise", "A.BO": "raise", "B.NS": {}, "B.BO": {}, "C.NS": {"sector": "Energy"}}):
        assert sec.fetch_sector("A") == (None, False)
        assert sec.fetch_sector("B") == (None, True)
        assert sec.fetch_sector("C") == ("Energy", True)


def test_committed_names_file_has_no_suffixed_keys():
    import json
    names = json.loads((Path(__file__).parent.parent / "data" / "equity_names.json").read_text())
    assert [k for k in names if "." in k] == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} equity lookup tests passed")
