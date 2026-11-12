# Quant Desk 2.6.3

Quant Desk combines Tradytics market data and optional Webull data with intraday entry rules, dealer hedge scenarios, volatility context and paper trade evaluation. Open any covered ticker in the command bar. The quick buttons open SPY, SPX and QQQ. The dashboard and Discord worker run in analysis and paper mode.

- [Read the Movement Detector rules and historical strategy audit](V2_6_RELEASE.md)
- [Deploy to your existing GitHub, Streamlit and Render services](V2_5_DEPLOYMENT.md)
- [Read the decision rules, model assumptions and limitations](V2_5_METHODS.md)
- [See what changed](CHANGELOG.md) and [review verification](VALIDATION.md).

## Start on Windows

1. Extract the full ZIP to a new folder. Install Python 3.12 if needed.
2. Double-click **START_QUANT_DASHBOARD.cmd**. It creates a local environment, installs dependencies on first run, and starts the dashboard at **http://localhost:8501**.
3. Open the newly created `.env` next to the launcher and set `TRADYTICS_API_KEY` to your existing key. Save it and restart the launcher. Enable **market**, **options**, **darkpool**, **signals**, and **scanner** for the feeds you use.

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
| Trade Plan | Review the trigger, separate invalidation and stop, targets, risk/reward, evidence, transitions, paper outcomes and Discord history. |
| Signals | Filter Tradytics patterns for a ticker, inspect the chosen contract and marks, and view provider-wide pattern performance. |
| Monte Carlo | Compare four entry methods with fill odds, target/stop odds and net modeled P&L. Open Validation for paper outcomes or replay. Price forecast contains the earlier range models. |
| Scanner | Rank stocks or ETFs by provider premium, volume, IV or dark-pool activity. |
| History & Replay | Review daily series and measure underlying returns after large prints, signals or closed-bar range breaks. |
| TradingView | Generate a Pine indicator or JSON snapshot of GEX strikes, gamma levels, dark-pool levels and support or resistance. |
| Learn | Choose a topic for the definition, reading guide and worked example. Use its button to open the corresponding page. |
| Strategy Lab | Review all 100 strategies, data requirements and selected walk-forward folds for the saved SPY sample. |
| Data Health | Inspect source time, API receipt time, errors and required scopes. Download diagnostics for troubleshooting. |

Click **?** beside a title for a quick explanation. Click outside or press Esc to close it. **Learn** has examples. Preview downloads carry a demo label.

## Exact DTE

The sidebar offers **All DTE**, **0 DTE**, **Listed expiration** and **Enter exact DTE**. The number means calendar days from the displayed regular session. An exact value of 3 targets contracts expiring three calendar days after that session. The chart shows an empty state when there is no matching data. Historical flow and tape use the selected session as their date reference.

The tape, option chain and exposure model query the exact date. Tradytics documents a maximum-DTE filter for the aggregate timeline. For an exact value above zero, Quant Desk subtracts the two adjacent DTE totals for matching completed intervals. The chart skips intervals that cannot be matched. The Flow page can show a separate premium chart based on the loaded tape. Market-wide leader and sector tiles, scanner rankings, and daily exposure history use the provider's all-DTE datasets. Watchlist tiles use the selected expiry when comparable intervals are available.

## TradingView levels

1. Choose a ticker and expiry. Open **TradingView** in the sidebar.
2. Select your layers and click **Generate level snapshot**.
3. Copy the code into **Pine Editor** on the same ticker in TradingView, then save it and choose **Add to chart**.

The lines extend forward from the export time. Generate a new snapshot after levels change. Pine uses the values embedded by Quant Desk, so the exported script contains level values and no API credentials. SPX has no dark-pool share levels. Open SPY or another ETF to export its own levels.

## Reading the setup and CVD

The price regime measures recent five-minute price swings. It can read Compressing, Normal or Expanding. The Entry Command Center checks the five-minute trend and signed options flow, then uses completed one- and three-minute candles for timing. Entry also requires two agreeing completed 5m price checks and two aligned 5m premium intervals. The evidence score summarizes rule agreement. The Monte Carlo page reports scenario probabilities separately.

Open **Trade Plan → Evidence and entry conditions** for supporting checks, missing information, conflicting evidence and reasons to stand aside. **History & Replay → Event replay** retains the earlier 15, 30 and 60 minute underlying comparisons. **Monte Carlo → Validation** includes the new setup replay and forward paper outcomes.

