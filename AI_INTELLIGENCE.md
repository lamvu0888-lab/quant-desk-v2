# Quant Desk AI planning and research

The monitor captures point-in-time market data, evaluates each published scenario locally and records paper candidates. The paper worker submits planning requests and reviews closed paper trades. The daily recorder can request one end-of-day optimization proposal. The API responds with strict JSON. Quant Desk checks level ordering, supported setups, flow confirmations, holding periods and bounded research proposals before use.

| Setting | Default | Purpose |
|---|---|---|
| QD_AI_PLANS | false | Enables paper scenario snapshots on the monitor and model processing on the paper worker. Older deployments can use GEMINI_PAPER_PLANS when QD_AI_PLANS is absent. |
| QD_AI_PROVIDER | openai | Selects the paper planning and review provider. |
| QD_AI_ROUTINE_MODEL / QD_AI_ROUTINE_EFFORT | gpt-6.2-sol / medium | Premarket plans, intraday revisions and closed-paper reviews. |
| QD_AI_RESEARCH_MODEL / QD_AI_RESEARCH_EFFORT | gpt-6.2-astra / high | End-of-day missed movements and research proposals. Model and reasoning choices are independent. Supported effort settings are low, medium, high and xhigh. |
| QD_V3_RECORD / QD_V3_OPENAI / QD_V3_EXTERNAL_AGGREGATES | false | Enable all three on the monitor for end-of-day research. |
| QD_V3_OPENAI_DAILY_USD / QD_V3_OPENAI_MONTHLY_USD | 5 / 150 | Set identically on workers using the same journal. |

Put OPENAI_API_KEY in the private environment of each worker making API requests. The first call to a model checks account access through the Models API. A rejected or unavailable model leaves the comparison waiting or failed. A live account and billable inference are required to validate the deployment end to end.

## Market checkpoints

- Premarket drafts start 30 minutes before the regular open when enough distinct market levels exist.
- The opening revision needs the first three completed five-minute candles. Midday and afternoon checkpoints occur halfway and three quarters through the actual regular session, including early closes.
- Significant regime changes and confirmed invalidations can queue a new version. Each published version is applied prospectively. A local scan between revisions makes no model call.
- The daily research stage becomes eligible 45 minutes after the exchange close. It needs recorded observations and external-aggregate opt-in. It proposes one bounded offline test and retains its evidence in the journal. Prospective conditional target/invalidation results and actual closed paper trades are recorded separately. Missing intervals, ambiguous bars and truncated follow-up have their own counts.
- Disabling plans or a temporary model outage leaves the original strategy and paper management available. To explicitly switch paper plans back to the historical integration, set QD_AI_PROVIDER=gemini and privately configure GEMINI_API_KEY and GEMINI_MODEL on the paper worker. That provider has separate billing. Set QD_AI_PLANS=false to stop new paper plan requests.

## Cost and data handling

The service uses the default synchronous OpenAI Responses API with store=false and a bounded output length. The monitor and paper worker must use the same PostgreSQL journal (or the same persistent SQLite file on one machine) for one spending ledger. Each request reserves a conservative upper bound atomically before inference. An unconfirmed charge retains its reservation. Reported token usage and the checked response are committed together. The private qdv3_ai_result table holds the response or a fixed rejection status, a request fingerprint and its cost. A restarted worker reuses a matching result. These receipts are stored in the shared journal and do not require read access from dashboard viewers. When usage omits cache-write detail, the estimate conservatively applies the write rate to the noncached input. The ledger covers this deployment's recorded OpenAI calls, and separate API consumers on the account can add charges.

Standard short-context USD per million tokens, checked November 28, 2026. Cache-write pricing uses the 30-minute retention selected by this service. Review the current pricing before changing the model allowlist or rates.

| Model | Input | Cached read | Cache write | Output |
|---|---:|---:|---:|---:|
| gpt-6.2-sol | $2.00 | $0.10 | $2.50 | $10.00 |
| gpt-6.2-astra | $8.00 | $0.40 | $10.00 | $40.00 |

[OpenAI API pricing](https://developers.openai.com/api/docs/pricing) · [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) · [Responses API](https://platform.openai.com/docs/api-reference/responses) · [Model discovery](https://platform.openai.com/docs/api-reference/models/retrieve)

Enabling paper plans sends allowlisted market context and completed simulated trade evidence to the selected provider. Enabling end-of-day feedback separately permits external aggregate processing. Missing inputs stay unavailable. The journal retains the input, timestamps, checked result and model for audit. Ticker-specific earlier daily summaries and up to five published closed-trade reviews are available as lessons for later sessions. Each proposed parameter change remains subject to independent replay, prospective testing and the existing promotion process.

### Offline contract-selection experiments

The daily reviewer can propose one fixed contract-ranking profile. IDs 0–3 retain the existing base, execution, downside and carry weights. IDs 4–9 apply a single stricter selection rule to contracts that already qualified at the original decision time.

| ID | Measured at the decision |
|---|---|
| 4 | Listed same-day expirations |
| 5 | Listed expirations 1–7 calendar days away |
| 6 | Absolute modeled Delta from 0.45 to 0.65 |
| 7 | Local Gamma implies a Delta change of 0.01–0.10 for a 0.1% underlying move |
| 8 | Provider and synchronized midpoint-implied IV agree within 3 volatility points |
| 9 | Spread up to 8% of midpoint and displayed ask size covers the proposed quantity |

All six rules retain the original base score and deterministic contract-code tie break among survivors. An explicitly declined opportunity earns zero in the paired comparison. Missing inputs for any original candidate make that comparison unscorable. Historical and later-session reports use recorded executable quotes, fees and an exit slippage allowance. IV agreement measures quote fit. It does not validate future volatility or a new pricing model. Displayed size does not establish an actual fill. These experiments have no order-routing or automatic promotion path.
