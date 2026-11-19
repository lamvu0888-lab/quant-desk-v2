"""Cash-underlying range reversals evaluated on completed regular-session candles."""
from statistics import mean

from data_utils import utc
from desk_analytics import FIVE_MINUTES, MINUTE
from ema_trend import ema_timing
from movement_detector import MIN_ENTRY_SCORE, MIN_RELATIVE_VOLUME, signed_flow_confirmation
from terminal_data import numeric

LOOKBACK = 12
MIN_WIDTH_ATR = 2.5
MIN_WIDTH_FRACTION = .003
EDGE_FRACTION = .35
RANGE_SETUP = "Range reversal"


def established_range(bars):
    """Use the hour ending before the potential rejection starts."""
    if len(bars) != LOOKBACK:
        return None
    low, high = min(bar["low"] for bar in bars), max(bar["high"] for bar in bars)
    width, midpoint = high - low, (high + low) / 2
    previous = [bars[0]["open"], *(bar["close"] for bar in bars[:-1])]
    normal = mean(max(bar["high"], prior) - min(bar["low"], prior) for bar, prior in zip(bars, previous))
    if not normal or width < max(MIN_WIDTH_ATR * normal, MIN_WIDTH_FRACTION * midpoint):
        return None
    travel = sum(abs(bar["close"] - prior) for bar, prior in zip(bars, previous))
    drift = abs(bars[-1]["close"] - bars[0]["open"])
    if not travel or drift > min(.35 * travel, .50 * width):
        return None
    # Count visits to alternating ends. A candle spanning both ends has ambiguous order.
    visits, last = 0, 0
    for bar in bars:
        bottom, top = bar["low"] <= low + .20 * width, bar["high"] >= high - .20 * width
        edge = int(bottom) - int(top)
        if edge and edge != last:
            visits += 1
            last = edge
    if visits < 3:
        return None
    return {"low": low, "high": high, "midpoint": midpoint, "width": width, "width_atr": width / normal,
            "width_pct": 100 * width / midpoint, "visits": visits, "efficiency": drift / travel,
            "as_of": (utc(bars[-1]["time_utc"]) + FIVE_MINUTES).isoformat()}


def range_context(bars5, bars3, bars1, atr, noise):
    """Find a fresh 3m rejection against boundaries fixed before that candle."""
    if not bars1 or not atr:
        return None
    now = utc(bars1[-1]["time_utc"]) + MINUTE
    price = bars1[-1]["close"]
    watch = pending = None
    for test in reversed(bars3[-3:]):
        opened = utc(test["time_utc"])
        closed = opened + 3 * MINUTE
        if not 0 <= (now - closed).total_seconds() <= 360:
            continue
        before = [bar for bar in bars5[-(LOOKBACK + 4):] if utc(bar["time_utc"]) + FIVE_MINUTES <= opened]
        box = established_range(before[-LOOKBACK:])
        if not box:
            continue
        low, high, width = box["low"], box["high"], box["width"]
        as_of = utc(box["as_of"])
        after = [b for b in bars1[-15:] if utc(b["time_utc"]) >= as_of]
        if not after or utc(after[0]["time_utc"]) != as_of or not low < price < high:
            continue
        outside = [int(b["close"] > high + noise) - int(b["close"] < low - noise) for b in after]
        if any(a and a == b for a, b in zip(outside, outside[1:])):
            continue
        if watch is None:
            watch = {**box, "direction": 0}
        reach = min(.18 * width, .45 * atr)
        lower, upper = test["low"] <= low + reach, test["high"] >= high - reach
        direction = int(lower) - int(upper)
        if not direction:
            continue
        edge, wick, trigger = (low, test["low"], test["high"]) if lower else (high, test["high"], test["low"])
        reclaim = direction * (test["close"] - edge)
        spread = test["high"] - test["low"]
        if (not noise <= reclaim <= EDGE_FRACTION * width or direction * (wick - edge) < -.5 * atr
                or direction * (test["close"] - test["open"]) <= 0 or spread <= 0
                or direction * (test["close"] - wick) < .60 * spread or direction * (trigger - box["midpoint"]) >= 0):
            continue
        # A stopped or completed rotation cannot be entered again on a return to the edge.
        following = [b for b in after if utc(b["time_utc"]) >= closed]
        stop = (min(edge, wick) if lower else max(edge, wick)) - direction * max(noise, .13 * atr)
        if lower:
            invalidated = any(b["close"] <= edge or b["low"] <= stop or b["high"] >= box["midpoint"] for b in following)
        else:
            invalidated = any(b["close"] >= edge or b["high"] >= stop or b["low"] <= box["midpoint"] for b in following)
        if invalidated or not 0 < direction * (price - edge) <= EDGE_FRACTION * width:
            continue
        confirmed = bool(following and direction * (price - trigger) >= noise)
        candidate = {**box, "direction": direction, "edge": edge, "stop": stop, "trigger": trigger,
                     "rejection_at": closed.isoformat(), "follow_through": confirmed}
        if confirmed:
            return candidate
        if pending is None:
            pending = candidate
    return pending or watch


