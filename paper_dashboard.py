"""Separate Streamlit app and background worker for paper trading."""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from dotenv import dotenv_values

from market_session import ET, active_session, rth_bounds, session_state
from paper_engine import PaperLedger, PaperWorker, clock, option_pnl, realized, stamp, unrealized
from paper_review import day_report
from research_journal import Journal
from settings import Settings
from terminal_data import time_or_none
from tradytics_feed import FeedStore, TradyticsClient
from webull_feed import WebullFeedStore
from schwab_feed import feed_store as make_schwab_store

ROOT = Path(__file__).resolve().parent
LOCAL = dotenv_values(ROOT / ".env")


def config(name, default=""):
    return os.getenv(name) or LOCAL.get(name) or default


def run_worker(once=False):
    key = config("TRADYTICS_API_KEY")
    app_key, secret = config("WEBULL_APP_KEY"), config("WEBULL_APP_SECRET")
    settings = Settings.load(config)
    if not all((key, app_key, secret)) or settings.endpoint != "api.webull.com":
        raise SystemExit("Set Tradytics and production Webull market-data keys. No brokerage orders are used.")
    journal = Journal(path=config("MONITOR_JOURNAL_PATH"), dsn=config("MONITOR_JOURNAL_DSN"))
    ledger = PaperLedger(journal)
    webull = WebullFeedStore(settings, app_key, secret, config("WEBULL_REGION", "us"))
    tradytics = FeedStore(TradyticsClient(key, config("TRADYTICS_AUTH_MODE", "query")))
    schwab = make_schwab_store(config)
    worker = PaperWorker(ledger, webull, tradytics, schwab=schwab)
    last_review = None
    try:
        with ledger.lock() as assert_lock:
            ledger.start(clock())
            print("Paper worker connected to the Trade Plan journal.", flush=True)
            while True:
                assert_lock()
                try:
                    worker.poll(assert_lock)
                    now = clock()
                    session = active_session(now)
                    opened, closed = rth_bounds(session)
                    if now < closed:
                        session = active_session(opened - timedelta(seconds=1))
                    if last_review != session:
                        with journal.connection() as db:
                            review_key = "review:" + str(session)
                            if ledger.meta(db, review_key) is None:
                                ledger.set_meta(db, review_key, day_report(ledger.rows(db), session))
                        last_review = session
                except Exception as exc:
                    print(f"Paper poll failed ({type(exc).__name__}).", flush=True)
                    if once:
                        raise
                if once:
                    return
                time.sleep(5 if session_state()["status"] == "OPEN" else 15)
    finally:
        webull.close()
        if schwab:
            schwab.close()
        tradytics.close()


def demo():
    day = date(2026, 11, 13)
    opened, _ = rth_bounds(day)
    now = opened + timedelta(hours=1)
    rows = []
    for index in range(6):
        ticker = "SPY" if index % 2 == 0 else "SPX"
        spot = 675 if ticker == "SPY" else 6750
        at = now - timedelta(minutes=5 + index * 8)
        fast = ticker == "SPY"
        trade = {"id": f"demo-{index}", "session": str(day), "symbol": ticker, "origin": stamp(at),
                 "opened": stamp(at), "state": "OPEN" if index == 0 else "CLOSED",
                 "qty": 2, "remaining": 2 if index == 0 else 0, "fills": [], "entry_ask": 2.40,
                 "mark_bid": 2.72, "mark_at": stamp(now), "fast": fast, "dte": 0 if fast else 3,
                 "expiry": "2026-11-13" if fast else "2026-11-16", "strike": spot + (1 if fast else 0),
                 "cost": 481.30, "spread": .08, "spot": spot,
                 "contract": "SPY261113C00676000" if fast else "SPXW261116C06750000",
                 "plan": {"setup": "EMA pullback" if fast else "Range break",
                          "score": 76 + index * 2, "direction": 1, "rr": 1.5,
                          "stop": spot - 2, "tp1": spot + 3, "tp2": spot + 5},
                 "reason": "Tracking Trade Plan" if index == 0 else "TP2" if index % 3 else "STOP"}
        if index:
            closed = at + timedelta(minutes=3)
            bid = 2.95 + index / 10 if index % 3 else 1.80
            trade["closed"] = stamp(closed)
            trade["fills"] = [{"at": stamp(closed), "bid": bid, "qty": 2,
                               "pnl": option_pnl(trade, bid, 2), "reason": trade["reason"]}]
        rows.append(trade)
    rows.append({"id": "demo-skip", "symbol": "QQQ", "session": str(day), "origin": stamp(now),
                 "state": "SKIPPED", "reason": "No fresh post-alert option quote", "plan": {}, "fills": []})
    return rows, [day_report(rows, day)], now, now, False


