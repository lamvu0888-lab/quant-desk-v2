"""Read-only movement charts. Close times, scan origins and forecast issuance stay distinct."""
from datetime import timedelta
import sqlite3
from types import SimpleNamespace

import psycopg
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from market_session import ET, rth_bounds
from terminal_data import chart_buckets, numeric, time_or_none
from terminal_style import style_chart
from v3_provenance import display_only, live_record, live_signal

MINUTE = timedelta(minutes=1)
COLORS = {"green": "#48dfad", "red": "#ff818e", "yellow": "#ffd166",
          "blue": "#75bcff", "purple": "#cd9cff", "gray": "#c3d0df"}
SCORES = (("bullish", "Bull pressure", "green"), ("bearish", "Bear pressure", "red"),
          ("readiness", "Readiness", "blue"), ("ignition", "Ignition", "yellow"))
# Each row describes a measured series and its axis. Older observations can lack these fields.
DETAILS = {
    "Netflow changes": (("net_change", "Netflow velocity", "$ / min change"),
                        ("net_acceleration", "Netflow acceleration", "$ / min² change")),
    "CVD changes": (("footprint_change", "CVD delta velocity", "Delta change / min"),
                    ("footprint_acceleration", "CVD delta acceleration", "Delta change / min²")),
    "Volatility": (("realized_volatility", "Realized volatility", "RMS 1m move (%)"),
                   ("range_ratio", "Recent range / baseline", "Range multiple")),
    "Momentum and volume": (("velocity", "Price velocity", "Robust scale units"),
                            ("relative_volume", "Relative volume", "Volume multiple")),
    "Dealer positioning": (("gex", "Modeled net GEX", "Provider GEX"),),
    "Cross-market context": (("peer_return", "Futures move", "Return (%)"),),
}


def _et(dt):
    """Use explicit local wall-clock labels so a user's browser timezone cannot shift the axis."""
    return dt.astimezone(ET).replace(tzinfo=None)


def _in_session(ctx, stamp, bounds):
    return stamp is not None and bounds[0] < stamp <= min(ctx.now, bounds[1])


def scan_history(ctx, plan, records):
    sig = plan.get("movement_v3") or {}
    bounds = rth_bounds(ctx.session)
    scans = {}
    if not sig or not bounds:
        return scans
    synthetic = ctx.preview or not live_signal(sig)
    for row in (() if synthetic else records):
        payload = row.get("payload") or {}
        saved = payload.get("signal") or {}
        at = time_or_none(saved.get("at"))
        processed = time_or_none(row.get("origin"))
        if (not live_record(row) or payload.get("scope") != ctx.scope
                or saved.get("symbol") != ctx.ticker or saved.get("session") != str(ctx.session)
                or any(saved.get(k) != sig.get(k) for k in ("source", "version", "mode"))
                or not _in_session(ctx, at, bounds) or not processed or not at <= processed <= ctx.now):
            continue
        scans.setdefault(at, payload)
    at = time_or_none(sig.get("at"))
    if (_in_session(ctx, at, bounds) and numeric(sig.get("price")) and (not ctx.preview or sig.get("preview") is True)
            and sig.get("session") == str(ctx.session) and sig.get("symbol") == ctx.ticker):
        scans.setdefault(at, {"signal": sig, "decision": plan.get("status")})
    return dict(sorted(scans.items()))


def price_bars(ctx, read, scans, minutes):
    """Re-use the dashboard's complete, session-anchored bar aggregation."""
    bounds = rth_bounds(ctx.session)
    if not bounds or minutes not in (1, 3, 5):
        return []
    candles = {}
    raw = read.get("candles", ()) if read.get("bars_usable", read.get("bars_ready", False)) else ()
    for b in raw:
        start = time_or_none(b.get("time_utc"))
        if start and _in_session(ctx, start + MINUTE, bounds) and b.get("closed", True) and not b.get("forming"):
            candles[start] = b
    for end, scan in scans.items():
        bar = scan.get("ohlcv")
        if bar and all(numeric(bar.get(k)) is not None for k in ("open", "high", "low", "close")):
            start = end - MINUTE
            candles[start] = {**bar, "time_utc": start.isoformat()}
    return chart_buckets([candles[t] for t in sorted(candles)], minutes)


