"""
Appends a summary entry for one backtest run to results/index.json, so the
frontend can list available runs without GitHub Pages directory listing
(not a thing) or hitting the GitHub API (CORS/rate-limit complexity for no
real benefit, since this file is same-origin and trivial to maintain here).

Called by .github/workflows/backtest.yml right after a results file is
written, before it gets committed — both files land in the same commit.

Usage: python scripts/update_results_index.py <results_json_path>
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
INDEX_PATH = ROOT / "results" / "index.json"


def build_entry(results_path: Path, results: dict) -> dict:
    m = results["metrics"]
    return {
        "filename": results_path.name,
        "run_at": results["run_at"],
        "run_label": results_path.stem.split("_", 1)[1] if "_" in results_path.stem else "run",
        "win_rate_pct": m["win_rate_pct"],
        "profit_factor": m["profit_factor"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "total_closed_trades": m["total_closed_trades"],
        "open_positions_count": m["open_positions_count"],
        "combined_pl_rs": m["combined_pl_rs"],
        "universe_size": results["universe_size"],
    }


def upsert_entry(index: list, entry: dict) -> list:
    """Replace the entry with the same filename in place (a re-run or workflow retry for
    the same results file must not list the run twice); append if it is new."""
    for i, existing in enumerate(index):
        if existing.get("filename") == entry["filename"]:
            index[i] = entry
            return index
    index.append(entry)
    return index


def write_atomic(path: Path, payload) -> None:
    """Write via a temp file + rename so an interrupted run can't leave a truncated index."""
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def load_index(path: Path) -> list:
    if not path.exists():
        return []
    try:
        index = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} is not valid JSON ({e}); refusing to overwrite it — fix or delete it first")
    if not isinstance(index, list):
        raise SystemExit(f"{path} must contain a JSON list")
    return index


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/update_results_index.py <results_json_path>")

    results_path = Path(sys.argv[1])
    entry = build_entry(results_path, json.loads(results_path.read_text()))
    index = upsert_entry(load_index(INDEX_PATH), entry)
    write_atomic(INDEX_PATH, index)
    print(f"Upserted run in {INDEX_PATH}: {entry['filename']} ({len(index)} runs listed)")


if __name__ == "__main__":
    main()
