"""Cash-index identity, OAuth rotation and provider selection at the dashboard and worker."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from discord_monitor import MonitorScan
from market_session import rth_bounds
from research_journal import Journal
from schwab_auth import SchwabTokens
from schwab_feed import BASE, MINUTE, SchwabMarketData, normalize_history, normalize_quote, quote_status
from schwab_setup import callback_code
from settings import Settings
from terminal_data import price_bars
from terminal_demo import DEMO_AT, DemoStore
from terminal_views import Desk, route_price
from test_hybrid_feeds import snapshot, webull_prices
from tradytics_feed import Feed, FeedError
from webull_feed import price_source

NOW = datetime(2026, 11, 18, 17, 0, 5, tzinfo=timezone.utc)


def millis(stamp):
    return int(stamp.timestamp() * 1000)


def raw_quote(at, realtime=True):
    return {"$SPX": {"assetMainType": "INDEX", "symbol": "$SPX", "realtime": realtime,
                     "quote": {"lastPrice": 7012.5, "closePrice": 7000, "tradeTime": millis(at)}}}


def raw_bar(at, **extra):
    return {"datetime": millis(at), "open": 7010, "high": 7014, "low": 7009,
            "close": 7012, "volume": 123, "vwap": 7011, **extra}


def bars_for(at):
    start = at.replace(second=0, microsecond=0) - MINUTE
    return normalize_history({"symbol": "$SPX", "candles": [raw_bar(start)]}, at)


class IndexFeeds:
    def __init__(self, at, *, realtime=True, bars=None, quote_at=None):
        self.quote = Feed(normalize_quote(raw_quote(quote_at or at, realtime), at))
        self.history = snapshot({"candles": bars_for(at) if bars is None else bars}, at, source="Schwab")

    def get(self, name, **params):
        return self.quote if name == "quote" else self.history


@pytest.mark.parametrize(("real", "expected"), [(True, "FRESH QUOTE"), (False, "DELAYED"),
                                               (None, "ENTITLEMENT UNKNOWN"), ("true", "ENTITLEMENT UNKNOWN")])
def test_index_quote_entitlement_and_last_price_clock(real, expected):
    body = raw_quote(NOW, real)
    assert quote_status(Feed(normalize_quote(body, NOW)), NOW) == expected
    body["$SPX"]["quote"].update(tradeTime=millis(NOW - 3 * MINUTE), quoteTime=millis(NOW))
    if real is True:
        assert quote_status(Feed(normalize_quote(body, NOW)), NOW) == "AGED"
        del body["$SPX"]["quote"]["tradeTime"]
        assert quote_status(Feed(normalize_quote(body, NOW)), NOW) == "FRESH QUOTE"
        del body["$SPX"]["quote"]["quoteTime"]
        assert quote_status(Feed(normalize_quote(body, NOW)), NOW) == "QUOTE UNVERIFIED"


def test_identity_and_regular_session_candles():
    with pytest.raises(FeedError):
        normalize_quote({"SPY": raw_quote(NOW)["$SPX"]}, NOW)
    with pytest.raises(FeedError):
        normalize_quote({"$SPX": {**raw_quote(NOW)["$SPX"], "assetMainType": "EQUITY"}}, NOW)
    opened, _ = rth_bounds(NOW.date())
    body = {"symbol": "$SPX", "candles": [raw_bar(opened - MINUTE), raw_bar(opened),
            raw_bar(opened, close=7013), raw_bar(opened + MINUTE, high=2),
            raw_bar(opened + 2 * MINUTE, symbol="SPY"), raw_bar(opened + 3 * MINUTE)]}
    rows = normalize_history(body, opened + 3 * MINUTE + timedelta(seconds=15))
    assert len(rows) == 1 and rows[0]["time_utc"] == opened.isoformat()
    assert rows[0]["close"] == 7013 and rows[0]["volume"] is None and rows[0]["vwap"] is None
    for override in ({"symbol": "SPY"}, {"symbol": "SPX"}, {"isIndex": False},
                     {"realtime": False}, {"delay_minutes": 15}, {"empty": True}):
        with pytest.raises(FeedError):
            normalize_history({**body, **override}, NOW)
    assert normalize_history({**body, "symbol": "SPX", "isIndex": True}, NOW)


def test_after_close_holiday_and_premarket():
    opened, closed = rth_bounds("2026-11-27")  # shortened Friday after Thanksgiving
    body = {"symbol": "$SPX", "candles": [raw_bar(closed - MINUTE), raw_bar(closed),
            raw_bar(opened - timedelta(days=1))]}
    assert len(normalize_history(body, closed + MINUTE)) == 1
    prior = Feed(normalize_quote(raw_quote(closed), closed))
    monday_early = closed + timedelta(days=2, hours=18)
    assert quote_status(prior, monday_early) == "SESSION CLOSE"
    assert quote_status(Feed(normalize_quote(raw_quote(monday_early), monday_early)), monday_early) == "OUTSIDE SESSION"


def test_rest_unauthorized_retry_and_no_secret_in_errors(monkeypatch):
    tokens = Mock()
    tokens.access_token.side_effect = ["old", "new", "new"]
    http = Mock(side_effect=[Mock(status_code=401), Mock(status_code=200, json=lambda: raw_quote(NOW)),
                             Mock(status_code=403, text="private-value")])
    monkeypatch.setattr("schwab_feed.requests.get", http)
    client = SchwabMarketData(tokens)
    assert client.get("quote").data["price"] == 7012.5
    assert tokens.access_token.call_args_list[1].kwargs == {"rejected": "old"}
    assert http.call_args.args == (BASE + "/quotes",)
    assert http.call_args.kwargs["params"] == {"symbols": "$SPX", "fields": "quote"}
    assert http.call_args.kwargs["headers"]["Authorization"] == "Bearer new"
    with pytest.raises(FeedError) as error:
        client.get("quote")
    assert error.value.status == "ACCESS DENIED" and "private-value" not in str(error.value)


def test_history_query_windows_and_daily(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr("schwab_feed.datetime", Clock)
    yesterday, _ = rth_bounds("2026-11-17")
    start = NOW.replace(second=0) - 2 * MINUTE
    history = Mock(side_effect=[{"symbol": "$SPX", "candles": [raw_bar(start)]},
                                {"symbol": "$SPX", "candles": [raw_bar(start, close=7013), raw_bar(start + MINUTE)]},
                                {"symbol": "$SPX", "candles": [raw_bar(yesterday), raw_bar(start)]},
                                {"symbol": "$SPX", "candles": [raw_bar(yesterday), raw_bar(start)]}])
    client = SchwabMarketData(None)
    monkeypatch.setattr(client, "_get", history)
    assert len(client.get("history").data["candles"]) == 1
    assert [r["close"] for r in client.get("history").data["candles"]] == [7013, 7012]
    assert len(client.get("history", session="2026-11-17").data["candles"]) == 1
    assert len(client.get("daily").data["candles"]) == 1
    first, second, dated, daily = [call.args[1] for call in history.call_args_list]
    assert all(call.args[0] == "/pricehistory" for call in history.call_args_list)
    assert first["symbol"] == "$SPX" and first["needExtendedHoursData"] == "false"
    assert first["frequencyType"] == "minute" and first["frequency"] == 1
    assert first["startDate"] < second["startDate"] and dated["startDate"] == millis(yesterday)
    assert daily["frequencyType"] == "daily"


def test_callback_binding_and_single_decoding():
    url = "https://127.0.0.1:8182/return"
    good = url + "?code=ab%40xy%252F&state=state1"
    assert callback_code(good, url, "state1") == "ab@xy%2F"
    for bad in (good.replace("127.0.0.1", "example.invalid"), good.replace("8182", "8183"),
                good.replace("/return", "/other"), good.replace("https:", "http:"), good + "&code=other",
                good.replace("state1", "wrong"), good + "&error=denied"):
        with pytest.raises(ValueError):
            callback_code(bad, url, "state1")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_encrypted_token_rotation_across_clients(backend, tmp_path, monkeypatch):
    dsn = os.environ.get("SCHWAB_TEST_PG_DSN", "") if backend == "postgres" else ""
    if backend == "postgres" and not dsn:
        pytest.skip("Set SCHWAB_TEST_PG_DSN to test a disposable PostgreSQL database")
    path = tmp_path / "tokens.sqlite"
    def client(secret="test-secret"):
        return SchwabTokens("test-index-app", secret, dsn, path)
    def exchange(url, *, auth, data, **kwargs):
        assert auth == ("test-index-app", "test-secret")
        if data["grant_type"] == "authorization_code":
            return Mock(status_code=200, json=lambda: dict(access_token="first-access", refresh_token="first-refresh",
                                                          expires_in=1800, token_type="Bearer"))
        assert data["refresh_token"] == "first-refresh"
        return Mock(status_code=200, json=lambda: dict(access_token="next-access", refresh_token="next-refresh",
                                                      expires_in=1800, token_type="Bearer"))
    post = Mock(side_effect=exchange)
    monkeypatch.setattr("schwab_auth.requests.post", post)
    initial = client()
    initial.prepare()
    initial.authorize("authorization-code", "https://127.0.0.1:8182")
    assert client().access_token() == "first-access"
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(lambda _: client().access_token(rejected="first-access"), range(3))) == ["next-access"] * 3
    assert post.call_count == 2
    with initial._db() as db:
        ciphertext = initial._query(db, "SELECT ciphertext FROM qd_schwab_oauth WHERE app=?", initial._key).fetchone()[0]
        if isinstance(ciphertext, bytes):
            ciphertext = ciphertext.decode("ascii")
        assert "next-refresh" not in ciphertext and "next-access" not in ciphertext
        assert b"next-refresh" in initial._cipher.decrypt(ciphertext.encode())
    with pytest.raises(FeedError, match="does not match"):
        client("different-secret").access_token()
    if backend == "sqlite":
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM qd_schwab_oauth").fetchone() == (1,)


@pytest.mark.parametrize(("realtime", "webull_ok", "expected"),
                         [(True, True, "Schwab"), (False, True, "Webull"), (None, False, "Tradytics")])
def test_auto_takes_one_verified_cash_spx_pair(realtime, webull_ok, expected):
    demo = DemoStore("SPX")
    index = IndexFeeds(DEMO_AT, realtime=realtime)
    feeds = webull_prices(index.history.obj["candles"], DEMO_AT)
    feeds["history"] = snapshot({**feeds["history"].obj, "delay_minutes": 0 if webull_ok else 15}, DEMO_AT)
    webull = SimpleNamespace(settings=Settings(), prices=lambda symbols: {"SPX": feeds})
    ctx = Desk("SPX", DEMO_AT, demo, False, False, None, True, "Schwab", webull_store=webull, schwab_store=index)
    actual, data = route_price(ctx, "Auto")
    assert actual.price_provider == expected
    assert actual.chart_quote["source"] == expected and data["bars_ready"]
    assert bool(actual.price_fallback) == (expected != "Schwab")
    assert route_price(ctx, "Schwab")[0].price_provider == "Schwab"


def test_missing_bars_and_normal_first_minute():
    opened, _ = rth_bounds(NOW.date())
    for at in (opened + timedelta(seconds=10), NOW):
        ctx = Desk("SPX", at, None, False, False, None, False, "Schwab", schwab_store=IndexFeeds(at, bars=[]))
        selected, data = route_price(ctx, "Auto")
        assert selected.chart_quote["source"] == "Schwab" and not data["bars_ready"]
    idx = IndexFeeds(DEMO_AT, bars=[])
    ctx = Desk("SPX", DEMO_AT, DemoStore("SPX"), False, False, None, True, "Schwab", schwab_store=idx)
    assert route_price(ctx, "Auto")[0].price_provider == "Tradytics"
    for symbol in ("SPY", "QQQ", "NDX"):
        assert price_source(symbol, "Auto", True, True) == price_source(symbol, "Auto", True, False)
        assert price_source(symbol, "Schwab", True, True) == price_source(symbol, "Auto", True, False)
    assert price_source("SPX", "Auto", True, False) == "Webull"


def test_rendered_dashboard_and_worker_share_selected_source(monkeypatch):
    import terminal
    demo = DemoStore("SPX")
    demo.client = SimpleNamespace(budget=lambda: {"calls_last_minute": 0, "local_limit": 45, "cooldown_seconds": 0})
    bars = price_bars(demo.get("bars", symbol="SPX", timeframe="1m"), "SPX", DEMO_AT)
    index = IndexFeeds(DEMO_AT, bars=bars)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return DEMO_AT
    config = {"DEFAULT_TICKER": "SPX", "TRADYTICS_API_KEY": "fixture", "SCHWAB_CLIENT_ID": "test-id",
              "SCHWAB_CLIENT_SECRET": "fixture-secret"}
    monkeypatch.setattr(terminal, "datetime", Clock)
    monkeypatch.setattr(terminal, "config_value", lambda local, name, default="": config.get(name, default))
    monkeypatch.setattr(terminal, "dotenv_values", lambda *_: {})
    monkeypatch.setattr(terminal, "tradytics_store", lambda *_: demo)
    monkeypatch.setattr(terminal, "schwab_store", lambda *_: index)
    monkeypatch.setattr(terminal, "journal_resource", lambda *_: None)
    app = AppTest.from_string("from terminal import main\nmain()", default_timeout=30)
    app.session_state["desk_expiry_mode"] = "All DTE"
    app.run()
    assert not app.exception
    assert "Schwab" in app.selectbox(key="desk_price_provider").options
    assert any("Schwab reported price 7,012.50" in caption.value for caption in app.caption)
    app.selectbox(key="desk_page").set_value("Data Health").run()
    assert not app.exception
    assert any("Schwab $SPX candles" in caption.value for caption in app.caption)
    _, reports = MonitorScan(demo, None, "Auto", None, index).run(["SPX"], DEMO_AT, 30, dry_run=True)
    assert reports[0]["price_source"] == "Schwab" and reports[0]["candle_at"] and reports[0]["quote_at"]


def test_paper_worker_uses_alerts_original_price_provider(tmp_path, monkeypatch):
    import paper_engine
    from test_paper_dashboard import PLAN
    journal = Journal(tmp_path / "paper.sqlite", dsn="")
    book = paper_engine.PaperLedger(journal)
    book.start(NOW)
    journal.record_candidate("test-id", "SPX", "0DTE", NOW,
                             {"plan": {**PLAN, "source": "Schwab"}, "decision_status": PLAN["status"]},
                             alert_id="test-id", cooldown=MINUTE)
    journal.claim_delivery("test-id")
    journal.delivery_state("test-id", "sent")
    seen = []
    def inspect(trade, ctx, *args):
        seen.append((trade["symbol"], ctx.chart_quote["source"], ctx.chart_quote["price"]))
    monkeypatch.setattr(paper_engine, "attempt_entry", inspect)
    paper_engine.PaperWorker(book, None, None, lambda: NOW, schwab=IndexFeeds(NOW)).poll()
    assert seen == [("SPX", "Schwab", 7012.5)]
