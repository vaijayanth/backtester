# VJ TradeIQ — Platform Overview

**VJ TradeIQ is a UI/brand name only** — used in the app's sidebar, in docs, and in commit messages when referring to the product as a whole. It does not rename any repo, file, Google Sheet, Apps Script deployment, or existing identifier. The GitHub repo stays `vaijayanth/backtester`; this file is the platform's front door inside that repo.

**Status (2026-10-25):** `backtester` is the canonical home for VJ TradeIQ going forward. `vaijayanth/TradingJournal` is frozen — still running daily, not getting new strategy/data rules — pending a sunset whose trigger is not yet decided (deliberately deferred; don't infer or propose one). See `TradingJournal/CLAUDE.md`'s banner for the freeze terms.

**Where this is headed:** a comprehensive trading platform — swing equity (SST), options selling (strangles across 3 brokers), and dividend/longterm investing today, with room to grow into a genuinely professional-grade platform (live broker APIs, real-time position monitoring, options Greeks, backtested strategy iteration) rather than a personal dashboard. Read that ambition into every doc below — these aren't just notes for today's feature, they're the platform's institutional memory.

## Strategy → Repo → Data → Integration

| Strategy | Status | Primary page(s) in `index.html` | Data source | Integration |
|---|---|---|---|---|
| SST Equity Swing | Live (also on legacy TradingJournal) | Equity Trading Journal, Equity Alerts, Equity Dashboard | Zerodha Tradebook/Holdings upload (manual), `supertrend_full.json`, `market_screens_full.json` | Equity Trade Analysis DB (Apps Script) |
| Options Selling (strangles) | Live, 3 brokers | Open Positions, Zerodha Option Trade Analysis | Zerodha/Dhan/Icici position dumps (manual paste → parsed), `supertrend.json`, `market_screens.json` | Option Trade Analysis DB (Apps Script) |
| Dividend/Longterm | Live, signal-only | ICICI Dividend Longterm | Fixed 29-stock universe, `supertrend_full.json`/`market_screens_full.json` | None (no logging, no sheet) |
| Dhan Covered Call | Live, signal + log | Dhan Options & Covered Call | Fixed 10-stock universe, same Supertrend/RSI feed | DHCC_TRADES tab (shares 52DH Forward Journal's sheet) |
| 52-Week High Forward Journal | Live | 52 Week High Forward Journal | `market_screens_full.json`, same Supertrend feed | 252DH Forward Journal Apps Script |
| IVP Options Screener | Live, entry-screening only | IVP Screener | `options_snapshot.json`, `supertrend.json`, `market_screens.json`, IVP Google Sheet | IVP Screener DB (Apps Script) |
| Backtesting Engine | Live | Strategy Builder, Results, Universe, Backfill, Stock Detail, Stock Threshold Analysis | Historical OHLC parquet (`checkpoints/`) | None — local compute, GitHub Actions |
| Options Terminal (volatility selling, IV-driven) | Planning only, no code | — | IVP/earnings/OI data (planned) | Planned, see `docs/SPEC.md` §3 |

Full detail on each data source's schema and trust rules: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).
Full detail on every external URL/Sheet ID/credential surface: [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md).
Engineering architecture and the full decision history: [`docs/SPEC.md`](docs/SPEC.md).
Strategy rules (entry/exit/sizing/adjustment logic) per strategy: `docs/STRATEGY_*.md` — **not yet extracted from prose; planned next pass, not done as of this file's creation.** Until then, the authoritative strategy text lives in `vaijayanth/TradingJournal`'s `CLAUDE.md` (SST + Options sections) — read it there, don't assume it's been duplicated here yet.

## Doc layering rule (why these files exist, not just what)

| File | Loaded | Job | Changes when |
|---|---|---|---|
| `CLAUDE.md` | Every session, automatically | Map + standing workflow rules only | Rarely — a new standing rule, not a feature |
| `PLATFORM.md` (this file) | On pointer from CLAUDE.md | Platform-level orientation | A strategy/repo/integration is added or a status changes |
| `docs/STRATEGY_*.md` | On pointer, when touching that strategy | Entry/exit/sizing/risk rules — the "why" | A strategy rule is deliberately revised |
| `docs/DATA_SOURCES.md` | On pointer, when touching data | Schemas, refresh cadence, precedence rules | A data source is added/changed, or a precedence conflict is resolved |
| `docs/INTEGRATIONS.md` | On pointer, when touching an integration | URLs, Sheet IDs, auth surfaces | A new broker/sheet/API is wired in |
| `docs/SPEC.md` | On pointer, for non-trivial engineering changes | Architecture + full decision history | Every non-trivial build, append-only |
| `docs/CHANGELOG.md` | Rarely — only "when did X change" questions | Dated session log | Every session, mechanically |

Never duplicate content across these — one fact lives in exactly one file, everything else points to it.
