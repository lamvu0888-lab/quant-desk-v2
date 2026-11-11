"""V2.5 decision, volatility, dealer and probability views."""
from __future__ import annotations

import json
import hashlib
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_utils import DataError, utc
from dealer_hedging import HORIZONS, hedge_change, hedge_scenarios
from decision_engine import ENTRY_STATES, paper_outcome
from decision_flow import apply_model_gate, build_decision, get_hedge, model_summary, surface_is_usable
from desk_analytics import MINUTE
from iv_decision import iv_read
from market_session import calendar, session_state
from option_payoff import create_position, position_risk, quoted_legs
from research_snapshot import shared_snapshot
from terminal_charts import series_figure
from terminal_data import et_time, feed_has_delay, listed_dtes, positive, time_or_none
from terminal_style import AMBER, BLUE, RED, TEAL, amount, compact, esc, feed_stamp, kpis, plot, ratio_pct, style_chart
from trade_probability import evaluate_plan
from trade_validation import paper_records, performance, pressure_review, replay_variants, subgroup_report, walk_forward_replay

TINT = {"ENTER LONG": "long", "ENTER SHORT": "short", "GET READY": "ready", "AVOID": "avoid", "EXIT": "exit"}
DIRECTION = {1: "Rising", 0: "Neutral", -1: "Falling"}


def metric(label, value, note="", direction=None):
    return label, value, note, "" if direction is None or direction == 0 else "up" if direction > 0 else "down"


def show_table(rows, height=None):
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=height or "auto")


def download_json(label, data, key, filename):
    st.download_button(label, json.dumps(data, indent=2, allow_nan=False), file_name=filename,
                       mime="application/json", key=key)


def current_scope(ctx):
    return f"{ctx.price_provider}:{ctx.dte if ctx.dte is not None else 'all'}"


@st.cache_data(ttl=30, max_entries=30, show_spinner=False)
def journal_rows(symbol, kind, scope, limit, journal_key, _journal):
    return _journal.records(symbol, kind, scope=scope, limit=limit)


@st.cache_data(ttl=90, max_entries=8, show_spinner=False)
def remote_rows(repo, branch, credential, _shared):
    return shared_snapshot(_shared)


def records(ctx, kind, limit=200):
    if ctx.preview:
        return []
    local = []
    if ctx.journal is not None:
        try:
            identity = hashlib.sha256(f"{ctx.journal.path}\0{ctx.journal.dsn}".encode()).hexdigest()
            local = journal_rows(ctx.ticker, kind, current_scope(ctx), limit, identity, ctx.journal)
        except Exception:
            st.caption("The local research journal is temporarily unavailable.")
    if current_scope(ctx) == "Tradytics:0" and ctx.shared and ctx.shared.configured and not (ctx.journal and ctx.journal.dsn):
        try:
            cred = hashlib.sha256(ctx.shared.token.encode()).hexdigest()
            remote, _ = remote_rows(ctx.shared.repo, ctx.shared.branch, cred, ctx.shared)
            combined = {row["id"]: row for row in remote.get(ctx.ticker, {}).get(kind, [])}
            combined.update({row["id"]: row for row in local})
            return sorted(combined.values(), key=lambda row: row["origin"], reverse=True)[:limit]
        except Exception:
            st.caption("The cloud research snapshot is temporarily unavailable.")
    return local


def prospective_report(ctx, plan=None):
    rows = paper_records(records(ctx, "candidate", 1200), before=ctx.now,
                         hold=plan["hold"] if plan else None, setup=plan.get("setup") if plan else None)
    return rows, performance(rows)


@st.cache_data(ttl=120, max_entries=36, show_spinner=False)
def current_model(candles, geometry, start):
    return model_summary(candles, geometry, utc(start))


def plan_signature(plan):
    return tuple(plan.get(key) for key in ("symbol", "dte", "source", "bar", "entry_bar", "trigger", "stop", "tp1", "tp2", "demo"))


def dashboard_decision(ctx, read):
    plan, iv = build_decision(ctx, read)
    if plan["status"] in ENTRY_STATES:
        fields = ("price", "entry", "stop", "invalidation", "tp1", "tp2", "hold", "direction", "flow",
                  "gex", "regime", "underlying_cost_bps")
        origin = ctx.now.replace(second=0, microsecond=0)
        if origin < ctx.now:
            origin += MINUTE
        model = current_model(read["candles"][-2400:], {field: plan[field] for field in fields}, origin.isoformat())
        _, evaluation = prospective_report(ctx, plan)
        plan = apply_model_gate(plan, model, evaluation)
    review = st.session_state.get("qd25_execution_review")
    if review and review["signature"] == plan_signature(plan) and time_or_none(review["expires"]) > ctx.now:
        plan["option_review"] = review["message"]
        if review["blocked"] and plan["status"] in ENTRY_STATES:
            plan["status"] = "AVOID"
            plan["blocks"] = plan["blocks"] + [review["message"]]
    return plan, iv


