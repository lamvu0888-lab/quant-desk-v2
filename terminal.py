"""Quant Desk 2.0. Single local dashboard for Tradytics and the Webull model."""
from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime, timezone

import streamlit as st
from dotenv import dotenv_values

from data_utils import DataError
from live_engine import LiveEngine, data_quality
from market_session import session_state
from settings import ROOT, Settings
from terminal_data import EXPIRY_DTE, EXPIRY_LABELS, INDICES, et_time, numeric, quote_row, status, ticker_symbol
from terminal_demo import DEMO_AT, DemoStore
from terminal_learn import learn_page
from terminal_style import STYLE, amount, esc, signed_pct, tag
from terminal_views import Desk, PAGES
from tradytics_feed import FeedError, FeedStore, TradyticsClient

UTC = timezone.utc
PAGES_LABEL = {
    "Overview": ("Market overview", "Price, dealer positioning and options flow for your selected market.", "◈"),
    "Options Flow": ("Options flow", "Net premium through the session and the prints behind it.", "≋"),
    "Positioning": ("Dealer positioning", "Gamma, Vanna, Charm and Delta by strike or expiration.", "▥"),
    "Volatility": ("Volatility", "Implied and realized volatility, skew, term structure and expected move.", "∿"),
    "Dark Pool": ("Dark pool", "Reported off-exchange share prints and their price concentrations.", "◉"),
    "Signals": ("Tradytics signals", "Flow pattern alerts, contract marks and provider performance.", "◇"),
    "Forecast": ("Monte Carlo forecast", "Short-horizon price distributions from completed market candles.", "⋰"),
    "History": ("History", "Daily exposure, volatility and off-exchange notional by session.", "◷"),
    "Learn": ("Learn", "A practical guide to the tools on your desk.", "▤"),
    "Data Health": ("Data health", "Connection, permissions and source timing across the desk.", "⊙"),
}


@st.cache_resource
def resources():
    return {"lock": threading.RLock(), "store": None, "token": None, "webull": None}


@st.cache_resource(max_entries=8)
def demo(ticker):
    return DemoStore(ticker)


def config_value(local, name, default=""):
    return str(local.get(name) or os.getenv(name) or default).strip()


def tradytics_store(key, auth):
    registry = resources()
    token = (hashlib.sha256(key.encode()).digest(), auth)
    with registry["lock"]:
        if registry["token"] != token:
            replacement = FeedStore(TradyticsClient(key, auth))
            if registry["store"]:
                registry["store"].close()
            registry["token"], registry["store"] = token, replacement
        return registry["store"]


def webull_snapshot(ticker, enabled, paused):
    registry = resources()
    with registry["lock"]:
        worker = registry["webull"]
        if not enabled:
            if worker:
                worker.stop()
            return {}, {}, None
        if ticker in INDICES:
            if worker:
                worker.stop()
            return {}, {}, "Use the Tradytics price feed for this index."
        try:
            settings = Settings.load()
            if worker and (worker.ticker != ticker or worker.settings != settings):
                worker.stop()
                if worker.alive():
                    return {}, {}, "Switching Webull to the selected ticker…"
                worker = None
            if worker is None:
                worker = LiveEngine(ticker, settings)
                registry["webull"] = worker
            if paused:
                worker.stop()
            elif not worker.alive() and worker.snapshot().get("connection") in ("DISCONNECTED", "STOPPED"):
                worker.start()
            state = worker.snapshot()
            return state, data_quality(state, settings), state.get("quote_error")
        except DataError as exc:
            return {}, {}, str(exc)


def choose_ticker(symbol):
    st.session_state["desk_ticker"] = symbol
    st.session_state["desk_symbol_input"] = symbol
    if symbol in INDICES:
        st.session_state["desk_price_provider"] = "Tradytics"


def open_symbol():
    try:
        choose_ticker(ticker_symbol(st.session_state.get("desk_symbol_input", "")))
        st.session_state.pop("desk_symbol_error", None)
    except DataError as exc:
        st.session_state["desk_symbol_error"] = str(exc)


