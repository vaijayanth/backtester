"""Compare 10-year NSE equity strategies on identical price history.

Variants:
  - 52-week high entry
  - Supertrend(10, 3) red-to-green entry

Both use the same user rules: exit next open when Supertrend is red and the
position is profitable at the signal close; average at -6% only while green,
adding ceil(10% of held quantity) no more than once per 7 calendar days.

Reads/writes the same canonical checkpoint set as backfill.py/update_ohlc.py
(checkpoints/raw/ — now a single ~10-year source of truth for every symbol,
see docs/SPEC.md's dated 2026-11-08 entry). A cache miss here fetches with
the same convention (period, auto_adjust, tz-naive) backfill.py uses, so
anything newly fetched here joins the one canonical dataset instead of a
second parallel copy.
"""

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from backfill import YEARS
from supertrend import ATR_PERIOD, MULTIPLIER, compute_ema, compute_supertrend

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "checkpoints" / "raw"
MANIFEST = ROOT / "checkpoints" / "manifest.csv"
OUT = ROOT / "results" / "strategy_comparison_10y.json"

STARTING_CAPITAL = 10_000_000
ENTRY_BUDGET = 100_000
MAX_POSITIONS = 200
ADD_LOSS_PCT = -6.0
ADD_FRACTION = 0.10
ADD_COOLDOWN_DAYS = 7
LOOKBACK_52W = 252


def symbols_to_fetch():
    manifest = pd.read_csv(MANIFEST)
    return sorted(manifest.loc[manifest["status"] == "VALIDATED", "symbol"].dropna().astype(str))


def fetch_history(symbol, retries=3):
    path = RAW_DIR / f"{symbol.replace('.', '_')}.parquet"
    if path.exists():
        try:
            frame = pd.read_parquet(path)
            if not frame.empty:
                return frame
        except Exception:
            pass
    last_error = None
    for attempt in range(retries):
        try:
            frame = yf.Ticker(symbol).history(period=f"{YEARS}y", auto_adjust=True)
            if frame is None or frame.empty:
                raise RuntimeError(f"empty {YEARS}-year history")
            frame.index = pd.to_datetime(frame.index).tz_localize(None)  # match canonical raw/ convention
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
            frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
            if frame.empty:
                raise RuntimeError("no complete OHLC rows")
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path)
            return frame
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    print(f"FAILED {symbol}: {last_error}", flush=True)
    return None


def load_or_fetch():
    symbols = symbols_to_fetch()
    data = {}
    failures = []
    for i, symbol in enumerate(symbols, 1):
        frame = fetch_history(symbol)
        if frame is not None:
            data[symbol] = frame
        else:
            failures.append(symbol)
        if i % 25 == 0 or i == len(symbols):
            print(f"Downloaded {i}/{len(symbols)} symbols; usable={len(data)} failed={len(failures)}", flush=True)
    return data, failures


def prepare(frame):
    highs = frame["High"].astype(float).tolist()
    lows = frame["Low"].astype(float).tolist()
    closes = frame["Close"].astype(float).tolist()
    opens = frame["Open"].astype(float).tolist()
    direction, _, _ = compute_supertrend(highs, lows, closes, ATR_PERIOD, MULTIPLIER)
    ema50 = compute_ema(closes, 50)
    # Match the repository's existing 52-week-high screen: use closing highs,
    # and treat a close within 1% of the previous 252-session close-high as a
    # signal. The earlier exploratory run used a stricter intraday-high break.
    prior_high = frame["Close"].rolling(LOOKBACK_52W).max().shift(1).tolist()
    rows = []
    for i, (date, _) in enumerate(frame.iterrows()):
        rows.append({
            "date": pd.Timestamp(date).normalize(),
            "open": opens[i], "high": highs[i], "low": lows[i], "close": closes[i],
            "direction": direction[i],
            "previous_direction": direction[i - 1] if i > 0 else None,
            # Align both entry methods to the same first eligible date; the
            # 52-week screen needs 252 completed bars before its first signal.
            "bullish_flip": i >= LOOKBACK_52W and i > 0 and direction[i - 1] == "down" and direction[i] == "up",
            "prior_52w_high": prior_high[i],
            "above_ema50": None if ema50[i] is None else closes[i] > ema50[i],
            "new_52w_high": i >= LOOKBACK_52W and pd.notna(prior_high[i]) and closes[i] >= prior_high[i] * 0.99,
        })
    return rows


