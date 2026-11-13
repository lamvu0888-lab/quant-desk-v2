## Overview

### Market overview and Entry Command Center

**What you see.** The index tape shows the latest reported index levels and changes. The command center shows the selected ticker, expiration scope, session state, price source, evidence score, price alignment, flow and the reason for the current status.

**Look for.** Start with the source clocks and session state. Check whether 5-minute direction agrees with the 3-minute structure and 1-minute timing. Read the first block under Evidence and entry conditions before opening Trade Plan. Check whether the latest price and flow closes describe the same period.

**Example.** A rising 1-minute reading inside a neutral 5-minute range is a reason to watch the range high. If the next opposing level leaves little room above the trigger, the plan can still show AVOID. The evidence score summarizes rule agreement and carries no probability units.

### Price action and large-order overlays

**What you see.** Horizontal position is candle time, and the right scale is underlying price. The candle body spans open to close, and the wick spans low to high. The lower panel shows share volume when available. VWAP is volume-weighted session price. The chart can label a candle-based estimate when native VWAP is unavailable. Dashed price levels and optional C/P markers add context.

**Look for.** Identify the range, trend and nearest opposing level on a slower view, then use 1- or 3-minute closes to see whether a break holds. Compare body size, wicks and available volume around the level. Hover a large print to see the option strike, expiry, premium and side. Its vertical position is the matched underlying price around the print.

**Example.** A wick above 760 followed by a close at 759.75 shows a rejection at that moment. A second close above 760 after a retest gives stronger evidence of acceptance. A call bubble near 760 may represent a different strike or a leg of a spread. Treat it as a prompt to inspect the tape.

### Overview flow snapshot

**What you see.** The compact cumulative premium line summarizes the loaded session. The larger chart adds interval net premium and momentum, with price on a separate axis when supplied.

**Look for.** Read the newest completed bar first, then compare the line's slope with the last several bars. A positive running total can coexist with recent selling pressure. Use the detailed Flow & Bubbles view to check scope and time alignment before treating a divergence as meaningful.

**Example.** Cumulative flow stays at +$8M after dropping from +$12M. The session is still net positive, but the latest $4M deterioration matters more for immediate timing.

### Near-spot 0DTE net gamma

**What you see.** Bars show modeled net gamma at each visible strike. The strike is on the horizontal axis, net exposure is on the vertical axis in the displayed units, and dotted lines mark spot and a modeled flip when available. Verify the expiration shown if the dashboard used the next listed date.

**Look for.** Identify the nearest large absolute bars on both sides of spot. Compare the distance to the next wall with the setup's target distance. Use the source timestamp and scope when comparing with the full exposure view.

**Example.** A long trigger at 760 with a large modeled strike at 761 gives one point of room before that area. Observe whether price closes through 761, stalls there, or rejects. The strike is a reference, and outcomes depend on actual price and flow.

### Estimated share-volume CVD

**What you see.** The upper line accumulates signed share volume through the session. Lower bars show signed volume by interval. The dashboard estimates sign from completed price candles. For SPX, any separately labeled SPY comparison represents SPY shares.

**Look for.** Compare the line's slope and recent signed bars with underlying price. Rising price and rising CVD support broad directional activity in that ticker. If price makes a new high while CVD weakens, look for a failed retest or renewed activity before drawing a conclusion.

**Example.** An up minute with 100,000 shares and a down minute with 70,000 contribute an estimated +30,000 shares. Candle direction assigns the whole minute's volume, so the chart cannot identify which individual trades lifted the offer.

## Movement Detector

### 1. Market status and completed closes

**What you see.** A line connects recent completed 5-minute closes. The card summarizes direction, volatility regime and how consistently the completed bars support the move. An early move watch may appear after a qualifying short-window price move.

**Look for.** Separate stair-stepping closes from overlapping closes. Check whether the 5-minute bias agrees with the short-window push and whether the push cleared a recent range. Then look at volatility and flow.

**Example.** One fast 1-minute push can trigger MOVE WATCH while the 5-minute line remains flat. Mark the range boundary and see where the next completed closes land.

### 2. Price-range volatility and intraday ATM IV

