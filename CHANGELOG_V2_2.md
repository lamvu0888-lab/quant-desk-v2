# Quant Desk V2.2 — additions

1. Overview 0DTE gamma exposure chart by strike, separate from the global expiry selection.
2. Plotly chart legends moved underneath the plotting area and enlarged.
3. Independent read-only Discord monitor (`discord_monitor.py`) with fail-closed signal eligibility, basic deduplication, and environment-secret webhook.

Validation: Python syntax compiled successfully. Full pytest suite not executed in this build environment because Streamlit is not installed. Run `pip install -r requirements.txt` and `python -m pytest -q` before deployment. Live Tradytics API and Discord delivery were not tested without credentials.
