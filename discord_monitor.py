"""Read-only, fail-closed Quant Desk monitoring service; never submits orders."""
from __future__ import annotations
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import dotenv_values
from market_session import session_state
from terminal_data import numeric
from terminal_views import Desk, read_workspace
from tradytics_feed import TradyticsClient, FeedStore
from monitor_storage import SharedStore
from signal_explain import describe, explain_evidence, discord_text

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc


def evaluate(ctx):
    """Only send an entry after the existing Trade Plan says ready and feeds pass gates."""
    if session_state(ctx.now)["status"] != "OPEN":
        return None
    data = read_workspace(ctx)
    plan = data["plan"]
    if data["reasons"] or plan.get("score") is None:
        return None
    state = str(plan.get("state") or "").upper()
    bias = str(plan.get("bias") or "mixed").upper()
    score = numeric(plan.get("score"))
    if score is None:
        return None
    if "READY" in state and "NOT" not in state and "UN" not in state:
        status = "ENTER SETUP"
    elif score >= 65 and ("WAIT" in state or "WATCH" in state):
        status = "GET READY"
    else:
        return None
    if bias not in ("BULLISH", "BEARISH"):
        return None
    stamp = data["price_stamp"]
    return {"symbol":ctx.ticker, "status":status, "bias":bias, "score":round(score),
            "bar":stamp.isoformat() if stamp else None, "state":state,
            "price":numeric(ctx.quote().get("price")), "scope":"0DTE",
            "trigger":numeric(plan.get("trigger")), "invalidation":numeric(plan.get("invalidation")),
            "evidence":explain_evidence(plan), "triggered_at_utc":ctx.now.isoformat(),
            "explanation":describe(plan, status),
            "notes":"Informational only. No order was sent."}


def send(webhook, signal):
    if not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith("https://discordapp.com/api/webhooks/"):
        raise ValueError("Discord webhook must be a Discord HTTPS webhook URL")
    content = discord_text(signal)
    response = requests.post(webhook, json={"content":content, "allowed_mentions":{"parse":[]}}, timeout=12)
    response.raise_for_status()


def send_test(webhook):
    """Send a harmless connectivity test; never evaluates a trading signal."""
    if not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith("https://discordapp.com/api/webhooks/"):
        raise ValueError("Discord webhook must be a Discord HTTPS webhook URL")
    response = requests.post(
        webhook,
        json={"content": "✅ Quant Desk Discord test successful. Alerts are connected.",
              "allowed_mentions": {"parse": []}},
        timeout=12,
    )
    response.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    local = dotenv_values(ROOT / ".env")
    def secret(k, default=""):
        return os.getenv(k) or local.get(k) or default
    key, webhook = secret("TRADYTICS_API_KEY"), secret("DISCORD_WEBHOOK_URL")
    if not key or not webhook:
        raise SystemExit("Set TRADYTICS_API_KEY and DISCORD_WEBHOOK_URL in environment or .env")
    if args.interval < 10:
        raise SystemExit("Minimum interval is 10 seconds; endpoint caching/rate limits still apply")
    store = FeedStore(TradyticsClient(key, secret("TRADYTICS_AUTH_MODE", "query")))
    shared = SharedStore(repo=secret("MONITOR_GITHUB_REPO"), token=secret("MONITOR_GITHUB_TOKEN"),
                         branch=secret("MONITOR_GITHUB_BRANCH", "main"))
    sent = {}
    next_scan = 0.0
    last_watchlist = None
    try:
        while True:
            try:
                watchlist, _ = shared.watchlist()
                last_watchlist = watchlist
            except Exception as exc:
                # Do not silently revert to hardcoded tickers after a remote error.
                print(f"Shared watchlist unavailable: {type(exc).__name__}: {exc}", flush=True)
                if shared.configured:
                    if args.once:
                        break
                    time.sleep(args.interval)
                    continue
                watchlist = last_watchlist or {'symbols': ['SPY', 'SPX'], 'enabled': True, 'interval_seconds': 30}
            if not watchlist['enabled']:
                print('Discord monitoring paused in shared settings', flush=True)
                if args.once:
                    break
                time.sleep(args.interval)
                continue
            for symbol in watchlist['symbols']:
                try:
                    now = datetime.now(UTC)
                    ctx = Desk(symbol, now, store, False, False, 0, True)
                    result = evaluate(ctx)
                    if result:
                        signature = (result["status"], result["bias"], result["bar"])
                        if sent.get(symbol) != signature:
                            event = {**result, 'id': f"{symbol}:{result['status']}:{result['bias']}:{result['bar']}"}
                            if shared.configured:
                                previous, _ = shared.history()
                                if any(row.get('id') == event['id'] for row in previous):
                                    sent[symbol] = signature
                                    continue
                            send(webhook, result)
                            sent[symbol] = signature
                            if shared.configured:
                                try:
                                    shared.append_event(event)
                                except Exception as log_exc:
                                    print(f"{symbol}: alert sent but history save failed: {log_exc}", flush=True)
                            print(json.dumps({"sent":result}), flush=True)
                    else:
                        print(f"{now.isoformat()} {symbol}: no eligible confirmed signal", flush=True)
                except Exception as exc:
                    print(f"{symbol}: monitor error ({type(exc).__name__}): {exc}", flush=True)
            if args.once:
                break
            time.sleep(max(args.interval, watchlist.get("interval_seconds", 30)))
    finally:
        store.close()


if __name__ == "__main__":
    main()
