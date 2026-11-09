# Quant Options Agent V1.7 — Scoped Greeks data integration

This version attempts real option-contract discovery with Webull SDK 3.0.4 `data.instrument.list_option_contracts` (V2 paginated endpoint) and option snapshots (`data.option_market_data.get_option_snapshot`, <=20 symbols/request). It computes modeled gamma, vanna, charm, and delta **only if all discovered pages and every snapshot within the configured bounded universe pass validation**.

## Run
1. Extract the ZIP, copy `.env.example` to `.env`, and set your Webull App Key and Secret locally. Never share `.env` or token files.
2. Double-click `START_QUANT_DASHBOARD.cmd`.
3. To collect live analysis, run `START_QUANT_AGENT.cmd` and select live scan; select the same ticker in the dashboard.
4. Test with `python quant_agent.py --ticker SPY --once --volatility` before continuous mode.

## Coverage and limitations
- Default scope: expirations within 7 calendar days and strikes within ±8% of spot. This is **not** total market gamma. GEX/gamma flip/walls reflect only this window.
- SDK response schemas, option IV/OI field availability, option contract multipliers, and production entitlements must be checked against your account. If any field is missing, or pagination exceeds the cap, the scanner blocks rather than inventing values.
- Option open-interest publication timestamp is not provided/verified by this adapter. Reports are marked `SCOPED_MODEL_UNVERIFIED_OI_ASOF`.
- Signed exposures use calls-positive/puts-negative proxy, not actual dealer positions. Do not interpret signs as observed dealer hedging.
- SPX requires `CHAIN_ROOT_SYMBOL=SPXW` or `SPX`; run separate roots and never silently merge them.
- Webull option snapshots are fetched in batches of 20, so scanning a large chain can be slow and subject to 429 rate limits. Narrow the strike/DTE scope or increase scan interval if necessary.
- Quote entitlement delay must be zero and quote timestamp fresh. Outside market hours live scans may be blocked as stale.
- **No order endpoints, trading tokens, or order submission code.**

## If it fails
Run `python quant_agent.py --ticker SPY --once --volatility` and share the redacted `[BLOCKED]` message. Common errors: `Unknown option list schema`, `Missing option snapshots`, `contracts missing IV/OI/multiplier`, `pagination exceeded page cap`, `401`, or `429`. Do not share keys, secrets, signatures or token files.