def observe_panel(ctx, plan, candles, *, key, bar_ok):
    """Track one explicit paper reference in this browser session."""
    signature = (ctx.ticker, current_scope(ctx), ctx.preview)
    tracked = st.session_state.get("qd25_paper_reference")
    if tracked and tracked["scope"] == signature:
        original = tracked["plan"]
        outcome = tracked.get("outcome")
        if not outcome and bar_ok:
            outcome = paper_outcome(original, candles, ctx.now)
            if outcome:
                tracked["outcome"] = outcome
        if outcome:
            if outcome.get("r") is None:
                st.warning("Paper outcome unavailable because completed candles are missing.")
                shown = plan
            else:
                shown = {**original, "status": "EXIT", "outcome": outcome,
                         "blocks": [f"Tracked paper setup closed by {outcome['state'].lower()} at {amount(outcome['price'])}."],
                         "missing": [], "conflicts": []}
            if st.button("Clear paper reference", key=f"{key}_clear_paper"):
                st.session_state.pop("qd25_paper_reference", None)
                st.rerun()
            return shown
        st.caption(f"Tracking a paper entry at {amount(original['entry'])}, captured {et_time(original['origin'])}.")
        if st.button("Clear paper reference", key=f"{key}_clear_paper"):
            st.session_state.pop("qd25_paper_reference", None)
            st.rerun()
        return {**original, "status": "WAIT", "missing": ["A tracked paper setup is open."],
                "blocks": [], "conflicts": plan["conflicts"]}
    if plan["status"] in ENTRY_STATES and st.button("Track this paper setup", key=f"{key}_paper"):
        st.session_state["qd25_paper_reference"] = {"scope": signature, "plan": {**plan, "origin": ctx.now.isoformat()}}
        st.rerun()
    return plan


def command_panel(ctx, plan, iv, candles, *, key, track=False, bar_ok=True):
    shown = observe_panel(ctx, plan, candles, key=key, bar_ok=bar_ok) if track else plan
    reasons = shown["blocks"] or shown["missing"] or shown["evidence"]
    headline = shown.get("setup") or ("Price and flow disagree" if shown["bias"] == "Mixed" else "Waiting for a directional setup")
    if shown["status"] == "EXIT":
        headline = f"Paper exit · {shown['outcome']['state']}"
    steps = "".join(f"<span><b>{period}</b> {esc(DIRECTION[reading['direction']])}</span>" for period, reading in plan["ema"].items())
    demo = " · ILLUSTRATIVE PREVIEW" if ctx.preview else ""
    st.markdown(
        '<section class="qd-command"><div class="qd-command-head"><div>'
        f'<div class="qd-eyebrow">{esc("ENTRY COMMAND CENTER" + demo)}</div><h2>{esc(headline)}</h2>'
        f'<p>{esc(reasons[0] if reasons else "Check the trigger, stop and targets.")}</p></div>'
        f'<span class="qd-status {TINT.get(shown["status"], "")}">{esc(shown["status"])}</span></div>'
        f'<div class="qd-timeframes">{steps}</div></section>',
        unsafe_allow_html=True,
    )
    flow = plan["flow"].get("recent_net")
    kpis([metric("Market bias", plan["bias"], plan["regime"] or "Regime pending"),
          metric("Entry quality", f"{plan['score']}/100", "Evidence score", plan["direction"]),
          metric("15m options flow", compact(flow, currency=True, signed=True),
                 "Confirms trend" if plan["direction"] and plan["flow"].get("direction") == plan["direction"] else
                 "Opposes trend" if plan["bias"] == "Mixed" else "Awaiting directional agreement", flow),
          metric("Data confidence", plan["confidence"], f"5m {et_time(plan['bar'], short=True)} · {plan['source']}")], small=True)
    st.caption(f"{plan['dealer_note']} · Option premium {iv['valuation'].lower()}.")
    if shown.get("trigger") is not None:
        kpis([metric("Trigger / entry", f"{amount(shown['trigger'])} / {amount(shown['entry'])}", f"{shown['hold']}m plan · {shown['scope']}"),
              metric("Structural invalidation", amount(shown["invalidation"]), "Completed close through the boundary"),
              metric("Protective stop", amount(shown["stop"]), "Intrabar price limit"),
              metric("TP1 / TP2", f"{amount(shown['tp1'])} / {amount(shown['tp2'])}",
                     f"{amount(shown['rr'])}R / {amount(shown['rr2'])}R")], small=True)
        if shown["trigger_text"]:
            st.caption(shown["trigger_text"])
        if shown["target_note"]:
            st.caption(shown["target_note"])
    if shown.get("outcome") and shown["outcome"].get("r") is not None:
        st.caption(f"Paper result {shown['outcome']['r']:+.2f}R after modeled costs.")
    with st.expander("Evidence and entry conditions", expanded=key == "trade"):
        left, right = st.columns(2)
        with left:
            st.markdown("**What agrees**")
            for note in shown["evidence"]:
                st.write(note)
            if not shown["evidence"]:
                st.caption("No confirming evidence yet.")
        with right:
            st.markdown("**Missing or conflicting evidence**")
            for note in dict.fromkeys(shown["blocks"] + shown["missing"] + shown["conflicts"]):
                st.write(note)
            if not (shown["blocks"] or shown["missing"] or shown["conflicts"]):
                st.caption("The current rules have no outstanding price or flow gate.")
        st.caption(iv["guidance"])
        if plan.get("nearest_wall"):
            wall = plan["nearest_wall"]
            st.caption(f"Nearest gamma wall is {wall['label']} at {amount(wall['price'])}, {wall['distance']:+,.2f} points from spot.")
        if plan.get("delay_status") and not ctx.preview:
            st.caption(plan["delay_status"] + ".")
        if plan.get("volume_note"):
            st.caption(plan["volume_note"])
        if plan.get("expected_remaining") is not None:
            st.caption(f"Room to the matching expected-move boundary {amount(plan['expected_remaining'])} price points.")
        model = plan.get("model") or {}
        if model.get("p_tp1") is not None:
            probability = "prospective comparison passed" if plan["validation"].startswith("Prospectively") else "exploratory"
            expectancy = "prospective comparison passed" if plan.get("expectancy_validation") else "exploratory"
            st.caption(f"Immediate price-path scenario · TP1 first {ratio_pct(model['p_tp1'])} ({probability}) · "
                       f"EV {model['ev_r']:+.2f}R ({expectancy}).")
        if plan.get("option_review"):
            st.caption(plan["option_review"])
    return shown


