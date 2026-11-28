"""Decision-oriented presentation of the shared V3 signal. Rendering cannot change a plan."""
from dataclasses import dataclass
from datetime import timedelta
from html import escape

import sqlite3

import psycopg
import streamlit as st

from market_session import ET
from terminal_data import numeric, positive, time_or_none
from v3_provenance import live_record, live_signal

MISSING = "Unavailable"
SCORE_LABELS = (
    ("readiness", "Readiness"), ("bullish", "Bullish pressure"), ("bearish", "Bearish pressure"),
    ("ignition", "Ignition"), ("continuation", "Continuation"), ("exhaustion", "Exhaustion risk"),
    ("data_confidence", "Data confidence"),
)


def number(value, digits=2, *, signed=False):
    val = numeric(value)
    return MISSING if val is None else format(val, f"{'+' if signed else ''},.{digits}f")


def amount(value):
    if value is None:
        return MISSING
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K"), (1, "")):
        if abs(value) >= divisor or divisor == 1:
            return f"{'-' if value < 0 else '+'}${abs(value)/divisor:,.2f}{suffix}"


def time_label(value):
    stamp = time_or_none(value)
    return stamp.astimezone(ET).strftime("%b %d · %I:%M:%S %p ET") if stamp else MISSING


def direction_text(value):
    return {1: "bullish", -1: "bearish", 0: "flat"}.get(value, MISSING)


def alignment(value, side):
    if value is None:
        return MISSING
    if not side or not value:
        return "Developing"
    return "Supportive" if value * side > 0 else "Conflicting"


@dataclass(frozen=True)
class Indicator:
    title: str
    status: str
    value: str
    meaning: str
    next_check: str
    observed: str


def fresh_label(sig, field):
    stamp = (sig.get("freshness") or {}).get(field)
    if not stamp and field == "price" and sig.get("price") is not None:
        stamp = sig.get("at")
    return time_label(stamp)


def plan_reasons(plan):
    values = [text for key in ("blocks", "data_reasons", "missing", "conflicts")
              for text in plan.get(key) or () if isinstance(text, str) and text]
    return list(dict.fromkeys(values))


def plan_summary(plan):
    state = plan.get("status") or "WAIT"
    setup = plan.get("setup") or "No active setup"
    if setup == "Breakout watch":
        setup = "Watching the range boundary"
    if plan.get("direction") in (-1, 1) and setup != "No active setup":
        setup += " · " + ("Long" if plan["direction"] == 1 else "Short")
    reasons = plan_reasons(plan)
    if state in ("ENTER LONG", "ENTER SHORT"):
        message = "The current Trade Plan qualifies for paper evaluation. Any fill requires the worker’s execution checks."
    elif state == "EXIT" or plan.get("active_paper"):
        message = "Review the active position against its protective stop and exit rules."
    elif reasons:
        message = reasons[0]
    elif state == "GET READY":
        message = "The setup is developing. Entry qualification is pending."
    else:
        message = "The Trade Plan has no qualified entry on this scan."
    return state, setup, message


def levels(sig, plan):
    f = sig.get("features") or {}
    side = sig.get("side", 0)
    agreement = bool(side and plan.get("direction") == side)
    trigger = positive(plan.get("trigger")) if agreement else None
    if not trigger:
        trigger = positive(f.get("range_high" if side == 1 else "range_low")) if side else None
    tp1 = positive(plan.get("tp1")) if agreement else None
    tp2 = positive(plan.get("tp2")) if agreement else None
    horizon = positive(plan.get("hold")) if tp1 else None
    if tp1:
        target = f"{number(tp1)} → {number(tp2)}" if tp2 and tp2 != tp1 else number(tp1)
        target_label = "Plan targets"
    else:
        barriers = sig.get("forecasts") or {}
        bound = "upper" if side == 1 else "lower"
        ends = [positive((barriers.get(k) or {}).get(bound)) for k in ("5", "15")] if side else []
        target = " → ".join(number(val) for val in ends) if ends and all(ends) else MISSING
        target_label = "Scenario barriers"
    return dict(trigger=number(trigger), trigger_value=trigger, target=target, target_label=target_label,
                invalidation=number(positive(sig.get("invalidation"))),
                horizon=f"{horizon:g} min" if horizon else "5–15 min" if target != MISSING and not tp1 else MISSING)