**What you see.** The first chart averages 5-minute true range over 14 bars, in underlying points. True range accounts for the prior close as well as the candle high and low. The optional second chart shows sampled ATM option IV in annualized percent. Its 1/3/5-minute comparison needs observations from the same source, expiry and strike pair when Webull supplies the estimate.

**Look for.** On the range chart, look for widening movement alongside a held break. On the IV chart, compare the change in volatility points with the adjustable watch level. The default +0.30 points is exploratory. Check quote age and the selected maturity. Sparse samples can leave the change blank.

**Example.** IV rising from 14.1% to 14.4% is +0.30 points. That can happen while price stays inside its range. A rising ATR chart with flat IV instead shows wider realized price swings without a matching rise in sampled option IV.

### 3. GEX and nearby dealer-positioning levels

**What you see.** When the snapshot is usable, net gamma bars are organized by strike with model spot and flip shown when available. The card also lists available call and put walls and may show scenario hedge units.

**Look for.** Focus on the next level price could encounter. Under a dealer-hedging model, positive gamma can dampen moves and negative gamma can amplify them. Judge the break or rejection with actual closes and directional flow. Recheck after a material price move or an exposure refresh.

**Example.** Price crossing a modeled flip on one wick provides weak evidence. A close through it, a held retest, and aligned completed flow give more information about the move.

### 4. Completed net premium bars and CVD context

**What you see.** Bars show signed directional options premium in dollars at completed interval closes. Green is positive and red is negative. The 5-minute chart can show dotted directional floors. The 1/3/5-minute selector changes the display. The card separately shows agreement for the 5-minute entry checks and available CVD context.

**Look for.** Compare multiple consecutive bars with price direction. Check whether the latest two completed 5-minute intervals clear their directional floor and line up with price. Use 1-minute bars to spot a change early and CVD as a separate context check.

**Example.** Two positive 1-minute bars followed by a negative completed 5-minute interval suggest the initial burst faded. A SPY CVD disagreement against SPX options flow is cross-market context.

### 5. Price structure

**What you see.** A line plots completed 1-, 3- or 5-minute closes. Where available, dotted lines mark the plan's prior 5-minute range high, range low and VWAP. The selector changes the price detail while the plan levels keep their original timeframe.

**Look for.** Look for a break, acceptance on closes, and a retest that stays on the expected side. A move back through the boundary or repeated VWAP crossings can indicate a failed break or a range. Switch to the candlestick view to inspect wicks.

**Example.** The 3-minute line closes above the range high twice, then retests and closes above it again. A subsequent close back inside the range weakens the continuation case.

### 6. Momentum and share volume

**What you see.** The upper bars show each 5-minute close minus the preceding close, in points. The lower bars show native share volume when available. SPX has no native share volume.

**Look for.** Compare the sign, size and persistence of successive close changes. Check whether volume expands on the push and quiets during a pullback when the data exists. Distinguish a one-bar spike from follow-through and compare with flow separately.

**Example.** Three price changes of +0.8, +0.6 and +0.2 points are still positive with slowing momentum. A new high with shrinking displacement near resistance calls for a closer look at price acceptance.

### 7. Movement decision and evidence thresholds

**What you see.** Horizontal bars show progress toward two price closes, two flow intervals and the minimum evidence score of 80. Hover for the actual count or score. A full bar means that row reached its threshold.

**Look for.** Find the unfinished check, then read the overall state and written blockers. Separate an early price watch, a setup getting ready and a confirmed entry. Session, price geometry, data freshness and the option quote check can independently hold confirmation.

**Example.** All three bars can fill while Trade Plan waits on a wide option spread. The chart shows progress on selected checks, and Trade Plan explains the remaining blocker.

### 8. Trigger, invalidation, stop and targets

**What you see.** Dotted horizontal lines show available plan levels in underlying points. A dot marks current spot. Trigger is where the setup can activate, invalidation is where the thesis fails, stop is the modeled risk exit, and TP1/TP2 are targets.

**Look for.** Compare distance from a realistic entry to the stop and first target. Check whether a major opposing level sits before TP1. For a bearish plan, the intended targets lie below entry. A gap can jump past a planned exit.

