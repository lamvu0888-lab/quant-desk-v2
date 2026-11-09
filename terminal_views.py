"""Terminal views. Fetch only the feeds needed by the page on screen."""
from __future__ import annotations

import json
from dataclasses import dataclass, field as default_field
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from data_utils import DataError, field, utc
from market_session import ET, session_state
from monte_carlo import forecast, walk_forward
from terminal_charts import dark_figure, exposure_figure, flow_figure, forecast_figure, price_figure, series_figure
from terminal_data import (INDICES, chart_buckets, display_exposure_unit, et_time, exposure_obj, flow_side, numeric,
                           positive, price_bars, quote_row, same_ticker, session_rows, snapshot_forecast_gate,
                           status, time_or_none)
from terminal_style import (AMBER, BLUE, RED, TEAL, VIOLET, amount, compact, empty, esc, feed_stamp, kpis,
                            panel_header, plot, ratio_pct, signed_pct)
from tradytics_feed import ENDPOINTS, Feed, FeedError

SCOPE_NAMES = {None: "All expiries", 0: "0DTE", 7: "≤7 DTE", 30: "≤30 DTE"}
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

    def history(self, daily=False):
        if self.price_provider == "Webull" and not daily:
            return (self.webull.get("history") or {}).get("candles", []), Feed()
        result = self.get("bars", symbol=self.ticker, timeframe="1d" if daily else "1m", limit=180 if daily else 5000,
                          **{"from": str(self.day - timedelta(days=280 if daily else 8))})
        return price_bars(result, self.ticker, self.now, daily=daily), result

    def exposure(self, greek="gamma", by="strike", expiry=None):
        return self.get("exposure", symbol=self.ticker, greek=greek, by=by, dte=None if expiry else self.dte, expiry=expiry)

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


def price_panel(ctx, gamma_feed):
    with st.container(border=True):
        panel_header("Price action", "price", "overview_price", "Regular session · completed candles · New York time")
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        timeframe = c1.segmented_control("Candle timeframe", ["1m", "3m", "5m", "15m", "1h", "1D"], default="5m",
                                         key="overview_timeframe", label_visibility="collapsed") or "5m"
        overlays = c2.toggle("Overlays", value=True, key="overview_overlays")
        daily = timeframe == "1D"
        candles, feed = ctx.history(daily=daily)
        rows = candles if daily else chart_buckets(candles, {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "1h": 60}[timeframe])
        levels = []
        if overlays and not daily:
            levels.append(("Prev close", positive(ctx.quote().get("previous_close")), BLUE))
            badge, _ = status("exposure", gamma_feed, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)
            if badge in ("DEMO", "RECENT"):
                dealer = option_levels(exposure_obj(gamma_feed, ctx.ticker, "gamma"))
                levels.extend((label, positive(dealer.get(key)), color) for key, label, _, color in LEVELS[:3])
        fig = price_figure(rows, ctx.ticker, timeframe, levels=levels, overlays=overlays) if rows else None
        using_webull = ctx.price_provider == "Webull" and not daily
        render_chart(fig, "price_action_chart", None if using_webull else feed,
                     ctx.webull_error or "Waiting for completed candles in this timeframe.")
        if using_webull:
            st.caption(f"Webull · {et_time((ctx.webull.get('history') or {}).get('last_bar_utc'))} · Session VWAP requires provider bar VWAP.")
        else:
            feed_stamp("bars", feed, ctx)
            if rows:
                stamp = utc(rows[-1]["time_utc"])
                last = stamp.date().isoformat() if daily else et_time(stamp)
                st.caption(f"Last completed {timeframe} bar started {last}.")
        if rows:
            with st.expander("Candle export"):
                csv_export(rows, ctx.title(f"{timeframe}_candles.csv"), "export_price_candles")