def _axis_points(stamps, step):
    if not stamps:
        return []
    count = (stamps[-1] - stamps[0]) // step + 1
    return [stamps[0] + i * step for i in range(count)]


def _detail_value(sig, key):
    if key == "peer_return":
        return numeric((sig.get("cross_market") or {}).get("change_pct"))
    value = numeric((sig.get("features") or {}).get(key))
    if value is not None and key == "realized_volatility":
        return value * 100
    return value


def _mark_style(state):
    if state in ("EXHAUSTION", "FAILED MOVE", "INVALIDATED", "CONFLICTING PRESSURE"):
        return "x", COLORS["yellow"]
    if "BULLISH" in state:
        return "triangle-up", COLORS["green"]
    if "BEARISH" in state:
        return "triangle-down", COLORS["red"]
    return "diamond-open", COLORS["gray"]


def _events(fig, scans):
    transitions, entries = [], []
    prev_state = prev_entry = prev_at = None
    for at, payload in scans.items():
        sig = payload["signal"]
        price = numeric(sig.get("price"))
        state = sig.get("state")
        time = time_or_none(sig.get("processed"))
        if price is None or not state or time is None:
            continue
        fresh = prev_at is None or at - prev_at > MINUTE
        if state != prev_state or fresh:
            transitions.append((_et(time), price, ("First reading · " if fresh else "") + state, *_mark_style(state)))
        decision = payload.get("decision")
        if decision in ("ENTER LONG", "ENTER SHORT") and (decision != prev_entry or fresh):
            entries.append((_et(time), price, decision))
        prev_state, prev_entry, prev_at = state, decision, at
    if transitions:
        x, y, labels, symbols, colors = zip(*transitions)
        fig.add_trace(go.Scatter(x=x, y=y, text=labels, name="Movement state", mode="markers",
                                 marker=dict(symbol=symbols, color=colors, size=8),
                                 hovertemplate="%{text}<br>%{x|%H:%M:%S} ET · close %{y:,.2f}<extra></extra>"), row=1, col=1)
    if entries:
        x, y, labels = zip(*entries)
        fig.add_trace(go.Scatter(x=x, y=y, text=labels, name="Qualified plan", mode="markers",
                                 marker=dict(symbol="star", color=COLORS["purple"], size=12),
                                 hovertemplate="%{text}<br>%{x|%H:%M:%S} ET · close %{y:,.2f}<extra></extra>"), row=1, col=1)


def lifecycle_extras(ctx, sig):
    """Read recorded invalidations and provisional quote impulses that can occur between closed bars."""
    if ctx.preview or not live_signal(sig) or not ctx.journal:
        return []
    result = []
    bounds = rth_bounds(ctx.session)
    if not bounds:
        return []
    for kind, scope, state in (("v3_transition", f"{ctx.session}|{ctx.scope}", "INVALIDATED"),
                               ("v3_quote", f"{ctx.session}|{sig['source']}", "PROVISIONAL IMPULSE")):
        for record in ctx.journal.records(ctx.ticker, kind, scope=scope, limit=480):
            p = record["payload"]
            at = time_or_none(p.get("processed"))
            price = numeric(p.get("price"))
            if (display_only(record) or display_only(p) or p.get("state") != state or p.get("version") != sig["version"] or p.get("source") != sig["source"]
                    or price is None or not _in_session(ctx, at, bounds)):
                continue
            if kind == "v3_transition" and (not live_signal(p) or p.get("mode") != sig["mode"]):
                continue
            result.append((_et(at), price, state))
    return result


def _zones(fig, ctx, sig):
    at = time_or_none(sig.get("processed"))
    ref = time_or_none(sig.get("at"))
    bounds = rth_bounds(ctx.session)
    if not at or not ref or not bounds or not ref <= at <= ctx.now < ref + MINUTE:
        return
    for h in (5, 15):
        barrier = (sig.get("forecasts") or {}).get(str(h), {})
        low, high = numeric(barrier.get("lower")), numeric(barrier.get("upper"))
        end = ref + h * MINUTE
        if low is None or high is None or end > bounds[1] or end <= at:
            continue
        x = [_et(at), _et(end)]
        suffix = f"{h}m barrier"
        for y, name, color in ((high, "Upper ", "green"), (low, "Lower ", "red")):
            fig.add_trace(go.Scatter(x=x, y=[y, y], name=name + suffix, mode="lines", showlegend=False,
                                     line=dict(dash="dot", color=COLORS[color], width=1.3),
                                     hovertemplate=name + suffix + " %{y:,.2f}<extra></extra>"), row=1, col=1)


