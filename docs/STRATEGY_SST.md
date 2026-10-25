# VJ TradeIQ — SST Equity Swing Strategy (CANONICAL)

SST = Sharegenius Swing Trading, a high-win-rate momentum system, fundamentally different from Minervini/O'Neil fixed-stop systems. **Read this before suggesting anything about any equity-swing page** — the instinct to "cut losses faster" or flag a 30-40% win rate as bad is wrong for this system by design, not an oversight.

**There are three live variants of this system in the app, deliberately different from each other.** They get conflated easily because they share a family resemblance (trend-filter entry, trailing exit, add-on-the-loser mechanic) — they are NOT the same rule with different numbers. Know which page you're on before applying a rule.

## Core philosophy (applies to all three variants)
- **Target ≥70% win rate** — high-WR system, not a low-WR large-winner system. 30-40% signals a real problem, not noise.
- **No hard stop loss on the loss side** — losses are held, not cut, by design. The only mechanisms managing loss-side risk are diversification and the add-on rule.
- **True performance = realised + unrealised combined.** Closed-only P&L looks misleadingly bad because losers sit open. Never judge system health from closed trades alone.
- **What NOT to suggest**: cutting losses fast, stop-loss slippage metrics, a fixed position-count limit as a problem, loss-streak alerts (a held loser isn't a "streak"), R-multiple analysis (no fixed risk-per-trade concept here).

## Variant 1 — Original SST (the live system, TradingJournal / Equity Trading Journal pages)

This is where real capital actually runs. Canonical rule text lives in `index.html`'s `STRAT_DEFAULT_RULES` constant — treat this doc as a summary, that constant as the source of truth for exact wording shown in-app.

| | |
|---|---|
| **Universe filter** | Above 200 EMA AND 50 EMA |
| **Entry signal** | Stock touches 21-Day High (21DH) — GTT order placed at that price, triggers automatically |
| **Setup types** | Breakout, Pullback, 21 EMA Pullback, 10 EMA Pullback (discretionary labels, not separately backtested — see "Entry signal scope" below) |
| **In profit** | Hold. At 6% profit, trailing SL activates (GTT set 6% below CMP). At 7%+, raise further. No fixed profit target — hold indefinitely while trend/fundamentals intact. |
| **In loss, above 200+50 EMA** | Add 1/10 qty via new GTT at 21DH. Max 1 add-on per week **per stock** (not portfolio-wide). Trigger: loss > −7%. |
| **In loss, below 50 EMA** | Hold. Do nothing. Wait for structure to return. No exit. |
| **Exit** | Only when trailing GTT SL fires, or genuine fundamental deterioration (earnings collapse, fraud, sector disruption) — never on price alone while above 50 EMA |
| **Position sizing** | ₹20,000 base, +₹5,000 every 100 closed trades, capped at ₹1,00,000 (16 tiers) |
| **Max open positions** | 200 |
| **Initial capital** | ₹50,00,000 |

**Health checks for this variant**: win rate (closed) ≥70%, losses held not cut (avg loss hold time should be long), add-ons taken correctly (above 200+50 EMA only), combined P&L positive, GTT trailing SLs set on all profitable positions. Full metric calibration table (color thresholds, which cards are "expected negative," equity curve construction) is in `vaijayanth/TradingJournal`'s CLAUDE.md under "Performance Tab — Calibrated Metric Rules" — not yet migrated here.

## Variant 2 — 52-Week High Forward Journal (`backtester`, `#page-dh252`)

A **different, simpler, harder-exit** variant — real trades, logged by hand, run alongside the backtest comparison on the same page.

| | |
|---|---|
| **Universe filter** | **None** — no EMA filter, unlike the original SST |
| **Entry signal** | Close at/above the prior 52-week high (252 trading days), universe-wide, excludes already-held symbols |
| **Exit** | **Full exit on Supertrend(10,3) turning red, regardless of P&L** — this is a hard rule, not "hold in loss" like the original. Deliberately the variant tested against the backtest engine (see `docs/SPEC.md` §7). |
| **Add-on** | Close ≤6% below weighted-average cost (`DH252_ADD_TRIGGER_PCT = -6`), Supertrend green, ≥7 calendar days since last fill on that position. Add 10% of qty (`DH252_ADD_FRACTION`). |
| **Position sizing** | ₹1,000 base (`DH252_POSITION_SIZE_RS`), +₹1,000 every 100 closed trades (`DH252_POSITION_INCREMENT_RS`/`_TRADES`) — note the increment is ₹1k not ₹5k, a different tier shape than the original |
| **Initial capital** | ₹20,00,000 (`DH252_INITIAL_CAPITAL_RS`) |

**Backtest reference numbers** (for comparing live performance against, not a target): avg P&L 8.45%, win rate 42.05%, 5,203 closed trades, CAGR 34.29% over the full 10yr window (`DH252_BACKTEST_*` constants). Note per `docs/SPEC.md` §7: under this exit rule, a closed trade can structurally never show a loss (the only exit path is the profit mechanism) — a 100% win rate on closed trades here is guaranteed by construction, not a performance signal. The real signal is combined realised + open-position MTM.

## Variant 3 — Dhan Covered Call, equity leg (`backtester`, `#page-dhcc`)

Fixed 10-stock universe (`DHCC_UNIVERSE`: CGPOWER, MCX, BSE, SHRIRAMFIN, DIXON, CUMMINSIND, HCLTECH, INFY, BANKINDIA, BPCL). Combines an equity leg (below) with an options leg — see `docs/STRATEGY_OPTIONS.md` for the options side.

| | |
|---|---|
| **Entry signal** | Supertrend(10,3) daily green, not already held |
| **Exit** | Supertrend red **only if in profit** — red while still in loss = hold (closer to the original SST's "never exit on pure loss" than Variant 2's hard exit) |
| **Add-on** | Supertrend green while in loss → add 10% of qty, max once/week (`DHCC_ADD_FRACTION`, `DHCC_ADD_COOLDOWN_DAYS`) |
| **Position sizing** | No auto-sizing constant in code — entered manually per trade |
| **Options leg stoploss** | 3× premium collected, via the user's own broker GTT — outside this app entirely |

## Shared Supertrend+RSI options-signal heuristic (dhcc, dividend, IVP pages)

All three of these pages also show a **SELL CALL / SELL PUT** column that is a lightweight screening heuristic, not the real options entry rule (that's in `docs/STRATEGY_OPTIONS.md`): `SELL CALL when Supertrend red OR RSI14>70`, `SELL PUT when Supertrend green OR RSI14<30`. Informational only — strike/expiry/qty/stoploss execution is manual, via the user's own broker GTT.

## Still to migrate from TradingJournal's CLAUDE.md (not done in this pass)
- Full column mapping / `DEFAULT_CONFIG` reference (Flag, Stock, Open/Close Date, etc.) — operational detail specific to the legacy Sheet schema, lower priority since `backtester`'s own Equity Trading Journal uses a different (FIFO-from-Tradebook) data model, see `docs/DATA_SOURCES.md`
- The full dated "Performance Tab — Calibrated Metric Rules" section (Win Rate/PF/Expectancy color thresholds, footnote language) — worth pulling over once the Performance tab itself gets rebuilt here, not before
