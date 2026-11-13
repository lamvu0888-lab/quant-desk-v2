## Day trade

### Before the session

1. **Data Health.** Select the ticker and intended expiry. During the regular session, check that price and completed 1-minute candles are current, 5-minute flow is aligned when available, and the actual contract has a fresh two-sided quote. For SPX, verify Tradytics index prices and Webull SPXW separately. Nearest listed can resolve to a later date if same-day contracts are absent.
2. **Price action and levels.** Mark the prior session high/low, early session range as it forms, VWAP when available and the nearest relevant modeled wall. Check whether price is trending or rotating around VWAP. Decide where the trade is invalidated and which obstacle comes before the first target.
3. **Calendar and contract.** Note scheduled events and the remaining session time. Compare same-day and later expirations where appropriate. Review bid/ask, actual expiry, settlement convention and maximum dollar loss. Same-day options can reprice sharply near the strike and lose their remaining time value quickly.

### During the setup

1. **5-minute direction, 3-minute structure, 1-minute timing.** Use the 5-minute view for direction and range, 3-minute for the break or retest, and 1-minute for entry timing. Look for expanding true range, a held level and room to the target. Repeated overlapping closes near VWAP suggest the range is still in control.
2. **Movement Detector and flow.** Treat MOVE WATCH as a cue to inspect the level. Check two completed aligned 5-minute flow bars against price for the dashboard's confirmation. Use recent interval bars more heavily than the full-session running total. Watch whether rising IV increases the premium you would actually pay.
3. **Trade Plan and payoff.** Compare the proposed entry with current spot, trigger, stop, invalidation and TP1. Use an actual quoted spread to estimate friction. Monte Carlo can compare entry variants at the same intraday horizon. Read fill probability and after-cost EV together. Recheck the price level and the option quote if you wait for a retest.

### Manage or pass

Plan a price invalidation, a premium loss budget and a time exit. Recalculate if you miss the entry. Pass on stale or unmatched inputs, a wide spread, poor target room, or a break that returns inside the range. A stop level on the underlying does not cap the option loss at the corresponding modeled premium.

**Worked example.** SPY breaks 760 with a 759 stop and 762 first target. A retest entry near 760 has roughly 2:1 gross underlying reward to risk. At 761, the same plan has 0.5:1. Wait for price and flow agreement, then compare the actual contract cost and spread. If the move is already near the target, the original geometry no longer applies.

## Swing trade

### Build the multi-session thesis

1. **Daily context first.** Use daily price structure in Overview when available and confirm higher-timeframe levels on your charting platform. Identify trend, the setup level, the daily invalidation and the next daily obstacle. Check whether the sector and broader market support the move. Money Map and breadth add context on their displayed session.
2. **Plan around catalysts.** Mark earnings, macro events, dividends and scheduled company updates during the planned hold. Decide in advance whether the trade depends on holding through the event. Use a listed expiry with time beyond the intended hold or catalyst and compare neighboring expiries.
3. **Read maturity-specific premium.** In IV Decision Center, choose the exact expiry, then inspect the term structure, skew and IV versus realized history. Use the selected contract's own IV and spread for execution. The 30-day benchmark helps with context when the chosen option is farther out.

### Time and manage the trade

1. **Price first, intraday timing second.** Look for a daily level that holds, a breakout with acceptance, or a pullback that forms a higher low for a bullish thesis. Use 5/3/1-minute panels to refine the fill after the daily setup exists. Look for directional options flow in relevant maturities and repeated clean prints where available.
2. **Compare outcomes before entry.** Estimate underlying and option outcomes if price reaches target, stalls for several sessions, gaps through invalidation, or moves as expected while IV falls. Compare net debit or credit and realistic exit friction. Define the dollar risk for the whole position and review short-leg assignment and settlement terms where relevant.
3. **Review after each close.** Track the underlying thesis, time remaining, catalyst changes and option liquidity. Re-evaluate if the expected move takes longer than planned. Trade Plan and the dashboard's Monte Carlo are intraday tools. Use their results for entry timing within the day and assess overnight gap and multi-session outcomes separately.

