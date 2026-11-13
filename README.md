# Quant Desk 2.7.1

Quant Desk uses Webull for stock and ETF price timing when configured, and Tradytics for index prices, options flow and modeled dealer exposure. Open any covered ticker in the command bar. The quick buttons open SPY, SPX and QQQ. The dashboard and Discord worker run in analysis and paper mode.

- [Read the earlier Movement Detector rules](V2_6_RELEASE.md)
- [Deploy to your existing GitHub, Streamlit and Render services](V2_5_DEPLOYMENT.md)
- [Read the decision rules, model assumptions and limitations](V2_5_METHODS.md)
- [See what changed](CHANGELOG.md) and [review verification](VALIDATION.md).

## Start on Windows

1. Extract the full ZIP to a new folder. Install Python 3.12 if needed.
2. Double-click **START_QUANT_DASHBOARD.cmd**. It creates a local environment, installs dependencies on first run, and starts the dashboard at **http://localhost:8501**.
3. Open the newly created `.env` next to the launcher and set your existing `TRADYTICS_API_KEY`, `WEBULL_APP_KEY` and `WEBULL_APP_SECRET`. Save it and restart the launcher. Enable the Tradytics scopes for the feeds you use.

The first launch opens the labeled Preview workspace when no keys are configured. Preview includes illustrative values for each page so you can learn the controls. Select **Live** to use your configured feed. The application runs locally and keeps the key in its server process.

On Mac or Linux, run `bash start_dashboard.sh`. Python 3.11+ is required. The dashboard listens on localhost. Stop an older dashboard if port 8501 is occupied.

## Update your existing Streamlit app

Use the PowerShell steps in the [deployment guide](V2_5_DEPLOYMENT.md) from your already-extracted folder. The Streamlit entry point is `terminal.py`. Render runs `python discord_monitor.py --interval 30` as a background worker.

## Workspace

| Page | What you can do |
| --- | --- |
| Overview | Start with the Entry Command Center. See the current status, 1m/3m/5m alignment, nearby levels, price and large-order markers, 0DTE GEX, cumulative flow, momentum and estimated share CVD. |
| Movement Detector | Read nine ordered checks from market state through data quality and open the current Trade Plan. |
| Flow & Bubbles | Filter the executed options tape by dollars, call or put, trade side, kind, session and expiry. Compare premium bubbles, flow momentum and contract-volume delta. |
| Money Map | Compare market leaders, sectors or a watchlist. Tile size represents absolute net options premium. |
| Dealer Positioning | Review price, IV and time hedge scenarios, sensitive strikes, past price response, exposure profiles and the optional Webull comparison. |
| IV Decision Center | Compare exact-expiry ATM IV, realized volatility, the 30-day benchmark, skew, and matching expected-move room. Advanced Analysis contains the detailed charts. |
| Dark Pool | See reported off-exchange stock prints and price bins by notional. Choose a lookback and minimum print size. |
| Trade Plan | Review the trigger, stop, targets, contract bid and ask, quote age, evidence and Discord alerts. |
| Signals | Filter Tradytics patterns for a ticker, inspect the chosen contract and marks, and view provider-wide pattern performance. |
| Monte Carlo | Compare four entry methods with fill odds, target/stop odds and net modeled P&L. Open Validation for paper outcomes or replay. Price forecast contains the earlier range models. |
| Scanner | Rank stocks or ETFs by provider premium, volume, IV or dark-pool activity. |
| TradingView | Generate a Pine indicator or JSON snapshot of GEX strikes, gamma levels, dark-pool levels and support or resistance. |
| Learn | Read chart-by-chart explanations and worked examples, or open the day trade, swing trade and LEAPS workflows. |
| Data Health | Compare provider quote timestamps and inspect the latest candle, flow interval and SPXW quote coverage. |

Click **?** beside a title for a quick explanation. Click outside or press Esc to close it. **Learn** has examples. Preview downloads carry a demo label.

## Exact DTE

The sidebar offers **All DTE**, **Nearest listed**, **0 DTE**, **Listed expiration** and **Enter exact DTE**. Nearest listed uses 0 DTE when listed and otherwise uses the next listed date. It is the default for a new browser session with both feeds configured. DTE means calendar days from the displayed session. Historical flow and tape use the selected session as their date reference.

The tape, option chain and exposure model query the exact date. Tradytics uses a maximum DTE filter for the aggregate timeline. The maximum DTE total already covers one expiration when the current listing verifies it as the first date. For other exact values above zero, Quant Desk subtracts adjacent DTE totals for matching completed intervals. The chart skips intervals that cannot be matched. Market-wide leader and sector tiles, scanner rankings, and daily exposure history use the provider's all-DTE datasets.

## TradingView levels

1. Choose a ticker and expiry. Open **TradingView** in the sidebar.
2. Select your layers and click **Generate level snapshot**.
3. Copy the code into **Pine Editor** on the same ticker in TradingView, then save it and choose **Add to chart**.

The lines extend forward from the export time. Generate a new snapshot after levels change. Pine uses the values embedded by Quant Desk, so the exported script contains level values and no API credentials. SPX has no dark-pool share levels. Open SPY or another ETF to export its own levels.

## Reading the setup and CVD