def checklist(sig, plan):
    f, quality = sig.get("features") or {}, sig.get("quality") or {}
    side = sig.get("side", 0)
    price_at = fresh_label(sig, "price")
    regime = sig.get("regime")
    rv = numeric(f.get("realized_volatility"))
    vol = f"{regime or 'Unclassified'} · {100 * rv:.3f}% typical 1m move" if rv is not None else (regime or MISSING)
    vol_status = "Supportive" if regime == "Expanding" else "Developing" if regime or rv is not None else MISSING
    out = [Indicator("Volatility regime", vol_status, vol,
                     {"Expanding":"Recent price ranges are expanding, which can give a move more room.",
                      "Compressing":"Recent ranges are contracting. Price may need to leave the range first."}.get(regime,
                      "The price regime has no expansion signal." if rv is not None else "No current volatility reading."),
                     "Watch the next range for continued expansion." if regime == "Expanding" else "Watch for ranges to widen.", price_at)]

    gamma = numeric(f.get("gex"))
    gamma_status = "Developing" if gamma is not None else MISSING
    out.append(Indicator("GEX and dealer positioning", gamma_status, f"{amount(gamma)} per 1% move" if gamma is not None else MISSING,
                         "Modeled negative gamma can amplify moves in either direction." if gamma is not None and gamma < 0 else
                         "Modeled positive gamma can dampen swings near large strikes." if gamma is not None and gamma > 0 else
                         "Modeled net gamma is flat." if gamma == 0 else "No qualifying dealer observation.",
                         "Watch the price response near the dealer levels." if gamma is not None else "Await a current scoped exposure snapshot.",
                         time_label((sig.get("freshness") or {}).get("gamma") or (plan.get("gamma_at") if gamma is not None else None))))

    net, cvd = numeric(f.get("net_pressure")), numeric(f.get("footprint_pressure"))
    evidence = [alignment(v, side) for v in (net, cvd)]
    flow_status = ("Conflicting" if "Conflicting" in evidence or net is not None and cvd is not None and net * cvd < 0
                   else MISSING if net is None and cvd is None else "Supportive" if evidence == ["Supportive"]*2 else "Developing")
    # Pressure describes the signed fraction of three intervals. Display the latest
    # native interval value when retained, with the signed pressure as a fallback.
    net_value = numeric(f.get("net_value"))
    cvd_value = numeric(f.get("footprint_value"))
    net_label = amount(net_value) if net_value is not None else (f"{net:+.2f} pressure" if net is not None else MISSING)
    cvd_label = f"{number(cvd_value,0,signed=True)} {f.get('footprint_units') or 'units'}" if cvd_value is not None else (f"{cvd:+.2f} pressure" if cvd is not None else MISSING)
    flow_value = f"1m netflow {net_label} · CVD delta {cvd_label}"
    details = []
    for name, p, delta, accel, units in (
        ("Netflow", net, f.get("net_change"), f.get("net_acceleration"), "$"),
        ("CVD", cvd, f.get("footprint_change"), f.get("footprint_acceleration"), f.get("footprint_units") or "units"),
    ):
        if p is not None:
            detail = f"{name} leans {'positive' if p > 0 else 'negative' if p < 0 else 'neutral'}"
            if delta is not None:
                detail += f", minute change {number(delta, 0, signed=True)} {units}"
            if accel is not None:
                detail += f", acceleration {number(accel, 0, signed=True)} {units}/min²"
            details.append(detail + ".")
    if cvd is not None:
        details.append(f"CVD source · {quality.get('cvd') or MISSING}.")
    flow_next = {"Conflicting":"Watch whether premium flow and signed volume return to the same direction.",
                 "Supportive":"Watch whether both remain aligned into the next completed minute."}.get(flow_status,
                 "Await three aligned, observed one-minute intervals for each input.")
    out.append(Indicator("Netflow and CVD", flow_status, flow_value, " ".join(details) or "No complete three-minute flow window.",
                         flow_next, f"Netflow {fresh_label(sig,'netflow')} · CVD {fresh_label(sig,'cvd')}"))

    velocity, accel, rv_ratio = (numeric(f.get(k)) for k in ("velocity", "acceleration", "relative_volume"))
    momentum_status = alignment(velocity, side)
    motion = "Price velocity is positive." if velocity and velocity > 0 else "Price velocity is negative." if velocity and velocity < 0 else "Price velocity is flat." if velocity == 0 else "Momentum unavailable."
    if accel is not None:
        motion += f" The latest change in velocity is {number(accel, signed=True)} volatility units."
    out.append(Indicator("Momentum and relative volume", momentum_status,
                         f"Velocity {number(velocity, signed=True)} · Volume {number(rv_ratio)}×" if rv_ratio is not None else f"Velocity {number(velocity, signed=True)} · Volume unavailable",
                         motion, "Watch for price and participation to strengthen in the same direction.", price_at))

    cross = sig.get("cross_market") or {}
    from schwab_futures import proxy_caption
    sampled = proxy_caption(sig.get("futures_cvd"))
    peer = cross.get("symbol")
    cross_status = alignment(cross.get("direction"), side) if peer else MISSING
    out.append(Indicator("Cross-market confirmation", cross_status,
                         f"{peer} {number(cross.get('change_pct'), 3, signed=True)}%" if peer else MISSING,
                         " ".join(filter(None, (
                             f"{cross.get('source') or 'Futures'} {peer} moved {direction_text(cross.get('direction'))} over the aligned minute." if peer else
                             "No synchronized peer price is available for this scan.", sampled))),
                         "Watch for agreement on the next aligned close." if peer else "Await an observed peer return with a matching close.",
                         time_label(cross.get("at"))))

    breakout, sweep = f.get("breakout"), f.get("sweep")
    event = sig.get("type")
    struct = breakout or sweep
    state = sig.get("state")
    struct_status = "Conflicting" if state in ("FAILED MOVE","INVALIDATED") else alignment(struct, side)
    if event and not struct and struct_status == MISSING:
        struct_status = "Developing"
    boundary = levels(sig, plan)["trigger_value"]
    watch = f"Watch whether price holds {'above' if side == 1 else 'below'} {number(boundary)} on a completed close." if boundary and side else "Watch the next completed range break or retest."
    structure = (f"Price closed {'above' if breakout == 1 else 'below'} the preceding six-bar range." if breakout
                 else f"The last candle swept and reclaimed the range {'low' if sweep == 1 else 'high'}." if sweep
                 else "The close is inside the preceding range." if breakout == 0 else "No valid price structure is available.")
    out.append(Indicator("Price structure", struct_status, event or MISSING, structure, watch, price_at))

    decision, setup, message = plan_summary(plan)
    out.append(Indicator("Trade Plan qualification","Supportive" if decision in ("ENTER LONG","ENTER SHORT") else
                         "Conflicting" if decision == "AVOID" else "Developing",f"{decision} · {setup}", message,
                         (plan.get("trigger_text") or "Review the next qualified setup in Trade Plan.").replace("Breakout watch:", "Range boundary:"),
                         time_label(plan.get("detected"))))
    return out


