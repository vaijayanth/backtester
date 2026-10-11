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


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/update_results_index.py <results_json_path>")

    results_path = Path(sys.argv[1])
    results = json.loads(results_path.read_text())
    m = results["metrics"]

    entry = {
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

    index = []
    if INDEX_PATH.exists():
        index = json.loads(INDEX_PATH.read_text())

    index.append(entry)
    INDEX_PATH.parent.mkdir(exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=2))
    print(f"Appended run to {INDEX_PATH}: {entry['filename']}")


if __name__ == "__main__":
    main()