**Worked example.** A stock breaks above 100 with daily invalidation below 97 and an initial target at 108. Earnings falls in the planned hold. The first expiry after earnings shows higher IV than the adjacent maturities. Compare scenarios for reaching 108 before the report, moving to 104 after the report with lower IV, and gapping below 97. If the trade only works under the first path, reconsider the expiry, structure or timing.

## LEAPS

### Start with the long-term thesis

1. **Define a thesis and review dates.** Use weekly and daily structure, the business or index thesis, valuation assumptions and expected catalysts. Write what would disprove the thesis and when you will reassess it. For a stock, review earnings and financial statements from primary sources. A LEAPS contract generally starts with more than a year to expiration. Evaluate the time remaining on the actual contract.
2. **Use the right maturity.** Select a listed expiration in IV Decision Center or the chain. Compare two or three expiries and multiple strikes. If a far expiry is missing or the Webull sample is narrow, verify the full chain and contract specifications in your broker. Compare exact-expiry IV, skew, bid/ask spread, quote age, delta, vega, theta and open interest.
3. **Separate intrinsic and time value.** For an illustrative 90-strike call with the stock at 100 and premium of 15, intrinsic value is 10 and time value is 5. The expiration breakeven is 105 before fees. For a standard 100 multiplier, the debit is $1,500 per contract and can be lost in full. Before expiry the option can still have time value, so the expiration breakeven does not describe an early exit.

### Compare alternatives and stress the premium

1. **Compare different strikes and ownership.** A deeper in-the-money call often has higher delta and a larger debit. An at-the-money or out-of-the-money call can have less immediate price sensitivity and rely more on the size and timing of a move. Compare with shares or a defined-risk spread, including dividends, capped upside, exercise, assignment and liquidity where applicable.
2. **Stress price, IV and time together.** Check what happens if the underlying rises, falls or stays flat over several months. Then repeat with lower IV and fewer months remaining. A higher underlying price can coexist with a lower premium if time value and IV fall enough. Rates and dividends can also matter at long maturities.
3. **Plan an exit or roll review.** Decide when to re-evaluate remaining maturity, delta, quote width and opportunity cost. A roll is the close of one contract and the opening of another. Compare the two fills and the new risk. Track thesis invalidation on the underlying and review option mark and liquidity independently.

**Use the dashboard in this order.** Start with higher-timeframe price structure, then use IV term structure, exact-expiry skew and the actual options chain. Use the near-term flow or Movement Detector only to refine entry timing when helpful. The 0DTE gamma chart and intraday Monte Carlo describe a different horizon from the LEAPS thesis.

**Worked example.** At spot 100, compare a 90-strike call at 15 with a 105-strike call at 7, at the same far-dated expiry. Their expiration breakevens are 105 and 112 before fees. With a 100 multiplier, the debits are $1,500 and $700. At expiration with spot at 110, the first has $500 gross profit and the second has a $200 gross loss. At spot 95, they lose $1,000 and $700, respectively. To evaluate a sale six months earlier, reprice both with the remaining time and at several IV levels. Use executable bid and ask estimates in the comparison.

**Before committing capital.** Check the full-position debit or modeled maximum loss in dollars, the acceptable quote width, what would invalidate the thesis, the date of your next review and what you would do if the contract becomes hard to exit.

**Reference reading**

- [Options Industry Council guide to LEAPS](https://www.optionseducation.org/optionsoverview/leaps-overview)
- [Options Industry Council guide to LEAPS pricing](https://www.optionseducation.org/optionsoverview/leaps-pricing)
- [Options Industry Council guide to volatility and the Greeks](https://www.optionseducation.org/advancedconcepts/volatility-the-greeks)