def journal_panel(ctx, plan):
    scope_key = (ctx.ticker, current_scope(ctx), ctx.preview)
    history = st.session_state.setdefault("qd25_dashboard_changes", {})
    events = history.setdefault(scope_key, [])
    current = (plan["status"], plan.get("direction"), plan.get("setup"), bool(plan["data_reasons"]))
    if not events or events[-1]["fingerprint"] != current:
        events.append({"fingerprint": current, "Time ET": et_time(plan["detected"]), "Status": plan["status"],
                       "Setup": plan["setup"], "Reason": (plan["blocks"] or plan["missing"] or plan["evidence"] or ["—"])[0]})
        del events[:-60]
    with st.expander("Setup transitions and paper outcomes", expanded=True, on_change="rerun", key="qd25_history_exp") as detail:
        if not detail.open:
            return
        observed = records(ctx, "decision", 100)
        candidates = records(ctx, "candidate", 100)
        if observed:
            st.markdown("**Worker transitions**")
            show_table([{"Time ET": et_time(e["origin"]), "Status": e["payload"]["status"], "Setup": e["payload"].get("setup"),
                         "Reason": e["payload"].get("explanation")} for e in observed], 255)
        if candidates:
            st.markdown("**Paper outcomes**")
            show_table([{"Time ET": et_time(e["origin"]), "Decision": e["payload"]["decision_status"],
                         "Setup": e["payload"]["plan"]["setup"], "Entry": e["payload"]["plan"]["entry"],
                         "Outcome": (e["outcome"] or {}).get("state", "Pending"),
                         "Paper R": (e["outcome"] or {}).get("r")} for e in candidates], 230)
            download_json("Download worker journal", {"decisions": observed, "candidates": candidates},
                          "qd25_journal_export", ctx.title("research_journal.json"))
        if not observed:
            st.caption("No worker transitions for this ticker, price source and DTE. The worker defaults to Tradytics 0DTE.")
        st.markdown("**This browser session**")
        show_table([{k: v for k, v in row.items() if k != "fingerprint"} for row in reversed(events)], 205)


def iv_center(ctx, vol, regime, spot):
    st.subheader("IV Decision Center")
    options = ["Dashboard scope", "0 DTE", "1 DTE", "7 DTE", "30 DTE", "Listed expiration"]
    mode = st.segmented_control("Volatility expiration", options, default=options[0], key="qd25_iv_expiry") or options[0]
    expiry = ctx.expiry
    if mode == "Listed expiration":
        listed = ctx.get("expirations", symbol=ctx.ticker)
        dtes = listed_dtes(listed, ctx.session) if not listed.error else []
        term = vol.obj.get("term") if isinstance(vol.obj.get("term"), list) else []
        listed_dates = {(ctx.session + timedelta(days=d)).isoformat() for d in dtes}
        listed_dates.update(row["expiry"] for row in term if isinstance(row, dict)
                            and isinstance(row.get("expiry"), str) and row["expiry"] >= str(ctx.session))
        expiry = st.selectbox("Available expiration", sorted(listed_dates), key="qd25_iv_listed") if listed_dates else ""
        if not expiry:
            st.caption("No expiration list is available.")
    elif mode != options[0]:
        expiry = (ctx.session + timedelta(days=int(mode.split()[0]))).isoformat()
    report = iv_read(vol.obj, expiry, ctx.now, spot, regime, usable=surface_is_usable(ctx, vol))
    use_expiry = mode != "Dashboard scope" or expiry is not None
    kpis([metric("Option premium", report["valuation"], "Selected ATM IV ÷ trailing 20-session RV"),
          metric("Price volatility", report["regime"], "Completed 5-minute bars"),
          metric("ATM IV", ratio_pct(report["atm"] if expiry != "" else None),
                 f"Exact expiry {expiry or '—'}" if use_expiry else "30-day ATM benchmark"),
          metric("IV / RV", f"{amount(report['ratio'])}×" if report["ratio"] is not None and expiry != "" else "—",
                 f"Trailing RV {ratio_pct(report['rv'])}")], small=True)
    kpis([metric("30-day IV rank", ratio_pct(report["rank30"]), "Position in the provider's 30-day IV range"),
          metric("30-day IV percentile", ratio_pct(report["percentile30"]), "Share of provider history below the reading"),
          metric("Expected move", f"±{amount(report['reported_move'] or report['reference_move'])}" if expiry != "" and
                 (report["reported_move"] or report["reference_move"]) is not None else "—",
                 f"Provider expiry {report['reported_expiry']}" if report["reported_move"] else "IV × square-root time estimate"),
          metric("Remaining band", f"↑ {amount(report['remaining_up'])} / ↓ {amount(report['remaining_down'])}"
                 if report["band"] else "—", "Distance to a matching, anchored provider band")], small=True)
    st.write(report["guidance"] if expiry != "" else "Choose a listed expiration to compare premium and movement.")
    st.caption(f"30-day ATM benchmark {ratio_pct(report['atm30'])} · Term structure {report['term_shape'] or 'unavailable'} · 25-delta put minus call skew "
               f"{amount(report['skew_points'])} IV points for the selected expiry.")
    if expiry and report["atm"] is None:
        st.info("The provider has no ATM IV for this exact date. Choose a listed expiration to review its data.")
    feed_stamp("volatility", vol, ctx)
    with st.expander("How to read IV"):
        st.write("Short-dated ATM IV prices the selected expiry. The 30-day IV rank and percentile describe the longer-dated benchmark.")
        st.write("An IV/RV ratio compares forward-priced volatility with past realized movement. This interface calls ratios at or below 0.85× low and ratios at or above 1.20× rich.")
        st.write("The estimated move uses current spot, annualized IV, and calendar time to the reference session close. Remaining band is shown only for the matching provider expiry and anchor.")
        st.write("Check trade direction in Trade Plan. IV helps compare what the option structure costs.")