def movement_figure(ctx, plan, read, scans, minutes=1, detail=None, extras=()):
    bars = price_bars(ctx, read, scans, minutes)
    step = minutes * MINUTE
    stamps = [time_or_none(b["time_utc"]) + step for b in bars]
    points = _axis_points(stamps, step)
    if not points:
        return None
    # At a 3m or 5m chart close sample the actual one-minute engine's last observation.
    # Missing closes stay empty. Lifecycle changes retain their actual observation time.
    valid = set(stamps)
    samples = [scans.get(at, {}).get("signal", {}) if at in valid else {} for at in points]
    values = DETAILS.get(detail, ())
    active = [(key, name, unit) for key, name, unit in values if any(_detail_value(s, key) is not None for s in samples)]
    rows = 3 if active else 2
    titles = ["Price and movement", "Pressure and readiness"] + ([detail] if active else [])
    specs = [[{}], [{}]] + ([[{"secondary_y": len(active) == 2}]] if active else [])
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=.09,
                        specs=specs, subplot_titles=titles,
                        row_heights=[.51, .28, .21] if active else [.62, .38])
    fig.add_trace(go.Candlestick(x=[_et(t) for t in stamps], name=f"{minutes}m price", showlegend=False,
                                 open=[b["open"] for b in bars], high=[b["high"] for b in bars],
                                 low=[b["low"] for b in bars], close=[b["close"] for b in bars],
                                 increasing_line_color=COLORS["green"], decreasing_line_color=COLORS["red"]), row=1, col=1)
    _events(fig, scans)
    if extras:
        x, y, labels = zip(*extras)
        fig.add_trace(go.Scatter(x=x, y=y, text=labels, mode="markers", name="Additional warnings",
                                 marker=dict(symbol="diamond", color=COLORS["yellow"], size=8),
                                 hovertemplate="%{text} · %{x|%H:%M:%S} ET · %{y:,.2f}<extra></extra>"), row=1, col=1)
    _zones(fig, ctx, plan["movement_v3"])
    axis = [_et(t) for t in points]
    for key, name, color in SCORES:
        y = [numeric((s.get("scores") or {}).get(key)) for s in samples]
        if any(v is not None for v in y):
            fig.add_trace(go.Scatter(x=axis, y=y, name=name, mode="lines+markers", marker_size=3, line_color=COLORS[color],
                                     connectgaps=False, hovertemplate=name + " %{y:.0f}/100<extra></extra>"), row=2, col=1)
    fig.update_yaxes(range=[-2, 102], row=2, col=1)
    for i, (key, name, unit) in enumerate(active):
        if key.startswith("footprint"):
            bases = {s.get("features", {}).get("footprint_units") for s in samples if _detail_value(s, key) is not None}
            if len(bases) == 1 and None not in bases:
                unit = unit.replace("Delta", next(iter(bases)))
        context = [s.get("cross_market") or {} for s in samples] if key == "peer_return" else []
        y = [_detail_value(s, key) for s in samples]
        # A futures roll or a switch between ES and MES breaks the observation sequence.
        if key.startswith("footprint") or key == "peer_return":
            source_keys = [p.get("symbol") for p in context] if context else [(s.get("quality") or {}).get("cvd") for s in samples]
            for j in range(1, len(y)):
                if source_keys[j] != source_keys[j-1] and y[j-1] is not None:
                    y[j] = None
        fig.add_trace(go.Scatter(x=axis, y=y, name=name, mode="lines+markers", marker_size=3,
                                 line_color=COLORS["purple" if i else "blue"], connectgaps=False,
                                 customdata=[c.get("symbol") for c in context] if context else None,
                                 hovertemplate=("%{customdata} " if context else "") + name + " %{y:.3g} " + unit + "<extra></extra>"),
                      row=3, col=1, secondary_y=bool(i))
        fig.update_yaxes(title_text=unit, row=3, col=1, secondary_y=bool(i))
    style_chart(fig, 690 if active else 485, revision=f"v3-{ctx.ticker}-{ctx.session}-{minutes}-{detail}")
    fig.update_layout(margin=dict(l=6, r=10, t=28, b=78), legend=dict(y=-.12, font=dict(size=10)),
                      hovermode="x unified", hoversubplots="axis")
    # Assign all panels to the same date axis to synchronize crosshair and zoom in Plotly.
    for trace in fig.data:
        trace.xaxis = "x"
    fig.update_xaxes(matches=None, rangeslider_visible=False, showspikes=True, spikemode="across", tickformat="%H:%M",
                     hoverformat="%H:%M:%S ET", spikedash="dot")
    fig.update_xaxes(showticklabels=True, anchor=f"y{rows}", title_text="Eastern time", row=1, col=1)
    fig.update_xaxes(showticklabels=False, row=rows, col=1)
    return fig


