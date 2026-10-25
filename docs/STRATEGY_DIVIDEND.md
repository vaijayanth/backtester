# VJ TradeIQ — Dividend/Longterm Strategy (CANONICAL)

Signal-only — no trade logging, no position tracking, no Google Sheet. The page's job is to surface the entry signal; discipline (not re-adding within a week, actually tracking what was bought) is on the user, not the app.

| | |
|---|---|
| **Universe** | Fixed 29-stock dividend/longterm list (`DIV_UNIVERSE`): Bank of Baroda, BPCL, CDSL, Coal India, CAMS, GAIL, Gold BeES, HDFC AMC, HAL, Hindustan Zinc, ICICI Bank, Nifty IETF, Indian Hotels, IOC, Ircon, ITC, LIC, MCX, National Aluminium, NMDC, NSDL, ONGC, Petronet, PFC, PTC, REC, SBI, Tata Investment, TCS |
| **Entry signal** | RSI14 < 30 (`DIV_RSI_OVERSOLD`) — the only gating condition |
| **Suggested add** | ₹20,000 (`DIV_ADD_AMOUNT_RS`), max once per week per stock — tracking that cadence is the user's own discipline, the app does not enforce or remember it |
| **Supertrend** | Shown for context only — does **not** gate this entry signal. A stock can show RSI<30 and still appear in the entry list regardless of Supertrend color. |
| **Options leg** | Same lightweight `SELL CALL`/`SELL PUT` heuristic as Dhan Covered Call and IVP Screener — see `docs/STRATEGY_SST.md`'s "Shared Supertrend+RSI options-signal heuristic" and `docs/STRATEGY_OPTIONS.md`'s closing note. Not the full strangle strategy. |

No exit rule exists for this strategy as implemented — it is purely an accumulation/entry signal for a longterm/dividend-focused holding, not a system with its own exit logic.
