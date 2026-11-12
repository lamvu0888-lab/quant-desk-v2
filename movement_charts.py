"""Compact evidence charts for the nine ordered Movement Detector checks.

All charts use data already loaded by the detector. None infer a trade signal
from an unsynchronized or unavailable feed.
"""

from __future__ import annotations

from datetime import timedelta

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_utils import utc
from desk_analytics import candles_on
from market_session import ET
from terminal_charts import exposure_figure
from terminal_data import display_exposure_unit, numeric, time_or_none
from terminal_style import AMBER, BLUE, INK, RED, TEAL, VIOLET, style_chart
from movement_detector import MIN_ENTRY_SCORE, MIN_RELATIVE_VOLUME


def _session_bars(ctx, read, key, count=30):
    return candles_on(read[key], ctx.session)[-count:]


def _clock(bars, minutes):
    return [(utc(bar["time_utc"]) + timedelta(minutes=minutes)).astimezone(ET).replace(tzinfo=None)
            for bar in bars]


def _price(ctx, read, plan, *, structure=False):
    bars = _session_bars(ctx, read, "candles" if structure else "buckets", 36 if structure else 24)
    if not bars:
        return None
    x = _clock(bars, 1 if structure else 5)
    fig = go.Figure()
    fig.add_scatter(x=x, y=[bar["close"] for bar in bars], name="Completed close",
                    line=dict(color=INK, width=2), hovertemplate="%{x|%H:%M} ET · %{y:,.2f}<extra>Close</extra>")
    if structure:
        for label, value, color in (("Range high", plan["range"][1], TEAL),
                                    ("Range low", plan["range"][0], RED),
                                    ("VWAP", plan["vwap"], VIOLET)):
            if value is not None:
                fig.add_hline(y=value, line_color=color, line_dash="dot",
                              annotation_text=label, annotation_font_color=color, annotation_font_size=10)
    fig.update_xaxes(tickformat="%H:%M", nticks=5)
    fig.update_yaxes(tickformat=",.2f", side="right", title="Price")
    return style_chart(fig, 182, legend=False)


def _volatility(ctx, read):
    bars = _session_bars(ctx, read, "buckets", 36)
    if len(bars) < 6:
        return None
    true_ranges = []
    for i, bar in enumerate(bars):
        previous = bars[i - 1]["close"] if i else bar["open"]
        true_ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - previous),
                               abs(bar["low"] - previous)))
    # This is a recent 5m true-range trend; the regime read has its own ATR baseline.
    points = [(i, sum(true_ranges[max(0, i - 13):i + 1]) / min(i + 1, 14))
              for i in range(4, len(bars))]
    fig = go.Figure(go.Scatter(x=[_clock(bars, 5)[i] for i, _ in points],
                               y=[value for _, value in points], mode="lines", name="5m true range · 14-bar mean",
                               line=dict(color=AMBER, width=2), fill="tozeroy"))
    fig.update_xaxes(tickformat="%H:%M", nticks=5)
    fig.update_yaxes(title="Points", rangemode="tozero")
    return style_chart(fig, 182, legend=False)


def _gamma(read, plan):
    if plan["gex"] is None:
        return None
    feed = read["gamma_feed"]
    rows = feed.rows if read["gamma"] else []
    if not rows:
        return None
    return exposure_figure(rows, "strike", display_exposure_unit(read["gamma"]),
                           plan["price"], plan["gamma_levels"]["flip"], compact=True,
                           focus_radius_pct=.015)


