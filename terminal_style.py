"""Shared terminal theme and compact chart panel components."""
from __future__ import annotations

import html

import streamlit as st

from terminal_data import numeric, status
from terminal_learn import explanation

PANEL = "#ffffff"
GRID = "#dfe6ee"
INK = "#172b43"
MUTED = "#4e6076"
TEAL = "#087c68"
RED = "#bf3e52"
BLUE = "#315fd3"
VIOLET = "#7352b5"
AMBER = "#9f650f"

STYLE = """
<style>
:root {color-scheme:light}
html,body,.stApp {font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif}
.stApp {background:#f4f7fb}
[data-testid="stHeader"] {background:#f4f7fbe8;height:2.65rem}
[data-testid="stSidebar"] {background:#f8fafc;border-right:1px solid #dce4ee}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding:1.35rem 1rem 1.7rem}
.block-container {max-width:1700px;padding:3.1rem 1.8rem 2.6rem}
[data-testid="stVerticalBlock"] {gap:.85rem}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:#dce4ee!important}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlockBorderWrapper"] {background:#ffffff;border-radius:12px}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlockBorderWrapper"] > div {padding:.9rem 1rem}
[data-testid="stCaptionContainer"] p,.stCaption {color:#4e6076;font-size:.77rem;line-height:1.42}
h1,h2,h3 {color:#172b43;letter-spacing:-.035em}
[data-testid="stMarkdownContainer"] h4 {letter-spacing:-.018em;font-size:1rem}
.qd-brand {display:flex;align-items:center;gap:11px;padding:.15rem 0 .35rem}
.qd-mark {width:36px;height:36px;display:grid;place-items:center;background:#0e3754;border:1px solid #174766;border-radius:10px;color:#d4fff4;font-weight:800}
.qd-name {font-size:1.12rem;font-weight:750;letter-spacing:-.045em;color:#10263e}
.qd-brand-sub,.qd-eyebrow {font-size:.64rem;font-weight:750;letter-spacing:.125em;text-transform:uppercase;color:#526780}
.qd-brand-sub {letter-spacing:.09em;margin-top:2px}
.qd-nav-label {font-size:.62rem;color:#526780;letter-spacing:.14em;font-weight:800;text-transform:uppercase;margin:.35rem 0 -.1rem}
.qd-rule {height:1px;background:#dce4ee;margin:.35rem 0}
.qd-page {display:flex;justify-content:space-between;gap:1rem;align-items:flex-end;padding:.25rem 0 .2rem}
.qd-page h1 {font-size:clamp(1.55rem,2.55vw,2.35rem);margin:.12rem 0 .28rem;line-height:1.12;font-weight:750;padding:0}
.qd-page p {color:#546982;margin:0;font-size:.84rem}
.qd-symbol-bar {display:flex;gap:.7rem;align-items:center;flex-wrap:wrap;border:1px solid #d9e4ef;border-radius:9px;background:#ffffff;padding:.63rem .8rem}
.qd-ticker {border-radius:5px;background:#dceeea;color:#116454;font-size:.76rem;font-weight:800;letter-spacing:.045em;padding:.28rem .48rem}
.qd-symbol-name {font-size:.8rem;color:#304661;font-weight:650}
.qd-spacer {flex:1 1 auto}
.qd-tag {display:inline-flex;white-space:nowrap;align-items:center;gap:.35rem;border:1px solid #cbd8e6;border-radius:5px;padding:.24rem .44rem;color:#405670;background:#f0f4fa;font-size:.61rem;font-weight:800;letter-spacing:.06em}
.qd-tag.good {color:#116454;background:#e6f5ed;border-color:#b5dac9}
.qd-tag.warn {color:#815312;background:#fff4dc;border-color:#e7d3ac}
.qd-tag.preview {color:#6241a5;background:#f2eafb;border-color:#dcd1ee}
.qd-source {display:flex;flex-wrap:wrap;align-items:center;gap:.45rem;color:#4b6079;font-size:.72rem}
.qd-source .sep {opacity:.6}
.qd-kpis {display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:.66rem}
.qd-kpis.qd-small {grid-template-columns:repeat(4,minmax(0,1fr))}
.qd-kpi {min-width:0;border:1px solid #dce4ee;border-radius:10px;background:#ffffff;padding:.8rem .85rem;position:relative;overflow:hidden}
.qd-kpi:first-child {background:linear-gradient(115deg,#e8f4f2,#ffffff 85%);border-color:#c7e1dd}
.qd-kpi-label {font-size:.65rem;letter-spacing:.075em;color:#526782;font-weight:750;text-transform:uppercase}
.qd-kpi-value {font-size:clamp(1rem,1.72vw,1.65rem);letter-spacing:-.04em;line-height:1.2;font-variant-numeric:tabular-nums;font-weight:750;color:#122a42;margin:.35rem 0 .33rem;overflow-wrap:anywhere}
.qd-kpi-value.up,.qd-positive {color:#08715e}
.qd-kpi-value.down,.qd-negative {color:#a72d43}
.qd-kpi-sub {font-size:.68rem;color:#516780;line-height:1.33;min-height:1em}
.qd-panel-title {font-size:.96rem;color:#172d46;font-weight:750;letter-spacing:-.016em;line-height:1.2;padding-top:.14rem}
.qd-panel-sub {color:#526985;font-size:.73rem;line-height:1.42;margin-top:.24rem}
.qd-level {display:flex;justify-content:space-between;gap:.5rem;align-items:center;padding:.46rem 0;border-bottom:1px solid #dfe6ee;font-size:.8rem;color:#344d69}
.qd-level:last-child {border-bottom:0}
.qd-level strong {color:#172d46;font-variant-numeric:tabular-nums}
.qd-dot {display:inline-block;width:.45rem;height:.45rem;border-radius:50%;margin-right:.38rem;background:#708ba9}
.qd-dot.up {background:#087c68}.qd-dot.down {background:#bf3e52}.qd-dot.gold {background:#9f650f}.qd-dot.purple {background:#7352b5}
.qd-learn-hero {padding:1.35rem 1.45rem;background:linear-gradient(110deg,#e5f4ef,#ffffff 74%);border:1px solid #c7e0d7;border-radius:12px}
.qd-learn-hero h2 {margin:.25rem 0 .38rem;padding:0}
.qd-learn-hero p {margin:0;color:#435d74;font-size:.88rem}
.qd-empty {min-height:104px;display:grid;place-items:center;text-align:center;color:#4e6076;font-size:.8rem;border:1px dashed #cbd7e5;border-radius:8px;padding:1rem;margin:.2rem 0}
.qd-market-strip {display:flex;flex-wrap:wrap;gap:.35rem .9rem;align-items:center;color:#526780;font-size:.73rem;border-top:1px solid #d9e4ef;border-bottom:1px solid #d9e4ef;padding:.53rem .15rem}
.qd-market-strip b {color:#243d59;margin-right:.28rem}
[data-testid="stPopover"] button {min-height:1.7rem;min-width:1.7rem;border-radius:50%;padding:.12rem .38rem;border-color:#c4d3e5;color:#314b68;font-size:.76rem}
[data-testid="stPopoverBody"] {max-width:min(27rem,calc(100vw - 2rem))}
[data-testid="stSidebar"] [role="radiogroup"] {gap:.08rem}
[data-testid="stSidebar"] [role="radiogroup"] label {padding:.32rem .38rem;border-radius:6px;width:100%}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#e7eef7}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#eff4f9}
[data-testid="stSidebar"] [role="radiogroup"] label p {font-size:.82rem}
[data-testid="stButton"] button {border-radius:7px}
[data-testid="stDataFrame"] {border:1px solid #dce4ee;border-radius:8px;overflow:hidden}

.qd-command {background:#fff;border:1px solid #d6e3ee;border-radius:14px;padding:1.25rem 1.4rem;box-shadow:0 4px 16px #172b4308}
.qd-command-head {display:flex;justify-content:space-between;align-items:flex-start;gap:1rem}
.qd-command h2 {font-size:1.35rem;font-weight:760;line-height:1.2;padding:0;margin:.15rem 0 .4rem}
.qd-command p {font-size:.88rem;color:#425b74;margin:0;line-height:1.48}
.qd-command .qd-status {white-space:nowrap;border-radius:7px;font-size:.73rem;font-weight:800;padding:.40rem .68rem;letter-spacing:.055em;background:#eef2f8;color:#3b516c}
.qd-status.long {background:#e4f4ed;color:#086252}
.qd-status.short,.qd-status.avoid {background:#fcecef;color:#a42f47}
.qd-status.ready {background:#fff2d7;color:#80550f}
.qd-status.exit {background:#eee9fa;color:#613aa5}
.qd-movement-stage {display:grid;grid-template-columns:1.7rem minmax(0,1fr) auto;gap:.75rem;align-items:start;background:#fff;border:1px solid #dce4ee;border-radius:9px;padding:.69rem .8rem}
.qd-stage-number {display:grid;place-items:center;width:1.6rem;height:1.6rem;background:#e6f3f0;color:#116454;border-radius:6px;font-weight:750;font-size:.73rem}
.qd-stage-body h3 {font-size:.89rem;font-weight:750;margin:0;padding:0 0 .17rem}
.qd-stage-body p {color:#526780;margin:0;font-size:.82rem;line-height:1.46}
.qd-stage-value {max-width:14rem;text-align:right;font-size:.75rem;font-weight:750;color:#304661;padding-top:.16rem;overflow-wrap:anywhere}
@media (max-width:620px) {
  .qd-movement-stage {grid-template-columns:1.6rem minmax(0,1fr);gap:.25rem .62rem}
  .qd-stage-value {grid-column:2;text-align:left;max-width:none}
}
.qd-timeframes {display:flex;flex-wrap:wrap;gap:.55rem;margin-top:.8rem}
.qd-timeframes span {border-radius:6px;background:#f1f5fa;padding:.27rem .48rem;font-size:.75rem;color:#405773}
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
</style>
"""

