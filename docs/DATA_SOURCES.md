# VJ TradeIQ — Data Sources Registry (CANONICAL)

What data feeds this platform, its schema, how fresh it is, and — the part that actually prevents bad trading decisions — **which source wins when two disagree.** Canonical copy lives here, in `backtester`.

## Precedence rules (read this before reconciling anything)

> **Holdings beats FIFO, always.** On any mismatch between FIFO (computed from Tradebook fills) and an uploaded Holdings snapshot, Holdings is the final reference — it's what the broker actually shows the user holds right now. FIFO is a derived computation that can be wrong for reasons a Tradebook alone can never reveal (splits/demergers/mergers not yet modeled, off-market transfers, gifts, pledge invocations). The Open Positions table **auto-corrects** to Holdings' numbers rather than just flagging the mismatch — see `docs/SPEC.md` §21 and `EQ_SPLIT_ADJUSTMENTS`/`EQ_MANUAL_FILLS` in `index.html`. Full triage rules (how to tell a split from a transfer from a merger) are in `vaijayanth/TradingJournal`'s CLAUDE.md under "Equity Trading Journal — Data Model" — not yet migrated here, read there for now.

> **A Tradebook upload is a full replace, not an append**, for both the SST journal and the Equity Trading Journal — every export covers complete history from account inception. Re-introducing append+dedupe logic would silently corrupt data the moment a future upload is ever a partial window; don't do it unless the user explicitly says a future upload will be partial.

> **supertrend.json / supertrend_full.json / market_screens*.json are generated once daily** by `.github/workflows/daily_update.yml` (weekday cron). `generated_at` in each file is the only freshness signal — if it's not surfaced on a page that reads one of these, that's a gap, not a feature (several pages already show "data as of" timestamps derived from this; follow that pattern for any new consumer).

> **Null means "no data," never a fabricated default.** A stock with insufficient OHLC history gets `color: null`/`above_ema50: null` from `supertrend.py`, not a false "red"/"below." Every consumer must render that as "—"/"not in price universe," never as a 0%-P&L or a false signal. This has already been a bug once (silently treating missing EMA50 as "below").

## Supertrend + RSI feed (`supertrend.json`, `supertrend_full.json`, `market_screens.json`, `market_screens_full.json`)

Built by `scripts/supertrend.py` (`--universe fno` → `supertrend.json`, `--universe full` → `supertrend_full.json`) and `scripts/market_screens.py`. Standard Supertrend(10,3): ATR period 10, multiplier 3.0, Wilder smoothing.

Per-entry schema (as of 2026-10-24, `_recent_flip` fields added this date — not yet in the committed JSON on `main` until the next scheduled pipeline run regenerates it):
```
{
  symbol, close, color: "green"|"red"|null, flipped_today: bool, above_ema50: bool|null, date,
  recent_bullish_flip_days_ago, recent_bullish_flip_date,   // bullish-only, 7-trading-day lookback, feeds the Equity Alerts re-entry signal
  recent_flip_days_ago, recent_flip_date, recent_flip_direction,  // either direction, 7-trading-day lookback (days_ago 0-6), feeds the "FLIP" column shown next to every SUPERTREND column app-wide
}
```
Lookback window is **inclusive 0–6 trading days, not 0–7** — a flip exactly 7 trading days ago reads as "no recent flip," same boundary for both the bullish-only and either-direction trackers.

**Symbol key convention**: entries carry the Yahoo Finance suffix (`.NS` or `.BO`). Every consumer strips it before use (`symLabel`/`eqStripNs`/etc. — search for `.replace(/\.(NS|BO)$/, '')`). `market_screens_full.json` also carries `week52_all` (52-week high data, used by the 52-Week High Forward Journal and Equity Alerts' near-52WH panel) and `rsi_all` in the same response — fetched once, not twice, per consumer.

**No shared fetch/cache layer** — `supertrend_full.json` (currently ~409KB) is independently fetched by at least 4 different page-init functions with zero sharing. Not currently a user-visible problem at today's data volume; flagged here so it doesn't get rediscovered as a surprise later. See this session's performance audit in `docs/SPEC.md` for the specifics if this ever needs fixing.

## F&O universe (`data/fno_universe.csv`)

213 NSE F&O stocks, column `symbol`, suffixed (`.NS`). **Symbols with hyphens/ampersands are stored verbatim, uppercase** — `BAJAJ-AUTO.NS`, `M&M.NS`, `GVT&D.NS`, `NAM-INDIA.NS`. Any code matching against this list must preserve these characters exactly; `.toUpperCase()` is safe (already uppercase) but never strip or re-encode `-`/`&`.

## NSE options expiry calendar (market convention, not app-specific — re-derive-proofing this)

As of the 2025 NSE circular, **monthly F&O contracts (both index and stock options) expire on the last Tuesday of the contract month**, shifted to the previous trading day if that Tuesday is a holiday. This replaced the older last-Thursday convention. Confirmed empirically this session: Nov 2026's last Tuesday (24th) is Guru Nanak Jayanti → actual expiry is Monday the 23rd; Oct 2026 (27th) and Dec 2026 (29th) land on plain Tuesdays with no shift.

**Zerodha's instrument code for a monthly contract is `SYMBOL + YY + MMM + STRIKE + CE|PE`** (e.g. `RELIANCE26NOV1360CE`) — no day digit, because individual stock options in India have no weekly contracts (only NIFTY/BANKNIFTY-class indices do). This differs from other formats you may see elsewhere (e.g. a day-specific `INFY 23 NOV 1000 PUT` style some other broker exports use) — do not assume one broker's format for another's export without checking.

Always verify an expiry date computation against a real, already-confirmed entry in the data (a position the user has pasted with a known correct date) before trusting a freshly-derived "last Tuesday" calculation for a far-future contract (next year+) — holiday calendars that far out aren't reliably published yet.

## Broker-specific open-position data (`Open Options for Dhan/Zerodha/Icici` sheet tabs)

Schema: `Symbol, Expiry (ISO date), Strike, Type (CALL|PUT), B/S (B|S), Qty (signed — negative = short), Avg Price, LTP, P&L Rs, % Change, As Of Date`. Entered by pasting a broker's raw position-table text into a Claude session for parsing (no automated upload path exists yet — see `docs/INTEGRATIONS.md`, "Broker data sources"). `Qty` sign is the short/long indicator throughout the app's JS — `B/S` is derived from it, not the other way around, so they can never disagree.

## Options Trade Analysis P&L data (`Option Trade Analysis DB` → `TRADES` tab)

Schema: `Symbol, Quantity, BuyValue, SellValue, RealizedPnl, OpenQuantity, OpenValue, UnrealizedPnl, Underlying, OptType, Strike, Expiry, ApproxExpiry`. Sourced from a Zerodha Console P&L statement (Reports → P&L statement → Segment: F&O → Excel), upserted by Symbol — safe to re-upload a wider period, nothing duplicates. `ApproxExpiry` is a flag, not a value — the actual `Expiry` field used to carry a last-Wednesday-of-month approximation from an older parsing pass; don't trust `Expiry` as exact without checking `ApproxExpiry` first (the real NSE rule is the last-Tuesday rule above, not Wednesday).