def dealer_center(ctx):
    candles, bar_feed = ctx.history()
    from desk_analytics import realized_regime
    from terminal_data import chart_buckets

    regime = realized_regime(chart_buckets(candles, 5))
    snapshot, feeds = get_hedge(ctx)
    spot = snapshot["spot"] if snapshot else None
    st.subheader("Dealer Hedging Intelligence")
    st.caption(f"{ctx.ticker} · {ctx.scope} · Signed Tradytics exposure model · positive hedge change denotes modeled buying")
    if not snapshot or not spot:
        st.info("Waiting for a recent gamma snapshot with known units and an underlying price.")
        for name, feed in feeds.items():
            st.caption(name.title())
            feed_stamp("exposure", feed, ctx)
        return
    gex = snapshot["gex"]
    labels = snapshot["levels"]
    kpis([metric("Gamma regime", "Positive" if gex > 0 else "Negative" if gex < 0 else "Flat",
                 "May dampen moves" if gex > 0 else "May amplify moves" if gex < 0 else "Limited modeled sensitivity"),
          metric("Gamma flip", amount(labels["flip"]), "Provider model level"),
          metric("Call wall / put wall", f"{amount(labels['call_wall'])} / {amount(labels['put_wall'])}", "Provider model levels"),
          metric("Hedge inputs", "Combined" if snapshot["complete"] else "Partial",
                 f"Exposure {et_time(snapshot['stamp'], short=True)} ET")], small=True)
    if not snapshot["complete"]:
        st.info("Vanna and/or Charm could not be aligned with Gamma. The corresponding component and combined total are unavailable.")
    left, right = st.columns(2)
    iv_shock = left.slider("IV shock size (vol points)", 0., 4., 1., .25, key="qd25_dealer_shock")
    horizon = right.selectbox("Pressure map horizon", HORIZONS, index=2, format_func=lambda x: f"{x} minutes", key="qd25_dealer_horizon")
    left_minutes = max(0, session_state(ctx.now)["minutes_left"] - 1)
    atr = positive(regime.get("atr"))
    scenarios = hedge_scenarios(snapshot, spot, atr, left_minutes, iv_shock=iv_shock)
    if scenarios and horizon <= left_minutes:
        positions = [spot + atr * step / 2 for step in range(-6, 7) if spot + atr * step / 2 > 0]
        iv_axis = [-iv_shock, -iv_shock/2, 0, iv_shock/2, iv_shock] if iv_shock else [0]
        field = "total" if snapshot["complete"] else "gamma"
        values = [[hedge_change(snapshot, price - spot, pp, horizon, base_price=spot)[field] for price in positions] for pp in iv_axis]
        fig = go.Figure(go.Heatmap(x=positions, y=iv_axis, z=values, zmid=0,
                                   colorscale=[[0, "#c0475d"], [.5, "#f3f5f8"], [1, "#087c68"]],
                                   colorbar={"title": {"text": "Hedge units"}, "tickformat": "~s"},
                                   hovertemplate="Spot %{x:,.2f}<br>IV shock %{y:+.2f} pp<br>Modeled hedge %{z:+,.0f}<extra></extra>"))
        fig.add_vline(x=spot, line_color=BLUE, line_dash="dot")
        fig.update_xaxes(title="Scenario spot", tickformat=",.2f", nticks=7)
        fig.update_yaxes(title="IV change (pp)")
        st.markdown(f"**{horizon}m hedge-pressure map · {'Gamma + Vanna + Charm' if snapshot['complete'] else 'Gamma component'}**")
        plot(style_chart(fig, 325, hover="closest", legend=False), "qd25_dealer_map")
    elif not scenarios:
        st.caption("Stress scenarios need completed 5m price ranges and remaining regular-session minutes.")
    else:
        st.caption("Choose a shorter map horizon within the remaining regular session.")
    if scenarios:
        st.markdown("**Scenario hedge demand**")
        show_table([{"Horizon": f"{r['horizon']}m", "Stress case": r["scenario"], "Scenario spot": round(r["price"], 2),
                     "IV change (pp)": r["iv_points"], "Gamma": round(r["gamma"]), "Vanna": round(r["vanna"]) if r["vanna"] is not None else None,
                     "Charm": round(r["charm"]) if r["charm"] is not None else None,
                     "Net hedge units": round(r["total"]) if r["total"] is not None else None} for r in scenarios], 300)
    with st.expander("Exposure inputs and sensitive strikes", on_change="rerun", key="qd25_dealer_inputs") as inputs:
        if inputs.open:
            show_table([{"Input": "Gamma", "Sensitivity": snapshot["gamma_shares_per_point"], "Unit": "Delta units per price point"},
                        {"Input": "Vanna", "Sensitivity": snapshot["vanna_shares_per_iv_point"], "Unit": "Delta units per IV point"},
                        {"Input": "Charm", "Sensitivity": snapshot["charm_shares_per_day"], "Unit": "Delta units per elapsed calendar day"},
                        {"Input": "Delta", "Sensitivity": snapshot["delta_shares"], "Unit": "Provider modeled delta units"}])
            show_table([{"Strike": r["strike"], "Net GEX": r["gex"],
                         "Delta change per point": r["delta_change_per_point"]} for r in snapshot["strikes"]], 220)
            st.caption("The largest nearby absolute strike sensitivities mark where the provider model is concentrated. A strike is neither a fill nor a guarantee of acceleration.")
            for feed in feeds.values():
                feed_stamp("exposure", feed, ctx)
    with st.expander("Price, volume and flow confirmation"):
        from desk_analytics import flow_read
        from terminal_data import chart_buckets

        reading = flow_read(ctx.timeline().rows)
        bars = chart_buckets(candles, 5)
        displacement = bars[-1]["close"] - bars[-4]["close"] if len(bars) >= 4 and not bar_feed.error else None
        kpis([metric("Price change", amount(displacement), "Last three 5m intervals"),
              metric("Relative volume", f"{amount(regime.get('volume_ratio'))}×" if regime.get("volume_ratio") is not None else "—",
                     "Native volume when provided"),
              metric("15m signed flow", compact(reading.get("recent_net"), currency=True, signed=True), "Price confirmation"),
              metric("Flow acceleration", compact(reading.get("momentum"), currency=True, signed=True), "5m average change")], small=True)
    with st.expander("Pressure timeline and scenario validation", on_change="rerun", key="qd25_dealer_history") as history:
        if history.open:
            events = records(ctx, "hedge", 160)
            same_session = [row for row in reversed(events) if row["origin"][:10] == ctx.now.date().isoformat()]
            points = []
            for row in same_session:
                projected = next((x for x in row["payload"]["scenarios"] if x["scenario"] == "Flat / IV flat" and x["horizon"] == 5), {})
                actual = next((x for x in (row["outcome"] or {}).get("outcomes", [])
                               if x["scenario"] == "Flat / IV flat" and x["horizon"] == 5), {})
                points.append({"time": et_time(row["origin"], short=True), "projected": projected.get("total"),
                               "conditional": actual.get("price_and_time_hedge_proxy")})
            fig = series_figure(points, "time", [("Flat spot/IV scenario", "projected", BLUE, 1),
                                                ("Observed spot, IV held flat", "conditional", TEAL, 1)], height=270, y_format="~s")
            if fig:
                plot(fig, "qd25_dealer_timeline")
            outcomes = [{"Origin ET": et_time(row["origin"]), "Horizon": f"{entry['horizon']}m", "Stress case": entry["scenario"],
                         "Observed spot": entry.get("observed_price"), "Actual minus scenario": entry.get("scenario_error"),
                         "Spot change (bps)": entry.get("return_bps")}
                        for row in events[:15] for entry in (row["outcome"] or {}).get("outcomes", [])]
            if outcomes:
                show_table(outcomes, 280)
                st.markdown("**Ex-ante pressure and subsequent price response**")
                summary = pressure_review(events)
                show_table([{**row, "Pressure / return agreement": ratio_pct(row["Pressure / return agreement"])}
                            for row in summary], 158)
                st.caption("Uses the flat-spot, flat-IV charm scenario for direction comparisons and disjoint outcome windows. Review sample counts for each horizon.")
                download_json("Download dealer scenario journal", events, "qd25_hedge_export", ctx.title("hedge_scenarios.json"))
            else:
                st.caption("The worker captures scenarios once per completed 5m bar. Pending horizons appear after their outcome candles arrive.")
    st.caption("Hedge units are share-equivalents. SPX uses index-equivalent units because the index itself has no share tape. The model reverses the change in signed delta and assumes fixed local sensitivities. Provider exposure already incorporates contract sizing. Recorded outcomes compare underlying prices with scenario paths. Dealer executions and inventory are unobserved.")


