# Quant Desk 2.1

Quant Desk combines Tradytics market data, dealer positioning, flow and signals with the Quant Options Agent Monte Carlo model. Open any supported ticker in the command bar. The quick buttons open SPY, SPX and QQQ.

## Start on Windows

1. Extract the full ZIP to a new folder. Install Python 3.12 if needed.
2. Double-click **START_QUANT_DASHBOARD.cmd**. It creates a local environment, installs dependencies on first run, and starts the dashboard at **http://localhost:8501**.
3. Open the newly created `.env` next to the launcher and set `TRADYTICS_API_KEY` to your existing key. Save it and restart the launcher. Enable **market**, **options**, **darkpool**, **signals**, and **scanner** for the feeds you use.

The first launch opens the labeled Preview workspace when no keys are configured. Preview includes illustrative values for each page so you can learn the controls. Select **Live** to use your configured feed. The application runs locally and keeps the key in its server process.

On Mac or Linux, run `bash start_dashboard.sh`. Python 3.11+ is required. The dashboard listens on localhost. Stop an older dashboard if port 8501 is occupied.

## Update your existing Streamlit app

Start in the GitHub repo folder you used for the first deployment. Run `git status --short` first. If it is clean, bring in the latest remote changes.

```powershell
git pull --rebase origin main
```

Extract the ZIP outside the repo, then copy its contents into the repo root so `terminal.py` is next to `requirements.txt`. Check the staged file list before pushing.

```powershell
git add -A
git diff --cached --name-only
git commit -m "Upgrade Quant Desk to 2.1"
git push origin main
```

Open the existing Streamlit URL after the rebuild and look for **2.1** in the sidebar. The main file is still `terminal.py`. For a local install, use `.env`. For Streamlit Cloud, enter your existing key under **Secrets** as TOML.

```toml
TRADYTICS_API_KEY = "your_key_here"
TRADYTICS_AUTH_MODE = "query"
```

## Workspace

| Page | What you can do |
| --- | --- |
| Overview | See the 5m regime, price, large-order markers, nearby levels, cumulative flow, momentum and estimated share CVD. |
| Flow & Bubbles | Filter the executed options tape by dollars, call or put, trade side, kind, session and expiry. Compare premium bubbles, flow momentum and contract-volume delta. |
| Money Map | Compare market leaders, sectors or a watchlist. Tile size represents absolute net options premium. |
| Dealer Positioning | View Gamma, Delta, Vanna and Charm by strike or expiry, inspect contracts, or compare the optional Webull model. |
| Market Regime | Compare the current 5m price regime with implied volatility, expected move and market breadth. |
| Dark Pool | See reported off-exchange stock prints and price bins by notional. Choose a lookback and minimum print size. |
| Trade Plan | See the rule-based setup state, direction, trigger, invalidation and the evidence behind the score. Download the setup evidence as JSON. |
| Signals | Filter Tradytics patterns for a ticker, inspect the chosen contract and marks, and view provider-wide pattern performance. |
| Forecast | Run the block bootstrap or GBM model over 5, 15, 30 and 60 minutes during the regular session. See 68% and 95% ranges, neutral-zone probabilities, level touches and recent calibration. |
| Scanner | Rank stocks or ETFs by provider premium, volume, IV or dark-pool activity. |
| History & Replay | Review daily series and measure underlying returns after large prints, signals or closed-bar range breaks. |
| TradingView | Generate a Pine indicator or JSON snapshot of GEX strikes, gamma levels, dark-pool levels and support or resistance. |
| Learn | Choose a topic for the definition, reading guide and worked example. Use its button to open the corresponding page. |
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

The price regime measures recent five-minute price swings. It can read Compressing, Normal or Expanding. The readiness score adds rule points for range compression, proximity to an edge, changes in flow, expansion, volume and modeled gamma. Direction comes from the last 15 minutes of price and options flow. A setup trigger uses a completed five-minute close outside the prior range and agreement from flow. The score describes evidence, and its replay reports underlying price moves.

Open **Trade Plan → How the score adds up** for the current points. **History & Replay → Event replay** shows 15, 30 and 60 minute underlying returns and returns to the close. Range-break replay uses only price bars and flow intervals available at each origin. It uses finalized provider data, which can differ from what was visible live. Review sample sizes and the forecast calibration on the Forecast page separately.

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

## V2.2: Discord monitoring and 0DTE Overview GEX

- Overview now has a **separate 0DTE GEX-by-strike** chart, regardless of the global expiration selection. A stale or missing exposure feed displays an unavailable message instead of substituting all-DTE data.
- Chart legends are placed **below the charts**, with larger text and reserved space.
- Run the **read-only** Discord monitor as a separate process with `python discord_monitor.py --interval 30`. It requires `TRADYTICS_API_KEY` and `DISCORD_WEBHOOK_URL` in `.env` or environment variables. Never commit the webhook or API key. `--once` is a diagnostic run.
- The monitor checks SPY and SPX, uses the existing completed-5-minute-bar Trade Plan, and sends only eligible GET READY / ENTER SETUP alerts. It deliberately **does not place orders**, does not use preview data, and suppresses signals when market is closed or feeds are stale/misaligned. Repeated alerts for the same bar/state are suppressed while the process remains running.
- For true 24/7 operation, deploy `discord_monitor.py` to an **always-on Python worker** with environment secrets. Streamlit Community Cloud alone does not provide a persistent background worker. Polling more frequently than provider refresh intervals does not create fresh data; follow API rate limits.
- **Important:** Entry classification is based on existing Trade Plan state and evidence score. Validate signal quality in paper mode before relying on alerts. The agent does not claim calibrated probability or guaranteed execution price.
