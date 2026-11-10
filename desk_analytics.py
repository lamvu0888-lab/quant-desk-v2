"""Closed-bar calculations shared by the dashboard and its historical replay."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import timedelta
from statistics import mean, median

from data_utils import field, utc
from market_session import ET, calendar, rth_bounds
from terminal_data import flow_side, numeric, positive, time_or_none
from volatility import CandleError, analyze_volatility

MINUTE = timedelta(minutes=1)
FIVE_MINUTES = 5 * MINUTE
REGIME_LABELS = {"COMPRESSION": "Compressing", "NEUTRAL": "Normal", "EXPANSION": "Expanding",
                 "BREAKOUT WATCH": "Expanding"}


def candles_on(bars, day):
    return [bar for bar in bars if utc(bar["time_utc"]).astimezone(ET).date() == day]


def realized_regime(buckets):
    try:
        vol = analyze_volatility(buckets[-220:], allow_missing_volume=True)
    except CandleError:
        return {"label": "Waiting for bars"}
    return {**vol, "label": REGIME_LABELS[vol["status"]]}


def sign(value):
    return int(value > 0) - int(value < 0)


def share_cvd(bars, day):
    """Sign each closed 1m bar's share volume by its price direction."""
    points = []
    cumulative, direction, previous_close = 0, 0, None
    previous_stamp = None
    for row in candles_on(bars, day):
        stamp = utc(row["time_utc"])
        volume = numeric(row.get("volume"))
        if previous_stamp is not None and stamp - previous_stamp != MINUTE:
            direction, previous_close = 0, None
        previous_stamp = stamp
        if volume is None or volume < 0:
            direction, previous_close = 0, None
            continue
        close = row["close"]
        direction = sign(close - row["open"]) or (sign(close - previous_close) if previous_close is not None else 0) or direction
        delta = direction * volume
        cumulative += delta
        points.append({"time": (stamp + MINUTE).isoformat(), "delta": delta, "cvd": cumulative, "price": close})
        previous_close = close
    return points


def order_rows(rows, ticker, now, *, session=None):
    """Validate executed trades. Price is the underlying at the print when supplied."""
    unique = {}
    for row in rows:
        stamp = time_or_none(row.get("time"))
        premium = positive(field(row, "premium_usd", "premium"))
        if row.get("symbol") != ticker or not stamp or stamp > now or not premium or row.get("type") not in ("call", "put"):
            continue
        if session and stamp.astimezone(ET).date() != session:
            continue
        key = row.get("id")
        if not isinstance(key, (int, str)) or isinstance(key, bool):
            key = (stamp, row.get("contract"), row.get("size"), premium)
        side = flow_side(row)
        directional = {"Bought": 1, "Sold": -1}.get(side, 0) * (1 if row["type"] == "call" else -1)
        unique[str(key)] = {**row, "premium_usd": premium, "stamp": stamp, "side_label": side, "directional": directional}
    return sorted(unique.values(), key=lambda row: row["stamp"])


def mark_orders(orders, bars, limit):
    """Match prints to a completed candle and use only prices known at the print."""
    if not bars:
        return []
    starts = [utc(row["time_utc"]) for row in bars]
    candidates = []
    for order in orders:
        stamp = order["stamp"]
        i = bisect_right(starts, stamp) - 1
        if i < 0 or stamp - starts[i] >= MINUTE:
            continue
        spot = positive(order.get("spot"))
        basis = "Underlying at print"
        if spot is None:
            if i == 0 or starts[i] - starts[i - 1] != MINUTE:
                continue
            spot, basis = bars[i - 1]["close"], "Prior completed minute close"
        candidates.append({**order, "spot": spot, "spot_basis": basis})
    return sorted(sorted(candidates, key=lambda row: row["premium_usd"], reverse=True)[:limit],
                  key=lambda row: row["stamp"])


def flow_read(rows):
    """Compare the last three 5m bins with the three immediately before them."""
    recent = rows[-6:]
    if len(recent) < 6 or any(utc(b["time"]) - utc(a["time"]) != FIVE_MINUTES for a, b in zip(recent, recent[1:])):
        return {}
    values = [numeric(row.get("net_premium_usd")) for row in recent]
    if None in values:
        return {}
    old, new = mean(values[:3]), mean(values[3:])
    noise = median(abs(value) for value in values[:3])
    # A flat period supplies no comparison baseline, so use a disclosed $25K floor.
    meaningful = max(25_000, .5 * noise)
    momentum = new - old
    return {"recent_net": sum(values[3:]), "momentum": momentum, "floor": meaningful,
            "direction": sign(new) if abs(new) >= meaningful else 0, "accelerating": abs(momentum) >= meaningful,
            "stamp": (utc(recent[-1]["time"]) + FIVE_MINUTES).isoformat()}


