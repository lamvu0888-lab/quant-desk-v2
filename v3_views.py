"""Movement, research and contract workspaces. Read-only in Streamlit."""
from datetime import timedelta

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from market_session import ET, calendar, rth_bounds
from option_quotes import option_rows, quote_status
from terminal_data import positive
from terminal_style import style_chart
from v3_options import YEAR, calibrated_iv, contract_spec, scenario, timeline
from v3_provenance import display_only


def movement_panel(ctx, plan, read=None):
    from movement_command import render
    render(ctx, plan, read)



def detector_comparison(reports, ticker, horizon):
    """Headline uses the most recent session, including a session with no eligible observations."""
    relevant = [r for r in reports if ticker in r.get("payload", {})
                and not any(display_only(item) for item in (r, r["payload"], r["payload"][ticker]))]
    if not relevant:
        return None
    report = max(relevant, key=lambda r: r["scope"])
    ticker_report = report["payload"][ticker]
    horizon_data = next((h for h in ticker_report.get("horizons", ()) if h.get("horizon") == horizon), {})
    counts = horizon_data.get("outcomes", {})
    comparable = sum(counts.get(k, 0) for k in ("up", "down", "neither"))
    directional = counts.get("up", 0) + counts.get("down", 0)
    rows = []
    comparisons = horizon_data.get("comparison", {})
    for key, label in (("v3_ignition", "V3 ignition or expansion"), ("baseline_move_watch", "V2 movement watch")):
        metrics = comparisons.get(key)
        if metrics:
            rows.append({"name":label, "signals":metrics["warning_origins"], "correct":metrics["correct"],
                         "precision":metrics.get("precision"), "recall":metrics.get("recall"),
                         "lead":metrics.get("median_seconds_to_touch")})
    return {"session":report["scope"], "config":report["payload"].get("research_config") or {},
            "scans":ticker_report.get("recorded", 0),
            "comparable":comparable, "directional":directional, "ambiguous":counts.get("ambiguous", 0), "rows":rows}


def learning(ctx):
    st.subheader("Movement comparison")
    if ctx.preview:
        st.info("Preview uses synthetic observations. Open Live to see the worker’s recorded performance.")
        return
    if not ctx.journal:
        st.info("Connect the worker’s shared journal to see recorded performance.")
        return
    reports = ctx.journal.records("DESK", "v3_report", limit=5)
    horizon = st.selectbox("Movement horizon", (3, 5, 10, 15), index=1, format_func=lambda n:f"{n} minutes",
                           key="movement_comparison_horizon")
    view = detector_comparison(reports, ctx.ticker, horizon)
    if view is None:
        st.info(f"Waiting for the first completed {ctx.ticker} research report.")
    elif not view["comparable"] or not view["rows"]:
        st.info(f"{ctx.ticker} · {view['session']} ET · No comparable {horizon}-minute outcomes yet.")
        st.caption(f"{view['scans']:,} scans recorded. Both methods need a complete future price window.")
    else:
        st.caption(f"{ctx.ticker} · {view['session']} ET · {view['comparable']:,} evaluated scans · "
                   f"{view['directional']:,} reached a price barrier · {view['ambiguous']:,} ambiguous scans excluded.")
        for column, row in zip(st.columns(2), view["rows"]):
            column.markdown(f"**{row['name']}**")
            if row["signals"]:
                hit = f"{row['precision']:.0%}" if row["precision"] is not None else "Unavailable"
                column.metric("Warnings in the correct direction", hit)
                column.caption(f"{row['correct']:,} correct out of {row['signals']:,} warning scans.")
            else:
                column.write("No directional warnings")
            if row["recall"] is not None:
                column.caption(f"Detected {row['recall']:.0%} of directional outcomes.")
            if row["lead"] is not None:
                column.caption(f"Median time from a correct scan to the price barrier: {row['lead']/60:.1f} min.")
        st.caption("Both methods use the same price window. Adjacent scans can describe the same move. Trade fills and P&L are tracked separately.")
    if view:
        config = view["config"]
        metadata = (f'{config["version"]} · {config["mode"]}' if config.get("version") and config.get("mode")
                    else "Unavailable for this archived report")
        st.caption(f"Recorded detector configuration · {metadata}")
    with st.expander("Independent price events"):
        for row in ctx.journal.records(ctx.ticker,"v3_events",limit=3):
            st.write(row["scope"])
            st.dataframe(row["payload"].get("events",[]),hide_index=True)
    for stage,label in (("report","Session coverage and outcomes"),("training","Challenger validation"),("feedback","OpenAI research proposals"),("replay","Proposal replay"),("discord","Daily summary delivery")):
        recent = reports if stage=="report" else ctx.journal.records("DESK","v3_"+stage,limit=5)
        with st.expander(label):
            if not recent:st.caption("No completed run in this journal.")
            for row in recent:
                st.write(row["scope"])
                st.json(row["payload"],expanded=False)
    from v3_feedback import budget_status
    usage=budget_status(ctx.journal,ctx.now.astimezone(ET).date())
    if usage:st.dataframe(usage,hide_index=True)
    st.caption("After close · outcomes +15 min · training +30 min · aggregate feedback +45 min. Production promotion disabled.")


