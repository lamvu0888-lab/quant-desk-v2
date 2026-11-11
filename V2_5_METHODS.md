# Quant Desk 2.5 decision and research guide

## 1. Start with Overview and Trade Plan

Open **Live**, select the ticker and expiration, and check data confidence. For comparison with the default Discord worker, select **Tradytics** and **0 DTE**. Expand **Evidence and entry conditions** to see which observations agree, what is missing and what blocks a plan.

| Status | How to read it |
| --- | --- |
| WAIT | Data, a direction, or a usable setup is still missing. An active paper reference can also put the view in WAIT. |
| GET READY | A setup is developing, or one of the model checks needs more evidence. Keep watching the trigger and the next completed candles. |
| ENTER LONG / ENTER SHORT | The underlying setup meets the entry rules at the recorded time. Review the displayed risk, liquidity, and any option structure independently. |
| AVOID | Evidence conflicts or the setup fails a risk check. Open the reason list before reassessing it. |
| EXIT | A tracked paper setup reached its stop, target, invalidation or time limit. |

The engine uses the closed 5-minute trend and signed options premium for direction, with closed 3- and 1-minute price for timing. It checks prior-range breaks, retests, EMA 8/10 pullbacks, session VWAP reclaims and rejections, and sweeps through a prior high or low. Volume expansion and directional flow acceleration add evidence. The entry-quality score counts checks. Monte Carlo probabilities appear separately.

For a confirmed setup, the trigger is the price boundary that has to hold. The structural invalidation is the completed-candle boundary for ending the setup. The protective stop adds an intrabar risk limit beyond that boundary. The first target is ordinarily 1.5R and can be capped by the nearest opposing level. TP2 is a continuation target. R is the distance from the reference entry to the protective stop, before costs. The rules require at least 1.35R of room to the first opposing level. A matching expected-move band can block an entry with less than 1R of room. The rules also check that price has not moved too far from the trigger. The usual horizon is 15 minutes and shortens near the regular-session close.

The price and flow timestamps must align on the same completed 5-minute bar. Quotes must be current and inside the regular session. The desk blocks explicitly delayed sources and shows when a feed omits delay metadata. Webull's price path also uses its existing verified-delay checks. SPX has no native share volume or share VWAP. It can qualify on its other evidence, and unavailable inputs stay blank. Current gamma and IV enrich the decision when their data is available. The decision stays at GET READY for premium review if exact-expiry ATM IV is at least 1.50× trailing RV and the 30-day IV rank or percentile is at least 80%. This is a conservative screening rule. An options-P&L result can downgrade the same dashboard plan when the selected structure breaches your entered loss budget, has no viable immediate fill or shows negative net expectancy across both price models.

**Example.** Price reclaims VWAP and holds on the 1- and 3-minute closes. The 5-minute trend and flow agree. Trade Plan still shows AVOID if resistance is 0.8R away. If the level clears, the next observation has to qualify on its own entry, stop and reward geometry.

## 2. Read modeled dealer hedge pressure

The model starts with signed Tradytics exposure estimates for the selected expiry scope. The vendor's assumptions determine the sign and size of the modeled book. The hedge calculation assumes a delta-neutral response equal to the negative of the change in that book's delta.

| Input | Conversion to a common unit |
| --- | --- |
| Gamma, USD per 1% spot move | Divide by 0.01 × spot² to get delta-equivalent units per price point. Multiply by the scenario's price change. |
| Vanna, USD per IV point | Divide by its snapshot spot to get delta-equivalent units per IV percentage point. Multiply by the IV shock in points. |
| Charm, delta-equivalent units per day | Multiply by elapsed minutes divided by 1,440, with all other inputs fixed. |
| Delta, equivalent units | Shows the vendor's starting exposure. Gamma, Vanna and Charm determine the modeled change. |

The sign of the combined hedge is flipped relative to the delta change. A positive result means estimated buying under the assumptions. Vendor exposures already include their contract sizing. SPX uses index-equivalent units. Snapshots require recognized units and timestamps and need to agree on scope, modeled spot and the reported model when identified. Missing Vanna or Charm leaves the combined estimate unavailable. You can still see the gamma component.

The pressure map applies those sensitivities around the vendor's spot. The 5-, 10-, 15- and 30-minute stress scenarios scale a 5-minute price range by the square root of time and pair the moves with a configurable IV shock. Those cases have no assigned forecast odds. The strike list highlights where the vendor reports concentrated sensitivity. Gamma flip and call/put walls are provider levels. Positive gamma generally models a hedge against the price move. Negative gamma models a hedge in the same direction. Actual price response can differ.

The worker records scenarios from then-current data and compares them with later 5-, 10-, and 15-minute completed underlying prices, plus 30 minutes when available. It calculates the model response to those observed prices with IV held flat. The ex-ante direction comparison uses the flat-spot and flat-IV scenario to test modeled time-driven pressure against the later price move over disjoint windows. This measures market response. Dealer inventory, hedge timing and dealer executions are unobserved. Open interest gives the total outstanding contracts without identifying the holders' net positions.

## 3. Use the IV Decision Center to compare premium

The DTE presets target exact calendar expirations from the displayed session. Use **Listed expiration** if a preset has no corresponding expiry. **Dashboard scope** uses the sidebar choice, or the 30-day ATM benchmark when the sidebar is on All DTE.