@st.cache_data(ttl=300, max_entries=16, show_spinner=False)
def cached_replay(candles, flows, day, symbol, dte):
    return walk_forward_replay(candles, flows, day, symbol=symbol, dte=dte)


def render_performance(report, rows):
    if not report["n"]:
        st.info("No mature, comparable outcomes are available yet.")
        return
    kpis([metric("Completed samples", str(report["n"]), f"{report['sessions']} sessions"),
          metric("TP1 Brier score", amount(report["brier"], 3), f"Gaussian {amount(report['baseline_brier'], 3)} · 50/50 {amount(report['even_brier'], 3)}"),
          metric("Realized expectancy", f"{report['realized_ev']:+.2f}R", "Paper underlying, after modeled cost"),
          metric("Max drawdown", f"{report['drawdown']:.2f}R", "Chronological cumulative paper R")], small=True)
    st.caption(f"TP1 observed {ratio_pct(report['tp1_frequency'])} · average predicted {ratio_pct(report['probability_mean'])} · "
               f"false-positive rate at 50% {ratio_pct(report['false_positive_rate'])} · profitable paper outcomes {ratio_pct(report['win_rate'])}.")
    show_table([{"Probability band": item["band"], "N": item["n"], "Mean estimated": ratio_pct(item["predicted"]),
                 "Observed": ratio_pct(item["observed"])} for item in report["calibration"]], 190)
    with st.expander("Performance by ticker, time, setup and regime"):
        show_table([{"Group": item["group"], "Value": item["value"], "N": item["n"],
                     "Brier": round(item["brier"], 3), "Baseline": round(item["baseline_brier"], 3),
                     "False positives": ratio_pct(item["false_positive_rate"]),
                     "Win rate": ratio_pct(item["win_rate"]), "Mean R": round(item["realized_ev"], 3),
                     "Drawdown R": round(item["drawdown"], 2)} for item in subgroup_report(rows)], 310)


