# Validation — Quant Desk 2.1

- 65 automated checks passed with Python 3.12, Streamlit 1.67.0 and Plotly 6.9.0.
- The tests cover exact nonzero DTE arithmetic, missing intervals, SPX regime without volume, share CVD, no-future-price order markers, price and flow agreement, and timestamped Pine exports.
- Preview checks render the new pages and interact with listed expiries, Money Map watchlist, contract volume, replay, and the TradingView generator.
- The local Streamlit server responded to its health check. Browser rendering, authenticated Tradytics data and Pine compilation in a TradingView account were outside this validation. Preview and API fixtures exercise the logic. Signal accuracy and trade P&L require separate evaluation.

## Prior validation — Quant Desk 2.0

Verified November 9, 2026 using Python 3.12, Streamlit 1.67.0 and Plotly 6.9.0.

- All 57 automated checks passed. The original V1.9 checks cover the Webull adapter, options model, Monte Carlo, and classic dashboard.
- The new checks exercised all ten terminal pages, SPX selection, Learn navigation, exact zero-DTE requests, provider quote and candle timestamps, rate-limit cooldowns, and missing-scope responses.
- The local Streamlit server returned HTTP 200 from its health endpoint.
- Tradytics requests were verified with documented response fixtures. Your local API key and its live entitlements can be checked in Data Health after setup. Windows startup and desktop pixel rendering were outside the local test environment.

## Prior validation — Quant Options Agent 1.9

Environment: Python 3.12, Webull SDK 3.0.4, Streamlit 1.66.1, Plotly 6.9.0. Prepared November 4, 2026.

50 automated checks passed, including the full dashboard interaction tests. A local Streamlit server also started successfully and returned HTTP 200 from its health endpoint.

Automated checks cover:

- Existing volatility/confirmation behavior and option-exposure validation.
- Gamma, Vanna and Charm against finite-difference option Delta derivatives.
- Gaussian Monte Carlo moments and quantiles against the analytical distribution.
- Deterministic seeds, ordered probability ranges, exhaustive up/down/neutral outcomes, and level touches.
- Bootstrap segments that exclude overnight and missing-minute returns.
- Rejection of unfinished candles, duplicate timestamps, nonfinite prices and zero observed variability.
- Walk-forward predictions unchanged by changes to data after the forecast origin.
- Nested real Webull response structures and server-reported delay metadata.
- Explicit delayed data overriding a fresh timestamp or zero-delay evidence elsewhere.
- Stale polling, paused polling, and absent candle entitlement metadata blocking live forecasts.
- V1.8's 90-second quote-age setting is accepted and capped to the V1.9 30-second forecast threshold.
- Option-feed failure leaving a valid stock/candle forecast available.
- Session-open candle aggregation, missing-bucket omission, holidays, early closes and DST.
- Nonblocking rate budgets and HTTP 429 cooldowns, including SDK-raised HTTP exceptions.
- No order-submission calls in the application modules.
- Full Streamlit rendering in disconnected and injected live-data states, model selection and the rolling-validation button. Injected UI observations are test fixtures, never a user-facing live feed.

Real-data exercise through the connected Webull service:

- SPY stock snapshot, bounded option-contract metadata and two option snapshots inspected for actual field names/units.
- 1,000 actual SPY RTH one-minute candles accepted by the new parser; server-reported `delay_minutes = 0`.
- Coverage: October 30, 2026 12:20 PM ET through November 3, 2026 3:59 PM ET.
- 200 complete five-minute candles reconstructed without partial buckets.
- Both bootstrap and GBM models generated distributions from those observations.
- Rolling 15-minute validation produced 49 nonoverlapping historical forecasts. This verifies workflow operation, not predictive skill.
- The exercise occurred before the November 4 regular-session open. No historical replay was labeled as a live forecast.

Not verified here: the user's local SDK authentication/entitlements, full production multi-expiry chain completion, Windows-native double-click startup, or pixel-level rendering in a desktop browser. A browser executable could not be downloaded in this environment; UI validation used Streamlit's application test harness. No claims of forecast accuracy, backtested profits, or live trading readiness are made.