def _flow(ctx, read, plan):
    rows = [(time_or_none(row.get("time")), numeric(row.get("net_premium_usd")))
            for row in read["flow"].rows]
    rows = [(stamp, value) for stamp, value in rows
            if stamp is not None and value is not None and stamp.astimezone(ET).date() == ctx.session
            and stamp + timedelta(minutes=5) <= ctx.now][-12:]
    if not rows:
        return None
    x = [(stamp + timedelta(minutes=5)).astimezone(ET).replace(tzinfo=None) for stamp, _ in rows]
    values = [value for _, value in rows]
    fig = go.Figure(go.Bar(x=x, y=values, name="Completed 5m net premium",
                           marker_color=[TEAL if v >= 0 else RED for v in values],
                           hovertemplate="%{x|%H:%M} ET · $%{y:,.0f}<extra>Net premium</extra>"))
    floor = numeric(plan["flow_confirmation"].get("floor"))
    if floor is not None:
        fig.add_hline(y=floor, line_dash="dot", line_color=TEAL)
        fig.add_hline(y=-floor, line_dash="dot", line_color=RED)
    fig.update_xaxes(tickformat="%H:%M", nticks=5)
    fig.update_yaxes(title="Net premium ($)", tickformat="~s")
    return style_chart(fig, 182, legend=False)


def _momentum(ctx, read, plan):
    bars = _session_bars(ctx, read, "buckets", 20)
    if len(bars) < 3:
        return None
    x = _clock(bars, 5)
    deltas = [None] + [bar["close"] - bars[i - 1]["close"] for i, bar in enumerate(bars) if i]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.09, row_heights=[.6, .4])
    fig.add_trace(go.Bar(x=x, y=deltas, marker_color=[TEAL if (v or 0) >= 0 else RED for v in deltas],
                         name="5m close change"), row=1, col=1)
    volume = [numeric(bar.get("volume")) for bar in bars]
    if any(v is not None for v in volume):
        fig.add_trace(go.Bar(x=x, y=volume, marker_color=BLUE, opacity=.6, name="Share volume"), row=2, col=1)
        fig.update_yaxes(title="Shares", tickformat="~s", row=2, col=1)
    else:
        fig.add_annotation(text="Native share volume unavailable", xref="paper", yref="paper", x=.5, y=.13,
                           showarrow=False)
    fig.update_yaxes(title="Points", row=1, col=1)
    fig.update_xaxes(tickformat="%H:%M", nticks=5, row=2, col=1)
    return style_chart(fig, 208, legend=False)


def _decision(plan):
    move, flow = plan["movement"], plan["flow_confirmation"]
    items = [("Price closes", min(2, move["closed_bars"]), 2),
             ("Flow intervals", min(2, flow["count"]), 2),
             ("Evidence score", min(100, plan["score"]), MIN_ENTRY_SCORE)]
    fig = go.Figure()
    for label, value, target in items:
        fig.add_trace(go.Bar(y=[label], x=[100], orientation="h", marker_color="#dce4ee",
                             showlegend=False, hoverinfo="skip", width=.48))
        fig.add_trace(go.Bar(y=[label], x=[min(100, value / target * 100)], orientation="h",
                             marker_color=TEAL if value >= target else AMBER,
                             showlegend=False, width=.48,
                             hovertemplate=f"{label}: {value}/{target}<extra></extra>"))
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(visible=False, range=[0, 105])
    fig.update_yaxes(categoryorder="array", categoryarray=[item[0] for item in items][::-1])
    return style_chart(fig, 165, legend=False, hover="closest")


def _trigger(plan):
    levels = [("Stop", plan["stop"], RED), ("Invalidation", plan["invalidation"], AMBER),
              ("Trigger", plan["trigger"], BLUE), ("TP1", plan["tp1"], TEAL),
              ("TP2", plan["tp2"], VIOLET)]
    levels = [(label, numeric(value), color) for label, value, color in levels if numeric(value) is not None]
    if not levels:
        return None
    fig = go.Figure()
    for label, value, color in levels:
        fig.add_hline(y=value, line_color=color, line_dash="dot", annotation_text=f"{label} {value:,.2f}",
                      annotation_font_color=color, annotation_font_size=10)
    spot = numeric(plan["price"])
    if spot is not None:
        fig.add_scatter(x=["Current"], y=[spot], mode="markers", marker=dict(color=INK, size=11),
                        name="Current price", hovertemplate="%{y:,.2f}<extra>Current price</extra>")
    fig.update_xaxes(visible=False)
    fig.update_yaxes(tickformat=",.2f", side="right", title="Price")
    fig.update_layout(margin=dict(l=10, r=105, t=15, b=18))
    return style_chart(fig, 204, legend=False, hover="closest")


