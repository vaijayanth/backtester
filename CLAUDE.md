# Backtester — Project Context for Claude

## What this is
Shared backtesting engine + manual strategy builder, serving both the SST equity swing system (TradingJournal) and the deferred options strangle strategy (Options Terminal). Single-file `index.html` frontend hosted on GitHub Pages, GitHub Actions as the compute/scheduling layer, no hosted database.

Full architecture and every design decision — including the ones that reverse or correct an earlier decision — is recorded in [`docs/SPEC.md`](docs/SPEC.md). Read it before making a non-trivial change; nothing in this repo should contradict it, and if a change requires deviating from it, the spec gets updated as part of that change, not left stale.

## Equity Trading Journal — Reconciliation Rule (CRITICAL)

This repo hosts a synced copy of the Equity Trading Journal feature (FIFO positions computed from a Zerodha Tradebook, reconciled against an uploaded Holdings snapshot). **On any mismatch between FIFO and Holdings, Holdings is always the final reference** — it's what Zerodha actually shows the user holds right now. The full rule (how to tell a split from an off-market transfer from a merger, and which `index.html` structures — `EQ_SPLIT_ADJUSTMENTS` / `EQ_MANUAL_FILLS` — to use for each) is documented in `vaijayanth/TradingJournal`'s `CLAUDE.md` under "Equity Trading Journal — Reconciliation Rule". Any fix made there must be mirrored here, and vice versa — the two `index.html` Equity Journal sections (search `EQ_LONGTERM_EXCLUDE`) must stay in sync.

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