- **ATM IV** describes the price of volatility around the current strike area for the selected expiry. **30-day IV rank** describes the benchmark's position in its past range. **30-day IV percentile** is the share of prior readings below the current 30-day value.
- **IV/RV** compares selected ATM IV with trailing 20-session realized volatility. The panel calls a ratio at or below 0.85× low and a ratio at or above 1.20× rich. The two measures use different time periods and 0DTE can have its own intraday behavior.
- **Term structure** compares IV across dates. **Skew** shows the 25-delta put IV minus call IV for the exact date when available.
- **Expected move** uses the provider's band when it matches the selected expiry and has a usable anchor. The fallback is spot × annualized IV × square root of remaining calendar years to the reference session close. Remaining up and down distances are available only from a matching anchored provider band.

Use Trade Plan to evaluate direction. With direction confirmed and low relative premium, compare a long call or put with a debit vertical after costs. With rich premium, compare whether the narrower payoff of a debit spread is worthwhile. In a compressing market with rich premium, a defined-risk credit spread is a scenario to price. Check short-strike distance, event risk, and the loss budget. Stand aside when the entry or payoff is weak. **Advanced Analysis** retains the detailed volatility, skew, breadth and price-regime charts.

## 4. Compare entry and payoff with Monte Carlo

Choose **Trade probability** and click **Evaluate trade plan**. The tool imports the current direction, expiry, stop, invalidation, TP1, TP2 and horizon. Each entry method shares the structural plan. A retest can fill after a touch and a confirming minute close. Pending entries can expire unfilled. Some entry methods need a reference level that the plan or provider has not supplied.

The primary model samples contiguous blocks of five 1-minute OHLC returns from completed regular-session bars. It weights blocks by time of day and volatility regime, centers drift, uses a short-memory variance estimate, and keeps GEX and flow effects bounded in the volatility scale. It compares this with a zero-drift Gaussian baseline and a separately labeled jump stress case. A minimum of 120 completed minute bars and 30 intact blocks is required. A same-minute stop and target conflict is resolved stop first. Overnight fills and extended-hours paths are outside the test.

| Readout | Meaning |
| --- | --- |
| Fill / no fill | Share of paths that qualify for a modeled fill under the entry rule and option quote checks. |
| TP1 / TP2 first | Target reached before the stop or another terminal exit, conditional on modeled fills. TP1 is tracked as a milestone. The whole paper position exits at TP2, stop, invalidation or timeout. |
| Stop first and neither | Whether the stop won the race to a target, or neither that target nor stop was reached before the plan ended. |
| EV per opportunity | Average net R including zero for missed trades. Check EV when filled alongside it. |
| Holding time, break-even, downside | Average filled holding time, the break-even win rate given modeled net wins and losses, and the fifth percentile and average loss-tail R. |
| Sampling intervals | 95% simulation intervals within a fixed model. Compare baseline and stress results for assumption sensitivity. |

**Quoted option structure** needs a specific expiration and recent, standard, two-sided option quotes with IV. Select a long call/put, debit vertical or credit vertical consistent with the plan. The engine reprices each leg using a dividend-adjusted European formula anchored to its current quoted midpoint. Price, time decay, IV response and stress affect the option mark. It estimates entry and exit bid/ask, slippage and per-leg fees. Dollar P&L is for one package with a standard 100 multiplier. R for a quoted structure is relative to estimated expiration max loss including modeled fees. Closing costs, gaps and liquidity can change actual results.

SPXW standard PM expirations can use the regular-session close when the chain omits the settlement time. AM-settled SPX/SPXO requires explicit timing and earlier-than-expiry-day evaluation. Options with ambiguous expiry, adjusted contracts or insufficient quote quality are excluded. The model does not reconstruct past NBBO, execution queues, early exercise, assignment or live fills. Option-path estimates have no prospective option performance history in this release. [Cboe SPX product specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/)

## 5. Interpret the validation record

**Worker paper outcomes** compares forecasts recorded before future outcomes arrived. It excludes incomplete results, overlapping windows for the same ticker and incompatible rule or model versions. A matched evaluation uses the current ticker, source, DTE scope, setup and horizon. A TP1 result needs at least 60 mature observations across eight sessions, sufficient hits and misses, improvement over the Gaussian and 50/50 baselines, and calibration checks. Uncertainty is bootstrapped by session. Expected R has a separate error check. The displayed drawdown uses chronological paper R. It does not account for simultaneous portfolio exposure.

**Historical replay** reconstructs decisions from completed prices and finalized vendor flow, fitting at each origin using earlier bars only. The replay lacks point-in-time GEX and IV, executable past option quotes, actual fills and a record of when a provider revised an interval. Use it to inspect behavior and compare modeled vs paper fill rates by entry method. It does not qualify live probabilities. The older **Price forecast** workspace is still available for general price ranges and coverage.

Calibration is per ticker and data scope, with subgroups for time of day, setup, regime and decision. The dashboard reports sample size, Brier score, calibration bands, false positives, win rate, realized paper expectancy and drawdown. No sample comes bundled as live results. Discord releases TP1 odds only after the prospective comparison qualifies and releases estimated R only if its separate check also qualifies. Worker alerts use the underlying plan. Option structures and simulated execution costs are reviewed in the dashboard.

**Paper reference.** On a confirmed Trade Plan, click **Track this paper setup** to follow it in the current browser session. The worker separately captures eligible decisions, resolves them from subsequent completed bars, and writes state changes and paper outcomes to its journal. There is no connection from any of these actions to order execution.
