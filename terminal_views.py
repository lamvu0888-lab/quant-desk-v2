"""Terminal views. Fetch only the feeds needed by the page on screen."""
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field as default_field
from datetime import date, datetime, timedelta
from functools import cached_property

import pandas as pd
import streamlit as st

from data_utils import DataError, field, utc
from desk_analytics import (FIVE_MINUTES, MINUTE, candles_on, flow_read, mark_orders, order_rows, price_levels,
                            realized_regime, replay_setups, session_window, setup_read, share_cvd, signed_moves)
from market_session import ET, active_session, rth_bounds, session_state
from monte_carlo import forecast, walk_forward
from terminal_charts import (bubble_figure, cvd_figure, dark_figure, exposure_figure, flow_desk_figure, flow_figure,
                             forecast_figure, money_map_figure, price_figure, series_figure)
from terminal_data import (INDICES, chart_buckets, display_exposure_unit, et_time, expiration_date, exposure_obj,
                           feed_has_delay, filter_expiration, interval_length, numeric, positive, price_bars, quote_row,
                           same_ticker, scope_timeline, session_rows, snapshot_forecast_gate, source_stamp, status,
                           ticker_symbol, time_or_none)
from terminal_style import (AMBER, BLUE, RED, TEAL, VIOLET, amount, compact, empty, esc, feed_stamp, kpis,
                            panel_header, plot, ratio_pct, signed_pct)
from tradytics_feed import ENDPOINTS, Feed, FeedError
from tradingview_levels import (PINE_COLORS, dark_levels, dealer_levels, expected_levels, export_payload, pine_script)

LEVELS = (("put_wall", "Put wall", "down", RED), ("flip", "Gamma flip", "gold", AMBER),
          ("call_wall", "Call wall", "up", TEAL), ("max_pain", "Max pain", "purple", VIOLET))
SIGNAL_PATTERNS = ("repeat-flow", "opening-calls", "heavy-calls", "etf-repeat-calls", "itm-puts", "burst-calls", "index-tide-calls")


@dataclass
class Desk:
    ticker: str
    now: datetime
    store: object
    preview: bool
    paused: bool
    dte: int | None
    configured: bool
    price_provider: str = "Tradytics"
    webull: dict = default_field(default_factory=dict)
    webull_quality: dict = default_field(default_factory=dict)
    webull_error: str | None = None
    config_error: str | None = None

    def get(self, name, **params):
        params = {key: value for key, value in params.items() if value is not None}
        if self.preview:
            return self.store.get(name, **params)
        if self.store:
            return self.store.get(name, paused=self.paused, **params)
        return Feed(error=FeedError("CONFIG", self.config_error or "Tradytics setup is required."))

    @property
    def day(self):
        return self.now.astimezone(ET).date()

    @cached_property
    def session(self):
        return active_session(self.now)

    @property
    def expiry(self):
        return expiration_date(self.session, self.dte)

    @property
    def scope(self):
        return f"{self.dte} DTE · {self.expiry}" if self.expiry else "All DTE"

    @property
    def quote_endpoint(self):
        return "indices" if self.ticker in INDICES else "quotes"

    def quote_feed(self):
        return self.get("indices") if self.ticker in INDICES else self.get("quotes", symbol=self.ticker, limit=10)

    def quote(self):
        if self.price_provider == "Webull":
            q = self.webull.get("quote") or {}
            return {"price": self.webull_quality.get("price"), "name": self.ticker,
                    "previous_close": q.get("pre_close"), "price_at": self.webull_quality.get("price_time_utc")}
        return quote_row(self.quote_feed(), self.ticker)

    def history(self, daily=False, *, session=None, symbol=None):
        if self.price_provider == "Webull" and not daily and session is None and symbol is None:
            return (self.webull.get("history") or {}).get("candles", []), Feed()
        ticker = symbol or self.ticker
        result = self.get("bars", symbol=ticker, timeframe="1d" if daily else "1m", limit=180 if daily else 5000,
                          to=str(session) if session else None,
                          **{"from": str((session or self.day) - timedelta(days=280 if daily else 8))})
        return price_bars(result, ticker, self.now, daily=daily), result

    def exposure(self, greek="gamma", by="strike"):
        return self.get("exposure", symbol=self.ticker, greek=greek, by=by, expiry=self.expiry)

    def timeline(self, *, interval="5m", session=None, market=False, symbol=None):
        return scope_timeline(self.get, self.now, self.dte, symbol=None if market else symbol or self.ticker,
                              interval=interval, session=session)

    def tape(self, *, session=None, limit=250, **filters):
        day = session or self.session
        feed = self.get("flow", symbol=self.ticker, date=str(session) if session else None,
                        expiry=expiration_date(day, self.dte), limit=limit, **filters)
        rows = filter_expiration(feed.rows, day, self.dte)
        return order_rows(rows, self.ticker, self.now, session=day), feed

    def title(self, filename):
        return ("demo_" if self.preview else "") + self.ticker + "_" + filename


def tone(value):
    value = numeric(value)
    return "" if value is None or value == 0 else "up" if value > 0 else "down"


def metric(label, value, subtitle="", direction=None):
    return label, value, subtitle, tone(direction)


def table(rows, *, height=285, config=None):
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=height, column_config=config)


def csv_export(rows, name, key):
    if not rows:
        return
    safe = [{k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
             for k, v in row.items()} for row in rows]
    st.download_button("Download CSV", pd.DataFrame(safe).to_csv(index=False), file_name=name, mime="text/csv", key=key)


def option_levels(gamma):
    levels = gamma.get("levels")
    return levels if isinstance(levels, dict) else {}


def level_list(gamma, spot=None):
    levels = option_levels(gamma)
    for key, label, color, _ in LEVELS:
        price = positive(levels.get(key))
        distance = f" · {(price / spot - 1) * 100:+.2f}%" if price is not None and spot else ""
        st.markdown(f'<div class="qd-level"><span><i class="qd-dot {color}"></i>{label}</span>'
                    f'<strong>{amount(price)}<small>{esc(distance)}</small></strong></div>', unsafe_allow_html=True)


def flow_rows(ctx, feed, market=False):
    return session_rows(feed.rows if market else same_ticker(feed.rows, ctx.ticker))


def net_sum(rows, field_name):
    values = [n for row in rows if (n := numeric(row.get(field_name))) is not None]
    return sum(values) if values else None


def render_chart(fig, key, feed=None, message="No observations returned for these filters."):
    if fig is not None:
        plot(fig, key)
    else:
        empty(feed, message)


def default_premium(ticker):
    if ticker in INDICES:
        return 500_000
    if ticker in {"SPY", "QQQ", "IWM", "DIA"}:
        return 250_000
    return 100_000


def read_workspace(ctx, *, include_flow=True):
    candles, bars = ctx.history()
    buckets = chart_buckets(candles, 5)
    gamma_feed = ctx.exposure()
    timeline = ctx.timeline() if include_flow else Feed()
    vol_feed = ctx.get("volatility", symbol=ctx.ticker)
    gamma = exposure_obj(gamma_feed, ctx.ticker, "gamma")
    day_bars = candles_on(buckets, ctx.session)
    regime = realized_regime(buckets)
    price_stamp = utc(day_bars[-1]["time_utc"]) + FIVE_MINUTES if day_bars else None
    flow_stamp = utc(timeline.rows[-1]["time"]) + FIVE_MINUTES if timeline.rows else None
    gamma_stamp = source_stamp("exposure", gamma_feed, ctx.ticker)
    recent_gamma = isinstance(gamma_stamp, datetime) and 0 <= (ctx.now - gamma_stamp).total_seconds() <= 420
    gex = numeric(gamma.get("total")) if recent_gamma and not gamma_feed.error and not feed_has_delay(gamma_feed) else None
    reasons = []
    if session_state(ctx.now)["status"] != "OPEN":
        reasons.append("Regular session is closed.")
    if ctx.paused or bars.error or timeline.error or feed_has_delay(bars) or feed_has_delay(timeline):
        reasons.append("Waiting for the price and flow feeds.")
    if ctx.price_provider == "Webull" and not ctx.webull_quality.get("forecast_ready"):
        reasons.append("Waiting for Webull's verified price and candles.")
    if price_stamp is None or price_stamp != flow_stamp or (ctx.now - price_stamp) > timedelta(minutes=7):
        reasons.append("Waiting for price and options flow from the same completed 5-minute bar.")
    if not flow_read(timeline.rows) or not regime.get("atr"):
        reasons.append("Need 30 minutes of session flow and enough recent candles.")
    levels = price_levels(buckets, ctx.session)
    plan = setup_read(buckets, timeline.rows, ctx.session, regime, levels, gex=gex, ready=not reasons)
    return {"candles": candles, "bars": bars, "buckets": buckets, "gamma_feed": gamma_feed,
            "gamma": gamma, "flow": timeline, "vol": vol_feed, "price_stamp": price_stamp,
            "regime": regime, "plan": plan, "levels": levels, "reasons": reasons}


def level_inputs(ctx, read, *, strikes=0, dark_count=3, show_expected=False):
    """Keep a late or missing provider snapshot out of today's chart layers."""
    spot = positive(ctx.quote().get("price")) or (read["buckets"][-1]["close"] if read["buckets"] else None)
    gamma_feed = read["gamma_feed"]
    gamma_stamp = source_stamp("exposure", gamma_feed, ctx.ticker)
    in_session = session_state(ctx.now)["status"] == "OPEN"
    gamma_current = (isinstance(gamma_stamp, datetime) and gamma_stamp.astimezone(ET).date() == ctx.session
                     and gamma_stamp <= ctx.now and (not in_session or (ctx.now - gamma_stamp).total_seconds() <= 420))
    gamma = read["gamma"] if gamma_current and not gamma_feed.error and not feed_has_delay(gamma_feed) else {}
    dark = ctx.get("dark_levels", symbol=ctx.ticker, window=5, limit=30) if ctx.ticker not in INDICES and dark_count else Feed()
    dark_day = source_stamp("dark_levels", dark, ctx.ticker)
    dark_ok = dark.snapshot and not dark.error and (dark_day is None or dark_day == ctx.session)
    dark_rows = dark_levels(same_ticker(dark.rows, ctx.ticker), dark.snapshot.received_at.isoformat(), dark_count) if dark_ok else []
    vol_feed = read["vol"]
    vol_day = source_stamp("volatility", vol_feed, ctx.ticker)
    expected = expected_levels(vol_feed.obj, spot) if show_expected and vol_day == ctx.session and not vol_feed.error else []
    return read["levels"] + dealer_levels(gamma, spot, strikes) + dark_rows + expected, dark


