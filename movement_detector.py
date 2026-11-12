"""Completed-bar movement checks used by Trade Plan and the monitor."""

from __future__ import annotations

from statistics import median

from data_utils import utc
from desk_analytics import FIVE_MINUTES, MINUTE, share_cvd, sign
from market_session import ET
from terminal_data import numeric, positive, time_or_none

MIN_ENTRY_SCORE = 80
MIN_RELATIVE_VOLUME = 1.25
ENTRY_COOLDOWN = 10 * MINUTE
DIRECTION = {-1: "Down", 0: "Flat", 1: "Up"}

CHECKS = ("Market State", "Volatility", "GEX / Dealer Positioning", "Netflow / CVD",
          "Price Structure", "Momentum / Volume", "Movement Decision", "Trade Trigger", "Data Quality")

PLAYBOOKS = {
    "Trend continuation": ("EMA 8/10 pullback", "VWAP reclaim or rejection", "Liquidity sweep"),
    "Breakout and retest": ("Breakout and retest", "Breakout close", "EMA 8/10 pullback"),
    "Volatility expansion": ("Breakout and retest", "Breakout close", "EMA 8/10 pullback"),
    "Range mean reversion": ("Range-edge sweep", "VWAP reversion"),
    "Wait for direction": (),
}

SETUP_ORDER = {
    "Trend continuation": ("ema", "vwap", "retest", "sweep", "break"),
    "Breakout and retest": ("retest", "break", "ema", "vwap", "sweep"),
    "Volatility expansion": ("retest", "break", "ema", "vwap", "sweep"),
    "Range mean reversion": ("sweep", "retest", "break", "ema", "vwap"),
    "Wait for direction": ("retest", "break", "ema", "vwap", "sweep"),
}


def signed_flow_confirmation(rows, flow, direction, price_close):
    """Confirm on two distinct matching, completed premium intervals."""
    recent = rows[-2:]
    if len(recent) != 2 or not flow or not price_close:
        return {"count": 0, "intervals": [], "confirmed": False}
    starts = [time_or_none(row.get("time")) for row in recent]
    values = [numeric(row.get("net_premium_usd")) for row in recent]
    if (None in starts or None in values or starts[1] - starts[0] != FIVE_MINUTES
            or starts[1] + FIVE_MINUTES != price_close):
        return {"count": 0, "intervals": [], "confirmed": False}
    floor = flow["floor"]
    count = sum(bool(direction and direction * value >= floor) for value in values)
    return {"count": count, "confirmed": count == 2, "floor": floor,
            "intervals": [{"close": (opened + FIVE_MINUTES).isoformat(), "net": value}
                          for opened, value in zip(starts, values)]}


def relative_volume(current, history):
    """Prefer the same exchange-local five-minute slot on prior sessions."""
    if not current:
        return None, None, 0
    last = current[-1]
    volume = positive(last.get("volume"))
    if volume is None:
        return None, None, 0
    local = utc(last["time_utc"]).astimezone(ET)
    samples = {}
    for row in reversed(history):
        stamp = utc(row["time_utc"]).astimezone(ET)
        if stamp.date() < local.date() and stamp.time() == local.time():
            sample = positive(row.get("volume"))
            if sample is not None:
                samples[stamp.date()] = sample
            if len(samples) >= 10:
                break
    if len(samples) >= 3:
        return volume / median(samples.values()), "same 5m slot on prior sessions", len(samples)
    recent = [positive(row.get("volume")) for row in current[-21:-1]]
    if len(recent) >= 8 and None not in recent:
        return volume / median(recent), "prior contiguous 5m bars", len(recent)
    return None, None, 0


def price_movement(bars, history, direction, prior_direction, atr, noise, regime, break_direction, break_level):
    """Describe price behavior before adding option flow, a setup, or a quote."""
    volume, basis, sample = relative_volume(bars, history)
    profile = {"market": "WAITING FOR DATA", "playbook": "Wait for direction", "efficiency": None,
               "net_atr": None, "momentum": 0, "accelerating": False, "volume": volume,
               "volume_basis": basis, "volume_sample": sample, "price_confirmed": False,
               "closed_bars": 0, "boundary_hold": False, "hold_level": None, "range_hold": False, "released": False}
    if len(bars) < 6 or not atr:
        return profile
    closes = [bar["close"] for bar in bars[-7:]]
    net = closes[-1] - closes[0]
    travel = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    efficiency = abs(net) / travel if travel else 0.0
    recent = closes[-1] - closes[-4]
    prior = closes[-4] - closes[0]
    momentum = sign(recent) if abs(recent) >= max(2 * noise, .25 * atr) else 0
    accelerated = bool(momentum and abs(recent) >= max(abs(prior) * 1.20, .45 * atr))
    preceding = bars[-14:-2]
    boundary = (max(bar["high"] for bar in preceding) if direction > 0 else
                min(bar["low"] for bar in preceding) if direction < 0 else None)
    holds = bool(boundary is not None and all(direction * (close - boundary) >= noise for close in closes[-2:]))
    expanded = regime == "Expanding" or bool(volume is not None and volume >= MIN_RELATIVE_VOLUME) or accelerated
    released = holds and expanded
    trending = bool(direction and direction * net >= .65 * atr and efficiency >= .45)
    market = ("TREND UP" if direction > 0 else "TREND DOWN") if trending or released else "SIDEWAYS"
    range_hold = market == "SIDEWAYS" or (regime == "Compressing" and not released)
    if range_hold:
        playbook = "Range mean reversion"
    elif holds or (break_level and break_direction == direction and direction * (closes[-1] - break_level) >= noise):
        playbook = "Breakout and retest"
    elif regime == "Expanding":
        playbook = "Volatility expansion"
    else:
        playbook = "Trend continuation"
    count = (1 + int(direction == prior_direction)) if direction else 0
    profile.update(market=market, playbook=playbook, efficiency=efficiency, net_atr=net / atr,
                   momentum=momentum, accelerating=accelerated, closed_bars=count, price_confirmed=count == 2,
                   boundary_hold=holds, hold_level=boundary if holds else None, range_hold=range_hold, released=released)
    return profile


def cvd_context(candles, session, price_close, label):
    """Use only a synchronized ten-minute estimate from available share candles."""
    if not price_close:
        return {"label": label, "delta": None, "close": None}
    points = share_cvd(candles, session)[-10:]
    stamps = [time_or_none(row["time"]) for row in points]
    if (len(points) != 10 or stamps[-1] != price_close
            or any(b - a != MINUTE for a, b in zip(stamps, stamps[1:]))):
        return {"label": label, "delta": None, "close": None}
    return {"label": label, "delta": sum(row["delta"] for row in points), "close": points[-1]["time"]}


def movement_status(plan):
    """Describe what has happened in price separately from the entry decision."""
    price = plan["movement"]
    if plan["data_reasons"] or plan["status"] == "AVOID" or plan["confidence"] == "Waiting for data":
        return "NO TRADE"
    if plan["status"] in ("ENTER LONG", "ENTER SHORT"):
        return "CONFIRMED MOVE"
    if plan.get("confirmed") and not price["range_hold"]:
        return "LONG TRIGGER" if plan["direction"] > 0 else "SHORT TRIGGER"
    if plan.get("near_boundary") and (price["range_hold"] or plan["setup"] == "Breakout watch"):
        return "BREAKOUT WATCH"
    if plan["status"] == "GET READY" or (plan["regime"] == "Compressing" and plan["flow"].get("accelerating")):
        return "PREPARING"
    if price["market"] == "SIDEWAYS":
        return "SIDEWAYS"
    return "NO TRADE"
