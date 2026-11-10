# 2.1 — Quant Desk

- Added exact calendar DTE and listed-expiration controls.
- Added executed-order overlays, premium bubbles, cumulative flow, momentum, a Money Map and estimated volume delta.
- Restored a candle-based price regime for ETFs, stocks and indices, including SPX without share volume.
- Added the Trade Plan, evidence download, scanner and historical event replay.
- Added Pine and JSON snapshots for support, resistance, gamma, GEX strikes and dark-pool price levels.
- Reordered the sidebar and shortened the help bubbles.

# 2.0 — Quant Desk

- Added a dark Tradytics terminal with the existing Webull model available for comparison.
- Added ticker and index selection, expiry scope, net options flow, print filters, IV and skew, dark-pool levels and Tradytics signals.
- Kept Monte Carlo ranges, level touches and calibration using completed candles from the selected price feed.
- Added a Learn page with examples and chart help popovers.
- Added source clocks, cached-data labels, per-scope diagnostics and a labeled offline Preview.

# 1.9 — Live data and probabilistic forecasting

- Replaced the report-viewer/subprocess design with one in-process data worker and one dashboard launcher.
- Rebuilt the interface with a restrained white/teal theme, clearer metric units, actual candle timeframes and per-source data health.
- Defaulted to production read-only Webull data; removed synthetic data from the dashboard flow.
- Fixed nested `result` candle parsing, same-ticker server delay discovery, ETF category handling and stale-trade versus fresh-quote distinction.
- Fixed exact-expiration contract discovery. `start_date` is exact expiry; `end_date` is a lower bound. They are no longer sent as a date range.
- Added contract caching, incremental option batches, a shared request budget and SDK-exception-aware HTTP 429 cooldowns.
- Preserved modeled Gamma, Vanna, Charm and Delta, with explicit scope, exclusions, snapshot ages, OI limitations and mathematical units.
- Added moving-block bootstrap and GBM Monte Carlo, 5/15/30/60-minute distributions, neutral zones, touch probabilities and rolling calibration diagnostics.
- Added market-calendar holiday/early-close/DST handling and live forecast gates that recalculate on each render.
- Added mathematical, parser, data-quality, rate-control and dashboard interaction checks.
- V1.8 migration fix: accept the legacy 90-second quote-age setting but cap the live forecast gate at 30 seconds.

Earlier version notes are retained under `docs/history/`.