def validation_view(ctx, plan):
    st.subheader("Walk-forward and prospective validation")
    mode = st.segmented_control("Evaluation source", ["Worker paper outcomes", "Historical replay"],
                                default="Worker paper outcomes", key="qd25_validation_mode") or "Worker paper outcomes"
    if mode == "Worker paper outcomes":
        rows, report = prospective_report(ctx)
        _, matching = prospective_report(ctx, plan)
        st.caption("Only predictions recorded before their outcomes are included. Overlapping windows and incomplete outcomes are excluded.")
        if matching["qualified"]:
            st.success(f"The current setup and horizon have {matching['n']} matched observations. TP1 calibration passed the comparison rules.")
        else:
            st.info(f"Current setup and horizon have {matching['n']} matched observations. TP1 calibration is pending.")
        if matching.get("expectancy_qualified"):
            st.caption("Underlying paper expectancy also passed the error check for this cohort.")
        st.caption("A matched cohort needs at least 60 outcomes across eight sessions. TP1 requires a Brier improvement over the Gaussian baseline, calibration checks and sufficient hits and misses.")
        render_performance(report, rows)
        if rows:
            download_json("Download prospective outcomes", rows, "qd25_validation_export", ctx.title("prospective_outcomes.json"))
        return
    latest = calendar().previous_session(ctx.session.isoformat()).date()
    day = st.date_input("Completed regular session", latest, max_value=latest, key="qd25_replay_day")
    if st.button("Run walk-forward replay", key="qd25_walk_forward"):
        st.session_state["qd25_replay_request"] = (ctx.ticker, ctx.dte, day, ctx.preview)
    if st.session_state.get("qd25_replay_request") != (ctx.ticker, ctx.dte, day, ctx.preview):
        return
    candles, bar_feed = ctx.history(session=day)
    flow = ctx.timeline(session=day)
    if bar_feed.error or flow.error:
        st.info("The selected session's bars or options flow are unavailable.")
        return
    if not candles or not flow.rows:
        st.info("Waiting for completed price bars and flow for the selected session.")
        return
    with st.spinner("Replaying as-of price and flow…"):
        rows = cached_replay(candles, flow.rows, day, ctx.ticker, ctx.dte)
    st.caption("Replay fits from prior completed bars at each origin. It uses finalized vendor flow and excludes historical GEX and IV. Results measure paper underlying outcomes.")
    if ctx.preview:
        st.caption("Preview replay uses illustrative data.")
    render_performance(performance(rows), rows)
    variants = replay_variants(rows)
    if variants:
        st.markdown("**Entry timing on the subsequent replay paths**")
        show_table([{**row, "Mean estimated fill": ratio_pct(row["Mean estimated fill"]),
                     "Replay fill rate": ratio_pct(row["Replay fill rate"])} for row in variants], 220)
    if rows:
        download_json("Download replay observations", rows, "qd25_replay_export", ctx.title("walk_forward_replay.json"))


