"""Regression checks for Tradytics ingestion and the new local entry point."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

import pytest
from streamlit.testing.v1 import AppTest

from terminal_data import chart_buckets, price_bars, quote_row, snapshot_forecast_gate, status
from terminal_demo import DEMO_AT, DemoStore
from tradytics_feed import BASE_URL, Feed, FeedError, FeedStore, Snapshot, TradyticsClient


class Reply:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload


def test_exposure_uses_documented_endpoint_and_keeps_zero_dte():
    observed = []

    def get(url, **kwargs):
        observed.append((url, kwargs))
        return Reply({"data": {"symbol": "SPX", "unit": "usd_per_vol_point", "rows": [{"strike": 7600, "net": 50}]},
                      "meta": {"as_of": DEMO_AT.isoformat()}})

    client = TradyticsClient("fixture_key", transport=get)
    feed = Feed(client.get("exposure", symbol="SPX", greek="vanna", by="strike", dte=0))
    assert observed[0][0] == BASE_URL + "/options/exposure"
    assert observed[0][1]["params"] == {"symbol": "SPX", "greek": "vanna", "by": "strike", "dte": 0, "apiKey": "fixture_key"}
    assert observed[0][1]["allow_redirects"] is False
    assert feed.rows[0]["net"] == 50
    assert feed.obj["unit"] == "usd_per_vol_point"


def test_rate_limit_cooldown_is_shared_between_endpoints():
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Reply({"error": {"code": "rate_limited", "message": "fixture_key"}}, 429, {"Retry-After": "45"})

    client = TradyticsClient("fixture_key", transport=get)
    for name in ("flow", "exposure"):
        with pytest.raises(FeedError) as exc:
            client.get(name, symbol="SPY")
        assert exc.value.status == "COOLDOWN"
        assert "fixture_key" not in str(exc.value)
        assert exc.value.retry_seconds > 40
    assert len(calls) == 1


def test_missing_scope_preserves_other_scopes():
    def get(url, **kwargs):
        if url.endswith("/signals"):
            return Reply({"error": {"code": "missing_scope"}}, 403)
        return Reply({"data": []})

    client = TradyticsClient("fixture_key", transport=get)
    with pytest.raises(FeedError, match="signals scope"):
        client.get("signals", symbol="SPY")
    assert client.get("quotes", symbol="SPY").data == []


def test_flow_reads_opaque_cursor_and_deduplicates_ids():
    calls = []
    now = datetime.now(timezone.utc)

    def get(url, **kwargs):
        params = kwargs["params"]
        calls.append(params)
        row_id = 102 if params.get("cursor") else 101
        return Reply({"data": [{"id": row_id, "time": (now + timedelta(seconds=row_id-100)).isoformat()}],
                      "next_cursor": None if params.get("cursor") else "opaque:next"})

    store = FeedStore(TradyticsClient("fixture_key", transport=get))
    try:
        from market_session import ET
        head = Snapshot([{"id": 100, "time": now.isoformat()}], {}, now)
        result, _, _ = store._read("flow", {"symbol": "SPY", "dte": 0, "limit": 250}, head,
                                   time.monotonic(), now.astimezone(ET).date().isoformat())
        assert [row["id"] for row in result.data] == [102, 101, 100]
        assert calls[0]["since"] == 100
        assert calls[1]["cursor"] == "opaque:next"
    finally:
        store.close()


def test_spx_candles_omit_forming_bar_and_keep_null_volume():
    demo = DemoStore("SPX")
    feed = demo.get("bars")
    forming = {**feed.rows[-1], "time": DEMO_AT.replace(second=0).isoformat()}
    replay = Feed(Snapshot(feed.rows + [forming], {}, DEMO_AT))
    rows = price_bars(replay, "SPX", DEMO_AT)
    assert len(rows) == len(feed.rows)
    assert all(row["volume"] is None for row in rows)
    assert chart_buckets(rows, 5)[-1]["session_vwap"] is None
    index = quote_row(demo.get("indices"), "SPX")
    assert index["symbol"] == "SPX"
    assert quote_row(demo.get("indices"), "SPY") == {}


def test_forecast_gate_checks_price_time_and_delays():
    demo = DemoStore("SPY")
    quotes = demo.get("quotes")
    bars = demo.get("bars")
    quote = quote_row(quotes, "SPY")
    candles = price_bars(bars, "SPY", DEMO_AT)
    assert not snapshot_forecast_gate(quote, candles, quotes, bars, DEMO_AT)[1]
    stale = {**quote, "price_at": (DEMO_AT - timedelta(minutes=16)).isoformat()}
    assert snapshot_forecast_gate(stale, candles, quotes, bars, DEMO_AT)[1]
    delayed = Feed(Snapshot(bars.rows, {"delay_minutes": 15}, DEMO_AT))
    assert "Tradytics reports delayed data." in snapshot_forecast_gate(quote, candles, quotes, delayed, DEMO_AT)[1]
    cached = Feed(quotes.snapshot, FeedError("NETWORK", "fixture"))
    assert status("quotes", cached, DEMO_AT, "SPY")[0] == "CACHED"


def test_terminal_preview_pages_and_index_switch(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **k: {})
    for key in ("TRADYTICS_API_KEY", "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "DEFAULT_TICKER"):
        monkeypatch.delenv(key, raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "terminal.py"), default_timeout=30).run()
    assert not app.exception
    assert app.session_state["desk_mode"] == "Preview"
    app.text_input(key="desk_symbol_input").set_value("nvda")
    next(button for button in app.button if button.label == "Open").click().run()
    assert app.session_state["desk_ticker"] == "NVDA"
    app.button(key="watch_SPX").click().run()
    assert app.session_state["desk_ticker"] == "SPX"
    assert not app.exception
    for page in ("Options Flow", "Positioning", "Volatility", "Dark Pool", "Signals", "Forecast", "History", "Data Health", "Learn"):
        app.radio(key="desk_page").set_value(page).run()
        assert not app.exception, page
    app.button(key="learn_open_page").click().run()
    assert app.session_state["desk_page"] == "Overview"
    assert not app.exception