def comparable_rows(records, sig, scope):
    """A previous reading must be from this symbol/session, scope, engine, provider and mode."""
    latest = time_or_none(sig.get("at"))
    if not live_signal(sig) or latest is None:
        return []
    asof = time_or_none(sig.get("processed")) or latest
    rows = {}
    for row in sorted(records,key=lambda x:x["origin"]):
        p = row["payload"]
        s = p.get("signal") or {}
        t = time_or_none(s.get("at"))
        processed = time_or_none(row.get("origin"))
        if (not live_record(row) or t is None or t > latest or processed is None or not t <= processed <= asof or p.get("scope") != scope
                or any(s.get(k) != sig.get(k) for k in ("symbol","session","mode","version","source"))):
            continue
        rows.setdefault(t, row)
    return [rows[t] for t in sorted(rows)]


def current_explanation(sig, prior):
    state = sig.get("state") or "INSUFFICIENT DATA"
    f = sig.get("features") or {}
    if not sig.get("scores"):
        return sig.get("reason") or "Waiting for a current completed observation."
    narrative = []
    side = sig.get("side") or 0
    if state == "CONFLICTING PRESSURE":
        narrative.append("Flow and price evidence disagree on direction.")
    elif state in ("FAILED MOVE","INVALIDATED"):
        prior_level = numeric(sig.get("prior_invalidation"))
        narrative.append(f"Price crossed the previous movement invalidation at {number(prior_level)}."
                         if prior_level is not None else "Price crossed back through the prior structure. Recheck the setup boundary.")
    elif state == "EXHAUSTION":
        narrative.append("The move is losing support. Review protection on any existing position.")
    elif state == "QUIET":
        narrative.append("The current scan has weak directional evidence.")
    elif state == "BUILDING":
        narrative.append(f"Readiness is building. Pressure currently leans {direction_text(side)}." if side else "Readiness is building with balanced pressure.")
    else:
        narrative.append(f"{(sig.get('type') or 'Movement')} is producing {direction_text(side)} pressure.")
    if f.get("breakout"):
        narrative.append(f"Price closed {'above' if f['breakout']>0 else 'below'} the preceding six-bar range.")
    elif f.get("absorption"):
        narrative.append("Strong signed flow is producing little price change.")
    elif numeric(f.get("relative_volume")) is not None:
        narrative.append(f"The latest minute traded {f['relative_volume']:.2f}× the median volume of the prior six minutes.")
    if prior:
        prev = prior.get("scores") or {}
        cur = sig["scores"]
        for k in ("ignition","readiness"):
            if cur.get(k) is not None and prev.get(k) is not None and abs(cur[k]-prev[k])>=1:
                narrative.append(f"{k.title()} moved from {number(prev[k],0)} to {number(cur[k],0)} since the previous minute.")
                pf=prior.get("features") or {}
                field="velocity" if k=="ignition" else "relative_volume"
                if numeric(pf.get(field)) is not None and numeric(f.get(field)) is not None and pf[field]!=f[field]:
                    narrative.append(f"{field.replace('_',' ').title()} changed from {number(pf[field])} to {number(f[field])}.")
                break
    return " ".join(narrative)