def ema_turn(history, width, reading, side):
    """Allow a turn before the 9/21 cross when their spread is improving inward."""
    previous = ema_timing(history[:-1], width)
    fast, slow = reading["ema9"], reading["ema21"]
    old_fast, old_slow = previous["ema9"], previous["ema21"]
    if None in (fast, slow, old_fast, old_slow):
        return False
    spread = side * (fast - slow)
    return (side * (history[-1]["close"] - fast) > 0 and side * (fast - old_fast) > 0
            and (spread > 0 or spread > side * (old_fast - old_slow)))


def reversal_plan(plan, box, *, minutes, threes, flow_rows, flow_usable, levels, atr, noise, iv,
                  dealer_scenario, min_reward):
    """Apply the reversal's own price confirmation and the shared market evidence."""
    side = box["direction"]
    missing, blocks, evidence = plan["missing"], plan["blocks"], plan["evidence"]
    ready_data = not missing
    movement, flow = plan["movement"], plan["flow"]
    movement.update(market="SIDEWAYS", playbook="Range mean reversion", range_hold=True)
    price = minutes[-1]["close"]
    near_edge = min(abs(price - box["low"]), abs(price - box["high"])) <= EDGE_FRACTION * box["width"]
    plan.update(near_boundary=near_edge, range_reversal=box, range=(box["low"], box["high"]), direction=side,
                setup=RANGE_SETUP if side else "Range reversal watch", bias="Range-bound",
                flow_confirmation=signed_flow_confirmation(flow_rows if flow_usable else [], flow, side, utc(plan["bar"])))
    evidence.append(f"Prior hour tested both sides of a {box['width_atr']:.1f}× average 5m true range.")
    if not side:
        missing.append("Wait for a completed rejection at support or resistance.")
        return plan
    edge, trigger, stop = (round(box[k], 2) for k in ("edge", "trigger", "stop"))
    spot = plan["price"]
    entry = round(spot or price, 2)
    location = "support" if side > 0 else "resistance"
    plan.update(bias="Bullish" if side > 0 else "Bearish", trigger=trigger, entry=entry, invalidation=edge, stop=stop,
                near_boundary=True, confirmed=box["follow_through"],
                trigger_text=f"A 3m candle rejected {location} at {edge:,.2f}. A later 1m close must clear "
                             f"{trigger:,.2f} toward the range midpoint.")
    evidence.append(f"The 3m candle rejected {location} and closed inside the range.")
    fast1 = ema_turn(minutes, MINUTE, plan["ema"]["1m"], side)
    fast3 = ema_turn(threes, 3 * MINUTE, plan["ema"]["3m"], side)
    agree = flow_usable and flow.get("direction") == side and plan["flow_confirmation"]["confirmed"]
    quote_ok = bool(spot and side * (spot - trigger) > 0 and abs(spot - price) <= max(.4 * atr, 3 * noise)
                    and 0 < side * (spot - edge) <= EDGE_FRACTION * box["width"])
    if not box["follow_through"]:
        missing.append("Waiting for a later completed 1m close through the rejection candle.")
    if not (fast1 and fast3):
        missing.append("Waiting for 1m and 3m EMA 9/21 timing to turn toward the midpoint.")
    if not agree:
        missing.append("Two matching 5m options-flow bars and the recent net premium must support the reversal.")
    if flow_usable and flow.get("direction") == -side:
        blocks.append("Signed options flow opposes the reversal.")
    if box["follow_through"] and not quote_ok:
        blocks.append("The current quote has left the confirmed entry area.")

    risk = side * (entry - stop)
    far = box["high"] if side > 0 else box["low"]
    limits = [(side * (far - entry), "Opposite range edge")]
    limits.extend((side * (level.price - entry), level.label) for level in levels
                  if level.kind in ("dealer", "support", "resistance", "dark")
                  and level.label not in ("Range high", "Range low", "Gamma flip", "Max pain")
                  and side * (level.price - entry) > 0)
    remaining = iv["remaining_up"] if side > 0 else iv["remaining_down"]
    if remaining is not None:
        limits.append((remaining, "Expected-move boundary"))
    room, obstacle = min(limits)
    # Leave distinct targets even when an intervening level caps the rotation.
    first = min(side * (box["midpoint"] - entry), room * 2 / 3)
    tp1, tp2 = round(entry + side * first, 2), round(entry + side * room, 2)
    rr = side * (tp1 - entry) / risk if risk > 0 else 0
    room_ok = 0 < risk <= 2.25 * atr and rr >= min_reward and side * (tp2 - tp1) > 0
    plan.update(tp1=tp1, tp2=tp2, rr=round(rr, 2), rr2=round(side * (tp2 - entry) / risk, 2) if risk > 0 else None,
                stop_risk=risk, opposing_level=obstacle, expected_remaining=remaining,
                target_note=f"Midpoint {box['midpoint']:,.2f}. {obstacle} caps TP2 at {tp2:,.2f}.")
    if not room_ok:
        blocks.append(f"Stop and target room require at least {min_reward:.2f}R to TP1 within the range.")

    proxy = plan.get("cvd_proxy") or {}
    delta = numeric(proxy.get("delta"))
    pressure_points = 0
    if delta is not None and side * delta > 0:
        pressure_points = {"footprint": 10, "estimated": 5}.get(proxy.get("quality"), 0)
    if delta is not None and side * delta < 0:
        plan["conflicts"].append("Available CVD pressure opposes the reversal.")
    gex = plan["gex"]
    if gex is not None and gex < 0 and plan["regime"] == "Expanding":
        blocks.append("Negative modeled GEX and expanding volatility favor a range break.")
    dealer_support = gex is not None and gex > 0
    if dealer_scenario is not None:
        box["dealer_scenario"] = dealer_scenario
        dealer_support = side * dealer_scenario["total"] > 0
    accel = agree and side * flow["momentum"] >= flow["floor"]
    volume = movement["volume"]
    vwap = plan["vwap"]
    confluence = any(level.kind in ("dealer", "dark", "support", "resistance")
                     and level.label not in ("Range high", "Range low", "Gamma flip", "Max pain")
                     and abs(level.price - edge) <= .35 * atr for level in levels)
    vwap_inside = vwap is not None and box["low"] < vwap < box["high"] and side * (vwap - entry) > 0
    scores = [
        ("Established sideways range", 15, 15), ("3m rejection and subsequent 1m confirmation", 15, 15 * box["follow_through"]),
        ("1m and 3m EMA 9/21 turn", 10, 10 * (fast1 and fast3)), ("Two aligned 5m options-flow intervals", 15, 15 * agree),
        ("Stop and target room", 10, 10 * room_ok), ("CVD in the reversal direction", 10, pressure_points),
        ("Modeled dealer support", 5, 5 * dealer_support), ("Inward flow acceleration", 5, 5 * accel),
        ("Expanded share activity", 5, 5 * (volume is not None and volume >= MIN_RELATIVE_VOLUME)),
        ("Expected-move room to TP1", 5, 5 * (remaining is not None and remaining >= first > 0)),
        ("VWAP inside the range or a matching edge level", 5, 5 * (vwap_inside or confluence)),
    ]
    plan["score_details"] = [{"factor": label, "possible": possible, "points": points,
                              "state": "Agrees" if points else "Missing or opposing"} for label, possible, points in scores]
    plan["score"] = sum(row["points"] for row in plan["score_details"])
    if plan["score"] < MIN_ENTRY_SCORE:
        missing.append(f"Reversal evidence is {plan['score']}/100. Entry needs {MIN_ENTRY_SCORE} or more.")
    if ready_data:
        if blocks:
            plan["status"] = "AVOID"
        elif not missing and quote_ok:
            plan["status"] = "ENTER LONG" if side > 0 else "ENTER SHORT"
            evidence.append("Confirmed edge rejection with inward EMA timing and aligned options flow.")
        else:
            plan["status"] = "GET READY"
    return plan