DARK_STYLE = """
<style>
:root {color-scheme:dark}
.stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"] {background:#0b1220!important;color:#e7edf7!important}
[data-testid="stHeader"] {background:#0b1220e8!important}
[data-testid="stHeader"] button,
[data-testid="stToolbar"] button,
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebarCollapseButton"] button {
  background:#243652!important;border:1px solid #526784!important;color:#f4f8ff!important
}
[data-testid="stHeader"] button svg,
[data-testid="stToolbar"] button svg,
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="stSidebarCollapseButton"] svg {
  color:#f4f8ff!important;fill:#f4f8ff!important
}
[data-testid="stHeader"] button:hover,
[data-testid="stToolbar"] button:hover,
[data-testid="stSidebarCollapsedControl"] button:hover,
[data-testid="stSidebarCollapseButton"] button:hover {background:#354d70!important}
[data-testid="stSidebar"],[data-testid="stSidebar"] > div:first-child {background:#0f1728!important;border-color:#2b3a54!important}
[data-testid="stSidebar"] label,[data-testid="stSidebar"] label p,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {color:#d7e3f3!important}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] svg,
[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg {color:#bed5f5!important;fill:#bed5f5!important}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlockBorderWrapper"],.qd-command,.qd-kpi,.qd-symbol-bar {background:#111c30!important;border-color:#34445f!important;color:#e7edf7!important}
.qd-movement-stage {background:#111c30;border-color:#34445f;color:#e7edf7}
.qd-stage-number {background:#123e3a;color:#7ee0c2}
.qd-stage-value {color:#c4d3e7}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:#34445f!important}
h1,h2,h3,.qd-panel-title,.qd-kpi-value,.qd-name {color:#e7edf7!important}
p,[data-testid="stCaptionContainer"] p,.stCaption,.qd-page p,.qd-panel-sub,.qd-command p,.qd-kpi-sub,.qd-source,.qd-symbol-name,.qd-level,.qd-empty {color:#aebdd2!important}
.qd-brand-sub,.qd-eyebrow,.qd-nav-label,.qd-kpi-label {color:#91a5c0!important}
.qd-rule {background:#2b3a54!important}
.qd-level,.qd-market-strip {background:transparent!important;border-color:#2b3a54!important;color:#bdcce0!important}
.qd-level strong,.qd-market-strip b {color:#edf4ff!important}
.qd-kpi:first-child,.qd-learn-hero {background:linear-gradient(115deg,#173d3c,#111c30 85%)!important;border-color:#28685f!important}
.qd-learn-hero p,.qd-command p {color:#bdcce0!important}
.qd-empty {border-color:#405474!important}
.qd-tag {background:#1b2940!important;border-color:#405474!important;color:#c4d3e7!important}
.qd-tag.good,.qd-status.long {background:#123e3a!important;color:#7ee0c2!important;border-color:#28685f!important}
.qd-tag.warn,.qd-status.ready {background:#453515!important;color:#f4cc79!important;border-color:#73582a!important}
.qd-tag.preview {background:#302647!important;color:#cbb4f5!important;border-color:#5b4781!important}
.qd-status {background:#1b2940!important;color:#c4d3e7!important}
.qd-status.short,.qd-status.avoid {background:#45202b!important;color:#ff9eae!important}
.qd-status.exit {background:#302647!important;color:#d0b9ff!important}
.qd-timeframes span {background:#1b2940!important;color:#c4d3e7!important}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#243451!important}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#1a2940!important}
input,textarea,[data-baseweb="select"] > div,[data-testid="stNumberInput"] input {background:#111c30!important;color:#e7edf7!important;border-color:#405474!important}
button {color:#e7edf7!important;border-color:#405474!important;background:#16243a!important}
[data-testid="stExpander"] {border-color:#34445f!important;background:#111c30!important}
[data-testid="stDataFrame"] {border-color:#34445f!important}
</style>
"""


