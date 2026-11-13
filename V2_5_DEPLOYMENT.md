# Deploy Quant Desk 2.7.1

Keep your existing repository, Streamlit URL and Render worker. The dashboard entry point remains `terminal.py`. The Render worker runs independently of the dashboard browser.

## 1. Upload the extracted folder to GitHub

In Windows PowerShell, set `$src` to the folder containing the **new** `terminal.py` and set `$repo` to your existing Git checkout. The paths below use your previous checkout location and an example Downloads path.

```powershell
$src = "$env:USERPROFILE\Downloads\Quant_Desk_V2_7_1_Learn_Guide"
$repo = "E:\Webull autotrade\github\quant_options_agent"
Set-Location -LiteralPath $repo
git remote -v
git status --short --branch
```

Check that `origin` is your `lamvu0888-lab/quant-desk-v2` repository, your branch is `main`, and the working tree is clean. Commit or set aside any edits you want to keep before continuing. Pull first because the monitor can add commits to the same branch.

```powershell
git pull --rebase origin main
```

After the pull succeeds, copy the contents of the extracted folder into the repo root. The copy command includes `.streamlit/config.toml` and leaves your local credentials, Git metadata and existing monitor history in place.

```powershell
if (!(Test-Path -LiteralPath (Join-Path $src "terminal.py") -PathType Leaf) -or
    !(Test-Path -LiteralPath (Join-Path $src "decision_engine.py") -PathType Leaf)) {
    throw "Set src to the extracted V2.7.1 folder containing terminal.py and decision_engine.py."
}
robocopy $src $repo /E /R:2 /W:2 /XD ".git" ".venv" "__pycache__" ".pytest_cache" "monitor_data" /XF ".git" ".env" "secrets.toml" "*.sqlite3*"
if ($LASTEXITCODE -ge 8) { throw "Folder copy failed. Check the Robocopy output." }
git add -A
git diff --cached --name-status
```

Confirm that the staged list includes `webull_feed.py`, `option_quotes.py`, `discord_monitor.py`, `terminal.py` and `render.yaml`. Keep `.env`, `.streamlit/secrets.toml`, webhook URLs, and token values out of Git.

```powershell
git commit -m "Upgrade Quant Desk to V2.7.1"
git push origin main
git log -1 --format="%h %s"
```

If another worker commit lands before your push, rerun `git pull --rebase origin main` and push again after the rebase succeeds. If Git reports a conflict, inspect `git status` and resolve it before pushing. Use the deployment commit SHA to identify the build in GitHub and Render.

## 2. Update the existing Streamlit app

Use the existing app connected to branch `main` and `terminal.py`. After the repository update, look for **Options intelligence · 2.7.1** in the sidebar. If the app keeps displaying the old version, verify its connected repository, branch and main file in the Community Cloud workspace. Check its logs before restarting it.

Keep your current **App settings → Secrets** values. Streamlit expects TOML. The following block shows the format. Replace the placeholder values only in the Streamlit settings.

```toml
TRADYTICS_API_KEY = "your_existing_tradytics_key"
TRADYTICS_AUTH_MODE = "query"
DISCORD_WEBHOOK_URL = "your_existing_discord_webhook"
MONITOR_GITHUB_REPO = "lamvu0888-lab/quant-desk-v2"
MONITOR_GITHUB_BRANCH = "main"
MONITOR_GITHUB_TOKEN = "your_existing_repository_scoped_token"
```

Add your existing `WEBULL_APP_KEY` and `WEBULL_APP_SECRET` to Streamlit Secrets. Leave `WEBULL_ENDPOINT` at its production default, `api.webull.com`, for live signals. Select **Feed controls → Price and candle source → Auto** to route stocks and ETFs to Webull and indices to Tradytics.