The price regime measures recent five-minute price swings. It can read Compressing, Normal or Expanding. The Entry Command Center checks the five-minute trend and signed options flow, then uses completed one- and three-minute candles for timing. Entry also requires two agreeing completed 5m price checks and two aligned 5m premium intervals. The evidence score summarizes rule agreement. The Monte Carlo page reports scenario probabilities separately.

Open **Trade Plan → Evidence and entry conditions** for the evidence and the reason a setup is waiting. **Monte Carlo → Validation** includes paper outcomes and setup replay. Strategy Lab and History & Replay are hidden from the workspace navigation.

Share CVD estimates signed volume from completed one-minute OHLCV bars. It adds volume from up minutes and subtracts volume from down minutes, then resets at the regular-session open. Flat minutes use the change from the previous close or the last known direction. Index charts can show a separately labeled SPY proxy. The optional contract-volume view counts classified buys minus sells in the loaded options tape. Share CVD, contract-volume delta and directional net premium have different units.

## Data sources and scope

The Tradytics client uses the current public REST endpoints and API key query parameter. Keep `TRADYTICS_AUTH_MODE=query` for the current quickstart. The optional `bearer` setting supports an account setup that specifies header authentication. Your key controls which scopes the terminal can access. Requests stay on the documented host. Failures appear in Data Health without credential details.

- **Supported symbols.** Enter a Tradytics ticker. Major index quotes, including SPX, use the dedicated index endpoint. Index candle volume and VWAP are blank when the provider omits them. Option, volatility, dark-pool and signal coverage varies by ticker and session.
- **Net flow.** The session chart uses the provider aggregate. The tape loads up to 500 recent matching records and polls by ID between periodic refreshes. Bubble ranks and review points apply to the loaded sample. The flow feed documents a $25K coverage floor. Market scope in the net premium chart is the provider's single-stock universe.
- **Dark pool.** This page uses share-trade notional and excludes exchange venue rows from the tape. The Options Flow page handles option sweeps and blocks.
- **Exposure.** Tradytics reports a dealer model about every five minutes. Use the reported unit and expiry scope. The chart may focus near spot while the total still covers the full selected scope.
- **Signal results.** Pattern performance uses the Tradytics entry and mark convention. Set Settled only to review completed contracts. The summary covers the provider's patterns across tickers.

Quotes typically refresh at Tradytics about every 30 seconds. The workspace requests each panel on its own schedule and labels the observation time. The app keeps the latest successful snapshot through a transient failure and marks it cached. The shared client respects retry timing and a local request budget. Use **Feed controls** to pause or refresh.

## Webull and Tradytics together

Set **Feed controls → Price and candle source → Auto**. Auto uses configured Webull OpenAPI for supported stocks and ETFs and Tradytics for SPX and other indices. Webull requests a warm history, then fetches recent completed minutes after each close. Prices and candles carry their own market timestamps. An explicit provider delay, stale market timestamp or sandbox data holds the entry check. Webull OpenAPI needs its own data entitlement. See [Webull market data permissions](https://developer.webull.com/apis/docs/market-data-api/overview/).

Choose an exact expiry to see Webull bid, ask, spread, quote age and Greeks. The Webull view samples up to 20 near-spot contracts and requests PM-settled SPXW contracts for SPX. **Trade Plan** checks the nearest contract in the setup direction and waits on stale, wide or incomplete quotes. **IV Decision Center** uses a same-strike call and put to estimate exact-expiry ATM IV when both quotes are fresh. **Movement Detector** compares 1, 3 or 5 minute IV changes only within the same provider, expiry and strike pair. The Tradytics surface supplies the 30-day benchmark and rank.

**Dealer Positioning → Compare sources** displays a bounded Webull model when enough fresh calls and puts span spot. The Tradytics model and Webull sample keep their own scope and units. Use **Data Health** to compare quote ages and test your account's SPXW access.

## References

- [Tradytics quickstart](https://tradytics.com/docs/quickstart)
- [Tradytics API reference](https://tradytics.com/docs)
- [Tradytics rate limits](https://tradytics.com/docs/rate-limits)
- [Dealer exposure schema](https://tradytics.com/docs/options-positioning/options-exposure)
- [Signal performance schema](https://tradytics.com/docs/signals/signals-performance)
- [TradingView Pine lines and boxes](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/)
- [TradingView cumulative volume delta](https://www.tradingview.com/support/solutions/43000725058-cumulative-volume-delta/)

## Discord monitoring

Open **Feed controls → Discord ticker monitoring** to edit the watchlist. The default is SPY and SPX. The worker checks the feed cache every two seconds during the session and re-evaluates when completed price or flow changes. Provider refresh and network timing determine alert latency. The normal scan interval remains configurable and the worker uses the nearest listed expiry.

**Move watch** uses completed price candles. A 1-minute move needs at least 0.10%, and a 3-minute move needs at least 0.18%. Both must break the recent range and exceed a threshold based on recent candle ranges. Rolling 5 and 10 minute moves can also qualify at 0.25% and 0.35%. The cooldown is 15 minutes per ticker. Confirmed setups require matching completed 5-minute price and flow, with the Webull contract check when configured. Discord sends short move, entry and paper exit messages. Feed issues appear in Data Health. See the [deployment guide](V2_5_DEPLOYMENT.md) for worker environment variables and the read-only check.
