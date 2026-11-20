"""Point-in-time session context for the experimental paper planner."""
from datetime import datetime, time, timedelta

from data_utils import field, records, reported_delay, utc
from market_session import ET, calendar, rth_bounds
from my_webull_adapter import category_for
from terminal_data import positive, time_or_none
from webull_feed import _symbol_in_payload

FIVE = timedelta(minutes=5)


def summarize(rows, start, end, step):
    bars = {}
    for row in rows:
        at = time_or_none(field(row, "time", "timestamp", "time_utc"))
        if not at or not start <= at <= end - step or (at-start) % step:
            continue
        values = [positive(row.get(k)) for k in ("open", "high", "low", "close")]
        if None in values or row.get("closed") is False or row.get("forming"):
            continue
        o, h, l, c = values
        if l <= min(o, c) <= max(o, c) <= h:
            bars[at] = values
    times = sorted(bars)
    result = {"bars": len(times), "expected": (end-start)//step}
    if times:
        result.update(open=bars[times[0]][0], high=max(b[1] for b in bars.values()),
                      low=min(b[2] for b in bars.values()), close=bars[times[-1]][3],
                      last_close=(times[-1]+step).isoformat())
    return result


def session_context(symbol, now, webull, schwab):
    day = now.astimezone(ET).date()
    prior = calendar().previous_session(str(day)).date()
    opened, closed = rth_bounds(prior)
    def local(d, hour):
        return datetime.combine(d, time(hour), ET)
    windows = {"RTH": (opened, closed), "ATH": (closed, local(prior, 20)),
               "OVN": (local(day-timedelta(days=1),20), local(day,4)),
               "PRE": (local(day,4), min(now, rth_bounds(day)[0]))}
    context, levels = {}, {}
    for name, (start, end) in windows.items():
        ticker = "SPY" if symbol == "SPX" and name != "RTH" else symbol
        stats = {}
        source = "Webull"
        if name == "RTH" and symbol == "SPX" and schwab:
            feed = schwab.get("history", session=str(prior))
            stats = summarize(feed.obj.get("candles", []), start, end, FIVE/5)
            if stats["bars"] == stats["expected"]:
                source = "Schwab"
            else:
                stats = {}
        if not stats and webull and webull.settings.endpoint == "api.webull.com":
            try:
                raw = webull._option_request("Session history", webull._client().data.market_data.get_batch_history_bar,
                    symbols=[ticker], category=category_for(ticker), timespan="M5", count="200",
                    trading_sessions=name, real_time_required=True, start_time=int(start.timestamp()*1000),
                    end_time=int(end.timestamp()*1000))
                if _symbol_in_payload(raw, ticker) and not reported_delay(raw, ticker):
                    rows = [r for r in records(raw,ticker) if field(r,"trading_session","trading_sessions") in (None,name)]
                    stats = summarize(rows,start,end,FIVE)
            except Exception:
                pass
        context[name] = dict(stats, ticker=ticker, source=source, start=start.isoformat(), end=end.isoformat(),
                             coverage="complete" if stats.get("bars") and stats["bars"] == stats["expected"]
                             else "partial" if stats.get("bars") else "unavailable")
        if ticker == symbol and context[name]["coverage"] == "complete":
            levels.update({f"{name} {k}": stats[k] for k in ("open","high","low","close")})
    return context, levels


def revision_reason(daily, bars, market, now, previous_market):
    if daily["state"] != "ready":
        return
    recent = sorted((utc(b["time_utc"]), b["close"]) for b in bars
                    if daily["at"] <= utc(b["time_utc"]) <= now-FIVE)
    if len(recent)<2 or recent[-1][0]-recent[-2][0] != FIVE or now-recent[-1][0] >= 2*FIVE:
        return
    original = daily["input"].get("market")
    if market in ("SIDEWAYS","TREND UP","TREND DOWN") and original and original != market and previous_market == market:
        return f"Market changed from {original} to {market} on two completed bars."
    for name, leg in daily["legs"].items():
        if leg["setup"] == "Wait":
            continue
        side = 1 if name in ("A","C support") else -1
        earlier = [daily["input"]["spot"], *(v for _,v in recent[:-2])]
        if any(side*(v-leg["trigger"])>0 for v in earlier) and all(
                side*(v-leg["invalidation"])<0 for _,v in recent[-2:]):
            return f"{name} crossed its trigger and then closed beyond invalidation twice."
