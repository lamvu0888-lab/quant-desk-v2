"""Read-only, fail-closed Quant Desk monitoring service; never submits orders."""
from __future__ import annotations
import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from dotenv import dotenv_values
from decision_engine import ENTRY_STATES
from desk_analytics import FIVE_MINUTES
from market_session import active_session, session_state
from movement_detector import ENTRY_COOLDOWN
from terminal_views import Desk, read_price, read_workspace
from tradytics_feed import TradyticsClient, FeedStore
from monitor_storage import SharedStore
from signal_explain import discord_text
from decision_flow import build_decision, price_quote_issues
from monitor_engine import price_move_watch, process_scan
from research_journal import Journal
from research_snapshot import publish_snapshot
from settings import Settings
from terminal_data import completed_boundary, first_listed_dte, source_stamp, time_or_none
from webull_feed import WebullFeedStore, price_source

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
FAST_SCAN_SECONDS = 2


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


def deliver(journal, shared, webhook, alert_id, alert, *, deadline=None):
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
    if deadline and datetime.now(UTC) >= deadline:
        journal.delivery_state(alert_id, "expired")
        return
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
                      "regime", "validation", "outcome", "aligned_crossings", "rule_version", "source", "dte",
                      "direction", "window", "change_pct", "previous_price", "option_quote")
            event = {key: alert[key] for key in fields if key in alert}
            shared.append_event({**event, "id": alert_id, "explanation": str(alert.get("explanation", ""))[:220]})
        except Exception:
            print(f"{alert['symbol']}: delivered; shared history could not save. Local journal retained.", flush=True)
    print(json.dumps({"sent": alert["status"], "symbol": alert["symbol"], "at": alert["triggered_at_utc"]}), flush=True)


def monitor_dte(store, symbol, session, now):
    expirations = store.get("expirations", symbol=symbol)
    return first_listed_dte(expirations, symbol, session, now) or 0


class MonitorScan:
    def __init__(self, store, webull, preference, journal):
        self.store, self.webull, self.preference, self.journal = store, webull, preference, journal
        self.scans = {}
        self.move_closes = {}

    def run(self, symbols, now, interval, *, dry_run=False):
        session = session_state(now)
        market_open = session["status"] == "OPEN"
        close = time_or_none(session["close_utc"])
        closeout = close and close <= now <= close + timedelta(minutes=20)
        if not (market_open or closeout or dry_run):
            return [], []
        day = active_session(now)
        sources = {symbol: price_source(symbol, self.preference, self.webull) for symbol in symbols}
        snapshots = self.webull.prices([symbol for symbol, source in sources.items() if source == "Webull"]) if self.webull else {}
        alerts, diagnostics = [], []
        for symbol in symbols:
            try:
                dte = monitor_dte(self.store, symbol, day, now)
                ctx = Desk(symbol, now, self.store, False, False, dte, True, sources[symbol],
                           webull_store=self.webull, webull_prices=snapshots.get(symbol))
                prices = read_price(ctx)
                minute_close = prices["minute_close"]
                if market_open and not dry_run and prices["bars_ready"] and self.move_closes.get(symbol) != minute_close:
                    watch = price_move_watch(self.journal, ctx, prices)
                    if watch:
                        alerts.append(watch)
                    self.move_closes[symbol] = minute_close

                timeline = ctx.timeline() if market_open else None
                flow_close = source_stamp("timeline", timeline, symbol) if timeline else None
                quote = ctx.quote()
                flow_key = tuple((row.get("time"), row.get("net_premium_usd")) for row in timeline.rows[-2:]) if timeline else ()
                state = (day, dte, sources[symbol], prices["bars_ready"], minute_close,
                         completed_boundary(now, day, FIVE_MINUTES), flow_key,
                         not price_quote_issues(ctx, quote), bool(timeline and timeline.error))
                before, due = self.scans.get(symbol, (None, now))
                if not dry_run and state == before and now < due:
                    continue
                read = read_workspace(ctx, include_flow=market_open, prices=prices, timeline=timeline)
                if dry_run:
                    plan, _ = build_decision(ctx, read)
                    chain = ctx.option_chain(ctx.expiry, provider="Webull")[0] if ctx.has_webull_options and ctx.expiry else None
                    diagnostics.append({"symbol": symbol, "price_source": sources[symbol], "expiration": ctx.expiry,
                                        "quote_at": plan["quote_at"], "candle_at": minute_close.isoformat() if minute_close else None,
                                        "flow_at": flow_close.isoformat() if isinstance(flow_close, datetime) else None,
                                        "flow_status": read["flow_state"], "status": plan["status"],
                                        "webull_contracts": len(chain.rows) if chain is not None else None,
                                        "webull_option_error": str(chain.error) if chain and chain.error else None,
                                        "option_check": (plan.get("option_quote") or {}).get("status"),
                                        "waiting": plan["data_reasons"]})
                    continue
                candidates, plan = process_scan(self.journal, ctx, read, check_move=False)
                alerts.extend(candidates)
                delay = min(interval, 5) if plan["status"] == "GET READY" else interval
                self.scans[symbol] = state, now + timedelta(seconds=delay)
                if candidates or before is None:
                    print(f"{now.isoformat()} {symbol}: {plan['status']} · {sources[symbol]} · {len(candidates)} setup or exit alerts", flush=True)
            except Exception as exc:
                print(f"{symbol}: monitor scan error ({type(exc).__name__}).", flush=True)
        for removed in self.scans.keys() - set(symbols):
            self.scans.pop(removed)
            self.move_closes.pop(removed, None)
        return alerts, diagnostics


