"""Shared terminal theme and compact chart panel components."""
from __future__ import annotations

import html

import streamlit as st

from market_session import session_state
from terminal_data import et_time, numeric, status
from terminal_learn import explanation

PANEL = "#141f30"
GRID = "#35455d"
INK = "#edf3ff"
MUTED = "#bacada"
TEAL = "#68dcb7"
RED = "#ff8d9b"
BLUE = "#83b5ff"
VIOLET = "#c7adff"
AMBER = "#f6c879"

STYLE = """
<style>
:root {color-scheme:dark}
html,body,.stApp {font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif}
.stApp {background:#0c1320}
[data-testid="stHeader"] {background:#0c1320ed;height:2.65rem}
[data-testid="stSidebar"] {background:#101a29;border-right:1px solid #384a62}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding:1.35rem 1rem 1.7rem}
.block-container {max-width:1600px;padding:3.2rem clamp(.8rem,2.4vw,2.2rem) 2.6rem}
[data-testid="stVerticalBlock"] {gap:.85rem}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:#384a62!important}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlockBorderWrapper"] {background:#141f30;border-radius:12px}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlockBorderWrapper"] > div {padding:.9rem 1rem}
[data-testid="stCaptionContainer"] p,.stCaption {color:#b8c8db;font-size:.77rem;line-height:1.42}
h1,h2,h3 {color:#ecf3ff;letter-spacing:-.035em}
[data-testid="stMarkdownContainer"] h4 {letter-spacing:-.018em;font-size:1rem}
.qd-brand {display:flex;align-items:center;gap:11px;padding:.15rem 0 .35rem}
.qd-mark {width:36px;height:36px;display:grid;place-items:center;background:#1c4143;border:1px solid #387966;border-radius:10px;color:#b2f8e7;font-weight:800}
.qd-name {font-size:1.12rem;font-weight:750;letter-spacing:-.045em;color:#f2f6ff}
.qd-brand-sub,.qd-eyebrow {font-size:.64rem;font-weight:750;letter-spacing:.125em;text-transform:uppercase;color:#b2c5da}
.qd-brand-sub {letter-spacing:.09em;margin-top:2px}
.qd-nav-label {font-size:.62rem;color:#b2c5da;letter-spacing:.14em;font-weight:800;text-transform:uppercase;margin:.35rem 0 -.1rem}
.qd-rule {height:1px;background:#384a62;margin:.35rem 0}
.qd-page {display:flex;justify-content:space-between;gap:1rem;align-items:flex-end;padding:.25rem 0 .2rem}
.qd-page h1 {font-size:clamp(1.55rem,2.55vw,2.35rem);margin:.12rem 0 .28rem;line-height:1.12;font-weight:750;padding:0}
.qd-page p {color:#b8c8db;margin:0;font-size:.84rem}
.qd-symbol-bar {display:flex;gap:.7rem;align-items:center;flex-wrap:wrap;border:1px solid #384a62;border-radius:9px;background:#141f30;padding:.63rem .8rem}
.qd-ticker {border-radius:5px;background:#153d37;color:#8fe8cd;font-size:.76rem;font-weight:800;letter-spacing:.045em;padding:.28rem .48rem}
.qd-symbol-name {font-size:.8rem;color:#dce8f6;font-weight:650}
.qd-spacer {flex:1 1 auto}
.qd-tag {display:inline-flex;white-space:nowrap;align-items:center;gap:.35rem;border:1px solid #50657f;border-radius:5px;padding:.24rem .44rem;color:#d6e3ef;background:#203147;font-size:.61rem;font-weight:800;letter-spacing:.06em}
.qd-tag.good {color:#8fe8cd;background:#153d37;border-color:#387966}
.qd-tag.warn {color:#ffd789;background:#413322;border-color:#856937}
.qd-tag.preview {color:#dac8ff;background:#34294a;border-color:#74618e}
.qd-source {display:flex;flex-wrap:wrap;align-items:center;gap:.45rem;color:#b8c8db;font-size:.72rem}
.qd-source .sep {opacity:.6}
.qd-kpis {display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:.66rem}
.qd-kpis.qd-small {grid-template-columns:repeat(4,minmax(0,1fr))}
.qd-kpi {min-width:0;border:1px solid #384a62;border-radius:10px;background:#141f30;padding:.8rem .85rem;position:relative;overflow:hidden}
.qd-kpi:first-child {background:linear-gradient(115deg,#193b3a,#141f30 85%);border-color:#3c695e}
.qd-kpi-label {font-size:.65rem;letter-spacing:.075em;color:#b2c5da;font-weight:750;text-transform:uppercase}
.qd-kpi-value {font-size:clamp(1rem,1.72vw,1.65rem);letter-spacing:-.04em;line-height:1.2;font-variant-numeric:tabular-nums;font-weight:750;color:#f2f6ff;margin:.35rem 0 .33rem;overflow-wrap:anywhere}
.qd-kpi-value.up,.qd-positive {color:#72e3b8}
.qd-kpi-value.down,.qd-negative {color:#ff97a4}
.qd-kpi-sub {font-size:.68rem;color:#b8c8db;line-height:1.33;min-height:1em}
.qd-panel-title {font-size:.96rem;color:#ecf3ff;font-weight:750;letter-spacing:-.016em;line-height:1.2;padding-top:.14rem}
.qd-panel-sub {color:#b8c8db;font-size:.73rem;line-height:1.42;margin-top:.24rem}
.qd-level {display:flex;justify-content:space-between;gap:.5rem;align-items:center;padding:.46rem 0;border-bottom:1px solid #35455d;font-size:.8rem;color:#d2e1ee}
.qd-level:last-child {border-bottom:0}
.qd-level strong {color:#ecf3ff;font-variant-numeric:tabular-nums}
.qd-dot {display:inline-block;width:.45rem;height:.45rem;border-radius:50%;margin-right:.38rem;background:#a8bfce}
.qd-dot.up {background:#68dcb7}.qd-dot.down {background:#ff8d9b}.qd-dot.gold {background:#f6c879}.qd-dot.purple {background:#c7adff}
.qd-learn-hero {padding:1.35rem 1.45rem;background:linear-gradient(110deg,#18372e,#141f30 74%);border:1px solid #456b59;border-radius:12px}
.qd-learn-hero h2 {margin:.25rem 0 .38rem;padding:0}
.qd-learn-hero p {margin:0;color:#c6d5e6;font-size:.88rem}
.qd-empty {min-height:104px;display:grid;place-items:center;text-align:center;color:#b8c8db;font-size:.8rem;border:1px dashed #506780;border-radius:8px;padding:1rem;margin:.2rem 0}
.qd-market-strip {display:flex;flex-wrap:wrap;gap:.35rem .9rem;align-items:center;color:#b2c5da;font-size:.73rem;border-top:1px solid #384a62;border-bottom:1px solid #384a62;padding:.53rem .15rem}
.qd-market-strip b {color:#e7effc;margin-right:.28rem}
[data-testid="stPopover"] button {min-height:1.7rem;min-width:1.7rem;border-radius:50%;padding:.12rem .38rem;border-color:#4c6079;color:#d7e6f6;font-size:.76rem}
[data-testid="stPopoverBody"] {max-width:min(27rem,calc(100vw - 2rem))}
[data-testid="stSidebar"] [role="radiogroup"] {gap:.08rem}
[data-testid="stSidebar"] [role="radiogroup"] label {padding:.32rem .38rem;border-radius:6px;width:100%}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#253a50}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#1e3045}
[data-testid="stSidebar"] [role="radiogroup"] label p {font-size:.82rem}
[data-testid="stButton"] button {border-radius:7px}
[data-testid="stDataFrame"] {border:1px solid #384a62;border-radius:8px;overflow:hidden}

.qd-command {background:#141f30;border:1px solid #485e77;border-radius:14px;padding:1.25rem 1.4rem;box-shadow:0 4px 16px #0002}
.qd-command-head {display:flex;justify-content:space-between;align-items:flex-start;gap:1rem}
.qd-command h2 {font-size:1.35rem;font-weight:760;line-height:1.2;padding:0;margin:.15rem 0 .4rem}
.qd-command p {font-size:.88rem;color:#d5e3ef;margin:0;line-height:1.48}
.qd-command .qd-status {white-space:nowrap;border-radius:7px;font-size:.73rem;font-weight:800;padding:.40rem .68rem;letter-spacing:.055em;background:#26374c;color:#d2e2f4}
.qd-status.long {background:#143a32;color:#8ae8c5}
.qd-status.short,.qd-status.avoid {background:#512a32;color:#ffadb9}
.qd-status.ready {background:#433622;color:#ffdb8e}
.qd-status.exit {background:#34294a;color:#dbc7ff}
.qd-movement-stage {display:grid;grid-template-columns:1.7rem minmax(0,1fr) auto;gap:.75rem;align-items:start;background:#141f30;border:1px solid #384a62;border-radius:9px;padding:.69rem .8rem}
.qd-stage-number {display:grid;place-items:center;width:1.6rem;height:1.6rem;background:#173d37;color:#8fe8cd;border-radius:6px;font-weight:750;font-size:.73rem}
.qd-stage-body h3 {font-size:.89rem;font-weight:750;margin:0;padding:0 0 .17rem}
.qd-stage-body p {color:#b2c5da;margin:0;font-size:.82rem;line-height:1.46}
.qd-stage-value {max-width:14rem;text-align:right;font-size:.75rem;font-weight:750;color:#dce8f6;padding-top:.16rem;overflow-wrap:anywhere}
.qd-next-cue {border-left:3px solid #68dcb7;background:#15352e;color:#d5ebe4;border-radius:0 7px 7px 0;padding:.46rem .65rem;font-size:.78rem;line-height:1.45;margin:-.35rem 0 .5rem}
.qd-next-cue b {color:#8de4c6}
@media (max-width:620px) {
  .qd-movement-stage {grid-template-columns:1.6rem minmax(0,1fr);gap:.25rem .62rem}
  .qd-stage-value {grid-column:2;text-align:left;max-width:none}
}
.qd-timeframes {display:flex;flex-wrap:wrap;gap:.55rem;margin-top:.8rem}
.qd-timeframes span {border-radius:6px;background:#203147;padding:.27rem .48rem;font-size:.75rem;color:#d3e3f5}
@media (min-width:1000px) {
  [data-testid="stSidebar"][aria-expanded="true"] {min-width:232px;max-width:232px}
}
@media (max-width:1200px) {
  .qd-kpis {grid-template-columns:repeat(3,minmax(0,1fr))}
}
@media (max-width:720px) {
  .block-container {padding:3.1rem .72rem 1.5rem}
  .qd-kpis,.qd-kpis.qd-small {grid-template-columns:repeat(2,minmax(0,1fr))}
  .qd-kpi-value {font-size:1.2rem}
  .qd-page {align-items:flex-start;flex-direction:column}
  .qd-command-head {flex-wrap:wrap}
  .qd-command {padding:1rem}
  .qd-timeframes {gap:.35rem}
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stPlotlyChart"]) {flex-wrap:wrap}
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stPlotlyChart"]) > [data-testid="stColumn"] {flex:1 1 100%;width:100%}
}

[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stHeader"] button {color:#f0f5ff;background:#253449;border:1px solid #526783}
[data-testid="stExpander"] summary {color:#e7effa}
[data-testid="stExpander"] summary:hover,[data-testid="stPopover"] button:hover {color:#92eed2}
button[kind="primary"]:not(:disabled),button[kind="primaryFormSubmit"]:not(:disabled) {background:#65d9b8;color:#062920;border:1px solid #65d9b8}
button[kind="primary"]:not(:disabled) p,button[kind="primaryFormSubmit"]:not(:disabled) p {color:#062920;font-weight:650}
:is(button,a,input,textarea,[role="combobox"],summary):focus-visible {outline:2px solid #8ee7d0;outline-offset:2px}
.js-plotly-plot .modebar-btn path {fill:#c1d0e2!important}
.js-plotly-plot .modebar-btn:hover path,.js-plotly-plot .modebar-btn.active path {fill:#92eed2!important}
@media (max-width:720px) {
  .block-container {padding-top:3.1rem}
  .qd-symbol-bar {gap:.35rem .45rem;padding:.5rem .58rem}
  .qd-page h1 {font-size:1.5rem}
  .qd-kpi {padding:.6rem}
  .qd-kpi-label,.qd-kpi-sub {font-size:.73rem}
  [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding:.75rem .85rem 1rem}
  [data-testid="stMain"] [data-testid="stColumn"] {min-width:0}
  .st-key-qd_page_header [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .qd-page) {flex-wrap:wrap;gap:.28rem}
  .st-key-qd_page_header [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .qd-page) > [data-testid="stColumn"] {flex:1 1 100%;width:100%}
}
@media (pointer:coarse) {
  .st-key-qd_page_header button,[data-testid="stSidebarCollapsedControl"] button {min-width:44px;min-height:44px}
}
</style>
"""


