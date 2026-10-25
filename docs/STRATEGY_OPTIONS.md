# VJ TradeIQ — Options Selling Strategy (CANONICAL)

**Migration note:** this is the full options-selling strategy as the user described it, originally written down under a planned-but-unbuilt "Options Terminal" section in `vaijayanth/TradingJournal`'s CLAUDE.md. That dedicated terminal still doesn't exist as a purpose-built tool, but the strategy itself is live and real — it's the one behind the actual Zerodha/Dhan/Icici positions tracked on `backtester`'s Open Positions and Option Trade Analysis pages. Don't mistake "Terminal not built" for "strategy not real."

A simpler, unrelated heuristic (`SELL CALL`/`SELL PUT` columns shown on the Dhan Covered Call, Dividend Longterm, and IVP Screener pages) is **not** this strategy — see the note at the bottom.

## Core Identity
- **Pure option seller** — every trade viewed from the selling side. Option writing is a probability game, not a prediction game; the "predict then pray/hope" cycle belongs to option buyers.
- **Probabilistic mindset**, not prediction.
- **"Profit is inversely proportional to ego"** — nimble adjustments, no fixation on a specific trade. Biggest losses come from unyielding responses; constant churning/flipping isn't the fix either — adjustments are driven by revised risk assessment, not emotion.
- **Consistent low-return simple setups beat high-return "bets."** Diversification (sector + strategy type) is the mechanism that makes this work, not stop-losses or defined-risk spreads.
- No revenge trades, no overtrading, no fixation on one name.
- **Riding profits is the hard part** — especially when a trade turns from a big loss back to green. Needs a fresh assessment each time: keep riding, or book the (possibly small) profit and move on.

## Default Trade Structure — Bidirectional Strangle, naked both legs

**Not a defined-risk spread.** Both CE and PE legs are sold naked — no bought hedge leg anywhere in this strategy. Downside protection comes from adjustment technique (strike rolling, new strangles, multi-platform hedging — see Adjustment Strategies below), not a pre-built spread structure.

- **Bias**: sell CE first, then add PE based on subsequent movement/premium/breakeven — not always simultaneous.
- **CE side primary**: call premiums are generally richer for a given breakeven range, so CE is the default first leg.
- **Bidirectional (both legs on) ~80% of the time.** Pure single-side ~20%.
- **CE/PE ratio**: majority CE-heavy (ratio >1, e.g. 5CE:4PE, 3CE:2PE); occasionally 1:1.
- **PE-first/PE-only**: rare, reserved for a stock with a clear strong support level and a news-driven downside break already priced in, and only on stocks with real trading history.
- **Breakeven range target**: >10% on each side.
- **Profit target on margin**: ~10-15% per expiry cycle.
- **Other variants**: naked single-leg selling on breakout stocks; straddle trades specifically for IV/premium crush around earnings (see below) — distinct from the default strangle.

## Entry Rules
- **Primary signal**: IVP (IV Percentile) > 70% — fat premium, market overpaying for fear. Screened via Sensibull/IVP Screener page.
- **RSI modifier**: RSI>70 → increase CE lots, sell further OTM (stock extended). RSI<30 → widen PE side, move toward equal ratio. RSI 40-60 → standard CE-biased strangle.
- **Timing**: mid-month entry for next month's expiry, subject to liquidity.
- **Never enter expiry week** — gamma risk isn't worth the residual premium.
- **Strike selection**: OI concentration = support/resistance. Sell CE above max CE OI, PE below max PE OI. Ichimoku used alongside OI for a higher-level read (harder when the stock is at ATH with no overhead resistance to read).
- **Consolidation rule**: for ATH/breakout stocks, wait 4-5 days of sideways before entering — selling into a fresh breakout without consolidation is a trap risk (also higher reward, acknowledged tradeoff).

## Universe
- **Preferred sectors**: Steel, Pharma, NBFC, Hospitals, Auto. **Less preferred**: IT, Banks (SBI ok), Oil & Gas, PSUs, wild midcaps.
- **Liquidity**: mostly Nifty50, rarely beyond. Prefer "boring" stocks — stable names with temporarily elevated IV, avoid flashy/wild movers.
- Full tiered watchlist (Core/Secondary/Situational per sector, 19 stocks + 4 situational) and the explicit avoid-list (Adani group, Zomato/Nykaa, ONGC/IOC, Yes Bank, sub-₹20k-crore-mcap, Reliance) — see the table at the bottom of this file.

## Portfolio Rules
- Minimum 4 of 5 core sectors active per expiry cycle. Typical 5-10 stocks per cycle.
- Neutral is the default stance — directional bias needs strong conviction.
- Diversify across both sector AND strategy type (strangle / single-leg / straddle) — this diversification, not stop-losses, is the primary risk management.

## Exit Rules
- **Profit exit**: 60-70% of max premium collected (lower bar for infrequent/volatile names, higher for stable/frequently-traded ones).
- **Expiry week**: strictly exit or don't enter — one bad expiry-week event wiped 25% of a year's profit in 2 sessions.
- **Loss exit**: no fixed rule — see Adjustment Strategies.

## Earnings Season — Volatility Crush Trades
Enter a few days before quarterly results (a volatility-crush trade, not directional) — the stock's move mostly happens before the announcement, IV crush happens after, so premiums stay compressed even through a sharp post-results move. Stock-specific earnings sensitivity matters (e.g. Infosys vs TCS), not just sector. A spread-out results calendar across names also helps margin circulation. Entry timing: results before the 15th → current expiry; after the 15th → next month. RSI>70 + earnings within 5 days → single CE leg only (pure theta/vega play). RSI<30 + strong support + earnings → single PE leg, short duration.

