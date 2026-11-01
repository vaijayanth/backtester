"""clean_universe.py: regenerating the universe must never drop what the app reads.

Incident (found 2026-11-01): merge_universe_additions.py calls clean_universe.main()
whenever a stock is added from the UI; it stripped GOLDBEES / NIFTYIETF / MIDCAPETF /
SMALLCAP, which update_ohlc.py then stopped refreshing (IVP index proxies, ICICI Dividend).
Run: python tests/test_clean_universe.py   (or pytest tests/)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import clean_universe as c


def _committed():
    lines = (ROOT / "data" / "universe_clean.csv").read_text().splitlines()[1:]
    return [x.strip() for x in lines if x.strip()]


def _regenerated():
    raw = [l.strip() for l in (ROOT / "scripts" / "raw_stocklist.txt").read_text().splitlines() if l.strip()]
    return c.build_clean(raw, c.load_manual_additions())


def test_regeneration_keeps_every_committed_symbol():
    clean = set(_regenerated()[0])
    missing = sorted(set(_committed()) - clean)
    assert not missing, f"regenerating universe_clean.csv would drop: {missing}"


def test_regeneration_adds_nothing_unexpected():
    extra = sorted(set(_regenerated()[0]) - set(_committed()))
    assert not extra, f"regeneration would add symbols not in the committed file: {extra}"


def test_app_dependent_etfs_are_kept():
    clean = set(_regenerated()[0])
    for sym in ("GOLDBEES.NS", "NIFTYIETF.NS", "MIDCAPETF.NS", "SMALLCAP.NS"):
        assert sym in clean, sym


def _app_symbols():
    """Symbols index.html actually reads: ICICI Dividend universe + IVP index proxies."""
    import re
    html = (ROOT / "index.html").read_text()
    block = re.search(r"const DIV_UNIVERSE = \[(.*?)\];", html, re.S).group(1)
    syms = set(re.findall(r"'([A-Z0-9&-]+)'", block))
    syms |= set(re.findall(r"ivpRenderIndexProxyStat\('[^']+',\s*'([A-Z0-9]+)'", html))
    return syms


def test_every_symbol_the_app_reads_survives_regeneration():
    bare = {c.strip_suffix(t) for t in _regenerated()[0]}
    missing = sorted(_app_symbols() - bare)
    assert not missing, f"index.html reads these but regenerated universe_clean.csv would drop them: {missing}"
    assert len(_app_symbols()) >= 30  # parsing sanity: 29 dividend names + index proxies


def test_other_etfs_still_removed():
    clean, removed_etf, _, _ = c.build_clean(["BANKBEES.NS", "ITBEES.NS", "RELIANCE.NS", "GOLDBEES.NS"], [])
    assert clean == ["RELIANCE.NS", "GOLDBEES.NS"] and removed_etf == ["BANKBEES.NS", "ITBEES.NS"]


def test_dedupe_and_manual_union():
    clean, _, dupes, added = c.build_clean(["A.NS", "A.NS", "B.BO"], ["B.BO", "C.NS", "BANKBEES.NS"])
    assert clean == ["A.NS", "B.BO", "C.NS"] and dupes == ["A.NS", "B.BO"] and added == ["C.NS"]


def test_suffix_is_preserved_never_normalised():
    clean, *_ = c.build_clean(["NSDL.BO", "NSDL.NS"], [])
    assert clean == ["NSDL.BO", "NSDL.NS"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} clean_universe tests passed")