def option_controls(ctx, plan):
    structure = st.selectbox("Paper structure", ["Long option", "Debit vertical", "Credit vertical"], key="qd25_option_structure")
    if not plan["expiry"]:
        st.info("Choose an exact expiration in the sidebar to evaluate executable option quotes.")
        return None, None
    spot, asof = plan["price"], ctx.now
    feed = ctx.get("chain", symbol=ctx.ticker, limit=250, expiry=plan["expiry"],
                   min_strike=round(spot * .96, 2), max_strike=round(spot * 1.04, 2))
    if (not feed.snapshot or feed.error or feed_has_delay(feed) or ctx.paused or
            feed.snapshot.meta.get("symbol") not in (None, ctx.ticker)):
        st.info("Waiting for the selected expiration's current option chain.")
        return None, None
    legs = quoted_legs(feed.rows, ctx.ticker, plan["expiry"], asof)
    direction = plan["direction"]
    right = ("call" if direction > 0 else "put") if structure != "Credit vertical" else ("put" if direction > 0 else "call")
    choices = sorted((leg for leg in legs if leg.right == right), key=lambda leg: abs(leg.strike - spot))
    if not choices:
        st.info("No matching standard contract has a fresh two-sided quote, IV and a usable settlement time.")
        return None, None
    by_id = {leg.contract: leg for leg in choices}

    def describe(contract):
        leg = by_id[contract]
        return f"{leg.strike:g} {leg.right} · {leg.bid:g} bid / {leg.ask:g} ask · {leg.iv:.1%} IV"

    a, b = st.columns(2)
    selected = a.selectbox("Buy leg", list(by_id), format_func=describe, key="qd25_long_option")
    long = by_id[selected]
    short = None
    if structure != "Long option":
        strike_dir = (1 if right == "call" else -1) * (1 if structure == "Debit vertical" else -1)
        shorts = [leg.contract for leg in sorted(choices, key=lambda leg: abs(leg.strike - long.strike))
                  if leg.expiry_at == long.expiry_at and (leg.strike - long.strike) * strike_dir > 0]
        if not shorts:
            st.info("No quoted second leg matches the structure. Try a different buy strike.")
            return None, None
        short = by_id[b.selectbox("Sell leg", shorts, format_func=describe, key="qd25_short_option")]
    with st.expander("Execution and IV assumptions", expanded=False):
        x, y, z = st.columns(3)
        fee = x.number_input("Fee per leg and side ($)", 0., 10., .65, .05, key="qd25_fee")
        slip = y.number_input("Extra slippage per leg (option points)", 0., 3., .02, .01, key="qd25_slip")
        budget = z.number_input("Max loss budget ($, 0 skips)", 0., 100000., 0., 25., key="qd25_budget")
        x, y, z = st.columns(3)
        rate = x.number_input("Annual rate assumption (%)", 0., 25., 4., .25, key="qd25_rate") / 100
        dividend = y.number_input("Annual dividend assumption (%)", 0., 25., 0., .25, key="qd25_dividend") / 100
        shock = z.number_input("Additional IV shock (points)", -10., 10., 0., .5, key="qd25_iv_shock")
    try:
        position = create_position(structure, direction, long, short, asof, plan["hold"], fee=fee, slippage=slip, rate=rate,
                                   dividend=dividend, iv_shock_points=shock)
    except DataError as exc:
        st.warning(str(exc))
        return None, None
    risk = position_risk(position)
    kpis([metric("Quoted entry cash flow", f"USD {risk['entry_cash']:+,.2f}", "Includes bid/ask and extra slippage"),
          metric("Estimated expiration max loss", f"USD {risk['max_loss']:,.2f}", "One package · includes modeled round-trip costs"),
          metric("Round-trip fees", f"USD {risk['round_trip_fees']:.2f}", "Per selected package"),
          metric("Quote filters", "Two-sided", "Up to 120 seconds old · ≤35% relative width")], small=True)
    if budget and risk["max_loss"] > budget:
        st.warning("The quoted structure exceeds the entered loss budget.")
    feed_stamp("chain", feed, ctx)
    st.caption("Each leg is repriced from its quoted midpoint with changes in spot, IV and time. Modeled trades pay the appropriate bid or ask. Early exercise, assignment and actual fills are outside this model.")
    return position, budget


