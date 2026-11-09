"""Plotly charts with common styling and explicit axis units."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_utils import utc
from market_session import ET
from terminal_data import numeric, time_or_none
from terminal_style import AMBER, BLUE, INK, MUTED, RED, TEAL, VIOLET, amount, style_chart


def price_figure(bars, ticker, timeframe, *, levels=None, overlays=True):
    rows = bars[-105:]
    times = [utc(row["time_utc"]) for row in rows]
    labels = [stamp.strftime("%b %d") if timeframe == "1D" else stamp.astimezone(ET).strftime("%b %d · %H:%M") for stamp in times]
    positions = list(range(len(rows)))
    volume_present = any(row.get("volume") is not None for row in rows)
    fig = make_subplots(rows=2 if volume_present else 1, cols=1, shared_xaxes=True,
                        vertical_spacing=.055, row_heights=[.81, .19] if volume_present else [1])
    fig.add_trace(go.Candlestick(x=positions,
        open=[r["open"] for r in rows], high=[r["high"] for r in rows],
        low=[r["low"] for r in rows], close=[r["close"] for r in rows], text=labels,
        increasing=dict(line=dict(color=TEAL, width=1), fillcolor=TEAL),
        decreasing=dict(line=dict(color=RED, width=1), fillcolor=RED), name=ticker), row=1, col=1)
    if overlays:
        closes = pd.Series([r["close"] for r in bars])
        for period, color in ((9, BLUE), (21, AMBER)):
            values = closes.ewm(span=period, adjust=False, min_periods=period).mean().iloc[-len(rows):]
            fig.add_trace(go.Scatter(x=positions, y=values, name=f"EMA {period}", line=dict(color=color, width=1.4),
                                     customdata=labels, hovertemplate="%{customdata}<br>%{y:,.2f}<extra>EMA " + str(period) + "</extra>"), row=1, col=1)
        vwaps = [row.get("session_vwap") for row in rows]
        if any(v is not None for v in vwaps):
            fig.add_trace(go.Scatter(x=positions, y=vwaps, name="Session VWAP", line=dict(color=VIOLET, width=1.4),
                                     customdata=labels, hovertemplate="%{customdata}<br>%{y:,.2f}<extra>VWAP</extra>"), row=1, col=1)
    if volume_present:
        fig.add_trace(go.Bar(x=positions, y=[r.get("volume") for r in rows],
                             marker_color=["#287d75" if r["close"] >= r["open"] else "#7c435a" for r in rows],
                             customdata=labels, hovertemplate="%{customdata}<br>%{y:,.0f} shares<extra>Volume</extra>",
                             name="Volume", showlegend=False), row=2, col=1)
        fig.update_yaxes(tickformat="~s", nticks=3, title="Volume", row=2, col=1)
    if levels:
        low, high = min(row["low"] for row in rows), max(row["high"] for row in rows)
        pad = max((high-low)*.25, rows[-1]["close"]*.0005)
        for name, price, color in levels:
            if price is not None and low - pad <= price <= high + pad:
                fig.add_hline(y=price, line_color=color, line_dash="dot", opacity=.7,
                              annotation_text=name, annotation_font_color=color, annotation_font_size=10,
                              annotation_position="top left", row=1, col=1)
    last = rows[-1]["close"]
    fig.add_annotation(x=len(rows)-1, y=last, text=amount(last), showarrow=False,
                       xanchor="left", xshift=10, bgcolor="#16433e", borderpad=4,
                       font=dict(color=TEAL, size=10), row=1, col=1)
    ticks = positions[::max(1, len(rows)//6)]
    fig.update_xaxes(rangeslider_visible=False, tickmode="array", tickvals=ticks,
                     ticktext=[labels[i] for i in ticks], range=[-.8, len(rows)+4], showgrid=False)
    fig.update_yaxes(side="right", tickformat=",.2f", row=1, col=1)
    fig.update_layout(bargap=.15)
    return style_chart(fig, 395, hover="closest", revision=f"{ticker}:{timeframe}")


def dated_rows(rows, field="time"):
    return sorted([(ts.astimezone(ET).replace(tzinfo=None), row) for row in rows if (ts := time_or_none(row.get(field)))],
                  key=lambda pair: pair[0])


def flow_figure(rows, components=False, compact=False):
    points = [(ts, row) for ts, row in dated_rows(rows) if numeric(row.get("net_premium_usd")) is not None]
    if not points:
        return None
    x = [ts for ts, _ in points]
    net = [numeric(row["net_premium_usd"]) for _, row in points]
    cumulative = pd.Series(net).cumsum()
    fig = make_subplots(specs=[[{"secondary_y": not compact}]])
    if not compact:
        fig.add_trace(go.Bar(x=x, y=net, marker_color=[TEAL if v >= 0 else RED for v in net],
                             name="Interval net", opacity=.78, hovertemplate="$%{y:,.0f}<extra>Interval net</extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(x=x, y=cumulative, name="Cumulative net", line=dict(color=BLUE if not compact else TEAL, width=2.1),
                             fill="tozeroy" if compact else None, fillcolor="rgba(57,213,180,.12)",
                             hovertemplate="$%{y:,.0f}<extra>Cumulative net</extra>"), secondary_y=not compact)
    if components and not compact:
        for field, name, color in (("call_premium_usd", "Net calls", TEAL), ("put_premium_usd", "Net puts", AMBER)):
            vals = [numeric(row.get(field)) for _, row in points]
            fig.add_trace(go.Scatter(x=x, y=pd.Series(vals, dtype=float).cumsum(), name=name, line=dict(color=color, width=1.25, dash="dot"),
                                     hovertemplate="$%{y:,.0f}<extra>" + name + "</extra>"), secondary_y=True)
    fig.update_yaxes(tickprefix="$", tickformat="~s", title=None if compact else "Interval net", secondary_y=False)
    if not compact:
        fig.update_yaxes(tickprefix="$", tickformat="~s", title="Cumulative net", showgrid=False, secondary_y=True)
    fig.update_xaxes(tickformat="%H:%M", nticks=6 if compact else 9)
    return style_chart(fig, 141 if compact else 330, legend=not compact)


def exposure_figure(rows, by, unit, spot=None, flip=None, *, split=False, compact=False):
    field = "strike" if by == "strike" else "expiry"
    data = [row for row in rows if row.get(field) is not None and numeric(row.get("net")) is not None]
    if by == "strike":
        data = sorted((row for row in data if numeric(row["strike"]) is not None), key=lambda row: numeric(row["strike"]))
    else:
        data.sort(key=lambda row: str(row[field]))
    if not data:
        return None
    x = [numeric(row[field]) if by == "strike" else str(row[field]) for row in data]
    y = [numeric(row["net"]) for row in data]
    fig = go.Figure(go.Bar(x=x, y=y, marker_color=[TEAL if v >= 0 else RED for v in y], name="Net",
                           hovertemplate="%{x}<br>%{y:,.0f} " + unit + "<extra>Net</extra>"))
    if split:
        for col, label, color in (("call", "Calls", BLUE), ("put", "Puts", AMBER)):
            values = [numeric(row.get(col)) for row in data]
            if any(value is not None for value in values):
                fig.add_trace(go.Scatter(x=x, y=values, mode="lines", line=dict(color=color, width=1.3), name=label))
    if by == "strike":
        for value, name, color in ((spot, "Spot", INK), (flip, "Flip", AMBER)):
            if value is not None and min(x) <= value <= max(x):
                fig.add_vline(x=value, line_color=color, line_dash="dot",
                              annotation_text=name, annotation_font_color=color, annotation_font_size=10)
    fig.update_xaxes(title=None if compact else field.title(), nticks=6 if compact else 12)
    fig.update_yaxes(tickformat="~s", title=None if compact else unit)
    return style_chart(fig, 176 if compact else 374, legend=split)


def series_figure(rows, x, series, *, height=300, y_format=None, ticksuffix=None, x_time=False):
    fig = go.Figure()
    for name, key, color, multiplier in series:
        pairs = [(row.get(x), v * multiplier) for row in rows if row.get(x) is not None and (v := numeric(row.get(key))) is not None]
        if pairs:
            xs, ys = zip(*pairs)
            fig.add_scatter(x=list(xs), y=list(ys), mode="lines+markers" if len(pairs) <= 18 else "lines",
                            name=name, line=dict(color=color, width=2), marker=dict(size=5))
    if not fig.data:
        return None
    if x_time:
        fig.update_xaxes(tickformat="%H:%M")
    fig.update_yaxes(tickformat=y_format, ticksuffix=ticksuffix)
    return style_chart(fig, height)


def dark_figure(rows):
    rows = [(numeric(row.get("price")), numeric(row.get("notional_usd"))) for row in rows]
    rows = sorted(((p, n) for p, n in rows if p is not None and n is not None), key=lambda pair: pair[1])[-12:]
    if not rows:
        return None
    fig = go.Figure(go.Bar(y=[amount(p) for p, _ in rows], x=[n for _, n in rows], orientation="h",
                           marker=dict(color=[n for _, n in rows], colorscale=[[0, "#284d6a"], [.5, "#438caa"], [1, TEAL]]),
                           hovertemplate="Price %{y}<br>Notional $%{x:,.0f}<extra></extra>"))
    fig.update_xaxes(tickprefix="$", tickformat="~s", title="Off-exchange notional")
    fig.update_yaxes(type="category", title="Price")
    return style_chart(fig, 372, hover="closest", legend=False)


def forecast_figure(result, minutes):
    rows = result["fan"][:minutes+1]
    x = [row["minute"] for row in rows]
    fig = go.Figure()
    for lo, hi, color, name in (("low_95", "high_95", "rgba(117,169,255,.17)", "95% range"),
                                 ("low_68", "high_68", "rgba(57,213,180,.29)", "68% range")):
        fig.add_scatter(x=x, y=[r[hi] for r in rows], line=dict(width=0), showlegend=False, hoverinfo="skip")
        fig.add_scatter(x=x, y=[r[lo] for r in rows], fill="tonexty", fillcolor=color, line=dict(width=0), name=name)
    fig.add_scatter(x=x, y=[row["median"] for row in rows], line=dict(color=TEAL, width=2.2), name="Median")
    fig.add_hline(y=result["spot"], line_color=MUTED, line_dash="dot")
    fig.update_xaxes(title="Minutes from forecast start")
    fig.update_yaxes(title="Underlying price / index level", tickformat=",.2f")
    return style_chart(fig, 405)
