"""Explainable intraday setup rules for the dashboard and the alert worker."""
from __future__ import annotations

from datetime import timedelta
from statistics import median

from data_utils import utc
from desk_analytics import FIVE_MINUTES, MINUTE, candles_on, flow_read, sign
from terminal_data import chart_buckets, numeric, positive, time_or_none

RULES_VERSION = "2.5.0"
MIN_REWARD_R = 1.35
ENTRY_STATES = ("ENTER LONG", "ENTER SHORT")


def contiguous_tail(bars, width):
    if not bars:
        return []
    start = len(bars) - 1
    while start and utc(bars[start]["time_utc"]) - utc(bars[start - 1]["time_utc"]) == width:
        start -= 1
    return bars[start:]


def ema(closes, period):
    if len(closes) < period:
        return None
    weight = 2 / (period + 1)
    result = sum(closes[:period]) / period
    for close in closes[period:]:
        result += weight * (close - result)
    return result


def timing(bars, noise):
    if len(bars) < 4:
        return {"direction": 0, "ema8": None, "ema10": None}
    closes = [bar["close"] for bar in bars[-80:]]
    fast, slow = ema(closes, 8), ema(closes, 10)
    displacement = closes[-1] - closes[-4]
    direction = sign(displacement) if abs(displacement) >= noise else 0
    if fast is not None and slow is not None and (
            direction * (fast - slow) <= 0 or direction * (closes[-1] - slow) <= 0):
        direction = 0
    return {"direction": direction, "ema8": fast, "ema10": slow}


def range_break(bars, tolerance):
    """Return the most recent boundary broken from a range known at that bar."""
    for i in range(len(bars) - 1, max(5, len(bars) - 5) - 1, -1):
        earlier = bars[max(0, i - 12):i]
        high, low = max(row["high"] for row in earlier), min(row["low"] for row in earlier)
        close, prior = bars[i]["close"], bars[i - 1]["close"]
        if prior <= high and close > high + tolerance:
            return 1, high, utc(bars[i]["time_utc"]) + FIVE_MINUTES
        if prior >= low and close < low - tolerance:
            return -1, low, utc(bars[i]["time_utc"]) + FIVE_MINUTES
    return 0, None, None


