"""After-close research using paper option fills and chronological validation."""
from collections import Counter
from statistics import mean, stdev

import numpy as np

from market_session import rth_bounds
from paper_engine import realized
from terminal_data import time_or_none

FEATURES = ("score", "spread", "DTE", "moneyness", "fast setup", "reward/risk")


def features(trade):
    plan = trade["plan"]
    return [plan["score"] / 100, trade["spread"], min(14, trade["dte"]) / 14,
            plan["direction"] * (trade["strike"] / trade["spot"] - 1),
            float(trade["fast"]), float(plan.get("rr") or 0)]


def train(rows):
    x = np.asarray([features(row) for row in rows], dtype=float)
    y = np.asarray([realized(row) / row["cost"] for row in rows])
    center, scale = x.mean(axis=0), np.maximum(x.std(axis=0), 1e-6)
    z = (x - center) / scale
    weights = np.linalg.solve(z.T @ z + 8 * np.eye(z.shape[1]), z.T @ (y - y.mean()))
    return center, scale, weights, float(y.mean())


def day_report(rows, session):
    _, cutoff = rth_bounds(session)
    day = str(session)
    settled = [t for t in rows if t["state"] == "CLOSED" and t["session"] <= day
               and time_or_none(t["closed"]) <= cutoff]
    today = [t for t in settled if t["session"] == day]
    skips = Counter(t["reason"] for t in rows if t["session"] == day and t["state"] == "SKIPPED")
    cohorts = [{"Contract rule": label, "Closed": len(group),
                "Net P/L": round(sum(map(realized, group)), 2),
                "Mean return %": round(mean([100 * realized(t) / t["cost"] for t in group]), 2)}
               for fast, label in ((True, "Fast · 0DTE"), (False, "Slower · next listed"))
               if (group := [t for t in today if t["fast"] == fast])]

    sessions = sorted({t["session"] for t in settled})
    effects, baseline, selected, seen, kept = [], 0, 0, 0, 0
    for session_day in sessions[-12:]:
        opened, _ = rth_bounds(session_day)
        prior = [t for t in settled if t["session"] < session_day and time_or_none(t["closed"]) < opened]
        if len(prior) < 60 or len({t["session"] for t in prior}) < 8:
            continue
        center, scale, weights, intercept = train(prior)
        test = [t for t in settled if t["session"] == session_day]
        picks = [t for t in test if (np.asarray(features(t)) - center) / scale @ weights + intercept > 0]
        all_pnl, pick_pnl = sum(map(realized, test)), sum(map(realized, picks))
        effects.append(pick_pnl - all_pnl)
        baseline += all_pnl
        selected += pick_pnl
        seen += len(test)
        kept += len(picks)

    lower = None
    if len(effects) >= 5 and seen >= 30 and .30 <= kept / seen < 1:
        lower = mean(effects) - 2 * stdev(effects) / len(effects) ** .5
    if lower is not None and lower > 0:
        recommendation = "Run a parallel paper test of the positive predicted return filter."
    elif len(settled) < 60 or len(sessions) < 8:
        recommendation = "Keep collecting fills. Aim for 60 closed trades across eight sessions before testing a filter."
    elif lower is None:
        recommendation = "Continue collecting independent sessions to evaluate the model."
    else:
        recommendation = "Keep the current entry rules. The model filter has no clear advantage in the replay."

    model = None
    if settled:
        _, _, weights, _ = train(settled)
        model = [{"Input": name, "Estimated return change (pp per std. dev.)": round(100 * float(w), 3)}
                 for name, w in sorted(zip(FEATURES, weights), key=lambda pair: -abs(pair[1]))]
    return {"day": day, "closed": len(today), "pnl": round(sum(map(realized, today)), 2),
            "trades": len(settled), "sessions": len(sessions), "model": model,
            "cohorts": cohorts, "skips": dict(skips), "recommendation": recommendation,
            "test_sessions": len(effects), "seen": seen, "kept": kept,
            "baseline": round(baseline, 2), "selected": round(selected, 2)}
