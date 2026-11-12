"""Read-only, fail-closed Quant Desk monitoring service; never submits orders."""
from __future__ import annotations
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from dotenv import dotenv_values
from decision_engine import ENTRY_STATES
from market_session import session_state
from movement_detector import ENTRY_COOLDOWN
from terminal_views import Desk, read_workspace
from tradytics_feed import TradyticsClient, FeedStore
from monitor_storage import SharedStore
from signal_explain import discord_text
from decision_flow import build_decision
from monitor_engine import event_id, notification, process_scan
from research_journal import Journal
from research_snapshot import publish_snapshot
from terminal_data import time_or_none

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc


class WebhookError(RuntimeError):
    def __init__(self, message, *, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def webhook_url(webhook):
    try:
        url = urlsplit(webhook)
        valid = (url.scheme == "https" and url.netloc in ("discord.com", "discordapp.com")
                 and re.fullmatch(r"/api/webhooks/[0-9]+/[A-Za-z0-9_.-]+", url.path) and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Use a Discord HTTPS webhook URL.")
    return url


def post_message(url, content):
    params = {"wait": "true"}
    thread = parse_qs(url.query).get("thread_id", [""])[0]
    if thread.isdecimal():
        params["thread_id"] = thread
    try:
        response = requests.post(url._replace(query="").geturl(), params=params,
                                 json={"content": content, "allowed_mentions": {"parse": []}},
                                 timeout=(4, 8), allow_redirects=False)
    except requests.RequestException:
        raise WebhookError("Discord did not acknowledge delivery. Check the channel before retrying.") from None
    if response.status_code == 429:
        try:
            retry = max(1., float(response.headers.get("Retry-After", 30)))
        except (ValueError, TypeError):
            retry = 30.
        raise WebhookError("Discord rate limit reached.", retry_after=retry)
    if not 200 <= response.status_code < 300:
        raise WebhookError(f"Discord returned HTTP {response.status_code}. Check the webhook and channel permissions.")


def send(webhook, signal):
    post_message(webhook_url(webhook), discord_text(signal))


def send_test(webhook):
    post_message(webhook_url(webhook), "✅ Quant Desk Discord test successful. Webhook delivery is connected.")


def data_warning(state, critical):
    """Open one warning per sustained incident and rearm after two healthy scans."""
    bad, good, warned = state
    if not critical:
        return (0, min(good + 1, 2), warned and good == 0), False
    notify = bad == 2 and not warned
    return (min(bad + 1, 3), 0, warned or notify), notify


def deliver(journal, shared, webhook, alert_id, alert):
    if alert["status"] == "DATA QUALITY":
        return
    entry_id = alert.get("entry_alert_id")
    if alert["status"] == "EXIT" and entry_id and not journal.was_delivered(entry_id):
        return
    if not journal.claim_delivery(alert_id):
        return
    if shared.configured:
        try:
            history, _ = shared.history()
            if any(isinstance(item, dict) and item.get("id") == alert_id for item in history):
                journal.delivery_state(alert_id, "sent")
                return
            alert_time = time_or_none(alert.get("triggered_at_utc"))
            if alert["status"] in ENTRY_STATES and alert_time:
                for item in history:
                    if (not isinstance(item, dict) or item.get("symbol") != alert["symbol"]
                            or item.get("setup") != alert.get("setup") or item.get("status") not in ENTRY_STATES):
                        continue
                    previous = time_or_none(item.get("triggered_at_utc"))
                    if previous and timedelta(0) <= alert_time - previous < ENTRY_COOLDOWN:
                        journal.delivery_state(alert_id, "suppressed")
                        return
        except Exception:
            print(f"{alert['symbol']}: shared alert history unavailable. Using the local delivery claim.", flush=True)
    try:
        send(webhook, alert)
    except WebhookError as exc:
        if exc.retry_after is not None:
            journal.release_throttled_delivery(alert_id)
        else:
            journal.delivery_state(alert_id, "unacknowledged")
        raise
    journal.delivery_state(alert_id, "sent")
    if shared.configured:
        try:
            fields = ("symbol", "status", "bias", "score", "bar", "entry_bar", "triggered_at_utc", "price", "scope",
                      "setup", "entry", "trigger", "invalidation", "stop", "tp1", "tp2", "rr", "rr2",
                      "regime", "validation", "outcome", "aligned_crossings", "rule_version")
            event = {key: alert[key] for key in fields if key in alert}
            shared.append_event({**event, "id": alert_id, "explanation": str(alert.get("explanation", ""))[:220]})
        except Exception:
            print(f"{alert['symbol']}: delivered; shared history could not save. Local journal retained.", flush=True)
    print(json.dumps({"sent": alert["status"], "symbol": alert["symbol"], "at": alert["triggered_at_utc"]}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Read feed status without writing history or posting to Discord.")
    args = parser.parse_args()
    local = dotenv_values(ROOT / ".env")
    def secret(k, default=""):
        return os.getenv(k) or local.get(k) or default
    key, webhook = secret("TRADYTICS_API_KEY"), secret("DISCORD_WEBHOOK_URL")
    if not key or (not webhook and not args.dry_run):
        raise SystemExit("Set TRADYTICS_API_KEY and DISCORD_WEBHOOK_URL in environment or .env")
    if args.interval < 30:
        raise SystemExit("Minimum interval is 30 seconds.")
    store = FeedStore(TradyticsClient(key, secret("TRADYTICS_AUTH_MODE", "query")))
    shared = SharedStore(repo=secret("MONITOR_GITHUB_REPO"), token=secret("MONITOR_GITHUB_TOKEN"),
                         branch=secret("MONITOR_GITHUB_BRANCH", "main"))
    if bool(shared.repo) != bool(shared.token):
        raise SystemExit("Set both shared GitHub settings, or leave both empty for the default watchlist.")
    journal = None if args.dry_run else Journal(path=secret("MONITOR_JOURNAL_PATH"), dsn=secret("MONITOR_JOURNAL_DSN"))
    retry = {}
    resume_sends = 0.
    next_snapshot = 0.
    try:
        while True:
            started = time.monotonic()
            interval = args.interval
            try:
                watchlist, _ = shared.watchlist()
                interval = max(interval, watchlist["interval_seconds"])
            except Exception as exc:
                print(f"Shared watchlist unavailable ({type(exc).__name__}). Alerts paused.", flush=True)
                watchlist = {"enabled": False, "symbols": []}
            if watchlist["enabled"]:
                observing = False
                for symbol in watchlist["symbols"]:
                    try:
                        now = datetime.now(UTC)
                        session = session_state(now)
                        closed = time_or_none(session["close_utc"])
                        closeout = closed and closed <= now <= closed + timedelta(minutes=20)
                        if session["status"] != "OPEN" and not closeout and not args.dry_run:
                            continue
                        observing = True
                        ctx = Desk(symbol, now, store, False, False, 0, True)
                        read = read_workspace(ctx, include_flow=session["status"] == "OPEN")
                        if args.dry_run:
                            plan, _ = build_decision(ctx, read)
                            print(json.dumps({"dry_run": symbol, "status": plan["status"], "waiting": plan["data_reasons"]}), flush=True)
                            continue
                        alerts, plan = process_scan(journal, ctx, read)
                        print(f"{now.isoformat()} {symbol}: scan complete ({len(alerts)} candidate alerts)", flush=True)
                        for alert_id, alert in alerts:
                            retry.setdefault(alert_id, (alert, now + timedelta(minutes=3)))
                    except Exception as exc:
                        print(f"{symbol}: monitor scan error ({type(exc).__name__}).", flush=True)
                if not args.dry_run:
                    for alert_id, (alert, deadline) in list(retry.items()):
                        if deadline < datetime.now(UTC) or alert["symbol"] not in watchlist["symbols"]:
                            retry.pop(alert_id)
                            continue
                        if time.monotonic() < resume_sends:
                            break
                        try:
                            deliver(journal, shared, webhook, alert_id, alert)
                        except WebhookError as exc:
                            print(f"{alert['symbol']}: {exc}", flush=True)
                            if exc.retry_after is not None:
                                resume_sends = time.monotonic() + exc.retry_after
                                break
                        except Exception as exc:
                            print(f"{alert['symbol']}: delivery error ({type(exc).__name__}).", flush=True)
                        retry.pop(alert_id)
                if (observing and not args.dry_run and shared.configured and not journal.dsn
                        and time.monotonic() >= next_snapshot):
                    next_snapshot = time.monotonic() + 15 * 60
                    try:
                        publish_snapshot(journal, shared, watchlist["symbols"], datetime.now(UTC))
                    except Exception as exc:
                        print(f"Research snapshot could not sync ({type(exc).__name__}). Worker journal retained.", flush=True)
            if args.once:
                break
            time.sleep(max(0., interval - (time.monotonic() - started)))
    finally:
        store.close()


if __name__ == "__main__":
    main()