def go_to(page):
    st.session_state["desk_page"] = page


def refresh():
    registry = resources()
    if registry["store"]:
        registry["store"].refresh()
    if registry["webull"]:
        registry["webull"].refresh()


def header(page):
    title, subtitle, _ = PAGES_LABEL[page]
    left, right = st.columns([2.7, 1.2], vertical_alignment="bottom")
    left.markdown('<div class="qd-page"><div><div class="qd-eyebrow">QUANT DESK / MARKET WORKSPACE</div>'
                  f'<h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div></div>', unsafe_allow_html=True)
    with right.form("command_symbol", border=False):
        a, b = st.columns([2.7, 1], vertical_alignment="center")
        a.text_input("Ticker", placeholder="SPY, SPX, NVDA…", max_chars=12,
                     label_visibility="collapsed", key="desk_symbol_input")
        b.form_submit_button("Open", type="primary", on_click=open_symbol, width="stretch")
    if st.session_state.get("desk_symbol_error"):
        st.warning(st.session_state["desk_symbol_error"])


def symbol_bar(ctx):
    quote = ctx.quote()
    if ctx.price_provider == "Webull":
        quote_badge = "VERIFIED QUOTE" if ctx.webull_quality.get("forecast_ready") else ctx.webull_quality.get("status", "UNAVAILABLE")
        stamp = et_time(quote.get("price_at"))
    else:
        quote_badge, stamp = status(ctx.quote_endpoint, ctx.quote_feed(), ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)
    session = session_state(ctx.now)["status"]
    mode = tag("DEMO · SYNTHETIC", "preview") if ctx.preview else tag("LIVE WORKSPACE")
    symbol = f'<span class="qd-ticker">{esc(ctx.ticker)}</span><span class="qd-symbol-name">{esc(quote.get("name") or ctx.ticker)}</span>'
    label = "REGULAR SESSION" if session == "OPEN" else "PREMARKET" if session == "PREMARKET" else "SESSION CLOSED"
    q_tone = "preview" if ctx.preview else "good" if quote_badge in ("RECENT", "VERIFIED QUOTE") else "warn"
    st.markdown('<div class="qd-symbol-bar">' + symbol + '<div class="qd-spacer"></div>' + mode +
                tag(label) + tag(quote_badge, q_tone) + f'<span class="qd-symbol-name">{esc(stamp)}</span></div>',
                unsafe_allow_html=True)


