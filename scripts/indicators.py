"""Shared technical indicators — the single definition of each formula.

Previously copy-pasted across supertrend.py, market_snapshot.py, market_screens.py,
strangle_screen.py, backtest_engine.py and backtest_252dh_supertrend.py (SPEC requires
formula parity everywhere, but 3-4 hand-synced copies drift). All are plain-list,
pure functions; outputs are byte-identical to the former copies (golden-tested).

  compute_ema(values, period)        manually seeded EMA (SMA of first N, then recursive
                                     multiplier) — matches Stock Detail's JS, NOT pandas ewm
  compute_rsi14(closes)              Wilder RSI(14), simple-average seed
  compute_atr(highs, lows, closes)   Wilder ATR (SMA seed)
  compute_supertrend(h, l, c)        standard Supertrend(10, 3) -> (direction, upper, lower)
"""

ATR_PERIOD = 10
MULTIPLIER = 3.0


def compute_ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    ema = sum(values[:period]) / period
    out[period - 1] = ema
    mult = 2 / (period + 1)
    for i in range(period, len(values)):
        ema = (values[i] - ema) * mult + ema
        out[i] = ema
    return out


def compute_rsi14(closes):
    out = [None] * len(closes)
    if len(closes) < 15:
        return out
    gains = losses = 0.0
    for i in range(1, 15):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0)
        losses += max(-diff, 0)
    avg_gain, avg_loss = gains / 14, losses / 14
    out[14] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(15, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain, loss = max(diff, 0), max(-diff, 0)
        avg_gain = (avg_gain * 13 + gain) / 14
        avg_loss = (avg_loss * 13 + loss) / 14
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def compute_atr(highs, lows, closes, period=ATR_PERIOD):
    n = len(closes)
    tr = [None] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = [None] * n
    if n < period:
        return atr
    atr[period - 1] = sum(tr[0:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_supertrend(highs, lows, closes, period=ATR_PERIOD, multiplier=MULTIPLIER):
    """Returns (direction, final_upper, final_lower) lists, 'up'/'down'/None per bar."""
    n = len(closes)
    atr = compute_atr(highs, lows, closes, period)
    final_upper = [None] * n
    final_lower = [None] * n
    direction = [None] * n

    start = period - 1
    if start >= n:
        return direction, final_upper, final_lower

    mid0 = (highs[start] + lows[start]) / 2
    final_upper[start] = mid0 + multiplier * atr[start]
    final_lower[start] = mid0 - multiplier * atr[start]
    direction[start] = "down" if closes[start] <= final_upper[start] else "up"

    for i in range(start + 1, n):
        mid = (highs[i] + lows[i]) / 2
        basic_upper = mid + multiplier * atr[i]
        basic_lower = mid - multiplier * atr[i]

        final_upper[i] = (
            basic_upper
            if (basic_upper < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            basic_lower
            if (basic_lower > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )

        prev_dir = direction[i - 1]
        if prev_dir == "down":
            direction[i] = "up" if closes[i] > final_upper[i] else "down"
        else:
            direction[i] = "down" if closes[i] < final_lower[i] else "up"

    return direction, final_upper, final_lower
