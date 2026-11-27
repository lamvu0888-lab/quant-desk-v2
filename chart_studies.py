"""Display studies for completed Quant Desk candles."""
from collections import deque
from datetime import timedelta
from statistics import fmean, pstdev

from data_utils import utc
from ema_trend import ema_lines, ema_timing
from market_session import rth_bounds
from terminal_data import chart_buckets, numeric


MINUTE = timedelta(minutes=1)
PIVOTS = "Confirmed swing levels"
MTF = "5m EMA 21 overlay"
OPENING = "Opening range (15m)"
ANCHOR = "Highest-volume anchored VWAP"
BANDS = "Bollinger bands (20, 2)"
RSI = "RSI (14)"
MACD = "MACD (12, 26, 9)"


def session_bars(candles, session, now):
    opened, closed = rth_bounds(session)
    return [bar for bar in candles if opened <= utc(bar["time_utc"]) <= min(now, closed) - MINUTE
            and bar.get("closed", True) and not bar.get("forming")]


def trend_strip(candles, session, now):
    """Show the latest expected completed bucket for each clock, with explicit warmup and gaps."""
    opened, closed = rth_bounds(session)
    asof = min(now, closed)
    rows = [b for b in candles[-1500:] if b.get("closed", True) and not b.get("forming")
            and utc(b["time_utc"]) + MINUTE <= asof]
    result = []
    for minutes in (1, 3, 5):
        width = minutes * MINUTE
        boundary = opened + ((asof-opened) // width) * width
        bars = chart_buckets(rows, minutes)
        if boundary <= opened:
            state = "Waiting for close"
        elif not bars or utc(bars[-1]["time_utc"]) + width != boundary:
            state = "Missing close"
        else:
            read = ema_timing(bars, width)
            state = ("Warming up" if read["ema21"] is None else
                     {1: "Up", -1: "Down", 0: "Mixed"}[read["direction"]])
        result.append((minutes, state))
    return result


def completed_five_minute_ema(candles, session, now):
    width = 5 * MINUTE
    opened, closed = rth_bounds(session)
    complete = [b for b in candles[-1500:] if b.get("closed", True) and not b.get("forming")
                and utc(b["time_utc"]) + MINUTE <= min(now, closed)]
    bars = chart_buckets(complete, 5)
    result = {}
    for bar, (_, slow) in zip(bars, ema_lines(bars, width)):
        stamp = utc(bar["time_utc"])
        if slow is not None and stamp >= opened:
            for offset in range(5):
                result[stamp + width + offset*MINUTE] = slow
    return result


def intraday_levels(candles, session, now):
    """Minute-close readings. A record-volume anchor advances only as each new record closes."""
    opened, _ = rth_bounds(session)
    records = {}
    opening_high = opening_low = None
    opening_count = 0
    opening_complete = True
    peak = weight = notional = 0
    expected = opened
    complete = True
    for bar in session_bars(candles, session, now):
        stamp = utc(bar["time_utc"])
        if stamp < opened + 15 * MINUTE:
            opening_complete = opening_complete and stamp == opened + opening_count * MINUTE
            opening_count += 1
            opening_high = max(opening_high, bar["high"]) if opening_high is not None else bar["high"]
            opening_low = min(opening_low, bar["low"]) if opening_low is not None else bar["low"]
        volume = numeric(bar.get("volume"))
        complete = complete and expected == stamp and volume is not None and volume >= 0
        if complete and volume:
            if volume > peak:
                peak, weight, notional = volume, 0, 0
            weight += volume
            notional += volume * (bar["high"] + bar["low"] + bar["close"]) / 3
        expected = stamp + MINUTE
        ready = expected >= opened + 15 * MINUTE and opening_count == 15 and opening_complete
        records[expected] = (opening_high if ready else None, opening_low if ready else None,
                             notional / weight if complete and weight else None)
    return records


def swing_levels(bars, width):
    """Publish a strict two-left/two-right swing only on the second confirming close."""
    window = deque(maxlen=5)
    support = resistance = prior = None
    values = {"Swing support": [], "Swing resistance": []}
    for row in bars:
        stamp = utc(row["time_utc"])
        if width and prior and stamp - prior != width:
            window.clear()
            support = resistance = None
        window.append(row)
        if len(window) == 5:
            left, before, pivot, after, right = window
            neighbors = (left, before, after, right)
            if all(pivot["high"] > b["high"] for b in neighbors):
                resistance = pivot["high"]
            if all(pivot["low"] < b["low"] for b in neighbors):
                support = pivot["low"]
        values["Swing support"].append(support)
        values["Swing resistance"].append(resistance)
        prior = stamp
    return values


def heikin_ashi(bars, width):
    """Synthetic OHLC for display only. Restart at missing intraday intervals."""
    result = []
    prior_stamp = None
    for row in bars:
        stamp = utc(row["time_utc"])
        close = (row["open"] + row["high"] + row["low"] + row["close"]) / 4
        gap = width and prior_stamp and stamp.date() == prior_stamp.date() and stamp - prior_stamp != width
        if not result or gap:
            opened = (row["open"] + row["close"]) / 2
        else:
            opened = (result[-1]["open"] + result[-1]["close"]) / 2
        result.append(dict(open=opened, close=close, high=max(row["high"], opened, close),
                           low=min(row["low"], opened, close)))
        prior_stamp = stamp
    return result


def _smooth(values, period, alpha):
    seed = []
    level = None
    for value in values:
        if value is not None:
            if level is None:
                seed.append(value)
                if len(seed) == period:
                    level = fmean(seed)
            else:
                level += alpha * (value - level)
        yield level


def close_studies(bars, width, bands, oscillator):
    """Use SMA-seeded MACD, population Bollinger bands and Wilder's RSI. Reset at intraday gaps."""
    series = {}
    groups = []
    for row in bars:
        stamp = utc(row["time_utc"])
        if (not groups or (width and stamp.date() == previous.date() and stamp - previous != width)):
            groups.append([])
        groups[-1].append(row["close"])
        previous = stamp
    for closes in groups:
        if bands:
            windows = [closes[i-19:i+1] if i >= 19 else [] for i in range(len(closes))]
            pairs = [(fmean(w), 2*pstdev(w)) if w else (None, None) for w in windows]
            for label, sign in (("Upper band", 1), ("Lower band", -1)):
                series.setdefault(label, []).extend(m + sign*s if m is not None else None for m, s in pairs)
        if oscillator == RSI:
            changes = [b-a for a, b in zip(closes, closes[1:])]
            gains = _smooth((max(0, x) for x in changes), 14, 1/14)
            losses = _smooth((max(0, -x) for x in changes), 14, 1/14)
            values = [None]
            for gain, loss in zip(gains, losses):
                values.append(None if gain is None else 50 if gain + loss == 0 else 100*gain/(gain + loss))
            series.setdefault("RSI", []).extend(values)
        elif oscillator == MACD:
            fast = _smooth(closes, 12, 2/13)
            slow = _smooth(closes, 26, 2/27)
            macd = [f-s if s is not None else None for f, s in zip(fast, slow)]
            series.setdefault("MACD", []).extend(macd)
            series.setdefault("Signal", []).extend(_smooth(macd, 9, 2/10))
    return series