def overview(ctx):
    quote = ctx.quote()
    gamma_feed, vanna_feed, charm_feed = (ctx.exposure(greek) for greek in ("gamma", "vanna", "charm"))
    gamma = exposure_obj(gamma_feed, ctx.ticker, "gamma")
    vanna = exposure_obj(vanna_feed, ctx.ticker, "vanna")
    charm = exposure_obj(charm_feed, ctx.ticker, "charm")
    timeline = ctx.get("timeline", symbol=ctx.ticker, dte=ctx.dte, interval="5m")
    flow = flow_rows(ctx, timeline)
    vol = ctx.get("volatility", symbol=ctx.ticker)
    spot, change = positive(quote.get("price")), numeric(quote.get("change_pct"))
    net = net_sum(flow, "net_premium_usd")
    scope = SCOPE_NAMES[ctx.dte]
    panel_header("Market pulse", "overview", "overview_kpis", f"{scope} for options figures")
    kpis([
        metric("Underlying", amount(spot), f"{signed_pct(change)} · {ctx.price_provider}", change),
        metric("Net GEX", compact(gamma.get("total"), currency=True, signed=True), f"{scope} · $ / 1% move", gamma.get("total")),
        metric("Net Vanna", compact(vanna.get("total"), currency=vanna.get("unit") == "usd_per_vol_point", signed=True),
               f"{scope} · {display_exposure_unit(vanna)}", vanna.get("total")),
        metric("Net Charm", compact(charm.get("total"), signed=True), f"{scope} · {display_exposure_unit(charm)}", charm.get("total")),
        metric("Options net flow", compact(net, currency=True, signed=True), f"{scope} · latest session", net),
        metric("30D ATM IV", ratio_pct(vol.obj.get("atm_iv_30d")), f"Rank {ratio_pct(vol.obj.get('iv_rank'))} · Tradytics"),
    ])
    with st.expander("Snapshot times"):
        for name, label, feed in (("exposure", "Gamma", gamma_feed), ("exposure", "Vanna", vanna_feed),
                                  ("exposure", "Charm", charm_feed), ("timeline", "Options flow", timeline),
                                  ("volatility", "Implied volatility", vol)):
            badge, stamp = status(name, feed, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)
            st.caption(f"{label} · {badge} · {stamp}")
    left, right = st.columns([2.55, 1], gap="medium")
    with left:
        price_panel(ctx, gamma_feed)
    with right:
        with st.container(border=True):
            panel_header("Dealer levels", "levels", "overview_levels", f"Gamma concentrations · {scope}")
            level_list(gamma, spot)
            feed_stamp("exposure", gamma_feed, ctx)
        with st.container(border=True):
            panel_header("Session net flow", "net_flow", "overview_flow", "Cumulative directional premium")
            render_chart(flow_figure(flow, compact=True), "overview_flow_spark", timeline)
            feed_stamp("timeline", timeline, ctx)
    col1, col2 = st.columns([2, 1], gap="medium")
    with col1.container(border=True):
        panel_header("Gamma around spot", "gamma", "overview_gamma", f"Tradytics strike profile · {scope}")
        rows = gamma_feed.rows
        if spot:
            rows = [row for row in rows if (strike := positive(row.get("strike"))) and abs(strike / spot - 1) <= .02]
        render_chart(exposure_figure(rows, "strike", "$ per 1% spot move", spot, positive(option_levels(gamma).get("flip")), compact=True),
                     "overview_gamma_chart", gamma_feed)
    with col2.container(border=True):
        breadth = ctx.get("breadth")
        panel_header("Market participation", "breadth", "overview_breadth", "Liquid US stocks")
        if breadth.obj:
            for label, key, color in (("Advancing", "up", "up"), ("Declining", "down", "down"), ("Counted", "total", "")):
                st.markdown(f'<div class="qd-level"><span><i class="qd-dot {color}"></i>{label}</span><strong>{amount(breadth.obj.get(key), 0)}</strong></div>',
                            unsafe_allow_html=True)
            feed_stamp("breadth", breadth, ctx)
        else:
            empty(breadth)


def tape_table(rows):
    return [
        {"Time (ET)": et_time(row.get("time"), short=True), "Ticker": row.get("symbol"), "Type": row.get("type"),
         "Strike": numeric(row.get("strike")), "Expiry": row.get("expiry"), "DTE": numeric(row.get("dte")),
         "Side": flow_side(row), "NBBO": "Checked" if row.get("side_checked") is True else "Provisional" if row.get("side_checked") is False else "—",
         "Kind": row.get("kind"), "Premium": numeric(field(row, "premium_usd", "premium")), "Size": numeric(row.get("size")),
         "Condition": row.get("condition"), "Opening flag": row.get("opening")}
        for row in rows
    ]


