"""The ordered Movement Detector and the Strategy Lab review."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dealer_hedging import hedge_scenarios
from decision_flow import get_hedge
from market_session import session_state
from movement_charts import chart_for_stage, next_cue
from movement_detector import CHECKS, DIRECTION, MIN_ENTRY_SCORE, PLAYBOOKS, cvd_context, movement_status
from terminal_data import INDICES, et_time, feed_has_delay, positive, time_or_none
from terminal_style import AMBER, amount, compact, empty, esc, plot, ratio_pct, style_chart

AUDIT_PATH = Path(__file__).resolve().parent / "research" / "strategy_audit.json"
LAB_MATCHES = {
    "Trend continuation": ("TF02", "TF14", "TF17", "TF19"),
    "Breakout and retest": ("TF07", "TF09", "TF10", "TF18"),
    "Volatility expansion": ("TF10", "TF13", "TF16", "SA14"),
    "Range mean reversion": ("MR02", "MR08", "MR16", "MR20"),
}


def nav_button(label, page, key):
    if st.button(label, key=key, on_click=st.session_state.__setitem__, args=("desk_page", page)):
        st.rerun()


def stage(number, value, detail, ctx, read, plan, state, *, flow_interval="5m", flow_feed=None):
    st.markdown(
        f'<section class="qd-movement-stage"><div class="qd-stage-number">{number}</div>'
        f'<div class="qd-stage-body"><h3>{esc(CHECKS[number - 1])}</h3>'
        f'<p>{esc(detail)}</p></div><div class="qd-stage-value">{esc(value)}</div></section>',
        unsafe_allow_html=True,
    )
    chart = chart_for_stage(number, ctx, read, plan, flow_interval=flow_interval, flow_feed=flow_feed)
    if chart is None:
        empty(message="Chart waiting for the matching completed data or current exposure snapshot.")
    else:
        plot(chart, f"movement_stage_{number}_chart")
    st.markdown(f'<div class="qd-next-cue"><b>Look next</b> · {esc(next_cue(number, ctx, plan, state))}</div>',
                unsafe_allow_html=True)


def intraday_iv(ctx, read, iv, minutes):
    """Session-local observed surface snapshots; never invent intervening IV."""
    key = f"movement_iv_samples_{ctx.ticker}_{ctx.expiry or '30d'}_{ctx.session}"
    samples = st.session_state.setdefault(key, [])
    feed = read["vol"]
    observed = feed.snapshot.received_at if feed.snapshot else None
    if iv["atm"] is not None and observed and not feed.error and not feed_has_delay(feed):
        if observed.astimezone(ctx.now.tzinfo).date() == ctx.now.date() and not any(t == observed for t, _ in samples):
            samples.append((observed, iv["atm"]))
    samples[:] = sorted(samples, key=lambda item: item[0])[-480:]
    current = samples[-1] if samples else None
    target = current[0] - timedelta(minutes=minutes) if current else None
    previous = min((sample for sample in samples if sample[0] <= target),
                   key=lambda sample: target - sample[0], default=None) if target else None
    # A sparse provider update cannot establish a short-window comparison.
    if previous and target - previous[0] > timedelta(seconds=75):
        previous = None
    delta = (current[1] - previous[1]) * 100 if previous else None
    fig = None
    if samples:
        fig = go.Figure(go.Scatter(x=[t for t, _ in samples], y=[100*v for _, v in samples],
                                   mode="lines+markers", line=dict(color=AMBER, width=2), name="Observed ATM IV"))
        fig.update_xaxes(tickformat="%H:%M", nticks=5)
        fig.update_yaxes(title="ATM IV (%)", ticksuffix="%")
        fig = style_chart(fig, 175, legend=False)
    return delta, fig, current


def detector_view(ctx, read, plan, iv):
    move, flow, two = plan["movement"], plan["flow"], plan["flow_confirmation"]
    direction = plan["direction"]
    state = movement_status(plan)
    def show_stage(number, value, detail, **kwargs):
        stage(number, value, detail, ctx, read, plan, state, **kwargs)
    reason = (plan["blocks"] or plan["missing"] or plan["evidence"] or ["Awaiting a completed price and flow read."])[0]
    tint = "long" if plan["status"] == "ENTER LONG" else "short" if plan["status"] == "ENTER SHORT" else (
        "avoid" if plan["status"] == "AVOID" else "ready" if state != "NO TRADE" else "")
    st.markdown(
        '<section class="qd-command"><div class="qd-command-head"><div>'
        f'<div class="qd-eyebrow">{esc(ctx.ticker)} · {esc(ctx.scope)}</div>'
        f'<h2>{esc(state)}</h2><p>{esc(reason)}</p></div>'
        f'<span class="qd-status {tint}">{esc(plan["status"])}</span></div></section>',
        unsafe_allow_html=True,
    )
    nav_button("Open Trade Plan", "Trade Plan", "movement_open_trade")

    efficiency = ratio_pct(move["efficiency"])
    show_stage(1, move["market"], f"5m bias {plan['bias'].lower()} · directional price efficiency {efficiency} · "
                              f"same direction on {move['closed_bars']}/2 completed bars.")
    volatility = read["regime"]
    iv_minutes = st.segmented_control("IV change window", [1, 3, 5], default=3, key="movement_iv_window") or 3
    iv_watch = st.number_input("IV expansion watch (vol points; exploratory)", min_value=0.1, max_value=5.0,
                               value=0.3, step=0.1, format="%.1f", key="movement_iv_watch")
    iv_delta, iv_chart, iv_current = intraday_iv(ctx, read, iv, iv_minutes)
    atr = positive(volatility.get("atr"))
    iv_label = "Selected-expiry ATM IV" if ctx.expiry else "30-day ATM IV"
    detail = (f"5m ATR {amount(atr)} points · ATR versus baseline {amount(volatility.get('atr_ratio'))}×. "
              f"{iv_label} {ratio_pct(iv['atm'])} · {iv_minutes}m IV change "
              f"{iv_delta:+.2f} vol points." if iv_delta is not None else
              f"5m ATR {amount(atr)} points · ATR versus baseline {amount(volatility.get('atr_ratio'))}×. "
              f"{iv_label} {ratio_pct(iv['atm'])} · {iv_minutes}m IV change awaiting two observed snapshots.")
    if move["range_hold"]:
        detail += " Wait for a held range break with expanding activity."
    show_stage(2, plan["regime"] or "Waiting for bars", detail)
    st.caption((f"IV change over {iv_minutes}m: {iv_delta:+.2f} vol points · "
                f"{'Above watch level' if iv_delta >= iv_watch else 'Below watch level'}. "
                f"Example: 14.1% to 14.4% is +0.3 vol points.") if iv_delta is not None
               else "IV change waiting for two actual source snapshots spanning the selected window.")
    if iv_chart is not None:
        plot(iv_chart, "movement_iv_history")
    st.caption("Price ATR expansion and option IV expansion are different. IV has no universal trigger number; compare the change "
               "in vol points with the same expiry and confirm with fresh quotes and price/flow.")

    gamma = plan["gex"]
    if gamma is None:
        gamma_label = "Unavailable"
    else:
        gamma_label = "Negative GEX" if gamma < 0 else "Positive GEX" if gamma > 0 else "Flat GEX"
    levels = plan["gamma_levels"]
    wall = plan.get("nearest_wall") or {}
    gamma_note = (f"Modeled {compact(gamma, currency=True, signed=True)} per 1% · gamma flip {amount(levels['flip'])} · "
                  f"call wall {amount(levels['call_wall'])} · put wall {amount(levels['put_wall'])}.")
    if wall:
        gamma_note += f" Nearest wall {wall['label'].lower()}, {wall['distance']:+,.2f} points from spot."
    crossings = [cross for cross in plan["crossings"] if direction and two["confirmed"]
                 and cross["direction"] == ("above" if direction > 0 else "below")]
    if crossings:
        gamma_note += f" Closed price and flow agreed at {crossings[0]['level'].lower()}."
    show_stage(3, gamma_label, gamma_note)
    if gamma is not None and atr and plan["price_context_ready"]:
        snapshot, _ = get_hedge(ctx, read["gamma_feed"])
        left = session_state(ctx.now)["minutes_left"] - 1
        scenarios = [row for row in hedge_scenarios(snapshot, plan["price"], atr, left) if row["horizon"] == 5]
        if scenarios and all(row["total"] is not None for row in scenarios):
            values = " · ".join(f"{row['scenario']} {compact(row['total'], signed=True)}" for row in scenarios)
            st.caption(f"5m modeled hedge pressure in {'index' if ctx.ticker in INDICES else 'share'}-equivalent units. {values}")
        else:
            st.caption("5m hedge scenarios require current spot, compatible gamma/vanna/charm and five session minutes.")

    cvd = plan["cvd"]
    flow_interval = st.segmented_control("Net premium chart interval", ["1m", "3m", "5m"], default="5m",
                                         key="movement_flow_interval") or "5m"
    flow_feed = ctx.timeline(interval="1m") if flow_interval != "5m" else None
    flow_note = (f"Trade Plan 15m net premium {compact(flow.get('recent_net'), currency=True, signed=True)} · "
                 f"acceleration {compact(flow.get('momentum'), currency=True, signed=True)} · "
                 f"{two['count']}/2 completed flow intervals agree.")
    if cvd["delta"] is not None:
        flow_note += f" 10m {cvd['label']} {compact(cvd['delta'], signed=True)} shares."
        if two["confirmed"] and direction * cvd["delta"] < 0:
            flow_note += " Estimated share CVD is moving against options flow."
    else:
        flow_note += f" {cvd['label']} is unavailable." if ctx.ticker not in INDICES else f" {cvd['label']}."
    show_stage(4, ("Aligned" if two["confirmed"] else "Opposing" if direction * flow.get("direction", 0) < 0
              else "Waiting for agreement"), flow_note, flow_interval=flow_interval, flow_feed=flow_feed)
    st.caption(f"Chart: completed {flow_interval} net premium. Trade Plan confirmation still uses two completed 5m intervals.")
    if two["intervals"]:
        pieces = " · ".join(f"{et_time(row['close'], short=True)} ET {compact(row['net'], currency=True, signed=True)}"
                            for row in two["intervals"])
        st.caption(f"Completed flow intervals {pieces} · directional floor {compact(two['floor'], currency=True)} per interval.")
    if ctx.ticker == "SPX" and st.toggle("Show SPY estimated CVD for comparison", key="movement_spy_cvd"):
        proxy, proxy_feed = ctx.history(symbol="SPY")
        context = cvd_context(proxy, ctx.session, time_or_none(plan["entry_bar"]), "SPY estimated share CVD")
        if proxy_feed.error or feed_has_delay(proxy_feed) or context["delta"] is None:
            st.caption("Waiting for ten synchronized SPY share candles.")
        else:
            st.caption(f"SPY estimated 10m CVD {compact(context['delta'], signed=True)} shares through "
                       f"{et_time(context['close'], short=True)} ET. Comparison only.")

    low, high = plan["range"]
    structure = f"Prior 5m range {amount(low)}–{amount(high)} · session VWAP {amount(plan['vwap'])}."
    if move["boundary_hold"]:
        structure += f" Two closes held {amount(move['hold_level'])}."
    elif plan.get("failed_break"):
        structure += " Price returned through the last breakout level."
    elif plan.get("break_level") is not None:
        structure += f" Recent break {amount(plan['break_level'])}."
    show_stage(5, plan["setup"] or "Awaiting structure", structure)

    volume = (f"5m volume {move['volume']:.2f}× {move['volume_basis']} ({move['volume_sample']} comparisons)."
              if move["volume"] is not None else "Native 5m share-volume comparison unavailable.")
    momentum = DIRECTION[move["momentum"]]
    show_stage(6, momentum + (" · accelerating" if move["accelerating"] else ""),
          f"1m {DIRECTION[plan['ema']['1m']['direction']].lower()} · 3m {DIRECTION[plan['ema']['3m']['direction']].lower()}. {volume}")

    show_stage(7, state, f"{move['playbook']} · {reason}")
    trigger = (f"Trigger {amount(plan['trigger'])} · invalidation {amount(plan['invalidation'])} · stop {amount(plan['stop'])} · "
               f"TP1 {amount(plan['tp1'])} ({amount(plan['rr'])}R) · TP2 {amount(plan['tp2'])}."
               if plan["trigger"] is not None else "Waiting for a directional price level and setup.")
    model = plan.get("model") or {}
    if model.get("p_tp1") is not None and model.get("ev_r") is not None:
        trigger += f" Monte Carlo estimate · TP1 first {ratio_pct(model['p_tp1'])} · {model['ev_r']:+.2f}R."
    show_stage(8, plan["status"], f"{trigger} Evidence {plan['score']}/100 · entry minimum {MIN_ENTRY_SCORE}.")
    stamps = (f"{plan['source']} quote {et_time(plan['quote_at'], short=True)} · "
              f"5m close {et_time(plan['bar'], short=True)} · Tradytics flow close {et_time(plan['flow_at'], short=True)} · "
              f"GEX {et_time(plan['gamma_at'], short=True)} ET.")
    show_stage(9, "Preview" if ctx.preview else plan["confidence"], stamps)
    if plan["data_reasons"]:
        st.caption(" ".join(plan["data_reasons"]))
    if plan.get("delay_status") and not ctx.preview:
        st.caption(plan["delay_status"])


def strategy_panel(plan):
    playbook = plan["movement"]["playbook"]
    with st.container(border=True):
        st.markdown(f"**Strategy selection · {playbook}**")
        watch = PLAYBOOKS[playbook]
        if watch:
            st.caption("Setups to watch · " + " · ".join(watch))
        if playbook == "Range mean reversion":
            st.caption("Range-reversion ideas are available for research. The entry rules wait for a directional release.")
        related = LAB_MATCHES.get(playbook, ())
        if related:
            try:
                names = {row["id"]: row["name"] for row in audit_report()["rows"] if row["id"] in related}
            except (OSError, ValueError):
                names = {}
            studies = " · ".join(names.get(key, key) for key in related)
            st.caption(f"Related Strategy Lab studies · {studies}. Saved SPY results are exploratory.")
        left, right = st.columns(2)
        with left:
            nav_button("Movement Detector", "Movement Detector", "trade_open_movement")
        with right:
            nav_button("Strategy Lab", "Strategy Lab", "trade_open_lab")


@st.cache_data(show_spinner=False)
def audit_report():
    return json.loads(AUDIT_PATH.read_text(encoding="utf-8"))


def strategy_lab(_ctx):
    try:
        report = audit_report()
    except (OSError, ValueError):
        st.warning("The strategy audit could not be loaded. Check the research folder in the release package.")
        return
    counts = report["status_counts"]
    st.caption(f"{report['strategy_count']} strategies · {report['bars']:,} saved {' / '.join(report['symbols'])} bars · "
               f"{len(report['sessions'])} sessions · {min(report['sessions'])} through {max(report['sessions'])}.")
    a, b, c = st.columns(3)
    a.metric("Historical runs completed", counts.get("completed", 0))
    b.metric("Strategies with a selected test fold", report["selected_strategies"])
    c.metric("Configurations promoted", 0)
    st.caption("Selection uses the prior session. The next session compares the selected settings with unchanged settings and doubled costs. "
               "Results cover simulated underlying fills for the saved SPY sample.")
    rows = report["rows"]
    filters = st.columns([1.4, 1, 1])
    search = filters[0].text_input("Search strategy", placeholder="EMA, VWAP, TF02", key="lab_search").strip().casefold()
    family = filters[1].selectbox("Strategy family", ["All", *dict.fromkeys(row["family"] for row in rows)], key="lab_family")
    state = filters[2].selectbox("Historical run", ["All", *counts], key="lab_state")
    filtered = [row for row in rows if (family == "All" or row["family"] == family) and
                (state == "All" or row["status"] == state) and
                (not search or search in f"{row['id']} {row['name']}".casefold())]
    if filtered:
        columns = [{"ID": row["id"], "Strategy": row["name"], "Family": row["family"], "Historical status": row["status"].replace("_", " "),
                    "Selected test folds": row["selected_folds"], "Selected $": row["selected_pnl"],
                    "Unchanged $": row["comparison_pnl"], "2× cost $": row["stress_pnl"],
                    "Required inputs": row["inputs"], "Run detail": row["reason"]} for row in filtered]
        money = st.column_config.NumberColumn(format="$%.2f")
        st.dataframe(pd.DataFrame(columns), hide_index=True, height=min(430, 52 + 35 * len(columns)), width="stretch",
                     column_config={name: money for name in ("Selected $", "Unchanged $", "2× cost $")})
        by_id = {row["id"]: row for row in filtered}
        strategy_id = st.selectbox("Review strategy", list(by_id),
                                   format_func=lambda key: f"{key} · {by_id[key]['name']}", key="lab_review")
        selected = by_id[strategy_id]
        st.write(selected["rule"])
        if selected["reason"]:
            st.info(selected["reason"])
        if selected["folds"]:
            with st.expander("Settings and out-of-sample results"):
                for fold in selected["folds"]:
                    st.markdown(f"**Test session {fold['test_start'][:10]}**")
                    selection = fold.get("selection")
                    if selection is None:
                        st.caption("The preceding session had insufficient qualifying activity for parameter selection.")
                    else:
                        st.write({"Settings": selection["parameters"] or "Default indicator settings", "Risk changes": selection["risk"] or "Default risk",
                                  "Selected": fold["selected"], "Unchanged": fold["baseline"], "Double costs": fold["cost_stress"]})
        st.download_button("Download full strategy audit", AUDIT_PATH.read_bytes(), "strategy_audit.json",
                           "application/json", key="lab_audit_download")
    else:
        st.info("No strategies match these filters.")
