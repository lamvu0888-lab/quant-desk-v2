# Quant Desk V2.4 — Discord ticker controls and signal history

## What changed

- Streamlit sidebar → **Feed controls → Discord ticker monitoring**: edit comma-separated symbols, enable/pause alerts, set minimum 30-second interval, and save to a shared GitHub JSON file.
- **Trade Plan** → "What does this setup mean?" and "Discord signal history (cloud)". History shows UTC detection time, signal, price, trigger, invalidation, bar timestamp and explanation. Download CSV.
- Discord alerts now contain the explanation, evidence checks, trigger/invalidation, 5-minute completed bar, observed price and UTC detection time. No real orders.
- Watchlist defaults to SPY/SPX until first saved cloud configuration. Supports other tickers *only where Tradytics endpoints provide required price/flow data*; unsupported symbols fail closed and do not trigger signals.

## Shared GitHub configuration — REQUIRED for dashboard edits and durable cloud history

1. Create a **fine-grained GitHub personal access token** scoped to the specific `quant_options_agent` repository, with **Contents: Read and write**. Treat it as a secret. This method writes configuration and signal events as JSON commits in your repository; keep the repository private if you don't want trading-signal history publicly visible. GitHub Contents API has request and write limits; this lightweight log is not designed for high-frequency event capture.
2. In **Streamlit Cloud → App settings → Secrets**, add:

   ```toml
   MONITOR_GITHUB_REPO = "YOUR_GITHUB_USERNAME/quant_options_agent"
   MONITOR_GITHUB_TOKEN = "YOUR_FINE_GRAINED_TOKEN"
   MONITOR_GITHUB_BRANCH = "main"
   ```

   Keep your existing `TRADYTICS_API_KEY` and `DISCORD_WEBHOOK_URL` secrets.
3. In **Render → quant-desk-discord-worker → Environment**, add the same `MONITOR_GITHUB_REPO`, `MONITOR_GITHUB_TOKEN`, `MONITOR_GITHUB_BRANCH=main`, plus `TRADYTICS_API_KEY`, `DISCORD_WEBHOOK_URL` and `TRADYTICS_AUTH_MODE=query`. The included `render.yaml` declares these fields.
4. Deploy the same GitHub branch to both Streamlit and Render. Render worker runs `python discord_monitor.py --interval 30` and reads the watchlist from GitHub on each cycle. Its loop takes at least 30 seconds **plus API processing time**, and signals are evaluated only during the market session.
5. In the Streamlit sidebar open **Feed controls → Discord ticker monitoring**. Set tickers e.g. `SPY, SPX, QQQ`, click **Save cloud monitor settings**. New settings are applied on the worker's next scan. The dashboard alone cannot start/stop a sleeping or failed Render worker.
6. Open **Trade Plan** for a selected ticker to see the signal log and explanation. The history records **Discord alerts actually sent by the worker**, not every dashboard state transition. Old signals before V2.4 are not backfilled. The most recent 500 records are retained in GitHub.
7. Use **Send Discord test message** to test webhook connectivity separately. Check **Render → Logs** for live worker status. The test button does not test the worker's market-data permissions.

## Safety / operational notes

- **No live order submission**. Alerts are informational and the evidence score is NOT a probability of success.
- Do not add `MONITOR_GITHUB_TOKEN` to `.env.example`, commit `.env`, or paste it in chat.
- Both services must have the same GitHub token and repository. Without shared storage, the worker uses SPY/SPX defaults and the UI cannot save changes or display durable history.
- The worker only sends GET READY and ENTER SETUP; WAIT/AVOID/EXIT are *not* separately implemented signal events in this release.
- Discord alert timestamps are UTC, not local ET. Data source errors, stale candles and mismatched flow bars prevent entry alerts.
- If the shared GitHub service is configured but unreachable, the worker **pauses alerts rather than reverting silently** to hardcoded symbols.
- Concurrent writes or revoked token can cause logging errors; review Render logs. For larger deployments use a database rather than a GitHub JSON log.
