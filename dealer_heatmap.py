"""Read-only strike/expiry exposure charts built from the desk's scoped feeds."""
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from math import sqrt

import plotly.graph_objects as go
import streamlit as st

from dealer_hedging import UNITS
from market_session import ET, session_state
from terminal_data import (GREEK_UNITS, et_time, expiration_date, feed_has_delay, first_listed_dte,
                           listed_dtes, numeric, positive, source_stamp)
from terminal_style import compact, plot, style_chart


@dataclass
class Column:
    values: dict
    issue: str = ""
    stamp: datetime | None = None
    spot: float | None = None


def read_column(ctx, greek, expiry, feed):
    if ctx.paused or feed.error or not feed.snapshot:
        return Column({}, "Paused" if ctx.paused else str(feed.error.status) if feed.error else "Waiting for data")
    obj, meta = feed.obj, feed.snapshot.meta
    stamp = source_stamp("exposure", feed, ctx.ticker)
    if feed_has_delay(feed) or (numeric(obj.get("delay_minutes")) or 0) > 0 or obj.get("stale") or meta.get("stale"):
        return Column({}, "Delayed or stale")
    if (not isinstance(stamp, datetime) or stamp.astimezone(ET).date() != ctx.session or stamp > ctx.now
            or (session_state(ctx.now)["status"] == "OPEN" and (ctx.now - stamp).total_seconds() > 420)):
        return Column({}, "Missing or stale observation time")
    if obj.get("unit") != UNITS[greek]:
        return Column({}, "Missing or unexpected exposure unit")
    def scope_matches(record):
        return (record.get("symbol") in (None, ctx.ticker) and record.get("greek") in (None, greek)
                and record.get("expiry") in (None, expiry) and record.get("by") in (None, "strike"))
    if not all(scope_matches(record) for record in (obj, meta)):
        return Column({}, "Exposure scope mismatch")
    values = {}
    seen = set()
    for row in feed.rows:
        if not scope_matches(row):
            return Column({}, "Strike scope mismatch")
        strike, value = positive(row.get("strike")), numeric(row.get("net"))
        if strike is None:
            continue
        if strike in seen:
            return Column({}, "Duplicate strike in response")
        seen.add(strike)
        if value is not None:
            values[strike] = value
    return Column(values, "" if values else "No signed strike readings", stamp, positive(obj.get("spot")))


def spot_reference(ctx, column):
    quote = ctx.chart_quote
    if quote:
        return quote["price"], f'{quote["source"]} quote · {et_time(quote["at"])}'
    if column.values and column.spot:
        return column.spot, f'Exposure snapshot · {et_time(column.stamp)}'
    return None, "Spot unavailable"


def nearby_levels(values, spot):
    """Largest absolute reading on either side within 1% of spot. Ties favor proximity."""
    if not spot:
        return []
    levels = []
    for side in (-1, 1):
        candidates = [strike for strike, net in values.items()
                      if net and 0 < side * (strike - spot) <= spot * .01]
        if candidates:
            strike = max(candidates, key=lambda k: (abs(values[k]), -abs(k - spot)))
            levels.append((strike, values[strike]))
    return levels


def level_name(net, greek="gamma"):
    if greek == "gamma":
        return "Brake" if net > 0 else "Amplifier"
    return "+Vanna" if net > 0 else "−Vanna"


def gamma_read(levels, spot):
    if not levels:
        return "No nonzero gamma readings within 1% of spot in this scope."
    phrases = []
    for strike, net in levels:
        side = "below" if strike < spot else "above"
        phrases.append(f"a {level_name(net).lower()} at **{strike:,.2f}** {side}")
    if len(levels) == 1:
        return f"Spot has {phrases[0]}. The other side has no nonzero reading within 1%."
    if all(net > 0 for _, net in levels):
        context = "Hedging near both levels could favor a choppy range."
    elif all(net < 0 for _, net in levels):
        context = "A move toward either level could pick up speed."
    else:
        context = "Hedging could slow a move near the brake and reinforce one near the amplifier."
    return f"Spot sits between {phrases[0]} and {phrases[1]}. {context}"


def side_total(values, spot, above):
    if spot is None:
        return None
    side = [v for k, v in values.items() if (k > spot if above else k < spot)]
    return sum(side) if side else None


