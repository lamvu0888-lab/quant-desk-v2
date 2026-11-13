"""One set of explanations for the Learn library and contextual help."""
from dataclasses import dataclass
from pathlib import Path

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
        "See what price is doing and whether there is a setup to watch.",
        "Check ticker, expiry and data confidence. Then read the status and the reason beneath it.",
        "GET READY means the trend and flow support a setup. Check the trigger and wait for the smaller candles to confirm.", "Overview"),
    "entry_command": Guide("Entry status and evidence", "Start here",
        "The status tells you whether to watch, wait or review a confirmed setup.",
        "Open Evidence and entry conditions for the reasons. An entry score measures how many checks agree.",
        "A long setup can reach its trigger and still show AVOID if the next resistance leaves too little room for the target.", "Trade Plan"),
    "price": Guide("Price and large orders", "Start here",
        "Candles show price. Circles mark large options trades.",
        "C means call and P means put. Bigger circles mean more premium.",
        "A C on the 10:15 candle means a call trade printed during that candle. Hover to see its price and size.", "Overview", DOC + "stocks/stocks-bars"),
    "regime": Guide("Volatility regime", "Start here",
        "Compressing means price swings are narrowing. Expanding means they are widening.",
        "Normal means neither pattern stands out.",
        "The desk uses completed five-minute candles. The label can change when a new candle closes.", "Volatility"),
    "breadth": Guide("Market breadth", "Start here",
        "See how many active stocks are up or down.",
        "If the index rises but most stocks fall, fewer names are carrying the move.",
        "A rising SPY with 300 stocks up and 700 down shows narrow participation.", "Volatility", DOC + "market/market-breadth"),
    "net_flow": Guide("Net options premium", "Options flow",
        "The line adds directional options premium through the session.",
        "Watch whether flow speeds up with price or starts moving the other way.",
        "Net calls of +$6M and net puts of −$2M give +$8M in directional flow. Momentum compares the last three intervals with the three before.", "Options Flow", DOC + "options-flow/options-flow-timeline"),
    "tape": Guide("Options tape", "Options flow",
        "Each row is an options trade that executed.",
        "Check the strike, expiry, size, and whether the quote-side check is finished.",
        "Repeated prints in one contract are worth a closer look. A spread can make a single leg misleading.", "Options Flow", DOC + "options-flow/options-flow"),
    "bubbles": Guide("Flow bubbles", "Options flow",
        "Bubbles show where large options trades printed on the price chart.",
        "Bigger means more premium. Filled means estimated buy. Hollow means estimated sell.",
        "A green hollow P is an estimated put sale. C or P always tells you the contract type.", "Options Flow", DOC + "options-flow/options-flow"),
    "money_map": Guide("Money map", "Options flow",
        "See which names have the largest net options flow.",
        "Bigger tiles mean more net dollars. Green leans bullish and red leans bearish.",
        "A green $40M tile shows a net bullish tilt of $40M. Brighter color means higher total activity versus usual when the comparison is available.", "Money Map", DOC + "options-flow/options-flow-leaders"),
    "cvd": Guide("Share-volume CVD estimate", "Options flow",
        "See whether more volume came on up minutes or down minutes.",
        "A rising line means more volume on up minutes. It starts over each session.",
        "An up minute with 100K shares and a down minute with 70K add an estimated 30K shares. SPX has no share volume.", "Overview", "https://www.tradingview.com/support/solutions/43000725058-cumulative-volume-delta/"),
    "contract_cvd": Guide("Contract-volume delta", "Options flow",
        "See bought contracts minus sold contracts in the loaded tape.",
        "Use net premium to compare bullish and bearish tilt.",
        "A purchase of 100 puts adds 100 contracts to the volume line even though the trade leans bearish.", "Options Flow", DOC + "options-flow/options-flow"),
    "gamma": Guide("Gamma exposure", "Dealer positioning",
        "Gamma estimates how dealer hedging may react when price moves.",
        "Positive gamma can soften moves. Negative gamma can make moves larger.",
        "Look at the largest bars near the current price. A big bar far away may matter less right now.", "Positioning", DOC + "options-positioning/options-exposure"),
    "delta": Guide("Delta exposure", "Dealer positioning",
        "Delta shows the model’s price sensitivity in share equivalents.",
        "Compare the same expiry and update time.",
        "A reading of +2M share equivalents describes the model’s exposure at that moment.", "Positioning", DOC + "options-positioning/options-exposure"),
    "vanna": Guide("Vanna exposure", "Dealer positioning",
        "Vanna estimates how exposure changes when implied volatility moves.",
        "Look at the direction and size for a one-point change in IV.",
        "IV rising from 20% to 21% is a one-point rise. The model holds price and other inputs fixed for that comparison.", "Positioning", DOC + "options-positioning/options-exposure"),
    "charm": Guide("Charm exposure", "Dealer positioning",
        "Charm estimates how exposure changes as time passes.",
        "Use the shares-per-day label to compare readings.",
        "A +250K reading means the model estimates a change of 250K share equivalents over a day with other inputs fixed.", "Positioning", DOC + "options-positioning/options-exposure"),
    "levels": Guide("Walls, flip and price levels", "Dealer positioning",
        "These are prices where traders may watch for a reaction.",
        "Compare the nearest levels with price and flow.",
        "A call wall can shift as the model updates. Range highs and lows come from completed price bars.", "Positioning", DOC + "options-positioning/options-exposure"),
    "compare": Guide("Compare sources", "Dealer positioning",
        "Tradytics and Webull use different models.",
        "Check the expiry range, units and update time before comparing.",
        "Vanna can be in dollars or shares per IV point. Read each source in its own units.", "Positioning"),
    "hedge_pressure": Guide("Hedge-pressure map", "Dealer positioning",
        "See how a dealer model responds if price, IV or time changes.",
        "Positive values indicate estimated hedge buying. Compare the stress cases and check actual price and flow separately.",
        "A green cell after a price drop shows the model would buy under those assumptions. The dealer's actual hedge trades are unknown.", "Positioning"),
    "chain": Guide("Options chain", "Dealer positioning",
        "See the listed calls and puts for this ticker.",
        "Check the expiry, strike, bid and ask for each contract.",
        "The sidebar expiry narrows the chain. Open interest counts outstanding contracts and volume counts trades.", "Positioning", DOC + "options-contracts/options-chain"),
    "volatility": Guide("Implied volatility and expected move", "Market regime",
        "IV is how much movement the selected option expiry prices in.",
        "Compare its ATM IV with past realized movement. Use the 30-day rank for the longer-dated benchmark.",
        "IV of 24% and past realized volatility of 20% give 1.20×. The premium is rich on that comparison. Direction comes from Trade Plan.", "Volatility", DOC + "options-volatility/options-volatility"),
    "dark": Guide("Dark-pool prints and levels", "Market regime",
        "These levels show where large off-exchange share trades clustered.",
        "Watch how price reacts if it returns to the area.",
        "Several prints around $760 make that price worth tracking. The report alone does not tell you the trader’s intent.", "Dark Pool", DOC + "darkpool/darkpool-levels"),
    "readiness": Guide("Move readiness", "Setups and review",
        "The detailed context shows the earlier five-minute readiness checklist.",
        "Use the main Entry Command Center for the current decision and shorter timeframes.",
        "A five-minute breakout can look ready while the one-minute entry timing is still falling.", "Trade Plan"),
    "timing": Guide("Time of day", "Setups and review",
        "Compare today with how far price typically moved at this time.",
        "Check the number of past sessions behind the comparison.",
        "A typical move of 0.25% means the median absolute price change across the displayed 30-minute windows was 0.25%.", "Trade Plan"),
    "signals": Guide("Tradytics signals", "Setups and review",
        "Signals flag a recurring pattern in options trades.",
        "Open one to see the contract and the trades behind it.",
        "Compare the ask-based entry with later marks and settlement when available.", "Signals", DOC + "signals/signals"),
    "performance": Guide("Signal performance", "Setups and review",
        "See how past Tradytics signals performed.",
        "Compare settled results and the number of signals in each group.",
        "A few big winners can lift the average. Check the median for a more typical result.", "Signals", DOC + "signals/signals-performance"),
    "forecast": Guide("Monte Carlo forecast", "Setups and review",
        "The model runs many possible paths from recent price candles.",
        "Read the shaded area as a range of possible outcomes.",
        "Compare the 15-minute range with the distance to a level you are watching.", "Forecast"),
    "trade_probability": Guide("Trade probability and payoff", "Setups and review",
        "Compare entry methods using the same stop and targets from Trade Plan.",
        "Check fill rate, net expectancy and the downside. Use Validation to see how predictions performed.",
        "An EMA retest may have higher target odds when it fills. It can still have lower expectancy per opportunity if it rarely fills.", "Forecast"),
    "calibration": Guide("Forecast calibration", "Setups and review",
        "Check how often earlier forecasts covered the actual price.",
        "Compare the observed coverage with the label on the range.",
        "If a 68% range contained half the actual closes, it was too narrow on that sample.", "Forecast"),
    "scanner": Guide("Stock scanner", "Setups and review",
        "Find stocks with high options premium, volume or dark-pool activity.",
        "Check when the scan was built before using a result.",
        "Rank by premium versus usual to find names that are unusually active for themselves.", "Scanner", DOC + "screener/screener-stocks"),
    "replay": Guide("Event replay", "Setups and review",
        "See how the underlying moved after a print, signal or range break.",
        "Compare 15, 30 and 60 minutes after the event.",
        "A +0.4% result after a call print describes the stock move. Option prices can move differently.", "History"),
    "tradingview": Guide("TradingView levels", "Exports and data",
        "Turn the current levels into a TradingView indicator.",
        "Copy the generated script into Pine Editor on the same ticker.",
        "After the gamma flip changes, generate a new snapshot and update the script on your chart.", "TradingView", "https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/"),
    "health": Guide("Data health and clocks", "Exports and data",
        "See which feeds are updating and when the data was recorded.",
        "Check the source time first. Old data can arrive in a new response.",
        "A prior-session badge means the observation is from an earlier market day.", "Data Health", DOC + "quickstart"),
    "history": Guide("Daily history", "Exports and data",
        "Compare each day’s positioning, volatility or dark-pool value.",
        "Use the same ticker and series when comparing days.",
        "Daily gamma history and the latest exact-expiry snapshot can cover different sets of contracts.", "History"),
}


