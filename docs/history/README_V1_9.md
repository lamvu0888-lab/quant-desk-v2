# Quant Options Agent 1.9 archive

A single white dashboard for official Webull market observations, modeled Gamma/Vanna/Charm, and short-horizon Monte Carlo price distributions. This is a local, read-only research application. It contains no order submission.

## Start on Windows

1. Extract the entire ZIP to a new folder. Install Python 3.12 if needed.
2. Double-click **START_QUANT_DASHBOARD.cmd**. The launcher creates the Python environment, installs dependencies, creates `.env` when missing, and opens the app at **http://localhost:8501**.
3. Open `.env` in Notepad. Copy your existing `WEBULL_APP_KEY` and `WEBULL_APP_SECRET` into the new file. Keep `WEBULL_ENDPOINT=api.webull.com`. Click **Connect / reconnect Webull** in the dashboard after saving.

Use the new `.env.example` for the settings. Copy your existing credentials rather than replacing the new configuration with an older `.env`: V1.8 used a sandbox default and looser timestamp limits. For compatibility, the old 90-second `QUOTE_MAX_AGE_SECONDS` value is accepted but capped to 30 seconds for live forecasts. Never send anyone your `.env` or API credentials.

Run this one launcher. The price collector, options collector, charts and models run inside it. Stop any older dashboard or scanner before starting. If port 8501 is occupied, the launcher tells you instead of starting a duplicate app. It installs again only when requirements change.

**Mac/Linux:** run `bash start_dashboard.sh` from this folder. Python 3.11+ is required; 3.12 is the tested/recommended version. The app listens on localhost only.

## What the dashboard does

| Area | Behavior |
| --- | --- |
| Market overview | Actual Webull one-minute OHLCV; 1m/3m/5m/15m/1h views; EMA 9/21; volatility metrics |
| Monte Carlo forecast | 5/15/30/60-minute probabilities and 68%/95% price ranges; 5k/10k/20k paths; selectable model and direction assumption |
| Gamma / Vanna / Charm | Total and strike-level exposure scenarios, Delta, OI walls, modeled Gamma flip and explicit coverage/exclusion counts |
| Data health | Per-source timestamps, server-reported delay, API budget, cooldowns, errors and exports |

Default collection schedules: price every **5 seconds**, one-minute candles every **60 seconds**, option snapshots every **120 seconds**. Contract discovery is reused for up to **15 minutes**, refreshed earlier when spot moves sufficiently or the New York date changes. API budgets and network latency can lengthen these schedules; actual timestamps are always shown. HTTP polling is used, not a tick-by-tick streaming feed.

The shared budget permits 45 requests/minute by default and spaces requests at least 1.05 seconds apart. One option batch contains at most 20 symbols. Options collection yields between requests so stock prices can continue updating. HTTP 429 errors, including SDK-raised exceptions, cause an automatic cooldown. Other errors retry without replacing observed data with demo values.

The default option window covers **today through seven calendar days**, **±2% strikes**, standard root only, at most 500 contracts. Discovery queries each exact expiry separately and follows all pagination keys. Narrow the window if the contract/page cap is reached. Excluded contracts are counted; invalid IV, OI, multipliers or timestamps are never silently replaced with zero. Exposures describe the usable subset of this window, not all outstanding options.

## Requirements for live data

- A Webull OpenAPI application with valid credentials and permissions for the requested endpoints.
- Appropriate **OpenAPI** stock/ETF and options data access. A subscription purchased for Webull Desktop/mobile does not automatically apply to OpenAPI.
- A working internet connection and an accurate local clock.
- For a live forecast: regular session open, production endpoint, a recent observed price, recent completed candles, and zero-delay evidence from Webull.

Some snapshot responses omit `delay_minutes`. This version checks the same ticker's candle response too. If that response reports zero delay and was retrieved recently, the UI identifies that as the stock entitlement evidence. A positive delay on the stock snapshot takes precedence. Candle delay must also be explicitly zero. Missing metadata is labeled **unverified**, never assumed to be zero. A recent receipt timestamp alone does not prove real-time entitlement.

The price used by the forecast is the recent last trade. If that trade is stale but a valid, recently timestamped bid/ask quote is available, the midpoint can be used and is labeled accordingly. A fresh quote timestamp cannot make an old trade price current. Price/receipt age defaults to at most 15 seconds. Candle freshness is checked separately. Pausing polling or a failed latest price/candle request pauses live forecasting.

The NYSE calendar handles holidays, daylight-saving changes and early closes. Forecast horizons never extend beyond the current regular-session close. Before the open, after the close, on holidays, or with delayed/stale data, observations remain visible but the **live forecast is paused**.

The validated categories here are US stocks and ETFs. Default ticker: **SPY**. QQQ and common ETFs are recognized automatically; set `WEBULL_CATEGORY=US_ETF` for other ETFs. A validated SPX/NDX/VIX index quote mapping is **not implemented**. Entering SPX does not silently substitute SPY or invent an index price. Stock/ETF option-pricing scenarios are implemented; American-option early exercise is approximated by Black–Scholes.

Sandbox can be selected with `WEBULL_ENDPOINT=api.sandbox.webull.com`, but it is labeled as test data and is not eligible for a production live forecast. This release never enables real orders in either environment.

## Monte Carlo: what the next move means

These are **uncalibrated conditional model probabilities**, not trading win rates or a guarantee of market direction. The default assumption is neutral. An up probability near 50% is a legitimate result.

