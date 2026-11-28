# Quant Desk 3.0.0-rc3

## Windows installation and review

1. Extract the entire download to a temporary folder, for example Downloads\QuantDesk-V3. The outer directory contains START-HERE.txt, UPLOAD_RC3_REVIEW.cmd, a Git bundle, a patch, a test transcript and the complete quant_options_agent source tree.
2. To preview without touching your current installation, open PowerShell inside the extracted quant_options_agent folder. Run the following commands one line at a time with Python 3.12 installed. In a credential-free session the dashboard offers Preview. Keep your existing live environment private.

       py -3.12 -m venv .venv
       .\.venv\Scripts\python.exe -m pip install -r requirements.txt
       .\.venv\Scripts\python.exe -m pytest -q -rs
       .\.venv\Scripts\python.exe -m streamlit run terminal.py

3. To upload the reviewed revision, double-click **UPLOAD_RC3_REVIEW.cmd** in the outer folder. It uses Git for Windows and your existing folder E:\Webull autotrade\github\quant_options_agent. It fetches the upstream base and the packaged commit, verifies the commit ID, and explicitly pushes that commit to review/v3-integrated-intelligence. Local edits, untracked files, index and current branch are outside that push. GitHub may ask you to sign in through your Git credential manager. The legacy UPLOAD_TO_GITHUB.cmd defaults to staging all working files and pushing main. Use the V3 review uploader for this release.
4. Review the comparison on GitHub against main. If main has advanced, examine intervening code before merging. Publishing the review branch and merging it are separate decisions. A merge to a branch monitored by Streamlit or Render may redeploy automatically. Keep the Paper Trading Dashboard on its current Streamlit service and use one existing monitor worker.
5. To apply it locally to your existing folder, open PowerShell and follow the commands below. Substitute the actual extracted bundle path. The verification and fetch commands preserve the files in your working tree. If git status reports a rebase, conflicts, or local modifications, retain that tree and reconcile your changes before switching. The bundle uploader can still transfer the pinned revision. On a clean repository use a fresh branch name for the local review.

       Set-Location -LiteralPath 'E:\Webull autotrade\github\quant_options_agent'
       git status
       git fetch --no-tags https://github.com/lamvu0888-lab/quant-desk-v2.git main
       git bundle verify 'C:\Downloads\QuantDesk-V3\QuantDeskRC3.bundle'
       git fetch --no-tags 'C:\Downloads\QuantDesk-V3\QuantDeskRC3.bundle' refs/heads/review/v3-integrated-intelligence
       git switch -c review/local-v3-rc3 FETCH_HEAD

   For a patch-based workflow on a clean review branch at the base shown in START-HERE.txt, use git apply --check with the full path to QuantDeskRC3.patch, then git apply with that same path. Choose either the bundle or patch workflow.
6. Roll back a local preview with git switch - immediately after testing on the new branch. For a reviewed hosted deployment, redeploy your previous known-good revision through the hosting service and revert the V3 change through a reviewed commit if needed. Existing journal records remain readable. The optional usage/lease tables are additive and can stay in place. Restore the previous service environment values as part of a hosting rollback.

## Where to look

**Market Overview.** Read the decision and its reason, then use the price chart or open the Trade Plan. Zoom and pan are saved per ticker, timeframe and feed. Follow latest advances the window as new candles arrive. Open the market internals and evidence panels when you need the underlying measurements.

**Movement Intelligence.** Select SPY, SPX or QQQ and read the lifecycle, direction and the Trade Plan decision at the top. The trigger, target or scenario barriers, invalidation and expected horizon come from the current scan. Each of the seven checks shows the evidence and the next observation to watch. Heuristic scores use a 0–100 scale. A large score alone cannot qualify a paper entry. Historical charts share a time axis and offer 1m, 3m and 5m views. Select a detailed indicator below the command center. A missing historical measurement produces a gap. The Preview price replay uses synthetic candles.

**Trade Plan.** V3 ignition triggers the option-market check in the same evaluation even when a V2 breakout has not qualified. Trade Plan still evaluates trend, price structure, EV, quotes and risk. An independent setup such as a range reversal can qualify on its own rules. Discord V3 watches are opt-in and distinct from qualified trade notifications. Trade Plan compares eligible broker contracts on the same market paths. A developing setup can display a candidate. A qualified paper entry requires an eligible series, a recent paper-worker heartbeat with measurable remaining risk, and an execution quote for that exact contract. Open **Analyze contract** to carry the series, target, clock and entry assumption to the pricing charts.