def dashboard():
    import pandas as pd
    import streamlit as st

    st.set_page_config(page_title="Quant Desk · Paper Trading", layout="wide")
    st.title("Quant Desk · Paper Trading")
    is_demo = st.toggle("Show demo data", value=not config("PAPER_HIDE_DEMO"), help="Displays a fixed sample ledger.")
    st.caption("Simulated options · New York time · estimated $0.65 per contract per side")

    def live_data():
        now = clock()
        try:
            dsn = st.secrets.get("MONITOR_JOURNAL_DSN") or config("MONITOR_JOURNAL_DSN")
        except FileNotFoundError:
            dsn = config("MONITOR_JOURNAL_DSN")
        path = Path(config("MONITOR_JOURNAL_PATH") or ROOT / "monitor_data/runtime/quant_desk.sqlite3")
        # Read-only dashboard access. The worker initializes and writes the tables.
        if dsn:
            import psycopg
            db = psycopg.connect(dsn, connect_timeout=4, options="-c statement_timeout=4500 -c default_transaction_read_only=on")
        else:
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = [json.loads(raw) for raw, in db.execute("SELECT body FROM qd_paper_trade ORDER BY origin,id")]
            reports, meta = [], {}
            for name, raw in db.execute("SELECT key,body FROM qd_paper_meta ORDER BY key DESC"):
                if name.startswith("review:"):
                    reports.append(json.loads(raw))
                else:
                    meta[name] = json.loads(raw)
            return rows, reports, now, time_or_none(meta.get("heartbeat")), meta.get("loss_stop") == str(now.astimezone(ET).date())
        finally:
            db.close()

    @st.fragment(run_every=5)
    def view():
        if is_demo:
            trades, reports, now, heartbeat, halted = demo()
            st.info("Demo data · example fills and P/L")
        else:
            try:
                trades, reports, now, heartbeat, halted = live_data()
            except Exception:
                st.warning("Waiting for the paper worker and shared journal. Check the setup guide.")
                return
            if not heartbeat or (now - heartbeat).total_seconds() > 60:
                st.warning("The paper worker has not reported in the last minute.")
        if halted:
            st.warning("Daily paper loss stop reached. New entries are paused.")
        opened = [t for t in trades if t["state"] == "OPEN"]
        marks = [value for t in opened if (value := unrealized(t, now)) is not None]
        closed = [t for t in trades if t["state"] == "CLOSED"]
        a, b, c, d = st.columns(4)
        a.metric("Realized P/L", f"${sum(map(realized, trades)):,.2f}")
        b.metric("Unrealized P/L", f"${sum(marks):,.2f}" if marks or not opened else "—",
                 f"{len(marks)}/{len(opened)} fresh bids")
        c.metric("Open positions", len(opened))
        d.metric("Closed trades", len(closed),
                 f"{sum(realized(t) > 0 for t in closed) / len(closed):.0%} win rate" if closed else None)

        def display_time(value):
            at = time_or_none(value)
            return at.astimezone(ET).strftime("%b %d, %H:%M:%S") if at else ""

        def table(rows):
            frame = pd.DataFrame([{"Ticker": t["symbol"], "State": t["state"], "Contract": t.get("contract"),
                                   "DTE": t.get("dte"), "Alert ET": display_time(t["origin"]),
                                   "Entry ET": display_time(t.get("opened")), "Exit ET": display_time(t.get("closed")),
                                   "Setup": t["plan"].get("setup"), "Score": t["plan"].get("score"),
                                   "Qty": t.get("qty"), "Remaining": t.get("remaining"),
                                   "Entry ask": t.get("entry_ask"), "Last bid": t.get("mark_bid"),
                                   "Bid ET": display_time(t.get("mark_at")), "Stop": t["plan"].get("stop"),
                                   "TP1": t["plan"].get("tp1"), "TP2": t["plan"].get("tp2"),
                                   "Realized $": realized(t), "Unrealized $": unrealized(t, now),
                                   "Details": t["reason"]} for t in rows])
            if frame.empty:
                st.caption("No records yet.")
            else:
                st.dataframe(frame, hide_index=True, width="stretch")
            return frame

        positions, history, learning = st.tabs(["Open positions", "Trade history", "Daily learning"])
        with positions:
            table(opened)
        with history:
            ticker = st.selectbox("Ticker", ["All"] + sorted({t["symbol"] for t in trades}))
            results = [t for t in reversed(trades) if ticker in ("All", t["symbol"])]
            table_data = table(results)
            if not table_data.empty:
                st.download_button("Download trade history", table_data.to_csv(index=False), "paper-trades.csv")
            exits = [{"Ticker": t["symbol"], "Contract": t["contract"], "Alert ID": t["id"],
                      "ET": time_or_none(fill["at"]).astimezone(ET), **fill}
                     for t in results for fill in t["fills"]]
            if exits:
                frame = pd.DataFrame(exits).sort_values("at")
                frame["Cumulative P/L"] = frame["pnl"].cumsum()
                st.line_chart(frame, x="ET", y="Cumulative P/L")
                st.dataframe(frame, hide_index=True, width="stretch")
                st.download_button("Download exit fills", frame.to_csv(index=False), "paper-exits.csv")
        with learning:
            if not reports:
                st.caption("A report appears after a completed market session.")
                return
            chosen = st.selectbox("Session", [report["day"] for report in reports])
            review = next(report for report in reports if report["day"] == chosen)
            st.metric("Realized P/L of that session's setups", f"${review['pnl']:,.2f}")
            st.write(review["recommendation"])
            st.caption(f"{review['trades']} closed trades across {review['sessions']} sessions · {review['test_sessions']} evaluation sessions")
            if review["cohorts"]:
                st.dataframe(review["cohorts"], hide_index=True, width="stretch")
            if review["skips"]:
                st.write("Skipped entries", review["skips"])
            if review["seen"]:
                st.write(f"Model kept {review['kept']}/{review['seen']} observed trades in chronological replay. "
                         f"Paper P/L was ${review['selected']:,.2f} versus ${review['baseline']:,.2f} for all fills.")
            if review["model"]:
                with st.expander("Model inputs and estimated effects"):
                    st.dataframe(review["model"], hide_index=True, width="stretch")
            st.caption("Recommendations are advisory. Small samples and unfilled alerts limit what the model can learn.")

    view()


if __name__ == "__main__":
    if "--worker" in sys.argv[1:]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--worker", action="store_true")
        parser.add_argument("--once", action="store_true")
        run_worker(parser.parse_args().once)
    else:
        dashboard()