def market_strip(ctx):
    index_feed = ctx.get("indices")
    if not index_feed.rows:
        return
    chunks = [f'<span class="qd-eyebrow">{"DEMO INDEX TAPE" if ctx.preview else "INDEX TAPE · TRADYTICS"}</span>']
    for name in ("SPX", "NDX", "RUT", "VIX"):
        quote = quote_row(index_feed, name)
        if not quote:
            continue
        n = numeric(quote.get("change_pct"))
        cls = "qd-positive" if n is not None and n > 0 else "qd-negative" if n is not None and n < 0 else ""
        quote_time = et_time(quote.get("time") or quote.get("price_at"))
        chunks.append(f'<span title="{esc(quote_time)}"><b>{name}</b>{amount(quote.get("price"))} '
                      f'<span class="{cls}">{signed_pct(n)}</span></span>')
    if index_feed.error:
        chunks.append(tag("CACHED", "warn"))
    st.markdown('<div class="qd-market-strip">' + ''.join(chunks) + '</div>', unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Quant Desk | Options Intelligence", page_icon="◈", layout="wide", initial_sidebar_state="auto")
    st.markdown(STYLE, unsafe_allow_html=True)
    local = dotenv_values(ROOT / ".env")
    key = config_value(local, "TRADYTICS_API_KEY")
    auth = config_value(local, "TRADYTICS_AUTH_MODE", "query").lower()
    webull_configured = bool(config_value(local, "WEBULL_APP_KEY") and config_value(local, "WEBULL_APP_SECRET"))
    config_error = None
    try:
        store = tradytics_store(key, auth)
    except FeedError as exc:
        store = None
        config_error = str(exc)
    if "desk_ticker" not in st.session_state:
        try:
            choose_ticker(ticker_symbol(config_value(local, "DEFAULT_TICKER", "SPY")))
        except DataError:
            choose_ticker("SPY")
    st.session_state.setdefault("desk_mode", "Live" if key or webull_configured else "Preview")
    st.session_state.setdefault("desk_price_provider", "Webull" if webull_configured and not key and st.session_state["desk_ticker"] not in INDICES else "Tradytics")
    ticker = st.session_state["desk_ticker"]
    with st.sidebar:
        st.markdown('<div class="qd-brand"><div class="qd-mark">◈</div><div><div class="qd-name">Quant Desk</div>'
                    '<div class="qd-brand-sub">Options intelligence · 2.0</div></div></div>', unsafe_allow_html=True)
        st.markdown('<div class="qd-rule"></div><div class="qd-nav-label">Workspace</div>', unsafe_allow_html=True)
        page = st.radio("Workspace navigation", list(PAGES_LABEL), format_func=lambda p: f"{PAGES_LABEL[p][2]}  {p}",
                        key="desk_page", label_visibility="collapsed", width="stretch")
        st.markdown('<div class="qd-rule"></div><div class="qd-nav-label">Quick symbols</div>', unsafe_allow_html=True)
        for col, symbol in zip(st.columns(3, gap="small"), ("SPY", "SPX", "QQQ")):
            col.button(symbol, key=f"watch_{symbol}", width="stretch", on_click=choose_ticker, args=(symbol,))
        expiry = st.selectbox("Options expiry scope", EXPIRY_LABELS, key="desk_expiry_scope")
        mode = st.segmented_control("Workspace mode", ["Live", "Preview"], key="desk_mode") or "Live"
        with st.expander("Feed controls"):
            selected_provider = st.selectbox("Price and candle source", ["Tradytics", "Webull"], key="desk_price_provider",
                                             disabled=mode == "Preview")
            comparison = st.toggle("Enable Webull comparison", key="desk_webull_compare", disabled=mode == "Preview")
            paused = st.toggle("Pause requests", key="desk_paused", disabled=mode == "Preview")
            st.button("Refresh feeds", key="desk_refresh", on_click=refresh, disabled=mode == "Preview", width="stretch")
            st.caption("Tradytics key configured" if key else "Set your Tradytics key in .env")
        st.caption("Quant Options Agent · 2.0")
    preview = mode == "Preview"
    price_provider = "Tradytics" if preview else selected_provider
    paused = bool(paused and not preview)
    if page == "Learn":
        webull_snapshot(ticker, enabled=False, paused=True)
        header(page)
        learn_page(go_to)
        return
    header(page)
    if config_error and not preview:
        st.warning(config_error)
    if preview and not key:
        st.caption("Preview uses illustrative data. Add your Tradytics key to .env and restart to load your live feeds.")
    if not preview and not key and price_provider == "Tradytics":
        st.info("Set TRADYTICS_API_KEY in the local .env file. Use Preview to explore every chart while you set up the feeds.")

    @st.fragment(run_every="4s" if not preview and not paused else None)
    def workspace():
        wanted = not preview and webull_configured and (comparison or price_provider == "Webull")
        w_state, w_quality, w_error = webull_snapshot(ticker, enabled=wanted, paused=paused)
        if not preview and (comparison or price_provider == "Webull") and not webull_configured:
            w_error = "Add WEBULL_APP_KEY and WEBULL_APP_SECRET to .env for Webull."
        ctx = Desk(ticker, DEMO_AT if preview else datetime.now(UTC), demo(ticker) if preview else store,
                   preview, paused, EXPIRY_DTE.get(expiry), bool(key), price_provider,
                   w_state, w_quality, w_error, config_error)
        symbol_bar(ctx)
        if page == "Overview":
            market_strip(ctx)
        PAGES[page](ctx)

    workspace()


if __name__ == "__main__":
    main()
