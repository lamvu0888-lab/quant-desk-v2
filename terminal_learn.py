"""One set of explanations for the Learn library and contextual help."""
from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class Guide:
    title: str
    group: str
    meaning: str
    read: str
    example: str
    page: str
    source: str | None = None


DOC = "https://tradytics.com/docs/"
GUIDES = {
    "overview": Guide("Market overview", "Start here",
        "The overview puts the selected symbol's price, net options premium, dealer model and implied volatility in one place.",
        "Check the symbol, session and source times first. Compare the price chart with nearby walls and the net flow chart over the same session.",
        "If SPX is approaching a call wall while net premium is rising, inspect the 0DTE strike profile and watch how price trades around that level.", "Overview"),
    "price": Guide("Price, EMA and VWAP", "Start here",
        "Candles show the open, high, low and close of each completed interval. The 9 and 21 EMAs weight recent closes. Session VWAP weights bar VWAP by shares traded.",
        "Use 5m for the session structure and 1m or 3m for a closer look. VWAP starts at the regular-session open and needs a complete sequence of volume and VWAP inputs.",
        "Price above both EMAs and session VWAP can describe a rising intraday trend. Index candles can have blank volume and VWAP fields.", "Overview", DOC + "stocks/stocks-bars"),
    "breadth": Guide("Market breadth", "Start here",
        "Tradytics counts rising and falling stocks among names that have traded at least $20 million that session.",
        "Compare the advance/decline counts with SPY or SPX. Breadth describes participation across names rather than the size of the index move.",
        "A rising index with more decliners than advancers can indicate a rally concentrated in a smaller group of stocks.", "Overview", DOC + "market/market-breadth"),
    "net_flow": Guide("Net options premium", "Options flow",
        "Tradytics calculates bought minus sold premium for calls and for puts. Directional net premium subtracts the put series from the call series.",
        "The line adds the signed interval values over the selected session. Use the DTE filter to compare same-day contracts with the broader tape. Put/call is a separate gross premium ratio.",
        "Net calls of +$6M and net puts of −$2M give +$8M of directional net premium for that interval. Check the individual trades for spreads and other linked legs.", "Options Flow", DOC + "options-flow/options-flow-timeline"),
    "tape": Guide("Options tape", "Options flow",
        "A print records an option transaction or an order assembled from fills. Tradytics reports premium, strike, expiry, size, trade type and inferred side.",
        "A sweep reaches multiple exchanges. A block can be a cross or a large trade. Bought and sold refer to execution against the quote. A mid print has unclear aggression. NBBO checks can revise the initial side.",
        "Filter SPY to sweeps above $250K. Inspect the condition and expiry before treating a call print as bullish. The opening flag uses size above open interest as a heuristic.", "Options Flow", DOC + "options-flow/options-flow"),
    "gamma": Guide("Gamma exposure", "Dealer positioning",
        "Gamma measures how option delta changes when spot moves. Tradytics reports modeled net dealer gamma in dollars of delta per 1% spot move.",
        "Use the zero line to see the sign of exposure by strike. Positive dealer gamma can imply hedging against a price move under the model. Negative dealer gamma can imply hedging in the same direction.",
        "Compare the same strike on 0DTE and all expiries. The sign or size can change because the populations and time to expiry differ.", "Positioning", DOC + "options-positioning/options-exposure"),
    "delta": Guide("Delta exposure", "Dealer positioning",
        "Delta estimates how option value responds to a small move in the underlying. The dealer model aggregates signed delta in share equivalents.",
        "Read positive and negative values as the model's direction of option delta. Look at Gamma to understand how that value can change with spot.",
        "A modeled +100,000 share equivalent means the option position responds locally like that share exposure under the model's inventory assumption.", "Positioning", DOC + "options-positioning/options-exposure"),
    "vanna": Guide("Vanna exposure", "Dealer positioning",
        "Vanna measures how option delta changes when implied volatility changes. Tradytics reports the aggregate in dollars per IV point. The local Webull model reports shares per IV point.",
        "Read the unit first. A one-point IV change is 20% to 21%, for example. In the simplest local sensitivity scenario, the effect of a one-point IV fall reverses the sign of the one-point rise.",
        "Tradytics Vanna of −$8M per IV point implies a local modeled delta exposure change of −$8M for a one-point IV rise, with other inputs held fixed.", "Positioning", DOC + "options-positioning/options-exposure"),
    "charm": Guide("Charm exposure", "Dealer positioning",
        "Charm measures how delta changes as time passes. Tradytics reports the aggregate in shares per day.",
        "Compare strikes and expiries and keep spot and IV fixed in the scenario. Near expiry the sensitivity can change quickly as a contract moves in or out of the money.",
        "A value of +250K shares/day is the model's local time sensitivity. Interpret a 0DTE reading near the close over the time actually left in the session.", "Positioning", DOC + "options-positioning/options-exposure"),
    "levels": Guide("Walls, flip and max pain", "Dealer positioning",
        "Tradytics labels call and put walls from gamma concentration. A gamma flip is a model crossing near spot. Max pain comes from open contract payout at an expiry.",
        "Check the expiry scope and snapshot time. The three levels can move when spot, inputs or the model run changes. A missing flip means the selected snapshot has no reported crossing.",
        "Compare the flip to current spot and the closest walls. Treat each level as an area to watch for a price reaction.", "Positioning", DOC + "options-positioning/options-exposure"),
    "compare": Guide("Tradytics and Webull comparison", "Dealer positioning",
        "The two readings use different data and positioning assumptions. The local Webull model signs call and put inventory over a configured chain window.",
        "Compare scope, timestamps, units and model assumptions before reading the gap. Open interest publication timing and coverage can also affect the local view.",
        "Tradytics Vanna in USD per IV point and Webull Vanna in shares per IV point should be read in their original units.", "Positioning"),
    "chain": Guide("Options chain", "Dealer positioning",
        "The chain lists individual calls and puts with quoted bid and ask, IV, open interest and first-order Greeks where supplied.",
        "Filter by DTE or exact expiry and sort by strike or volume. Open interest is the prior-session count of outstanding contracts. Volume counts today's trades.",
        "Check the spread and quote time on a near-the-money contract before using its IV or interpreting a large print.", "Positioning", DOC + "options-contracts/options-chain"),
    "volatility": Guide("Implied volatility and expected move", "Volatility",
        "IV is the annualized volatility implied by option prices. Realized volatility is estimated from past returns. IV rank locates current IV within its trailing high and low.",
        "Read IV rank alongside IV percentile, which counts past days below current IV. Term structure compares expiries. 25-delta skew compares put IV with call IV at similar absolute delta.",
        "An expected move of ±$7 for the next expiry describes a priced range. Compare it with the forecast's horizon and the date of the expiry.", "Volatility", DOC + "options-volatility/options-volatility"),
    "dark": Guide("Dark-pool prints and levels", "Flow and signals",
        "Dark-pool rows in this workspace are reported off-exchange share trades. Levels group notional around trade prices over the selected window.",
        "Look for repeat concentrations near spot and check the report time. The price and dollar size of a print describe the transaction. The feed can include late reports.",
        "Several large prints clustered near $760 can identify a price area of interest. Use price behavior there to assess its relevance.", "Dark Pool", DOC + "darkpool/darkpool-levels"),
    "signals": Guide("Tradytics signals", "Flow and signals",
        "A signal is a named options-flow pattern and the contract it flagged. Tradytics records an ask-based entry and later quote marks or settlement.",
        "Inspect the pattern, expiry, bid/ask and supporting prints. Use the details to distinguish a recent open mark from a settled result.",
        "A repeat-flow signal can identify repeated interest in a contract. Check whether those prints were spreads and whether the entry is still available.", "Signals", DOC + "signals/signals"),
    "performance": Guide("Signal performance", "Flow and signals",
        "Tradytics summarizes pattern returns using its ask-based entry and latest mark or settlement. The performance table covers the provider's pattern universe.",
        "Compare settled sample count, win rate, average and median return over the same window. A few very large winners can pull the average above the median.",
        "Use the Settled filter to compare completed results. Pattern-level performance can differ from one ticker's results.", "Signals", DOC + "signals/signals-performance"),
    "forecast": Guide("Monte Carlo forecast", "Forecast and data",
        "The model simulates many price paths from completed regular-session one-minute returns. The block model resamples recent sequences. GBM uses Gaussian increments.",
        "The 68% and 95% bands are ranges at each minute across simulations. Up and down use the selected neutral zone. Level-touch estimates check each simulated minute.",
        "Compare the 15-minute range with the distance to a fresh wall or your own level. Flow and dealer readings remain separate context while the model uses price returns.", "Forecast"),
    "calibration": Guide("Recent forecast calibration", "Forecast and data",
        "Walk-forward validation fits the model using candles available at each historical origin and compares with the realized close at the selected horizon.",
        "Compare empirical 68% and 95% coverage with their nominal targets. A Brier score below 0.25 improves on a 50/50 directional baseline on that sample.",
        "If the 68% band covered only 50% of 20 recent outcomes, the ranges were narrow on that short sample. More days and regimes are needed to assess calibration.", "Forecast"),
    "health": Guide("Data health and clocks", "Forecast and data",
        "Each feed has its own source timestamp, session, refresh schedule and key scope. The HTTP receipt time records when your app received the response.",
        "Check for prior-session values, an aged source time or a scope error before relying on a chart. Dealer exposure is built on a roughly five-minute schedule.",
        "A response received now can still contain yesterday's price. An empty tape can mean no trades matched the filters, while a scope error means access needs attention.", "Data Health", DOC + "quickstart"),
    "history": Guide("Historical views and exports", "Forecast and data",
        "Historical views show provider sessions or a local report captured at a known time. Export buttons download the selected view for review.",
        "Read the session and endpoint coverage. A flow table download contains the loaded rows under the selected filters. Daily exposure history is a separate series.",
        "To compare two days, keep the ticker and expiry scope the same and record each source's timestamp.", "Data Health"),
}