def intervals(now,end):
    previous=now
    gaps=[]
    for s in calendar().sessions_in_range(now.astimezone(ET).date().isoformat(),end.astimezone(ET).date().isoformat()):
        op,cl=rth_bounds(s.date())
        if cl<=now or op>=end:continue
        if op>previous:gaps.append((previous,min(op,end)))
        previous=max(previous,min(cl,end))
    if previous<end:gaps.append((previous,end))
    return gaps


PRICE_COLORS = {
    "premium": "#42e6a5", "delta": "#73b5ff", "gamma": "#ffb451",
    "iv": "#d6a3ff", "underlying": "#d5e1ef", "theta": "#ff858e",
    "vega": "#63e4ee", "vanna": "#efb8e1", "charm": "#eff49a",
}
AXIS_LABELS = {
    "premium": "Premium ($)", "delta": "Delta", "gamma": "Gamma", "iv": "IV (%)",
    "underlying": "Underlying ($)", "theta": "Theta ($/day)", "vega": "Vega ($/IV pt)",
    "vanna": "Vanna (Δ/IV pt)", "charm": "Charm (Δ/day)",
}


def chart_history(ctx, spec, row, feed):
    """Keep genuine, already-observed samples of this exact contract on the time axis."""
    from terminal_data import numeric, time_or_none
    from v3_provenance import live_record, simulated_feed

    points = {}

    def add(sample, available_at):
        if str(sample.get("contract", "")).replace(" ", "").upper() != spec.code:
            return
        at = time_or_none(sample.get("updated_at"))
        if not at or at > available_at or at > spec.last_trade:
            return
        bid, ask = positive(sample.get("bid")), positive(sample.get("ask"))
        if bid is None or ask is None or ask < bid:
            return
        point = {k: numeric(sample.get(k)) for k in ("delta", "gamma", "theta", "vega")}
        iv = positive(sample.get("iv"))
        point.update(at=at, premium=(bid + ask) / 2, iv=100 * iv if iv else None)
        points[at] = point

    if not ctx.preview and ctx.journal:
        rows = ctx.journal.records(ctx.ticker, "v3_scan", scope=str(ctx.session), limit=450)
        for record in reversed(rows):
            payload = record["payload"]
            origin = time_or_none(record.get("origin"))
            sig = payload.get("signal", {})
            if not live_record(record) or not origin or origin > ctx.now:
                continue
            if sig.get("symbol") != ctx.ticker or sig.get("session") != str(ctx.session):
                continue
            for sample in payload.get("quote_samples", ()):
                add(sample, origin)
    if (ctx.preview or not simulated_feed(feed)) and quote_status(
        row, feed, ctx.now, paused=ctx.paused, max_age=ctx.option_max_age
    ) == "Fresh quote":
        add(row, ctx.now)
    return [points[at] for at in sorted(points)]


def _plot_time_axis(fig, start, end):
    ticks = [start + (end - start) * i / 4 for i in range(5)]
    fig.update_xaxes(
        type="date", range=[start, end], tickvals=ticks,
        ticktext=[t.astimezone(ET).strftime("%b %d<br>%H:%M") for t in ticks],
        title_text="Eastern time", showspikes=True, spikemode="across",
        spikesnap="cursor", spikethickness=1, spikecolor="#b0bdcc",
    )


def _display_values(values, scale):
    if scale is None:
        return values
    low, high = scale
    if low == high:
        return np.where(np.isfinite(values), 50., np.nan)
    return (values - low) * (100 / (high - low))