For a new Community Cloud install, select the same repository, `main` and `terminal.py`, choose Python 3.12 where available, and add the same secrets in Advanced settings. [Streamlit secrets documentation](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

## 3. Update the Render background worker

Open your existing **quant-desk-discord-worker**. Deploy the latest commit from the same repository and branch, or let its current auto-deploy setting pick it up. The worker settings should match this table.

| Setting | Value |
| --- | --- |
| Type | Background Worker |
| Runtime | Python |
| Branch | `main` |
| Build command | `pip install -r requirements.txt` |
| Start command | `python discord_monitor.py --interval 30` |
| Ignored build paths | `monitor_data/**` |

Keep the same Tradytics, Discord and shared GitHub settings shown above. Enter each as a Render environment variable with its value. The GitHub token needs repository-scoped **Contents read and write** access for watchlist edits and log commits. The included Blueprint supports a fresh worker and includes the ignored build path. If you configured the existing worker manually, add that build filter in its settings so the worker's own history commits do not trigger redeployments. [Render background workers](https://render.com/docs/background-workers) · [Render Blueprint reference](https://render.com/docs/blueprint-spec)

Add the same `WEBULL_APP_KEY` and `WEBULL_APP_SECRET` to the Render worker environment. `MONITOR_PRICE_SOURCE=Auto` selects Webull for covered stocks and ETFs when configured and Tradytics for indices. Dashboard secrets are separate from Render environment variables. Check authorization in the Webull OpenAPI console if either host reports a token or entitlement error.

The default watchlist is **SPY, SPX**. Set your preferred symbols, the enable switch and routine scan interval in **Feed controls → Discord ticker monitoring**. Completed price and flow updates can trigger earlier checks. Provider delays and request throttling affect timing. Without shared GitHub settings the worker uses the default list. An unreachable configured watchlist pauses alerts until it returns.

The worker writes watchlist settings, delivered signal history and a bounded research snapshot to the configured GitHub branch when using its SQLite journal. These files contain market and setup data. Their visibility follows the repository's visibility.

### Journal storage

- **Existing GitHub setup.** The worker keeps detailed observations in SQLite. The dashboard can read its latest bounded research snapshot through GitHub, usually refreshed within 15 minutes during observations. The delivered Discord history keeps its existing 500-event limit. The snapshot is a recent view of the journal and does not restore the worker's full database.
- **Persistent SQLite.** For one Render worker with a persistent disk, set `MONITOR_JOURNAL_PATH` to a file on that disk, for example `/var/data/quant_desk.sqlite3` if the disk is mounted at `/var/data`. This keeps research data and delivery claims through restarts. The dashboard still reads the bounded GitHub snapshot.
- **Shared PostgreSQL.** For longer validation history and shared delivery claims, set the same `MONITOR_JOURNAL_DSN` in Render and Streamlit. Both services need network access and the database role needs permission to create and update the Quant Desk tables. The watchlist and delivered Discord history still use GitHub. A fresh SQLite journal can lose older observations on an ephemeral service restart.

## 4. Verify the deployment

1. Open **Live → Data Health** during regular U.S. market hours. Check **SPY** and another stock or ETF for Webull quote and candle times. Check **SPX** for Tradytics index price and Webull SPXW contracts. Choose an exact expiry to see options coverage.
2. Set the dashboard expiration to **Nearest listed** to compare its plan with the worker. Review the price and flow timestamps in **Movement Detector**. **Trade Plan** shows contract readiness and the latest worker alerts.
3. Click **Feed controls → Send Discord test message** once and check the channel. This checks the dashboard webhook. Check Render's logs for worker scans and any data or delivery errors.
4. Open **Monte Carlo → Trade probability**, select a usable plan and click **Evaluate trade plan**. For quoted option P&L, use an exact expiry with current quotes. In **Validation**, look for the prospective sample count.

For a local check, double-click `START_QUANT_DASHBOARD.cmd` and open `http://localhost:8501`. An existing `.env` is used by the launcher. To inspect worker feed readiness without posting or writing history, run the following from a configured environment. The first pass can still be warming up, and premarket checks show a closed-session state.

```powershell
.\.venv\Scripts\python.exe discord_monitor.py --dry-run --once
```

The one-time check lets the cache warm briefly and then prints source times, flow status and Webull options coverage for the configured watchlist. To run the automated checks locally after dependencies install, use `.\.venv\Scripts\python.exe -m pytest -q`. See [release verification](VALIDATION.md) for tested and unverified paths.
