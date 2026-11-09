# Quant Options Agent V1.6 — read-only Webull integration

## Start on Windows
1. Extract this ZIP into a NEW folder (do not overwrite your trading collector).
2. Double-click `START_QUANT_DASHBOARD.cmd`.
3. Put your own Webull App Key and Secret in `.env`; set the correct endpoint (`api.sandbox.webull.com` or `api.webull.com`). Never share `.env`.
4. In the sidebar, turn OFF synthetic demo and start scanner. The market may be closed; historical data permissions and quote entitlement may vary.

## Implemented and verified in source
- Read-only SDK client, stock snapshot, M1 historical bar retrieval, completed-candle aggregation and volatility analysis.
- Full dark dashboard, clickable explanation panels, per-chart timeframe selectors, price candlesticks, GEX chart when complete option chain is available, report freshness and provenance.
- No TradeClient, order submission, trading PIN, or execution.

## IMPORTANT limitations
The uploaded SPY collector does NOT contain a validated multi-ticker full option-chain discovery and pagination routine. Therefore V1.6 intentionally runs in **PRICE_ONLY** mode for real Webull scans: verified stock spot plus volatility candles where available. GEX/Vanna/Charm/Delta are **UNAVAILABLE**, never silently filled with zeros or guessed. Demo mode still supports synthetic exposure examples. This is not a finished live full-chain exposure product.

Webull SDK may require token approval in your OpenAPI portal. A 401, pending authorization, market closure, missing quote delay or unavailable candle history is not a reason to bypass validation.

The `5m` candles are derived from real Webull `M1` bars. A minimum history is required for the volatility algorithm; some brokers return fewer bars than requested. 15m/1h are chart-only aggregations. Do not treat the 1h view as a full hour if data is incomplete.

## Testing
`python -m pytest -q`
`python quant_agent.py --demo --once --ticker SPY --volatility`
`python quant_agent.py --once --ticker SPY --volatility` (requires credentials)

## Security
Never include `.env`, `conf/token.txt`, virtualenv, or local logs in shared ZIPs. Reports may contain proprietary market data. Webull usage subject to account entitlement and API limits.
