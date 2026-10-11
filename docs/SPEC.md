# Backtesting Dashboard + Strategy Builder — Running Spec

Status: IN PROGRESS — spec/architecture phase, no code written yet per explicit instruction.
Rule: nothing gets built until the full spec is closed and signed off.

## 1. Core Purpose (CLOSED)
- Shared backtesting engine serving BOTH strategies:
  1. SST swing system (TradingJournal) — 21DH breakout, no stop-loss, add-on rules
  2. Options volatility-selling strangle (Options Terminal) — IVP-based entries, CE naked + PE hedged
- This reverses the earlier Options Terminal scope line "no backtesting engine" — deliberate, user-driven reversal.
- Tool is a **manual strategy builder**, NOT an automated optimizer:
  - Every threshold (21DH vs 10DH, 6% lock-in vs 8%, IVP≥70 vs ≥60, etc.) is a user-controlled parameter (slider/input in UI)
  - User sets parameters → runs backtest → sees results → manually adjusts → re-runs → compares
  - NO automated parameter search/grid-search/walk-forward optimization engine. No overfitting-guard machinery needed because the tool isn't the one deciding "optimal" — the user is, through manual iteration.
  - Tool should make running + comparing multiple manual parameter sets easy (so manual exploration isn't painful), but does not itself search or rank parameter space.

## 2. Backtest Window (CLOSED)
- 5+ years of history (not 3 years) — covers more regime diversity including volatile periods.

## 3. Options side — deferred (NOT closed, parked)
- Options pricing-model question (NSE Bhavcopy actual settlement prices vs Black-Scholes approximation from historical IV) was asked and explicitly dismissed/paused by user — "lets tackle options later."
- Do not proceed on options-specific backtest design until user returns to it.

## 4. Equity Historical OHLC Data Pipeline (CLOSED)
Target: SST swing backtest, ~2,000 NSE-listed midcap+smallcap+largecap stocks (not a fixed F&O-only list — broader than Options Terminal's 213-stock universe).

| Decision | Choice |
|---|---|
| Universe source | User's Google Sheet "StockList" tab: https://docs.google.com/spreadsheets/d/1tDRsk2B9udGRhirzwxrryc3HvtHhg6rwN8UTjeJlwAc/edit?gid=0#gid=0 (sheet "StockList", single column A). **Verified raw row count: 1,610** (not ~2,400 as originally estimated — that figure came from the sheet's grid dimensions/rowCount metadata, not actual populated data; confirmed by checking rows 1605-1620, data ends exactly at row 1610 with KRONOX.NS, rows 1611+ blank). |
| Universe cleanup | Implemented in `scripts/clean_universe.py`, explicit ETF exclusion list (curated by known fund family: BEES/IETF/MO-prefix/named thematic ETFs — NOT positional heuristics, since some real stocks like RKSWAMY/ALBERTDAVD/PLASTIBLEN/DENORA sit directly adjacent to the ETF block in the sheet but aren't ETFs). **Result: 1,610 raw → 65 ETFs removed → 10 duplicates removed → 1,535 clean equities.** Full removal detail in `data/universe_report.txt`. |
| Exchange suffix | **Trust the sheet as-is per symbol — do NOT normalize to one exchange.** Correction logged: NSDL.BO is correct (NSDL is BSE-only, no NSE listing) — my earlier assumption that all should be `.NS` was WRONG and reversed. There may be other legitimately BSE-only (`.BO`) names in the list — don't force-correct any suffix without verifying that specific symbol's actual listing venue. |
| Historical backfill source | Yahoo Finance (`yfinance` library), one-time 5-year pull per symbol. Ticker format already matches (`.NS`/`.BO` suffix as given in sheet). |
| Price adjustment | **Split/bonus-adjusted close** (and O/H/L adjusted proportionally) — continuous series across corporate actions, required for EMA/RSI/21DH-breakout logic to not produce false signals at split/bonus dates. (Raw unadjusted + separate corp-actions table was the alternative, rejected — more moving parts, easier to get wrong.) |
| Ongoing incremental updates (post-backfill) | **CUT FROM V1.** One-time 5-year backfill only — no scheduled daily/incremental Dhan updates for this tool. Data is a static snapshot as of backfill date; acceptable because this is a backtesting tool (point-in-time historical analysis), not a live feed. Re-running the backfill manually later is how data ever gets refreshed, not an automated job. Removes an entire scheduled-job dependency from v1. |
| Storage | **SUPERSEDED — see note below.** CockroachDB Serverless was recommended based on research showing a 10 GiB permanent free tier, but the user found CockroachDB killed that free tier for new orgs created on/after Sept 15, 2026 (replaced with a 30-day/$400-credit trial, then pause/delete without a card). Real-time lesson: even freshly-verified free-tier research can go stale within the same session. Decision reversed — see "Storage (revised)" below. |
| **Storage (revised, CLOSED)** | **No hosted database at all.** Historical OHLC stored as **Parquet files in GitHub Releases** (not raw git commits — Git handles large binaries poorly across revisions; Releases are static asset storage, the right tool), partitioned sensibly (e.g. by year and/or sector) so a backtest only pulls the slice it needs. Queried via **DuckDB** (embedded SQL engine, no server/daemon, `pip install duckdb`, reads Parquet directly including remote via HTTP byte-range requests). Removes the entire "will this vendor's free tier survive" risk category — GitHub's free storage for public repos has been stable for years, unlike every DB-as-a-service free tier checked this session (CockroachDB pulled its tier THIS WEEK; Supabase 500MB+7day-pause; Neon 0.5GB+5min-suspend; Aiven 1GB; Render 1GB+30-day-expiry). **Known divergence from actual build (not yet reconciled)**: the backtest engine as actually built (`scripts/backtest_engine.py`) reads raw per-symbol Parquet files straight off the `backfill-checkpoints` git branch via plain pandas (`load_universe_data()`) — no DuckDB, no GitHub Releases, no year-partitioning, no separate validation/publish step. This works fine at the current scale (1,466 symbols × 5yr, ~26s runtime for the one-stock test, well within job limits for the full run too) so it hasn't blocked anything, but it means build-order item 5 below (the Releases publish pipeline this row originally specified) was effectively bypassed rather than completed. Revisit only if read performance or branch size actually becomes a problem — no evidence of that yet. |
| Fallback/cross-check for equity data | NSE Bhavcopy equity EOD archives — used only as a spot-check/gap-filler if Yahoo pull looks wrong or missing for specific symbols, not a parallel full pipeline. |
| Indicator computation (EMA/RSI) | Computed on-the-fly from Parquet OHLC at backtest-run time (pandas/DuckDB, vectorized, fast even for ~2,000 stocks × 5yr) — NOT pre-computed/stored as extra columns. |

### Resolved
- **Parquet partitioning (CLOSED)**: partition by YEAR (one file per year, all stocks inside), not by stock. Matches the engine's actual access pattern — it walks day-by-day across the whole universe checking breakout signals, so a backtest over N years reads N year-files either way; partitioning by stock instead would mean opening ~2,000 small files per year of history, strictly worse for this engine's query shape.
- **Backfill execution (CLOSED, re-confirmed)**: GitHub Actions, manually triggered — NOT local machine, re-confirmed after a specific reconsideration (see below) rather than reverted. Same execution model as everything else — zero local Python dependency, reproducible from any machine.

### Backfill Reliability Design (CLOSED)
~2,000 stocks × 5 years via yfinance needs chunking across multiple workflow runs (Actions' 6hr/job limit) AND must survive partial failures without re-downloading completed work. Full design, locked:

- **Chunk + checkpoint + resume, per-STOCK granularity (not per-batch)**: if 37 of 50 stocks in a batch complete before a failure, only the remaining 13 retry — never re-download a stock already validated.
- **Download manifest**: tracks every stock from the cleaned StockList with status `PENDING → DOWNLOADED → VALIDATED → PUBLISHED`, plus record count, first/last available date, validation status, error message if any. A failed stock stays independently retryable without affecting completed ones.
- **Batch size**: 50 stocks/batch, provisional default, tunable.
- **Retries**: built-in delay/backoff retries for transient yfinance failures and rate limits (same pattern as existing Dhan `dhan_post` retry logic in the Options Terminal backend).
- **Durable checkpointing ACROSS SEPARATE ACTIONS RUNS — the key technical resolution**: GitHub Actions' default filesystem is ephemeral (wiped when a job ends), so checkpoints can't live only on the runner's local disk, or a second manually-triggered run starts with nothing and resumability breaks. Fix: each completed batch's manifest update + raw data commits directly to a dedicated git branch (e.g. `backfill-checkpoints`) and pushes. The next run, whenever triggered, reads that branch's current state first. Durable, inspectable via commit history, free, no artifact-retention windows or cache-eviction risk to manage — keeps the backfill fully Actions-native rather than reverting to local execution (which was considered and rejected — see note below).
- **Checkpoint storage is SEPARATE from the final published dataset**: raw/in-progress batch data + manifest live on the checkpoint branch; the final year-partitioned Parquet files only get published to GitHub Releases once, after full validation across all symbols. Never publish incomplete annual Parquet files as the final dataset.
- **Resume logic on every run start**: Validated+checkpointed → skip. Downloaded but not validated → re-validate. Failed temporarily → retry. Permanently unavailable → record and continue (don't block the rest of the universe on one bad symbol). Never attempted → download. Deterministic — doesn't depend on anyone remembering where the last run stopped.
- **Publish-after-validation pipeline**: combine all downloaded data → check for duplicates, missing values, invalid OHLC relationships (e.g. high < low), date coverage gaps → generate final year-partitioned Parquet → generate a data-quality report → publish to Releases. The backtesting engine only ever consumes a published, validated dataset — never reads from the in-progress checkpoint branch.
- **Never silently treat a failed/unavailable symbol as successfully downloaded** — explicit failed-symbol report alongside the manifest.

**Note on local-execution alternative (considered, rejected)**: a proposal to run the one-time backfill locally (citing the ephemeral-storage problem above as the reason) was considered and rejected in favor of the git-branch-checkpoint fix — it solves the identified problem without reintroducing the "depends on one specific computer" fragility that the rest of this architecture (Actions for live refresh, Actions for backtest execution, Actions for this backfill) has consistently been designed to avoid.

**Yahoo reachability caveat**: could not be verified from the sandboxed dev environment used to write this spec (proxy blocked the test with a 403 on CONNECT tunnel) — this is a sandbox network-policy limitation, NOT evidence Yahoo itself is unreachable in production (e.g. from GitHub Actions). Known independent risk regardless of that test: Yahoo's unofficial API has no SLA and has a history of rate-limiting/blocking cloud datacenter IP ranges. Mitigated by NSE Bhavcopy fallback for any gaps — validated empirically below.

**Full backfill run — DONE (2026-10-11)**: ran against the real 1,535-symbol cleaned universe. Result: **1,466 VALIDATED / 69 FAILED**. Yahoo reachability from GitHub Actions is confirmed fine — none of the 69 failures were network/rate-limit errors. All failures are legitimate data-availability cases: "empty dataframe" or "only N rows — too little history" for recently-listed names (new IPOs, REITs/InvITs like CUBEINVIT/CAPINVIT that genuinely don't have 5 years of trading history on Yahoo), plus a couple of "null OHLC values present" data-quality cases. These are retryable (manifest tracks up to `MAX_AUTO_RETRY_ATTEMPTS=3`) but most will likely stay failed since the root cause is real listing age, not a transient blip. 95.5% coverage on a real universe — considered good enough to proceed to backtesting without chasing the remaining 69.

**Real bug found + fixed during the full-run trigger**: the workflow was first triggered with `limit: ""` intending "unlimited" (per the `backfill.py` CLI contract where an absent `--limit` flag means no cap). But GitHub's `workflow_dispatch` REST API does not treat an empty-string input as "unset" — it silently fell back to the YAML's own `default: "1"`, so that first "full" run actually only processed 1 new symbol in 26 seconds (confirmed via the run's own log line `limit=1 symbol=`, not assumed). **Fix**: trigger with an explicit large number instead (`limit: "2000"`, safely above the 1,535-symbol universe) — `targets[:2000]` is a no-op slice, sidestepping the empty-string ambiguity entirely. Re-triggered and ran to completion correctly (run ID `38119506823`, ~70+ minutes for 1,533 real symbol fetches with retry backoff on failures).

**Live-progress caveat, confirmed real**: the `backfill-checkpoints` branch (which the deployed site's BACKFILL tab reads) only gets committed/pushed ONCE, in the workflow's final step, after the entire run finishes — even though `backfill.py` itself saves its manifest locally after every symbol. So the live site's BACKFILL tab shows no incremental progress during a long run; it jumps straight from old state to final state. The only place to see true live per-symbol progress during a run is the GitHub Actions run log itself (prints `[N/1535] SYMBOL... VALIDATED/FAILED` per symbol in real time). Not yet fixed in the workflow — would need the commit step moved inside the per-batch loop instead of running once at the end; flagged as a possible follow-up, not done.

## 4b. Backtest Execution Architecture (CLOSED)
- **Model: asynchronous job, not synchronous request/response.** User adjusts parameters, clicks "Run Backtest," dashboard shows job status (Queued → Running → Completed/Failed). User can close the page; job runs independently. This removed the earlier "need sub-second response" framing that was pushing toward Cloud Run/Cloudflare Workers as the compute layer — a manual-exploration backtest tool never needed instant results, just a way to check back.
- **Compute: GitHub Actions** runs the actual Python backtest (reuses existing data-pipeline pattern, no new compute vendor, no free-tier-stability risk since GitHub Actions on public repos is free/unlimited).
- **Data access during a run:** Python in the Actions job uses DuckDB to read the needed Parquet slice(s) directly from GitHub Releases.
- **Results:** written as small JSON files (a few KB — equity curve points, win rate, PF, drawdown, trade list), committed back to the repo, same pattern as the existing OptionsTerminal data-refresh JSON outputs. Dashboard polls/reads these once available. Preserves run history naturally — old result files = saved past runs, enabling side-by-side comparison of manually-tried parameter sets (ties back to section 1's "make comparing manual runs easy" requirement).
- **The one gap this didn't originally cover — now resolved:** GitHub has no mechanism to trigger a workflow (`workflow_dispatch`) from an unauthenticated public page; it requires an authenticated API call with a token, and that token can't be safely embedded in GitHub Pages' public client-side JS (anyone viewing dev tools/network tab gets it, and a leaked GitHub token is a bigger liability than a leaked app password). **Fix: a thin serverless relay** — Cloudflare Workers (free, no card required, confirmed viable earlier in this research) holds the GitHub token as a secret and does nothing but forward "run this" requests from the dashboard to GitHub's dispatch API. It does NOT run any compute or touch any data — pure authenticated doorbell. This is the only piece of infrastructure in the whole execution path that isn't GitHub-native. **SUPERSEDED — see build order item 8: this relay was deliberately SKIPPED, never built. GitHub's own native "Run workflow" button in the Actions tab is used instead.**
- **Honest caveat carried forward, not yet empirically tested:** GitHub Actions free tier has real limits (6hr max runtime per job, concurrency caps) — needs validation once built that a full 2,000-stock/5-year backtest run actually completes comfortably within them, rather than assuming.

## 5. Repo location (CLOSED)
New dedicated repo, not bundled into TradingJournal or Options Terminal, serves both strategies cleanly. Created and confirmed: `vaijayanth/backtester` (github.com/vaijayanth/backtester). Note: an earlier attempt under a different account (`jayanthmk/backtester`) failed — this session's GitHub connector is scoped to `vaijayanth` only.

## 6. Dhan token automation (CLOSED, for later build — relevant to live Options Terminal, tangential to backtesting)
- Dhan access tokens expire in 24h by regulation, no way around it.
- Full automation IS possible without daily manual browser login, but NOT via plain API-key+secret exchange (that still needs a human 2FA browser session).
- Unlock: enable TOTP on the Dhan account once → a separate endpoint accepts PIN + live TOTP code (via `pyotp` or similar) and returns a fresh 24h token headlessly. Community `dhanhq` Python SDK exposes this as `generate_token(pin, totp)`.
- NOT fully verified against live Dhan docs (research came from search snippets, not a direct fetch) — user was asked to manually confirm the exact endpoint at dhanhq.co/docs/v2/authentication/ before this gets coded against.
- Not directly part of the backtesting spec (that's live-terminal plumbing) but logged here so it isn't lost.

## 7. SST Strategy Parameterization (CLOSED — defaults set, all tunable, fine-tuning deferred to actual use)

Decision: **everything tunable** — no rules fixed as hardcoded constants, including position sizing/max positions (reversed from my own initial "recommended" narrower scope, per explicit user direction).

| Parameter | Default | Notes |
|---|---|---|
| `breakout_window_days` | 21 | The "N-Day High" entry trigger |
| `ema_filter_fast` | 50 | Universe filter: price must be above this EMA |
| `ema_filter_slow` | 200 | Universe filter: price must be above this EMA too |
| `profit_lock_trigger_pct` | **7%** | P&L% (from peak) at which trailing stop arms |
| `trail_checkpoints` | single tier, 6% flat | Trail distance below peak once armed (see below — superseded the earlier 4-tier ratchet) |
| `add_on_trigger_pct` | −7% | Loss level that triggers an add-on (only if above both EMAs) |
| `add_on_size_fraction` | 1/10 | Add-on size as a fraction of current position qty |
| `add_on_max_per_week` | 1 | Per stock, not portfolio-wide |
| `base_position_rs` | ₹20,000 | Starting position size |
| `position_size_increment_rs` | ₹5,000 | Added per N closed trades |
| `position_size_increment_trades` | 100 | Trade-count threshold per tier step |
| `position_size_cap_rs` | ₹1,00,000 | Max position size |
| `max_open_positions` | 200 | Portfolio-wide cap, reconfirmed by user before the full-universe run |
| `initial_capital_rs` | ₹50,00,000 | Starting capital |

**Trailing-stop mechanical rule — REVISED (CLOSED), supersedes the original 4-tier ratchet below.**

User re-specified the exact live-system rule directly (2026-10-11): arm the trail only once peak profit reaches **7%**, then trail at a **flat 6% below peak** — not a progressive multi-tier ratchet. Implemented by setting `profit_lock_trigger_pct: 7.0` and collapsing `trail_checkpoints` to a single entry `{"profit_pct": 0, "trail_pct": 6}` (the engine's checkpoint-table code is unchanged and still supports multiple tiers — this is a params choice, not a code removal).

**Why the change was made — found via validation, not assumption:** backtesting 360ONE.NS against the *original* 4-tier table (arm at 6%, tiered 6/5/4/3% trail) surfaced a same-day whipsaw: entry 2022-09-16, the stock's High spiked 6.58% intraday, which armed the trail immediately and set a stop at the new peak's 6%-below level — and that same day's Low dipped back below that stop, forcing an exit 4 days later for only +0.18%. Walking the raw OHLC day-by-day reproduced the engine's output exactly, confirming this wasn't a bug — just an aggressive activation threshold reacting to a single day's intraday range. The user's restated rule (arm at 7%, not 6%) fixes this at the source: that same trade now rides to +1.92% over 133 days instead of exiting same-week.

**Structural property of the revised rule, worth remembering when reading results**: since the ONLY exit path is this profit-trail (section 7's "Loss exit rule" below is unchanged — still no loss-exit at all), **a closed trade can never show a loss**. A position that never reaches 7% simply stays open; it's never force-closed into negative territory. So "100% win rate on closed trades" under this rule isn't a performance signal — it's guaranteed by construction. The real signal is in open-position MTM (including add-on-averaged losers) plus realized gains, combined — consistent with the "combined realised + unrealised" framing already used everywhere else in this system (see TradingJournal's CLAUDE.md). Also provable algebraically: once armed, the worst-case locked-in exit is never below `entry × 1.07 × 0.94 = entry × 1.0058` (+0.58%), since arming itself requires +7% and the trail then gives back at most 6% of that peak.

**Validated against raw data, not just the engine's own output** — two trades on 360ONE.NS independently replayed by hand from the raw parquet:
1. The first breakout trade (entry 395.70, exit 397.91 under the old rule) — confirmed breakout condition, EMA50/EMA200 filter, position-size tier, and the exact trailing-stop fill.
2. The one add-on trade — confirmed the position actually took **two** separate add-ons (41 days apart, correctly past the 7-day per-stock cooldown), with the blended average price matching the engine's reported entry_price to the paisa after each one.

**Known modeling simplification, flagged but not changed**: entry and add-on fills are priced at the exact `rolling_high` (21-day-high) level itself, not the day's Close or a slippage-adjusted fill — i.e. every breakout is assumed to fill at a theoretically perfect GTT price. Matches "GTT order triggers exactly at 21DH" as a mechanical rule, but means profit_factor/win-rate across the full universe will run structurally more optimistic than live fills. Not revisited yet — flag if it matters once full-universe results are reviewed.

<details>
<summary>Original 4-tier ratchet (SUPERSEDED, kept for history)</summary>

| Profit level (P&L% ≥) | Trailing stop distance below CMP |
|---|---|
| 6% | 6% |
| 10% | 5% |
| 15% | 4% |
| 25% | 3% |

Mechanics: stop distance tightens as profit grows. One-directional ratchet — once a checkpoint is crossed, the tighter distance applies and never loosens back even if price pulls back below that checkpoint. This table is still fully supported by the engine's `trail_checkpoints` structure for future tuning; it's just not the current default.
</details>

### Resolved
- **Entry signal scope (CLOSED)**: v1 backtests ONLY the mechanical 21(N)DH breakout signal. Pullback / 21 EMA Pullback / 10 EMA Pullback setup types are NOT simulated — they involve discretionary/visual judgment in live trading that isn't precisely specifiable from OHLC alone, and inventing a mechanical proxy would bake in a guess rather than test a real rule.
- **Loss exit rule (CLOSED)**: NO loss-exit mechanism at all, matching the live SST design's actual intent exactly (not a simplification). A losing position only closes if it later recovers enough to trigger the profit trailing-stop, or stays open through the end of the backtest window. No fundamental-deterioration exit (not backtestable from price data), no time-based forced-close cutoff.

## 8. Backtest Output / Metrics (CLOSED)
Full set, matching existing live-dashboard metric language (not a lean summary) — so backtest results are directly comparable to what's already shown in TradingJournal/Options Terminal dashboards, not a separate vocabulary:
- Equity curve (realized + open-position MTM — combined-P&L philosophy, same as existing dashboards)
- Win rate, profit factor, max drawdown
- Avg hold time (winners vs losers split)
- Total trade count
- Full trade-by-trade list (not just headline numbers) — needed to drill into WHY a parameter set performed a certain way without re-running
- Result files are per-run JSON (a few KB each per earlier execution-architecture decision), one file per run, naturally preserving run history for side-by-side comparison

## 9. Range Analysis — real-data upgrade (CLOSED, BUILT)
Options Terminal's existing Range Analysis tab (1-year outlier/percentile-boundary tool: weekly/monthly periods, OC or H-L calc type, percentile coverage slider, scatter chart + outlier table) was built earlier with 100% synthetic data (`yearSeriesFor()`, seeded fake OHLC), explicitly labeled "DUMMY DATA — not live" at the time pending a real historical data source.

User confirmed (via reference screenshot of a similar third-party tool's "Outlier Distribution / Survivability Scatter Plot") that this is a real, wanted feature: for ANY selected stock, show the distribution of its periodic returns with percentile-boundary thresholds, so "what's the threshold this stock has moved in a month" is answerable at a glance.

**Resolution**: this becomes a direct consumer of the same Parquet/DuckDB 5-year historical OHLC pipeline being built for the SST backtest (section 4) — no separate data source needed. Once that pipeline exists, Range Analysis swaps its synthetic generator for a real DuckDB query against the Parquet data and the "DUMMY DATA" label comes off. Same underlying asset (historical OHLC for the stock universe) serving two different views: backtesting (SST engine) and distributional/outlier analysis (Range Analysis).

**Built, then simplified per direct feedback**: first version used percentile-derived boundaries (coverage slider, 5th/95th percentile bounds) — user found this unintuitive and asked for a plain, direct framing instead: "how many months has it gone beyond +10%/-10% from the monthly beginning price." Rebuilt as "Threshold Breach Analysis": a fixed ±% input (default 10%, user-adjustable), weekly/monthly period toggle, always OC (open→close) return vs the period's own opening price — no H-L calc-type toggle, no percentile machinery. Stats row: count of periods above +threshold, below −threshold, within range, total. Bar chart (period-end date vs return%) with dashed threshold lines, colored by breach direction. Outlier table lists actual breaching periods sorted by size. Computed client-side from the same real OHLC rows already loaded via DuckDB-WASM for the selected symbol — no second query. Core math (period grouping, OC return, threshold comparison) unit-tested directly in Node against known values before shipping — all passed.

## 10. Strategy Builder UI — Run Comparison (CLOSED)
Side-by-side run list: a table of past runs (parameter summary + headline metrics per row), select any two+ to overlay their equity curves on one chart and diff their metrics. Each result JSON file (section 8) is one row — run history is already naturally preserved by the file-per-run design, this just adds the comparison view on top. Lets manual parameter exploration (section 1's actual stated goal — "give me control to test with various parameters") build intuition across tries instead of relying on memory/screenshots.

## Status

Spec closed for the equity/SST side, end to end — purpose, data pipeline, storage, backfill reliability, execution architecture, strategy parameterization, output metrics, and UI run-comparison are all locked (see sections above). Repo created and confirmed: `vaijayanth/backtester` (github.com/vaijayanth/backtester), push access verified.

**2026-10-11 — full pipeline run, end to end, real data:**
- Full backfill completed: 1,466/1,535 symbols validated (see section 4).
- Trailing-stop rule corrected per direct user spec (7% arm, flat 6% trail) and re-validated against raw data (see section 7) before scaling up.
- Full-universe backtest triggered against all 1,466 validated symbols with the corrected params (run label `full-universe-7pct-trigger-6pct-trail`) — this is the first real, full-scale SST backtest result produced by this system. Results reviewed next.

### Still open
- **Options strategy side** — fully deferred (see section 3). Not started: pricing model (Bhavcopy settlement vs Black-Scholes), parameterization, anything options-specific.
- **Range Analysis real-data wiring** (section 9) — confirmed as a feature, not yet re-specced in detail; revisit once the historical data pipeline is live.
- Exact UI layout/visual design for the Strategy Builder dashboard itself — functional requirements locked (sections 1, 10), visual design not yet discussed.

### Build order (in progress, one step at a time — user explicitly wants incremental build with checkpoints for review, not everything at once)
1. ~~Repo scaffold~~ — DONE
2. ~~StockList universe cleanup~~ — DONE (1,610 raw → 1,535 clean, Universe tab live)
3. ~~Backfill script: chunk + checkpoint + resume~~ — DONE (`scripts/backfill.py`)
4. ~~GitHub Actions workflow for the backfill~~ — DONE (`backfill.yml`, `backfill-checkpoints` branch). **Full real run completed 2026-10-11: 1,466 VALIDATED / 69 FAILED of 1,535** (see section 4's "Full backfill run" note for the failure breakdown and the empty-string `limit` bug found+fixed along the way).
5. Validation + publish pipeline (year-partitioned Parquet → GitHub Releases) — **STILL NOT STARTED, and now effectively bypassed** — see section 4's "known divergence" note: the engine reads straight from `backfill-checkpoints` via pandas instead. Not blocking anything at current scale.
6. ~~SST backtest engine~~ — DONE (`scripts/backtest_engine.py`). Tested against real 360ONE.NS data twice: once under the original 4-tier trail (found+fixed the pl_pct/exit-price mismatch bug via manual trade inspection), and again after the user's 2026-10-11 trail-rule correction (7% arm / flat 6% trail) — two trades independently hand-verified against raw OHLC both times, see section 7.
7. ~~GitHub Actions workflow for on-demand backtest execution~~ — DONE (`backtest.yml`). Used for the real single-stock validation run and the full-universe run (see Status below).
8. ~~Cloudflare Workers trigger relay~~ — SKIPPED (deliberate decision, not deferred-by-blocker). GitHub's own Actions tab has a native "Run workflow" button that takes `workflow_dispatch` inputs directly — since this is a personal tool (not a multi-user product needing public self-service triggering), the two-tab flow (Strategy Builder → Copy Params as JSON → paste into GitHub's native trigger UI) is good enough and avoids a whole extra account/service/maintenance surface. Revisit only if one-click-from-the-page convenience becomes worth it later. Separately, GitHub's native `schedule:` cron trigger remains available for recurring automated runs (a different use case than manual parameter exploration) without needing this relay either. (Note: README.md's stack list still named Cloudflare Workers as live infra — corrected alongside this update, since that line was stale.)
9. ~~Strategy Builder UI~~ — DONE (`STRATEGY BUILDER` tab: full 14-param form + trailing-stop checkpoint editor), params JSON verified end-to-end against the real Python engine (extracted live from the rendered form, fed directly into `backtest_engine.py`, ran successfully)
   - "Run Backtest" button currently shows a clear not-yet-wired message (step 8 dependency) + a "Copy params as JSON" fallback for manual triggering in the meantime
   - ~~Results display~~ — DONE (see 10 below)
10. ~~Results display + run comparison~~ — DONE. `RESULTS` tab: run list (reads `results/index.json`, a same-origin file the workflow maintains alongside each result — avoids both GitHub API CORS/rate-limit complexity and the separate-branch pattern checkpoints needed), checkbox selection up to 4 runs, equity curve (single run: realized solid + combined dashed; multi-run: one combined-P&L line per run, color-coded), full metrics comparison table, and a trade-by-trade table (single-run only). Tested end-to-end against the real first backtest run. One real robustness bug found+fixed: the equity chart's failure (ApexCharts CDN blocked in the sandboxed test environment) was cascading and silently preventing the metrics/trades tables — which have no such dependency — from rendering at all; each render step is now isolated with its own try/catch so one failure doesn't take down unrelated parts of the page.

### Also built, not originally itemized here
- Stock Detail page (DuckDB-WASM live parquet query, candlestick+EMA+RSI charts)
- Threshold Breach Analysis + 90%-range readout (the "Outlier Analysis" from section 9, simplified per direct user feedback from percentile-slider UX to plain fixed-threshold counts + a percentile-range sentence)
