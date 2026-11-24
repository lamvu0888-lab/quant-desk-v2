"""Prospective underlying targets for the paired Standard paper exit experiment."""
from math import floor
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from decision_engine import MIN_REWARD_R
from terminal_data import numeric, positive, time_or_none

POLICY = "Adaptive exits v1"


def propose(plan, atr, levels):
    """Freeze a hypothesis using only the signal snapshot. Stop and time limit stay structural."""
    entry, stop = positive(plan.get("entry")), positive(plan.get("stop"))
    side = plan.get("direction")
    atr = positive(atr)
    if side not in (-1, 1) or not entry or not stop or not atr:
        return None
    risk = side * (entry - stop)
    if risk <= 0 or not plan.get("confirmed") or not plan.get("price_context_ready"):
        return None
    box = plan.get("range_reversal")
    move = plan.get("movement") or {}
    strong = (plan.get("regime") == "Expanding" and move.get("accelerating")
              and move.get("price_confirmed") and move.get("momentum") == side
              and not move.get("range_hold") and (plan.get("flow_confirmation") or {}).get("confirmed"))
    profile = "Range rotation" if box else "Expanding trend" if strong else "Ordinary trend"
    # A shorter remaining plan has fewer completed 5m intervals in which to travel.
    windows = min(3, floor((plan.get("hold") or 0) / 5))
    if windows < 1:
        return None
    room = []
    signal_at = time_or_none(plan.get("detected"))
    for level in levels:
        if level.kind not in ("dealer", "support", "resistance", "dark") or level.label in ("Gamma flip", "Max pain"):
            continue
        if level.as_of:
            at = time_or_none(level.as_of)
            if not at or not signal_at or at > signal_at:
                continue
        distance = side * (level.price - entry)
        if distance > 0:
            room.append((distance, level.label))
    remaining = numeric(plan.get("expected_remaining"))
    if remaining is not None:
        room.append((remaining, "expected-move boundary"))
    if box:
        midpoint = positive(box.get("midpoint"))
        far = positive(box.get("high" if side == 1 else "low"))
        if not midpoint or not far:
            return None
        room.append((side * (far - entry), "opposite range edge"))
        first = side * (midpoint - entry)
        second = side * (far - entry)
    else:
        first = min(2 * risk, atr * (1.25 if strong else .85))
        second = min((3.5 if strong else 2.5) * risk, atr * windows * (1.25 if strong else .85))
    if room:
        cap, label = min(room)
        second = min(second, cap)
    else:
        label = None
    # Reserve room for a distinct second target when a boundary clips the move.
    first = min(first, .8 * second)
    # Round toward entry so a displayed target cannot cross a structural boundary.
    rounding = ROUND_FLOOR if side == 1 else ROUND_CEILING
    def price(distance):
        return float(Decimal(str(round(entry + side * distance, 8))).quantize(Decimal(".01"), rounding=rounding))
    tp1, tp2 = price(first), price(second)
    explanation = {"profile": profile, "policy": POLICY, "nearest_boundary": label}
    if tp2 <= 0 or side * (tp2 - tp1) <= 0 or side * (tp1 - entry) + 1e-9 < MIN_REWARD_R * risk:
        return {**explanation, "reason": f"Insufficient room for two targets and {MIN_REWARD_R}R to TP1."}
    return {**explanation, "tp1": tp1, "tp2": tp2, "rr": round(side * (tp1-entry)/risk, 2),
            "rr2": round(side * (tp2-entry)/risk, 2)}


def performance_by_profile(pairs, realized):
    """Include adaptive skips as cash in opportunity EV and report matched win/loss separately."""
    groups = {}
    for standard, trial in pairs:
        profile = trial["plan"]["adaptive_exit"]["profile"]
        groups.setdefault(profile, []).append((standard, trial))
    rows = []
    for profile, observations in sorted(groups.items()):
        settled = [(a, b) for a, b in observations if a["state"] == "CLOSED"
                   and b["state"] in ("CLOSED", "SKIPPED")]
        closed = [realized(b) for _, b in settled if b["state"] == "CLOSED"]
        wins = [p for p in closed if p > 0]
        losses = [-p for p in closed if p < 0]
        n = len(settled)
        baseline = sum(realized(a) for a, _ in settled)
        adaptive = sum(closed)
        rows.append({"Profile": profile, "Evaluated": len(observations), "Settled opportunities": n,
                     "Paired trades": len(closed), "Adaptive skips (settled)": n - len(closed),
                     "Baseline $ / opportunity": round(baseline/n, 2) if n else None,
                     "Adaptive $ / opportunity": round(adaptive/n, 2) if n else None,
                     "Adaptive avg win / avg loss": round((sum(wins)/len(wins))/(sum(losses)/len(losses)), 2)
                     if wins and losses else None})
    return rows
