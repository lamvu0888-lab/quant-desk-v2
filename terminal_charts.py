"""Plotly charts with common styling and explicit axis units."""
from __future__ import annotations

from bisect import bisect_right
from datetime import timedelta
from math import sqrt

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from desk_analytics import flow_read
from data_utils import utc
from market_session import ET
from terminal_data import et_time, interval_length, numeric, time_or_none
from terminal_style import AMBER, BLUE, INK, MUTED, RED, TEAL, VIOLET, amount, compact, esc, style_chart


def quote_marker(quote, x):
    return go.Scatter(x=[x], y=[quote["price"]], mode="markers", name=f"{quote['source']} quote",
                      marker=dict(color=AMBER, size=10, symbol="diamond", line=dict(color="white", width=1)),
                      text=[f"{quote['basis']} · {et_time(quote['at'])}"],
                      hovertemplate="%{y:,.2f}<br>%{text}<extra>%{fullData.name}</extra>", showlegend=False)


def price_figure(bars, ticker, timeframe, *, levels=None, overlays=True, orders=(), order_labels=False, quote=None):
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
        for field, name, dash in (("session_vwap", "Session VWAP", "solid"),
                                  ("estimated_session_vwap", "VWAP estimate from candles", "dot")):
            vwaps = [row.get(field) for row in rows]
            if any(v is not None for v in vwaps):
                fig.add_trace(go.Scatter(x=positions, y=vwaps, name=name, line=dict(color=VIOLET, width=1.4, dash=dash),
                                         customdata=labels, hovertemplate="%{customdata}<br>%{y:,.2f}<extra>%{fullData.name}</extra>"), row=1, col=1)
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
    if orders and timeframe != "1D":
        width = interval_length(timeframe)
        visible = []
        for order in orders:
            i = bisect_right(times, order["stamp"]) - 1
            if i >= 0 and order["stamp"] < times[i] + width:
                fraction = (order["stamp"] - times[i]) / width
                visible.append((i + .8 * fraction - .4, order))
        for trace in order_traces(visible, show_labels=order_labels, max_size=29):
            fig.add_trace(trace, row=1, col=1)
    show_quote = (timeframe != "1D" and quote and quote["at"] >= times[-1] + interval_length(timeframe)
                  and quote["at"].astimezone(ET).date() == times[-1].astimezone(ET).date())
    if show_quote:
        fig.add_trace(quote_marker(quote, len(rows)), row=1, col=1)
    last = quote["price"] if show_quote else rows[-1]["close"]
    fig.add_annotation(x=len(rows) if show_quote else len(rows)-1, y=last, text=amount(last), showarrow=False,
                       xanchor="left", xshift=10, bgcolor="#e4f4ed", borderpad=4,
                       font=dict(color=TEAL, size=10), row=1, col=1)
    ticks = positions[::max(1, len(rows)//6)]
    fig.update_xaxes(rangeslider_visible=False, tickmode="array", tickvals=ticks,
                     ticktext=[labels[i] for i in ticks], range=[-.8, len(rows)+4], showgrid=False)
    fig.update_yaxes(side="right", tickformat=",.2f", row=1, col=1)
    fig.update_layout(bargap=.15)
    return style_chart(fig, 395, hover="closest", revision=f"{ticker}:{timeframe}")


def ema_series(bars, period=21):
    """Warm up using a simple average, then calculate EMA from completed closes."""
    if len(bars) < period:
        return [None] * len(bars)
    alpha = 2 / (period + 1)
    result = [None] * (period - 1) + [sum(bar["close"] for bar in bars[:period]) / period]
    for bar in bars[period:]:
        result.append(result[-1] + alpha * (bar["close"] - result[-1]))
    return result


def rolling_chart_flow(rows, interval):
    """Sum the last three intervals only where the feed has no gaps."""
    width = interval_length(interval)
    times = [utc(row["time"]) for row in rows]
    net = [numeric(row["net_premium_usd"]) for row in rows]
    rolling = [None] * len(rows)
    for i in range(2, len(rows)):
        window = net[i - 2:i + 1]
        if None not in window and times[i] - times[i - 1] == times[i - 1] - times[i - 2] == width:
            rolling[i] = sum(window)
    return rolling


def integrated_intraday_figure(bars, ticker, timeframe, *, flow_rows=(), levels=(), gamma_rows=(),
                               gamma_unit="", cvd_points=(), profile="Volume", quote=None):
    """Align completed price, flow and share CVD on the same market clock."""
    rows = bars[-105:]
    if not rows:
        return None
    width = interval_length(timeframe)
    first = utc(rows[0]["time_utc"])
    last_close = utc(rows[-1]["time_utc"]) + width

    def clock(stamp):
        return stamp.astimezone(ET).replace(tzinfo=None)

    times = [clock(utc(row["time_utc"]) + width) for row in rows]
    window_minutes = 3 * (width // timedelta(minutes=1))
    flow_title = f"{timeframe} net premium · {window_minutes}m rolling sum"
    profile_title = "Volume profile" if profile == "Volume" else "Modeled net GEX"
    fig = make_subplots(rows=3, cols=2, specs=[[{}, {}], [{}, None], [{}, None]],
                        shared_xaxes=True, shared_yaxes=True, column_widths=[.76, .24],
                        row_heights=[.64, .19, .17], vertical_spacing=.09, horizontal_spacing=.025,
                        subplot_titles=(f"{ticker} · {timeframe} closes", profile_title, flow_title, "Estimated share CVD"))
    fig.add_trace(go.Candlestick(x=times, open=[row["open"] for row in rows], high=[row["high"] for row in rows],
                                 low=[row["low"] for row in rows], close=[row["close"] for row in rows],
                                 increasing=dict(line=dict(color=TEAL, width=1), fillcolor=TEAL),
                                 decreasing=dict(line=dict(color=RED, width=1), fillcolor=RED),
                                 name=ticker, showlegend=False), row=1, col=1)
    for field, label, color, dash in (("ema21", "EMA 21", BLUE, "solid"),
                                      ("session_vwap", "Session VWAP", VIOLET, "solid"),
                                      ("estimated_session_vwap", "Estimated VWAP", VIOLET, "dot")):
        values = [row.get(field) for row in rows]
        if any(value is not None for value in values):
            fig.add_scatter(x=times, y=values, name=label, line=dict(color=color, width=1.6, dash=dash),
                            hovertemplate="%{x|%H:%M} ET · %{y:,.2f}<extra>%{fullData.name}</extra>", row=1, col=1)
    for name, price, color in levels:
        fig.add_hline(y=price, line_color=color, line_dash="dot",
                      annotation_text=f"{name} {amount(price)}", annotation_font_color=color,
                      annotation_font_size=9, annotation_position="top left", row=1, col=1)
    if quote and quote["at"] >= last_close:
        fig.add_trace(quote_marker(quote, clock(quote["at"])), row=1, col=1)
        fig.add_hline(y=quote["price"], line_color=AMBER, line_dash="dot", opacity=.55, row=1, col=2)

    low, high = min(row["low"] for row in rows), max(row["high"] for row in rows)
    if profile == "Volume":
        bin_count = 14
        step = (high - low) / bin_count if high > low else low * .0002
        totals = [0.0] * bin_count
        for row in rows:
            if (volume := numeric(row.get("volume"))) is not None and volume > 0:
                index = min(bin_count - 1, int((row["close"] - low) / step))
                totals[index] += volume
        if any(totals):
            populated = [(total, low + (i + .5) * step) for i, total in enumerate(totals) if total > 0]
            fig.add_bar(x=[total for total, _ in populated], y=[price for _, price in populated], width=step * .86,
                        orientation="h", marker_color="#8da8c3", name="Volume profile", showlegend=False,
                        hovertemplate="Price bin %{y:,.2f}<br>%{x:,.0f} shares<extra>Estimated volume</extra>", row=1, col=2)
        else:
            fig.add_annotation(text="No share<br>volume", x=.5, y=.5, xref="x domain", yref="y domain",
                               showarrow=False, row=1, col=2)
    else:
        prices = [low, high, *(price for _, price, _ in levels)]
        if quote:
            prices.append(quote["price"])
        pad = max((high - low) * .15, rows[-1]["close"] * .0004)
        lower, upper = min(prices) - pad, max(prices) + pad
        near = [(strike, net) for row in gamma_rows if isinstance(row, dict) and
                (strike := numeric(row.get("strike"))) is not None and lower <= strike <= upper
                and (net := numeric(row.get("net"))) is not None]
        if near:
            fig.add_bar(x=[net for _, net in near], y=[strike for strike, _ in near], orientation="h",
                        marker_color=[TEAL if net >= 0 else RED for _, net in near], name="Net GEX", showlegend=False,
                        hovertemplate="Strike %{y:,.2f}<br>%{x:,.0f} " + esc(gamma_unit) + "<extra>Modeled GEX</extra>", row=1, col=2)
        else:
            fig.add_annotation(text="No nearby<br>GEX", x=.5, y=.5, xref="x domain", yref="y domain",
                               showarrow=False, row=1, col=2)

    flow = [(clock(end), numeric(row["net_premium_usd"]), force)
            for row, force in zip(flow_rows, rolling_chart_flow(flow_rows, timeframe))
            if (end := utc(row["time"]) + width) > first]
    if flow:
        dates, values, rolling = zip(*flow)
        fig.add_bar(x=dates, y=values, marker_color=[TEAL if value >= 0 else RED for value in values],
                    name="Interval net premium", showlegend=False,
                    hovertemplate="%{x|%H:%M} ET · $%{y:,.0f}<extra>Interval net premium</extra>", row=2, col=1)
        fig.add_scatter(x=dates, y=rolling, mode="lines", line=dict(color=AMBER, width=1.8), name="Rolling net",
                        hovertemplate="%{x|%H:%M} ET · $%{y:,.0f}<extra>Rolling net</extra>", row=2, col=1)
    else:
        fig.add_annotation(text="Waiting for completed flow", x=.5, y=.5, xref="x domain", yref="y domain",
                           showarrow=False, row=2, col=1)
    points = [(clock(stamp), point["cvd"]) for point in cvd_points if (stamp := utc(point["time"])) > first]
    if points:
        cvd_times, cvd_values = zip(*points)
        fig.add_scatter(x=cvd_times, y=cvd_values, mode="lines", line=dict(color=TEAL, width=1.7),
                        name="Estimated share CVD", showlegend=False,
                        hovertemplate="%{x|%H:%M} ET · %{y:,.0f} shares<extra>Estimated share CVD</extra>", row=3, col=1)
    else:
        fig.add_annotation(text="Share CVD unavailable", x=.5, y=.5, xref="x domain", yref="y domain",
                           showarrow=False, row=3, col=1)
    fig.update_xaxes(rangeslider_visible=False, type="date", row=1, col=1)
    fig.update_xaxes(showgrid=False, showticklabels=False, fixedrange=True, row=1, col=2)
    fig.update_xaxes(type="date", row=2, col=1)
    fig.update_xaxes(type="date", tickformat="%H:%M", nticks=5, title="Time (ET)", row=3, col=1)
    fig.update_yaxes(tickformat=",.2f", row=1, col=1)
    fig.update_yaxes(showticklabels=False, showgrid=False, row=1, col=2)
    fig.update_yaxes(tickformat="~s", tickprefix="$", nticks=3, row=2, col=1)
    fig.update_yaxes(tickformat="~s", nticks=3, row=3, col=1)
    fig.update_annotations(font_size=10, selector=lambda annotation: annotation.xref == "paper")
    styled = style_chart(fig, 610, hover="closest", revision=f"integrated:{ticker}:{timeframe}:{first.date()}")
    styled.update_layout(margin=dict(t=24, b=50), legend=dict(y=-.12))
    return styled


def dated_rows(rows, field="time"):
    return sorted([(ts.astimezone(ET).replace(tzinfo=None), row) for row in rows if (ts := time_or_none(row.get(field)))],
                  key=lambda pair: pair[0])


ORDER_STYLES = (
    ("call", "Bought", "Calls bought", TEAL, "circle"),
    ("put", "Bought", "Puts bought", RED, "circle"),
    ("call", "Sold", "Calls sold", RED, "circle-open"),
    ("put", "Sold", "Puts sold", TEAL, "circle-open"),
    ("call", "Mid", "Calls at mid", MUTED, "diamond"),
    ("put", "Mid", "Puts at mid", MUTED, "diamond"),
    ("call", "Unknown", "Calls · side unclear", MUTED, "diamond-open"),
    ("put", "Unknown", "Puts · side unclear", MUTED, "diamond-open"),
)


def order_traces(points, *, show_labels, max_size):
    if not points:
        return []
    peak = max(row["premium_usd"] for _, row in points)
    traces = []
    for option_type, side, name, color, marker in ORDER_STYLES:
        group = [(x, row) for x, row in points if row["type"] == option_type and row["side_label"] == side]
        if not group:
            continue
        positions, orders = zip(*group)
        labels = [f"{option_type[0].upper()} {amount(row.get('strike'))} · {compact(row['premium_usd'], currency=True)}"
                  if show_labels else option_type[0].upper() for row in orders]
        detail = [[et_time(row["stamp"]), esc(row.get("contract") or option_type.upper()), row["premium_usd"],
                   amount(row.get("size"), 0), esc(row.get("expiry") or "—"), esc(row.get("kind") or "—"),
                   "Checked" if row.get("side_checked") is True else "Provisional" if row.get("side_checked") is False else "Unverified",
                   row["spot_basis"], esc(row.get("condition") or "—"), amount(row.get("bid")), amount(row.get("ask"))] for row in orders]
        traces.append(go.Scatter(
            x=positions, y=[row["spot"] for row in orders], mode="markers+text", name=name, text=labels,
            textposition="top center" if show_labels else "middle center", textfont={"color": INK, "size": 10 if show_labels else 8},
            marker=dict(size=[max(12, max_size * sqrt(row["premium_usd"] / peak)) for row in orders], color=color,
                        line=dict(width=1.4, color=color), symbol=marker,
                        opacity=[.44 if row.get("side_checked") is False else .78 for row in orders]),
            customdata=detail, hovertemplate=("<b>%{customdata[0]}</b><br>%{customdata[1]}<br>" + name +
                " · $%{customdata[2]:,.0f} · %{customdata[3]} contracts<br>Expiry %{customdata[4]} · %{customdata[5]}<br>"
                "Side check: %{customdata[6]} · Condition: %{customdata[8]}<br>Bid %{customdata[9]} / Ask %{customdata[10]}"
                "<br>%{customdata[7]}: %{y:,.2f}<extra></extra>")))
    return traces


def bubble_figure(bars, orders, ticker, *, labels=False):
    if not bars:
        return None
    fig = go.Figure()
    fig.add_scatter(x=[(utc(row["time_utc"]) + timedelta(minutes=1)).astimezone(ET).replace(tzinfo=None) for row in bars],
                    y=[row["close"] for row in bars], name=ticker, line=dict(color=INK, width=1.75),
                    hovertemplate="%{x|%H:%M ET}<br>%{y:,.2f}<extra>Completed close</extra>")
    points = [(row["stamp"].astimezone(ET).replace(tzinfo=None), row) for row in orders]
    for trace in order_traces(points, show_labels=labels, max_size=39):
        fig.add_trace(trace)
    fig.update_xaxes(tickformat="%H:%M", title="New York time")
    fig.update_yaxes(side="right", tickformat=",.2f")
    return style_chart(fig, 335, hover="closest", revision=ticker)


def flow_desk_figure(rows, interval, *, components=True):
    if not rows:
        return None
    width = interval_length(interval)
    times = [(utc(row["time"]) + width).astimezone(ET).replace(tzinfo=None) for row in rows]
    net = [numeric(row["net_premium_usd"]) for row in rows]
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=.065, row_heights=[.50, .25, .25],
                        specs=[[{"secondary_y": True}], [{}], [{}]])
    series = [("Directional net", "net_premium_usd", TEAL)]
    if components:
        series += [("Net calls", "call_premium_usd", BLUE), ("Net puts", "put_premium_usd", AMBER)]
    for name, field, color in series:
        vals = [numeric(row.get(field)) for row in rows]
        if any(v is not None for v in vals):
            fig.add_scatter(x=times, y=pd.Series(vals, dtype=float).cumsum(), name=name, row=1, col=1,
                            line=dict(color=color, width=2 if field == "net_premium_usd" else 1.15),
                            hovertemplate="$%{y:,.0f}<extra>" + name + "</extra>")
    prices = [numeric(row.get("price")) for row in rows]
    if any(price is not None for price in prices):
        fig.add_scatter(x=times, y=prices, name="Price", line=dict(color=INK, width=1.25), row=1, col=1, secondary_y=True)
        fig.update_yaxes(showgrid=False, row=1, col=1, secondary_y=True, title="Price")
    fig.add_bar(x=times, y=net, name="Interval net", marker_color=[TEAL if v >= 0 else RED for v in net],
                hovertemplate="$%{y:,.0f}<extra>Interval net</extra>", row=2, col=1)
    momentum, diverging = [], []
    for i, stamp in enumerate(times):
        if i < 5:
            momentum.append(None)
            continue
        window = rows[i - 5:i + 1]
        consecutive = all(utc(b["time"]) - utc(a["time"]) == width for a, b in zip(window, window[1:]))
        rate = (sum(net[i - 2:i + 1]) - sum(net[i - 5:i - 2])) / 3 if consecutive else None
        momentum.append(rate)
        direction = flow_read(window).get("direction") if interval == "5m" else None
        if direction and prices[i] and prices[i - 3] and (prices[i] - prices[i - 3]) * direction < 0:
            diverging.append((stamp, rate))
    fig.add_bar(x=times, y=momentum, name="Flow momentum", marker_color=[TEAL if (v or 0) >= 0 else RED for v in momentum],
                hovertemplate="$%{y:,.0f}<extra>Change in average flow</extra>", row=3, col=1)
    if diverging:
        stamps, values = zip(*diverging)
        fig.add_scatter(x=stamps, y=values, mode="markers", name="Price / flow disagree", marker=dict(color=AMBER, symbol="diamond", size=7),
                        hovertemplate="Price and flow moved in opposite directions<extra></extra>", row=3, col=1)
    fig.update_yaxes(row=1, col=1, secondary_y=False, tickprefix="$", tickformat="~s", title="Cumulative")
    fig.update_yaxes(row=2, col=1, tickprefix="$", tickformat="~s", title="Interval")
    fig.update_yaxes(row=3, col=1, tickprefix="$", tickformat="~s", title="Momentum")
    fig.update_xaxes(tickformat="%H:%M", title="Interval close · ET", row=3, col=1)
    return style_chart(fig, 445)


def cvd_figure(points, *, unit="shares"):
    if not points:
        return None
    x = [utc(row["time"]).astimezone(ET).replace(tzinfo=None) for row in points]
    y = [row["cvd"] for row in points]
    deltas = [row["delta"] for row in points]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.08, row_heights=[.70, .30])
    fig.add_scatter(x=x, y=y, name="Cumulative volume delta", line=dict(color=BLUE, width=2), row=1, col=1,
                    hovertemplate="%{y:,.0f} " + unit + "<extra>Cumulative volume delta</extra>")
    fig.add_bar(x=x, y=deltas, name="Signed volume", marker_color=[TEAL if value >= 0 else RED for value in deltas],
                row=2, col=1, hovertemplate="%{y:,.0f} " + unit + "<extra>Signed volume</extra>")
    fig.update_yaxes(tickformat="~s", title=unit.title(), row=1, col=1)
    fig.update_yaxes(tickformat="~s", title="Delta", row=2, col=1)
    fig.update_xaxes(tickformat="%H:%M", title="ET", row=2, col=1)
    return style_chart(fig, 300, legend=False)


def money_map_figure(rows, label):
    if not rows:
        return None
    names, parents, keys, values, colors, text = [label], [""], ["map"], [sum(abs(r["net"]) for r in rows)], ["#22314b"], [""]
    for side, select in (("Bullish", lambda r: r["net"] > 0), ("Bearish", lambda r: r["net"] < 0)):
        group = [r for r in rows if select(r)]
        if not group:
            continue
        names.append(side)
        parents.append("map")
        keys.append(side)
        values.append(sum(abs(r["net"]) for r in group))
        colors.append("#244b49" if side == "Bullish" else "#613744")
        text.append(compact(values[-1], currency=True))
        for row in group:
            names.append(row["label"])
            parents.append(side)
            keys.append(f"{side}:{row['label']}")
            values.append(abs(row["net"]))
            multiple = row.get("multiple")
            strength = min(1, max(0, (multiple - 1) / 3)) if multiple is not None else 0
            colors.append(f"rgb({int(35 - 15*strength)},{int(94 + 35*strength)},{int(83 + 18*strength)})"
                          if side == "Bullish" else f"rgb({int(129 + 32*strength)},{int(55 - 8*strength)},{int(67 - 9*strength)})")
            text.append(compact(row["net"], currency=True, signed=True) +
                        (f"<br>{multiple:.1f}× usual premium" if multiple is not None else ""))
    fig = go.Figure(go.Treemap(ids=keys, labels=names, parents=parents, values=values, branchvalues="total", customdata=text,
                              marker=dict(colors=colors, line=dict(width=2, color="#122034")), tiling=dict(pad=2),
                              texttemplate="<b>%{label}</b><br>%{customdata}", hovertemplate="<b>%{label}</b><br>%{customdata}<extra></extra>",
                              textfont=dict(color="#ffffff", size=14)))
    return style_chart(fig, 390, hover="closest", legend=False)


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


def exposure_figure(rows, by, unit, spot=None, flip=None, *, split=False, compact=False, focus_radius_pct=None):
    field = "strike" if by == "strike" else "expiry"
    data = [row for row in rows if row.get(field) is not None and numeric(row.get("net")) is not None]
    if by == "strike":
        data = sorted((row for row in data if numeric(row["strike"]) is not None), key=lambda row: numeric(row["strike"]))
        # Provider exposure can include very distant strikes. They make the
        # chart technically correct but unreadable on a phone. Keep the
        # default view centered on spot while preserving the full values in
        # hover and the underlying feed.
        if spot is not None and len(data) > 12:
            radius = (max(5.0, abs(float(spot)) * focus_radius_pct) if focus_radius_pct is not None
                      else max(100.0, abs(float(spot)) * 0.025))
            focused = [row for row in data if abs(numeric(row["strike"]) - float(spot)) <= radius]
            if len(focused) >= 6:
                data = focused
            elif focused:
                data = sorted(data, key=lambda row: abs(numeric(row["strike"]) - float(spot)))[:max(6, len(focused))]
                data.sort(key=lambda row: numeric(row["strike"]))
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
                              annotation_text=f"{name} {amount(value)}", annotation_font_color=color,
                              annotation_position="top right" if name == "Spot" else "bottom left", annotation_font_size=10)
        magnitudes = [abs(v) for v in y if v is not None]
        if len(magnitudes) >= 8:
            # A single strike can be much larger than the rest. Use a robust
            # default display scale so nearby structure remains visible;
            # hover still reports the unmodified exposure value.
            core = sorted(magnitudes)[:-2]
            robust = float(pd.Series(core).quantile(0.9))
            peak = max(magnitudes)
            if robust > 0 and peak > robust * 3:
                fig.update_yaxes(range=[-robust * 1.35, robust * 1.35])
                fig.add_annotation(x=0.01, y=0.98, xref="paper", yref="paper",
                                   text="Largest bars clipped for readability; hover for exact values",
                                   showarrow=False, xanchor="left", yanchor="top",
                                   font=dict(size=10, color=MUTED), bgcolor="rgba(255,255,255,.88)")
    fig.update_xaxes(title=None if compact else field.title(), nticks=6 if compact or focus_radius_pct else 10)
    fig.update_yaxes(tickformat="~s", title=None if compact else unit)
    styled = style_chart(fig, 176 if compact else 374, legend=split)
    if by == "strike" and not compact:
        styled.update_layout(margin=dict(t=38, l=16, r=16))
    return styled


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