STYLE = """
<style>
.mv-hero,.mv-level,.mv-score,.mv-check {background:#13202d;color:#f3f7fc;border:1px solid #34485c;border-radius:9px;min-width:0}
.mv-hero {padding:1rem 1.1rem;border-left:4px solid #65d4c1}
.mv-kicker,.mv-meta {color:#bccbd8;font-size:.8rem;line-height:1.45;overflow-wrap:anywhere}
.mv-hero .mv-title {font-weight:750;font-size:clamp(1.15rem,2vw,1.65rem);margin:.18rem 0}
.mv-hero .mv-summary {font-size:.93rem;line-height:1.5;color:#e0eaf4;margin:.3rem 0}
.mv-action {margin-top:.5rem;padding-top:.5rem;border-top:1px solid #34485c;font-size:.9rem}
.mv-level-grid,.mv-score-grid {display:grid;gap:.4rem;margin:.45rem 0}
.mv-level-grid {grid-template-columns:repeat(auto-fit,minmax(min(130px,44%),1fr))}
.mv-score-grid {grid-template-columns:repeat(auto-fit,minmax(min(93px,30%),1fr))}
.mv-level,.mv-score {padding:.45rem .55rem}
.mv-level b,.mv-score b {display:block;font-size:1.05rem;line-height:1.35}
.mv-score small {color:#b9c8d8;font-size:.65rem;font-weight:500}
.mv-score .mv-track {display:block;height:4px;background:#314355;border-radius:2px;margin-top:.32rem}
.mv-track i {display:block;height:100%;border-radius:2px}
.mv-checklist {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(270px,100%),1fr));gap:.5rem}
.mv-check {padding:.64rem .75rem;overflow-wrap:anywhere}
.mv-check-head {display:flex;align-items:baseline;justify-content:space-between;gap:.35rem}
.mv-check-head strong {font-size:.89rem;line-height:1.2}
.mv-check-value {font-weight:600;font-size:.88rem;color:#e9f3fb;margin:.3rem 0}
.mv-check p {font-size:.82rem;color:#cbd9e7;margin:.27rem 0;line-height:1.45}
.mv-check .mv-stamp {color:#b4c5d4;font-size:.73rem}
.mv-badge {font-size:.69rem;font-weight:700;padding:.12rem .28rem;border-radius:4px;white-space:nowrap}
.mv-supportive {background:#183b32;color:#8aedbf}.mv-conflicting {background:#40282f;color:#ffabb6}
.mv-developing {background:#393322;color:#ffdb82}.mv-unavailable {background:#303a46;color:#d5dfe9}
</style>
"""


