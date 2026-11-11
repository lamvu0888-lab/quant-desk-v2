"""Prospective verification and causal replay of underlying paper setups."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

import numpy as np

from data_utils import utc
from dealer_hedging import price_response
from decision_engine import ENTRY_STATES, RULES_VERSION, make_trade_plan, paper_outcome
from decision_flow import model_summary
from desk_analytics import FIVE_MINUTES, MINUTE, candles_on, price_levels, realized_regime
from iv_decision import iv_read
from market_session import ET, rth_bounds
from terminal_data import chart_buckets, time_or_none
from trade_probability import MODEL_VERSION, STRATEGIES, evaluate_plan, path_outcomes, summarize_paths

MIN_SAMPLE = 60
MIN_SESSIONS = 8


def settle_candidates(journal, symbol, candles, now):
    resolved = []
    for event in journal.records(symbol, "candidate", limit=150, pending=True):
        result = paper_outcome(event["payload"]["plan"], candles, now)
        if result and journal.settle(event["id"], result):
            resolved.append((event, result))
    return resolved


def hedge_outcomes(event, candles, now):
    """Compare each stress projection with the exact subsequent completed minute."""
    p = event["payload"]
    origin = utc(event["origin"])
    first = origin.replace(second=0, microsecond=0)
    if first < origin:
        first += MINUTE
    closes = {utc(row["time_utc"]) + MINUTE: row["close"] for row in candles
              if row.get("closed", True) and first <= utc(row["time_utc"]) < first + 30 * MINUTE
              and utc(row["time_utc"]) + MINUTE <= now}
    rows = []
    for horizon in sorted({row["horizon"] for row in p["scenarios"]}):
        at = first + timedelta(minutes=horizon)
        if now < at + 2 * MINUTE and at not in closes:
            return None
        scenarios = [row for row in p["scenarios"] if row["horizon"] == horizon]
        if at not in closes:
            rows.extend({"horizon": horizon, "scenario": row["scenario"], "at": at.isoformat(),
                         "observed_price": None, "scenario_error": None} for row in scenarios)
            continue
        outcome = price_response(p["spot"], p["snapshot"], closes[at], horizon)
        rows.extend({"horizon": horizon, "scenario": row["scenario"], "at": at.isoformat(), **outcome,
                     "scenario_error": outcome["observed_price"] - row["price"],
                     "pressure_agreed": int(np.sign(outcome["move"])) == int(np.sign(row["total"]))
                     if row["scenario"] == "Flat / IV flat" and row["total"] and outcome["move"] else None,
                     "sign_matched": int(np.sign(outcome["move"])) == int(np.sign(row["move"]))}
                    for row in scenarios)
    return {"outcomes": rows}


def settle_hedges(journal, symbol, candles, now):
    for event in journal.records(symbol, "hedge", limit=100, pending=True):
        result = hedge_outcomes(event, candles, now)
        if result:
            journal.settle(event["id"], result)


def pressure_review(events):
    """Check ex-ante time-only pressure against subsequent returns with disjoint windows."""
    rows = []
    for horizon in (5, 10, 15):
        sample, next_origin = [], None
        for event in sorted(events, key=lambda item: item["origin"]):
            origin = utc(event["origin"])
            if next_origin and origin < next_origin:
                continue
            outcome = next((row for row in (event["outcome"] or {}).get("outcomes", [])
                            if row["horizon"] == horizon and row["scenario"] == "Flat / IV flat"), {})
            if outcome.get("observed_price") is None:
                continue
            next_origin = utc(outcome["at"])
            expected = next(row for row in event["payload"]["scenarios"]
                            if row["horizon"] == horizon and row["scenario"] == "Flat / IV flat")
            sample.append((outcome, expected, event["payload"]["snapshot"]["gex"]))
        paired = [item[0]["pressure_agreed"] for item in sample if item[0].get("pressure_agreed") is not None]
        buys = [outcome["return_bps"] for outcome, expected, _ in sample if (expected["total"] or 0) > 0]
        sells = [outcome["return_bps"] for outcome, expected, _ in sample if (expected["total"] or 0) < 0]
        pos = [abs(outcome["return_bps"]) for outcome, _, gex in sample if gex > 0]
        neg = [abs(outcome["return_bps"]) for outcome, _, gex in sample if gex < 0]
        rows.append({"Horizon": f"{horizon}m", "Disjoint outcomes": len(sample), "Nonzero comparisons": len(paired),
                     "Pressure / return agreement": float(np.mean(paired)) if paired else None,
                     "Buy-pressure mean return (bps)": float(np.mean(buys)) if buys else None,
                     "Sell-pressure mean return (bps)": float(np.mean(sells)) if sells else None,
                     "Positive GEX mean absolute move (bps)": float(np.mean(pos)) if pos else None,
                     "Negative GEX mean absolute move (bps)": float(np.mean(neg)) if neg else None})
    return rows


def paper_records(events, *, before=None, hold=None, setup=None):
    """Keep like-for-like, nonoverlapping, fully matured predictions."""
    eligible = []
    next_origin = {}
    for record in sorted(events, key=lambda row: row["origin"]):
        p, result = record["payload"], record.get("outcome") or {}
        plan, pred = p["plan"], p["model"]
        if (plan.get("demo") or plan.get("rule_version") != RULES_VERSION or pred.get("version") != MODEL_VERSION
                or result.get("r") is None or pred.get("p_tp1") is None or pred.get("baseline_p_tp1") is None
                or (hold is not None and plan.get("hold") != hold) or (setup is not None and plan.get("setup") != setup)):
            continue
        origin = utc(record["origin"])
        known = time_or_none(result.get("at"))
        if known is None or (before and (known >= before or origin >= before)):
            continue
        symbol = plan.get("symbol")
        if symbol in next_origin and origin < next_origin[symbol]:
            continue
        next_origin[symbol] = origin + timedelta(minutes=plan["hold"] + 1)
        eligible.append(record)
    return eligible


def session_intervals(rows):
    by_day = defaultdict(list)
    for row in rows:
        p, pred, observed = row["payload"]["plan"], row["payload"]["model"], row["outcome"]
        event = float(observed["tp1_first"])
        a, b = pred["p_tp1"], pred["baseline_p_tp1"]
        by_day[p["session"]].append(((b - event) ** 2 - (a - event) ** 2,
                                      a - event, observed["r"], pred["ev_r"] - observed["r"]))
    groups = [np.asarray(group) for group in by_day.values()]
    rng = np.random.default_rng(2505)
    totals = np.array([g.sum(axis=0) for g in groups])
    counts = np.array([len(g) for g in groups])
    choices = rng.integers(0, len(groups), (1200, len(groups)))
    estimates = totals[choices].sum(axis=1) / counts[choices].sum(axis=1)[:, None]
    return np.quantile(estimates, [.025, .975], axis=0).T.tolist()


def performance(rows):
    if not rows:
        return {"n": 0, "qualified": False, "expectancy_qualified": False, "calibration": []}
    y = np.array([bool(row["outcome"]["tp1_first"]) for row in rows], dtype=float)
    a = np.array([row["payload"]["model"]["p_tp1"] for row in rows])
    b = np.array([row["payload"]["model"]["baseline_p_tp1"] for row in rows])
    r = np.array([row["outcome"]["r"] for row in rows])
    equity = np.r_[0, np.cumsum(r)]
    report = {"n": len(rows), "sessions": len({row["payload"]["plan"]["session"] for row in rows}),
              "brier": float(np.mean((a - y) ** 2)), "baseline_brier": float(np.mean((b - y) ** 2)),
              "even_brier": float(np.mean((.5 - y) ** 2)), "probability_mean": float(a.mean()),
              "tp1_frequency": float(y.mean()), "false_positive_rate": float(np.mean(a[y == 0] >= .5)) if np.any(y == 0) else None,
              "false_alert_fraction": float(np.mean(y[a >= .5] == 0)) if np.any(a >= .5) else None,
              "win_rate": float(np.mean(r > 0)), "realized_ev": float(r.mean()),
              "drawdown": float(np.max(np.maximum.accumulate(equity) - equity)),
              "qualified": False, "expectancy_qualified": False, "calibration": []}
    for low, high in zip(np.arange(5) / 5, np.arange(1, 6) / 5):
        mask = (a >= low) & ((a < high) if high < 1 else (a <= high))
        if mask.any():
            report["calibration"].append({"band": f"{low:.0%}–{high:.0%}", "n": int(mask.sum()),
                                          "predicted": float(a[mask].mean()), "observed": float(y[mask].mean())})
    if len(rows) >= MIN_SAMPLE and report["sessions"] >= MIN_SESSIONS:
        improvement, bias, outcome, ev_error = session_intervals(rows)
        report.update(realized_ev_ci=outcome, improvement_ci=improvement, calibration_bias_ci=bias, ev_error_ci=ev_error,
                      qualified=improvement[0] > 0 and report["brier"] < report["even_brier"]
                      and abs(report["probability_mean"] - report["tp1_frequency"]) <= .10
                      and max(abs(bias[0]), abs(bias[1])) <= .16
                      and int(y.sum()) >= 10 and int((1 - y).sum()) >= 10,
                      expectancy_qualified=max(abs(ev_error[0]), abs(ev_error[1])) <= .35)
    return report


def subgroup_report(rows):
    groups = defaultdict(list)
    for record in rows:
        plan = record["payload"]["plan"]
        stamp = utc(record["origin"]).astimezone(ET)
        part = "Open–10:30" if (stamp.hour, stamp.minute) < (10, 30) else "10:30–14:00" if stamp.hour < 14 else "14:00–close"
        for label, value in (("Ticker", plan["symbol"]), ("Time of day", part),
                             ("Setup", plan.get("setup")), ("Regime", plan.get("regime")),
                             ("Decision", record["payload"].get("decision_status"))):
            if value:
                groups[label, value].append(record)
    return [{"group": group, "value": value, **performance(group_rows)}
            for (group, value), group_rows in sorted(groups.items())]


def walk_forward_replay(candles, flow_rows, session, *, symbol, dte=None, paths=400, limit=20):
    """Recreate candidates at historical 5m closes using only then-completed price bars."""
    bounds = rth_bounds(session)
    if not bounds:
        return []
    _, end = bounds
    buckets = chart_buckets(candles, 5)
    today = candles_on(buckets, session)
    out = []
    next_origin = None
    for i in range(9, len(today)):
        origin = utc(today[i]["time_utc"]) + FIVE_MINUTES
        if next_origin is not None and origin < next_origin:
            continue
        remaining = int((end - origin).total_seconds() // 60)
        if remaining <= 15:
            break
        earlier = [row for row in candles if utc(row["time_utc"]) + MINUTE <= origin]
        past_buckets = [row for row in buckets if utc(row["time_utc"]) + FIVE_MINUTES <= origin]
        flow = [row for row in flow_rows if utc(row["time"]) + FIVE_MINUTES <= origin]
        if not flow or utc(flow[-1]["time"]) + FIVE_MINUTES != origin:
            continue
        price, regime = today[i]["close"], realized_regime(past_buckets)
        plan = make_trade_plan(candles=earlier, buckets=past_buckets, flow_rows=flow, levels=price_levels(past_buckets, session),
                               now=origin, session=session, expiry=(session + timedelta(days=dte)).isoformat() if dte is not None else None,
                               dte=dte, spot=price, regime=regime, iv=iv_read({}, None, origin, price, regime, usable=False),
                               gamma={}, quality_reasons=[], quote_at=origin.isoformat(), minutes_left=remaining, source="Replay")
        if plan["status"] not in ENTRY_STATES:
            continue
        plan.update(symbol=symbol, origin=origin.isoformat(), underlying_cost_bps=1.)
        prediction = model_summary(earlier, plan, origin, n=paths, include_stress=False)
        outcome = paper_outcome(plan, candles, end)
        if outcome and outcome.get("r") is not None and prediction.get("p_tp1") is not None:
            following = [row for row in candles if origin <= utc(row["time_utc"]) < origin + plan["hold"] * MINUTE]
            variants = {}
            if len(following) == plan["hold"] and all(utc(row["time_utc"]) == origin + i * MINUTE
                                                     for i, row in enumerate(following)):
                compare = evaluate_plan(earlier, plan, origin, n=paths, include_stress=False)["models"]["Conditioned block"]
                actual = {name: np.array([[row[name] for row in following]]) for name in ("low", "high", "open")}
                actual["close"] = np.array([[price] + [row["close"] for row in following]])
                for method in STRATEGIES:
                    realized = path_outcomes(actual, plan, method)
                    if realized is not None and method in compare:
                        summary = summarize_paths(actual, realized, plan)
                        variants[method] = {"predicted_fill": compare[method]["p_fill"], "filled": summary["p_fill"],
                                            "r": summary.get("ev_r", 0.)}
            out.append({"origin": origin.isoformat(), "payload": {"plan": plan, "model": prediction},
                        "outcome": outcome, "variants": variants})
            next_origin = origin + timedelta(minutes=plan["hold"] + 1)
            if len(out) == limit:
                break
    return out


def replay_variants(rows):
    groups = defaultdict(list)
    for record in rows:
        for method, row in record.get("variants", {}).items():
            groups[method].append(row)
    return [{"Entry": method, "Origins": len(sample), "Mean estimated fill": float(np.mean([r["predicted_fill"] for r in sample])),
             "Replay fill rate": float(np.mean([r["filled"] for r in sample])),
             "Replay mean R per opportunity": float(np.mean([r["r"] for r in sample]))}
            for method, sample in groups.items()]
