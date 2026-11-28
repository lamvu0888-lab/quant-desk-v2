# Quant Desk 3.0.0-rc1

## Windows installation and review

1. Extract the entire download to a temporary folder, for example Downloads\QuantDesk-V3. The outer directory contains START-HERE.txt, UPLOAD_V3_REVIEW.cmd, a Git bundle, a patch, a test transcript and the complete quant_options_agent source tree.
2. To preview without touching your current installation, open PowerShell inside the extracted quant_options_agent folder. Run the following commands one line at a time with Python 3.12 installed. In a credential-free session the dashboard offers Preview. Keep your existing live environment private.

       py -3.12 -m venv .venv
       .\.venv\Scripts\python.exe -m pip install -r requirements.txt
       .\.venv\Scripts\python.exe -m pytest -q -rs
       .\.venv\Scripts\python.exe -m streamlit run terminal.py

3. To upload the reviewed revision, double-click **UPLOAD_V3_REVIEW.cmd** in the outer folder. It uses Git for Windows and your existing folder E:\Webull autotrade\github\quant_options_agent. It fetches the upstream base and the packaged commit, verifies the commit ID, and explicitly pushes that commit to review/v3-intelligence. Local edits, untracked files, index and current branch are outside that push. GitHub may ask you to sign in through your Git credential manager. The legacy UPLOAD_TO_GITHUB.cmd defaults to staging all working files and pushing main. Use the V3 review uploader for this release.
4. Review the comparison on GitHub against main. If main has advanced, examine intervening code before merging. Publishing the review branch and merging it are separate decisions. A merge to a branch monitored by Streamlit or Render may redeploy automatically. Keep the Paper Trading Dashboard on its current Streamlit service and use one existing monitor worker.
5. To apply it locally to your existing folder, open PowerShell and follow the commands below. Substitute the actual extracted bundle path. The first command group is read-only in your working tree. If git status reports a rebase, conflicts, or local modifications, retain that tree and reconcile your changes before switching. The bundle uploader can still transfer the pinned revision. On a clean repository use a fresh branch name for the local review.

       Set-Location -LiteralPath 'E:\Webull autotrade\github\quant_options_agent'
       git status
       git fetch --no-tags https://github.com/lamvu0888-lab/quant-desk-v2.git main
       git bundle verify 'C:\Downloads\QuantDesk-V3\QuantDeskV3.bundle'
       git fetch --no-tags 'C:\Downloads\QuantDesk-V3\QuantDeskV3.bundle' refs/heads/review/v3-intelligence
       git switch -c review/local-v3 FETCH_HEAD

   For a patch-based workflow on a clean review branch at the base shown in START-HERE.txt, use git apply --check with the full path to QuantDeskV3.patch, then git apply with that same path. Choose either the bundle or patch workflow.
6. Roll back a local preview with git switch - immediately after testing on the new branch. For a reviewed hosted deployment, redeploy your previous known-good revision through the hosting service and revert the V3 change through a reviewed commit if needed. Existing journal records remain readable. The optional usage/lease tables are additive and can stay in place. Restore the previous service environment values as part of a hosting rollback.

## Where to look

**Movement Detector.** Select SPY, SPX or QQQ. Read the lifecycle, explanation and data confidence together. A sudden price move can raise an ignition warning even with unavailable flow. The contributor panel identifies which observations were available. A fresh ES/MES footprint for SPX is labeled as a proxy. Under V2 confirmation details the original five-minute, early-paper and risk checks are available. A flat or warming feed can correctly show INSUFFICIENT DATA.

**Trade Plan.** Movement timing appears above the entry checklist. An ignition calls attention to the existing directional setup. A completed bar is reevaluated through the monitor’s normal scan, and an opt-in provisional impulse can request an earlier evaluation. An advisory comparison shows contract alternatives when a fresh chain and a directional scenario exist. Buying power, entry quality, executable quotes and the paper ledger still determine simulated fills. No V3 warning grants order permission.

**Contracts.** Open Options research → Contracts. Choose an actual listed expiry, side and strike. Check settlement, price timestamp and freshness. Choose a manual target, a Trade Plan target or a movement barrier, then set arrival time, IV shock, rate, continuous dividend yield, entry premium, quantity and costs. The absolute time axis runs to the series’ payoff reference. ET labels, quote markers in raw mode, dashed model curves, optional session-only display and comparisons across expirations help isolate time decay. Compare earlier and later arrivals for the same target. At expiry, the model uses intrinsic value and suppresses undefined Greeks. The primary chart represents calendar time. Hiding closed sessions changes only the display. For SPX AM the opening time is a modeling reference. The ultimate SET fixing can differ from the displayed cash index level. For SPY/QQQ the regular equity close is the payoff-reference endpoint, with the separate last-trading reference displayed.