def esc(value):
    return html.escape(str(value), quote=True)


def amount(value, digits=2):
    n = numeric(value)
    return f"{n:,.{digits}f}" if n is not None else "—"


def signed_pct(value):
    n = numeric(value)
    return f"{n:+.2f}%" if n is not None else "—"


def ratio_pct(value):
    n = numeric(value)
    return f"{n:.1%}" if n is not None else "—"


def compact(value, *, currency=False, signed=False):
    n = numeric(value)
    if n is None:
        return "—"
    sign = "−" if n < 0 else "+" if signed and n > 0 else ""
    prefix = sign + ("$" if currency else "")
    n = abs(n)
    for scale, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= scale:
            return f"{prefix}{n / scale:.2f}{unit}"
    return f"{prefix}{n:,.2f}"


def tag(label, tone=""):
    return f'<span class="qd-tag {esc(tone)}">{esc(label)}</span>'


def kpis(items, small=False):
    cards = []
    for label, value, subtitle, tone in items:
        cards.append(f'<div class="qd-kpi"><div class="qd-kpi-label">{esc(label)}</div>'
                     f'<div class="qd-kpi-value {esc(tone)}">{esc(value)}</div>'
                     f'<div class="qd-kpi-sub">{esc(subtitle)}</div></div>')
    st.markdown('<div class="qd-kpis' + (' qd-small' if small else '') + '">' + ''.join(cards) + '</div>', unsafe_allow_html=True)


