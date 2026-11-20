# Quant Desk Paper Trading

## Gemini A/B/C plans and post-trade reviews on Render and Streamlit

The original strategy and the Gemini comparison each have a paper book. Gemini drafts A for bullish setups, B for bearish setups and C for buying a support reversal or selling a resistance reversal. Each active scenario names an existing Quant Desk setup, its trigger, invalidation, target and required flow confirmation. Gemini can choose **Wait** for any scenario.

1. Extract the update ZIP. Open the root of your existing GitHub repository on the branch your services deploy. Use **Add file → Upload files**, drag in the extracted contents including the `tests` folder, then **Commit changes**. If you use a pull request, merge it into the deployment branch. The archive contains changed files only.
2. In Render, open your existing **paper worker**. Its start command is `python paper_dashboard.py --worker`. Under **Environment**, add `GEMINI_API_KEY` with your private Google AI Studio key. Choose **Save, rebuild, and deploy**. The default model is `gemini-3.5-flash-lite`. To select another model with structured JSON output, set `GEMINI_MODEL` on this worker. [Create or view a Gemini API key](https://aistudio.google.com/apikey)
3. Open your existing **Discord monitor worker** on Render. Its command is `python discord_monitor.py --interval 30`. Under **Environment**, set `GEMINI_PAPER_PLANS` to `1` and choose **Save, rebuild, and deploy**. It captures scans for the enabled watchlist. The paper worker handles all calls to Google.
4. Both workers, Quant Desk on Render and the Paper Trading dashboard on Streamlit should use the same existing PostgreSQL journal through `MONITOR_JOURNAL_DSN`. Deploy the same GitHub revision to the dashboards. If your dashboard has a dedicated read-only database role, grant it `SELECT` on the new `qd_gemini_job` table. Quant Desk also reads its existing `qd25_observation` table.
5. From 9:00 a.m. ET on a trading day, open **Quant Desk → Trade Plan → Gemini A/B/C paper plan** for a monitored ticker. The draft needs three distinct underlying levels. The monitor requests prior regular, after-hours, overnight and premarket bars. For cash SPX, SPY extended hours are labeled proxy context. Expand Plan versions and session data to see source and coverage. You can see the source, publication time, prices, reasons and up to 60 minutes of recent scan decisions. Near the opening bell the first completed candles take time.
6. Open **Paper Trading → Gemini paper comparison** in journal mode. Select the session to compare entry times, skips, net closed P/L and the outcome of each position. A complete exit queues a review even if the position lost money. A partial exit waits for the final fill. A day with zero qualified trades can still produce a plan and a record of skipped setups.

### What the experiment tests

A paper candidate requires a published scenario, a matching completed Quant Desk setup, a score of at least 71, fresh data, directional flow, structural room and the existing price-path model checks. For trend setups it can act on aligned 1m/3m timing and 5m momentum with the latest aligned 5m premium close. A scenario can require two flow closes. Range reversals still require a 3m edge rejection and subsequent 1m follow-through. The underlying must clear the planned trigger, and the detected setup must be within one 5m ATR of that level. A scenario can tighten protection or cap the target.

Fills use the existing Webull simulation, including executable quotes, the spread limit, the 20% premium stop, the two-contract maximum and the $1,000 entry cap. Each simulated book has its own $500 daily loss stop. Gemini candidates go directly to the comparison book. Only the original monitor publishes Discord signals. The comparison includes no brokerage order calls.

### How feedback reaches the next session

The paper worker sends Google a limited report for every fully closed position from either strategy after activation. The report includes the timestamped setup, available EMA, options flow, volatility and dealer evidence, quoted fill prices, partial exits, fees reflected in net P/L, and net return. A Gemini review records a finding, one suggested change, a forward test and a measure of success. Credentials, account numbers and Discord history are outside the report.

The next plan for that ticker receives up to five reviews available from earlier sessions and a count of the previous session’s recorded reasons for waiting. Counts represent scan minutes. Skipped opportunities have no measured return. Gemini can use the feedback to choose a different supported setup or one versus two flow closes for a future paper scenario. Each version is frozen. The monitor requests the premarket version from 9:00 a.m. ET, an opening update after three complete opening 5-minute candles, and intraday revisions for confirmed regime changes or invalidations. Each revision takes effect after publication. Pending Gemini entries wait, and open positions retain their recorded levels. The database keeps the plan inputs, model, output and review linked to each closed trade.

Pending reviews persist across worker restarts. The worker retries temporary connection, quota and response-validation failures in the background. An HTTP 401 or 403 points to the paper worker's Gemini key or project access. For repeated HTTP 429 responses check the Google project's quota. A Google outage leaves original signals and paper position management running. To pause new scenario entries, set `GEMINI_PAPER_PLANS=0` on the monitor. The paper worker can continue managing open comparisons and reviewing completed positions.

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