def explanation(topic, key):
    guide = GUIDES[topic]
    with st.popover("?", key=key, help=f"How to read {guide.title}", type="secondary"):
        st.markdown(f"**{guide.title}**")
        st.write(guide.meaning)
        st.markdown("**How to read it**")
        st.write(guide.read)
        st.markdown("**Example**")
        st.write(guide.example)
        if guide.source:
            st.markdown(f"[Source documentation]({guide.source})")


def learn_page(go_to):
    st.markdown(
        '<div class="qd-learn-hero"><div class="qd-eyebrow">FIELD GUIDE</div>'
        '<h2>Read the market with context.</h2><p>Understand what each chart measures, how to read it, and what to check next.</p></div>',
        unsafe_allow_html=True,
    )
    st.markdown("#### A quick session walkthrough")
    columns = st.columns(3)
    for column, (number, title, detail) in zip(columns, (
        ("01", "Check the session", "Start with the symbol, price timestamp and active data source in Overview."),
        ("02", "Map nearby levels", "Open Positioning, choose Gamma and compare 0DTE with all expiries around spot."),
        ("03", "Compare with flow", "Review net premium and the prints behind it. Use the Forecast range to put distances in context."),
    )):
        with column.container(border=True):
            st.markdown(f'<div class="qd-eyebrow">{number}</div>', unsafe_allow_html=True)
            st.markdown(f"**{title}**")
            st.caption(detail)
    groups = list(dict.fromkeys(guide.group for guide in GUIDES.values()))
    group = st.selectbox("Topic group", ["All tools", *groups], key="learn_group")
    topics = [key for key, guide in GUIDES.items() if group in ("All tools", guide.group)]
    topic = st.selectbox("Tool or chart", topics, format_func=lambda key: GUIDES[key].title, key="learn_topic")
    guide = GUIDES[topic]
    with st.container(border=True):
        st.markdown(f"### {guide.title}")
        st.caption(guide.group)
        st.write(guide.meaning)
        left, right = st.columns(2)
        with left:
            st.markdown("**How to read it**")
            st.write(guide.read)
        with right:
            st.markdown("**Worked example**")
            st.write(guide.example)
        if guide.source:
            st.markdown(f"[Read the provider documentation]({guide.source})")
        st.button(f"Open {guide.page}", on_click=go_to, args=(guide.page,), key="learn_open_page")
