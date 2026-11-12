# Quant Desk 2.6

## 2.6.1 Movement Detector charts

Every check now includes a compact chart and a **Look next** cue tied to its current status. Market State shows completed 5m closes; Volatility shows recent 5m true range; Dealer Positioning shows current modeled net GEX by strike; Netflow shows completed 5m net premium; Structure shows 1m closes with range and VWAP levels; Momentum shows 5m price change and available native volume; Movement Decision shows completed price and flow checks plus the evidence score; Trade Trigger plots underlying stop, invalidation, trigger and targets; Data Quality shows quote, price, flow and GEX age against freshness limits. Missing or stale evidence does not become a synthetic chart. The charts explain current Trade Plan evidence; they do not change entry rules or backtest results.

## Start with Movement Detector

Open **Movement Detector** from Overview or the workspace menu. Read the checks from top to bottom. **Movement Decision** describes the market move and **Trade Trigger** gives the current Trade Plan action.

| Check | What to look for |
| --- | --- |
| Market State | Sideways, uptrend or downtrend based on completed 5m closes, recent displacement relative to 5m ATR and how directly price moved. |
| Volatility | Compression, normal or expansion. A directional entry during compression requires two closes beyond a boundary known beforehand plus expanding activity. |
| GEX / Dealer Positioning | Selected-expiry modeled GEX, gamma flip, call wall, put wall and compatible 5m gamma, vanna and charm scenarios. |
| Netflow / CVD | Recent signed options premium and the two individual 5m bins. Estimated share CVD is separate. On SPX, the optional SPY comparison is labeled with its own symbol. |
| Price Structure | Prior range, confirmed break or retest, and VWAP when the feed supplies enough session data. |
| Momentum / Volume | Closed 1m, 3m and 5m direction, price acceleration and available relative share volume. |
| Movement Decision | Sideways, preparing, breakout watch, long or short trigger, confirmed move, or no trade. |
| Trade Trigger | Enter, get ready, wait or avoid. See the underlying trigger, invalidation, stop, targets, evidence score and available Monte Carlo estimates. |
| Data Quality | Price, candle, flow and exposure times. Delayed or unsynchronized core feeds hold the trade plan in wait. |

Trade Plan selects a playbook from the current price regime and setup. Trends look for EMA 8/10 pullbacks, VWAP reclaims or rejections and liquidity sweeps. Breakout and expansion conditions prioritize range breaks and retests. Sideways conditions surface range-reversion ideas for study and hold the directional entry rules until price releases from the range. Relative volume compares the same five-minute slot in at least three earlier sessions where available and uses earlier contiguous bars when that comparison is thin.

Entry requires an evidence score of at least 80, agreement across two completed 5m direction checks, two distinct aligned 5m signed premium intervals, completed 1m and 3m timing, directional 5m momentum and acceptable stop and target geometry. Each of the two flow intervals must clear the direction-adjusted floor used by the flow read, which starts at $25,000 and can rise with recent premium activity. Monte Carlo can move a candidate to **Get Ready** or **Avoid** on weak or inconsistent model results. Its estimates describe paper scenarios and use matched forward observations for calibration.

## Strategy Lab audit

The supplied Strategy Lab archive defines the 100 strategies. Its controlled validation report covered all 100 module paths. The fresh historical audit used 1,000 closed Webull SPY candles across four sessions, from October 30 through November 4, 2026. The first and last sessions were partial.

| Measure | Result |
| --- | ---: |
| Strategy records reviewed | 100 |
| Historical modules completed | 64 |
| Modules needing additional inputs | 36 |
| Strategies with at least one selected test fold | 56 |
| Selected strategy-session test folds | 110 |
| Selected results with positive combined test P&L at normal costs | 8 |
| Selected results with positive combined test P&L at doubled costs | 2 |
| Configurations promoted | 0 |