**Example.** A long entry at 760, stop at 759 and TP1 at 762 has two points of gross reward per point of planned underlying risk. Entry at 761 leaves one point to TP1 and two to the same stop, before costs.

### 9. Data quality and timestamp alignment

**What you see.** Rows visualize source age relative to the chart's threshold for quote, 5-minute price, flow and GEX. Hover for elapsed seconds and limits. Missing rows are labeled, and red indicates an invalid or out-of-limit timestamp.

**Look for.** Compare the actual source timestamps and require the right ticker, expiry, session and completed intervals. A recent HTTP response can contain an old observation. A short green bar reflects this chart's age check. This chart uses 120 seconds for the quote and 420 seconds for 5-minute price, flow and GEX. Individual feed and entry rules can impose stricter requirements. Read the separate status text for exact bucket alignment and option readiness.

**Example.** Flow stamped 10:15 and price stamped 10:20 can both be recent by age. Wait for a compatible completed bucket before using them together for confirmation.

## Options Flow

### Net premium timeline and flow acceleration

**What you see.** The top panel accumulates directional net premium and optional net calls and net puts. Price uses its own axis when available. The middle bars are interval net dollars. The bottom bars compare the average of the latest three consecutive intervals with the preceding three. Amber diamonds can flag price/flow disagreement on the 5-minute view.

**Look for.** Match ticker, expiry scope, session and interval. Look for recent flow acceleration with price follow-through. Compare the newest interval values with the cumulative line so old activity does not dominate your read. A gap in consecutive intervals can leave momentum unavailable.

**Example.** +$6M net call premium and −$2M net put premium contribute +$8M directional net under the displayed convention. Positive flow into a falling price is a divergence to investigate. Possible explanations include hedging, mixed strategies or timing differences.

### Flow bubble chart and executed tape

**What you see.** Bubbles are placed by trade time and matched underlying price. Size represents premium, C/P denotes call/put, and filled/hollow indicates classified buy/sell. Open the tape for actual contract details, filters and quote-side review. Review points describe tape characteristics.

**Look for.** Inspect repeated prints at the same expiry and nearby strikes, with checked side and clean single-leg classification when available. Compare what price did after the print. Check the loaded sample size and filters if nothing appears.

**Example.** A large hollow P is an estimated put sale. One such print can be a spread leg, closing trade or hedge. Repeated qualifying prints and a price level that holds provide more context than bubble size alone.

### Directional premium in the loaded tape

**What you see.** The lines accumulate bullish premium, bearish premium and their signed net from loaded, side-checked prints. Filters and the recent-tape limit define the sample.

**Look for.** Compare the slopes after narrowing to the maturity or minimum premium you care about. Cross-check with the provider's aggregate timeline before making session-wide claims. Recheck filters when the two views differ.

**Example.** A large bullish tape net with flat aggregate flow can mean activity concentrates in the selected trades or expiration. The views may cover different prints.

### Contract-volume delta

**What you see.** The line accumulates bought minus sold option contracts. Lower bars show signed contract volume by interval in the loaded sample.

**Look for.** Use the chart to identify a shift in classified buying versus selling. Read contract type, expiry and directional premium alongside it. Compare like-for-like samples.

**Example.** Buying 100 puts adds +100 contracts while contributing bearish directional premium. Buying 100 calls adds the same contract volume with a different directional interpretation.

## Money Map

### Money Map treemap

**What you see.** Tile area is absolute directional net premium for a ticker or group. Green and red indicate opposite net directions. Color intensity reflects activity versus usual when the metric is available. The universe can be leaders, sectors or a watchlist.

**Look for.** Compare peer groups and ask whether a move has broad sector participation or centers on one name. Use the activity multiple to find smaller names with unusual activity relative to their own baseline. Open the ticker's price and flow views for confirmation.

**Example.** A +$30M tile in a large name and a +$8M tile at 4× usual in a smaller peer answer different questions. The larger tile dominates absolute net premium while the smaller name is more unusual relative to itself.

## Positioning

### Hedge-pressure heatmap and scenario table