## Adjustment Strategies (when breakeven is breached or approached)

Decision framework: *"Do I believe the stock will reverse, and do I have margin to wait?"* Managing a loss-making trade is explicitly the hardest part of this approach — there is no single clean answer, every case differs, and sometimes the only real options are booking the loss or flipping the position (CE↔PE).

**Adjustment trigger is CMP approaching/breaching the BREAKEVEN RANGE — not an individual strike going ITM.**

| | Reversal belief | Margin | Action | Cost |
|---|---|---|---|---|
| **A1 — Strike Roll** | Yes | Constrained | Close the ITM strike at a loss, open 2 lots of new OTM strikes to recover premium, add a counter lot on the other side to stay bidirectional | Near-zero net premium on the roll, margin requirement rises |
| **A2 — New Strangle** | Yes | Available | Keep original as-is, add a fresh wider strangle around new CMP for a wider breakeven | Significant additional margin blocked |
| **A3 — Book Loss and Exit** | No | — | Close all legs, accept the loss, free the margin. No revenge trade, no re-entry same stock/expiry | Realized loss |
| **A4 — Second Platform Hedge** | No, but want offset | — | Keep original on Platform A, open a new trade biased toward actual direction on Platform B. Track as one consolidated P&L across platforms | Two separate margin pools (a benefit, not a cost) |
| **A5 — Convert CE to Bear Call Spread** | — | — | *Unconfirmed in live practice* — buy a higher CE to cap upside loss, converting naked CE to a defined-risk spread. Not in the original strategy document; was added as a hypothetical. **Verify with the user before treating as real before building anything on it.** |

**Real example (A2+A4 combined)**: HDFCAMC broke out past its breakeven high (5090) around its ex-dividend date in late May; M2M loss hit −₹2.75L by 9-Jun as the stock ran to 5217. A new strangle opened on a separate platform (Angel) on 9-Jun with a higher CE-side breakeven (5432 vs original 5138), based on an OI-concentration read (heaviest OI at 5000 despite spot at 5200) plus Ichimoku, judging the move overextended vs its 9-day/26-day averages. The stock retraced; by 13-Jun the original position had recovered from −₹2.75L to just −₹7.5K, and the new-platform position was up ₹22.4K — net small profit of ~₹14K across both legs.

## What a future Options Terminal must do (4 decisions only, still unbuilt)
1. **Morning Scan**: high IVP + right sector + not expiry week + no imminent earnings conflict
2. **Trade Setup**: OI levels → breakeven calculator → CE/PE ratio → margin estimate
3. **Position Monitor**: live breakeven range health per strangle, MTM consolidated across platforms, exit alert at 60-70% profit, breach alert
4. **Expiry Close**: total premium collected, realized P&L, return on margin, what went wrong

Must NOT do: replicate a full options chain, show a Greeks dashboard, algo execution, or become a backtesting engine.

**Current state vs this plan**: the Open Positions page (consolidated 3-broker view with grouping and an Exit Signal panel) and Option Trade Analysis page already cover a meaningful slice of #3 (Position Monitor) and the realized-P&L side of #4. #1 (Morning Scan) and #2 (Trade Setup, breakeven calculator) remain unbuilt.

## Full Stock Watchlist (5 core sectors)

| Sector | Core | Secondary | Situational |
|---|---|---|---|
| Steel | JSW Steel, Tata Steel, NMDC | Hindalco, Vedanta | — |
| Pharma | Sun Pharma, Cipla, Divislab | Dr Reddy's | Aurobindo (earnings plays only) |
| NBFC | Bajaj Finserv, Bajaj Finance | ICICI Prudential Life, HDFC AMC, Muthoot Finance | — |
| Hospitals | Apollo Hospitals | Max Healthcare | — (thin sector, only 2 liquid F&O names — skip and run other 4 when both look unfavourable) |
| Auto | Tata Motors, Bajaj Auto, M&M | Maruti, Hero MotoCorp | — |

**Outside core sectors, situational only**: ITC (FMCG, high IVP non-results months), SBI (PSU Bank, user-confirmed acceptable), Infosys (IT, non-results months only), HUL (FMCG, very stable/low-beta/high-IVP only).

**Avoid**: Adani group (regulatory/news risk), Zomato/Nykaa (high beta, thin earnings history), ONGC/IOC (PSU + crude policy risk), Yes Bank (too volatile, thin options), any sub-₹20,000cr mcap (liquidity too thin), Reliance (too many moving factors, expensive options).

Total watchlist: 19 stocks across 5 sectors + 4 situational.

## Not this strategy: the lightweight SELL CALL/PUT heuristic

The Dhan Covered Call, Dividend Longterm, and IVP Screener pages each show a simple `SELL CALL`/`SELL PUT` signal column — `SELL CALL when Supertrend red OR RSI14>70`, `SELL PUT when Supertrend green OR RSI14<30`. This is a screening proxy for entry bias, informational only, with no OI-based strike selection, no breakeven calculation, no adjustment framework. It is not the strategy documented above — don't conflate the two when reading either page's code or docs. See `docs/STRATEGY_SST.md`'s "Shared Supertrend+RSI options-signal heuristic" section.
