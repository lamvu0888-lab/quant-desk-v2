"""EMA 9/21 timing from completed regular-session price bars."""
from datetime import timedelta

from data_utils import utc
from market_session import ET
from terminal_data import chart_buckets

EMA_LABEL = "EMA 9/21"
EMA_PULLBACK = f"{EMA_LABEL} pullback"
MINUTE = timedelta(minutes=1)
TREND_INTERVAL = 3 * MINUTE
TREND_ALERT_AGE = timedelta(seconds=90)


def ema_lines(bars, width):
    """Carry the EMA from prior sessions. Warm up again after a gap within a session."""
    periods = (9, 21)
    totals = [0., 0.]
    values = [None, None]
    n, previous = 0, None
    result = []
    for bar in bars:
        stamp = utc(bar["time_utc"])
        if (width and previous and stamp - previous != width
                and stamp.astimezone(ET).date() == previous.astimezone(ET).date()):
            totals, values, n = [0., 0.], [None, None], 0
        previous = stamp
        n += 1
        close = bar["close"]
        for i, period in enumerate(periods):
            if n <= period:
                totals[i] += close
                if n == period:
                    values[i] = totals[i] / period
            else:
                values[i] += 2 / (period + 1) * (close - values[i])
        result.append(tuple(values))
    return result


def ema_read(bar, current, previous):
    fast, slow = current
    prior_slow = previous[1]
    direction = 0
    if fast is not None and slow is not None and prior_slow is not None:
        close = bar["close"]
        # Require measurable separation as well as a rising or falling slow EMA.
        separation = close * .00005
        if fast - slow >= separation and close > slow > prior_slow:
            direction = 1
        elif slow - fast >= separation and close < slow < prior_slow:
            direction = -1
    return {"direction": direction, "ema9": fast, "ema21": slow}


def ema_timing(bars, width):
    if not bars:
        return {"direction": 0, "ema9": None, "ema21": None}
    lines = ema_lines(bars[-210:], width)
    return ema_read(bars[-1], lines[-1], lines[-2] if len(lines) > 1 else (None, None))


def ema_trend(candles, session, now):
    """Find two consecutive 3m closes beyond both EMAs and detect a new trend."""
    available = [bar for bar in candles[-636:] if bar.get("closed", True) and not bar.get("forming")
                 and utc(bar["time_utc"]) + MINUTE <= now]
    bars = chart_buckets(available, 3)
    if len(bars) < 3:
        return None
    lines = ema_lines(bars, TREND_INTERVAL)
    signals = []
    for i in range(len(bars) - 3, len(bars)):
        stamp = utc(bars[i]["time_utc"])
        reading = ema_read(bars[i], lines[i], lines[i-1] if i else (None, None))
        direction = reading["direction"]
        if (stamp.astimezone(ET).date() != session or not direction
                or direction * (bars[i]["close"] - reading["ema9"]) <= 0):
            direction = 0
        signals.append((stamp, direction))
    (earlier, before), (prior, preceding), (opened, direction) = signals
    close = opened + TREND_INTERVAL
    if (not direction or preceding != direction or opened - prior != TREND_INTERVAL
            or not timedelta(0) <= now - close < TREND_INTERVAL + TREND_ALERT_AGE):
        return None
    return {"direction": "UP" if direction > 0 else "DOWN", "price": bars[-1]["close"],
            "ema9": lines[-1][0], "ema21": lines[-1][1], "bar": close.isoformat(),
            "new": before != direction or prior - earlier != TREND_INTERVAL}