def probability_results(result):
    primary = result["models"]["Conditioned block"]
    available = [(strategy, row) for strategy, row in primary.items() if row.get("ev_r") is not None]
    if available:
        strategy, best = max(available, key=lambda item: item[1]["ev_r"])
        st.markdown(f"**Highest modeled EV per opportunity · {strategy} ({best['ev_r']:+.2f}R)**")
    st.caption(f"{result['training_minutes']:,} completed one-minute bars across {result['sessions']} sessions · "
               f"{result['horizon']}m horizon · estimates include possible missed fills.")
    st.caption("TP1 and TP2 rates are conditional on modeled fills. The strategy exits the full paper position at TP2, stop, invalidation or time limit. No-fill paths contribute zero to per-opportunity EV.")
    show_table([{"Entry method": name, "Fill": ratio_pct(row.get("p_fill")), "No fill": ratio_pct(row.get("p_no_fill")),
                 "TP1 first": ratio_pct(row.get("p_tp1")), "TP2 first": ratio_pct(row.get("p_tp2")),
                 "Stop before TP1": ratio_pct(row.get("p_stop_first")), "Stop before TP2": ratio_pct(row.get("p_stop_before_tp2")),
                 "Neither TP1 nor stop": ratio_pct(row.get("p_neither")), "Neither TP2 nor stop": ratio_pct(row.get("p_no_tp2_or_stop")),
                 "Invalidated": ratio_pct(row.get("p_invalidated"))} for name, row in primary.items()], 218)
    pnl_unit = "EV $ / package" if result["mode"] == "option" else "EV per share or index point"
    show_table([{"Entry method": name, "EV per opportunity (R)": amount(row.get("ev_r"), 3),
                 pnl_unit: amount(row.get("ev_pnl"), 3), "EV when filled (R)": amount(row.get("ev_r_filled"), 3),
                 "Avg hold when filled": amount(row.get("expected_minutes"), 1),
                 "Win rate when filled": ratio_pct(row.get("p_profit")), "Break-even win rate": ratio_pct(row.get("breakeven")),
                 "Avg net win / loss": amount(row.get("reward_risk")), "5th percentile (R)": amount(row.get("p05_r")),
                 "Worst 5% mean (R)": amount(row.get("expected_shortfall_r"))} for name, row in primary.items()], 218)
    with st.expander("Baseline, jump stress and simulation uncertainty"):
        def interval(value, percent=False):
            if not value:
                return "—"
            return " to ".join(ratio_pct(x) if percent else amount(x, 3) for x in value)

        show_table([{"Model": model, "Entry": name, "Fill": ratio_pct(row.get("p_fill")),
                     "TP1 95% sample interval": interval(row.get("tp1_interval"), True),
                     "TP2 95% sample interval": interval(row.get("tp2_interval"), True),
                     "EV (R)": amount(row.get("ev_r"), 3), "EV 95% sample interval": interval(row.get("ev_ci"))}
                    for model, methods in result["models"].items() for name, row in methods.items()], 350)
        st.caption("Intervals measure simulation sampling error under fixed assumptions. Compare baseline and jump stress for model sensitivity. Retest levels are held fixed at the plan snapshot.")
    if not available:
        st.info("No entry variant produced a viable modeled fill at the selected geometry and costs.")


def probability_center(ctx, read, plan):
    st.subheader("Trade Probability and Profitability")
    if ctx.preview:
        st.caption("Illustrative prices and quotes. Results demonstrate the workflow.")
    if plan["data_reasons"] or any(plan.get(key) is None for key in ("entry", "stop", "tp1", "tp2")):
        st.info("Waiting for Trade Plan to produce usable entry, stop and targets from synchronized price and flow.")
        if plan["data_reasons"]:
            st.caption(" ".join(plan["data_reasons"]))
    else:
        st.caption(f"Imported {plan['setup']} · {plan['bias']} · {plan['scope']} · {plan['hold']}m · {plan['status']}")
        mode = st.segmented_control("Value paths as", ["Underlying", "Quoted option structure"], default="Underlying",
                                    key="qd25_payoff_mode") or "Underlying"
        option, budget = option_controls(ctx, plan) if mode == "Quoted option structure" else (None, None)
        can_run = mode == "Underlying" or option is not None
        if st.button("Evaluate trade plan", type="primary", disabled=not can_run, key="qd25_run_plan_mc"):
            try:
                with st.spinner("Simulating entry, exits and costs…"):
                    result = evaluate_plan(read["candles"], plan, ctx.now, n=2000, option=option)
                st.session_state["qd25_result"] = (plan_signature(plan), mode, result)
                if option:
                    primary = result["models"]["Conditioned block"].get("Immediate") or {}
                    baseline = result["models"]["Gaussian baseline"].get("Immediate") or {}
                    blocked = bool(budget and position_risk(option)["max_loss"] > budget)
                    reason = "Quoted structure exceeds your entered maximum loss."
                    if not blocked and not primary.get("fills"):
                        blocked, reason = True, "The quoted structure has no viable immediate fill in this scenario."
                    elif not blocked and primary.get("ev_ci") and baseline.get("ev_ci") and max(primary["ev_ci"][1], baseline["ev_ci"][1]) < 0:
                        blocked, reason = True, "Both models estimate negative option expectancy after spread, fees and slippage."
                    if not blocked:
                        reason = "The selected option structure has a paper scenario. Prospective option calibration is pending."
                    st.session_state["qd25_execution_review"] = {"signature": plan_signature(plan), "blocked": blocked,
                                                                  "message": reason, "expires": (ctx.now + 2 * MINUTE).isoformat()}
                    if blocked:
                        st.warning(reason)
            except DataError as exc:
                st.info(str(exc))
        saved = st.session_state.get("qd25_result")
        if saved and saved[1] == mode and saved[0][:3] == plan_signature(plan)[:3]:
            if saved[0] != plan_signature(plan):
                st.caption("The plan changed since this run. Evaluate again to compare the current setup.")
            st.caption(f"Run captured {et_time(saved[2]['asof'])}.")
            probability_results(saved[2])
            download_json("Download probability report", saved[2], "qd25_probability_export", ctx.title("trade_probability.json"))
    st.caption("Price-path and option P&L estimates are exploratory. Review the Validation view for measured outcomes.")
