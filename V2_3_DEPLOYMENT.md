# Quant Desk V2.3 - Discord worker and GEX chart

## Changes
- Overview: focused near-spot net GEX only, no calls/puts traces or legend.
- 0DTE preferred; when missing or stale, checks the next 7 calendar-day expirations and displays the actual expiration and DTE. The main dashboard DTE selection is unchanged.
- Existing Discord test button remains in terminal.py.
- Background Discord worker: SPY and SPX, 30-second scan cycle, no broker orders.

## Deploy
Push the project contents to the same GitHub repository/branch used by Streamlit.
In Render create a Blueprint from the repository using render.yaml.
Choose a paid worker plan if required by Render; verify current pricing before enabling.
Add TRADYTICS_API_KEY and DISCORD_WEBHOOK_URL as private environment variables in Render.
The worker is a separate service: Streamlit hosting alone does not start it.
Check Render logs for per-symbol scans and send a test message using the existing Streamlit dashboard button.
Never commit .env, API keys, or webhook URLs.

## Safety / validation
Monitoring is read-only and suppresses signals when data is stale or the market is closed.
This build has not been tested against live Tradytics entitlements or a real Discord webhook.