def make_trade_plan(*, candles, buckets, flow_rows, levels, now, session, expiry, dte,
                    spot, regime, iv, gamma, quality_reasons, quote_at, minutes_left, source, preview=False):
    """Use completed bars only. Quote drift and structural room are separate gates."""
    today = candles_on(candles, session)
    bars5 = contiguous_tail(candles_on(buckets, session), FIVE_MINUTES)
    bars3 = contiguous_tail(chart_buckets(today, 3), 3 * MINUTE)
    bars1 = contiguous_tail(chart_buckets(today, 1), MINUTE)
    atr = positive(regime.get("atr"))
    flow = flow_read(flow_rows)
    flow_dir = flow.get("direction", 0)
    stamp5 = utc(bars5[-1]["time_utc"]) + FIVE_MINUTES if bars5 else None
    stamp3 = utc(bars3[-1]["time_utc"]) + 3 * MINUTE if bars3 else None
    stamp1 = utc(bars1[-1]["time_utc"]) + MINUTE if bars1 else None
    price = bars1[-1]["close"] if bars1 else None
    noise = max((price or 1) * .00006, (atr or 0) * .07)
    tf = {"5m": timing(bars5, 2 * noise), "3m": timing(bars3, noise), "1m": timing(bars1, .6 * noise)}
    trend, align3, align1 = (tf[period]["direction"] for period in ("5m", "3m", "1m"))
    bias = {1: "Bullish", -1: "Bearish", 0: "Neutral"}[trend]
    if trend * flow_dir < 0:
        bias = "Mixed"
    checks, missing, conflicts = [], list(quality_reasons), []
    stops = []
    result = {"status": "WAIT", "direction": trend, "bias": bias, "setup": None, "entry": None, "trigger": None,
              "trigger_text": None, "invalidation": None, "stop": None, "tp1": None, "tp2": None, "rr": None,
              "rr2": None, "target_note": None, "hold": min(15, max(0, minutes_left - 1)), "scope": expiry or "All DTE",
              "dte": dte, "expiry": expiry, "session": str(session), "price": spot, "source": source,
              "detected": now.isoformat(), "bar": stamp5.isoformat() if stamp5 else None,
              "entry_bar": stamp1.isoformat() if stamp1 else None, "quote_at": quote_at,
              "regime": regime.get("label"), "iv_valuation": iv["valuation"], "atm_iv": iv["atm"],
              "iv_ratio": iv["ratio"], "gex": numeric(gamma.get("total")), "flow": flow, "ema": tf,
              "evidence": checks, "missing": missing, "conflicts": conflicts, "blocks": stops, "score": 0,
              "demo": preview, "rule_version": RULES_VERSION, "validation": "Unvalidated"}
    if not stamp1 or not 0 <= (now - stamp1).total_seconds() <= 120:
        missing.append("Recent completed 1-minute candles.")
    if not stamp3 or not 0 <= (now - stamp3).total_seconds() <= 240:
        missing.append("Recent completed 3-minute candles.")
    if len(bars5) < 10 or not atr:
        missing.append("Ten contiguous 5-minute bars and a readable price regime.")
    if result["hold"] < 5:
        missing.append("At least five tradable minutes after the decision.")
    core_ready = not missing
    result["confidence"] = "Synchronized" if core_ready else "Waiting for data"
    if not price or not atr or len(bars1) < 4 or len(bars5) < 6:
        return result
    if not trend:
        missing.append("Directional 5-minute trend.")
    else:
        checks.append(f"5m EMA 8/10 and price confirm a {bias.lower() if bias != 'Mixed' else 'price'} trend.")
    if trend and flow_dir == trend:
        checks.append("Recent signed options premium confirms the 5m trend.")
    elif trend and flow_dir == -trend:
        conflicts.append("Options flow opposes the 5m trend.")
        stops.append("Price and options flow disagree.")
    else:
        missing.append("Directional options-flow confirmation.")
    accel = bool(trend and flow.get("accelerating") and trend * flow["momentum"] > 0)
    if accel:
        checks.append("Options flow strengthened in the setup direction.")
    elif trend and flow.get("accelerating") and trend * flow["momentum"] < 0:
        conflicts.append("Options-flow acceleration opposes the setup.")
    aligned3 = bool(trend and align3 == trend)
    aligned1 = bool(trend and align1 == trend)
    if aligned3 and aligned1:
        checks.append("Closed 3m and 1m candles agree with the 5m trend.")
    elif trend:
        missing.append("Aligned 3m and 1m entry timing.")
        if trend * align3 < 0 or trend * align1 < 0:
            conflicts.append("At least one entry timeframe is moving against the trend.")
    last, prior = bars1[-1], bars1[-2]
    volumes = [positive(row.get("volume")) for row in bars5[-21:-1]]
    relative_volume = positive(bars5[-1].get("volume"))
    relative_volume = (relative_volume / median(volumes) if relative_volume and len(volumes) >= 8
                       and all(value is not None for value in volumes) else None)
    result["volume_ratio"] = relative_volume
    if relative_volume is not None and relative_volume >= 1.25:
        checks.append(f"5m volume expanded to {relative_volume:.2f}× its recent median.")
    elif relative_volume is None:
        result["volume_note"] = "Native share volume unavailable."
    levels_by_name = {level.label: level.price for level in levels}
    hi, lo = levels_by_name.get("Range high"), levels_by_name.get("Range low")
    result["range"] = (lo, hi)
    level = hi if trend > 0 else lo if trend < 0 else None
    vwap = positive(last.get("session_vwap"))
    result["vwap"] = vwap
    break_dir, break_level, break_at = range_break(bars5, noise)
    result["break_level"] = break_level
    result["break_at"] = break_at.isoformat() if break_at else None
    result["break_direction"] = break_dir
    failed_break = bool(trend and break_dir == trend and trend * (price - break_level) < -noise)
    sweep_side = 0
    if hi and last["high"] > hi + noise and price < hi - noise:
        sweep_side = -1
    if lo and last["low"] < lo - noise and price > lo + noise:
        sweep_side = 0 if sweep_side else 1
    if sweep_side:
        (checks if sweep_side == trend else conflicts).append(
            "Price swept the prior range low and reclaimed it." if sweep_side > 0
            else "Price swept the prior range high and rejected it.")
    if failed_break:
        stops.append("The recent breakout failed back through its range boundary.")
    if sweep_side and sweep_side == -trend:
        stops.append("Price rejected the range edge against the proposed direction.")
    if not trend or level is None:
        if core_ready and stops:
            result["status"] = "AVOID"
        return result

    extreme = "low" if trend > 0 else "high"
    retest_pad = max(noise, .2 * atr)
    retest = bool(break_dir == trend and utc(last["time_utc"]) >= break_at
                  and abs(last[extreme] - break_level) <= retest_pad and trend * (price - break_level) >= noise)
    # Use EMA values and VWAP from before the confirming bar to avoid moving the test level.
    slow = timing(bars1[:-1], noise)["ema10"]
    fast = timing(bars1[:-1], noise)["ema8"]
    ema_retest = bool(fast and slow and trend * (fast - slow) > 0
                      and min(fast, slow) - retest_pad <= last[extreme] <= max(fast, slow) + retest_pad
                      and trend * (price - fast) >= noise and trend * (price - prior["close"]) > 0)
    prior_vwap = positive(prior.get("session_vwap"))
    vwap_test = bool(vwap and prior_vwap and trend * (price - vwap) >= noise
                     and (trend * (prior["close"] - prior_vwap) <= 0 or abs(last[extreme] - prior_vwap) <= retest_pad))
    if vwap and trend * (price - vwap) < -retest_pad:
        conflicts.append("Price is on the opposing side of session VWAP.")
    if retest:
        setup, trigger, confirmed = "Breakout and retest", break_level, True
    elif break_dir == trend and break_at == stamp5 and trend * (price - break_level) >= noise:
        setup, trigger, confirmed = "Breakout close", break_level, True
    elif ema_retest:
        setup, trigger, confirmed = "EMA 8/10 pullback", fast, True
    elif vwap_test:
        setup, trigger, confirmed = "VWAP reclaim" if trend > 0 else "VWAP rejection", vwap, True
    elif sweep_side == trend:
        setup, trigger, confirmed = "Liquidity sweep reclaim" if trend > 0 else "Liquidity sweep rejection", lo if trend > 0 else hi, True
    else:
        setup, trigger, confirmed = "Breakout watch", level, False
    # Pending geometry is anchored to the trigger plus its confirmation buffer.
    # Confirmed geometry uses a fresh quote and a distinct protective stop.
    entry = (spot or price) if confirmed else trigger + trend * noise
    recent_swing = min(row["low"] for row in bars1[-3:]) if trend > 0 else max(row["high"] for row in bars1[-3:])
    invalidation = trigger
    stop = (min(recent_swing, invalidation) if trend > 0 else max(recent_swing, invalidation)) - trend * max(noise, .13 * atr)
    entry, invalidation, stop, trigger = (round(value, 2) for value in (entry, invalidation, stop, trigger))
    risk = trend * (entry - stop)
    result.update(setup=setup, entry=entry, trigger=trigger, invalidation=invalidation, stop=stop,
                  confirmed=confirmed, stop_risk=risk, ema_level=round(slow, 2) if slow else None,
                  trigger_text=f"{setup}: {'close and hold above' if trend > 0 else 'close and hold below'} {trigger:,.2f} "
                               "with 1m/3m timing and 5m trend/flow agreement.")
    if confirmed:
        checks.append(f"{setup} detected on completed candles.")
        if spot and (trend * (spot - trigger) <= 0 or abs(spot - price) > max(.4 * atr, 3 * noise)):
            stops.append("The current quote no longer confirms the completed entry candle.")
    else:
        missing.append("A completed candle confirming the entry trigger.")
    if risk <= 0 or risk > 2.25 * atr:
        stops.append("Protective stop is invalid or wider than 2.25× 5m ATR.")
    if confirmed and trend * (entry - trigger) > max(.8 * atr, 5 * noise):
        stops.append("Price is too far beyond the trigger for a fresh entry.")
    if risk > 0:
        obstacles = [(trend * (level.price - entry), level.label) for level in levels
                     if level.kind in ("support", "resistance", "dealer", "dark")
                     and level.label not in ("Gamma flip", "Max pain")
                     and trend * (level.price - entry) > .005]
        room, label = min(obstacles, default=(float("inf"), None))
        result["opposing_level"] = label
        if label and room / risk < MIN_REWARD_R:
            stops.append(f"{label} is only {room / risk:.2f}R from entry.")
        first = min(1.5 * risk, room)
        second = max(2.5 * risk, first + .5 * risk)
        if label:
            result["target_note"] = (f"{label} caps the available room before continuation."
                                     if room <= second else f"Nearest opposing level is {label}, beyond TP2.")
        remaining = iv["remaining_up"] if trend > 0 else iv["remaining_down"]
        if remaining is not None and remaining < risk:
            stops.append("The matching provider expected-move band leaves less than 1R of headroom.")
        target1, target2 = round(entry + trend * first, 2), round(entry + trend * second, 2)
        result.update(tp1=target1, tp2=target2, rr=round(trend * (target1 - entry) / risk, 2),
                      rr2=round(trend * (target2 - entry) / risk, 2), expected_remaining=remaining)
    if iv["valuation"] == "Rich vs trailing RV":
        conflicts.append("Selected IV is rich versus trailing realized volatility. Check quoted premium and execution costs.")
    result["score"] = (20 + 20 * (flow_dir == trend) + 10 * aligned3 + 10 * aligned1 + 20 * confirmed
                       + 10 * accel + 5 * (relative_volume is not None and relative_volume >= 1.25)
                       + 5 * bool(result["rr"] and result["rr"] >= MIN_REWARD_R))
    if not core_ready:
        result["status"] = "WAIT"
    elif stops:
        result["status"] = "AVOID"
    elif confirmed and aligned3 and aligned1 and flow_dir == trend and result["score"] >= 75:
        result["status"] = "ENTER LONG" if trend > 0 else "ENTER SHORT"
    elif flow_dir == trend and abs(price - trigger) <= .8 * atr:
        result["status"] = "GET READY"
    return result