def theme_css(theme):
    """Return the base dashboard CSS plus the selected appearance overrides."""
    return STYLE + (DARK_STYLE if str(theme).lower() == "dark" else "")


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
    dark = bool(st.session_state.get("desk_dark_mode", False))
    panel = "#111c30" if dark else PANEL
    grid = "#2c3d57" if dark else GRID
    ink = "#edf4ff" if dark else INK
    muted = "#b3c3d9" if dark else MUTED
    if dark:
        # The price and spot traces use the light theme's ink constant.
        # Recolor these before applying the plot template.
        for trace in fig.data:
            line = getattr(trace, "line", None)
            if line is not None and getattr(line, "color", None) == INK:
                line.color = ink
            textfont = getattr(trace, "textfont", None)
            if textfont is not None and getattr(textfont, "color", None) == INK:
                textfont.color = ink
    fig.update_layout(
        template="plotly_dark" if dark else "plotly_white", paper_bgcolor=panel, plot_bgcolor=panel, height=height,
        font=dict(family="Inter, system-ui, sans-serif", color=muted, size=11),
        margin=dict(l=12, r=14, t=12, b=65 if legend else 20),
        legend=dict(orientation="h", y=-0.19, x=0, yanchor="top", xanchor="left",
                    bgcolor="rgba(0,0,0,0)", font=dict(size=12, color=ink),
                    itemclick="toggle", itemdoubleclick="toggleothers"),
        hovermode=hover, showlegend=legend, uirevision=revision,
        hoverlabel=dict(bgcolor="#22324b" if dark else "#f2f6fc", font_color=ink, bordercolor="#526784" if dark else "#aabdd0"),
    )
    fig.update_xaxes(gridcolor=grid, linecolor=grid, zeroline=False, tickfont=dict(color=muted), title_font=dict(color=muted))
    fig.update_yaxes(gridcolor=grid, linecolor=grid, zerolinecolor="#526784" if dark else "#9aacc1", tickfont=dict(color=muted), title_font=dict(color=muted))
    if dark:
        for annotation in fig.layout.annotations or ():
            if annotation.font and annotation.font.color == MUTED:
                annotation.font.color = muted
            if annotation.bgcolor == "rgba(255,255,255,.88)":
                annotation.bgcolor = "#22324b"
    return fig


def plot(fig, key):
    st.plotly_chart(fig, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "displayModeBar": False, "scrollZoom": False})
