# Backtester — Project Context for Claude

## What this is
This repo is the canonical home of **VJ TradeIQ** — read [`PLATFORM.md`](PLATFORM.md) first, every session, before anything else here. It has the strategy→repo→data→integration map and the doc-layering rule the rest of this file assumes. "VJ TradeIQ" is a UI/brand name only (used in the sidebar, docs, commit messages) — it does not rename this repo, any file, or any existing identifier.

Single-file `index.html` frontend hosted on GitHub Pages, GitHub Actions as the compute/scheduling layer, no hosted database. `vaijayanth/TradingJournal` is a frozen legacy sibling (no new strategy/data rules go there — see its own CLAUDE.md banner).

**Read before touching the relevant area:**
- [`PLATFORM.md`](PLATFORM.md) — always, first
- [`docs/STRATEGY_SST.md`](docs/STRATEGY_SST.md) / [`docs/STRATEGY_OPTIONS.md`](docs/STRATEGY_OPTIONS.md) / [`docs/STRATEGY_DIVIDEND.md`](docs/STRATEGY_DIVIDEND.md) — any trading rule question (entry/exit/sizing/adjustment logic). Each strategy's live variants are kept explicitly distinct in these files — don't assume "equity swing" or "options selling" means one single rule set, check which page/variant you're actually touching.
- [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) — any data source, schema, or precedence question (Holdings-vs-FIFO, Supertrend/RSI feed, expiry-date logic, broker position formats)
- [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) — any Apps Script URL, Sheet ID, or external connector
- [`docs/SPEC.md`](docs/SPEC.md) — any non-trivial engineering change. Full architecture and every design decision, including reversed ones, is recorded here. Nothing in this repo should contradict it; if a change requires deviating from it, the spec gets updated as part of that change, not left stale.

## Equity Trading Journal — sync tax with the legacy repo (CRITICAL)

This repo hosts a synced copy of the Equity Trading Journal feature. **This is a standing sync burden, not a design choice** — it exists only because `vaijayanth/TradingJournal` hasn't been sunset yet. Any fix made to one `index.html`'s Equity Journal section (search `EQ_LONGTERM_EXCLUDE`) must be mirrored to the other until cutover — do not let them drift, and do not "fix" this by investing further engineering into the sync itself; the real fix is finishing the sunset (see `PLATFORM.md`).

The Holdings-vs-FIFO precedence rule and corporate-action triage (split vs. transfer vs. merger) live in [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) — read that, not this section, for the actual rule.

**Read `docs/SPEC.md` section 21 before touching anything equity-journal/alerts/dashboard-related.** It's the full write-up of this feature's data model, the FIFO oversell bug and its fix (`unmatchedQty`), the Alerts page's exit/add-on categorization, the Dashboard's windowed-vs-all-time P&L split (`EQ_DASHBOARD_START_DATE`, currently `2026-06-01`) and why mixing those two scopes is a bug that has already shipped once, the Journal table's exact column order/date format, and the sector data pipeline (`scripts/fetch_equity_sectors.py` → `data/equity_sectors.json`). This feature is **backtester-only** beyond the Journal page itself — Equity Alerts and Equity Dashboard do not exist in `vaijayanth/TradingJournal`, don't add them there.

## Workflow requirement — mobile + desktop testing via subagents

**User's explicit instruction (2026-10-17, verbatim):** "next time i suggest any changes, you need to spin off agents to build on mobile and desktop and test as well."

Context: a mobile layout bug (two real CSS issues — missing `overflow-x` on scrollable tables, and a classic CSS-grid `min-width:auto` trap that let content stretch a grid track past the viewport) shipped and went unnoticed through several rounds of feature work on `index.html`, until the user hit it directly on a real phone. See `docs/SPEC.md` section 14 for the full incident and fix.

