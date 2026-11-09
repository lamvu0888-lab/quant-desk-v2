# Quant Desk 2.0

Quant Desk combines Tradytics market data, dealer positioning, flow and signals with the Quant Options Agent Monte Carlo model. Open any supported ticker in the command bar. The quick buttons open SPY, SPX and QQQ.

## Start on Windows

1. Extract the full ZIP to a new folder. Install Python 3.12 if needed.
2. Double-click **START_QUANT_DASHBOARD.cmd**. It creates a local environment, installs dependencies on first run, and starts the dashboard at **http://localhost:8501**.
3. Open the newly created `.env` next to the launcher and set `TRADYTICS_API_KEY` to your existing key. Save it and restart the launcher. Enable the **market**, **options**, **darkpool**, and **signals** scopes on your Tradytics key for the feeds you want.

The first launch opens the labeled Preview workspace when no keys are configured. Preview includes illustrative values for each page so you can learn the controls. Select **Live** to use your configured feed. The application runs locally and keeps the key in its server process.

On Mac or Linux, run `bash start_dashboard.sh`. Python 3.11+ is required. The dashboard listens on localhost. Stop an older dashboard if port 8501 is occupied.

## Workspace

| Page | What you can do |
| --- | --- |
| Overview | Open a ticker, view price, EMA 9/21, session VWAP where available, nearby walls, net flow and market breadth. |
| Options Flow | View net calls, net puts and cumulative directional net premium. Filter the ticker's tape by premium, type, kind, side, session and DTE. |
| Positioning | View Gamma, Vanna, Charm and Delta by strike or expiry. Filter to 0DTE, a DTE window or an exact expiry. Inspect the option chain or compare Tradytics with the Webull model. |
| Volatility | Compare 30-day ATM IV with realized volatility, IV rank, percentile, expiry term structure, skew and expected move. |
| Dark Pool | See reported off-exchange stock prints and price bins by notional. Choose a lookback and minimum print size. |
| Signals | Filter Tradytics patterns for a ticker, inspect the chosen contract and marks, and view provider-wide pattern performance. |
| Forecast | Run the block bootstrap or GBM model over 5, 15, 30 and 60 minutes during the regular session. See 68% and 95% ranges, neutral-zone probabilities, level touches and recent calibration. |
| History | Review daily provider Gamma or Delta, volatility, and dark-pool notional. |
| Learn | Choose a topic for the definition, reading guide and worked example. Use its button to open the corresponding page. |
| Data Health | Inspect source time, API receipt time, errors and required scopes. Download diagnostics for troubleshooting. |

Click **?** next to a panel title to show its explanation. Click outside or press Esc to close it. CSV and JSON downloads use the loaded view and label Preview data in the filename.

## Data sources and scope

The Tradytics client uses the current public REST endpoints and API key query parameter. Keep `TRADYTICS_AUTH_MODE=query` for the current quickstart. The optional `bearer` setting supports an account setup that specifies header authentication. Your key controls which scopes the terminal can access. Requests stay on the documented host. Failures appear in Data Health without credential details.

- **Supported symbols.** Enter a Tradytics ticker. Major index quotes, including SPX, use the dedicated index endpoint. Index candle volume and VWAP are blank when the provider omits them. Option, volatility, dark-pool and signal coverage varies by ticker and session.
- **Net flow.** The session chart uses the provider aggregate. The tape shows up to 250 matching records and polls new prints by ID between periodic refreshes. A print's side can be provisional. The flow feed documents a $25K coverage floor. Market scope in the net premium chart is the provider's single-stock universe.
- **Dark pool.** This page uses share-trade notional and excludes exchange venue rows from the tape. The Options Flow page handles option sweeps and blocks.
- **Exposure.** Tradytics reports a dealer model about every five minutes. Use the reported unit and expiry scope. The chart may focus near spot while the total still covers the full selected scope.
- **Signal results.** Pattern performance uses the Tradytics entry and mark convention. Set Settled only to review completed contracts. The summary covers the provider's patterns across tickers.

Quotes typically refresh at Tradytics about every 30 seconds. The workspace requests each panel on its own schedule and labels the observation time. The app keeps the latest successful snapshot through a transient failure and marks it cached. The shared client respects retry timing and a local request budget. Use **Feed controls** to pause or refresh.

## Webull and forecasts

Webull is optional. If you want to compare exposure, add your existing `WEBULL_APP_KEY` and `WEBULL_APP_SECRET` to the new `.env`. Under **Feed controls**, enable **Webull comparison**, then open **Positioning → Compare sources**. You can also select Webull as the stock/ETF price and candle source. Tradytics supplies the major index quote path and daily charts. Webull retains the V1.9 zero-delay forecast checks.

The comparison preserves each model's units and scope. Tradytics Vanna is dollars per IV point. The Webull model reports shares per IV point. The Webull collection settings and local reports follow the V1.9 model described in [the archived guide](docs/history/README_V1_9.md).

The forecast uses completed regular-session candles and a recent quote from the selected price feed. Tradytics forecast readiness uses source timestamps and checks any reported delay. Horizons stop at the session close. Market context and provider levels appear next to the simulation. The paths use historical price returns, and the levels are used for touch probabilities. The calibration button shows coverage on recent nonoverlapping observations.

## References

- [Tradytics quickstart](https://tradytics.com/docs/quickstart)
- [Tradytics API reference](https://tradytics.com/docs)
- [Tradytics rate limits](https://tradytics.com/docs/rate-limits)
- [Dealer exposure schema](https://tradytics.com/docs/options-positioning/options-exposure)
- [Signal performance schema](https://tradytics.com/docs/signals/signals-performance)
