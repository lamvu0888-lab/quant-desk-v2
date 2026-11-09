# Quant Options Agent V1.8 — White Contrast Dashboard

- White high-contrast Streamlit interface and Plotly charts.
- Independent chart timeframe controls and on-demand explanation popovers retained.
- Synthetic demo mode is OFF by default. Live market data still requires your local Webull credentials and Webull market-data entitlements.
- GEX/Vanna/Charm/Delta are modeled, scoped exposure estimates, not observed dealer positions.
- **Start:** double-click `START_QUANT_DASHBOARD.cmd` after installing dependencies; open http://localhost:8501.
- Do not run other starters simultaneously. Stop existing scanner/dashboard instances before starting.
- This package contains no `.env`, tokens, reports, or local account credentials. Copy your own `.env` from your local secure configuration.
- Real Webull connection cannot be verified without access to your authenticated account.