def options_flow(ctx):
    a, b, c = st.columns([1, 1, 1])
    market = a.selectbox("Flow universe", [ctx.ticker, "Market · single-stock net flow"], key="flow_universe") != ctx.ticker
    interval = b.selectbox("Aggregation", ["1m", "5m", "15m", "1h"], index=1, key="flow_interval")
    date = c.date_input("Session date (empty = latest)", value=None, max_value=ctx.day, key="flow_date")
    timeline = ctx.get("timeline", symbol=None if market else ctx.ticker, interval=interval, date=str(date) if date else None, dte=ctx.dte)
    rows = flow_rows(ctx, timeline, market)
    totals = [net_sum(rows, key) for key in ("net_premium_usd", "call_premium_usd", "put_premium_usd", "prints")]
    kpis([metric(label, compact(total, currency=True, signed=True), "Loaded session intervals", total)
          for label, total in zip(("Directional net", "Net calls", "Net puts"), totals)] +
         [metric("Print count", amount(totals[-1], 0), "Provider aggregate")], small=True)
    with st.container(border=True):
        panel_header("Net premium timeline", "net_flow", "flow_timeline", "Bars show each interval · line shows cumulative net")
        components = st.toggle("Show cumulative call and put components", value=True, key="flow_components")
        render_chart(flow_figure(rows, components=components), "full_flow_timeline", timeline)
        feed_stamp("timeline", timeline, ctx)
        csv_export(rows, ctx.title("net_flow.csv"), "export_net_flow")
    with st.container(border=True):
        panel_header(f"{ctx.ticker} options tape", "tape", "flow_tape", "Most recent 250 prints under these filters")
        x, y, z, w = st.columns(4)
        min_premium = x.number_input("Minimum premium ($)", min_value=0, max_value=100_000_000, value=25000, step=25000, key="flow_min")
        option_type = y.selectbox("Contract", ["All", "Call", "Put"], key="flow_type")
        kind = z.selectbox("Print kind", ["All", "Sweep", "Block", "Split", "Single"], key="flow_kind")
        side = w.selectbox("Inferred side", ["All", "Bought", "Sold", "Mid"], key="flow_side")
        prints = ctx.get("flow", symbol=ctx.ticker, min_premium=min_premium, dte=ctx.dte, limit=250,
                         type=None if option_type == "All" else option_type.lower(), kind=None if kind == "All" else kind.lower(),
                         side=None if side == "All" else side.lower(), date=str(date) if date else None)
        display = tape_table(same_ticker(prints.rows, ctx.ticker))
        if display:
            table(display, height=365, config={"Premium": st.column_config.NumberColumn(format="dollar")})
        else:
            empty(prints, "No options prints match these filters.")
        feed_stamp("flow", prints, ctx)
        csv_export(display, ctx.title("options_tape.csv"), "export_options_tape")