**What you see.** The horizontal axis is hypothetical spot, the vertical axis is the IV shock in percentage points, and the selected horizon supplies elapsed time. The color/hover value is estimated hedge demand in share-equivalents or SPX index-equivalents. Positive values represent modeled buying. The map uses combined gamma, vanna and charm when compatible inputs exist, and otherwise labels a gamma-only map.

**Look for.** Start at current spot and zero IV shock. Compare neighboring price scenarios and test a plausible IV change. Check whether the sign survives a modest change in assumptions. In the table, identify which component drives the result. Review actual price and flow separately for confirmation.

**Example.** A selloff and +1 IV point can show estimated buying in the combined model while the gamma-only case shows selling. The sign depends on the scenario and available components. These values are model outputs and do not report executed hedges.

### Pressure timeline and scenario validation

**What you see.** One line records the flat-spot, flat-IV 5-minute scenario at each journal origin. The other estimates hedge units using subsequently observed spot with IV still held flat. Tables show available subsequent price outcomes and agreement counts.

**Look for.** Check that both traces refer to the same origin and that the outcome window has matured. Review several nonoverlapping observations and sample size before reading directional agreement. The conditional line still comes from the model.

**Example.** A flat scenario projects buying while the observed-spot proxy shows selling. This can mean the price move changed the modeled hedge requirement. It does not establish who traded or why.

### Gamma, vanna, charm and delta exposure profiles

**What you see.** The horizontal axis is strike or expiry. The vertical axis is modeled net exposure in the displayed unit. Spot and flip can appear for strike views. The optional split shows supported call/put components. Near-spot focus trims the visible strike window, while the top total can describe a broader scope.

**Look for.**
- **Gamma** measures how delta changes with spot. Focus on near-spot concentrations and compare net sign by expiry.
- **Vanna** measures how delta changes with IV. Compare the sign and magnitude per displayed IV point. Use an IV shock relevant to the chosen expiry.
- **Charm** measures how delta changes with elapsed time. Check the sign convention and per-day unit. Apply intraday scenarios within remaining session time.
- **Delta** measures the model's current directional exposure in its stated equivalent units. Review the current inventory proxy alongside gamma to distinguish level from sensitivity.

**Example.** Positive gamma concentrated in 0DTE can coexist with negative gamma in a later expiry. An all-expiry total can hide both. Compare using the same timestamp, source, units and scope before interpreting changes. Walls, flip and max pain are model references.

### Webull exposure comparison and option chain

**What you see.** The comparison has a Webull modeled strike chart when a sufficient fresh sample is available. The table shows source, timestamp, unit and scope. Option Chain lists strikes, bid/ask, IV, Greeks and volume/open interest where available.

**Look for.** Compare matched expiries and overlapping strikes first. Check Webull sample coverage and which source supplied the quote or model. In the chain, compare bid, ask, midpoint, quote age, spread, delta, IV and the contract's actual expiration and settlement terms. Open interest measures outstanding contracts and can have an older observation time than quotes.

**Example.** A $4.80 bid and $5.20 ask give a $5.00 midpoint and an 8% quoted width. A standard 100 multiplier makes the width $40 per contract. A fresh midpoint is still an indicative price. A scoped strike sample and a full-chain Tradytics model can legitimately produce different totals.

## Volatility

### IV Decision Center and expected move

**What you see.** The cards show selected-expiry ATM IV, trailing 20-session realized volatility, their ratio, the 30-day IV rank and percentile, and a provider or calculated move estimate. Remaining band appears only when a matching provider expiry and anchor are available.

**Look for.** Choose the actual expiration being evaluated. Compare like maturities and check the event calendar. IV rank measures where the 30-day benchmark sits in its historical range. Percentile measures how often readings were below today's reading. The app labels IV/RV at or below 0.85× low and at or above 1.20× rich. Those are comparison labels and offer no direction by themselves.

**Example.** ATM IV of 24% and realized volatility of 20% give 1.20×. A matching expected move of ±4 around 100 gives reference levels at 96 and 104. Spot at 103 leaves one point to the upper reference, which price can exceed.

### ATM IV term structure

**What you see.** Horizontal positions are expirations, and the vertical axis is annualized at-the-money IV. This is a cross-section at the current provider snapshot.