def alert_deadline(alert, now):
    if alert["status"] == "MOVE WATCH":
        return (time_or_none(alert.get("bar")) or now) + timedelta(seconds=75)
    return now + timedelta(seconds=45 if alert["status"] in ENTRY_STATES else 180)


def _send_pending(journal, shared, webhook, alert_id, alert, deadline):
    try:
        deliver(journal, shared, webhook, alert_id, alert, deadline=deadline)
    except WebhookError as exc:
        print(f"{alert['symbol']}: {exc}", flush=True)
        return exc.retry_after
    except Exception as exc:
        print(f"{alert['symbol']}: delivery error ({type(exc).__name__}).", flush=True)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=30, help="Routine scan interval. New completed price and flow can trigger an earlier scan.")
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
    preference = secret("MONITOR_PRICE_SOURCE", "Auto").strip().lower()
    preference = {"auto": "Auto", "webull": "Webull", "tradytics": "Tradytics"}.get(preference)
    if not preference:
        raise SystemExit("Set MONITOR_PRICE_SOURCE to Auto, Webull or Tradytics.")
    app_key, app_secret = secret("WEBULL_APP_KEY"), secret("WEBULL_APP_SECRET")
    if preference == "Webull" and not (app_key and app_secret):
        raise SystemExit("Set WEBULL_APP_KEY and WEBULL_APP_SECRET for Webull price monitoring.")
    webull = (WebullFeedStore(Settings.load(secret), app_key, app_secret, secret("WEBULL_REGION", "us"))
              if app_key and app_secret else None)
    store = FeedStore(TradyticsClient(key, secret("TRADYTICS_AUTH_MODE", "query")))
    shared = SharedStore(repo=secret("MONITOR_GITHUB_REPO"), token=secret("MONITOR_GITHUB_TOKEN"),
                         branch=secret("MONITOR_GITHUB_BRANCH", "main"))
    if bool(shared.repo) != bool(shared.token):
        raise SystemExit("Set both shared GitHub settings, or leave both empty for the default watchlist.")
    journal = None if args.dry_run else Journal(path=secret("MONITOR_JOURNAL_PATH"), dsn=secret("MONITOR_JOURNAL_DSN"))
    scanner = MonitorScan(store, webull, preference, journal)
    print(f"Quant Desk 2.7.0 worker · price source {preference} · Webull {'configured' if webull else 'unconfigured'}", flush=True)
    retry = {}
    resume_sends = 0.
    next_snapshot = 0.
    next_watchlist = 0.
    watchlist = {"enabled": False, "symbols": [], "interval_seconds": args.interval}
    config_read = snapshot_write = delivery = None
    io = ThreadPoolExecutor(max_workers=3, thread_name_prefix="monitor-io")
    once_until = time.monotonic() + 10
    try:
        while True:
            started = time.monotonic()
            if config_read is None and started >= next_watchlist:
                config_read = io.submit(shared.watchlist)
            if config_read is not None and config_read.done():
                try:
                    watchlist, _ = config_read.result()
                except Exception as exc:
                    print(f"Shared watchlist unavailable ({type(exc).__name__}). Alerts paused.", flush=True)
                    watchlist = {"enabled": False, "symbols": [], "interval_seconds": args.interval}
                config_read = None
                next_watchlist = started + 30
            interval = max(args.interval, watchlist["interval_seconds"])
            now = datetime.now(UTC)
            symbols = watchlist["symbols"] if watchlist["enabled"] else []
            alerts, diagnostics = scanner.run(symbols, now, interval, dry_run=args.dry_run)
            for alert_id, alert in alerts:
                retry.setdefault(alert_id, (alert, alert_deadline(alert, now)))
            if delivery and delivery[1].done():
                sent_id, result = delivery
                delay = result.result()
                if delay is None:
                    retry.pop(sent_id, None)
                else:
                    resume_sends = time.monotonic() + delay
                delivery = None
            for alert_id, (alert, deadline) in list(retry.items()):
                if deadline <= now or alert["symbol"] not in symbols:
                    retry.pop(alert_id)
            if retry and delivery is None and started >= resume_sends:
                alert_id, (alert, deadline) = next(iter(retry.items()))
                delivery = alert_id, io.submit(_send_pending, journal, shared, webhook, alert_id, alert, deadline)

            if snapshot_write and snapshot_write.done():
                try:
                    snapshot_write.result()
                except Exception as exc:
                    print(f"Research snapshot could not sync ({type(exc).__name__}). Worker journal retained.", flush=True)
                snapshot_write = None
            if (symbols and not args.dry_run and shared.configured and not journal.dsn and snapshot_write is None
                    and started >= next_snapshot and session_state(now)["status"] == "OPEN"):
                next_snapshot = started + 15 * 60
                snapshot_write = io.submit(publish_snapshot, journal, shared, symbols, now)

            if args.dry_run and (not args.once or started >= once_until):
                for row in diagnostics:
                    print(json.dumps({"dry_run": True, **row}), flush=True)
            if args.once and started >= once_until and config_read is None and delivery is None:
                break
            time.sleep(max(0., FAST_SCAN_SECONDS - (time.monotonic() - started)))
    finally:
        io.shutdown(wait=False, cancel_futures=True)
        store.close()
        if webull:
            webull.close()


if __name__ == "__main__":
    main()
