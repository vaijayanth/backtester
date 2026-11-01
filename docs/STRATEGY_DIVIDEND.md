# VJ TradeIQ — Dividend/Longterm Strategy (CANONICAL)

Signal-only — no trade logging, no position tracking, no Google Sheet. The page's job is to surface the entry signal; discipline (not re-adding within a week, actually tracking what was bought) is on the user, not the app.

| | |
|---|---|
| **Universe** | Fixed 29-stock dividend/longterm list (`DIV_UNIVERSE`): Bank of Baroda, BPCL, CDSL, Coal India, CAMS, GAIL, Gold BeES, HDFC AMC, HAL, Hindustan Zinc, ICICI Bank, Nifty IETF, Indian Hotels, IOC, Ircon, ITC, LIC, MCX, National Aluminium, NMDC, NSDL, ONGC, Petronet, PFC, PTC, REC, SBI, Tata Investment, TCS |
| **Entry signal** | Any ONE of three rules lists a stock (OR, not AND): **RSI14 < 25** (`DIV_ENTRY_RSI_THRESHOLD`), **Supertrend flipped to green THIS WEEK and is still green** — the flip's date (`recent_bullish_flip_date`) must fall on/after the Monday of the week of the latest close (`divWeekStart`); a flip from the previous week's Friday is not a signal (changed 2026-11-01 from a rolling 6-trading-day window, which let last Friday's flips through; `DIV_FLIP_WINDOW_DAYS` is now only a fallback for a feed entry with no flip date). RSI<25 and the 52-week low stay latest-close-only checks, or **today's close is the trailing 52-week low** (`pct_from_low <= 0`, close-based, 252 trading days). RSI 30 (`DIV_RSI_OVERSOLD`) is NOT the entry rule — it only drives the SELL PUT options leg. (An earlier version of this file said 30 for entry; the code has used 25 since the entry/options thresholds were split.) |
| **Cadence** | One buy a week, placed Saturday or Sunday from Friday's close. The page header shows a "Buy this weekend" order slip (symbol × quantity) whenever any stock qualifies on the latest data, and warns instead if the feed is 2+ trading days behind. |
| **Suggested add** | ₹20,000 (`DIV_ADD_AMOUNT_RS`); quantity = floor(20,000 / CMP), min 1. Max once per week per stock — tracking that cadence is the user's own discipline, the app does not enforce or remember it |
| **Supertrend** | Shown for context only — does **not** gate this entry signal. A stock can show RSI<30 and still appear in the entry list regardless of Supertrend color. |
| **Options leg** | Same lightweight `SELL CALL`/`SELL PUT` heuristic as Dhan Covered Call and IVP Screener — see `docs/STRATEGY_SST.md`'s "Shared Supertrend+RSI options-signal heuristic" and `docs/STRATEGY_OPTIONS.md`'s closing note. Not the full strangle strategy. |

No exit rule exists for this strategy as implemented — it is purely an accumulation/entry signal for a longterm/dividend-focused holding, not a system with its own exit logic.
