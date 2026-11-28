# Quant Desk 3.0.0-rc3

- Market Overview opens with a decision, a reason and a price chart that saves zoom and pan by ticker, interval and feed.
- Trade Plan ranks broker-listed contracts against shared price paths, accounts for spreads, fees and permitted size, and pins the selected series through paper entry and position review.
- Contract Intelligence shows synchronized premium, Delta, Gamma and Theta against time and underlying price. A timestamp-checked midpoint fit resolves inconsistent broker or demo IV.
- OpenAI handles opted-in ABC plans, paper reviews and after-close research. A shared journal tracks usage, proposed research and prospective outcomes.
- Schwab ES/MES sampled flow runs in shadow for SPX. AI Learning measures coverage and forward results for contract selection and experimental signals.

See [RC3 research and operating notes](V3_INTEGRATED_RESEARCH.md) and the release ZIP's test transcripts.

# Quant Desk 2.8.8

- Added long support bounces and short resistance rejections to Trade Plan. The setup combines a prior sideways hour, a completed 3m rejection, later 1m follow-through, 1m/3m EMA 9/21 timing and synchronized options flow.
- Overview and Movement Detector show the range midpoint, entry evidence and reasons to wait. Confirmed reversals use the existing alert worker and paper journal, with the underlying provider and both targets in the Discord message.

# Quant Desk 2.8.7

- Added Schwab Market Data Production for cash SPX quotes and completed regular-session candles. Auto checks Schwab, Webull and Tradytics in that order for SPX. The dashboard and monitoring worker use the same source selection.
- Data Health shows Schwab's real-time indicator, market timestamp and last completed minute. A private authorization helper checks both quote and history access for the owner's account. See [Schwab SPX setup](SCHWAB_SPX_SETUP.md).

# Quant Desk 2.8.6

- Trade Plan and Movement Detector now expose a ten-factor 100-point entry evidence breakdown. Enter states need at least 71 points as well as existing hard gates. The score is not a probability of winning.
- SPX uses ten synchronized minutes of zero-delay Webull ES footprint buy-minus-sell contracts as CVD evidence, falling back to MES. Missing/delayed footprint awards zero, without manufacturing index volume or a price-derived CVD. Webull futures volume/CVD appears below the options-flow detector stage. SPY estimated share CVD remains comparison only for SPX.
- Dealer walls and expected-move headroom are modest conditional score factors; modeled gamma/vanna/charm/delta remain context until forward validation supports a directional rule. Old version paper samples are not used to qualify the new score.

# Quant Desk 2.8.5

- Replaced EMA 8/10 with EMA 9/21 in Trade Plan, Movement Detector and the scenario entry comparison. Added the two EMA lines to the price chart.
- Added a short Discord alert when two completed 3m closes confirm a new EMA trend direction. It evaluates current price candles before the quote and flow checks for a Trade Plan setup. A move watch cooldown now applies separately to each direction.
- Applied Auto price selection to both the worker and dashboard. Auto falls back to fresh Tradytics candles and returns to Webull when eligible. An explicit Webull `INVALID_SYMBOL` receives a longer retry interval and appears in Data Health with the endpoint.
- Removed the integrated intraday chart. SPX keeps ES/MES contract activity as an option in the CVD panel.

# Quant Desk 2.8.3

- If Webull SPX candle requests fail with no cached bars, the dashboard shows clearly labeled Tradytics SPX candles instead of a blank chart. The Discord monitor retains its live-data gate.
- Webull server failures now show a safe HTTP status and error code in Data Health without printing signed requests or credentials.

# Quant Desk 2.8.2

- Verified SPX `US_INDEX` snapshot and one-minute candle responses directly through Webull and connected them to Auto, dashboard charts and the Discord monitor's price path.
- Required an explicit zero-delay, current same-symbol SPX candle response when the index snapshot omits delay metadata. Stale, delayed, sandbox or unavailable data still holds live checks.
- Kept index volume out of share VWAP, volume profile and CVD. Other indices retain Tradytics pricing; explicit Tradytics selection remains available for SPX.

# Quant Desk 2.8.1

