"""Monitoring lifecycle shared across worker polling cycles."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from data_utils import utc
from dealer_hedging import hedge_change, hedge_scenarios, level_crossings
from decision_engine import ENTRY_STATES
from decision_flow import apply_model_gate, build_decision, get_hedge, model_summary
from desk_analytics import FIVE_MINUTES, MINUTE, candles_on
from market_session import ET, session_state
from movement_detector import ENTRY_COOLDOWN, MIN_ENTRY_SCORE
from research_journal import plan_scope
from terminal_data import feed_has_delay, positive
from trade_validation import paper_records, performance, settle_candidates, settle_hedges


def event_id(*parts):
    return ":".join(str(part) for part in parts)


def semantic_key(plan):
    """Ignore minor quote, target and score movement during the same setup."""
    status = plan["status"]
    if status in (*ENTRY_STATES, "GET READY", "AVOID", "EXIT"):
        return [status, plan.get("direction"), plan.get("setup")]
    return [status]


def notification(plan, status=None):
    return {**plan, "status": status or plan["status"], "triggered_at_utc": plan["detected"],
            "state": plan["status"], "notes": "Analysis and paper tracking only.",
            "explanation": (plan.get("blocks") or plan.get("missing") or plan.get("evidence") or
                            ["Await the next completed price and flow observations."])[0]}


def tracked_entry(payload):
    """Allow exits for pre-upgrade entries and newly reserved alerts."""
    return payload["decision_status"] in ENTRY_STATES and (
        "entry_alert_id" not in payload or bool(payload["entry_alert_id"]))


def process_scan(journal, ctx, read):
    plan, _ = build_decision(ctx, read)
    scope, symbol, now = plan_scope(plan), ctx.ticker, ctx.now
    market_open = session_state(now)["status"] == "OPEN"
    alerts = []
    entry_alert = None
    # Candles must be usable for outcomes even when options flow is missing.
    if not read["bars"].error and not feed_has_delay(read["bars"]) and read["candles"]:
        for event, outcome in settle_candidates(journal, symbol, read["candles"], now):
            payload = event["payload"]
            if tracked_entry(payload) and outcome.get("r") is not None:
                original = payload["plan"]
                finished = {**original, "status": "EXIT", "detected": now.isoformat(), "bar": outcome["at"],
                            "outcome": outcome, "price": outcome["price"], "model": payload["model"]}
                if payload.get("entry_alert_id"):
                    finished["entry_alert_id"] = payload["entry_alert_id"]
                # Keep the recorded outcome through outages. Send promptly during
                # the session and during the close-out pass.
                exit_id = event_id("exit", event["id"])
                exited = {**notification(finished), "semantic": ["EXIT", finished["direction"], finished["setup"]],
                          "quality": ["data", False], "explanation": f"Tracked setup closed by {outcome['state'].lower()}."}
                if journal.insert(exit_id, symbol, "decision", plan_scope(finished), now, exited) and now - utc(outcome["at"]) <= FIVE_MINUTES:
                    alerts.append((exit_id, exited))
        settle_hedges(journal, symbol, read["candles"], now)
    if not market_open:
        return alerts, plan

    prev = journal.records(symbol, "decision", scope=scope, limit=1)
    prior = prev[0]["payload"] if prev and prev[0]["payload"].get("session") == str(ctx.session) else {}
    before_key = prior.get("semantic")
    active = [event for event in journal.records(symbol, "candidate", scope=scope, pending=True, limit=8)
              if tracked_entry(event["payload"])]
    if active:
        plan["active_paper"] = True
        plan["status"] = "WAIT"
        plan["missing"].insert(0, "A prior paper setup is still being tracked.")
    if plan["status"] in ENTRY_STATES and (
            plan["score"] < MIN_ENTRY_SCORE or not plan["movement"]["price_confirmed"]
            or not plan["flow_confirmation"]["confirmed"] or plan["data_reasons"]):
        plan["status"] = "GET READY"
        plan["missing"].append("The worker is waiting for completed price, flow and score confirmation.")
    if plan["status"] in ENTRY_STATES:
        last = journal.records(symbol, "candidate", scope=scope, limit=1)
        prior_candidate = last[0]["payload"]["plan"] if last else {}
        if prior_candidate.get("entry_bar") == plan["entry_bar"]:
            prior_status = last[0]["payload"]["decision_status"]
            plan["status"] = prior_status if prior_status in ("AVOID", "GET READY") else "WAIT"
            plan["missing"].append("Waiting for a distinct setup after the last recorded candidate.")
        else:
            prior_rows = journal.records(symbol, "candidate", scope=scope, limit=1200, settled=True)
            matching = paper_records(prior_rows, before=now, hold=plan["hold"], setup=plan["setup"])
            validation = performance(matching)
            model = model_summary(read["candles"], plan, now)
            candidate = {**plan, "origin": now.isoformat()}
            plan = apply_model_gate(plan, model, validation)
            if model.get("p_tp1") is not None:
                candidate_id = event_id("candidate", scope, symbol, candidate["entry_bar"])
                alert_id = event_id("entry", scope, symbol, plan["bar"], plan["setup"])
                is_entry = plan["status"] in ENTRY_STATES
                payload = {"plan": candidate, "model": model, "decision_status": plan["status"]}
                inserted, reserved = journal.record_candidate(candidate_id, symbol, scope, now, payload,
                                                               alert_id=alert_id if is_entry else None,
                                                               cooldown=ENTRY_COOLDOWN)
                if reserved:
                    entry_alert = alert_id
                elif inserted and is_entry:
                    plan["discord_suppression"] = "10-minute ticker/setup cooldown."

    if plan["price_context_ready"]:
        prior_hedge = journal.records(symbol, "hedge", scope=scope, limit=4)
        preceding = next((row for row in prior_hedge if utc(row["origin"]) <= utc(plan["bar"]) - FIVE_MINUTES
                          and utc(row["origin"]).astimezone(ET).date() == ctx.session
                          and utc(plan["bar"]) - utc(row["origin"]) <= 3 * FIVE_MINUTES), None)
        current_bars = candles_on(read["buckets"], ctx.session)
        if preceding and len(current_bars) >= 2:
            old_levels = preceding["payload"]["snapshot"]["levels"]
            crosses = level_crossings(current_bars[-2]["close"], current_bars[-1]["close"], old_levels)
            direction = plan["direction"]
            if direction and plan["flow_confirmation"]["confirmed"]:
                plan["aligned_crossings"] = [cross for cross in crosses
                                             if cross["direction"] == ("above" if direction > 0 else "below")]

        current = next((row for row in prior_hedge if row["payload"].get("bar") == plan["bar"]), None)
        if not current:
            snapshot, _ = get_hedge(ctx, read["gamma_feed"])
            if snapshot:
                move = positive(read["regime"].get("atr"))
                left = session_state(now)["minutes_left"] - 1
                scenarios = hedge_scenarios(snapshot, plan["price"], move, left)
                if scenarios and (snapshot["complete"] or now - utc(plan["bar"]) >= 2 * MINUTE):
                    journal.insert(event_id("hedge", scope, symbol, plan["bar"]), symbol, "hedge", scope, now,
                                   {"bar": plan["bar"], "spot": plan["price"], "snapshot": snapshot, "scenarios": scenarios})
        else:
            snapshot = current["payload"]["snapshot"]
        if snapshot:
            neutral = hedge_change(snapshot, 0, 0, 5, base_price=plan["price"])["total"]
            plan["dealer_pressure"] = neutral
            if neutral is not None:
                plan["dealer_note"] += f". Flat spot/IV scenario {neutral:+,.0f} hedge-equivalent units in 5m"

    semantic = semantic_key(plan)
    reason_key = ["data", bool(plan["data_reasons"])]
    if plan.get("active_paper"):
        semantic = before_key or semantic
    if before_key != semantic or prior.get("quality") != reason_key:
        fingerprint = hashlib.sha256(repr((semantic, reason_key)).encode()).hexdigest()[:10]
        decision_id = event_id("decision", scope, symbol, now.isoformat(), fingerprint)
        payload = {**notification(plan), "semantic": semantic, "quality": reason_key}
        journal.insert(decision_id, symbol, "decision", scope, now, payload)
    if entry_alert:
        alerts.append((entry_alert, notification(plan)))
    return alerts, plan
