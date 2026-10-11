# Backtester

Shared backtesting engine + manual strategy builder, serving:
- **SST swing system** (TradingJournal) — 21-day-high breakout, no stop-loss, add-on rules
- **Options volatility-selling strangle** (Options Terminal) — deferred, not yet specced

Full architecture and every design decision is recorded in [`docs/SPEC.md`](docs/SPEC.md) — read that before touching code. Nothing in this repo should contradict it; if a change is needed, the spec gets updated first.

## Status

Spec closed (equity/SST side). Build in progress, one step at a time.

## Stack

- Frontend: GitHub Pages (static)
- Historical data: Parquet files, committed to the `backfill-checkpoints` git branch (originally planned as GitHub Releases + DuckDB, see `docs/SPEC.md` section 4 for why that got bypassed — not yet reconciled, not currently a problem)
- Backtest execution: GitHub Actions (async job model)
- Trigger: GitHub's own native "Run workflow" button (`workflow_dispatch`) — no separate relay. An earlier Cloudflare Workers relay plan was deliberately skipped, not built (see `docs/SPEC.md` build order, item 8)
- Database server: none, by design

No hosted database, no recurring infra cost beyond what's already explained in the spec.

## Status (2026-10-11)

Full pipeline run end-to-end on real data: 1,466/1,535 stocks backfilled, SST strategy parameters validated trade-by-trade against raw OHLC, full-universe backtest run completed. See `docs/SPEC.md` Status section for details.