def explanation(topic, key):
    guide = GUIDES[topic]
    with st.popover("?", key=key, help=f"How to read {guide.title}", type="secondary"):
        st.markdown(f"**{guide.title}**")
        st.write(guide.meaning)
        st.write(guide.read)
        st.caption("See Learn for an example.")


def load_sections(filename):
    text = (Path(__file__).parent / filename).read_text(encoding="utf-8")
    return dict(section.strip().split("\n", 1) for section in ("\n" + text).split("\n## ") if section.strip())


def learn_page(go_to, nav_labels, pages):
    st.subheader("Learn")
    st.write("Read each chart, check what supports a setup, and build a checklist for your holding period.")
    mode = st.segmented_control("Guide", ["Charts and tools", "Trade workflows"],
                                default="Charts and tools", key="learn_mode")
    if mode == "Trade workflows":
        workflows = load_sections("learn_workflows.md")
        st.markdown(
            "| Trade | Start with | Check before entry |\n"
            "| --- | --- | --- |\n"
            "| Day trade | 5m trend, 3m structure, 1m timing | Matching flow, target room, quote spread and session time |\n"
            "| Swing trade | Daily structure, sector and catalyst | Exact expiry, overnight gap scenarios and IV changes |\n"
            "| LEAPS | Weekly and daily thesis, time to play out | Far-dated chain, delta, vega, time value and full debit at risk |"
        )
        workflow = st.selectbox("Holding period", list(workflows), key="learn_holding_period")
        st.markdown(f"### {workflow}")
        st.markdown(workflows[workflow])
        return

    sections = load_sections("learn_charts.md")
    available = [page for page in sections if page in pages]
    page = st.selectbox("Dashboard page", available,
                        format_func=lambda value: nav_labels.get(value, value), key="learn_page_topic")
    st.button(f"Open {nav_labels.get(page, page)}", on_click=go_to, args=(page,), key="learn_open_page")
    entries = sections[page].strip().split("\n### ")
    for index, entry in enumerate(entries):
        title, body = entry.removeprefix("### ").split("\n", 1)
        with st.expander(title, expanded=index == 0):
            st.markdown(body)