**Learning.** Open Tools & help → Learning while using the same private journal as the worker. Independent price events, matured horizons, qualified observations, blocked decisions, quotes, drift diagnostics and reviews accumulate as sessions complete. A missing data row and a zero-event day have different meanings. The V3 tables and signals are append-only. Opening a dashboard page does not schedule training or send a report.

## Configuration

The default enables the three dashboard views. Paid calls, new outbound alerts and durable V3 market-data capture require explicit enablement. Reuse your existing worker start command, price routing, and private MONITOR_JOURNAL_DSN. On Render these are environment variables. On Streamlit Community Cloud use root-level secrets for shared settings. Apply matching mode and display settings to services that evaluate signals. Streamlit read access to an unrelated SQLite file will show a different history.

| Setting | Default | Effect |
| --- | --- | --- |
| QD_V3_MOVEMENT / QD_V3_PLAN | true / true | Compute the shared signal / show timing on the Trade Plan. Turn off MOVEMENT to use the original detector. |
| QD_V3_MODE | Balanced | Aggressive, Balanced or Conservative evidence cutoffs. These presets are experimental. |
| QD_V3_OPTIONS / QD_V3_SELECTION | true / true | Contract workspace / conditional research ranking. |
| QD_V3_RECORD | false | On the existing monitor, capture the enabled watchlist in its private journal. Enable once your data agreement allows retention. |
| QD_V3_PROVISIONAL | false | Save distinct fresh quote observations and reevaluate on a provisional impulse. Requires RECORD. |
| QD_V3_DISCORD / QD_V3_DISCORD_REPORT | false / false | Optional lifecycle watches / post-close aggregate summary on the existing webhook. Requires RECORD and the appropriate distribution rights. |
| QD_V3_ML / QD_V3_ONLINE | false / false | Run after-close challenger benchmarks / delayed-label online replay. Requires RECORD. |
| QD_V3_EXTRA_MODELS | false | Evaluate locally installed LightGBM, XGBoost and River after sample gates. No extra worker is required. |
| QD_V3_ARCHIVE_DIR | empty | Optional private durable directory for zstd Parquet. Use a mounted path with adequate storage. SQLite is for a single host. Ephemeral Render paths do not provide a durable archive. |
| QD_V3_OPENAI / QD_V3_EXTERNAL_AGGREGATES | false / false | Opt in to research reviews and separately confirm permission for external aggregate processing. Configure OPENAI_API_KEY only on the research worker. |
| QD_V3_OPENAI_DAILY_USD / QD_V3_OPENAI_MONTHLY_USD | 5 / 150 | Application reservation ceilings. One request per session. Other apps on the API project can still incur charges. |

The worker checks on a five-minute cadence, also catching up recent internal stages on restart. Target offsets follow the exchange close, including half days. The report runs at +15 minutes, training +30, feedback +45, retrospective proposal replay +60 and optional Discord summary +65. A reservation survives uncertain HTTP delivery. An interrupted daily stage has a 30-minute lease. A daily summary is an aggregate and sends independently of normal signal alerts. No real Discord receipt was observed during offline testing.

If collection stays empty, verify that the existing monitor is running the reviewed revision, RECORD is enabled on that worker, the watchlist is active and fresh regular-session candles arrive. Check Data Health for provider/symbol timing and option entitlements. Keep the first full week as a forward data-quality audit. The model gate needs at least 20 sessions and 500 eligible labeled origins per ticker. Meeting it starts a research benchmark. No code promotes the winner into production.

## Safety and cost

The audited engine limits are $1,000 debit, two contracts, a 20% premium stop and a $500 daily loss stop. This release retains them. The $5,000/$1,000 settings mentioned in the brief were not the values in the working baseline. Compute, API entitlements, exchange exceptions, vendor retention limits and live credentials require confirmation in the user's environment. Use a private journal and controlled archive retention. Forecasts, quotes, raw arrays and API credentials belong outside GitHub. The ZIP excludes the uploaded environment, OAuth database and virtual environment.

For engineering coverage, empirical evidence and remaining work, read V3_RESEARCH.md and V3_ACCEPTANCE.md. Exact changed paths and the packaged test transcript are in the release root.