def heatmap_figure(columns, selected, spot, greek):
    strikes = sorted(set().union(*(column.values for column in columns.values())))
    if not strikes or spot is None:
        return None
    strikes = sorted(sorted(strikes, key=lambda k: (abs(k - spot), k))[:17])
    labels = list(columns)
    raw = [[columns[label].values.get(k) for label in labels] for k in strikes]
    scale = max((abs(v) for row in raw for v in row if v is not None), default=0) or 1
    colors = [[None if v is None else (1 if v >= 0 else -1) * sqrt(abs(v) / scale)
               for v in row] for row in raw]
    texts = [[compact(v, signed=True) if v is not None else "" for v in row] for row in raw]
    fig = go.Figure(go.Heatmap(
        x=list(range(len(labels))), y=list(range(len(strikes))), z=colors, customdata=[
            [[labels[j], k, v] for j, v in enumerate(row)] for k, row in zip(strikes, raw)],
        text=texts, texttemplate="%{text}", textfont=dict(size=11, color="#152136"), zmin=-1, zmax=1,
        colorscale=[[0, "#b795e1"], [.5, "#f4f4f5"], [1, "#64c3a1"]], showscale=False,
        xgap=2, ygap=2, hoverongaps=False,
        hovertemplate="%{customdata[0]} · strike %{customdata[1]:,.2f}<br>Net $%{customdata[2]:,.2f}<extra></extra>"))
    for k, net in nearby_levels(columns[selected].values if selected else {}, spot):
        if k in strikes:
            y, x = strikes.index(k), labels.index(selected)
            fig.add_shape(type="rect", x0=x-.47, x1=x+.47, y0=y-.46, y1=y+.46,
                          line=dict(color="#145b49" if net > 0 else "#614594", width=3))
            fig.add_annotation(x=1, xref="paper", xshift=4, xanchor="left", y=y,
                               text=level_name(net, greek), showarrow=False)
    # Ordinal rows give even spacing on irregular strike grids. Interpolate only the spot marker.
    i = bisect_left(strikes, spot)
    if i < len(strikes) and strikes[i] == spot:
        pos = i
    elif i == 0 or i == len(strikes):
        pos = -.5 if i == 0 else len(strikes)-.5
    else:
        pos = i-1 + (spot-strikes[i-1]) / (strikes[i]-strikes[i-1])
    spot_text = f"Spot {spot:,.2f}"
    if spot < strikes[0] or spot > strikes[-1]:
        spot_text += " (off grid)"
    fig.add_annotation(x=0, xref="paper", xshift=-45, y=pos, text="▶", xanchor="right", showarrow=False,
                       bgcolor="#183551", font=dict(color="white", size=10), hovertext=spot_text)
    fig.update_xaxes(tickmode="array", tickvals=list(range(len(labels))), ticktext=[s if s == "All" else datetime.fromisoformat(s).strftime("%b %d") for s in labels],
                     side="top", fixedrange=True, range=[-.5, len(labels)-.5])
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(strikes))),
                     ticktext=[f"{k:g}" for k in strikes], range=[-.65, len(strikes)-.35], fixedrange=True)
    style_chart(fig, max(235, 29*len(strikes)+92), hover="closest", legend=False)
    fig.update_layout(margin=dict(l=67, r=68, t=48, b=12))
    return fig


def heatmap_view(ctx):
    st.subheader("Dealer exposure by strike")
    kind = st.segmented_control("Exposure measure", ["Gamma (GEX)", "Vanna (VEX)"],
                                default="Gamma (GEX)", key="dealer_map_measure") or "Gamma (GEX)"
    greek = "gamma" if kind.startswith("Gamma") else "vanna"
    expirations = ctx.get("expirations", symbol=ctx.ticker)
    dates = []
    if first_listed_dte(expirations, ctx.ticker, ctx.session, ctx.now) is not None:
        dates = [expiration_date(ctx.session, d) for d in listed_dtes(expirations, ctx.session)[:3]]
    if ctx.expiry and ctx.expiry not in dates:
        dates = dates[:2] + [ctx.expiry]
    columns = {}
    for expiry in [None, *dates]:
        feed = ctx.get("exposure", symbol=ctx.ticker, greek=greek, by="strike", expiry=expiry)
        columns[expiry or "All"] = read_column(ctx, greek, expiry, feed)
    selected = ctx.expiry or "All"
    active = columns[selected]
    spot, basis = spot_reference(ctx, active if active.values else columns["All"])
    provider = "Illustrative preview" if ctx.preview else "Tradytics"
    st.caption(f"{ctx.ticker} · {provider} · {ctx.session} · {GREEK_UNITS[UNITS[greek]]}. "
               "All covers the provider’s full expiry scope. Blank cells have no usable reading.")
    if ctx.expiry_waiting:
        st.info("Waiting for a listed expiration for the chart indicators.")
    if spot is not None:
        st.markdown(f"**▶ Spot {spot:,.2f}** · {basis}")
    fig = heatmap_figure(columns, None if ctx.expiry_waiting else selected, spot, greek)
    if fig is None:
        st.info("Waiting for signed exposure by strike and a usable reference price.")
    else:
        if greek == "gamma" and not ctx.expiry_waiting and active.values:
            st.markdown(gamma_read(nearby_levels(active.values, spot), spot))
        plot(fig, "dealer_exposure_matrix")
        if greek == "gamma":
            st.caption("Green (+) · Brake: modeled dealer hedging may dampen a move. "
                       "Purple (−) · Amplifier: hedging may extend a move in either direction.")
        else:
            st.caption("Green (+) and purple (−) show the sign of modeled vanna. "
                       "Vanna measures how the book’s delta exposure changes as implied volatility changes.")
        if not ctx.expiry_waiting:
            levels = nearby_levels(active.values, spot)
            if levels:
                st.markdown(" · ".join(f"**{level_name(v, greek)} {k:,.2f}** "
                                       f"({compact(v, currency=True, signed=True)})" for k, v in levels))
        st.caption(f"Indicators use {selected} and pick the largest absolute exposure within 1% on each side of spot. "
                   "The grid shows up to 17 nearest strikes. Color intensity uses a shared square-root scale.")
    st.dataframe([{"Scope": scope, "Observed (ET)": et_time(col.stamp) if col.stamp else "—",
                   "Net above spot": compact(side_total(col.values, spot, True), signed=True, currency=True),
                   "Net below spot": compact(side_total(col.values, spot, False), signed=True, currency=True),
                   "Coverage": col.issue or f"{len(col.values)} strikes"}
                  for scope, col in columns.items()], hide_index=True, width="stretch")
    st.caption("Totals include every reported strike strictly above or below spot. A dash means no observations on that side.")
