# Backtester

Shared backtesting engine + manual strategy builder, serving:
- **SST swing system** (TradingJournal) — 21-day-high breakout, no stop-loss, add-on rules
- **Options volatility-selling strangle** (Options Terminal) — deferred, not yet specced

Full architecture and every design decision is recorded in [`docs/SPEC.md`](docs/SPEC.md) — read that before touching code. Nothing in this repo should contradict it; if a change is needed, the spec gets updated first.

## Status

Spec closed (equity/SST side). Build in progress, one step at a time.

## Stack

- Frontend: GitHub Pages (static)
- Historical data: Parquet files in GitHub Releases, partitioned by year
- Query engine: DuckDB
- Backtest execution: GitHub Actions (async job model)
- Trigger relay: Cloudflare Workers (the only non-GitHub-native piece — holds the GitHub token needed to dispatch a workflow run from the browser)
- Database server: none, by design

No hosted database, no recurring infra cost beyond what's already explained in the spec.