def nearest_chart_levels(levels, rows, show_all):
    if not rows:
        return []
    visible = rows[-105:]
    price = visible[-1]["close"]
    low, high = min(b["low"] for b in visible), max(b["high"] for b in visible)
    pad = max((high - low) * .25, price * .0005)
    candidates = sorted((level for level in levels if low - pad <= level.price <= high + pad),
                        key=lambda level: (abs(level.price - price), level.label))
    selected = []
    for level in candidates:
        if any(abs(other.price - level.price) < price * .00015 for other in selected):
            continue
        selected.append(level)
        if len(selected) == (12 if show_all else 3):
            break
    return [(level.label, level.price, PINE_COLORS[level.kind]) for level in selected]


def price_panel(ctx, read):
    with st.container(border=True):
        panel_header("Price & large orders", "price", "overview_price", "Completed candles · New York time")
        left, right = st.columns([3, 1], vertical_alignment="center")
        timeframe = left.segmented_control("Candle timeframe", ["1m", "3m", "5m", "15m", "1h", "1D"], default="5m",
                                           key="overview_timeframe", label_visibility="collapsed") or "5m"
        daily = timeframe == "1D"
        overlays = right.toggle("Levels & VWAP", value=True, key="overview_overlays", disabled=daily)
        with st.expander("Price chart filters"):
            a, b, c = st.columns(3)
            show_orders = a.toggle("Large order markers", value=True, key="price_show_orders", disabled=daily)
            premium = b.number_input("Minimum premium ($)", min_value=25_000, max_value=100_000_000,
                                     value=default_premium(ctx.ticker), step=25_000, key=f"price_min_{ctx.ticker}", disabled=daily)
            option_type = c.selectbox("Show contracts", ["Calls & puts", "Calls", "Puts"], key="price_order_type", disabled=daily)
            a, b = st.columns(2)
            labels = a.toggle("Show strike and premium labels", key="price_order_labels", disabled=daily)
            more_levels = b.toggle("Show more levels", key="price_more_levels", disabled=daily)
        candles, feed = ctx.history(daily=True) if daily else (read["candles"], read["bars"])
        rows = candles if daily else chart_buckets(candles, {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "1h": 60}[timeframe])
        levels, _ = level_inputs(ctx, read, show_expected=more_levels) if overlays and not daily else ([], Feed())
        prints, prints_feed = ([], Feed())
        if show_orders and not daily:
            prints, prints_feed = ctx.tape(min_premium=premium, type={"Calls": "call", "Puts": "put"}.get(option_type))
        marks = []
        if rows and prints:
            visible = rows[-105:]
            start = utc(visible[0]["time_utc"])
            end = utc(visible[-1]["time_utc"]) + interval_length(timeframe)
            marks = mark_orders([order for order in prints if start <= order["stamp"] < end],
                                candles_on(candles, ctx.session), 14 if labels else 32)
        fig = (price_figure(rows, ctx.ticker, timeframe, levels=nearest_chart_levels(levels, rows, more_levels),
                            overlays=overlays and not daily, orders=marks, order_labels=labels) if rows else None)
        using_webull = ctx.price_provider == "Webull" and not daily
        render_chart(fig, "price_action_chart", None if using_webull else feed,
                     ctx.webull_error or "Waiting for completed candles in this timeframe.")
        if using_webull:
            st.caption(f"Webull · {et_time((ctx.webull.get('history') or {}).get('last_bar_utc'))}")
        else:
            feed_stamp("bars", feed, ctx)
        if show_orders and not daily:
            st.caption(f"Orders · {ctx.scope} · {compact(premium, currency=True)}+ · {len(marks)} matched prints selected by premium")
            feed_stamp("flow", prints_feed, ctx)
        if rows:
            with st.expander("Download candles"):
                csv_export(rows, ctx.title(f"{timeframe}_candles.csv"), "export_price_candles")
        return prints, prints_feed


def show_session_changes(ctx, read, prints, prints_feed):
    session_key = (ctx.ticker, ctx.scope, ctx.preview, ctx.price_provider, ctx.session)
    snap = {}
    timeline, gamma_feed, vol = read["flow"], read["gamma_feed"], read["vol"]
    if timeline.rows and not timeline.error:
        snap["Net premium"] = (timeline.snapshot.received_at.isoformat(), net_sum(timeline.rows, "net_premium_usd"), True)
    gamma_stamp = source_stamp("exposure", gamma_feed, ctx.ticker)
    if isinstance(gamma_stamp, datetime) and gamma_stamp.astimezone(ET).date() == ctx.session and not gamma_feed.error:
        for key, label, _, _ in LEVELS[:3]:
            snap[label] = (gamma_stamp.isoformat(), positive(option_levels(read["gamma"]).get(key)), False)
        snap["Net GEX"] = (gamma_stamp.isoformat(), numeric(read["gamma"].get("total")), True)
    if vol.snapshot and not vol.error and source_stamp("volatility", vol, ctx.ticker) == ctx.session:
        snap["30D IV"] = (vol.snapshot.received_at.isoformat(), numeric(vol.obj.get("atm_iv_30d")), False)
    state_key = "qd_last_observation"
    previous_key, prior, previous_changes = st.session_state.get(state_key, (None, {}, []))
    if session_key != previous_key:
        prior, previous_changes = {}, []
    changes = []
    for name, (stamp, value, dollars) in snap.items():
        old_stamp, old, _ = prior.get(name, (None, None, None))
        if old is not None and value is not None and stamp != old_stamp and value != old:
            change = (f"{(value - old) * 100:+.2f} pts" if name == "30D IV" else compact(value - old, currency=dollars, signed=True)
                      if dollars else f"{amount(old)} → {amount(value)}")
            changes.append(f"{name} {change}")
    bias = read["plan"]["bias"]
    if bias != "Waiting":
        previous_bias = prior.get("bias")
        if previous_bias and bias != previous_bias:
            changes.append(f"Bias {previous_bias} → {bias}")
        snap["bias"] = bias
    # Compare the same print filter only; toggling the filter should never count as new activity.
    if prints_feed.snapshot and not prints_feed.error:
        filter_key = (st.session_state.get(f"price_min_{ctx.ticker}"), st.session_state.get("price_order_type"))
        stamp = prints_feed.snapshot.received_at
        current_ids = {str(row["id"]) for row in prints if row.get("id") is not None}
        last_key, last_stamp, last_ids = prior.get("prints", (None, None, set()))
        if last_key == filter_key and stamp != last_stamp and last_ids is not None and current_ids - last_ids:
            changes.append(f"{len(current_ids - last_ids)} new large prints in view")
        snap["prints"] = (filter_key, stamp, current_ids)
    previous_changes = changes or previous_changes
    st.session_state[state_key] = (session_key, {**prior, **snap}, previous_changes)
    with st.expander("What changed"):
        if previous_changes:
            for change in previous_changes:
                st.write(change)
            st.caption("Compared with the previous observation for this ticker and expiry scope.")
        else:
            st.caption("Changes will appear here as new observations arrive on this page.")


