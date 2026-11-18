"""Authorize on the owner's computer and check only cash SPX market data."""
import argparse
from datetime import datetime, timezone
from getpass import getpass
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from dotenv import dotenv_values, set_key

from market_session import ET, active_session, rth_bounds
from schwab_auth import SchwabTokens
from schwab_feed import MINUTE, SchwabMarketData, quote_status
from terminal_data import time_or_none
from tradytics_feed import Feed, FeedError

ENV = Path(__file__).resolve().parent / ".env"
AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"


def callback_parts(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None
                or parts.fragment or not 0 < (parts.port or 443) < 65536):
            raise ValueError()
    except ValueError:
        raise ValueError("Enter the registered HTTPS callback address from your Schwab app settings.") from None
    return (parts.hostname, parts.port or 443, parts.path or "/"), parse_qs(parts.query, keep_blank_values=True)


def callback_code(redirect, registered, state):
    expected, fixed = callback_parts(registered)
    actual, params = callback_parts(redirect)
    if actual != expected or any(params.get(k) != v for k, v in fixed.items()):
        raise ValueError("The redirect address differs from the registered callback.")
    states, codes = params.get("state", []), params.get("code", [])
    if len(states) != 1 or not secrets.compare_digest(states[0].encode(), state.encode()):
        raise ValueError("The callback belongs to a different authorization attempt. Restart authorization.")
    if params.get("error") or len(codes) != 1 or not codes[0]:
        raise ValueError("Schwab authorization did not return a code.")
    return codes[0]


def authorize(config, local):
    if not sys.stdin.isatty():
        raise ValueError("Open the authorization helper in a private terminal on your own computer.")
    print("Schwab sign-in for cash SPX market data. Values entered here go in this folder's private .env.")
    def save(key, value):
        if not value or any(ch in value for ch in "\r\n\0"):
            raise ValueError("A required setting is missing or invalid.")
        os.close(os.open(ENV, os.O_CREAT | os.O_WRONLY, 0o600))
        if os.name == "posix":
            ENV.chmod(0o600)
        set_key(str(ENV), key, value)
        local[key] = value
    for key, label in (("SCHWAB_CLIENT_ID", "App Key / Client ID"), ("SCHWAB_CLIENT_SECRET", "App Secret")):
        if not config(key):
            save(key, getpass(label + " (hidden): ").strip())
    callback = config("SCHWAB_CALLBACK_URL")
    if not callback:
        callback = input("Registered HTTPS callback URL [https://127.0.0.1:8182]: ").strip() or "https://127.0.0.1:8182"
    _, params = callback_parts(callback)
    if set(params) & {"state", "code", "error"}:
        raise ValueError("Use the registered callback URL before login, without an authorization response.")
    if not config("SCHWAB_CALLBACK_URL"):
        save("SCHWAB_CALLBACK_URL", callback)
    if not config("SCHWAB_TOKEN_DSN") and not config("SCHWAB_TOKEN_PATH"):
        value = getpass("Hosted dashboard: paste its external PostgreSQL URL (hidden). This PC only: press Enter. ").strip()
        if value:
            save("SCHWAB_TOKEN_DSN", value)
    tokens = SchwabTokens.from_config(config)
    tokens.prepare()
    print("Authorization destination:", "shared PostgreSQL" if config("SCHWAB_TOKEN_DSN") else "local encrypted token store")
    state = secrets.token_urlsafe(32)
    url = AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": tokens.client_id,
                                      "redirect_uri": callback, "state": state})
    print("Sign in at Schwab and approve your app. At the redirect, copy the entire browser address.")
    print("For a local callback, the browser may show a connection error. The address bar still contains the response.")
    if not webbrowser.open(url):
        print("Open this link in your private browser on this computer:", url)
    redirect = getpass("Paste the full redirect URL here (hidden): ").strip()
    tokens.authorize(callback_code(redirect, callback, state), callback)
    print("Authorization saved. Checking cash SPX quotes and completed one-minute candles.")
    return tokens


def check(tokens):
    if not tokens:
        raise ValueError("Set the Schwab app credentials with AUTHORIZE_SCHWAB_SPX.cmd first.")
    client = SchwabMarketData(tokens)
    result = {}
    for key in ("quote", "history"):
        try:
            result[key] = Feed(client.get(key))
        except FeedError as exc:
            result[key] = Feed(error=exc)
    now = datetime.now(timezone.utc)
    session = active_session(now)
    opened, closed = rth_bounds(session)
    first_minute = opened <= now < opened + MINUTE
    quote, history = result["quote"], result["history"]
    at = time_or_none(quote.obj.get("price_at"))
    bars = history.obj.get("candles", [])
    last = time_or_none(bars[-1]["time_utc"]) + MINUTE if bars else None
    state = quote_status(quote, now)
    if first_minute and (not history.error or history.error.status == "NO CANDLES"):
        bars_state = "WAITING FOR FIRST COMPLETED MINUTE"
    elif history.error:
        bars_state = history.error.status
    elif opened <= now < closed:
        bars_state = "FRESH" if last and last > opened and 0 <= (now-last).total_seconds() <= 120 else "AGED OR MISSING"
    else:
        bars_state = "SESSION CLOSE" if last == closed else "LAST SESSION CLOSE UNVERIFIED"
    print(json.dumps({"checked_et": now.astimezone(ET).isoformat(timespec="seconds"), "symbol": "$SPX",
                      "session": str(session), "regular_session_open": opened <= now < closed,
                      "quote": {"status": state, "realtime": quote.obj.get("realtime"), "price": quote.obj.get("price"),
                                "time_et": at.astimezone(ET).isoformat() if at else None, "note": str(quote.error or "")},
                      "minute_candles": {"status": bars_state, "count_this_session": sum(
                          time_or_none(r["time_utc"]).astimezone(ET).date() == session for r in bars),
                          "last_close_et": last.astimezone(ET).isoformat() if last else None,
                          "note": str(history.error or "")}}, indent=2))
    return 0 if state in ("FRESH QUOTE", "SESSION CLOSE") and bars_state in (
        "FRESH", "SESSION CLOSE", "WAITING FOR FIRST COMPLETED MINUTE") else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("authorize", "check"))
    args = parser.parse_args()
    local = dotenv_values(ENV)
    def config(key):
        return (os.getenv(key) or local.get(key) or "").strip()
    tokens = authorize(config, local) if args.command == "authorize" else SchwabTokens.from_config(config)
    return check(tokens)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, FeedError) as exc:
        sys.exit(str(exc))
    except (KeyboardInterrupt, EOFError):
        sys.exit("Authorization interrupted.")
    except Exception as exc:
        sys.exit(f"Schwab check stopped ({type(exc).__name__}). Check the private settings.")