Share CVD estimates signed volume from completed one-minute OHLCV bars. It adds volume from up minutes and subtracts volume from down minutes, then resets at the regular-session open. Flat minutes use the change from the previous close or the last known direction. Index charts can show a separately labeled SPY proxy. The optional contract-volume view counts classified buys minus sells in the loaded options tape. Share CVD, contract-volume delta and directional net premium have different units.

## Data sources and scope

The Tradytics client uses the current public REST endpoints and API key query parameter. Keep `TRADYTICS_AUTH_MODE=query` for the current quickstart. The optional `bearer` setting supports an account setup that specifies header authentication. Your key controls which scopes the terminal can access. Requests stay on the documented host. Failures appear in Data Health without credential details.

- **Supported symbols.** Enter a Tradytics ticker. Major index quotes, including SPX, use the dedicated index endpoint. Index candle volume and VWAP are blank when the provider omits them. Option, volatility, dark-pool and signal coverage varies by ticker and session.
- **Net flow.** The session chart uses the provider aggregate. The tape loads up to 500 recent matching records and polls by ID between periodic refreshes. Bubble ranks and review points apply to the loaded sample. The flow feed documents a $25K coverage floor. Market scope in the net premium chart is the provider's single-stock universe.
- **Dark pool.** This page uses share-trade notional and excludes exchange venue rows from the tape. The Options Flow page handles option sweeps and blocks.
- **Exposure.** Tradytics reports a dealer model about every five minutes. Use the reported unit and expiry scope. The chart may focus near spot while the total still covers the full selected scope.
- **Signal results.** Pattern performance uses the Tradytics entry and mark convention. Set Settled only to review completed contracts. The summary covers the provider's patterns across tickers.

Quotes typically refresh at Tradytics about every 30 seconds. The workspace requests each panel on its own schedule and labels the observation time. The app keeps the latest successful snapshot through a transient failure and marks it cached. The shared client respects retry timing and a local request budget. Use **Feed controls** to pause or refresh.

## Webull and forecasts

Webull is optional. If you want to compare exposure, add your existing `WEBULL_APP_KEY` and `WEBULL_APP_SECRET` to the new `.env`. Under **Feed controls**, enable **Webull comparison**, then open **Dealer Positioning → Compare sources**. You can also select Webull as the stock/ETF price and candle source. Tradytics supplies major index quotes and historical replay. Webull retains the V1.9 zero-delay forecast checks.

The comparison preserves each model's units and scope. Tradytics Vanna is dollars per IV point. The Webull model reports shares per IV point. The Webull collection settings and local reports follow the V1.9 model described in [the archived guide](docs/history/README_V1_9.md).

The forecast uses completed regular-session candles and a recent quote from the selected price feed. Tradytics forecast readiness uses source timestamps and checks any reported delay. Horizons stop at the session close. Market context and provider levels appear next to the simulation. The paths use historical price returns, and the levels are used for touch probabilities. The calibration button shows coverage on recent nonoverlapping observations.

## References

- [Tradytics quickstart](https://tradytics.com/docs/quickstart)
- [Tradytics API reference](https://tradytics.com/docs)
- [Tradytics rate limits](https://tradytics.com/docs/rate-limits)
- [Dealer exposure schema](https://tradytics.com/docs/options-positioning/options-exposure)
- [Signal performance schema](https://tradytics.com/docs/signals/signals-performance)
- [TradingView Pine lines and boxes](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/)
- [TradingView cumulative volume delta](https://www.tradingview.com/support/solutions/43000725058-cumulative-volume-delta/)

## Discord monitoring

Open **Feed controls → Discord ticker monitoring** to edit the watchlist. The default is SPY and SPX. The worker monitors Tradytics 0DTE at a 30-second target cadence with the same entry rules used in the dashboard. **Trade Plan** shows setup transitions, paper outcomes and delivered messages. Discord sends confirmed entries, tracked exits and sustained data warnings. A 10-minute cooldown applies per ticker and setup. **Feed controls → Send Discord test message** checks webhook connectivity. See the [deployment guide](V2_5_DEPLOYMENT.md) for the required environment variables, journal storage and the worker's dry run.
