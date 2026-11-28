# Quant Desk Paper Trading

## Configure the A/B/C comparison

1. Set the same `MONITOR_JOURNAL_DSN` on the monitor, paper worker and both dashboards. Use the same code revision on both workers. A dedicated read-only dashboard database role needs SELECT access to `qd_gemini_job`, the existing paper and observation tables, and `qdv3_usage` to show research spend.
2. Set `QD_AI_PLANS=true` and `QD_AI_PROVIDER=openai` on the monitor and paper worker. Put `OPENAI_API_KEY` in the **paper worker's** private environment. The existing commands are `python discord_monitor.py --interval 30` and `python paper_dashboard.py --worker`. The monitor records conditions, and the paper worker submits paper plan and review requests. The display dashboards read journal entries.
3. For the optional end-of-day research call, enable `QD_V3_RECORD=true`, `QD_V3_OPENAI=true` and `QD_V3_EXTERNAL_AGGREGATES=true` on the monitor. Set `OPENAI_API_KEY` there too. The shared dollar limits, data sent and model choices are documented in [AI intelligence](AI_INTELLIGENCE.md).
4. Open **Quant Desk → Trade Plan → A/B/C paper plan** and select a monitored ticker. Versions show publication time, model and observed coverage. If historical SPX extended sessions use SPY, their prices are labeled proxy context. Open the Paper Trading comparison in journal mode to see separate baseline and A/B/C fills, skips and after-cost results.

### How papers are evaluated

A published A/B/C scenario supplies a conditional setup, direction, trigger, target, invalidation and required flow. The monitor evaluates the actual tape locally. Each paper candidate still needs the score minimum, matching valid setup, fresh price and options data, flow confirmation, room to the target, price-path model approval and applicable contract and execution checks. Range reversals retain their rejection and follow-through checks. Existing paper risk limits apply. A pending plan or a day without a matching setup can yield zero comparison trades.

A fully closed recorded paper position can generate a review of its market context, fills, costs, outcome and one proposed experiment. A partial exit waits for the final fill. Future plans consult earlier same-symbol reviews and aggregated results available at the decision time. The original strategy and its monitor continue to run independently. Historical journal table and track identifiers containing `gemini` are retained for prior results.

## Open the dashboard on Windows

1. Extract the entire ZIP to a new folder. Install Python 3.12 if Python is missing.
2. Double-click **START_PAPER_DASHBOARD.cmd**. The launcher installs the existing Quant Desk dependencies when needed and starts the dashboard on [localhost:8502](http://localhost:8502).
3. Leave **Show demo data** on to explore the positions, fills, P/L chart, and session review. The demo uses fixed example quotes.

You can also start it with `python -m streamlit run paper_dashboard.py --server.port 8502 --server.address 127.0.0.1`.

## Follow the real alert journal in simulation

The paper worker reads confirmed **ENTER LONG** and **ENTER SHORT** candidates whose Discord delivery the Quant Desk worker recorded as sent. That means it follows the same watchlist with no second ticker list. Start the new paper worker before the session you want to record. It follows alerts created after first activation. Previously queued alerts may age out, and a skipped trade records the reason.

1. Give the paper worker the same `MONITOR_JOURNAL_DSN` that the Discord monitoring worker uses. For processes running on the same computer, they can instead use an identical absolute `MONITOR_JOURNAL_PATH` to a local SQLite journal. If both run in this extracted folder with the defaults, they share its local journal.
2. Set `WEBULL_ENDPOINT=api.webull.com` and configure your Webull OpenAPI market data credentials and `TRADYTICS_API_KEY` in the worker environment or a private `.env`. Use a production Webull market data entitlement that reports zero delay. Tradytics supplies the next listed expiry calendar. Webull verifies the exact contract and provides the fill quotes.
3. Keep the original alert worker running, then start the paper worker with `python paper_dashboard.py --worker`. The paper worker can run with the dashboard browser closed.
4. Open the dashboard and turn off **Show demo data**. The dashboard process needs the same journal setting. Set `PAPER_HIDE_DEMO=1` on the dashboard to open directly in journal mode.

For separate cloud services, use shared PostgreSQL. In a separate Render worker use the existing Python build requirements and start command `python paper_dashboard.py --worker`. For a separate Streamlit app use `paper_dashboard.py` as the entry point and set `MONITOR_JOURNAL_DSN` in its private secrets. You can use a separate PostgreSQL login for that app with SELECT access to `qd_paper_trade` and `qd_paper_meta`. Run one paper worker per journal.

## How the simulator fills and exits

- Fast confirmed moves look for the nearest affordable slightly out-of-the-money 0DTE call or put, up to 0.5% from spot. Other qualifying setups use the next listed expiry and a strike within 0.75% of spot. If the Trade Plan blocks a sideways setup, there is no trade to simulate.
- Simulated buys use a fresh Webull ask observed after the alert. Entries need a valid bid and ask, an observed spread of at most 20% of the midpoint, and an underlying price still inside the setup. The worker skips unavailable contracts and expired signals. Maximum size is two contracts, with at most $1,000 in premium and estimated entry fees.
- It tracks the plan stop, invalidation, both targets, and the planned hold time. For two contracts it exits one at TP1 and tracks the second to TP2, stop, invalidation, or time limit. A one-contract trade takes TP1. It also queues an exit if the option bid falls 20% below the entry ask. When observed daily net P/L reaches −$500, it stops new entries and queues exits on open trades. Losses can exceed the thresholds while waiting for a bid or after a price gap.
- A paper exit uses a fresh bid observed at or after the exit trigger. If a contract expires before an exit can be valued, it is marked unresolved. Open P/L uses fresh bids and displays coverage. Fees are an assumed $0.65 per contract per side. The simulator approximates small fills from displayed quotes and does not submit brokerage orders.
- Daily learning fits a regularized model using prior closed paper trades. It compares the model's suggested filter with all observed fills on later sessions and shows how many trades it would retain. Suggestions are advisory. The model cannot infer P/L for skipped alerts or unavailable contracts.

## Verification

From the extracted folder, run `python -m pytest tests/test_paper_dashboard.py -q` for tests using local fixtures. Live cloud credentials and actual service deployment must be verified in your own environment.