def panel_header(title, topic, key, detail=None):
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center", gap="small"):
        st.markdown(f'<div class="qd-panel-title">{esc(title)}</div>' +
                    (f'<div class="qd-panel-sub">{esc(detail)}</div>' if detail else ""), unsafe_allow_html=True, width="content")
        explanation(topic, f"guide_{key}")


def chart_quote_caption(ctx):
    quote = ctx.chart_quote
    if quote:
        pace = (f" · polling target {ctx.webull_store.quote_poll_seconds}s"
                if ctx.price_provider == "Webull" and ctx.webull_store else "")
        st.caption(f"{quote['source']} {quote['basis'].lower()} {amount(quote['price'])} · "
                   f"{et_time(quote['at'])}{pace}.")
    elif ctx.paused:
        st.caption("Price updates are paused.")
    elif session_state(ctx.now)["status"] != "OPEN":
        st.caption("Regular session closed. Price charts show completed candles.")
    else:
        st.caption(f"Waiting for a fresh {ctx.price_provider} price. The chart uses the available completed candles.")


def feed_stamp(name, feed, ctx):
    label, time_label = status(name, feed, ctx.now, ctx.ticker, preview=ctx.preview, paused=ctx.paused)
    tone = "preview" if ctx.preview else "good" if label in ("RECENT", "TAPE", "SESSION DATA") else "warn" if label in ("CACHED", "AGED", "COOLDOWN", "SCOPE REQUIRED") else ""
    provider = "Illustrative data" if ctx.preview else "Tradytics"
    checked = ""
    if not ctx.preview and feed.snapshot:
        seconds = max(0, int((ctx.now - feed.snapshot.received_at).total_seconds()))
        checked = f'<span class="sep">·</span><span>API checked {seconds // 60}m {seconds % 60}s ago</span>'
    source_label = "Bar closed" if name == "timeline" else "Source"
    st.markdown(f'<div class="qd-source">{tag(label, tone)}<span>{provider}</span><span class="sep">·</span><span>{source_label} {esc(time_label)}</span>{checked}</div>',
                unsafe_allow_html=True)
    if feed.error and feed.snapshot:
        st.caption(f"Cached observation. {feed.error}")


