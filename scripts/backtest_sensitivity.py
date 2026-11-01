"""SST backtest sensitivity — the honest way to read a daily-bar backtest.

Daily OHLC cannot say whether a day's High or Low came first, and a same-day tie for a
limited number of position slots has no objectively right order. Those two assumptions
move the SST result a lot (measured 2026-11-01 on 350 real symbols: P&L Rs 3.6M vs Rs 9.3M,
max drawdown 16% vs 7%). So instead of one number this runs a grid —

    intrabar_order  in {high_first, low_first}
    random_seed     in {0 .. seeds-1}          (same-day entry priority shuffle)
    gap_aware_fills = True                      (always: legacy fills were unreachable prices)

— on one load of the data, and reports worst / median / best per metric ("worst" = the worst
outcome for that metric: lowest win rate / profit factor / P&L, HIGHEST drawdown).

Usage:
  python scripts/backtest_sensitivity.py                         # defaults, 2 x 5 = 10 runs
  python scripts/backtest_sensitivity.py --params params.json --seeds 3 --out results/sensitivity_latest.json
"""

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import backtest_engine as engine

ROOT = Path(__file__).parent.parent
DEFAULT_OUT = ROOT / "results" / "sensitivity_latest.json"
INTRABAR_ORDERS = ("high_first", "low_first")

# metric key in results["metrics"] -> (output name, higher_is_better)
METRICS = {
    "win_rate_pct": ("win_rate_pct", True),
    "profit_factor": ("profit_factor", True),
    "max_drawdown_pct": ("max_drawdown_pct", False),
    "combined_pl_rs": ("combined_pl_rs", True),
    "total_closed_trades": ("total_closed_trades", True),
}


def summarise(values, higher_is_better):
    """worst / median / best over the non-null values; None when there are none.
    'worst' is direction-aware (for drawdown it is the largest value)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"worst": None, "median": None, "best": None, "n": 0}
    lo, hi = min(vals), max(vals)
    return {
        "worst": lo if higher_is_better else hi,
        "median": round(statistics.median(vals), 2),
        "best": hi if higher_is_better else lo,
        "n": len(vals),
    }


def aggregate(runs):
    return {name: summarise([r["metrics"].get(key) for r in runs], hib) for key, (name, hib) in METRICS.items()}


def run_grid(base_params, seeds, symbols=None):
    """Runs the grid on ONE load of the data. Returns (runs, data_through, universe_size)."""
    cache = engine.load_universe_data(symbols)
    original = engine.load_universe_data
    engine.load_universe_data = lambda symbols=None: cache   # prep_symbol_df copies, so sharing is safe
    runs, data_through, universe = [], None, None
    try:
        for order in INTRABAR_ORDERS:
            for seed in range(seeds):
                params = {**base_params, "gap_aware_fills": True, "entry_priority": "random",
                          "intrabar_order": order, "random_seed": seed}
                res = engine.run_backtest(params)
                data_through = res["equity_curve"][-1]["date"] if res["equity_curve"] else data_through
                universe = res.get("universe_size", universe)
                runs.append({"intrabar_order": order, "random_seed": seed, "metrics": {k: res["metrics"].get(k) for k in METRICS}})
                print(f"  {order:10} seed {seed}: win {res['metrics']['win_rate_pct']}%  PF {res['metrics']['profit_factor']}  "
                      f"DD {res['metrics']['max_drawdown_pct']}%  P&L Rs{res['metrics']['combined_pl_rs']:,.0f}")
    finally:
        engine.load_universe_data = original
    return runs, data_through, universe


def build_summary(runs, data_through, universe, base_params, seeds):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_through": data_through,
        "universe_size": universe,
        "grid": {"intrabar_order": list(INTRABAR_ORDERS), "random_seeds": list(range(seeds)),
                 "gap_aware_fills": True, "entry_priority": "random", "runs": len(runs)},
        "base_params_overrides": base_params,
        "range": aggregate(runs),
        "by_intrabar_order": {o: aggregate([r for r in runs if r["intrabar_order"] == o]) for o in INTRABAR_ORDERS},
        "runs": runs,
        "note": ("Daily bars cannot show whether the day's High or Low came first, and same-day entry priority "
                 "is a shuffle, so read this as a range, not a point estimate."),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", type=str, default=None, help="JSON file of base strategy params (overrides defaults)")
    parser.add_argument("--seeds", type=int, default=5, help="random seeds per intrabar order (default 5)")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT))
    args = parser.parse_args()
    if not 1 <= args.seeds <= 20:
        parser.error("--seeds must be between 1 and 20")

    base = {}
    if args.params:
        try:
            base = json.loads(Path(args.params).read_text())
        except (OSError, json.JSONDecodeError) as err:
            parser.error(f"--params {args.params}: {err}")
        if not isinstance(base, dict):
            parser.error("--params must contain a JSON object (e.g. {\"max_open_positions\": 100})")
        unknown = sorted(set(base) - set(engine.DEFAULT_PARAMS))
        if unknown:
            parser.error(f"unknown strategy param(s) {unknown}; known: {sorted(engine.DEFAULT_PARAMS)}")
    for forced in ("gap_aware_fills", "entry_priority", "intrabar_order", "random_seed"):
        base.pop(forced, None)   # the grid owns these

    print(f"Sensitivity grid: {len(INTRABAR_ORDERS)} intrabar orders x {args.seeds} seeds, gap-aware fills")
    runs, data_through, universe = run_grid(base, args.seeds)
    summary = build_summary(runs, data_through, universe, base, args.seeds)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, default=float))
    tmp.replace(out)
    r = summary["range"]
    print(f"\nRange over {len(runs)} runs (data through {data_through}):")
    for name in ("win_rate_pct", "profit_factor", "max_drawdown_pct", "combined_pl_rs"):
        print(f"  {name:18} worst {r[name]['worst']}  median {r[name]['median']}  best {r[name]['best']}")
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
