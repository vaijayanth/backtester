"""merge_universe_additions.main(): dedupe repeated requests, never report an ETF as MERGED.
Network (Apps Script sheet, Yahoo) is mocked. Run: python tests/test_merge_universe_additions.py"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import clean_universe
import merge_universe_additions as m


def run_merge(pending, existing_manual=(), real_data=("NEWCO.NS",), raw_lines=("RELIANCE.NS",)):
    """Runs main() in a temp universe; returns (status updates sent to the sheet, manual file lines, clean csv symbols)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        manual = tmp / "manual.csv"
        manual.write_text("symbol\n" + "".join(s + "\n" for s in existing_manual))
        raw = tmp / "raw.txt"
        raw.write_text("\n".join(raw_lines) + "\n")
        out_csv, report = tmp / "clean.csv", tmp / "report.txt"
        out_csv.write_text("symbol\n" + "".join(x + "\n" for x in raw_lines))  # the universe as last generated
        sent = []
        patches = [
            mock.patch.object(m, "fetch_pending", lambda: list(pending)),
            mock.patch.object(m, "symbol_has_real_data", lambda sym: sym in real_data),
            mock.patch.object(m, "update_status", lambda u: sent.extend(u) or len(u)),
            mock.patch.object(m, "MANUAL_ADDITIONS_FILE", manual),
            mock.patch.object(m.time, "sleep", lambda s: None),
            mock.patch.object(clean_universe, "MANUAL_ADDITIONS_FILE", manual),
            mock.patch.object(clean_universe, "RAW_FILE", raw),
            mock.patch.object(clean_universe, "OUT_CSV", out_csv),
            mock.patch.object(clean_universe, "OUT_REPORT", report),
            mock.patch.object(clean_universe, "OUT_DIR", tmp),
        ]
        for p in patches:
            p.start()
        try:
            m.main()
        finally:
            for p in patches:
                p.stop()
        clean = [l.strip() for l in out_csv.read_text().splitlines()[1:]] if out_csv.exists() else []
        return sent, [l.strip() for l in manual.read_text().splitlines()[1:] if l.strip()], clean


def by_status(sent):
    out = {}
    for u in sent:
        out.setdefault(u["status"], []).append(u["symbol"])
    return out


def test_new_valid_symbol_is_merged_once_even_if_requested_twice():
    sent, manual, clean = run_merge(["NEWCO", "NEWCO"])
    assert manual == ["NEWCO.NS"]                      # appended once, not twice
    assert "NEWCO.NS" in clean and by_status(sent) == {"MERGED": ["NEWCO"]}


def test_bare_and_suffixed_request_of_same_symbol_append_once_both_rows_merged():
    sent, manual, _ = run_merge(["NEWCO", "NEWCO.NS"])
    assert manual == ["NEWCO.NS"]
    assert by_status(sent) == {"MERGED": ["NEWCO", "NEWCO.NS"]}


def test_etf_is_rejected_not_reported_as_merged():
    sent, manual, clean = run_merge(["NIFTYBEES"], real_data=("NIFTYBEES.NS",))
    assert manual == [] and "NIFTYBEES.NS" not in clean
    st = by_status(sent)
    assert list(st) == ["REJECTED"]
    assert "ETF" in [u for u in sent][0]["note"]


def test_kept_etf_is_not_rejected_as_etf():
    # NIFTYIETF is in KEEP_ETFS: the 'ETF excluded' rejection must not apply to it
    sent, *_ = run_merge(["NIFTYIETF"], existing_manual=("NIFTYIETF.NS",))
    assert by_status(sent) == {"ALREADY_EXISTS": ["NIFTYIETF"]}


def test_existing_and_bad_symbols_resolved():
    sent, manual, _ = run_merge(["RELIANCE", "TYPO", "NEWCO"], raw_lines=("RELIANCE.NS",))
    st = by_status(sent)
    assert st["ALREADY_EXISTS"] == ["RELIANCE"] and st["REJECTED"] == ["TYPO"] and st["MERGED"] == ["NEWCO"]
    assert manual == ["NEWCO.NS"]


def test_fetch_pending_survives_numeric_and_null_symbols():
    rows = {"success": True, "rows": [{"status": "PENDING", "symbol": 500325}, {"status": "PENDING", "symbol": None},
                                      {"status": "PENDING", "symbol": " reliance "}, {"status": "DONE", "symbol": "X"}]}
    resp = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: rows})()
    with mock.patch.object(m.requests, "get", lambda *a, **k: resp):
        assert m.fetch_pending() == ["500325", "RELIANCE"]


def test_no_pending_is_a_noop():
    sent, manual, _ = run_merge([])
    assert sent == [] and manual == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} merge_universe_additions tests passed")