- **Historical block bootstrap (default):** resamples contiguous five-minute blocks of observed one-minute log returns. It never constructs a block across a session boundary or missing minute. Blocks are centered and scaled to current realized volatility. It retains some observed short-run dependence and empirical tail behavior but cannot invent shocks absent from the sample.
- **Lognormal / GBM:** simulates independent Gaussian one-minute log returns. It is a useful baseline; real markets can have much larger tails and abrupt volatility changes.
- **Volatility:** EWMA of the last 240 valid one-minute returns, decay factor 0.97. At least 120 valid same-session returns are required. Overnight and missing-minute returns are excluded. About 1,000 completed Webull bars are requested by default.
- **Neutral direction:** expected log return is zero. This centers the median under the Gaussian model; it does not imply that expected arithmetic price change is zero.
- **Recent trend (experimental):** uses 10% of the mean of the last 60 valid returns, capped at ±0.15 of the one-minute sigma. The choice is disclosed and not optimized or claimed to have a predictive edge.
- **Neutral zone:** defaults to ±2 basis points (±0.02%). Up and down probabilities refer to terminal prices outside that zone. All three outcomes add to 100%.
- **Touch probabilities:** count sampled one-minute checkpoints, including the origin. Intraminute touches can be missed. Fresh options levels or your manually entered levels can be inspected.
- **Fan chart:** marginal quantile ranges at each minute; it is not a confidence band covering 95% of entire paths.

Gaussian model equation, with minute units: `log(S[t+1]/S[t]) = m + sigma*z`, where `z ~ N(0,1)`. In conventional GBM notation, arithmetic drift is `mu = m + sigma^2/2` per minute.

Increasing the path count reduces simulation noise, not model error. News, event risk, unseen jumps, liquidity changes and a new volatility regime can invalidate the ranges. No option implied volatility is substituted for realized volatility, and no unobserved dealer inventory is treated as a measured price drift.

**Recent out-of-sample calibration:** the optional button replays forecasts at historical origins using only preceding bars. Outcomes do not overlap within each horizon. It reports 68%/95% empirical coverage and the Brier score for non-flat binary outcomes. The 50/50 Brier baseline is 0.250. Gaps and cross-session outcomes are skipped. A few days of observations are a small sample, not evidence of profitability or a stable directional edge.

## Gamma, Vanna and Charm interpretation

Inputs are observed Webull IV, open interest, contract multiplier, strike and expiry. Exposure is calculated using dividend-adjusted Black–Scholes with the **calls-positive / puts-negative** inventory scenario. Actual dealer positions are unknown. Daily OI publication time is not supplied, so OI-as-of remains unverified.

| Output | Unit / interpretation |
| --- | --- |
| Gamma / GEX | USD per 1% underlying move: signed Gamma × OI × multiplier × spot² × 0.01 |
| Vanna | Delta-share change per +1 IV percentage point: signed dDelta/dSigma × OI × multiplier × 0.01 |
| Charm | Delta-share sensitivity per elapsed calendar day: signed dDelta/dCalendarDay × OI × multiplier |
| Delta | Hypothetical signed option delta in underlying-share equivalents |

The opposite-sign delta change would be the hypothetical counter-hedge. These are not observed hedge orders. Charm is a local derivative; multiplying a 0DTE Charm number by a whole day is not a reliable projection. Gamma-flip price sweeps hold IV and OI fixed. OI walls are concentrations, not guaranteed support or resistance. The dashboard identifies fresh option snapshots separately from fresh stock observations.

## Exports and diagnostics

- Export observed one-minute candles as CSV, forecast distributions as JSON, or sanitized data-health diagnostics as JSON.
- The worker stores local report checkpoints about once a minute and when option collection completes, using atomic writes. It retains up to 1,000 reports per ticker. Live status is recalculated from source timestamps; it is not inferred from file modification times.
- Optional terminal connection check: `python launch.py --diagnose`. This checks stock/candle access and prints safe timestamps and entitlement metadata. The full options scope/permissions check occurs in the dashboard.
- Automated checks after setup: `.venv\Scripts\python.exe -m pytest -q` on Windows, or `.venv/bin/python -m pytest -q` on Mac/Linux.
- An optional headless collector remains available: `python quant_agent.py --ticker SPY --once`. Do not run it alongside the dashboard with the same API key; the dashboard already collects data.

SDK logs containing signed requests are suppressed. Diagnostic exports do not include API keys, secrets, signed headers, or raw SDK exception dumps. Webull may manage its own authorization token locally. If token approval is pending, approve it in the OpenAPI console and reconnect.

## Validation and remaining limits

See `VALIDATION.md` for the checks performed. The official SDK version is pinned to 3.0.4. Real Webull stock, candle and option snapshot response shapes were inspected through the connected service, and both Monte Carlo models were exercised on 1,000 genuine SPY RTH one-minute candles. That exercise was a **historical replay**; the market was premarket, so it was not presented as a live next-move forecast.

The user's local API keys, subscriptions and complete multi-expiry option collection still need to be exercised on their own computer. The package does not contain those credentials. An authenticated local SDK connection and an installed Windows launcher were not available for end-to-end verification here.

## Official references

- [Webull market-data getting started](https://developer.webull.com/apis/docs/market-data-api/getting-started/)
- [Separate OpenAPI quote subscriptions](https://developer.webull.com/apis/docs/market-data-api/subscribe-quotes/)
- [Stock snapshots](https://developer.webull.com/apis/docs/reference/snapshot/)
- [Stock historical bars](https://developer.webull.com/apis/docs/reference/historical-bars/)
- [Option contracts](https://developer.webull.com/apis/docs/reference/option-contract-list/)
- [Option snapshots](https://developer.webull.com/apis/docs/reference/option-snapshot/)