def render(ctx, plan, read=None):
    sig = plan.get("movement_v3")
    if not sig:
        st.info(plan.get("v3_input_issue") or "Movement Intelligence is unavailable for this ticker or configuration.")
        return
    if (bool(ctx.preview) != bool(sig.get("preview")) or not ctx.preview and not live_signal(sig)):
        st.warning("Movement source mode changed. Waiting for a matching scan.")
        return
    rows = []
    if ctx.journal and not ctx.preview:
        try:
            records = ctx.journal.records(ctx.ticker, "v3_scan", scope=str(ctx.session), limit=480)
            rows = comparable_rows(records, sig, ctx.scope)
        except (OSError, sqlite3.Error, psycopg.Error):
            st.caption("Worker history unavailable. The current scan is shown.")
    close = time_or_none(sig.get("at"))
    prior = next((r["payload"]["signal"] for r in reversed(rows) if close and
                  time_or_none(r["payload"]["signal"]["at"]) == close - timedelta(minutes=1)),None)
    summary = current_explanation(sig, prior)
    decision, setup, message = plan_summary(plan)
    state = sig.get("state","INSUFFICIENT DATA")
    timing = sig.get("action") or "Wait for fresh movement evidence"
    mode = "Synthetic preview" if ctx.preview else sig.get("source") or MISSING
    header = f"""
    <section class="mv-hero"><div class="mv-kicker">Movement Command Center · {escape(ctx.ticker)}</div>
    <div class="mv-title">{escape(state)}</div><div class="mv-summary">{escape(summary)}</div>
    <div class="mv-meta">{escape(mode)} · {escape(time_label(sig.get("at")))} · {escape(sig.get("mode") or "Balanced")}</div>
    <div class="mv-action"><b>Next move</b> · {escape(timing)}<br>
    <b>Trade Plan · {escape(decision)}</b> · {escape(setup)}<br>{escape(message)}</div></section>"""
    info = levels(sig, plan)
    boundary_side = {1: "Above", -1: "Below"}.get(sig.get("side"))
    invalid_side = {1: "Below", -1: "Above"}.get(sig.get("side"))
    trigger = f"{boundary_side} {info['trigger']}" if boundary_side and info["trigger"] != MISSING else info["trigger"]
    invalidate = f"{invalid_side} {info['invalidation']}" if invalid_side and info["invalidation"] != MISSING else info["invalidation"]
    invalid_label = "Movement invalidation"
    if state == "INVALIDATED" and positive(sig.get("prior_invalidation")):
        invalid_label, invalidate = "Broken level", number(sig["prior_invalidation"])
    fields = [("Trigger to watch",trigger),(info["target_label"],info["target"]),
              (invalid_label,invalidate),("Horizon",info["horizon"])]
    level_html = "".join(f'<div class="mv-level"><span class="mv-meta">{escape(k)}</span><b>{escape(v)}</b></div>' for k,v in fields)
    palette = ["#7bbaff","#68e1ac","#ffa3ae","#ffbb76","#77d9ee","#e8a9ff","#b8c8db"]
    scores = sig.get("scores") or {}
    cards = []
    for (key, title), color in zip(SCORE_LABELS, palette):
        value = numeric(scores.get(key))
        label = f'{value:.0f}<small> / 100</small>' if value is not None else "N/A"
        width = max(0,min(value,100)) if value is not None else 0
        cards.append(f'<div class="mv-score"><span class="mv-meta">{title}</span><b>{label}</b>'
                     f'<span class="mv-track"><i style="width:{width:.1f}%;background:{color}"></i></span></div>')
    st.html(STYLE + header + '<div class="mv-level-grid">' + level_html + '</div>'
            + '<div class="mv-score-grid">' + ''.join(cards) + '</div>')
    st.caption("Scores show current evidence on a 0–100 scale. Scenario barriers use recent volatility. Both are experimental.")
    st.subheader("Movement confirmation")
    details = []
    for ind in checklist(sig, plan):
        tone = ind.status.lower()
        details.append(f'<article class="mv-check"><div class="mv-check-head"><strong>{escape(ind.title)}</strong>'
                       f'<span class="mv-badge mv-{tone}">{escape(ind.status)}</span></div>'
                       f'<div class="mv-check-value">{escape(ind.value)}</div><p>{escape(ind.meaning)}</p>'
                       f'<p><b>Watch</b> · {escape(ind.next_check)}</p><div class="mv-stamp">{escape(ind.observed)}</div></article>')
    st.html('<div class="mv-checklist">' + ''.join(details) + '</div>')
    from movement_charts_v3 import render_movement_charts
    render_movement_charts(ctx, plan, read, records=rows)