@dataclass(frozen=True)
class PriceLevel:
    label: str
    price: float
    kind: str
    as_of: str | None = None
    strength: float | None = None


def price_levels(buckets, session):
    """Use the prior twelve completed 5m bars; the latest bar can test the levels."""
    today = candles_on(buckets, session)
    levels = []
    lookback = today[-13:-1]
    if len(lookback) >= 4 and utc(today[-1]["time_utc"]) - utc(lookback[0]["time_utc"]) == len(lookback) * FIVE_MINUTES:
        at = (utc(lookback[-1]["time_utc"]) + FIVE_MINUTES).isoformat()
        levels.extend([PriceLevel("Range high", max(b["high"] for b in lookback), "resistance", at),
                       PriceLevel("Range low", min(b["low"] for b in lookback), "support", at)])
    prior_day = calendar().previous_session(session.isoformat()).date()
    prior = candles_on(buckets, prior_day)
    if prior:
        opened, closed = rth_bounds(prior_day)
        if (len(prior) * FIVE_MINUTES == closed - opened and utc(prior[0]["time_utc"]) == opened and
                utc(prior[-1]["time_utc"]) + FIVE_MINUTES == closed):
            levels.extend([PriceLevel("Prior session high", max(b["high"] for b in prior), "resistance", closed.isoformat()),
                           PriceLevel("Prior session low", min(b["low"] for b in prior), "support", closed.isoformat())])
    return levels


def _price_direction(bars, atr):
    if len(bars) < 4 or utc(bars[-1]["time_utc"]) - utc(bars[-4]["time_utc"]) != 3 * FIVE_MINUTES:
        return 0, False
    start, end = bars[-4]["close"], bars[-1]["close"]
    floor = max(start * .0002, .15 * (atr or 0))
    return (sign(end - start) if abs(end - start) >= floor else 0), True


def setup_read(buckets, flow_rows, session, regime, levels, *, gex=None, ready=True):
    """A transparent evidence score, with direction from price and options flow."""
    today = candles_on(buckets, session)
    flow = flow_read(flow_rows)
    atr = positive(regime.get("atr"))
    price_dir, price_known = _price_direction(today, atr)
    flow_dir = flow.get("direction", 0)
    range_levels = {level.label: level.price for level in levels}
    up, down = range_levels.get("Range high"), range_levels.get("Range low")
    price = today[-1]["close"] if today else None
    bias = ("Bullish" if price_dir > 0 else "Bearish") if price_dir == flow_dir and price_dir else (
        "Mixed" if price_dir * flow_dir < 0 else "Neutral")
    evidence = [
        ("Price", {1: "Rising", -1: "Falling", 0: "Flat"}[price_dir] if price_known else "Waiting"),
        ("Options flow", {1: "Bullish tilt", -1: "Bearish tilt", 0: "Balanced"}[flow_dir] if flow else "Waiting"),
        ("Regime", regime["label"]),
        ("5m relative volume", f"{ratio:.2f}×" if (ratio := numeric(regime.get("volume_ratio"))) is not None else "Unavailable"),
        ("Dealer gamma", "Unavailable" if gex is None else "Negative · may amplify moves" if gex < 0 else
         "Positive · may dampen moves" if gex > 0 else "Flat"),
    ]
    result = {"bias": "Waiting", "state": "Waiting", "score": None, "agreement": None, "evidence": evidence,
              "flow": flow, "trigger": None, "invalidation": None, "direction": 0, "points": [],
              "divergence": price_known and price_dir * flow_dir < 0}
    if not (ready and flow and price_known and up and down and atr and up > down):
        return result
    last, previous = today[-1], today[-2]
    edge = max(.45 * atr, price * .0003)
    is_near = min(abs(price - up), abs(price - down)) <= edge
    broke = 1 if price > up and previous["close"] <= up else -1 if price < down and previous["close"] >= down else 0
    volume_ok = ratio is None or ratio >= 1.2
    points = [("Tight price range", 25 if regime["label"] == "Compressing" else 0),
              ("Near the range edge", 15 if is_near or broke else 0),
              ("Flow is changing quickly", 20 if flow["accelerating"] else 0),
              ("Range break or wider swings", 20 if broke or regime["label"] == "Expanding" else 0),
              ("Volume at least 1.2×", 10 if ratio is not None and volume_ok else 0),
              ("Negative modeled gamma", 10 if gex is not None and gex < 0 else 0)]
    score = sum(value for _, value in points)
    direction = price_dir if price_dir == flow_dir else 0
    rejected = (last["high"] > up > price and flow_dir < 0) or (last["low"] < down < price and flow_dir > 0)
    invalidated = False
    if len(today) >= 6:
        prior_band = today[-14:-2]
        prior_high, prior_low = max(b["high"] for b in prior_band), min(b["low"] for b in prior_band)
        invalidated = previous["close"] > prior_high >= price or previous["close"] < prior_low <= price
    if invalidated:
        state = "Invalidated"
    elif rejected:
        state = "Reversal watch"
    elif broke and broke == direction and volume_ok:
        state = "Triggered"
    elif direction and is_near and ((direction > 0 and price < up) or (direction < 0 and price > down)) and score >= 40:
        state = "Ready"
    elif score >= 30 and bias != "Mixed":
        state = "Building"
    else:
        state = "No trade"
    trigger = up if direction > 0 else down if direction < 0 else None
    agreement = 2 if direction else int(bool(price_dir) != bool(flow_dir))
    return {**result, "bias": bias, "state": state, "score": score, "points": points, "agreement": agreement,
            "direction": direction, "trigger": trigger if state in ("Ready", "Triggered") else None,
            "invalidation": trigger if state in ("Ready", "Triggered") else None}