**Options Contract Intelligence.** Select an underlying, listed expiration, side and strike. The nearest strike is shown first. Auto uses an eligible Trade Plan target, a directional V3 scenario barrier, or a flat underlying when neither supplies a target. Manual lets you set the target and arrival in Eastern Time. Read the ask and contract cost, then compare the dashed modeled premium, Delta, Gamma and Theta against available solid quote observations. A second chart shows those measures across underlying prices at a fixed evaluation time. IV is available in hover and as an overlay. The primary timeline uses calendar time to the series payoff reference. The separate last-trading marker matters especially for AM-settled SPX. The actual SET fixing can differ from the opening cash index.

Rates, carry, fees, contract count, chart interval, IV adjustment, custom paths and overlays are under Advanced settings. The rate can come from a qualifying Schwab chain. Otherwise the desk uses the configured carry assumptions. The initial ask and current IV use the selected quote when available, with an explicit midpoint-IV fit where supported. Future IV is a scenario using available skew and any specified shock. Contract comparisons use the same target and arrival, include costs and only compare eligible series that still exist at that time. The price-time surface, Greek details and recorded estimates remain available below the results.

**AI Learning.** V2 and V3 are compared on common recorded observations in the same forward window. Read counts and denominators before comparing results. The page also exposes model records, audit details and feedback. Preview cannot read or train on production research. For an opted-in live worker, use the same private journal for recording and display. A new build or mode needs its own observations before differences can be evaluated. Archive replay accepts explicit version and mode filters.

**Navigation and display.** Market Overview, Movement Intelligence, Trade Plan, Options Contract Intelligence, Dealer Positioning, Monte Carlo, AI Learning, Data Health and Learn are the primary destinations. Flow, volatility and the other specialist views remain in Research tools. The workspace uses a single dark palette. On a phone, open the sidebar to change workspaces.

## Configuration

The default enables the three dashboard views. Paid calls, new outbound alerts and durable V3 market-data capture require explicit enablement. Reuse your existing worker start command, price routing, and private MONITOR_JOURNAL_DSN. On Render these are environment variables. On Streamlit Community Cloud use root-level secrets for shared settings. Apply matching mode and display settings to services that evaluate signals. Streamlit read access to an unrelated SQLite file will show a different history.

| Setting | Default | Effect |
| --- | --- | --- |
| QD_V3_MOVEMENT / QD_V3_PLAN | true / true | Compute the shared signal / show timing on the Trade Plan. Turning MOVEMENT off pauses V3 display and computation. Historical V2 comparisons are in AI Learning. |
| QD_V3_MODE | Balanced | Aggressive, Balanced or Conservative evidence cutoffs. These presets are experimental. |
| QD_V3_OPTIONS / QD_V3_SELECTION | true / true | Contract workspace / ranked series selection for the supported paper paths. Disabling SELECTION restores the legacy contract lookup for subsequent plans. |
| QD_CONTRACT_RANK_MODE | risk_adjusted | Worker objective. Supported values are risk_adjusted, net_profit, net_return, conservative and probability. The last requires previous validation for the applicable cohort. |
| QD_AI_PLANS / QD_AI_PROVIDER | false / openai | Paper ABC planning and closed-trade reviews. Older installations should review GEMINI_PAPER_PLANS and their explicit provider choice. See [AI intelligence](AI_INTELLIGENCE.md). |
| QD_V3_RECORD | false | On the existing monitor, capture the enabled watchlist in its private journal. Enable once your data agreement allows retention. |
| QD_V3_PROVISIONAL | false | Save distinct fresh quote observations and reevaluate on a provisional impulse. Requires RECORD. |
| QD_V3_DISCORD / QD_V3_DISCORD_REPORT | false / false | Optional lifecycle watches / post-close aggregate summary on the existing webhook. Requires RECORD and the appropriate distribution rights. |
| QD_V3_ML / QD_V3_ONLINE | false / false | Run after-close challenger benchmarks / delayed-label online replay. Requires RECORD. |
| QD_V3_EXTRA_MODELS | false | Evaluate locally installed LightGBM, XGBoost and River after sample gates. No extra worker is required. |
| QD_V3_ARCHIVE_DIR | empty | Optional private durable directory for zstd Parquet. Use a mounted path with adequate storage. SQLite is for a single host. Ephemeral Render paths do not provide a durable archive. |
| QD_V3_OPENAI / QD_V3_EXTERNAL_AGGREGATES | false / false | Opt in to research reviews and separately confirm permission for external aggregate processing. Configure OPENAI_API_KEY only on the research worker. |
| QD_V3_OPENAI_DAILY_USD / QD_V3_OPENAI_MONTHLY_USD | 5 / 150 | Application reservation ceilings. Shared across intraday plans, reviews and research. See [AI intelligence](AI_INTELLIGENCE.md). Other apps on the API project can still incur charges. |

