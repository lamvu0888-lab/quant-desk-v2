# Quant Desk RC3 research and operating notes

## Reading a session

1. Open **Market Overview** for the current decision, its reason and the chart. Toggle **Follow latest** to keep the same zoom while tracking the newest bar. Turn it off to inspect a prior move. The chart discloses when it retains candles that have rolled out of the provider window. Detailed exposure, flow and diagnostics open on demand.
2. Open **Trade Plan** for the underlying trigger, target, invalidation and holding period. A developing setup can show an option to watch. An active block or expired quote leaves an explicit reason. Once a baseline paper position opens, the page shows the held series, executable bid when available, and updated scenario values.
3. Select **Analyze contract** to carry the exact series and assumptions to Options Contract Intelligence. The upper projection varies time through the series payoff reference. The other varies spot at one evaluation time. Both display premium, Delta, Gamma and Theta. Colors identify the same quantity in each chart. The price paid, synchronized midpoint and model fair value are separate quantities.
4. Use **AI Learning** to review selection quality, coverage, previous ABC plans and challenger results. Keep the main dashboard focused on the next decision.

## How Trade Plan compares options

An aligned plan supplies the direction, price target, invalidation and holding period. The engine compares reported expirations through 14 calendar days plus the first available expirations at or beyond 15, 30, 60 and 90 days, and the selected listed expiry. Broker subscriptions and near-spot chain sampling limit strike coverage. Each eligible series uses the same 256 underlying paths. At a simulated minute in which price touches both barriers, the model gives precedence to the stop. Its numerical precision check can reject a small positive mean. That check addresses Monte Carlo sampling variation only.

| Worker objective | When it can select |
| --- | --- |
| risk_adjusted | Highest composite opportunity under the original budget and evidence gates. Starting weights for expected net, target before stop, downside, liquidity, sensitivity, carry and IV risk are 25%, 20%, 15%, 15%, 10%, 10% and 5%. |
| net_profit | Highest modeled net dollars for the allowed quantity. |
| net_return | Highest modeled return after costs. |
| conservative | Positive base and stressed expectancy after modeled IV contraction, a wider spread and an added exit concession. |
| probability | A prior validated group exists for this ticker, DTE band, regime and setup. The estimate describes positive fixed-time net marks in that historical group. |

A current two-sided quote, synchronized spot, tradeable series and reported zero delay are prerequisites for ranking with live inputs. The default long entry uses the ask. Exit estimates charge for the spread and fees. The active paper book supplies remaining loss capacity after closed P&L and reserved stop exposure. A missing heartbeat or unpriced open position leaves the displayed quantity provisional and holds ranked entry confirmation. A later paper fill requires a fresh post-alert Webull quote for the same OCC series at or below the approved ask. A Schwab-ranked contract may therefore produce a legitimate skipped paper fill. An unavailable displayed ask size limits a modeled purchase to one contract. Open positions retain their original series. The position panel flags historical bids and withholds updated valuation when the required observations are stale.

The working source enforces a maximum debit of $1,000, two contracts, a 20% premium stop, a $500 daily loss stop and the separate $300 SPX spread risk cap. Stops act on available quotes. Realized losses can exceed a modeled stop when quotes gap. Existing Render services, Streamlit entry points and order-routing configuration do not require changes for this package.

## How selection quality is measured

With recording enabled, the monitor freezes the original candidate set, features, scores, assumed entry, scenario premium samples and rejection reasons. It subsequently seeks observed bids for the same contracts from the original provider at the planned arrival. All eligible exit bids must appear together in the same poll, within 15 seconds of each other in the original one-minute mark window, with adequate displayed size. The modeled liquidation uses the bid less a one-cent cushion and includes fees on both sides. Duplicates, mismatched series, prices outside the matching window and incomplete alternative sets cannot establish comparative performance. A selected contract is compared against the median observed alternative. The report includes counts of incomplete cases. A paper order can have a different exit or no fill at all.

The baseline remains fixed. Research compares bounded alternatives on time-ordered, non-overlapping opportunities. The initial ranking research gate requires at least 100 such origins over 20 trading sessions, reserving at least five later sessions for evaluation. Ticker, setup and DTE cohorts require additional support before returning a probability. New AI proposals can be tested on later sessions after publication. The fixed research profiles include DTE choice, absolute Delta, Gamma sensitivity, the difference between quote-implied and provider IV, and execution quality. A profile with missing inputs abstains from evaluation. A fully observed profile which rejects every series records a zero-dollar no-trade comparator. Small samples, missing alternative quotes and uneven capture rates can still bias results. No profile is promoted automatically.

Option execution research emphasizes how order timing and bid/ask placement alter effective costs. Cboe's sampled option data distinguishes NBBO, size and volume. Those findings informed separate accounting for the target premium, the paper entry and the observed exit. Multiple trials on a reused price history also favor some configurations by chance. Keeping a dated proposal and later observations permits an independent comparison. [Muravyev and Pearson](https://doi.org/10.1093/rfs/hhaa010) · [Cboe option quote intervals](https://datashop.cboe.com/option-quote-intervals) · [Bailey et al.](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

## Evidence available at release

The archival replay measured five-minute option valuation given the later observed SPY price. Across 1,007 matched observations, mean absolute error was $0.0304 per share for the calibrated model and $0.1778 for carrying the starting midpoint forward. The data cover 22 SPY contracts across 11 dates. Exchange quote clocks, historical executable size and fills are missing. Further evidence is needed for trade direction, selected-strike profitability, SPX and QQQ. See the [pricing replay methods and results](CONTRACT_PRICING_RESEARCH.md).

The Schwab ES/MES study uses sampled last-trade sizes and preceding quotes to estimate signed futures volume. Its coverage and contract month are displayed. The new estimate runs in shadow alongside the existing observed-footprint route. The result depends on the brokerage entitlement, clock alignment and share of volume sampled. See the [SPX futures research notes](SCHWAB_FUTURES.md).

OpenAI paper plans and after-close research require private credentials, a shared journal and explicit settings. Reserved costs, plan versions and supported scheduling are documented in the [AI setup and cost guide](AI_INTELLIGENCE.md). Conditions are evaluated on local scans between checkpoints. Production model access, broker entitlements, cloud deployments and Discord delivery need owner-side validation.
