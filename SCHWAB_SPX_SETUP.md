# Connect Schwab to Quant Desk

Quant Desk 2.8.7 reads **cash SPX** from Schwab Market Data Production. The local check reports what your own account returns for the index quote and completed one-minute candles. SPXW option quotes still use the dashboard's existing option providers.

## 1. Choose where to run it

For personal use, start on your Windows PC or use a Streamlit app accessible only to you. Schwab's personal market-data terms require written agreements with the relevant providers for redistribution. This also matters for any shared journal, GitHub signal history, or Discord destination that receives Schwab prices. [Schwab Brokerage Account Agreement, section 24](https://www.schwab.com/legal/schwab-brokerage-account-agreement)

- **Local dashboard.** Extract the ZIP to a private folder. Install Python 3.12 if needed. The helper can keep authorization on this PC.
- **Hosted dashboard.** Use a private PostgreSQL database accessible from your PC and Streamlit. You can use your existing hosted journal database if you control it. Have its **external PostgreSQL connection URL** available. Use the provider's encrypted connection setting, such as `sslmode=require` for a Render external URL. The helper creates one table named `qd_schwab_oauth`. It stores encrypted tokens and coordinates renewals between processes. The initial connection needs permission to create the table. Hosted readers need SELECT, INSERT and UPDATE access to that table.

## 2. Authorize and test on your PC

1. In the extracted folder, double-click **AUTHORIZE_SCHWAB_SPX.cmd**. The first run installs the app dependencies.
2. Enter the App Key (client ID), App Secret and the **exact registered HTTPS callback URL** from the approved Schwab app. Enter the external database URL for a hosted dashboard. Press Enter at the database prompt for a PC-only setup.
3. Sign in on Schwab's website and approve access. When Schwab redirects your browser, copy the full address from the address bar and paste it into the helper's hidden prompt. A loopback callback may show a connection error after the redirect. The address bar still contains the code. Keep that address, your secrets and your database URL private.
4. Read the two results. During the regular session, **FRESH QUOTE** with `realtime: true` verifies the quoted index level. **FRESH** under `minute_candles` verifies recent completed candles. The report includes both market timestamps in ET. At the open, the first 9:30–9:31 candle can only appear after 9:31 ET. Outside the session, **SESSION CLOSE** describes the latest regular close. A fresh quote by itself leaves minute-bar access unverified.

To repeat the read-only check, open a terminal in the same folder and run `py -3 launch.py --schwab-check`. On macOS or Linux, use `python3 launch.py --schwab-authorize` and `python3 launch.py --schwab-check`.

The helper saves private configuration in the local `.env`. With local-only setup, the encrypted token file defaults to `monitor_data/runtime/schwab-oauth.sqlite3`. Keep the folder on a computer you control. For a local run, start **START_QUANT_DASHBOARD.cmd**, then open **Live → SPX → Data Health**.

## 3. Publish to your existing Streamlit app

1. From the release ZIP, double-click **PUBLISH_SCHWAB_UPDATE.cmd** on a PC with Git for Windows and write access to `lamvu0888-lab/quant-desk-v2`. It applies the bundled patch to the latest `main` in a separate checkout, pushes a feature branch using your GitHub sign-in, and opens the pull request page. Review and merge the branch to deploy. Your current checkout and the worker's market history are preserved.
2. In your **private** Streamlit app, open **Settings → Secrets**. Add the three entries below to the existing TOML. Use values matching the app and database used in step 2. The callback is needed only on the machine running authorization.

```toml
SCHWAB_CLIENT_ID = "<your approved app key>"
SCHWAB_CLIENT_SECRET = "<your app secret>"
SCHWAB_TOKEN_DSN = "<external PostgreSQL connection URL>"
```

3. Check the sidebar for **Options intelligence · 2.8.7**. Open **Live → SPX → Feed controls → Price and candle source → Schwab**, then open **Data Health**. Check the quote time, real-time status and latest completed minute. Switch the source to **Auto** for normal use. A selected **Schwab** source keeps its own errors visible.
4. If the monitoring and paper workers should also read Schwab prices, add the same three settings to their private Render **Environment**. Use a database address reachable by that service. Both workers must reach the same database as the authorization helper. Keep `MONITOR_PRICE_SOURCE=Auto` on the monitor. It records the selected provider with each signal. The paper worker uses the signal's recorded source for the underlying. The paper dashboard that only displays the journal needs no Schwab keys.

## What Data Health means

| Indication | What to check |
| --- | --- |
| FRESH QUOTE | Schwab identified cash `$SPX` as an index, reported real-time access, and supplied a market timestamp within 30 seconds. |
| Latest completed regular-session minute | A candle is eligible after its full minute has elapsed. During the regular session a minute close older than 120 seconds cannot confirm a new signal. |
| DELAYED or ENTITLEMENT UNKNOWN | Check your app's Market Data Production product and index data entitlement with Schwab. |
| NO CANDLES or stale candles | Check the separate minute-bar result even if the quote works. Auto tries a usable cash SPX quote and candles from Webull, then Tradytics. |
| AUTH REQUIRED | Run the authorization helper again on your PC using the same database and app settings. Schwab sign-in generally needs renewal about once a week. [Token lifetime](https://schwab-py.readthedocs.io/en/stable/auth.html#notes-on-token-expiration) |
| TOKEN STORE | Check the private database address, TLS, network access and table permissions from the affected host. |

The cash index chart omits share volume and VWAP. Existing ES/MES comparisons measure futures activity separately. Options flow and dealer exposure continue to use their existing providers.