def combined(spec, times, prices, remaining, stats, entry, qty, cost, normalize, session_only,
             arrival, extra, *, observed=None, preview=False, spread=0.):
    """A single absolute time axis. Observed and scenario traces never join."""
    keys = ["premium", "delta", "gamma", "iv", *extra]
    data = {**stats, "underlying": prices}
    fig = go.Figure()
    samples = [point for point in (observed or []) if point["at"] <= times[0]]
    hist_t = []
    hist = {key: [] for key in keys}
    for point in samples:
        # A visible discontinuity signals a missing observation interval.
        if hist_t and point["at"] - hist_t[-1] > timedelta(minutes=2):
            hist_t.append(hist_t[-1] + timedelta(minutes=1))
            for series in hist.values():
                series.append(np.nan)
        hist_t.append(point["at"])
        for key in keys:
            item = point.get(key)
            hist[key].append(np.nan if item is None else item)

    start = min([times[0], *hist_t])
    gaps = intervals(start, times[-1])
    shape_list = [] if session_only else [
        dict(type="rect", x0=a, x1=b, y0=0, y1=1, yref="paper", layer="below",
             line_width=0, fillcolor="rgba(151,171,194,.10)") for a, b in gaps
    ]
    timestamps = [t.astimezone(ET).strftime("%b %d, %Y %H:%M %Z") for t in times]
    # After an AM-settled contract stops trading there is no executable exit bid.
    exit_concession = np.array([spread / 2 if at <= spec.last_trade and at < spec.expires else 0. for at in times])
    exit_values = np.maximum(0, data["premium"] - exit_concession)
    pnl = (exit_values - entry) * qty * spec.multiplier - 2 * cost * qty
    grouped_annotations = {}
    markers = [(times[0], "Now"), (arrival, "Target"), (spec.expires, "Expiry")]
    if spec.last_trade < spec.expires:
        markers.append((spec.last_trade, "Last trade"))
    for stamp, label in markers:
        if start <= stamp <= times[-1]:
            grouped_annotations.setdefault(stamp, []).append(label)
    for stamp, labels in grouped_annotations.items():
        shape_list.append(dict(type="line", x0=stamp, x1=stamp, y0=0, y1=1, yref="paper",
                               line=dict(dash="dot", width=1, color="#92a9c2")))
        fig.add_annotation(x=stamp, y=1, yref="paper", text=" / ".join(labels), showarrow=False,
                           yshift=9, font=dict(size=11, color="#eaf2ff"))

    for index, key in enumerate(keys):
        projected = np.asarray(data[key], dtype=float)
        actual = np.asarray(hist[key], dtype=float)
        finite = np.concatenate((projected[np.isfinite(projected)], actual[np.isfinite(actual)]))
        scale = (float(finite.min()), float(finite.max())) if normalize and finite.size else None
        axis = "y" if normalize or index == 0 else f"y{index+1}"
        label = AXIS_LABELS[key]
        hover = "%{customdata[0]}<br>" + label + " %{customdata[1]:.4g}"
        if key == "premium":
            hover += "<br>Theoretical P&L $%{customdata[2]:.2f}<br>Underlying $%{customdata[4]:.2f}<br>Remaining %{customdata[3]:.2f} h"
        fig.add_scatter(
            x=times, y=_display_values(projected, scale), yaxis=axis,
            name=label, mode="lines", legendgroup=key, connectgaps=False,
            line=dict(dash="dash", color=PRICE_COLORS[key], width=2),
            customdata=list(zip(timestamps, projected, pnl, remaining * YEAR / 3600, prices)),
            hovertemplate=hover + "<extra>Scenario</extra>",
        )
        if np.any(np.isfinite(actual)):
            qualifier = "Demo quote" if preview else "Observed quote"
            fig.add_scatter(
                x=hist_t, y=_display_values(actual, scale), yaxis=axis,
                name=qualifier + " · " + label, mode="lines+markers",
                legendgroup=key, showlegend=False, connectgaps=False,
                line=dict(dash="solid",color=PRICE_COLORS[key], width=2), marker_size=4,
                customdata=[(t.astimezone(ET).strftime("%b %d, %Y %H:%M %Z"), v) for t,v in zip(hist_t,actual)],
                hovertemplate="%{customdata[0]}<br>" + label + " %{customdata[1]:.4g}<extra>" + qualifier + "</extra>",
            )
    style_chart(fig, 460, hover="x unified")
    fig.update_layout(shapes=shape_list, dragmode="zoom", margin=dict(t=27,b=78,l=8,r=8),
                      legend=dict(y=-.22, font=dict(size=10)), hoverlabel=dict(namelength=-1))
    fig.update_yaxes(title_text="Relative scale 0–100" if normalize else "Premium ($)",
                     title_font_size=10, tickfont_size=10, automargin=True)
    if not normalize:
        for n, key in enumerate(keys[1:], 2):
            fig.update_layout(**{f"yaxis{n}": dict(
                overlaying="y",side="right" if n % 2 == 0 else "left",anchor="free",
                autoshift=True, showgrid=False, zeroline=False,
                showticklabels=n<=4, tickfont=dict(size=9, color=PRICE_COLORS[key]),
                title=dict(text=AXIS_LABELS[key] if n<=4 else None, font=dict(size=10,color=PRICE_COLORS[key])),
                automargin=True,
            )})
    _plot_time_axis(fig, start, times[-1])
    fig.update_xaxes(rangebreaks=[dict(values=[a], dvalue=(b-a).total_seconds()*1000)
                                  for a,b in gaps] if session_only else [],
                     rangeslider_visible=False)
    return fig


def contracts(ctx):
    from contract_workspace import contracts as show_contracts
    show_contracts(ctx)


