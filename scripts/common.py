"""Shared paths and universe loaders for the data scripts (one definition instead of
five copies). Paths are resolved from this file, so scripts work from any cwd."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "checkpoints" / "raw"
MANIFEST_CSV = ROOT / "checkpoints" / "manifest.csv"
FNO_UNIVERSE_CSV = ROOT / "data" / "fno_universe.csv"


def load_fno_symbols(path=None):
    path = path or FNO_UNIVERSE_CSV
    if not Path(path).exists():
        return []
    return pd.read_csv(path)["symbol"].tolist()


def load_manifest_symbols(status, manifest_csv=None):
    """Symbols whose manifest status == `status`, in manifest order."""
    path = manifest_csv or MANIFEST_CSV
    if not Path(path).exists():
        return []
    m = pd.read_csv(path)
    return m[m["status"] == status]["symbol"].tolist()


def load_validated_symbols(manifest_csv=None):
    return load_manifest_symbols("VALIDATED", manifest_csv)


def load_validated_set(manifest_csv=None):
    return set(load_validated_symbols(manifest_csv))


def load_partial_set(manifest_csv=None):
    """PARTIAL = real, structurally-sound OHLC below backfill.py's MIN_ROWS floor."""
    return set(load_manifest_symbols("PARTIAL", manifest_csv))
