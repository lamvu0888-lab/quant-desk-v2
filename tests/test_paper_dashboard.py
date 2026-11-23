from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

import pytest
import paper_engine
from paper_engine import PaperLedger, PaperWorker, option_pnl, realized, stamp, unrealized
from research_journal import Journal
from tradytics_feed import Feed, Snapshot

START = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)
PLAN = {"status": "ENTER LONG", "direction": 1, "score": 82, "entry": 675, "trigger": 674.5,
        "stop": 673, "tp1": 677, "tp2": 680, "invalidation": 674, "hold": 8,
        "movement": {"accelerating": True}, "setup": "Breakout", "source": "Webull"}


@pytest.mark.parametrize("early", [False, True])
def test_worker_from_confirmed_alert_to_partial_and_stop(tmp_path, monkeypatch, early):
    journal = Journal(tmp_path / "desk.sqlite3", dsn="")
    book = PaperLedger(journal)
    book.start(START)
    if early:
        journal.record_early("early", "SPY", "0DTE", START, dict(PLAN, stop=673.8, score=60), timedelta(minutes=10))
    for ident, state in (() if early else (("sent-id", "sent"), ("reserved-id", "claimed"))):
        journal.record_candidate(ident, "SPY", "0DTE", START,
                                 {"plan": {**PLAN, "setup": ident}, "decision_status": PLAN["status"]},
                                 alert_id=ident, cooldown=timedelta(minutes=10))
        journal.claim_delivery(ident)
        journal.delivery_state(ident, state)
    now = START + timedelta(seconds=5)
    spot = 675
    bid = 2.30

    class Quotes:
        def prices(self, symbols):
            return {}
        def options(self, symbol, expiry, spot, now):
            rows = [{"contract": f"SPY261113C00{strike}000", "strike": strike, "type": "call",
                     "multiplier": 100, "standard": True, "symbol": symbol, "expiry": expiry,
                     "bid": 2.30, "ask": 2.40, "updated_at": stamp(now)}
                    for strike in (675, 676, 685)]
            return Feed(Snapshot(rows, {"symbol": symbol, "expiry": expiry}, now))
        def _client(self):
            return SimpleNamespace(data=SimpleNamespace(option_market_data=SimpleNamespace(get_option_snapshot=None)))
        def _option_request(self, label, method, symbols, **params):
            return [{"symbol": code, "bid": bid, "ask": max(bid + .01, 2.40),
                     "bid_size": 2, "ask_size": 2, "delay_minutes": 0, "quote_time": stamp(now)}
                    for code in symbols.split(",")]

    def desk(symbol, observed, *args, **kwargs):
        return SimpleNamespace(now=observed, ticker=symbol, chart_quote={"at": observed, "price": spot})

    monkeypatch.setattr(paper_engine, "Desk", desk)
    monkeypatch.setattr(paper_engine, "read_price", lambda _: {"bars_usable": False, "candles": []})
    worker = PaperWorker(book, Quotes(), None, lambda: now)

    def rows():
        with journal.connection() as db:
            return book.rows(db)

    with book.lock() as assert_lock:
        worker.poll(assert_lock)
        assert len(rows()) == 1
        assert rows()[0]["contract"] == "SPY261113C00676000"
        assert rows()[0]["qty"] == 2
        assert unrealized(rows()[0], now) == -22.60

        now, spot, bid = START + timedelta(seconds=20), 677.50, 2.90
        worker.poll(assert_lock)
        assert rows()[0]["remaining"] == 1
        assert rows()[0]["fills"][0]["reason"] == "TP1"
        worker.poll(assert_lock)
        assert rows()[0]["remaining"] == 1

        now, spot, bid = START + timedelta(seconds=25), 672.95, 1.70
        worker.poll(assert_lock)
        done = rows()[0]
        assert done["state"] == "CLOSED"
        assert done["fills"][-1]["reason"] == "STOP"
        assert realized(done) == round(option_pnl(done, 2.90, 1) + option_pnl(done, 1.70, 1), 2)


def test_next_listed_expiration_and_slow_classification():
    assert not paper_engine.fast_setup({**PLAN, "movement": {"accelerating": True, "range_hold": True}})
    assert not paper_engine.fast_setup({**PLAN, "movement": {"market": "SIDEWAYS", "accelerating": True}})
    paper_feed = Feed(Snapshot([{"symbol": "SPX", "expiry": "2026-11-13"},
                               {"symbol": "SPX", "expiry": "2026-11-16"}],
                              {"symbol": "SPX"}, START))
    ctx = SimpleNamespace(get=lambda *a, **kw: paper_feed, ticker="SPX", now=START)
    assert paper_engine.expiry_for(ctx, START.date(), False) == "2026-11-16"


def test_dashboard_starts_and_filters():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "paper_dashboard.py").run()
    assert not app.exception
    assert app.metric[0].label == "Realized P/L"
    assert app.metric[1].value == "$61.40"
    assert app.metric[2].value == "1"
    app.selectbox[0].select("SPX").run()
    assert not app.exception
    assert set(app.dataframe[1].value["Ticker"]) == {"SPX"}