def comparison_label(ticker, spec, now, peers=()):
    day = spec.expires.astimezone(ET).date()
    days = (day - now.astimezone(ET).date()).days
    label = f"{ticker} {spec.strike:g} {spec.right.title()} · {days}DTE"
    if any(other.code != spec.code and other.strike == spec.strike and other.right == spec.right
           and other.expires.astimezone(ET).date() == day for other in peers):
        return f"{label} · {spec.settlement.split(' · ')[0]}"
    return label


def compare_contracts(ctx, dates, exp, side, primary_spec, primary_times, primary_stats,
                      spot, target, arrival, shift, rate, div, fee, custom, *, iv_basis="midpoint",
                      primary_entry=None, primary_spread=None, qty=1):
    from contract_sources import contract_chain
    from iv_decision import iv_value

    if not st.toggle("Compare another strike or expiration", key="v3_compare"):
        return
    today = ctx.now.astimezone(ET).date()
    other_day = st.selectbox(
        "Comparison expiration", dates, index=dates.index(exp), key="v3_compare_expiry",
        format_func=lambda day: f"{(day-today).days}DTE · {day:%b %d, %Y}",
    )
    feed, source = contract_chain(ctx, other_day.isoformat(), side)
    entries = {}
    for row in option_rows(feed, ctx.ticker, other_day.isoformat()):
        if row.get("type") != side or quote_status(
            row, feed, ctx.now, max_age=ctx.option_max_age, paused=ctx.paused
        ) != "Fresh quote":
            continue
        try:
            other = contract_spec(row, ctx.ticker)
        except (ValueError, TypeError):
            continue
        if other.code != primary_spec.code and min(other.last_trade, other.expires) > ctx.now:
            entries[other.code] = other, row
    if not entries:
        st.info("No other fresh contract matches this side and expiration.")
        return
    peers = [primary_spec, *(spec for spec, row in entries.values())]
    selected = st.selectbox(
        "Comparison contract", sorted(entries, key=lambda key: abs(entries[key][0].strike-spot)),
        format_func=lambda key: comparison_label(ctx.ticker, entries[key][0], ctx.now, peers),
        key=f"v3_other_{primary_spec.code}_{other_day}",
    )
    other, row = entries[selected]
    fitted = iv_value(row.get("iv")) if iv_basis == "provider" else None
    iv_source = "provider IV"
    if fitted is None:
        fitted = calibrated_iv(other, spot, ctx.now, (row["bid"]+row["ask"])/2, rate, div)
        iv_source = "midpoint-implied IV"
    if fitted is None or fitted+shift <= 0:
        st.info("The comparison contract has no usable IV for this scenario.")
        return
    try:
        times = timeline(ctx.now, other.expires, arrival=arrival)
        _, _, stats = scenario(other, ctx.now, times, spot, target, arrival, fitted,
                               fitted+shift, rate, div, custom)
    except ValueError as exc:
        st.info(str(exc))
        return
    field = st.selectbox("Comparison measure", ["premium","delta","gamma","iv","theta","vega"],
                         format_func=lambda k: AXIS_LABELS[k])
    chart = go.Figure()
    summaries = []
    for spec, axis, data, entry, width in (
        (primary_spec, primary_times, primary_stats, primary_entry, primary_spread),
        (other, times, stats, row["ask"], row["ask"] - row["bid"]),
    ):
        label = comparison_label(ctx.ticker, spec, ctx.now, peers)
        chart.add_scatter(
            x=axis, y=data[field], name=label, mode="lines", line=dict(dash="dash"),
            customdata=[x.astimezone(ET).strftime("%b %d %H:%M %Z") for x in axis],
            hovertemplate="%{customdata}<br>%{y:.4g}<extra>%{fullData.name}</extra>",
        )
        if arrival not in axis:
            summaries.append(f"{label} expires before the target time.")
        elif entry is not None and width is not None:
            mark = data["premium"][axis.index(arrival)]
            concession = width/2 if arrival<spec.expires else 0.
            pnl = ((max(0,mark-concession)-entry)*spec.multiplier-2*fee)*qty
            if arrival<=spec.last_trade:
                summaries.append(f"{label} · target premium ${mark:,.2f} · modeled P&L ${pnl:,.2f}")
            else:
                summaries.append(f"{label} · theoretical value ${mark:,.2f} after last trading.")
    style_chart(chart, 330)
    chart.update_yaxes(title=AXIS_LABELS[field])
    _plot_time_axis(chart, ctx.now, max(primary_spec.expires, other.expires))
    st.plotly_chart(chart, width="stretch", theme=None)
    if summaries:
        st.write("  \n".join(summaries))
    st.caption(f"{source} · comparison starts from {iv_source} ({fitted:.1%}). Both contracts use the same underlying path and IV-point change.")