def paper_outcome(plan, candles, now):
    """Track a captured underlying entry using completed bars after detection."""
    origin = time_or_none(plan.get("origin") or plan.get("detected"))
    if origin is None or plan.get("status") not in ENTRY_STATES:
        return None
    first = origin.replace(second=0, microsecond=0)
    if origin != first:
        first += MINUTE
    deadline = first + timedelta(minutes=plan["hold"])
    direction, entry, stop, target = (plan[key] for key in ("direction", "entry", "stop", "tp2"))
    risk = abs(entry - stop)
    if not risk:
        return None
    touched_first = False
    last = None
    for bar in candles:
        if not bar.get("closed", True):
            continue
        opened = utc(bar["time_utc"])
        if opened < first:
            continue
        if opened >= deadline or opened + MINUTE > now:
            break
        if opened != (first if last is None else last + MINUTE):
            return {"state": "MISSING BARS", "at": now.isoformat()} if now >= deadline + 2 * MINUTE else None
        last = opened
        adverse, favorable = (bar["low"], bar["high"]) if direction > 0 else (bar["high"], bar["low"])
        state, exit_price = None, None
        if direction * (adverse - stop) <= 0:
            state, exit_price = "STOP", bar["open"] if direction * (bar["open"] - stop) < 0 else stop
        elif direction * (favorable - target) >= 0:
            state, exit_price = "TP2", target
            touched_first = True
        else:
            touched_first |= direction * (favorable - plan["tp1"]) >= 0
            if direction * (bar["close"] - plan["invalidation"]) < 0:
                state, exit_price = "INVALIDATED", bar["close"]
            elif opened + MINUTE == deadline:
                state, exit_price = "TIME LIMIT", bar["close"]
        if state:
            cost = plan.get("underlying_cost_bps", 1.0) * entry / 10_000
            return {"state": state, "at": (opened + MINUTE).isoformat(), "price": exit_price,
                    "tp1_first": touched_first, "tp2_first": state == "TP2",
                    "minutes": int((opened + MINUTE - first).total_seconds() / 60),
                    "r": (direction * (exit_price - entry) - cost) / risk}
    return {"state": "MISSING BARS", "at": now.isoformat()} if now >= deadline + 2 * MINUTE else None