def cvd_panel(ctx, candles, feed):
    with st.container(border=True):
        panel_header("Cumulative volume delta", "cvd", "overview_cvd", "Estimated share volume · resets each regular session")
        source, name = ctx.price_provider, ctx.ticker
        if ctx.ticker in INDICES:
            use_proxy = st.toggle("Show SPY share-volume proxy", key="index_cvd_proxy")
            if not use_proxy:
                st.caption(f"{ctx.ticker} has no traded share volume. Use SPY as a separate comparison or view contract-volume delta in Flow & Bubbles.")
                return
            candles, feed = ctx.history(symbol="SPY")
            source, name = "Tradytics", "SPY"
        points = share_cvd(candles, ctx.session)
        if points:
            plot(cvd_figure(points), "underlying_cvd")
            opened, closed = rth_bounds(ctx.session)
            expected = int((min(closed, ctx.now) - opened).total_seconds() // 60)
            st.caption(f"{name} · {source} · {len(points)} of {expected} closed minutes · as of {et_time(points[-1]['time'])}")
            if len(points) < expected:
                st.caption("The total covers the available minutes. Some minutes are missing or still arriving.")
        else:
            empty(feed, "Share volume is unavailable for this ticker and session.")
        if source == "Tradytics":
            feed_stamp("bars", feed, ctx)


def overview(ctx):
    read = read_workspace(ctx)
    quote = ctx.quote()
    flow, plan, regime = read["flow"], read["plan"], read["regime"]
    gamma, vol = read["gamma"], read["vol"]
    spot, change = positive(quote.get("price")), numeric(quote.get("change_pct"))
    net = net_sum(flow.rows, "net_premium_usd")
    panel_header("Market pulse", "overview", "overview_kpis", ctx.scope)
    kpis([
        metric("Underlying", amount(spot), f"{signed_pct(change)} · {ctx.price_provider}", change),
        metric("Volatility regime", regime["label"], "Completed 5m candles"),
        metric("Move readiness", f"{plan['score']}/100" if plan["score"] is not None else "—", plan["state"]),
        metric("Net GEX", compact(gamma.get("total"), currency=True, signed=True), "$ per 1% move", gamma.get("total")),
        metric("Options net flow", compact(net, currency=True, signed=True), "Completed intervals", net),
        metric("IV rank", ratio_pct(vol.obj.get("iv_rank")), f"30D ATM IV {ratio_pct(vol.obj.get('atm_iv_30d'))}"),
    ])
    if plan["score"] is not None:
        st.caption(f"Market bias {plan['bias']} · {plan['state']} · price and flow through {et_time(read['price_stamp'])}")
    elif read["reasons"]:
        st.caption(" ".join(read["reasons"]))
    left, right = st.columns([2.55, 1], gap="medium")
    with left:
        prints, prints_feed = price_panel(ctx, read)
    with right:
        with st.container(border=True):
            panel_header("Dealer levels", "levels", "overview_levels", ctx.scope)
            level_list(gamma, spot)
            feed_stamp("exposure", read["gamma_feed"], ctx)
        with st.container(border=True):
            panel_header("Session net flow", "net_flow", "overview_flow", "Cumulative directional premium")
            render_chart(flow_figure(flow.rows, compact=True), "overview_flow_spark", flow)
            feed_stamp("timeline", flow, ctx)
    # Independent 0DTE profile: never silently reuse the workspace DTE selection.
    with st.container(border=True):
        panel_header("0DTE gamma exposure by strike", "gamma", "overview_0dte_gex", f"{ctx.ticker} · same-session expiration")
        zero_feed = ctx.get("exposure", symbol=ctx.ticker, greek="gamma", by="strike", expiry=expiration_date(ctx.session, 0))
        zero = exposure_obj(zero_feed, ctx.ticker, "gamma")
        stamp = source_stamp("exposure", zero_feed, ctx.ticker)
        valid = (ctx.preview or (not zero_feed.error and not feed_has_delay(zero_feed)
                 and isinstance(stamp, datetime) and 0 <= (ctx.now - stamp).total_seconds() <= 420))
        zero_rows = zero.get("rows") if isinstance(zero.get("rows"), list) else zero_feed.rows
        if valid and zero_rows:
            chart = exposure_figure(zero_rows, "strike", display_exposure_unit(zero), spot,
                                    positive((zero.get("levels") or {}).get("flip")), split=True)
            render_chart(chart, "overview_0dte_gex_chart", zero_feed)
            st.caption("0DTE only · provider exposure model · calls/puts and net are model estimates, not observed dealer inventory.")
        else:
            st.info("0DTE GEX unavailable or not fresh. No chart is inferred from all-expiration exposure.")
        feed_stamp("exposure", zero_feed, ctx)
    show_session_changes(ctx, read, prints, prints_feed)
    with st.container(border=True):
        panel_header("Cumulative flow & momentum", "net_flow", "overview_momentum", ctx.scope)
        render_chart(flow_desk_figure(flow.rows, "5m"), "overview_flow_chart", flow)
        feed_stamp("timeline", flow, ctx)
    cvd_panel(ctx, read["candles"], read["bars"])


def tape_table(rows, threshold=None):
    recent = defaultdict(deque)
    result = []
    for row in rows:
        key = (row.get("contract"), row["side_label"])
        prior = recent[key]
        stamp = row["stamp"]
        while prior and stamp - prior[0] > timedelta(minutes=15):
            prior.popleft()
        checks = (row.get("side_checked") is True and row["side_label"] in ("Bought", "Sold"),
                  row.get("condition") not in ("spread", "complex"), bool(prior),
                  threshold is not None and row["premium_usd"] >= 2 * threshold)
        result.append({
            "Time (ET)": et_time(stamp, short=True), "Ticker": row.get("symbol"), "Type": row.get("type"),
            "Strike": numeric(row.get("strike")), "Expiry": row.get("expiry"), "DTE": numeric(row.get("dte")),
            "Side": row["side_label"], "Side check": "Checked" if row.get("side_checked") is True else "Pending",
            "Kind": row.get("kind"), "Premium": row["premium_usd"], "Size": numeric(row.get("size")),
            "Similar prints in prior 15m": len(prior) if key[0] else None,
            **({"Review points": sum(weight for condition, weight in zip(checks, (35, 25, 20, 20)) if condition)}
               if threshold is not None else {}),
            "Condition": row.get("condition"), "Opening flag": row.get("opening"),
        })
        if key[0]:
            prior.append(stamp)
    return list(reversed(result))


def contract_volume_delta(prints):
    groups = {}
    for row in prints:
        size = positive(row.get("size"))
        side = {"Bought": 1, "Sold": -1}.get(row["side_label"])
        if size is None or side is None or row.get("side_checked") is not True:
            continue
        stamp = row["stamp"].replace(second=0, microsecond=0) + MINUTE
        groups[stamp] = groups.get(stamp, 0) + side * size
    total = 0
    points = []
    for stamp, delta in sorted(groups.items()):
        total += delta
        points.append({"time": stamp.isoformat(), "delta": delta, "cvd": total})
    return points


def premium_sample(orders, day):
    opened, closed = rth_bounds(day)
    rows = {}
    for order in orders:
        if (not opened <= order["stamp"] < closed or order.get("side_checked") is not True or
                order.get("condition") in ("spread", "complex") or not order["directional"]):
            continue
        stamp = order["stamp"].replace(second=0, microsecond=0)
        row = rows.setdefault(stamp, {"time": stamp.isoformat(), "bullish": 0, "bearish": 0})
        row["bullish" if order["directional"] > 0 else "bearish"] += order["premium_usd"]
    bullish = bearish = 0
    for stamp in sorted(rows):
        row = rows[stamp]
        bullish += row["bullish"]
        bearish += row["bearish"]
        row.update(bullish=bullish, bearish=-bearish, net=bullish - bearish)
    return sorted(rows.values(), key=lambda row: row["time"])


def options_flow(ctx):
    a, b, c = st.columns([1, 1, 1])
    market = a.selectbox("Flow universe", [ctx.ticker, "Market · single-stock net flow"], key="flow_universe") != ctx.ticker
    interval = b.selectbox("Aggregation", ["1m", "5m", "15m", "1h"], index=1, key="flow_interval")
    day = c.date_input("Session (blank = latest)", value=None, max_value=ctx.session, key="flow_date")
    if day and not rth_bounds(day):
        st.info("Choose a regular market session.")
        return
    tape_day = day or ctx.session
    expiry = expiration_date(tape_day, ctx.dte)
    scope = f"{ctx.dte} DTE · {expiry}" if expiry else "All DTE"
    timeline = ctx.timeline(interval=interval, session=day, market=market)
    rows = timeline.rows
    totals = [net_sum(rows, key) for key in ("net_premium_usd", "call_premium_usd", "put_premium_usd", "prints")]
    kpis([metric(label, compact(total, currency=True, signed=True), "Loaded session intervals", total)
          for label, total in zip(("Directional net", "Net calls", "Net puts"), totals)] +
         [metric("Net options delta", compact(net_sum(rows, "net_delta_usd"), currency=True, signed=True), "Stock-equivalent dollars"),
          metric("Print count", amount(totals[-1], 0), "Provider aggregate")], small=True)
    with st.container(border=True):
        panel_header("Cumulative flow & momentum", "net_flow", "flow_timeline",
                     f"{scope} · {'market-wide single stocks with SPY price' if market else ctx.ticker}")
        components = st.toggle("Show net calls and net puts", value=True, key="flow_components")
        render_chart(flow_desk_figure(rows, interval, components=components), "full_flow_timeline", timeline,
                     "No completed intervals are available for this session and expiry.")
        if timeline.snapshot and ctx.dte and timeline.snapshot.meta.get("source_intervals", 0) > len(rows):
            st.caption("Only intervals present in both DTE totals are shown. The cumulative line covers that matched sample.")
        feed_stamp("timeline", timeline, ctx)
        csv_export(rows, ctx.title("net_flow.csv"), "export_net_flow")
    with st.container(border=True):
        panel_header(f"{ctx.ticker} flow bubbles", "bubbles", "flow_bubbles", f"Large executed options trades · {scope}")
        x, y, z, w = st.columns(4)
        min_premium = x.number_input("Minimum premium ($)", min_value=25_000, max_value=100_000_000,
                                     value=default_premium(ctx.ticker), step=25_000, key=f"flow_min_{ctx.ticker}")
        option_type = y.selectbox("Contract", ["All", "Call", "Put"], key="flow_type")
        kind = z.selectbox("Print kind", ["All", "Sweep", "Block", "Split", "Single"], key="flow_kind")
        side = w.selectbox("Inferred side", ["All", "Bought", "Sold", "Mid", "At or above ask"], key="flow_side")
        a, b, c = st.columns(3)
        opening = a.toggle("Opening-size flag only", key="flow_opening")
        window = b.selectbox("Tape window", ["Full session", "Last 60m", "Last 30m"], key="flow_window")
        rank = c.selectbox("Bubble density", ["Largest 45", "Top 2% of loaded prints"], key="bubble_rank")
        orders, tape = ctx.tape(session=day, min_premium=min_premium, limit=500,
                                type=None if option_type == "All" else option_type.lower(),
                                kind=None if kind == "All" else kind.lower(), opening=True if opening else None,
                                side="bought" if side == "At or above ask" else None if side == "All" else side.lower())
        if side == "At or above ask":
            orders = [row for row in orders if (numeric(row.get("aggression")) or 0) >= 2]
        if window != "Full session":
            _, close = rth_bounds(tape_day)
            cutoff = min(close, ctx.now) - timedelta(minutes=60 if window == "Last 60m" else 30)
            orders = [row for row in orders if row["stamp"] >= cutoff]
        candles, bar_feed = ctx.history(session=day)
        bars = candles_on(candles, tape_day)
        count = max(1, (len(orders) + 49) // 50) if rank == "Top 2% of loaded prints" else 45
        marks = mark_orders(orders, bars, count)
        render_chart(bubble_figure(bars, marks, ctx.ticker), "option_bubble_chart", bar_feed,
                     "Waiting for completed underlying price candles for this session.")
        st.caption(f"{len(marks)} bubbles from {len(orders)} loaded prints · size by premium · filled bought, hollow sold · C / P is the contract type")
        if tape.snapshot and (tape.snapshot.cursor or len(tape.rows) >= 500):
            st.caption("The tape is a recent sample. Earlier matching prints may exist.")
        feed_stamp("flow", tape, ctx)
    with st.container(border=True):
        panel_header("Executed order tape", "tape", "flow_tape", f"{ctx.ticker} · latest matching prints")
        display = tape_table(orders, min_premium)
        if display:
            table(display, height=365, config={"Premium": st.column_config.NumberColumn(format="dollar")})
        else:
            empty(tape, "No options prints match these filters.")
        csv_export(display, ctx.title("options_tape.csv"), "export_options_tape")
        with st.expander("How the review points work"):
            st.write("Trades get 35 points for a checked buy or sell side and 25 if Tradytics did not flag a spread or complex trade. An earlier matching trade adds 20. Premium at twice your filter adds 20.")
        if st.toggle("Show directional premium for this tape", value=not rows, key="tape_directional_premium"):
            sample = premium_sample(orders, tape_day)
            points = [{**row, "time_et": utc(row["time"]).astimezone(ET).replace(tzinfo=None)} for row in sample]
            panel_header("Directional premium in this tape", "net_flow", "sample_premium", "Loaded prints with checked sides · chart filters apply")
            render_chart(series_figure(points, "time_et", [("Bullish premium", "bullish", TEAL, 1),
                         ("Bearish premium", "bearish", RED, 1), ("Net", "net", BLUE, 1)], x_time=True, y_format="~s"),
                         "options_premium_sample", tape)
        if st.toggle("Show contract-volume delta", key="flow_contract_cvd"):
            points = contract_volume_delta(orders)
            panel_header("Contract-volume delta", "contract_cvd", "options_cvd", "Bought minus sold contracts · loaded sample")
            render_chart(cvd_figure(points, unit="contracts"), "options_contract_cvd", tape,
                         "Waiting for prints with a checked buy or sell side.")


def chain_panel(ctx):
    with st.container(border=True):
        panel_header("Option chain", "chain", "chain", "First 250 contracts ordered by expiry and strike. Use filters for a narrower view.")
        a, c = st.columns(2)
        right = a.selectbox("Contract type", ["Both", "Call", "Put"], key="chain_type")
        near = c.toggle("Within 3% of spot", value=True, key="chain_near")
        spot = positive(ctx.quote().get("price"))
        chain = ctx.get("chain", symbol=ctx.ticker, limit=250, expiry=ctx.expiry,
                        type=None if right == "Both" else right.lower(),
                        min_strike=round(spot*.97, 3) if near and spot else None, max_strike=round(spot*1.03, 3) if near and spot else None)
        display = [{"Contract": row.get("contract"), "Type": row.get("type"), "Expiry": row.get("expiry"), "Strike": numeric(row.get("strike")),
                    "Bid": numeric(row.get("bid")), "Ask": numeric(row.get("ask")), "Volume": numeric(row.get("volume")),
                    "OI": numeric(row.get("open_interest")), "IV": ratio_pct(row.get("iv")), "Delta": numeric(row.get("delta")),
                    "Gamma": numeric(row.get("gamma")), "Vega": numeric(row.get("vega")), "Theta": numeric(row.get("theta")),
                    "Quote (ET)": et_time(row.get("updated_at"))}
                   for row in filter_expiration(same_ticker(chain.rows, ctx.ticker), ctx.session, ctx.dte)]
        if display:
            table(display, height=440)
            csv_export(display, ctx.title("option_chain.csv"), "export_chain")
        else:
            empty(chain, "No contracts match the selected window.")
        feed_stamp("chain", chain, ctx)


def comparison_panel(ctx, greek, feed):
    obj = exposure_obj(feed, ctx.ticker, greek)
    webull_exp = ctx.webull.get("exposure") or {}
    coverage = webull_exp.get("coverage") or {}
    field_name, local_unit = {
        "gamma": ("net_gex_usd_per_1pct", "$ per 1% spot move"),
        "delta": ("net_delta_proxy_shares", "shares"),
        "vanna": ("net_vex_delta_shares_per_vol_point", "shares per IV point"),
        "charm": ("net_charm_delta_shares_per_day", "shares per day"),
    }[greek]
    with st.container(border=True):
        panel_header("Source comparison", "compare", "compare", "Each source keeps its own model scope and units")
        webull_scope = f"{coverage.get('contracts_modeled', 0)} contracts · ≤{coverage.get('dte_days', '—')} DTE · ±{coverage.get('strike_width_pct', '—')}%"
        dealer_stamp = status("exposure", feed, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)
        rows = [{"Source": "Illustrative data" if ctx.preview else "Tradytics", "Exposure": compact(obj.get("total"), signed=True),
                 "Unit": display_exposure_unit(obj), "Scope": ctx.scope, "As of": dealer_stamp[1], "Status": dealer_stamp[0]},
                {"Source": "Webull local model", "Exposure": compact(webull_exp.get(field_name), signed=True), "Unit": local_unit,
                 "Scope": webull_scope if coverage else "—", "As of": et_time(coverage.get("oldest_option_quote_utc")),
                 "Status": "CURRENT" if ctx.webull_quality.get("options_live") else "AGED / UNVERIFIED" if webull_exp else "UNAVAILABLE"}]
        table(rows, height=143)
        if ctx.webull_error or not webull_exp:
            st.caption(ctx.webull_error or "Enable Webull comparison under Feed controls and add your local Webull credentials.")
        st.caption("Webull scope comes from its configured strike and expiry window. Tradytics uses the current workspace expiry filter.")
    if webull_exp.get("by_strike"):
        with st.container(border=True):
            panel_header(f"Webull modeled {greek.title()} by strike", "compare", "webull_profile", local_unit)
            exposure_key = {"gamma": "gex", "vanna": "vex", "charm": "charm", "delta": "delta"}[greek]
            strikes = [{"strike": row.get("strike"), "net": row.get(exposure_key)} for row in webull_exp["by_strike"]]
            render_chart(exposure_figure(strikes, "strike", local_unit, positive(webull_exp.get("spot")),
                                         positive(webull_exp.get("nearest_gamma_flip")) if greek == "gamma" else None),
                         "webull_profile_chart")


def positioning(ctx):
    view = st.segmented_control("Positioning view", ["Exposure", "Hedge context", "Option chain", "Compare sources"], default="Exposure",
                                key="positioning_view", label_visibility="collapsed") or "Exposure"
    if view == "Option chain":
        chain_panel(ctx)
        return
    if view == "Hedge context":
        panel_header("Dealer hedge sensitivities", "gamma", "hedge_sensitivities", f"{ctx.ticker} · {ctx.scope}")
        rows = []
        for greek, text in (("gamma", "Price moves"), ("delta", "Current price exposure"), ("vanna", "IV changes"), ("charm", "Time passes")):
            feed = ctx.exposure(greek)
            obj = exposure_obj(feed, ctx.ticker, greek)
            tag, stamp = status("exposure", feed, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)
            rows.append({"Exposure": greek.title(), "What changes": text, "Net": numeric(obj.get("total")),
                         "Unit": display_exposure_unit(obj), "Status": tag, "Source time (ET)": stamp})
        table(rows, height=200)
        st.caption("The table shows modeled sensitivities. Each Greek has its own units.")
        return
    left, middle = st.columns([1.5, 1])
    greek = (left.segmented_control("Greek", ["Gamma", "Vanna", "Charm", "Delta"], default="Gamma", key="positioning_greek") or "Gamma").lower()
    if view == "Compare sources":
        comparison_panel(ctx, greek, ctx.exposure(greek))
        return
    by = middle.selectbox("Group by", ["Strike", "Expiry"], key="positioning_by").lower()
    feed = ctx.exposure(greek, by=by)
    obj = exposure_obj(feed, ctx.ticker, greek)
    level_feed = feed if greek == "gamma" and by == "strike" else ctx.exposure("gamma")
    level_obj = exposure_obj(level_feed, ctx.ticker, "gamma")
    levels = option_levels(level_obj)
    spot = positive(obj.get("spot")) or positive(ctx.quote().get("price"))
    scope = ctx.scope
    unit = display_exposure_unit(obj)
    kpis([metric("Net " + greek, compact(obj.get("total"), signed=True), unit, obj.get("total")),
          metric("Model spot", amount(spot), scope),
          metric("Gamma flip", amount(levels.get("flip")), "Same expiry scope"),
          metric("Max pain", amount(levels.get("max_pain")), "Provider expiry reference")], small=True)
    a, b = st.columns([2.7, 1], gap="medium")
    with a.container(border=True):
        panel_header(f"{greek.title()} by {by}", greek, "exposure_profile", f"{scope} · {unit}")
        c, d = st.columns(2)
        near = c.toggle("Focus near spot (±3%)", value=True, disabled=by != "strike", key="exposure_near")
        split = d.toggle("Call / put components", value=False, disabled=greek in ("vanna", "charm"), key="exposure_split")
        rows = feed.rows if obj else []
        if near and by == "strike" and spot:
            rows = [row for row in rows if (strike := positive(row.get("strike"))) and abs(strike / spot - 1) <= .03]
        render_chart(exposure_figure(rows, by, unit, spot, positive(levels.get("flip")) if greek == "gamma" else None,
                                     split=split and greek not in ("vanna", "charm")), "positioning_profile_chart", feed)
        feed_stamp("exposure", feed, ctx)
        csv_export(rows, ctx.title(f"{greek}_by_{by}.csv"), "export_exposure")
    with b.container(border=True):
        panel_header("Key levels", "levels", "positioning_levels", scope)
        level_list(level_obj, spot)
        feed_stamp("exposure", level_feed, ctx)
        if obj.get("model"):
            st.caption(str(obj["model"])[:280])
        total = numeric(obj.get("total"))
        if greek == "vanna" and total is not None:
            st.markdown("**Local IV scenario**")
            st.caption(f"+1 IV point: {compact(total, signed=True)} · −1 IV point: {compact(-total, signed=True)} ({unit}).")
        elif greek == "charm" and total is not None:
            st.markdown("**Local time sensitivity**")
            st.caption(f"{compact(total, signed=True)} shares/day at this snapshot. Sensitivity changes as expiry approaches.")


def volatility_page(ctx):
    candles, bar_feed = ctx.history()
    regime = realized_regime(chart_buckets(candles, 5))
    with st.container(border=True):
        panel_header("Volatility regime", "regime", "market_regime", "Recent completed 5-minute price bars")
        rv = numeric(regime.get("realized_vol_annual_pct"))
        volume = numeric(regime.get("volume_ratio"))
        kpis([metric("Regime", regime["label"]),
              metric("Recent volatility", f"{rv:.1f}%" if rv is not None else "—", "Annualized from recent 5m bars"),
              metric("Range width rank", amount(regime.get("bb_width_percentile"), 0), "0–100 relative to recent widths"),
              metric("5m relative volume", f"{volume:.2f}×" if volume is not None else "—", "Versus prior 20 five-minute bars")], small=True)
        if ctx.price_provider == "Tradytics":
            feed_stamp("bars", bar_feed, ctx)
        st.caption(f"Last completed 5m bar started {et_time(regime.get('last_closed_candle_utc'))}")
        window = session_window(candles, ctx.now, ctx.session)
        typical = window["typical_move"]
        if typical is not None:
            st.caption(f"{window['phase']} · median absolute 30m move at this time {typical:.2%} · {window['sample']} prior sessions")
    vol = ctx.get("volatility", symbol=ctx.ticker)
    obj = vol.obj
    move = obj.get("expected_move") if isinstance(obj.get("expected_move"), dict) else {}
    move_amount = numeric(move.get("move_usd"))
    kpis([metric("30D ATM IV", ratio_pct(obj.get("atm_iv_30d")), "Annualized"),
          metric("IV rank", ratio_pct(obj.get("iv_rank")), f"Percentile {ratio_pct(obj.get('iv_percentile'))}"),
          metric("Realized vol", ratio_pct(obj.get("realized_vol_20d")), "20-session close-to-close"),
          metric("Expected move", f"±{amount(move_amount)}" if move_amount is not None else "—", f"Expiry {move.get('expiry', '—')}")], small=True)
    left, right = st.columns(2, gap="medium")
    with left.container(border=True):
        panel_header("ATM term structure", "volatility", "iv_term", "Implied volatility by expiry")
        render_chart(series_figure(obj.get("term") or [], "expiry", [("ATM IV", "atm_iv", TEAL, 100)], ticksuffix="%"), "iv_term_chart", vol)
        feed_stamp("volatility", vol, ctx)
    with right.container(border=True):
        panel_header("25-delta skew", "volatility", "iv_skew", "Put IV and call IV at matched absolute delta")
        render_chart(series_figure(obj.get("skew") or [], "expiry",
                                   [("Put IV", "put_25d_iv", RED, 100), ("Call IV", "call_25d_iv", TEAL, 100)], ticksuffix="%"), "iv_skew_chart", vol)
        feed_stamp("volatility", vol, ctx)
    with st.container(border=True):
        panel_header("IV against realized volatility", "volatility", "iv_history", "Daily history · last 90 calendar days")
        history = ctx.get("volatility_history", symbol=ctx.ticker, **{"from": str(ctx.day-timedelta(days=90))})
        render_chart(series_figure(history.rows, "date", [("30D ATM IV", "atm_iv_30d", TEAL, 100),
            ("20D realized", "realized_vol_20d", BLUE, 100)], ticksuffix="%"), "iv_history_chart", history)
        feed_stamp("volatility_history", history, ctx)
    with st.container(border=True):
        breadth = ctx.get("breadth")
        panel_header("Market participation", "breadth", "regime_breadth", "Liquid US stocks")
        kpis([metric("Advancing", amount(breadth.obj.get("up"), 0)), metric("Declining", amount(breadth.obj.get("down"), 0)),
              metric("Equal-weight S&P 500", signed_pct(breadth.obj.get("equal_weight_change_pct"))),
              metric("Cap-weight S&P 500", signed_pct(breadth.obj.get("cap_weight_change_pct")))], small=True)
        feed_stamp("breadth", breadth, ctx)


def dark_pool(ctx):
    if ctx.ticker in INDICES:
        st.info(f"{ctx.ticker} is an index and has no off-exchange share prints. Open an ETF such as SPY or QQQ to see its own levels.")
        return
    a, b = st.columns(2)
    window = a.selectbox("Level lookback (sessions)", [1, 5, 10, 20], index=1, key="dark_window")
    min_notional = b.number_input("Minimum print notional ($)", min_value=250000, max_value=2_000_000_000, value=1_000_000,
                                  step=250000, key="dark_notional")
    levels = ctx.get("dark_levels", symbol=ctx.ticker, window=window, limit=30)
    prints = ctx.get("dark_prints", symbol=ctx.ticker, venue="dark_pool", min_notional=min_notional, sort="time", limit=150)
    left, right = st.columns([1, 1.6], gap="medium")
    with left.container(border=True):
        panel_header("Dark-pool price levels", "dark", "dark_levels", f"Top notional bins · {window} sessions")
        render_chart(dark_figure(levels.rows), "dark_price_profile", levels)
        feed_stamp("dark_levels", levels, ctx)
    with right.container(border=True):
        panel_header("Reported stock prints", "dark", "dark_tape", "Off-exchange share trades")
        display = [{"Time (ET)": et_time(row.get("time")), "Price": numeric(row.get("price")), "Shares": numeric(row.get("size")),
                    "Notional": numeric(row.get("notional_usd")), "Rank": numeric(row.get("rank")), "Kind": row.get("kind"),
                    "Conditions": ", ".join(map(str, row.get("conditions") or [])) if isinstance(row.get("conditions"), list) else row.get("conditions")}
                   for row in same_ticker(prints.rows, ctx.ticker) if row.get("venue") == "dark_pool"]
        if display:
            table(display, height=372, config={"Notional": st.column_config.NumberColumn(format="dollar")})
            csv_export(display, ctx.title("dark_pool_prints.csv"), "export_dark")
        else:
            empty(prints, "No off-exchange share prints match this symbol and threshold.")
        feed_stamp("dark_prints", prints, ctx)


def signals_page(ctx):
    a, b, c = st.columns(3)
    source = a.selectbox("Pattern", ["All patterns", *SIGNAL_PATTERNS], key="signals_source")
    state = b.selectbox("Contract status", ["Any", "Open", "Settled"], key="signals_status")
    session = c.date_input("Fired on (empty = latest)", value=None, max_value=ctx.day, key="signal_date")
    result = ctx.get("signals", symbol=ctx.ticker, source=None if source == "All patterns" else source,
                     status=None if state == "Any" else state.lower(), date=str(session) if session else None, limit=100)
    rows = filter_expiration(same_ticker(result.rows, ctx.ticker), session or ctx.session, ctx.dte)
    with st.container(border=True):
        panel_header("Pattern signals", "signals", "signals_list", f"{ctx.ticker} · filter applied to the loaded provider alerts")
        display = [{"Fired (ET)": et_time(row.get("fired_at")), "Pattern": field(row, "pattern", "source"), "Contract": row.get("contract"),
                    "Strike": numeric(row.get("strike")), "Expiry": row.get("expiry"), "Ask entry": numeric(row.get("ask")),
                    "Mark / settlement return": ratio_pct(row.get("return")), "Status": row.get("status"), "Premium": numeric(row.get("premium_usd"))}
                   for row in rows]
        if display:
            table(display, config={"Premium": st.column_config.NumberColumn(format="dollar")})
        else:
            empty(result, "No signals match this ticker, session and pattern.")
        feed_stamp("signals", result, ctx)
        csv_export(display, ctx.title("signals.csv"), "export_signals")
        ids = {str(row["id"]): row for row in rows if str(row.get("id")).isdecimal()}
        if ids:
            selected = st.selectbox("Inspect a signal", ["", *ids], key="selected_signal",
                                    format_func=lambda sid: (f"{field(ids[sid], 'pattern', 'source')} · {ids[sid].get('contract')} · {et_time(ids[sid].get('fired_at'), short=True)}" if sid else "Choose a signal"))
            if selected:
                detail = ctx.get("signal", id=int(selected))
                obj = detail.obj if detail.obj.get("symbol") in (ctx.ticker, None) else {}
                if obj:
                    st.write(str(obj.get("title") or "Signal detail")[:300])
                    st.text(str(obj.get("body") or "")[:3000])
                    marks = [{"time_et": ts.astimezone(ET).replace(tzinfo=None), "value": row.get("value")}
                             for row in obj.get("marks") or [] if (ts := time_or_none(row.get("time")))]
                    panel_header("Contract marks", "signals", "signal_marks", "Provider mid marks, then settlement")
                    render_chart(series_figure(marks, "time_et", [("Contract mark", "value", TEAL, 1)], x_time=True), "signal_marks_chart",
                                 detail, "This signal has no marks yet.")
                    if isinstance(obj.get("prints"), list):
                        panel_header("Supporting prints", "tape", "signal_prints")
                        table(tape_table(order_rows(obj["prints"], ctx.ticker, ctx.now)), height=220)
                else:
                    empty(detail)
    with st.container(border=True):
        panel_header("Pattern performance", "performance", "signal_performance", "Provider universe · grouped by pattern across tickers")
        left, right = st.columns(2)
        window = left.segmented_control("Lookback", ["7d", "30d", "90d", "all"], default="30d", key="signal_window") or "30d"
        settled = right.toggle("Settled only", value=True, key="signal_settled")
        perf = ctx.get("performance", window=window, status="settled" if settled else None,
                       source=None if source == "All patterns" else source)
        display = [{"Pattern": field(row, "pattern", "source"), "Signals": numeric(row.get("signals")), "Settled": numeric(row.get("settled")),
                    "Win rate": ratio_pct(row.get("win_rate")), "Average": ratio_pct(row.get("average_return")), "Median": ratio_pct(row.get("median_return")),
                    "Best": ratio_pct(row.get("best_return")), "Worst": ratio_pct(row.get("worst_return"))} for row in perf.rows]
        if display:
            table(display, height=245)
        else:
            empty(perf)
        feed_stamp("performance", perf, ctx)


@st.cache_data(show_spinner=False, max_entries=8, ttl=180)
def forecast_result(candles, spot, horizons, paths, model, drift, band, levels, origin):
    return forecast(candles, spot, horizons=horizons, paths=paths, model=model, drift_mode=drift, deadband_bps=band,
                    levels=levels, asof=origin)


@st.cache_data(show_spinner=False, max_entries=6, ttl=300)
def calibration(candles, horizon, model, drift):
    return walk_forward(candles, horizon=horizon, model=model, drift_mode=drift)


def forecast_page(ctx):
    quote = ctx.quote()
    spot = positive(quote.get("price"))
    candles, bar_feed = ctx.history()
    if ctx.price_provider == "Webull":
        session = session_state(ctx.now)
        reasons = [] if ctx.webull_quality.get("forecast_ready") and not ctx.paused else ctx.webull_quality.get("reasons") or [ctx.webull_error or "Waiting for a verified Webull quote and candles."]
    else:
        session, reasons = snapshot_forecast_gate(quote, candles, ctx.quote_feed(), bar_feed, ctx.now, ctx.paused)
    with st.container(border=True):
        panel_header("Forecast settings", "forecast", "forecast_settings", f"{ctx.price_provider} · completed one-minute returns")
        a, b, c, d = st.columns(4)
        model = a.selectbox("Path model", ["Block bootstrap", "Lognormal (GBM)"], key="forecast_model")
        drift = b.selectbox("Direction assumption", ["Neutral", "Recent trend"], key="forecast_drift")
        paths = c.selectbox("Simulated paths", [5000, 10000, 20000], index=1, key="forecast_paths")
        band = d.number_input("Neutral zone (bps)", min_value=0.0, max_value=100.0, value=2.0, step=1.0, key="forecast_band")
        with st.expander("Custom price levels"):
            upper = st.number_input("Upper level (0 disables)", min_value=0.0, step=1.0, key="forecast_upper")
            lower = st.number_input("Lower level (0 disables)", min_value=0.0, step=1.0, key="forecast_lower")
    horizons = tuple(h for h in (5, 15, 30, 60) if h <= session["minutes_left"])
    if reasons or spot is None or not horizons:
        with st.container(border=True):
            panel_header("Forecast paused", "forecast", "forecast_paused")
            st.write(" ".join(reasons) if reasons else "The shortest forecast needs five regular-session minutes and an underlying price.")
        return
    levels = {}
    gamma = ctx.exposure("gamma")
    badge, _ = status("exposure", gamma, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)
    if badge in ("DEMO", "RECENT"):
        reported = option_levels(exposure_obj(gamma, ctx.ticker, "gamma"))
        levels.update({f"{label} · {'demo' if ctx.preview else 'Tradytics'}": val for key, label, _, _ in LEVELS[:3]
                       if (val := positive(reported.get(key)))})
    if ctx.price_provider == "Webull" and ctx.webull_quality.get("options_live"):
        webull_model = ctx.webull.get("exposure") or {}
        for label, field_name in (("Call OI wall", "call_oi_wall"), ("Put OI wall", "put_oi_wall"),
                                  ("Gamma flip", "nearest_gamma_flip")):
            if (value := positive(webull_model.get(field_name))) is not None:
                levels[f"{label} · Webull model"] = value
    if upper:
        levels["Custom upper"] = upper
    if lower:
        levels["Custom lower"] = lower
    model_key = "bootstrap" if model.startswith("Block") else "gbm"
    drift_key = "neutral" if drift == "Neutral" else "recent_trend"
    origin = ctx.now.replace(second=0, microsecond=0).isoformat()
    try:
        result = forecast_result(candles, spot, horizons, paths, model_key, drift_key, band, levels, origin)
    except DataError as exc:
        st.info(str(exc))
        return
    with st.container(border=True):
        panel_header("Price distribution", "forecast", "forecast_fan", f"{paths:,} paths · {result['training_returns']:,} training returns · {ctx.price_provider}")
        horizon = st.segmented_control("Horizon", list(horizons), default=15 if 15 in horizons else horizons[0],
                                       format_func=lambda x: f"{x}m", key="forecast_horizon") or horizons[0]
        render_chart(forecast_figure(result, horizon), "forecast_fan_chart")
        st.caption(f"Quote {et_time(field(quote, 'price_at', 'time'))} · last bar {et_time(candles[-1]['time_utc'])} · origin {et_time(origin)}")
    with st.container(border=True):
        panel_header("Market context", "forecast", "forecast_context", f"Separate from the simulated paths · {ctx.scope}")
        flow_feed = ctx.timeline()
        vol_feed = ctx.get("volatility", symbol=ctx.ticker)
        gex = exposure_obj(gamma, ctx.ticker, "gamma").get("total")
        net_flow = net_sum(flow_rows(ctx, flow_feed), "net_premium_usd")
        iv = vol_feed.obj.get("atm_iv_30d")
        gex_status = status("exposure", gamma, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)[0]
        flow_status = status("timeline", flow_feed, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)[0]
        iv_status = status("volatility", vol_feed, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)[0]
        kpis([metric("Net GEX", compact(gex, currency=True, signed=True), gex_status, gex),
              metric("Net premium", compact(net_flow, currency=True, signed=True), flow_status, net_flow),
              metric("30D ATM IV", ratio_pct(iv), iv_status),
              metric("Realized model vol", f"{amount(result.get('realized_vol_annual_pct'), 1)}%", "Annualized from model inputs")], small=True)
    with st.container(border=True):
        panel_header("Model probabilities and ranges", "forecast", "forecast_table", f"Neutral zone ±{band:g} basis points around the starting price")
        display = [{"Horizon": f"{r['minutes']}m", "Up": ratio_pct(r["p_up"]), "Neutral": ratio_pct(r["p_neutral"]),
                    "Down": ratio_pct(r["p_down"]), "68% range": f"{amount(r['low_68'])} – {amount(r['high_68'])}",
                    "95% range": f"{amount(r['low_95'])} – {amount(r['high_95'])}", "Median": amount(r["median"])} for r in result["horizons"]]
        table(display, height=200)
        target = next(row for row in result["horizons"] if row["minutes"] == horizon)
        if target.get("levels"):
            st.caption(f"Touch probability over {horizon}m, sampled at one-minute checkpoints")
            table([{"Level": r["name"], "Price": amount(r["price"]), "Touch probability": ratio_pct(r["touch_probability"])}
                   for r in target["levels"]], height=222)
        export = {**result, "ticker": ctx.ticker, "source": "ILLUSTRATIVE" if ctx.preview else ctx.price_provider,
                  "quote_as_of": field(quote, "price_at", "time")}
        st.download_button("Download forecast JSON", json.dumps(export, indent=2), file_name=ctx.title("forecast.json"),
                           mime="application/json", key="forecast_download")
    with st.container(border=True):
        panel_header("Walk-forward calibration", "calibration", "calibration", "Recent history · nonoverlapping outcomes")
        calibration_key = (ctx.ticker, ctx.price_provider, ctx.preview, horizon, model_key, drift_key)
        if st.button("Run calibration", key="run_terminal_calibration"):
            st.session_state["desk_calibration"] = (calibration_key, calibration(candles, horizon, model_key, drift_key))
        saved_key, sample = st.session_state.get("desk_calibration", (None, None))
        if saved_key == calibration_key and sample:
            kpis([metric("Outcomes", amount(sample.get("forecasts"), 0)), metric("68% coverage", ratio_pct(sample.get("coverage_68"))),
                  metric("95% coverage", ratio_pct(sample.get("coverage_95"))), metric("Brier", amount(sample.get("brier_score"), 3), "50/50 baseline 0.250")], small=True)


def money_map(ctx):
    a, b, c = st.columns(3)
    universe = a.selectbox("Map view", ["Market leaders", "Sectors", "Watchlist"], key="map_universe")
    direction = b.selectbox("Flow direction", ["Both", "Bullish", "Bearish"], key="map_side")
    minimum = c.number_input("Minimum absolute net premium ($)", min_value=0, max_value=1_000_000_000,
                             value=0, step=250_000, key="map_min")
    rows = {}
    note = "All DTE · latest Tradytics single-stock flow leaders"
    feeds = []
    pending = []
    if universe == "Watchlist":
        value = st.text_input("Up to eight tickers, separated by commas", value="SPY, SPX, QQQ, NVDA, TSLA, AMD, AVGO, META",
                              max_chars=115, key="map_watchlist")
        try:
            tickers = list(dict.fromkeys(ticker_symbol(part) for part in value.split(",") if part.strip()))
        except DataError as exc:
            st.info(str(exc))
            return
        if len(tickers) > 8:
            st.info("Use eight or fewer tickers to keep the map responsive.")
            return
        for ticker in tickers:
            feed = ctx.timeline(symbol=ticker)
            opened, _ = rth_bounds(ctx.session)
            complete = (feed.rows and time_or_none(feed.rows[0]["time"]) == opened and
                        all(utc(b["time"]) - utc(a["time"]) == FIVE_MINUTES for a, b in zip(feed.rows, feed.rows[1:])))
            net = net_sum(feed.rows, "net_premium_usd") if complete and not feed.error else None
            if net is None:
                pending.append(ticker)
            else:
                rows[ticker] = {"label": ticker, "net": net, "as_of": (utc(feed.rows[-1]["time"]) + FIVE_MINUTES).isoformat()}
        # Comparable tiles share the same as-of time.
        if rows:
            newest = max(row["as_of"] for row in rows.values())
            pending.extend(key for key, row in rows.items() if row["as_of"] != newest)
            rows = {key: row for key, row in rows.items() if row["as_of"] == newest}
            note = f"{ctx.scope} · watchlist through {et_time(newest)}"
        else:
            note = f"{ctx.scope} · watchlist"
    else:
        by = "sector" if universe == "Sectors" else "symbol"
        sides = ["bullish", "bearish"] if direction == "Both" else [direction.lower()]
        for side in sides:
            feed = ctx.get("leaders", by=by, direction=side, limit=35)
            feeds.append(feed)
            for row in feed.rows:
                label, net = row.get(by), numeric(row.get("net_premium_usd"))
                if isinstance(label, str) and label and net is not None:
                    rows[label] = {"label": label, "net": net, "multiple": numeric(row.get("premium_vs_average")),
                                   "gross": numeric(row.get("premium_usd"))}
    display = sorted((row for row in rows.values() if row["net"] and abs(row["net"]) >= minimum
                      and (direction == "Both" or (row["net"] > 0) == (direction == "Bullish"))),
                     key=lambda row: abs(row["net"]), reverse=True)
    panel_header("Money map", "money_map", "money_map", note)
    if pending:
        st.caption("Waiting for comparable session intervals for " + ", ".join(pending) + ".")
    for feed in feeds:
        if feed.error or feed.loading:
            empty(feed)
    if not display:
        empty(message="No map tiles match these filters.")
        return
    kpis([metric("Bullish net shown", compact(sum(row["net"] for row in display if row["net"] > 0), currency=True), "Displayed tiles"),
          metric("Bearish net shown", compact(sum(row["net"] for row in display if row["net"] < 0), currency=True), "Displayed tiles"),
          metric("Tiles", str(len(display)), universe)], small=True)
    plot(money_map_figure(display, universe), "money_map_chart")
    if feeds:
        feed_stamp("leaders", feeds[0], ctx)
    with st.expander("See the numbers"):
        data = [{"Name": row["label"], "Net premium": row["net"], "Total premium": row.get("gross"),
                 "Premium vs usual (×)": row.get("multiple")} for row in display]
        table(data)
        csv_export(data, ctx.title("money_map.csv"), "money_map_download")


def trade_plan(ctx):
    read = read_workspace(ctx)
    plan = read["plan"]
    panel_header("Setup checklist", "readiness", "trade_plan", f"{ctx.ticker} · {ctx.scope}")
    momentum = plan["flow"].get("momentum")
    kpis([metric("Setup", plan["state"], "Current 5m observation"),
          metric("Bias", plan["bias"], "Price and net options flow"),
          metric("Readiness", f"{plan['score']}/100" if plan["score"] is not None else "—", "Rule-based evidence points"),
          metric("Flow momentum", compact(momentum, currency=True, signed=True), "Change in average 5m net flow", momentum)], small=True)
    if read["reasons"]:
        st.info(" ".join(read["reasons"]))
    elif plan["bias"] == "Mixed":
        st.info("Price and options flow disagree. Wait for them to agree.")
    if plan["agreement"] is not None:
        st.caption(f"Directional agreement {plan['agreement']}/2 · price and options flow.")
    if plan["state"] in ("Ready", "Triggered"):
        up = plan["direction"] > 0
        price, invalidation = amount(plan["trigger"]), amount(plan["invalidation"])
        if plan["state"] == "Triggered":
            st.write(f"Price closed {'above' if up else 'below'} **{price}** and net options flow agreed.")
        else:
            st.write(f"{'Upward' if up else 'Downward'} setup. Watch for a completed 5-minute close {'above' if up else 'below'} **{price}**.")
        st.caption(f"A close {'below' if up else 'above'} {invalidation} puts price back inside the prior range. Review the next one to three closes.")
    elif plan["state"] in ("Reversal watch", "Invalidated"):
        st.write("Price returned inside the prior range. Wait for another completed candle to confirm the direction.")
    if plan["divergence"]:
        st.caption("The latest price change and signed options premium are pointing in opposite directions.")
    a, b = st.columns(2)
    with a.container(border=True):
        panel_header("What agrees?", "readiness", "plan_evidence")
        table([{"Check": name, "Reading": value} for name, value in plan["evidence"]], height=212)
        if plan["points"]:
            with st.expander("How the score adds up"):
                table([{"Rule": name, "Points": value} for name, value in plan["points"]], height=245)
    with b.container(border=True):
        panel_header("Nearby price levels", "levels", "plan_levels")
        levels, dark = level_inputs(ctx, read, show_expected=True)
        spot = positive(ctx.quote().get("price"))
        nearby = sorted(levels, key=lambda level: abs(level.price - spot) if spot else level.price)
        table([{"Level": level.label, "Price": level.price, "Distance %": 100 * (level.price / spot - 1) if spot else None}
               for level in nearby[:8]], height=245)
        if spot and plan["trigger"]:
            further = [level for level in nearby if (level.price - spot) * plan["direction"] > .0002 * spot
                       and (level.price - plan["trigger"]) * plan["direction"] > .0002 * spot]
            if further:
                st.caption(f"Next level in the setup direction is {further[0].label.lower()} at {amount(further[0].price)}.")
        if dark.snapshot:
            feed_stamp("dark_levels", dark, ctx)
    with st.container(border=True):
        panel_header("Time of day", "timing", "plan_timing")
        window = session_window(read["candles"], ctx.now, ctx.session)
        st.write(window["phase"])
        if window["typical_move"] is not None:
            st.caption(f"Typical absolute move in this 30m slot: {window['typical_move']:.2%} · median of {window['sample']} prior sessions.")
        else:
            st.caption("Need three complete prior 30-minute windows to compare movement at this time.")
    audit = {"schema": 1, "ticker": ctx.ticker, "expiry": ctx.expiry, "session": str(ctx.session), "demo": ctx.preview,
             "price_source": ctx.price_provider, "as_of_utc": read["price_stamp"].isoformat() if read["price_stamp"] else None,
             "rules_version": "2.1", "regime": read["regime"], "setup": plan, "waiting_reasons": read["reasons"]}
    st.download_button("Download setup evidence (JSON)", json.dumps(audit, indent=2, allow_nan=False),
                       file_name=ctx.title("setup_evidence.json"), mime="application/json", key="export_trade_plan")


def scanner(ctx):
    sort_fields = {"Options premium": "options_premium_usd", "Bullish net premium": "options_net_premium_usd",
                   "Bearish net premium": "options_net_premium_usd", "Premium vs usual": "options_premium_relative",
                   "Relative share volume": "relative_volume", "IV rank": "iv_rank",
                   "IV change": "iv_change", "Dark-pool value": "dark_pool_value_usd"}
    a, b, c = st.columns(3)
    metric_name = a.selectbox("Rank by", list(sort_fields), key="scanner_sort")
    kind = b.selectbox("Security type", ["All", "Stock", "ETF", "ADR"], key="scanner_kind")
    minimum = c.number_input("Minimum options premium ($)", min_value=0, max_value=1_000_000_000,
                             value=0, step=250_000, key="scanner_min")
    fields = ("symbol,sector,kind,date,price,change_pct,relative_volume,options_premium_usd,options_net_premium_usd,"
              "options_premium_relative,iv_rank,iv_change,dark_pool_value_usd")
    result = ctx.get("screener", sort=sort_fields[metric_name], order="asc" if metric_name == "Bearish net premium" else "desc",
                     kind=None if kind == "All" else kind.lower(), fields=fields, limit=60,
                     min_options_premium_usd=minimum or None)
    panel_header("Market scanner", "scanner", "scanner_results", "Provider snapshot · all DTE")
    rows = [{"Ticker": row.get("symbol"), "Sector": row.get("sector"), "Price": numeric(row.get("price")),
             "Change %": numeric(row.get("change_pct")), "Relative volume": numeric(row.get("relative_volume")),
             "Options premium": numeric(row.get("options_premium_usd")), "Net premium": numeric(row.get("options_net_premium_usd")),
             "Premium vs usual": numeric(row.get("options_premium_relative")), "IV rank / 100": numeric(row.get("iv_rank")),
             "IV change (pts)": numeric(row.get("iv_change")), "Dark-pool value": numeric(row.get("dark_pool_value_usd"))}
            for row in result.rows]
    if rows:
        table(rows, height=475)
        csv_export(rows, ctx.title("scan.csv"), "download_scan")
        st.caption("Enter a ticker from the table in the symbol box to open its charts.")
    else:
        empty(result)
    if result.snapshot:
        meta = result.snapshot.meta
        st.caption(f"Scan built {et_time(meta.get('scan_as_of'))} · quotes {et_time(meta.get('quote_as_of'))}")
    feed_stamp("screener", result, ctx)


def tradingview_page(ctx):
    read = read_workspace(ctx, include_flow=False)
    panel_header("Generate TradingView levels", "tradingview", "tradingview", f"{ctx.ticker} · {ctx.scope}")
    a, b = st.columns(2)
    groups = a.multiselect("Layers to export", ["Support / resistance", "Dealer levels & GEX", "Dark-pool levels", "Expected move"],
                           default=["Support / resistance", "Dealer levels & GEX", "Dark-pool levels"], key="tv_layers")
    strikes = b.slider("Strong GEX strikes near spot", min_value=0, max_value=12, value=5, key="tv_strikes")
    levels, dark = level_inputs(ctx, read, strikes=strikes, dark_count=5 if "Dark-pool levels" in groups else 0,
                                show_expected="Expected move" in groups)
    categories = {"support": "Support / resistance", "resistance": "Support / resistance",
                  "dealer": "Dealer levels & GEX", "gex_pos": "Dealer levels & GEX", "gex_neg": "Dealer levels & GEX",
                  "dark": "Dark-pool levels", "expected": "Expected move"}
    if ctx.paused or read["bars"].error or feed_has_delay(read["bars"]):
        levels = [level for level in levels if level.kind not in ("support", "resistance")]
    levels = [level for level in levels if categories[level.kind] in groups]
    options_key = (ctx.ticker, ctx.scope, ctx.session, ctx.preview, tuple(groups), strikes, ctx.price_provider)
    if "Dark-pool levels" in groups and ctx.ticker in INDICES:
        st.caption(f"{ctx.ticker} has no off-exchange share prints. Open SPY or QQQ to export their own levels.")
    elif "Dark-pool levels" in groups and dark.error:
        st.caption(str(dark.error))
    if "Dealer levels & GEX" in groups:
        feed_stamp("exposure", read["gamma_feed"], ctx)
    if levels:
        table([{"Level": level.label, "Price": level.price, "Source time (ET)": et_time(level.as_of),
                "Amount / exposure": level.strength} for level in levels], height=300)
    if st.button("Generate level snapshot", type="primary", disabled=not levels, key="generate_tv"):
        payload = export_payload(ctx.ticker, ctx.session, ctx.expiry, ctx.now, levels, preview=ctx.preview,
                                 price_source=ctx.price_provider,
                                 gamma=read["gamma"].get("total") if any(level.kind in ("dealer", "gex_pos", "gex_neg") for level in levels) else None)
        st.session_state["tv_export"] = (options_key, payload)
    saved_key, payload = st.session_state.get("tv_export", (None, None))
    if saved_key != options_key:
        if not levels:
            st.info("Waiting for price levels under these filters.")
        return
    code = pine_script(payload)
    st.caption(f"Snapshot generated {et_time(payload['as_of_utc'])}. Regenerate it when you want newer levels.")
    a, b = st.columns(2)
    a.download_button("Download Pine indicator", code, file_name=ctx.title("levels.pine"), mime="text/plain", key="tv_pine")
    b.download_button("Download levels JSON", json.dumps(payload, indent=2, allow_nan=False), file_name=ctx.title("levels.json"),
                      mime="application/json", key="tv_json")
    st.write(f"Open {ctx.ticker} in TradingView. Paste the code into Pine Editor, save it, and choose **Add to chart**.")
    st.code(code, language="text")


@st.cache_data(show_spinner=False, max_entries=8, ttl=300)
def replay_report(buckets, flow, day):
    return replay_setups(buckets, flow, day)


def replay_page(ctx):
    a, b = st.columns(2)
    mode = a.selectbox("Replay source", ["Large options prints", "Range-break triggers", "Tradytics signals"], key="replay_type")
    day = b.date_input("Replay session", value=ctx.session, min_value=date(2025, 3, 31), max_value=ctx.session, key="replay_day")
    if not rth_bounds(day):
        st.info("Choose a regular market session.")
        return
    expiry = expiration_date(day, ctx.dte)
    panel_header("What happened afterward?", "replay", "history_replay", f"{day} · {expiry or 'All DTE'} · underlying price moves")
    candles, bars = ctx.history(session=day)
    if mode == "Range-break triggers":
        flow = ctx.timeline(session=day)
        events = replay_report(chart_buckets(candles, 5), flow.rows, day)
        feed_stamp("timeline", flow, ctx)
        st.caption("Price and flow rules run at each completed 5-minute close. Replay uses the provider's finalized flow, which can differ from the live feed.")
    elif mode == "Tradytics signals":
        signals = ctx.get("signals", symbol=ctx.ticker, date=str(day), limit=250)
        rows = filter_expiration(same_ticker(signals.rows, ctx.ticker), day, ctx.dte)
        events = [{"stamp": stamp, "spot": row.get("spot"), "directional": 1 if row.get("type") == "call" else -1,
                   "label": str(field(row, "pattern", "source") or "Signal")}
                  for row in rows if row.get("type") in ("call", "put") and (stamp := time_or_none(row.get("fired_at")))]
        feed_stamp("signals", signals, ctx)
    else:
        minimum = st.number_input("Minimum replay premium ($)", min_value=25_000, max_value=100_000_000,
                                  value=default_premium(ctx.ticker), step=25_000, key=f"replay_premium_{ctx.ticker}")
        orders, tape = ctx.tape(session=day, min_premium=minimum, limit=500)
        events = [{**row, "label": f"{row['type'].title()} · {row['side_label']} · {compact(row['premium_usd'], currency=True)}"}
                  for row in orders if row.get("side_checked") is True and row["directional"] and row.get("condition") not in ("spread", "complex")]
        feed_stamp("flow", tape, ctx)
        st.caption("Up to 500 recent matching prints. Direction uses checked sides and excludes flagged spreads and complex trades.")
    outcomes = signed_moves(candles, events, day, ctx.now)
    if outcomes:
        for label in ("15m", "30m", "60m"):
            values = [row[label] * row["lean"] for row in outcomes if row[label] is not None and row["lean"]]
            favorable = sum(value > 0 for value in values)
            st.caption(f"{label} · {favorable} of {len(values)} moves matched the event direction.")
        table([{"Time (ET)": et_time(row["time"], short=True), "Event": row["label"], "Start price": row["spot"],
                "Regime": row["regime"], "15m %": row["15m"], "30m %": row["30m"],
                "60m %": row["60m"], "To close %": row["Close"]}
               for row in outcomes], height=380)
        csv_export(outcomes, ctx.title("event_replay.csv"), "replay_export")
    else:
        empty(bars, "No matching events with a usable underlying reference price.")
    st.caption("Returns measure the underlying. Empty outcomes are outside the session, still forming, or missing a close.")
    feed_stamp("bars", bars, ctx)


def history_page(ctx):
    mode = st.segmented_control("History workspace", ["Daily charts", "Event replay"], default="Daily charts", key="history_workspace")
    if mode == "Event replay":
        replay_page(ctx)
        return
    window = st.selectbox("History window (calendar days)", [30, 60, 90, 180, 365, 540], index=2, key="history_window")
    start = str(ctx.day - timedelta(days=window))
    view = st.segmented_control("History series", ["Exposure", "Volatility", "Dark pool"], default="Exposure", key="history_view") or "Exposure"
    with st.container(border=True):
        panel_header(f"{view} history", "history", "history_chart", f"{ctx.ticker} · daily provider observations · all DTE")
        exposure = st.selectbox("Exposure series", ["Net Gamma", "Net Delta"], key="history_exposure") if view == "Exposure" else None
        name, columns, fmt = {
            "Exposure": ("exposure_history", [("Net Delta", "net_delta", BLUE, 1)] if exposure == "Net Delta" else
                         [("Net Gamma", "net_gamma_usd", TEAL, 1)], "~s"),
            "Volatility": ("volatility_history", [("30D ATM IV", "atm_iv_30d", TEAL, 100), ("20D realized", "realized_vol_20d", BLUE, 100)], ".1f"),
            "Dark pool": ("dark_daily", [("Off-exchange notional", "dark_notional_usd", VIOLET, 1)], "~s"),
        }[view]
        feed = ctx.get(name, symbol=ctx.ticker, **{"from": start})
        rows = sorted(same_ticker(feed.rows, ctx.ticker), key=lambda r: str(r.get("date")))
        render_chart(series_figure(rows, "date", columns, y_format=fmt, ticksuffix="%" if view == "Volatility" else None),
                     "daily_history_chart", feed)
        feed_stamp(name, feed, ctx)
        if view == "Exposure":
            st.caption("Share equivalents" if exposure == "Net Delta" else "Dollars of delta per 1% spot move")
        fields = {"date", "close", "net_gamma_usd", "net_delta", "atm_iv_30d", "iv_rank", "iv_percentile", "realized_vol_20d",
                  "dark_notional_usd", "exchange_notional_usd", "prints", "rank"}
        csv_export([{key: val for key, val in row.items() if key in fields} for row in rows], ctx.title("daily_history.csv"), "history_export")


def health_page(ctx):
    with st.container(border=True):
        panel_header("Feed diagnostics", "health", "health", "Provider timestamps, access scopes and HTTP receipt times")
        queries = [
            (ctx.quote_endpoint, ctx.quote_feed()), ("bars", ctx.get("bars", symbol=ctx.ticker, timeframe="1m", limit=10)),
            ("timeline", ctx.timeline()),
            ("flow", ctx.tape(limit=10)[1]),
            ("expirations", ctx.get("expirations", symbol=ctx.ticker)),
            ("exposure", ctx.exposure("gamma")), ("volatility", ctx.get("volatility", symbol=ctx.ticker)),
            ("dark_prints", ctx.get("dark_prints", symbol=ctx.ticker, venue="dark_pool", limit=10)),
            ("signals", ctx.get("signals", symbol=ctx.ticker, limit=10)),
        ]
        rows = []
        for name, feed in queries:
            label, stamp = status(name, feed, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)
            entry = ENDPOINTS[name]
            rows.append({"Dataset": entry.label, "Scope": entry.scope, "Status": label, "Source time (ET)": stamp,
                         "Received (ET)": et_time(feed.snapshot.received_at) if feed.snapshot else "—",
                         "Cadence": f"{entry.refresh_seconds}s request", "Note": str(feed.error) if feed.error else ""})
        table(rows, height=330)
        if ctx.config_error:
            st.warning(ctx.config_error)
        if ctx.store and not ctx.preview:
            budget = ctx.store.client.budget()
            st.caption(f"Tradytics local budget {budget['calls_last_minute']} / {budget['local_limit']} calls in the past minute · cooldown {budget['cooldown_seconds']}s.")
        if ctx.webull or ctx.webull_error:
            st.caption(f"Webull · {ctx.webull.get('connection', 'Unavailable')} · {ctx.webull_error or ctx.webull_quality.get('status', 'Waiting')}")
        st.download_button("Download diagnostics", json.dumps({"ticker": ctx.ticker, "preview": ctx.preview, "feeds": rows}, indent=2),
                           file_name=ctx.title("data_health.json"), mime="application/json", key="download_health")
    with st.container(border=True):
        panel_header("Connect your data", "health", "health_connection")
        st.write("Set your Tradytics key in Streamlit Secrets or your local .env file. Enable the market, options, darkpool, signals and scanner scopes for the pages you use.")
        st.code("TRADYTICS_API_KEY=your_key_here", language="ini")
        st.caption("Quotes and models use the provider source time. Tradytics quote snapshots typically update about every 30 seconds and positioning models about every five minutes.")


PAGES = {"Overview": overview, "Options Flow": options_flow, "Money Map": money_map, "Positioning": positioning,
         "Volatility": volatility_page, "Dark Pool": dark_pool, "Trade Plan": trade_plan, "Signals": signals_page,
         "Forecast": forecast_page, "Scanner": scanner, "History": history_page, "TradingView": tradingview_page,
         "Data Health": health_page}