- Replaced the Movement Detector IV threshold number field with a dropdown covering the same +0.1 to +5.0 volatility points.
- Added chart reading notes on Movement Detector and Overview, plus an integrated chart entry in Learn.
- Added a timestamped quote diamond to the intraday price charts and a two-second default regular-session Webull quote cadence in the dashboard. The quote uses its own clock, while candle-based indicators use completed bars.
- Split the volume and GEX profiles by units and aligned the price, completed options flow and estimated share CVD panels by time.

# Quant Desk 2.8.0

- Added an integrated intraday chart with 1m, 3m and 5m controls, EMA 21, VWAP, price levels, volume profile, modeled net GEX, options flow force and CVD.
- Added a simple directional watch state and “look next” confirmation cue so the chart is readable during fast moves.
- Excluded proprietary D1/Cycle Intelligence; no unsupported intelligence label is shown.

# Quant Desk 2.7.1

- Expanded Learn into a chart and tool reference organized by dashboard page.
- Added day trade, swing trade and LEAPS checklists with worked examples.
- This dashboard release keeps the 2.7.0 signal rules and Discord worker build.

# Quant Desk 2.7.0

- Both the dashboard and worker can use Webull stock and ETF quotes and completed candles alongside Tradytics flow, index prices and modeled positioning. Batched background requests update prices and candles independently of options.
- Added nearest listed expiry, Webull exact-expiry quotes and Greeks, SPXW contract coverage checks, same-strike ATM IV tracking, and provider quote timing in Data Health.
- Added quicker move watches for supported watchlist symbols and prompt rechecks when new price or flow arrives. Confirmed hybrid setups require current price, aligned flow and a fresh matching contract quote.
- Hid Strategy Lab and History & Replay from the workspace navigation.

# Quant Desk 2.6.5

- Connected current market playbook to a clearly labeled Strategy Lab research shortlist in Trade Plan and Strategy Lab. Candidates are ranked by the saved doubled-cost SPY test, with no claim of a validated live winner or change to entry rules.

# Quant Desk 2.6.4

- Removed Discord data-quality notifications; feed issues remain visible in the dashboard and worker logs.
- Rewrote Discord move watches, entries and paper exits as short, readable messages. Old queued quality alerts are dropped by the updated worker.

# Quant Desk 2.6.3

- Added a distinct SPY/SPX price-move watch alert on a fresh completed 5m candle moving at least 0.25%, or a completed 10m window moving at least 0.35%. It is labeled price-only, uses a 15-minute cooldown, and never implies a Trade Plan entry.
- Data-quality notices now include quote observation time and repeat at most once an hour per symbol during the same session.

# Quant Desk 2.6.2

- Added 1m, 3m and 5m net-premium chart choices in Movement Detector. The 3m view aggregates three matching, completed 1m bins; Trade Plan confirmation remains on completed 5m bars.
- Added 1, 3 and 5-minute observed ATM IV change views and a configurable exploratory expansion watch level in volatility points. Intra-session comparison waits for actual snapshots in the current dashboard session.

# Quant Desk 2.6.1

- Added a compact evidence chart and context-sensitive “Look next” cue below each of the nine Movement Detector checks.
- Charts use the already loaded completed price bars, selected-expiry exposure, options flow and feed times. Missing observations display a waiting state; modeled dealer exposure and estimated share volume remain labeled separately.
- Kept Trade Plan rules, Strategy Lab results and worker alerts unchanged in this display update.

# Quant Desk 2.6

- Added Movement Detector with nine ordered checks, native and proxy CVD labeling, modeled hedge pressure and synchronized feed times.
- Connected Trade Plan to regime-specific setup priority and shared closed-bar, flow and momentum checks.
- Added a Strategy Lab audit of the 100 supplied strategies, with previous-session selection, unchanged baselines and doubled-cost tests.
- Limited Discord notifications to confirmed entries, associated exits and sustained data warnings. Added an atomic ticker/setup cooldown and kept alert evidence in the journal.

# 2.5 — Quant Desk

## Entry Command Center and Trade Plan

- Added a shared decision engine for the dashboard and Discord worker. It combines completed 5-minute trend and flow with 1- and 3-minute timing and checks for EMA 8/10 pullbacks, VWAP reclaims and rejections, breakouts, retests, failed breaks and liquidity sweeps.
- Added WAIT, GET READY, ENTER LONG, ENTER SHORT, AVOID and paper EXIT. An entry has a trigger, reference entry, separate structural invalidation and protective stop, two targets, reward/risk, an evidence score and explanations. Nearby opposing price, gamma and available dark-pool levels limit room.
- Added gates for stale or mismatched data, poor geometry, conflicting flow, stretched entries, limited expected-move room, elevated relative premium, negative modeled expectancy and large differences between the scenario models.
- Added timestamped setup transitions, browser-session paper tracking, worker paper outcomes and research exports. Kept V2.4 delivered Discord history and the earlier setup detail under its own expander.