The two positive stress results were the Kelly and volatility position-sizing studies. Both retained their default settings on the matched test folds. The directional strategies had zero positive results at doubled costs. On these folds, the VWAP intraday mean-reversion study MR20 moved from $5.05 with default settings to $29.09 with selected settings, then to −$60.75 with doubled costs. The Donchian breakout study TF07 improved from −$63.98 to −$25.12 and finished at −$78.49 under stress. These are underlying simulations on the supplied data.

Candidates were evaluated on the preceding session with earlier bars available for warm-up. Selection used net P&L less 1.5× drawdown and required at least four trades or equivalent fills in the selection session. The next session was evaluated with settings frozen, next-open fills, session close-outs and the Lab risk rules. Normal cost was 1 bp on each modeled fill plus $0.50 per order. The stress pass doubled both. The two test windows were November 3 and the available part of November 4. The supplied Lab uses at least 20 test sessions for its promising-candidate label. The audit retains each attempted setting, unchanged baseline, cost stress result and missing-data reason.

The saved SPY sample supports single-symbol underlying studies. Options and cross-asset validation needs synchronized historical bid/ask, IV, flow, exposure or companion-symbol data as specified by each module. The saved collector ticks supplied spot and selected-contract marks without the signed premium intervals or full-chain dealer history needed for an end-to-end SPY/SPX entry replay. Historical calibration and paper history are versioned by decision rules.

Open **Strategy Lab** to filter the table, review the individual folds or download the audit. To rerun it with the supplied Lab and its research dependencies installed, extract the Lab archive and pass the directory containing `researchlab`. Supply `--data` to use an updated Strategy Lab input folder.

```sh
python research/run_strategy_audit.py --lab /path/to/Strategy_Research_Lab_v3
```

## Discord and deployment

The worker posts **Enter Long**, **Enter Short**, tracked **Exit** and sustained data-quality warnings. A 10-minute cooldown applies per ticker and setup. An entry can include a crossed dealer level if the completed close and flow agree. Get Ready, Wait and Avoid remain visible in Trade Plan history. Data warnings start after three consecutive unhealthy scans and rearm after two healthy scans. The dashboard includes the Discord test button.

The November 11 worker history contained 132 delivered messages. Of those, 77 were Get Ready, Wait, Avoid or standalone level crossings, which the new status filter excludes. The other 55 consisted of 39 data warnings, eight entries and eight exits. Those counts describe the old messages by status. A replay of the new entry and warning rules would require the underlying scan inputs.

Extract the full ZIP and run **START_QUANT_DASHBOARD.cmd** on Windows or `bash start_dashboard.sh` on Mac or Linux. Use [the deployment steps](V2_5_DEPLOYMENT.md) to update the current GitHub checkout, Streamlit app and Render worker. The release uses `terminal.py` and `python discord_monitor.py --interval 30`. The sidebar shows **Options intelligence · 2.6** when the update is active.

## Research references

- Intraday volatility and trading volume vary systematically by time of day. This motivates the relative-volume time-slot comparison. [Andersen and Bollerslev, Journal of Empirical Finance](https://doi.org/10.1016/S0927-5398(97)00004-2)
- Order-book imbalance uses quote and order-book events. The dashboard labels share CVD as a signed-volume estimate from available candles and keeps it separate from options premium. [Cont, Kukanov and Stoikov](https://arxiv.org/abs/1011.6402) · [TradingView CVD methodology](https://www.tradingview.com/support/solutions/43000725058-cumulative-volume-delta/)
- Cboe's SPX 0DTE dealer analysis uses participant-level buys and sells. Quant Desk uses vendor-modeled exposure and scenario sensitivities as market context. [Cboe SPX 0DTE research](https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options)
- Chronological evaluation keeps future observations out of candidate selection. Reviewing many configurations increases selection bias, so the audit reports individual folds and unchanged settings. [Hyndman and Athanasopoulos, Time series cross-validation](https://otexts.com/fpp3/tscv.html) · [Bailey et al., The Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb)