def signed_moves(bars, events, day, now):
    """Underlying outcomes, measured at or before each horizon with a one-minute tolerance."""
    session_bars = candles_on(bars, day)
    ends = [utc(bar["time_utc"]) + MINUTE for bar in session_bars]
    bounds = rth_bounds(day)
    if not bounds or not ends:
        return []
    opened, closed = bounds
    outcomes = []
    for event in events:
        at, spot = event["stamp"], positive(event.get("spot"))
        if not opened <= at < closed or not spot:
            continue
        row = {"time": at.isoformat(), "spot": spot, "label": event["label"], "lean": event["directional"],
               "regime": event.get("regime"), "trigger_level": event.get("trigger_level")}
        for label, target in (("15m", at + 15 * MINUTE), ("30m", at + 30 * MINUTE),
                              ("60m", at + 60 * MINUTE), ("Close", closed)):
            j = bisect_right(ends, target) - 1
            row[label] = (100 * (session_bars[j]["close"] / spot - 1)
                          if j >= 0 and at < ends[j] and target <= min(closed, now) and target - ends[j] < MINUTE else None)
        outcomes.append(row)
    return outcomes


def replay_setups(bars, flow_rows, session):
    """Evaluate the price/flow trigger as of each 5m close, excluding future data."""
    events = []
    last_direction = 0
    for i, bar in enumerate(bars):
        stamp = utc(bar["time_utc"])
        if stamp.astimezone(ET).date() != session:
            continue
        available = bars[max(0, i - 219):i + 1]
        same_day_flow = [row for row in flow_rows if utc(row["time"]) + FIVE_MINUTES <= stamp + FIVE_MINUTES]
        if not same_day_flow or utc(same_day_flow[-1]["time"]) != stamp:
            continue
        outcome = setup_read(available, same_day_flow, session, realized_regime(available), price_levels(available, session))
        if outcome["state"] != "Triggered":
            last_direction = 0
            continue
        direction = outcome["direction"]
        if direction != last_direction:
            events.append({"stamp": stamp + FIVE_MINUTES, "spot": bar["close"], "directional": direction,
                           "regime": dict(outcome["evidence"])["Regime"], "trigger_level": outcome["trigger"],
                           "label": "Upward range break" if direction > 0 else "Downward range break"})
        last_direction = direction
    return events


def session_window(bars, now, session):
    """Same-time historical context from complete 30m price windows."""
    opened, closed = rth_bounds(session)
    elapsed = max(0, int((min(now, closed) - opened).total_seconds() // 60))
    total = int((closed - opened).total_seconds() // 60)
    phase = ("Session closed" if now >= closed else "Opening 30m" if elapsed < 30 else "Final hour" if elapsed >= total - 60 else
             "Morning" if elapsed < 120 else "Midday" if elapsed < 240 else "Afternoon")
    offset = min((elapsed // 30) * 30, total - 30)
    history = {}
    for bar in bars:
        stamp = utc(bar["time_utc"])
        day = stamp.astimezone(ET).date()
        if day >= session:
            continue
        history.setdefault(day, {})[stamp] = bar
    moves = []
    for day, by_time in sorted(history.items())[-10:]:
        start, end = rth_bounds(day)
        stamps = [start + (offset + j) * MINUTE for j in range(30)]
        if stamps[-1] < end and all(stamp in by_time for stamp in stamps):
            moves.append(abs(by_time[stamps[-1]]["close"] / by_time[stamps[0]]["open"] - 1))
    return {"phase": phase, "typical_move": median(moves) if len(moves) >= 3 else None,
            "sample": len(moves), "window_at": (opened + offset * MINUTE).isoformat()}