**Look for.** Identify event humps, front-end stress and unusually expensive maturities relative to nearby expirations. Compare the expiry before and after a known catalyst. When comparing far-dated options, verify coverage beyond the dates visible on the chart.

**Example.** IV rises from 24% in one expiry to 34% in the first expiry after earnings, then falls to 27% farther out. That pattern can be consistent with event premium concentrated in the nearer expiry. The curve does not determine event direction.

### 25-delta skew

**What you see.** Two lines show annualized put and call IV at roughly matched absolute delta for each expiry. The gap is put IV minus call IV in volatility points.

**Look for.** Compare the gap across nearby maturities and with the same maturity on earlier observations when available. Look for unusually costly downside protection or upside premium. Check whether the strikes you would actually trade show the same relationship.

**Example.** A 29% put IV and 24% call IV show +5 points of put skew. This tells you about relative premium at those deltas. The market can still rally.

### 30-day ATM IV versus 20-session realized volatility

**What you see.** The chart plots daily observations of annualized 30-day ATM IV and trailing 20-session realized volatility over the displayed history. The series measure forward-priced and backward-looking volatility respectively.

**Look for.** Check whether the gap is widening, narrowing or near its typical range. Review recent earnings, macro events or actual price shocks. Use the term structure or exact chain for the contract you are evaluating.

**Example.** IV at 30% and realized volatility at 19% can reflect an upcoming event outside the realized window. A historically wide gap by itself cannot establish that short volatility is attractive.

### Realized price regime and breadth panels

**What you see.** The regime panel uses completed 5-minute bars and can show annualized short-window realized volatility, range-width rank and share volume relative to the prior 20 bars. Breadth shows advancing and declining liquid stocks and equal-weight versus cap-weight index changes.

**Look for.** A rising range rank with volume expansion supports a move out of compression. Check whether participation is broad. For an index move carried by a few large stocks, treat weak breadth as context and watch whether the divergence persists.

**Example.** Cap-weighted S&P 500 rises while equal-weight declines and most names are down. The index can continue higher, with narrower participation than the headline move suggests.

## Dark Pool

### Off-exchange price profile and prints

**What you see.** Horizontal bar length is reported off-exchange notional, and each row is a price bucket. The chart shows up to 12 leading levels by notional. Use the tape and settings to inspect session, lookback, minimum size and actual reports.

**Look for.** Locate clusters near present spot, especially around an independently identified range or prior high/low. Track the reaction on a return to the level. Check the reporting timestamp and scope. SPX itself has no share tape.

**Example.** A $120M bucket around 760 makes that area worth observing if spot returns there. An accepted break through 760 and a rejection at 760 imply different price behavior. The reported prints alone do not reveal the initiator or motive.

## Signals

### Contract mark history

**What you see.** The line plots available marks for the selected options contract against time. The detail panel identifies the contract, pattern, signal trades and ask-based entry when reported.

**Look for.** Verify symbol, strike, expiration and quote timestamps. Compare entry with later marks and settlement. Look at the observed bid and ask if available before interpreting marked returns as executable. Sparse points hide what happened between marks.

**Example.** A $2.00 ask-based entry and a later $2.60 mark represent a 30% marked gain. A $2.30 bid at that moment gives a different potential exit before fees.

### Provider signal performance

**What you see.** Provider aggregates describe historical patterns. Individual signal lists may include open or settled observations, depending on the selection.

**Look for.** Compare sample count, settlement status, median, mean and the period. Separate repeated patterns from duplicated prints. Check whether a few extreme results dominate the mean and whether the sample represents your expiry and hold period.

**Example.** A pattern with a +25% average and −5% median may depend on a few outliers. Compare it with a larger and more relevant settled sample before drawing conclusions.

## Trade Plan

### Entry conditions, risk geometry and quote check

**What you see.** Trade Plan displays directional evidence, blockers, entry or trigger, invalidation, stop, TP1/TP2 and any recorded transitions. The option quote panel shows matching contract information, spread, age and readiness when available.

**Look for.** Read the reason for the status. Verify ticker, expiry, side and the freshness of price, flow and the option. Measure the room to the first target and opposing wall after a realistic fill. Review the quote check and distinguish underlying risk from the option premium's dollar risk.