@st.cache_data(max_entries=6, show_spinner=False)
def preview_history(candles, symbol, session, expiry, source, mode, version):
    """Transient price-only replay of deterministic demo candles. No journal or provider access."""
    from tradytics_feed import Feed
    from v3_movement import signal
    ctx = SimpleNamespace(preview=True, paused=False, ticker=symbol, session=session,
                          price_provider=source, expiry=expiry)
    snapshots = {}
    for i in range(max(6, len(candles) - 60), len(candles)):
        close = time_or_none(candles[i]["time_utc"]) + MINUTE
        ctx.now = close
        read = {"bars_ready": True, "minute_close": close, "candles": candles[max(0, i-34):i+1]}
        result = signal(ctx, read, {}, Feed(), [], "unavailable", {})
        if result.get("price") and result["mode"] == mode and result["version"] == version:
            snapshots[close] = {"signal": result}
    return snapshots


def render_movement_charts(ctx, plan, read=None, *, records=None):
    sig = plan.get("movement_v3")
    if not sig:
        return
    read = read or {}
    synthetic = ctx.preview or not live_signal(sig)
    if records is None:
        records = ctx.journal.records(ctx.ticker, "v3_scan", scope=str(ctx.session), limit=450) if ctx.journal and not synthetic else []
    scans = scan_history(ctx, plan, records)
    if ctx.preview and sig.get("preview") and read.get("bars_ready") and sig.get("at"):
        at = time_or_none(sig["at"])
        bars = [b for b in read.get("candles", [])[-100:] if time_or_none(b.get("time_utc"))
                and time_or_none(b["time_utc"]) + MINUTE < at and not b.get("forming") and b.get("closed", True)]
        scans = dict(sorted({**preview_history(bars,ctx.ticker,ctx.session,ctx.expiry,sig["source"],sig["mode"],sig["version"]),
                             **scans}.items()))
    st.subheader("Movement on the chart")
    minutes = st.segmented_control("Candle interval", [1, 3, 5], default=1, format_func=lambda value: f"{value}m",
                                   key="movement_plot_interval") or 1
    with st.expander("More chart indicators"):
        detail = st.selectbox("Add an indicator panel", ["None", *DETAILS], key="movement_plot_detail")
        if detail in DETAILS and not any(_detail_value(row["signal"], key) is not None for row in scans.values()
                                          for key, _, _ in DETAILS[detail]):
            st.caption(f"{detail} is unavailable for these observations.")
    try:
        extras = lifecycle_extras(ctx, sig)
    except (OSError, sqlite3.Error, psycopg.Error):
        extras = []
        st.caption("Additional worker warnings are unavailable.")
    fig = movement_figure(ctx, plan, read, scans, minutes, detail, extras)
    if fig is None:
        st.info(f"Waiting for a complete {minutes}-minute regular-session candle.")
        return
    st.plotly_chart(fig, width="stretch", theme=None, key="movement_command_history",
                    config={"displaylogo": False, "scrollZoom": False})
    source = "Synthetic preview" if synthetic else str(sig.get("source") or "Source unavailable")
    st.caption(f"{source} · Markers identify observed states and qualified plans at scan time. Dotted levels are conditional 5m and 15m volatility barriers.")
    if ctx.preview and len(scans) > 1:
        st.caption("Illustrative history replays demo prices only. The latest point uses the current demo inputs.")
    elif len(scans) < 2:
        st.caption("Historical scores appear after the live worker records successive readings for this ticker and expiry.")