## Dealer positioning and volatility

- Added hedge sensitivity and demand from signed Tradytics Gamma, Vanna and Charm on a common hedge-equivalent basis. Checks source age, scope, snapshot alignment and units. Shows partial components when the combined estimate is unavailable.
- Added the pressure map, 5/10/15/30-minute stress scenarios, sensitive strikes, gamma regime and walls, and key-level crossing alerts. Added an intraday timeline and comparison against later completed underlying prices.
- Added the IV Decision Center with exact-date and 0/1/7/30-DTE views, ATM IV, 30-day IV rank and percentile, trailing RV, IV/RV, term structure, skew, expected move and anchored remaining range. Moved the existing detail charts under Advanced Analysis.
- Added short explanations of what the data and models measure. Tradytics and Webull comparison remains available, and SPX share-only metrics stay blank.

## Monte Carlo and validation

- Imports Trade Plan geometry and compares immediate, breakout-retest, EMA 8/10 and VWAP entries. Pending entries can remain unfilled. Path outcomes account for stops, invalidation, targets and the time limit.
- Added a regime- and clock-conditioned block model, Gaussian baseline and jump stress. Reports modeled fill, target/stop, holding time, break-even, tail risk and net expectancy. Preserved the earlier Price forecast workspace.
- Added quoted long-option, debit-vertical and credit-vertical evaluation. Values each leg using price, time and a changing IV scenario, with modeled bid/ask, slippage and per-leg fees. Checks quote age, standard multipliers, expiry timing and optional loss budget.
- Added forward paper validation and separate walk-forward replay, with Brier score, calibration, false positives, expectancy, win rate, drawdown and subgroup reports. Validated claims require comparable prospective outcomes. Replay and simulated options remain exploratory.

## Dashboard and deployment

- Switched to a light palette, high-contrast status colors and simpler Learn topics. Preserved flow bubbles and large-order overlays, Money Map, net-only near-spot gamma, dark pools, CVD, Tradytics signals, scanner, history and TradingView exports.
- Preserved configurable ticker monitoring, SPY/SPX defaults, 30-second target cadence and the Discord test button. Added full trigger and risk details, flow and modeled dealer context, data-quality and level alerts, durable delivery claims, and outcome-aware signal changes.
- Added a SQLite research journal, optional PostgreSQL storage and a bounded GitHub snapshot. The Render Blueprint ignores monitor-data commits when deciding whether to rebuild.
- Kept live order execution disabled. Added installation, cloud update and method guides, a release manifest and regression tests.

## 2.2–2.4 — Prior Discord and chart releases

- 2.4 added dashboard watchlist editing and delivered signal history through shared GitHub storage.
- 2.3 added the Render worker configuration, SPY/SPX monitoring and next-available-expiry fallback for the Overview gamma chart.
- 2.2 improved the near-spot gamma chart and added the Discord test button. See [2.2 release notes](CHANGELOG_V2_2.md) for details.

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
# Feed timing and freshness update

- The live workspace still redraws every four seconds. Tradytics flow is now checked every 15 seconds and dealer exposure every 30 seconds, subject to API limits and request completion.
- A flow bar now displays its completed interval close instead of its interval start. The label also shows how long since the app received the latest API response, separately from the provider's source time.
- Exposure older than two minutes and flow older than 90 seconds are labeled AGED instead of RECENT. Charts may still display an older observation with its timestamp; an API check does not create a newer provider observation.

# Quant Desk 2.8.4

- Added an ES/MES selector to the SPX integrated chart. Webull futures one-minute bars supply contract volume; a footprint CVD line appears only with explicit zero-delay buy and sell totals from the same contract and minute. No futures price replaces SPX index price.
- Automatically selects the nearest active contract and labels its symbol. Futures activity stays out of the trade and Discord signal gates.
- Limited the integrated chart price scale to nearby levels so distant walls and stale quotes do not flatten intraday candles.