def empty(feed=None, message="No observations returned for these filters."):
    if feed and feed.error:
        message = str(feed.error)
    elif feed and feed.loading:
        message = "Loading this feed…"
    st.markdown(f'<div class="qd-empty">{esc(message)}</div>', unsafe_allow_html=True)


def style_chart(fig, height=320, *, hover="x unified", legend=True, revision=None):
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL, height=height,
        font=dict(family="Inter, system-ui, sans-serif", color=MUTED, size=11),
        margin=dict(l=12, r=14, t=12, b=65 if legend else 20),
        legend=dict(orientation="h", y=-0.19, x=0, yanchor="top", xanchor="left",
                    bgcolor="rgba(0,0,0,0)", font=dict(size=12, color=INK),
                    itemclick="toggle", itemdoubleclick="toggleothers"),
        hovermode=hover, showlegend=legend, uirevision=revision,
        hoverlabel=dict(bgcolor="#22324b", font_color=INK, bordercolor="#526784"),
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=GRID, zeroline=False, tickfont=dict(color=MUTED), title_font=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, linecolor=GRID, zerolinecolor="#526784", tickfont=dict(color=MUTED), title_font=dict(color=MUTED))
    return fig


def plot(fig, key):
    st.plotly_chart(fig, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "displayModeBar": False, "scrollZoom": False})
