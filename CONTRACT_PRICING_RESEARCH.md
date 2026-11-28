# Option mark calibration and the two chart axes

## Reproducing the $1.62 screenshot

The SPY preview for November 6 at 12:16:20 p.m. ET selects the standard 778 call expiring that afternoon. Its synthetic bid and ask are $1.56 and $1.62, giving a $1.59 midpoint. The fixture supplies 19.4% IV independently of those prices and assigns a tape-print spot from an earlier synthetic trade. Contract Intelligence uses the current SPY observation of about $776.52 with a source clock 20 seconds before the option quote. The signed contract identifier, expiration and multiplier resolve consistently.

Using 19.4% IV, the desk’s 4% annual rate, zero dividend yield and its 4 p.m. payoff reference gives **$0.64263** in this fixture. The roughly 98-cent discrepancy between the ask and the old model line includes both the inconsistent IV and the three cents between ask and midpoint. A midpoint inversion gives **35.15% IV** and a fair value near **$1.59**. The analysis charges a long buyer the $1.62 ask by default. A future quoted exit can differ from any modeled mark.

## How to read the charts

- The **time chart** moves the underlying along the selected path, evolves the configured IV scenario and reduces remaining calendar time through settlement. Solid observations preserve their actual source timestamps. Dashed scenarios end at the payoff reference. The target marker is a hypothetical arrival and exit time.
- The **underlying price chart** holds the valuation clock and scenario IV fixed and changes only spot. Use Advanced settings to evaluate now, at target arrival or at expiration. It shows price levels that apply to the selected scenario. At expiration, the model reports intrinsic payoff and suppresses undefined Greeks.
- The red line is long-option Theta in premium dollars per share per calendar day. Delta measures premium change per dollar of underlying, and Gamma measures Delta change per underlying dollar. The Greek sensitivities are local derivatives. The P&L shown in the hover uses a full model reprice. IV remains available in hover and as an optional line.
- Choosing a contract from Trade Plan carries its exact OCC series, target, arrival and entry assumption. The workspace rechecks the listing and current quote. A change in the live offer is shown alongside the original entry assumption.

## What a calibration establishes

The model only fits a fresh, uncrossed two-sided quote to a fresh price. It checks each source clock, enforces a 30-second maximum clock difference and retains the existing spread limit. The 30 seconds is an engineering tolerance. A volatile underlying can move materially within it. The quote alignment panel shows the actual clock difference and both IVs. The model retains a usable provider IV if its price lies inside the quoted spread with half a cent of rounding tolerance. Otherwise it uses the midpoint-implied IV when the selected pricing model admits a solution. If timestamps or the root solve fail, the displayed reason explains the gap. Subsequent model P&L includes a modeled exit concession and fees.

Cboe’s historical option interval data distinguishes the bid, offer, associated size and underlying observations. Research using OptionMetrics also distinguishes quote midpoints from transacted prices and discusses asynchronous observations. Those distinctions motivate separate displays for entry cost, bid/ask midpoint and theoretical mark. Each chart projects a conditional scenario. [Cboe historical option quotes](https://datashop.cboe.com/option-quote-intervals) · [University of Cologne working paper on implied volatilities](https://www.cfr-cologne.de/download/workingpaper/cfr-09-07.pdf) · [Cboe SPX settlement specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications)

## Historical collector replay

Fourteen stored Quant Desk collector sessions across eleven trading dates in October 2026 supplied 22 named SPY call/put series and timestamped underlying/midpoint samples. The dry-run collector recorded no executions in this analysis. Only one fixed, identified contract pair per run, positive marks and a future sample within ten seconds of the five-minute horizon were accepted. Evaluation steps forward to the end of each window before choosing the next origin within that session. Exact duplicate contract/time origins are collapsed. Calls and puts can share an underlying path. The model fits origin midpoint IV, freezes it, and reprices at the **realized later underlying spot** and remaining time using 4% annual carry and zero dividends. The flat comparison carries forward the entry midpoint.

| Series | Five-minute pairs | Conditional mark MAE per share | Unchanged-midpoint MAE per share |
| --- | ---: | ---: | ---: |
| Calls | 504 | $0.0319 | $0.1801 |
| Puts | 503 | $0.0290 | $0.1754 |
| Combined | 1,007 | $0.0304 | $0.1778 |

This measures valuation error conditional on knowing the later SPY price. It supplies no evidence of predictive edge, entry profitability or achievable fills. The samples contain collector timestamps and midpoints. Individual exchange quote clocks, bid/ask size and historical spread are unavailable. The results cannot establish NBBO synchronization or validate the live 30-second matching rule. The sample covers SPY over eleven dates; independent QQQ, SPX and wider-regime validation remains open. The release omits the private input directory.

Reproduce the check with Python in an environment containing the project dependencies and your recorded collector folders.

```powershell
python validate_contract_marks.py 'D:\QuantDeskResearch\research_collector_sessions'
```
