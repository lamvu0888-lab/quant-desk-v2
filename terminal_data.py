"""Source timestamps and transformations shared by the terminal pages."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from functools import lru_cache

from data_utils import DataError, field, utc
from market_session import ET, active_session, rth_bounds, session_state
from tradytics_feed import Feed, FeedError, Snapshot

INDICES = {"SPX", "NDX", "RUT", "DJI", "VIX"}
GREEK_UNITS = {
    "usd_per_1pct_move": "$ per 1% spot move",
    "shares": "shares",
    "usd_per_vol_point": "$ per IV point",
    "shares_per_day": "shares per day",
}


def ticker_symbol(value):
    symbol = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", symbol):
        raise DataError("Enter a ticker such as SPY, SPX or NVDA.")
    return symbol


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def positive(value):
    n = numeric(value)
    return n if n is not None and n > 0 else None


def time_or_none(value):
    try:
        return utc(value)
    except DataError:
        return None


def et_time(value, *, short=False):
    ts = time_or_none(value)
    if ts is None:
        return "—"
    return ts.astimezone(ET).strftime("%I:%M:%S %p" if short else "%b %d · %I:%M:%S %p ET")


def valid_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def expiration_date(session, dte):
    return (session + timedelta(days=dte)).isoformat() if dte is not None else None


def listed_dtes(feed, session):
    dates = {valid_date(row.get("expiry")) for row in feed.rows}
    return sorted((day - session).days for day in dates if day and day >= session)


def filter_expiration(rows, session, dte):
    if dte is None:
        return rows
    target = expiration_date(session, dte)
    return [row for row in rows if row.get("expiry") == target]


def interval_length(interval):
    return timedelta(minutes=int(interval[:-1])) if interval.endswith("m") else timedelta(hours=1)


def closed_flow_rows(rows, session, now, interval):
    """Validate interval bounds and remove the forming bar or another session."""
    bounds = _bounds(session.isoformat())
    if not bounds:
        return []
    opened, closed = bounds
    width = interval_length(interval)
    selected = {}
    for row in rows:
        stamp = time_or_none(row.get("time"))
        if not stamp or not opened <= stamp or stamp + width > min(closed, now):
            continue
        if (stamp - opened) % width or row.get("date") not in (None, session.isoformat()):
            continue
        if numeric(row.get("net_premium_usd")) is None:
            continue
        selected[stamp] = {**row, "time": stamp.isoformat(), "date": session.isoformat()}
    return [selected[stamp] for stamp in sorted(selected)]


def exact_timeline(upper, lower, session, now, interval):
    """Recover one DTE by subtracting additive buckets at matching timestamps."""
    if upper.error or lower.error:
        return Feed(error=upper.error or lower.error, loading=upper.loading or lower.loading)
    if not upper.snapshot or not lower.snapshot:
        return Feed(loading=upper.loading or lower.loading)
    latest = max(upper.snapshot.received_at, lower.snapshot.received_at)
    if abs((upper.snapshot.received_at - lower.snapshot.received_at).total_seconds()) > 75:
        return Feed(error=FeedError("WAITING", "Waiting for both expiry totals to refresh together."))
    a = closed_flow_rows(upper.rows, session, now, interval)
    b = closed_flow_rows(lower.rows, session, now, interval)
    before = {row["time"]: row for row in b}
    fields = ("call_premium_usd", "put_premium_usd", "net_premium_usd", "net_delta_usd", "prints")
    result = []
    for row in a:
        match = before.get(row["time"])
        if match is None:
            continue
        values = {}
        for key in fields:
            x, y = numeric(row.get(key)), numeric(match.get(key))
            values[key] = x - y if x is not None and y is not None else None
        # Higher DTE threshold is a superset. A negative count implies out-of-sync data.
        if values["prints"] is not None and values["prints"] < 0:
            continue
        result.append({"time": row["time"], "date": row["date"], "price": row.get("price"), **values})
    delay = max(numeric(feed.snapshot.meta.get("delay_minutes")) or 0 for feed in (upper, lower))
    return Feed(Snapshot(result, {"date": str(session), "interval": interval, "matched_intervals": len(result), "delay_minutes": delay,
                                  "source_intervals": max(len(a), len(b))}, latest), loading=upper.loading or lower.loading)


def scope_timeline(fetch, now, dte, *, symbol=None, interval="5m", session=None):
    selected = session or active_session(now)
    params = {"symbol": symbol, "interval": interval, "date": str(session) if session else None}
    if dte is None or dte == 0:
        feed = fetch("timeline", dte=dte, **params)
        if not feed.snapshot:
            return feed
        if symbol and feed.snapshot.meta.get("symbol") not in (None, symbol):
            return Feed(error=FeedError("CHECK SYMBOL", "Options flow response identified a different ticker."))
        rows = closed_flow_rows(same_ticker(feed.rows, symbol) if symbol else feed.rows, selected, now, interval)
        return Feed(Snapshot(rows, {**feed.snapshot.meta, "interval": interval}, feed.snapshot.received_at, feed.snapshot.cursor), feed.error, feed.loading)
    upper = fetch("timeline", dte=dte, **params)
    lower = fetch("timeline", dte=dte - 1, **params)
    if symbol and any(feed.snapshot and (feed.snapshot.meta.get("symbol") not in (None, symbol) or
                      any(row.get("symbol") not in (None, symbol) for row in feed.rows)) for feed in (upper, lower)):
        return Feed(error=FeedError("CHECK SYMBOL", "The two expiry totals did not identify the selected ticker."))
    return exact_timeline(upper, lower, selected, now, interval)


def quote_row(feed, ticker):
    rows = feed.rows or ([feed.obj] if feed.obj else [])
    return next((row for row in rows if str(row.get("symbol", "")).upper() == ticker), {})


def exposure_obj(feed, ticker, greek):
    obj = feed.obj
    if obj.get("symbol") not in (ticker, None) or obj.get("greek") not in (greek, None):
        return {}
    return obj


def same_ticker(rows, ticker):
    return [row for row in rows if row.get("symbol") in (None, ticker)]


def session_rows(rows):
    """Use the latest dated session if an endpoint supplies more than one."""
    sessions = []
    for row in rows:
        ts = time_or_none(row.get("time"))
        sessions.append(valid_date(row.get("date")) or (ts.astimezone(ET).date() if ts else None))
    dates = [day for day in sessions if day is not None]
    if not dates:
        return rows
    latest = max(dates)
    return [row for row, day in zip(rows, sessions) if day == latest]


def source_stamp(name, feed, ticker=None):
    """Keep provider observation and HTTP receipt times separate."""
    if not feed.snapshot:
        return None
    if name in ("quotes", "indices") and ticker:
        q = quote_row(feed, ticker)
        return time_or_none(field(q, "price_at", "time")) or valid_date(q.get("date"))
    if name == "exposure":
        return time_or_none(feed.obj.get("time")) or valid_date(feed.obj.get("date"))
    if name == "volatility":
        # An as_of wrapper can be only an API reply time. The session still
        # tells us whether a surface is from today's or an earlier close.
        return valid_date(feed.obj.get("date")) or valid_date(feed.snapshot.meta.get("date"))
    fields = {"bars": ("time", "t"), "timeline": ("time",), "flow": ("time",),
              "dark_prints": ("time",), "signals": ("fired_at",), "chain": ("updated_at",)}
    if name in fields:
        stamps = [ts for row in feed.rows if (ts := time_or_none(field(row, *fields[name])))]
        if stamps:
            stamp = max(stamps)
            # Timeline rows are labeled by interval start; display the actual
            # completed bar time so a 10:05-10:10 bar is not shown as 10:05.
            if name == "timeline":
                interval = feed.snapshot.meta.get("interval")
                if interval in ("1m", "3m", "5m", "15m", "1h"):
                    stamp += interval_length(interval)
            return stamp
    days = [d for row in feed.rows if (d := valid_date(row.get("date")))]
    return max(days) if days else valid_date(field(feed.snapshot.meta, "date", "session"))


def feed_has_delay(feed):
    return bool(feed.snapshot and (numeric(feed.snapshot.meta.get("delay_minutes")) or 0) > 0)


def status(name, feed, now, ticker=None, *, preview=False, paused=False):
    stamp = source_stamp(name, feed, ticker)
    display = et_time(stamp) if isinstance(stamp, datetime) else stamp.isoformat() if stamp else "—"
    if preview:
        return "DEMO", display
    if feed.error:
        return ("CACHED" if feed.snapshot else feed.error.status), display
    if not feed.snapshot:
        return ("PAUSED" if paused else "LOADING"), display
    if paused:
        return "PAUSED", display
    if name in ("quotes", "indices") and not quote_row(feed, ticker):
        return "NO QUOTE", display
    if not feed.rows and not feed.obj:
        return "NO RECORDS", display
    today = now.astimezone(ET).date()
    if isinstance(stamp, datetime):
        age = (now - stamp).total_seconds()
        if age < -5:
            return "CHECK TIME", display
        if stamp.astimezone(ET).date() < today:
            return "PRIOR SESSION", display
        if name in ("flow", "dark_prints", "signals"):
            return "TAPE", display
        if name in ("quotes", "indices", "bars", "chain", "exposure", "timeline"):
            threshold = 120 if name == "exposure" else 90 if name == "timeline" else 150
            if name == "quotes" and quote_row(feed, ticker).get("stale") is True:
                return "AGED", display
            return ("RECENT" if age <= threshold else "AGED"), display
    elif isinstance(stamp, date):
        return ("PRIOR SESSION" if stamp < today else "SESSION DATA"), display
    return "SNAPSHOT", display


@lru_cache(maxsize=96)
def _bounds(day):
    return rth_bounds(day)


def price_bars(feed, ticker, now, *, daily=False):
    if feed.snapshot and feed.snapshot.meta.get("symbol") not in (None, ticker):
        return []
    parsed = {}
    for row in same_ticker(feed.rows, ticker):
        stamp = time_or_none(field(row, "time", "t"))
        if stamp is None:
            continue
        day = stamp.date() if daily else stamp.astimezone(ET).date()
        bounds = _bounds(day.isoformat())
        if not bounds or row.get("closed") is False or row.get("forming") is True:
            continue
        if daily:
            if bounds[1] > now:
                continue
        elif stamp.second or stamp.microsecond or not bounds[0] <= stamp or stamp + timedelta(minutes=1) > min(now, bounds[1]):
            continue
        o, h, l, c = (positive(field(row, key, short)) for key, short in
                      (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c")))
        if None in (o, h, l, c) or not (l <= min(o, c) <= max(o, c) <= h):
            continue
        volume = numeric(field(row, "volume", "v"))
        parsed[stamp] = {"time_utc": stamp.isoformat(), "open": o, "high": h, "low": l, "close": c,
                         "volume": volume if volume is not None and volume >= 0 else None,
                         "vwap": positive(row.get("vwap")), "closed": True}
    return [parsed[stamp] for stamp in sorted(parsed)]


def chart_buckets(bars, minutes):
    """Aggregate complete RTH buckets and compute true session VWAP where supported."""
    groups = {}
    day = None
    total_volume = total_notional = 0
    vwap_complete = False
    expected = None
    for bar in bars:
        stamp = utc(bar["time_utc"])
        local_day = stamp.astimezone(ET).date()
        bounds = _bounds(local_day.isoformat())
        if not bounds:
            continue
        if local_day != day:
            day = local_day
            total_volume = total_notional = 0
            vwap_complete = True
            expected = bounds[0]
        volume, vwap = bar.get("volume"), bar.get("vwap")
        if expected != stamp or volume is None or (volume > 0 and vwap is None):
            vwap_complete = False
        if vwap_complete and volume > 0:
            total_volume += volume
            total_notional += vwap * volume
        expected = stamp + timedelta(minutes=1)
        offset = int((stamp - bounds[0]).total_seconds() // 60)
        bucket = bounds[0] + timedelta(minutes=minutes * (offset // minutes))
        groups.setdefault(bucket, []).append((stamp, bar, total_notional / total_volume if vwap_complete and total_volume else None))
    result = []
    for stamp, group in sorted(groups.items()):
        if len(group) != minutes or any(ts != stamp + timedelta(minutes=i) for i, (ts, _, _) in enumerate(group)):
            continue
        values = [bar for _, bar, _ in group]
        volumes = [bar.get("volume") for bar in values]
        result.append({"time_utc": stamp.isoformat(), "open": values[0]["open"], "close": values[-1]["close"],
                       "high": max(row["high"] for row in values), "low": min(row["low"] for row in values),
                       "volume": sum(volumes) if None not in volumes else None, "session_vwap": group[-1][2]})
    return result


def snapshot_forecast_gate(quote, candles, quote_feed, bar_feed, now, paused=False):
    session = session_state(now)
    reasons = []
    if session["status"] != "OPEN":
        reasons.append("Waiting for the regular session.")
    if paused:
        reasons.append("Polling is paused.")
    quote_stamp = time_or_none(field(quote, "price_at", "time"))
    today = now.astimezone(ET).date()
    if not quote_stamp or quote_stamp.astimezone(ET).date() != today or not -5 <= (now - quote_stamp).total_seconds() <= 120:
        reasons.append("Waiting for a current Tradytics quote timestamp.")
    delay = numeric(field(quote, "delay_minutes"))
    bar_delay = numeric(bar_feed.snapshot.meta.get("delay_minutes")) if bar_feed.snapshot else None
    quote_delay = numeric(quote_feed.snapshot.meta.get("delay_minutes")) if quote_feed.snapshot else None
    if any(value is not None and value > 0 for value in (delay, bar_delay, quote_delay)):
        reasons.append("Tradytics reports delayed data.")
    if quote.get("stale") is True or quote.get("session") not in (None, "regular"):
        reasons.append("Waiting for the current regular-session price.")
    if quote_feed.error or bar_feed.error:
        reasons.append("A price or candle request is waiting to recover.")
    if len(candles) < 121:
        reasons.append("At least 121 completed one-minute candles are needed.")
    else:
        bar_close = utc(candles[-1]["time_utc"]) + timedelta(minutes=1)
        if bar_close.astimezone(ET).date() != today or not 0 <= (now - bar_close).total_seconds() <= 150:
            reasons.append("Waiting for a recent completed one-minute candle.")
    return session, reasons


def flow_side(row):
    side = row.get("side")
    if side in ("bought", 1, "1"):
        return "Bought"
    if side in ("sold", -1, "-1"):
        return "Sold"
    if side in ("mid", 0, "0"):
        return "Mid"
    return "Unknown"


def display_exposure_unit(obj):
    unit = obj.get("unit")
    return GREEK_UNITS.get(unit, str(unit) if isinstance(unit, str) and len(unit) < 40 else "unit unavailable")