def _quality(ctx, plan):
    checks = [("Quote", plan.get("quote_at"), 120), ("5m price", plan.get("bar"), 420),
              ("Flow", plan.get("flow_at"), 420), ("GEX", plan.get("gamma_at"), 420)]
    fig = go.Figure()
    for label, stamp, limit in checks:
        observed = time_or_none(stamp)
        age = (ctx.now - observed).total_seconds() if observed else None
        valid = age is not None and 0 <= age <= limit
        value = min(max(age, 0) / limit, 1.25) if age is not None else 0
        fig.add_trace(go.Bar(y=[label], x=[1], orientation="h", marker_color="#dce4ee", width=.48,
                             showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Bar(y=[label], x=[value], orientation="h", width=.48,
                             marker_color=TEAL if valid else RED, showlegend=False,
                             hovertemplate=(f"{label}: {age:.0f}s / {limit}s limit" if age is not None
                                            else f"{label}: unavailable") + "<extra></extra>"))
        if age is None:
            fig.add_annotation(x=.02, y=label, text="Missing", showarrow=False, xanchor="left",
                               font=dict(color=RED, size=11))
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(visible=False, range=[0, 1.28])
    fig.update_yaxes(categoryorder="array", categoryarray=[item[0] for item in checks][::-1])
    return style_chart(fig, 182, legend=False, hover="closest")


def chart_for_stage(number, ctx, read, plan):
    """Return a chart or None when its input is unavailable."""
    return {1: lambda: _price(ctx, read, plan),
            2: lambda: _volatility(ctx, read),
            3: lambda: _gamma(read, plan),
            4: lambda: _flow(ctx, read, plan),
            5: lambda: _price(ctx, read, plan, structure=True),
            6: lambda: _momentum(ctx, read, plan),
            7: lambda: _decision(plan),
            8: lambda: _trigger(plan),
            9: lambda: _quality(ctx, plan)}[number]()


def next_cue(number, ctx, plan, state):
    """Short next observation keyed to the current check's state."""
    move, two = plan["movement"], plan["flow_confirmation"]
    if number == 1:
        return ("Watch the prior range edge and two completed 5m closes outside it; then check volatility."
                if move["range_hold"] else "Price has direction. Check whether 5m volatility is expanding.")
    if number == 2:
        return ("Wait for true range and activity to expand with a held break; then check GEX."
                if plan["regime"] == "Compressing" else "Check nearby GEX levels, then ask whether flow supports this move.")
    if number == 3:
        return ("Wait for a current modeled exposure snapshot; then compare completed flow intervals."
                if plan["gex"] is None else "Watch the nearest wall or flip as context; confirm direction with options flow.")
    if number == 4:
        return ("Wait for two completed, same-direction premium bars aligned with price; then inspect structure."
                if not two["confirmed"] else "Flow agrees on two bars. Check whether price held the range break or retest.")
    if number == 5:
        return ("Watch a retest and two closes holding the broken level before checking momentum."
                if move["boundary_hold"] else "Watch range high/low and VWAP; wait for a held break before checking momentum.")
    if number == 6:
        return ("Wait for direction, acceleration and stronger volume to agree; then review the movement decision."
                if not move["accelerating"] or move["volume"] is None or move["volume"] < MIN_RELATIVE_VOLUME
                else "Momentum and volume support the move. Review the decision checks below.")
    if number == 7:
        return ("Review the trigger, stop and targets below before considering an entry."
                if state in ("CONFIRMED MOVE", "LONG TRIGGER", "SHORT TRIGGER")
                else "No entry yet. Wait for the missing price or flow confirmation shown above.")
    if number == 8:
        return ("Check live option spread and risk against the stop; the displayed levels are underlying prices."
                if plan["status"].startswith("ENTER") else "Wait for a valid trigger and acceptable risk; do not treat watch levels as an entry.")
    return ("Refresh stale or missing feeds and wait for matching completed price/flow bars."
            if plan["data_reasons"] or plan["confidence"] == "Waiting for data"
            else "Inputs are synchronized. Recheck the first six stages on the next completed 5m bar.")