def simulate(data, entry_type, average_down=True):
    bars_by_date = {}
    final_dates = {}
    first_dates = []
    for symbol, rows in data.items():
        final_dates[symbol] = rows[-1]["date"]
        first_dates.append(rows[0]["date"])
        for i, row in enumerate(rows):
            bars_by_date.setdefault(row["date"], {})[symbol] = (i, row)

    cash = float(STARTING_CAPITAL)
    positions, pending, latest_close = {}, {}, {}
    trades, skipped, fills = [], [], []
    equity_curve = []
    peak_deployed = 0.0

    for date in sorted(bars_by_date):
        today = bars_by_date[date]
        due = [(symbol, pending.pop(symbol)) for symbol in sorted(today) if symbol in pending]
        due.sort(key=lambda item: ({"exit": 0, "addon": 1, "entry": 2}[item[1]["action"]], item[0]))
        for symbol, order in due:
            row = today[symbol][1]
            price = row["open"]
            if not math.isfinite(price) or price <= 0:
                skipped.append({"symbol": symbol, "date": str(date.date()), "reason": "invalid next open"})
                continue
            if order["action"] == "exit":
                pos = positions.pop(symbol, None)
                if pos is None:
                    continue
                proceeds = price * pos["qty"]
                pnl = proceeds - pos["cost"]
                cash += proceeds
                fills.append({
                    "action": "exit", "symbol": symbol, "signal_date": order["signal_date"],
                    "fill_date": str(date.date()), "price": price, "qty": pos["qty"],
                    "notional_rs": proceeds, "position_cost_before_rs": pos["cost"],
                    "realized_pl_rs": pnl, "cash_after_rs": cash,
                    "signal_close": order["signal_close"], "signal_direction": order["signal_direction"],
                    "signal_pl_pct": order["signal_pl_pct"],
                })
                trades.append({
                    "symbol": symbol, "entry_date": pos["entry_date"], "exit_date": str(date.date()),
                    "entry_signal_date": pos["entry_signal_date"], "exit_signal_date": order["signal_date"],
                    "avg_entry": round(pos["cost"] / pos["qty"], 4), "exit_price": round(price, 4),
                    "qty": pos["qty"], "pl_rs": round(pnl, 2),
                    "pl_pct": round(pnl / pos["cost"] * 100, 2),
                    "hold_days": (date - pd.Timestamp(pos["entry_date"])).days,
                    "addon_count": pos["addon_count"],
                })
            elif order["action"] == "entry":
                if symbol in positions:
                    continue
                if len(positions) >= MAX_POSITIONS:
                    skipped.append({"symbol": symbol, "date": str(date.date()), "reason": "position cap"})
                    continue
                qty = int(ENTRY_BUDGET // price)
                cost = qty * price
                if qty < 1 or cost > cash:
                    skipped.append({"symbol": symbol, "date": str(date.date()), "reason": "insufficient cash entry"})
                    continue
                cash -= cost
                positions[symbol] = {
                    "entry_signal_date": order["signal_date"], "entry_date": str(date.date()),
                    "qty": qty, "cost": cost, "last_buy_date": date, "addon_count": 0,
                }
                fills.append({
                    "action": "entry", "symbol": symbol, "signal_date": order["signal_date"],
                    "fill_date": str(date.date()), "price": price, "qty": qty,
                    "notional_rs": cost, "cash_after_rs": cash,
                    "signal_close": order["signal_close"], "entry_rule": order["entry_rule"],
                    "signal_direction": order["signal_direction"],
                    "previous_direction": order["previous_direction"],
                    "prior_52w_high": order.get("prior_52w_high"),
                })
            else:
                pos = positions.get(symbol)
                if pos is None:
                    continue
                qty = max(1, math.ceil(pos["qty"] * ADD_FRACTION))
                cost = qty * price
                if cost > cash:
                    skipped.append({"symbol": symbol, "date": str(date.date()), "reason": "insufficient cash add"})
                    continue
                pos["qty"] += qty
                pos["cost"] += cost
                pos["last_buy_date"] = date
                pos["addon_count"] += 1
                cash -= cost
                fills.append({
                    "action": "addon", "symbol": symbol, "signal_date": order["signal_date"],
                    "fill_date": str(date.date()), "price": price, "qty": qty,
                    "notional_rs": cost, "cash_after_rs": cash,
                    "signal_close": order["signal_close"], "signal_direction": order["signal_direction"],
                    "signal_pl_pct": order["signal_pl_pct"], "days_since_last_buy": order["days_since_last_buy"],
                })

        for symbol in sorted(today):
            _, row = today[symbol]
            latest_close[symbol] = row["close"]
            pos = positions.get(symbol)
            if symbol in pending:
                continue
            if pos:
                pnl_pct = (row["close"] * pos["qty"] - pos["cost"]) / pos["cost"] * 100
                if row["direction"] == "down" and pnl_pct > 0:
                    pending[symbol] = {"action": "exit", "signal_date": str(date.date()),
                                       "signal_close": row["close"], "signal_direction": row["direction"],
                                       "signal_pl_pct": round(pnl_pct, 6)}
                elif average_down and row["direction"] == "up" and pnl_pct <= ADD_LOSS_PCT:
                    if (date - pos["last_buy_date"]).days >= ADD_COOLDOWN_DAYS:
                        pending[symbol] = {"action": "addon", "signal_date": str(date.date()),
                                           "signal_close": row["close"], "signal_direction": row["direction"],
                                           "signal_pl_pct": round(pnl_pct, 6),
                                           "days_since_last_buy": (date - pos["last_buy_date"]).days}
            else:
                signal = row["bullish_flip"] if entry_type == "supertrend_flip" else row["new_52w_high"]
                if signal:
                    pending[symbol] = {"action": "entry", "signal_date": str(date.date()),
                                       "signal_close": row["close"],
                                       "entry_rule": entry_type, "signal_direction": row["direction"],
                                       "previous_direction": row["previous_direction"],
                                       "prior_52w_high": row["prior_52w_high"]}

        deployed = sum(pos["cost"] for pos in positions.values())
        peak_deployed = max(peak_deployed, deployed)
        equity = cash + sum(pos["qty"] * latest_close.get(symbol, pos["cost"] / pos["qty"]) for symbol, pos in positions.items())
        unrealized = equity - cash - sum(pos["cost"] for pos in positions.values())
        equity_curve.append({
            "date": str(date.date()), "cash_rs": round(cash, 2), "equity_rs": round(equity, 2),
            "deployed_rs": round(deployed, 2), "unrealized_pl_rs": round(unrealized, 2),
            "closed_trades": len(trades), "open_positions": len(positions),
        })

    open_positions = []
    for symbol, pos in sorted(positions.items()):
        mark = latest_close.get(symbol, pos["cost"] / pos["qty"])
        open_positions.append({
            "symbol": symbol, "entry_date": pos["entry_date"], "avg_entry": round(pos["cost"] / pos["qty"], 4),
            "last_close": round(mark, 4), "qty": pos["qty"], "addon_count": pos["addon_count"],
            "unrealized_pl_rs": round(mark * pos["qty"] - pos["cost"], 2),
        })
    wins = [t for t in trades if t["pl_rs"] > 0]
    losses = [t for t in trades if t["pl_rs"] < 0]
    equity_values = [p["equity_rs"] for p in equity_curve]
    peak = STARTING_CAPITAL
    max_dd = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100)
    end_value = equity_values[-1] if equity_values else STARTING_CAPITAL
    duration_days = max((max(final_dates.values()) - min(first_dates)).days, 1)
    cagr = (end_value / STARTING_CAPITAL) ** (365.25 / duration_days) - 1 if end_value > 0 else None
    gross_loss = abs(sum(t["pl_rs"] for t in losses))
    gross_win = sum(t["pl_rs"] for t in wins)
    return {
        "entry_type": entry_type,
        "average_down_enabled": average_down,
        "universe_size": len(data),
        "period": {"start": str(min(first_dates).date()), "end": str(max(final_dates.values()).date())},
        "metrics": {
            "initial_capital_rs": STARTING_CAPITAL, "ending_equity_rs": round(end_value, 2),
            "total_return_pct": round((end_value / STARTING_CAPITAL - 1) * 100, 2),
            "cagr_pct": round(cagr * 100, 2) if cagr is not None else None,
            "realized_pl_rs": round(sum(t["pl_rs"] for t in trades), 2),
            "unrealized_pl_rs": round(sum(p["unrealized_pl_rs"] for p in open_positions), 2),
            "closed_trades": len(trades),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "max_drawdown_pct": round(max_dd, 2),
            "average_hold_days": round(sum(t["hold_days"] for t in trades) / len(trades), 1) if trades else None,
            "open_positions": len(open_positions), "cash_rs": round(cash, 2),
            "peak_deployed_rs": round(peak_deployed, 2), "skipped_orders": len(skipped),
        },
        "trades": trades, "fills": fills, "open_positions_detail": open_positions, "equity_curve": equity_curve,
        "skipped_orders": skipped,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    data_frames, failures = load_or_fetch()
    prepared = {symbol: prepare(frame) for symbol, frame in data_frames.items() if len(frame) >= 20}
    if not prepared:
        raise RuntimeError("No 10-year price histories are available.")
    print(f"Running four strategy variants over {len(prepared)} stocks", flush=True)
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Yahoo Finance auto-adjusted OHLC; current validated universe (survivorship bias applies)",
        "rules": {
            "starting_capital_rs": STARTING_CAPITAL, "initial_entry_budget_rs": ENTRY_BUDGET,
            "max_positions": MAX_POSITIONS, "exit": "Supertrend red and position profitable at signal close; exit next open",
            "average_down": "Supertrend green and total position at or below -6%; add ceil(10% of quantity) next open, at least 7 calendar days since last buy; disabled in the no-add baseline",
            "entries": {"52_week_high": "close within 1% of previous 252-session highest close; enter next available open",
                        "supertrend_flip": "red-to-green Supertrend(10,3) flip; enter next available open; entry starts on the same date as 52-week-high eligibility"},
            "same_day_priority": "exits, add-ons, then entries; alphabetical by symbol",
            "costs": "fees, taxes, slippage, and dividends are not modeled separately; adjusted OHLC is used",
        },
        "downloaded_symbols": len(prepared), "failed_symbols": failures,
        "strategies": {
            "52_week_high_with_averaging": simulate(prepared, "52_week_high", True),
            "52_week_high_no_averaging": simulate(prepared, "52_week_high", False),
            "supertrend_flip_with_averaging": simulate(prepared, "supertrend_flip", True),
            "supertrend_flip_no_averaging": simulate(prepared, "supertrend_flip", False),
        },
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2))
    print(f"Wrote {output}", flush=True)
    for name, result in results["strategies"].items():
        print(name, json.dumps(result["metrics"], indent=2), flush=True)
    print(f"Failed download count: {len(failures)}", flush=True)


if __name__ == "__main__":
    main()
