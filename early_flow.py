"""Paper-only pressure test. All minute observations must end at the same price close."""
from statistics import median
from market_session import ET
from datetime import timedelta

from entry_score import MIN_ENTRY_SCORE
from decision_engine import MIN_REWARD_R
from desk_analytics import share_cvd
from market_session import rth_bounds
from terminal_data import numeric, time_or_none

MINUTE = timedelta(minutes=1)
EARLY = "Early 3m"
STANDARD = "Standard 5m"
FLOW_WAITS = {"Directional options-flow confirmation.",
              "Two consecutive completed 5m options-flow intervals in the setup direction."}


def direction(values):
    if not values or any(value is None for value in values):
        return "Unavailable"
    if all(value > 0 for value in values):
        return "Bullish"
    if all(value < 0 for value in values):
        return "Bearish"
    return "Neutral"


def window(rows, field, times, *, starts=False):
    values = {}
    for row in rows:
        at = time_or_none(row.get("time"))
        if starts and at:
            at += MINUTE
        if row.get("closed") is False or row.get("forming") or row.get("stale"):
            continue
        if at in times:
            values[at] = None if at in values else numeric(row.get(field))
    return [values.get(t) for t in times]


def confirmation(plan, now, flow, points, cvd_label, candles):
    side = plan["direction"]
    wanted = {1: "Bullish", -1: "Bearish"}.get(side)
    close = time_or_none(plan.get("entry_bar"))
    bounds = rth_bounds(plan["session"])
    times = [close - i * MINUTE for i in (2, 1, 0)] if close else []
    recent = bool(times and bounds and bounds[0] < times[0] and close <= now < bounds[1]
                  and now - close < MINUTE and now >= bounds[0])
    meta = flow.snapshot.meta if flow.snapshot else {}
    received = flow.snapshot.received_at if flow.snapshot else None
    flow_ok = bool(recent and received and 0 <= (now - received).total_seconds() <= 90
                   and not flow.error and meta.get("interval") == "1m"
                   and not meta.get("sandbox") and not meta.get("delayed")
                   and numeric(meta.get("delay_minutes", 0)) == 0)
    signed = window(flow.rows, "net_premium_usd", times, starts=True) if flow_ok else []
    delta = window(points, "delta", times) if recent else []
    net, cvd = direction(signed), direction(delta)
    # At least one minute's share of the strategy's existing five-minute premium floor.
    floor = numeric(plan["flow"].get("floor"))
    participation = bool(floor and net == wanted and min(abs(n) for n in signed) >= floor / 5)
    prices = {time_or_none(b["time_utc"]) + MINUTE: b["close"] for b in candles[-8:]
              if time_or_none(b.get("time_utc")) and b.get("closed", True) and not b.get("forming")}
    sequence = [prices.get(t) for t in ([times[0] - MINUTE] + times if times else [])]
    changes = [side * (b - a) for a, b in zip(sequence, sequence[1:])] if sequence and None not in sequence else []
    acceleration = bool(changes and min(changes) > 0 and changes[-1] > median(changes[:2]))
    trigger = numeric(plan.get("trigger"))
    structure = bool(recent and plan.get("confirmed") and trigger and sequence and sequence[-1]
                     and side * (sequence[-1] - trigger) > 0)
    move = plan["movement"]
    reasons = [r for r in plan.get("missing", []) if r not in FLOW_WAITS]
    reasons += list(plan.get("data_reasons", [])) + list(plan.get("blocks", []))
    if plan.get("range_reversal"):
        reasons.append("Range reversals retain their standard confirmation.")
    if plan.get("demo") or not plan.get("price_context_ready") or not recent:
        reasons.append("Current regular-session price and latest completed minute required.")
    if not wanted or not move.get("price_confirmed") or plan["ema"]["5m"]["direction"] != side:
        reasons.append("Confirmed directional 5m trend required.")
    if net != wanted or not participation:
        reasons.append("Three consecutive aligned 1m net premium bins above the participation floor required.")
    if cvd != wanted:
        reasons.append("Three synchronized observed footprint deltas in the setup direction required.")
    if not structure:
        reasons.append("Waiting for a completed 1m entry trigger and hold.")
    if not acceleration:
        reasons.append("Waiting for accelerating 1m price momentum.")
    if plan["score"] < MIN_ENTRY_SCORE:
        reasons.append(f"Evidence {plan['score']}/100 is below {MIN_ENTRY_SCORE}.")
    if not (plan.get("rr") and plan["rr"] >= MIN_REWARD_R) or plan.get("premium_review_required"):
        reasons.append("Reward/risk or premium review blocks the candidate.")
    if not (plan.get("option_quote") or {}).get("ready"):
        reasons.append("Waiting for an executable exact-expiry option quote.")
    reasons = list(dict.fromkeys(reasons))
    observed = net != "Unavailable" and cvd != "Unavailable"
    status = ("AVOID" if plan.get("blocks") else "EARLY PAPER CANDIDATE" if not reasons
              else "EARLY MOVE DETECTED" if wanted and net == cvd == wanted and recent
              else "GET READY" if plan["status"] == "GET READY" else "WAIT")
    five = plan["flow_confirmation"]
    five_close = time_or_none(plan.get("bar"))
    five_delta = window(points, "delta", [five_close - i * MINUTE for i in range(4,-1,-1)]) if five_close else []
    source = cvd_label
    five_source = source
    estimated = False
    if (not five_delta or None in five_delta) and five_close and plan.get("symbol") in ("SPY", "QQQ"):
        estimates = share_cvd(candles, five_close.astimezone(ET).date())
        five_delta = window(estimates, "delta", [five_close - i * MINUTE for i in range(4,-1,-1)])
        estimated = bool(five_delta and None not in five_delta)
        if estimated:
            five_source = "Estimated share CVD from signed candle volume"
    if five_delta and None not in five_delta:
        five_cvd = direction([sum(five_delta)])
    else:
        five_cvd = "Unavailable"
    std_quality = "Observed" if five.get("intervals") else "Unavailable"
    if estimated and std_quality == "Observed":
        std_quality = "Observed flow / estimated CVD"
    if plan.get("data_reasons"):
        std_quality = "Stale / unavailable"
    return {"status": status, "reasons": reasons, "early": {
        "Netflow": net, "CVD": cvd, "CVD source": source, "Price confirmation": "Confirmed" if structure else "Waiting",
        "Momentum": "Accelerating" if acceleration else "Weakening" if changes else "Unavailable",
        "Data quality": "Observed" if observed else "Stale" if close and not recent else "Unavailable"},
        "standard": {"Netflow": direction([i["net"] for i in five.get("intervals", [])]),
                     "CVD": five_cvd, "CVD source": five_source,
                     "Price confirmation": "Confirmed" if move.get("price_confirmed") else "Waiting",
                     "Momentum": "Accelerating" if move.get("accelerating") else "Weakening" if move.get("closed_bars") else "Unavailable",
                     "Data quality": std_quality}}
