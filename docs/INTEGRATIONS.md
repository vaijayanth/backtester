# VJ TradeIQ — Integrations Registry (CANONICAL)

This is the single source of truth for every external system VJ TradeIQ reads from or writes to — Apps Script URLs, Google Sheet IDs, MCP connectors. **Canonical copy lives here, in `backtester`.** `vaijayanth/TradingJournal`'s CLAUDE.md points here rather than carrying its own copy — do not let a second copy of this table accumulate there.

This file is credentials-adjacent. Every Apps Script URL below grants write access to real financial data to anyone who has it ("Anyone with the link" deployment). Treat it with the same care as a secret even though it's committed — don't paste these URLs into any public or shared channel beyond this repo, and never into a prompt/response outside this working context without reason.

## Apps Script Web Apps + their Google Sheets

| Feature | Apps Script `/exec` URL | Google Sheet (name · id) | Tab(s) | Used by |
|---|---|---|---|---|
| SST swing journal + F&O watchlist | `https://script.google.com/macros/s/AKfycbwh85dD1AlNlMFRk4M4A8yCA5YAs5gvnCIMgq6ixQUhdxmes-CqU3OYOdxtzDRLRgLq/exec` | "2026 Master Trade Book_14JAN" · `18EpaIzhZI_FKopRES6JvlHSTX2MUNrnnymIrpteRdyc` | `TRADES`, `NSEFO` | `vaijayanth/TradingJournal` only (legacy, frozen) |
| Equity Trading Journal (Tradebook FIFO + Holdings) | `EQ_SHEET_URL` = `https://script.google.com/macros/s/AKfycbyMNI8hj7lfWQLLEfcBYL3CgCFCdca7gJsOoaiR6KWC5vq_EiyyahYTyrRmrw0uAfk/exec` | "Equity Trade Analysis DB" · `1Zo1eXVX4yH2Wrv7r7f8Ugc6DL_234AWLxU5IlUtPn_c` | `FILLS`, `HOLDINGS` | Both repos (same sheet, same URL — kept in sync; see `docs/SPEC.md` §21) |
| Option Trade Analysis (P&L statement) + 3-broker Open Positions | `OT_SHEET_URL_DEFAULT` = `https://script.google.com/macros/s/AKfycbzCzgNLWexbsdEH0jpuMn4-wNIVphjfwPdGbJPCfhJtpPAxqBnkYmbcvwFgDpPc0AwQ/exec` | "Option Trade Analysis DB" · `1urXGRi73YyAjTEvXg7yHoRKCtUKfN0DXfOFxvEOTKkc` | `TRADES`, `Open Options for Dhan`, `Open Options for Zerodha`, `Open Options for Icici` | `backtester` only. `doGet(?sheet=<tab>)` routes to one of the 3 broker tabs (strict allowlist, verified no traversal path — see this session's audit), defaults to `TRADES` when omitted. `doPost` always targets `TRADES` regardless of any `sheet` field — the 3 broker tabs are read-only from the app's side, written directly in the Sheet. |
| IVP Screener (Sensibull snapshot) | `IVP_SHEET_URL` = `https://script.google.com/macros/s/AKfycbzJZOQ_o1-iK-DpqkvCky8tPSk4tuks1ZMefleZso_Uw4EilX_I9jUuvMxV3dGi_w2L/exec` | "IVP Screener DB" · `1K6SF4sPA_nS9W1AzYYh9uazSzBN26sa6ILi2xiWHxtc` | `IVP`, `IVP_META` | `backtester` only |
| Universe Additions (UI "Add Stock") | `UNIVERSE_ADDITIONS_URL` = `https://script.google.com/macros/s/AKfycbx0DhYyh9aQ74Bi-p6rVUEs_l6ExQD-G7yIss9c2MUkAWpaGF5vqicQb9W-azuEI1pM/exec` | "Backtester Universe Additions" · `1E-1M6gQPdIgxFQhY0oJkQMIMgAAtKcGiqNybyvmAD4s` | `ADDITIONS` | `backtester` only |
| 52DH Forward Journal (shares with Dhan Covered Call) | `DH252_SHEET_URL_DEFAULT` = `https://script.google.com/macros/s/AKfycbwBJDaJKO5eg2Df2eKwLCN-opkDtNe1ZmCg6YvqZzU5b1-XHZ8L7iSL4zc-iUnwajp6/exec` | "252DH Forward Journal" | `TRADES`, `DHCC_TRADES` | `backtester` only. v4+ supports `?sheet=` param, defaults to `TRADES`. |
| Universe source list (manual, no Apps Script) | — | "STStocklist" · `1tDRsk2B9udGRhirzwxrryc3HvtHhg6rwN8UTjeJlwAc` | — | Hand-edited, feeds `backtester`'s universe pipeline |

Each Apps Script's `.gs` source lives only in its bound Google Sheet's Extensions → Apps Script editor — **none are committed to any repo** (the deployed URL alone grants write access to real financial data). Redeploying requires Deploy → Manage deployments → **New version** — editing+saving alone does not update the live `/exec` URL. A working copy of the current deployed source is usually saved to the session scratchpad when it's modified — check there before assuming you need to ask the user to paste it again.

## MCP / Claude Code connectors used by this platform's maintenance sessions

| Connector | Purpose | Notes |
|---|---|---|
| Google Sheets (MCP) | Direct read/write to the Sheets above, from a Claude session, bypassing the Apps Script layer | Used for bulk data entry (e.g. pasting a broker's position dump) — the live app never uses this path, only maintenance sessions do |
| Google Drive (MCP) | Locating/sharing the Sheets above by name | — |
| GitHub (MCP) | All repo operations — commits, PRs, Actions | Direct commits to `main`, no PR workflow (see `CLAUDE.md`) |

## Broker data sources (manual entry, no live API yet)

No broker has a live programmatic connection as of this writing — Zerodha Kite Connect, Dhan API, and ICICI Breeze are all **unintegrated**. Every position/fill/holding currently enters the system via manual upload (Tradebook/Holdings CSV) or manual paste (options position dumps, parsed by Claude on request). This is the single biggest gap between "comprehensive dashboard" and "professional platform" — see `docs/DATA_SOURCES.md` for the per-broker data format notes that would matter the moment any of these go live.