def chain_panel(ctx):
    with st.container(border=True):
        panel_header("Option chain", "chain", "chain", "First 250 contracts ordered by expiry and strike. Use filters for a narrower view.")
        a, b, c = st.columns(3)
        right = a.selectbox("Contract type", ["Both", "Call", "Put"], key="chain_type")
        expiry = b.date_input("Exact expiry", value=None, min_value=ctx.day, key="chain_expiry")
        near = c.toggle("Within 3% of spot", value=True, key="chain_near")
        spot = positive(ctx.quote().get("price"))
        chain = ctx.get("chain", symbol=ctx.ticker, limit=250, expiry=str(expiry) if expiry else None,
                        max_dte=None if expiry else ctx.dte, type=None if right == "Both" else right.lower(),
                        min_strike=round(spot*.97, 3) if near and spot else None, max_strike=round(spot*1.03, 3) if near and spot else None)
        display = [{"Contract": row.get("contract"), "Type": row.get("type"), "Expiry": row.get("expiry"), "Strike": numeric(row.get("strike")),
                    "Bid": numeric(row.get("bid")), "Ask": numeric(row.get("ask")), "Volume": numeric(row.get("volume")),
                    "OI": numeric(row.get("open_interest")), "IV": ratio_pct(row.get("iv")), "Delta": numeric(row.get("delta")),
                    "Gamma": numeric(row.get("gamma")), "Vega": numeric(row.get("vega")), "Theta": numeric(row.get("theta")),
                    "Quote (ET)": et_time(row.get("updated_at"))} for row in same_ticker(chain.rows, ctx.ticker)]
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
                 "Unit": display_exposure_unit(obj), "Scope": SCOPE_NAMES[ctx.dte], "As of": dealer_stamp[1], "Status": dealer_stamp[0]},
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
    view = st.segmented_control("Positioning view", ["Exposure", "Option chain", "Compare sources"], default="Exposure",
                                key="positioning_view", label_visibility="collapsed") or "Exposure"
    if view == "Option chain":
        chain_panel(ctx)
        return
    left, middle, right = st.columns([1.5, 1, 1.2])
    greek = (left.segmented_control("Greek", ["Gamma", "Vanna", "Charm", "Delta"], default="Gamma", key="positioning_greek") or "Gamma").lower()
    if view == "Compare sources":
        comparison_panel(ctx, greek, ctx.exposure(greek))
        return
    by = middle.selectbox("Group by", ["Strike", "Expiry"], key="positioning_by").lower()
    expiry = right.date_input("Exact expiry (optional)", value=None, min_value=ctx.day, key="positioning_expiry")
    feed = ctx.exposure(greek, by=by, expiry=str(expiry) if expiry else None)
    obj = exposure_obj(feed, ctx.ticker, greek)
    level_feed = feed if greek == "gamma" else ctx.exposure("gamma", expiry=str(expiry) if expiry else None)
    level_obj = exposure_obj(level_feed, ctx.ticker, "gamma")
    levels = option_levels(level_obj)
    spot = positive(obj.get("spot")) or positive(ctx.quote().get("price"))
    scope = str(expiry) if expiry else SCOPE_NAMES[ctx.dte]
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


def dark_pool(ctx):
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
    rows = same_ticker(result.rows, ctx.ticker)
    with st.container(border=True):
        panel_header("Pattern signals", "signals", "signals_list", f"{ctx.ticker} · provider alerts and latest contract marks")
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
                        table(tape_table(obj["prints"]), height=220)
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
        panel_header("Market context", "forecast", "forecast_context", f"Separate from the simulated paths · {SCOPE_NAMES[ctx.dte]}")
        flow_feed = ctx.get("timeline", symbol=ctx.ticker, interval="5m", dte=ctx.dte)
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


def history_page(ctx):
    window = st.selectbox("History window (calendar days)", [30, 60, 90, 180, 365, 540], index=2, key="history_window")
    start = str(ctx.day - timedelta(days=window))
    view = st.segmented_control("History series", ["Exposure", "Volatility", "Dark pool"], default="Exposure", key="history_view") or "Exposure"
    with st.container(border=True):
        panel_header(f"{view} history", "history", "history_chart", f"{ctx.ticker} · daily provider observations")
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
            ("timeline", ctx.get("timeline", symbol=ctx.ticker, interval="5m", dte=ctx.dte)),
            ("flow", ctx.get("flow", symbol=ctx.ticker, limit=10, dte=ctx.dte)),
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
        st.write("Set your Tradytics key in the local .env file and restart the launcher. Enable the market, options, darkpool and signals scopes for the pages you use.")
        st.code("TRADYTICS_API_KEY=your_key_here", language="ini")
        st.caption("Quotes and models use the provider source time. Tradytics quote snapshots typically update about every 30 seconds and positioning models about every five minutes.")


PAGES = {"Overview": overview, "Options Flow": options_flow, "Positioning": positioning, "Volatility": volatility_page,
         "Dark Pool": dark_pool, "Signals": signals_page, "Forecast": forecast_page, "History": history_page, "Data Health": health_page}