The worker checks on a five-minute cadence, also catching up recent internal stages on restart. Target offsets follow the exchange close, including half days. The report runs at +15 minutes, training +30, feedback +45, retrospective proposal replay +60 and optional Discord summary +65. A reservation survives uncertain HTTP delivery. An interrupted daily stage has a 30-minute lease. A daily summary is an aggregate and sends independently of normal signal alerts. No real Discord receipt was observed during offline testing.

If collection stays empty, verify that the existing monitor is running the reviewed revision, RECORD is enabled on that worker, the watchlist is active and fresh regular-session candles arrive. Check Data Health for provider/symbol timing and option entitlements. Keep the first full week as a forward data-quality audit. The model gate needs at least 20 sessions and 500 eligible labeled origins per ticker. Meeting it starts a research benchmark. No code promotes the winner into production.

## Safety and cost

The audited engine limits are $1,000 debit, two contracts, a 20% premium stop and a $500 daily loss stop. This release retains them. The $5,000/$1,000 settings mentioned in the brief were not the values in the working baseline. Compute, API entitlements, exchange exceptions, vendor retention limits and live credentials require confirmation in the user's environment. Use a private journal and controlled archive retention. Forecasts, quotes, raw arrays and API credentials belong outside GitHub. The ZIP excludes the uploaded environment, OAuth database and virtual environment.

Read [the RC3 research and operating notes](V3_INTEGRATED_RESEARCH.md) for contract selection, calibration and evidence requirements. V3_RESEARCH.md and V3_ACCEPTANCE.md document the earlier release. Exact changed paths and the packaged test transcript are in the release root.

## Replay recorded sessions on Windows

Once private daily Parquet files exist, run the following inside the extracted source with the same environment. Substitute your authorized archive location. The offline evaluator reads only that directory and issues no provider, broker, AI or notification requests.

    .\.venv\Scripts\python.exe v3_evaluate.py 'D:\QuantDeskResearch' --symbol SPY

Repeat with SPX or QQQ. The output includes daily coverage and pricing matches, five-minute baseline and V3 comparisons on identical origins, a 2,000-draw paired session bootstrap after ten covered dates, a development benchmark and bounded threshold sensitivity. A confidence interval on overlapping-origin recall does not measure profitability. Treat delivered forward time periods as a separate, locked validation sample before approving trading changes.

## Using RC3 on the current hosted services

1. Upload the review branch using the outer Windows uploader. Review the source diff against main. You can preview the dashboard with synthetic inputs before changing any hosted service.
2. When you choose to release it, use the same reviewed revision for the Quant Desk monitor and paper worker. The dashboards and workers that need shared state must point at the same private PostgreSQL journal. A dashboard-only update can show the new screens while an older worker continues producing older signals. Keep the existing paper service on Streamlit.
3. To use OpenAI for ABC plans, set `QD_AI_PLANS=true` and `QD_AI_PROVIDER=openai` on the monitor and paper worker. Put `OPENAI_API_KEY` in the environment of the paper worker. Also configure the key on the monitor if enabling after-close OpenAI research. For after-close learning on the monitor, enable `QD_V3_RECORD` and `QD_V3_ML`. For a paid aggregate review also enable `QD_V3_OPENAI` and `QD_V3_EXTERNAL_AGGREGATES`. Set the same daily and monthly budget on both workers. An existing `GEMINI_PAPER_PLANS=true` can enable the migrated plan service when `QD_AI_PLANS` is absent. Set the desired values explicitly before rollout.
4. During a regular session, `python discord_monitor.py --dry-run --once` on the existing monitor shows the monitored-expiry feed and the separate ranked-contract check. A qualified direction can still be waiting for market depth, current quotes, risk capacity or a plan. Use the paper ledger to confirm an actual simulated fill.
5. Open **AI Learning** after the regular close for counts of eligible origins, complete alternative sets, and missing outcomes. Validate live SPX and QQQ separately. When available, the successor report for an earlier proposal shows results from later sessions with the original candidates and prices.

## Reproducing the tests

Use Python 3.12 and the versions in requirements.txt. Run `python -m pytest -q -ra` inside the source folder. To include the PostgreSQL integration cases, create an empty disposable test database and privately set both `QUANT_DESK_TEST_PG_DSN` and `SCHWAB_TEST_PG_DSN` for the test process. Tests create database objects and exercise token revocation and worker recovery using fixtures. Keep production connection strings out of the test process. The packaged transcripts identify any dependency warnings. Browser validation used Chromium at 1440×950 and 390×844 with controlled inputs.