**Example.** A confirmed underlying break can still sit near an opposing level and have a wide quoted spread. Compare the expected net payoff after entry and exit friction. Worker transitions and Discord history describe recorded events and may have different timestamps.

## Forecast

### Price forecast fan chart

**What you see.** The horizontal axis is minutes from forecast start, and the vertical axis is underlying price. Shading shows pointwise 68% and 95% simulated ranges. The center line is the simulated median, and the dotted horizontal line is starting spot.

**Look for.** Compare the chosen horizon and width with a real support, stop or target. Review the model, source bars and available calibration. A pointwise range describes price at one time. For target-before-stop and fill-aware outcomes, use Trade probability.

**Example.** A 15-minute band of 758–762 around spot 760 places a 763 target beyond that band at minute 15. Some paths can touch 763 earlier and finish back inside the band. The endpoint band does not provide the touch probability.

### Trade probability, net payoff and stress tables

**What you see.** Tables compare entry methods on the same plan geometry, showing fill rate, conditional target and stop rates, modeled net P&L and downside. R normalizes the result by modeled risk. The full paper exit follows TP2, stop, invalidation or time limit. No fills count as zero in EV per opportunity.

**Look for.** Compare per-opportunity EV and EV when filled. Then review worst-tail outcomes, baseline and jump stress, and costs. In option mode check actual expiry, quoted legs, IV assumptions and the units per package. Model sampling intervals capture uncertainty inside the simulation's assumptions.

**Example.** A retest with +0.40R conditional EV and 25% fills gives +0.10R per opportunity under zero P&L on misses. An immediate method at +0.15R per opportunity ranks higher on that metric. Stress cases may change the ordering.

### Calibration and paper outcomes

**What you see.** Price forecast validation compares historical range coverage. The Validation view groups model probabilities, observed outcomes, Brier score, paper expectancy and drawdown. Historical replay and prospective worker records are distinct sources.

**Look for.** Compare predicted and observed frequencies in the same cohort, read the sample count and check baseline performance. A lower Brier score indicates better probability accuracy on the evaluated sample. Look for repeatability across sessions and regimes before relying on headline figures.

**Example.** If forecasts in a 60% band hit only 45% of the time, they overestimated outcomes on that sample. Ten observations provide weak evidence. Paper underlying returns omit contract-specific execution effects.

## Scanner

### Scanner rankings and filters

**What you see.** The table ranks names by selected metrics such as dollar premium, relative activity, IV or dark-pool value. The displayed provider snapshot uses all DTE.

**Look for.** Choose a ranking that matches your question. Use relative activity to spot an unusual name for itself and absolute dollars to see where the most premium is concentrated. Check the observation time, then open the actual ticker, expiry and chart.

**Example.** A large index ETF can lead total premium every day. A stock at three times its normal activity may be a more unusual research candidate. Confirm with price, catalyst timing and the actual options chain.

## TradingView

### Exported price levels

**What you see.** The export packages selected dashboard levels as a Pine indicator or JSON snapshot. The plotted lines refer to the chosen symbol and the export timestamp.

**Look for.** Apply the indicator to the matching underlying. Verify spot scale, expiry and source timestamp. Regenerate after a material refresh and compare the plotted levels with price behavior on the chart.

**Example.** A line at a 760 SPY strike cannot be plotted meaningfully on a 7,600 SPX cash chart. A previously exported gamma flip can also drift as exposure changes.

## Data Health

### Source comparison and data readiness

**What you see.** The tables show source price, market timestamp, age, provider status and request diagnostics. Separate lines show latest 1-minute price close, 5-minute flow close and Webull options coverage. Auto typically routes supported stocks and ETFs to configured Webull price data and indices such as SPX to Tradytics.

**Look for.** Check market time first, then provider status, matching expiry and session. For Webull options, compare usable two-sided quotes with returned coverage and verify SPXW separately for SPX. Distinguish closed session, a missing permission, a stale observation and a rate-limit cooldown.

**Example.** Webull SPY price can update while Tradytics flow is still on the last completed 5-minute bucket. A 1-minute watch may be useful, while the confirmation still waits for the aligned bucket. After the regular close, expired options can correctly show no usable quotes.