**Going forward, for any change touching `index.html` (or anything else user-facing):**
1. Make the change.
2. Dispatch parallel subagents via the Agent tool — at minimum one verifying the change at mobile viewports (375px and 320px, Playwright, against real production data pulled from `main` — not fixtures) and one verifying desktop (1400px+) — each checking for horizontal overflow (`document.documentElement.scrollWidth` vs `clientWidth`), console/page errors, and visual correctness via screenshots.
3. Only report the change complete once both have confirmed. A single inline pass at one viewport is not sufficient — that is exactly the gap that let the mobile bug ship unnoticed.

This applies even when the user's request doesn't mention mobile or testing — it's a standing requirement for this repo, not something to be asked for each time.

## Workflow requirement — confirm before building

**User's explicit instruction (2026-10-18, verbatim):** "first of all i didnt want you to go ahead and bulilt it without my permission. Going forward remember the context..my style is that you understand the requirements, confirm with me and ask questions and show me steps as what you want to do and then post my confirmation to build."

This happened after a direct request ("start from empty database for options") was reasonably but wrongly read as license to also design and build Excel-upload support unasked. Don't repeat that reading.

**For any non-trivial change (new feature, format/schema change, UI rework — not a one-line fix the user described exactly):**
1. Understand the request. Ask clarifying questions where there's real ambiguity (format, placement, scope, persistence model, etc.) rather than assuming.
2. State back, in plain steps, what you intend to build — before writing any code.
3. Wait for explicit go-ahead.
4. Only then build (and, per the rule above, verify via mobile+desktop subagents before reporting done).

A request to fix/change X is not blanket permission to also design and build Y, even if Y seems like a natural companion. If the user's message implies a follow-on decision (e.g. "upload Excel from now on" implies "add .xlsx support"), surface that as a numbered step to confirm, not as something already built when you report back.

## Repo & branch
- Repo: `vaijayanth/backtester`
- Single branch: `main` for code + generated JSON data, `backfill-checkpoints` for the raw OHLC parquet (kept separate — see `docs/SPEC.md` section 4 for why).
- No PRs — direct commits to `main` after local verification, matching the pattern used throughout this project's history (see `docs/SPEC.md` for the git-push-and-verify-via-GitHub-API discipline this repo follows; `raw.githubusercontent.com` caches aggressively and has misled verification before — always confirm via `gh api repos/vaijayanth/backtester/contents/<path>` size/sha, not a raw fetch).

## Scheduled automation already running
- `.github/workflows/daily_update.yml` — weekday cron, keeps OHLC data current and regenerates every dependent JSON. See `docs/SPEC.md` section 13.
- Do not add a second daily OHLC job or duplicate this pipeline — extend `scripts/update_ohlc.py` / this workflow instead.

## GitHub Pages deployment can silently stall (2026-10-30 incident)
Pages here runs in classic "deploy from branch: main / root" mode — no user workflow file deploys it; GitHub auto-manages an internal `pages-build-deployment` workflow (`dynamic/pages/pages-build-deployment`, not in `.github/workflows/`) that normally fires and completes within ~1 minute of any push to `main`.

Observed once: two consecutive pushes' deployment runs got stuck at `queued`/`waiting` for 10+ minutes, then a `cancel_workflow_run` + `rerun_workflow_run` via the GitHub API only cycled the run to `completed`/`cancelled` again — never to `success`. That workflow has no `workflow_dispatch` trigger, so it cannot be manually re-fired via `run_workflow`; the only way to force a genuinely fresh deployment attempt is a brand-new commit to `main` (a new run gets a new run_id, avoiding whatever state poisoned the stuck one).

**If a push's changes aren't showing live after a reasonable wait**: don't assume browser/CDN caching first — check `pages-build-deployment`'s most recent runs (`mcp__github__actions_list` `list_workflow_runs` with `resource_id` = that workflow's id, found via `list_workflows`) and compare the latest `conclusion: success` run's `head_sha` against the latest commit on `main`. If they differ, the deployment pipeline itself stalled — incognito/hard-refresh troubleshooting is a waste of time until a fresh push produces a run that actually reaches `success`.
