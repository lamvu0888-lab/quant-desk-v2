"""Hybrid provider regressions at the quote, dashboard and worker boundaries."""
from concurrent.futures import Future
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from streamlit.testing.v1 import AppTest

from data_utils import BudgetPause
from discord_monitor import MonitorScan
from desk_analytics import MINUTE
from market_session import rth_bounds
from movement_charts import chart_for_stage
from my_webull_adapter import WebullSDKAdapter, category_for, normalize_candles, normalize_quote
from movement_detector import early_price_move
from option_quotes import atm_iv_sample
from research_journal import Journal
from settings import Settings
from terminal_charts import integrated_intraday_figure
from terminal_data import chart_buckets, price_bars, time_or_none
from terminal_demo import DEMO_AT, DemoStore
from terminal_views import Desk, read_workspace
from test_v25 import synthetic_inputs
from tradytics_feed import Feed, Snapshot
from webull_feed import WebullFeedStore, price_source, webull_price_quality


def snapshot(data, now, **meta):
    return Feed(Snapshot(data, meta, now))


def webull_prices(candles, now):
    stamp = now.isoformat()
    return {"quote": snapshot({"last_price": candles[-1]["close"], "price_time_utc": stamp}, now),
            "history": snapshot({"candles": candles, "last_bar_utc": candles[-1]["time_utc"]}, now)}


def option_pair(symbol, expiry, now, *, bid=.99, ask=1.01):
    root = "SPXW" if symbol == "SPX" else symbol
    strike = 103.
    expires = rth_bounds(expiry)[1].isoformat()
    return snapshot([{"contract": f"{root}{expiry[2:].replace('-', '')}{right[0].upper()}00103000",
                      "symbol": symbol, "expiry": expiry, "expiry_utc": expires, "type": right,
                      "strike": strike, "bid": bid, "ask": ask, "iv": .18, "multiplier": 100,
                      "updated_at": now.isoformat()} for right in ("call", "put")],
                    now, symbol=symbol, expiry=expiry, root=root, source="Webull")


def test_webull_bar_request_includes_latest_market_data():
    data = Mock()
    data.market_data.get_batch_history_bar.return_value = Mock(status_code=200, json=lambda: [])
    adapter = WebullSDKAdapter(Settings(), data=data)
    adapter.history_batch(["SPY", "QQQ"], "US_ETF", 610, Mock())
    call = data.market_data.get_batch_history_bar.call_args.kwargs
    assert call["symbols"] == ["SPY", "QQQ"] and call["real_time_required"] is True and call["trading_sessions"] == "RTH"


def test_webull_batch_cannot_reuse_one_symbols_bars_for_another(monkeypatch):
    inputs = synthetic_inputs()
    now = inputs["now"]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("webull_feed.datetime", Clock)
    rows = [{**row, "time": row["time_utc"]} for row in inputs["candles"]]
    rows.append({**rows[-1], "time": now.isoformat()})  # still forming
    client = Mock()
    client.history_batch.return_value = {"result": [{"symbol": "SPY", "result": rows}]}
    store = WebullFeedStore(Settings(), adapter_factory=lambda: client)
    try:
        result = store._read_prices("history", "US_ETF", {"SPY": {}, "QQQ": {}})
        assert len(result["SPY"].data["candles"]) == len(inputs["candles"])
        assert result["QQQ"].status == "WEBULL UNAVAILABLE"
        assert client.history_batch.call_args.args[0] == ("SPY", "QQQ")
    finally:
        store.close()


def test_spxw_discovery_and_quote_time_are_separate_from_last_trade():
    inputs = synthetic_inputs()
    now, expiry = inputs["now"], inputs["expiry"]
    symbols = [f"SPXW{expiry[2:].replace('-', '')}{right}05200000" for right in "CP"]
    client = Mock()
    client.request.side_effect = [
        {"data": [{"symbol": code, "underlying_symbol": "SPX", "multiplier": "100", "def_type": "STANDARD"} for code in symbols]},
        [{"symbol": code, "bid": "2.1", "ask": "2.2", "imp_vol": "0.18", "delta": "0.5",
          "quote_time": int(now.timestamp() * 1000), "last_trade_time": int((now + MINUTE).timestamp() * 1000)} for code in symbols],
    ]
    store = WebullFeedStore(Settings(), adapter_factory=lambda: client)
    try:
        chain = store._read_options(("SPX", expiry), 5200, rth_bounds(expiry)[1])[("SPX", expiry)]
        discovery = client.request.call_args_list[0].kwargs
        assert discovery["root_symbol"] == "SPXW" and discovery["start_date"] == expiry
        assert {row["updated_at"] for row in chain.data} == {now.isoformat()}
        assert {row["contract"] for row in chain.data} == set(symbols)
        assert price_source("SPX", "Auto", store) == "Webull"
        assert atm_iv_sample(Feed(chain), "SPX", expiry, 5200, now)["value"] == .18
    finally:
        store.close()


def test_spx_index_endpoint_shapes_keep_index_volume_out_of_share_charts():
    now = datetime.fromisoformat("2026-11-13T20:59:04+00:00")
    quote_raw = [{"symbol": "SPX", "price": "7561.67", "last_trade_time": int((now - timedelta(seconds=4)).timestamp() * 1000),
                  "volume": "2727332784", "instrument_id": "913354362", "trade_status": "OC"}]
    bars_raw = {"result": [{"symbol": "SPX", "instrument_id": "913354362", "delay_minutes": 0,
                            "result": [{"time": "2026-11-13T20:58:00.000+0000", "open": "7559.22",
                                        "close": "7561.67", "high": "7562.79", "low": "7557.85",
                                        "volume": "617295295", "trading_session": "RTH"}]}]}
    assert category_for("SPX") == "US_INDEX"
    assert price_source("SPX", "Auto", True) == "Webull"
    assert price_source("SPX", "Tradytics", True) == "Tradytics"
    assert price_source("NDX", "Auto", True) == "Tradytics"
    quote = normalize_quote(quote_raw, "SPX", now=now)
    bars = normalize_candles(bars_raw, "SPX", now=now)
    assert quote["spot"] == bars["candles"][-1]["close"] == 7561.67
    assert bars["delay_minutes"] == 0 and bars["candles"][-1]["volume"] is None
    quality = webull_price_quality(snapshot(quote, now), snapshot(bars, now), Settings(), now)
    assert quality["quote_ready"] and quality["bars_ready"]
    assert not webull_price_quality(snapshot(quote, now), snapshot({**bars, "delay_minutes": None}, now), Settings(), now)["quote_ready"]
    assert not webull_price_quality(snapshot(quote, now), snapshot({**bars, "delay_minutes": 15}, now), Settings(), now)["quote_ready"]
    assert not webull_price_quality(snapshot(quote, now), snapshot(bars, now), Settings(), now + timedelta(minutes=3))["quote_ready"]


def test_spx_is_batched_separately_and_has_no_etf_category_retry(monkeypatch):
    now = datetime.fromisoformat("2026-11-13T20:55:04+00:00")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("webull_feed.datetime", Clock)
    client = Mock()
    client.quote_batch.return_value = [{"symbol": "SPX", "price": "7561.67",
                                         "last_trade_time": int(now.timestamp() * 1000)}]
    client.history_batch.return_value = {"result": [{"symbol": "SPX", "delay_minutes": 0,
        "result": [{"time": "2026-11-13T20:54:00.000+0000", "open": "7559.22", "close": "7561.67",
                    "high": "7562.79", "low": "7557.85", "volume": "617295295"}]}]}
    store = WebullFeedStore(Settings(), adapter_factory=lambda: client)
    try:
        assert "SPX" in store.prices(["SPX", "NDX"])
        q = store._read_prices("quote", "US_INDEX", {"SPX": {}})["SPX"]
        h = store._read_prices("history", "US_INDEX", {"SPX": {}})["SPX"]
        assert q.data["spot"] == 7561.67 and h.data["delay_minutes"] == 0
        assert client.quote_batch.call_args.args[1] == "US_INDEX"
        assert client.history_batch.call_args.args[1] == "US_INDEX"
        assert "SPX" not in store._category_hints
    finally:
        store.close()


def test_spx_dashboard_uses_webull_price_and_leaves_index_volume_blank():
    inputs = synthetic_inputs()
    now = inputs["now"] + timedelta(seconds=4)
    candles = [{**bar, "volume": None} for bar in inputs["candles"]]
    feeds = {
        "quote": snapshot({"ticker": "SPX", "last_price": 7561.67,
                           "price_time_utc": now.isoformat(), "delay_minutes": None}, now),
        "history": snapshot({"candles": candles, "last_bar_utc": candles[-1]["time_utc"],
                             "delay_minutes": 0}, now),
    }
    ctx = Desk("SPX", now, DemoStore("SPX"), False, False, None, True, price_provider="Webull",
               webull_store=SimpleNamespace(settings=Settings()), webull_prices=feeds)
    assert ctx.quote()["price"] == 7561.67
    assert ctx.history()[0] == candles
    assert ctx.chart_quote["source"] == "Webull"
    fig = integrated_intraday_figure(chart_buckets(candles, 5), "SPX", "5m", profile="GEX", quote=ctx.chart_quote)
    assert any(trace.name == "Webull quote" for trace in fig.data)
    assert all(trace.name != "Volume profile" for trace in fig.data)


def test_new_receipt_cannot_refresh_delayed_or_sandbox_prices():
    inputs = synthetic_inputs()
    now = inputs["now"]
    feeds = webull_prices(inputs["candles"], now)
    assert webull_price_quality(feeds["quote"], feeds["history"], Settings(), now)["quote_ready"]
    delayed = snapshot({**feeds["quote"].obj, "delay_minutes": 15}, now)
    assert not webull_price_quality(delayed, feeds["history"], Settings(), now)["quote_ready"]
    assert not webull_price_quality(feeds["quote"], feeds["history"], Settings(), now + 30 * MINUTE)["bars_ready"]
    sandbox = Settings(endpoint="api.sandbox.webull.com")
    assert not webull_price_quality(feeds["quote"], feeds["history"], sandbox, now)["quote_ready"]


def test_dashboard_webull_marker_moves_between_completed_candles():
    inputs = synthetic_inputs()
    now = inputs["now"] + timedelta(seconds=7)
    candles = inputs["candles"]
    store = SimpleNamespace(settings=Settings(quote_seconds=2))
    prices = webull_prices(candles, now)
    bars = chart_buckets(candles, 5)
    read = {"candles": candles, "buckets": bars}

    def charts(feeds, at=now, paused=False):
        ctx = Desk("SPY", at, None, False, paused, None, True, price_provider="Webull",
                   webull_store=store, webull_prices=feeds)
        return (integrated_intraday_figure(bars, "SPY", "5m", quote=ctx.chart_quote),
                chart_for_stage(1, ctx, read, {}))

    first = charts(prices)
    new_quote = snapshot({"last_price": candles[-1]["close"] + .22,
                          "price_time_utc": (now + timedelta(seconds=2)).isoformat()}, now + timedelta(seconds=2))
    second = charts({**prices, "quote": new_quote}, now + timedelta(seconds=2))
    for original, refreshed in zip(first, second):
        completed_series = original.data[0]
        closes = completed_series.y if completed_series.type == "scatter" else completed_series.close
        assert list(closes) == [row["close"] for row in bars]
        old_marker = next(trace for trace in original.data if trace.name == "Webull quote")
        new_marker = next(trace for trace in refreshed.data if trace.name == "Webull quote")
        assert old_marker.x[0] < new_marker.x[0]
        assert new_marker.y[0] - old_marker.y[0] > .2

    delayed = snapshot(prices["quote"].obj, now, delay_minutes=15)
    for feeds, at, paused in ((prices, now + timedelta(seconds=20), False),
                              ({**prices, "quote": delayed}, now, False), (prices, now, True),
                              (prices, rth_bounds(inputs["session"])[1], False)):
        assert all(trace.name != "Webull quote" for fig in charts(feeds, at, paused) for trace in fig.data)


def test_live_dashboard_reads_webull_quote_and_renders_movement_detector(monkeypatch):
    import terminal

    demo = DemoStore("SPY")
    candles = price_bars(demo.get("bars", symbol="SPY", timeframe="1m"), "SPY", DEMO_AT)
    current = [DEMO_AT]
    prices = webull_prices(candles, current[0])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]

    store = SimpleNamespace(settings=Settings(quote_seconds=2), quote_poll_seconds=2,
                            prices=lambda symbols, paused=False: {"SPY": prices})
    config = {"TRADYTICS_API_KEY": "fixture-tradytics", "WEBULL_APP_KEY": "fixture-webull",
              "WEBULL_APP_SECRET": "fixture-secret", "DEFAULT_TICKER": "SPY"}
    monkeypatch.setattr(terminal, "datetime", Clock)
    monkeypatch.setattr(terminal, "dotenv_values", lambda *_: {})
    monkeypatch.setattr(terminal, "config_value", lambda local, name, default="": config.get(name, default))
    monkeypatch.setattr(terminal, "webull_store", lambda *_: store)
    monkeypatch.setattr(terminal, "tradytics_store", lambda *_: demo)
    monkeypatch.setattr(terminal, "journal_resource", lambda *_: None)
    app = AppTest.from_string("from terminal import main\nmain()\n", default_timeout=30)
    app.session_state["desk_expiry_mode"] = "All DTE"
    app.run()
    assert not app.exception and app.session_state["desk_mode"] == "Live"
    assert any(f"Webull last trade {candles[-1]['close']:,.2f}" in row.value and "polling target 2s" in row.value
               for row in app.caption)

    current[0] += timedelta(seconds=2)
    spot = candles[-1]["close"] + .35
    prices["quote"] = snapshot({"last_price": spot, "price_time_utc": current[0].isoformat()}, current[0])
    app.button(key="overview_open_movement").click().run()
    assert not app.exception and not app.number_input
    assert any(f"Webull last trade {spot:,.2f}" in row.value for row in app.caption)


def test_webull_fast_quote_cadence_keeps_retry_and_sandbox_limits(monkeypatch):
    inputs = synthetic_inputs()
    clock = [100.]
    monkeypatch.setattr("webull_feed.time.monotonic", lambda: clock[0])
    settings = Settings.load({"QUOTE_REFRESH_SECONDS": "2"}.get)
    store = WebullFeedStore(settings, adapter_factory=Mock())
    sandbox = WebullFeedStore(Settings(endpoint="api.sandbox.webull.com", quote_seconds=2), adapter_factory=Mock())
    future = Future()
    monkeypatch.setattr(store._pools["quote"], "submit", Mock(return_value=future))
    try:
        assert sandbox.quote_poll_seconds == 5
        assert store.prices(["SPY"])["SPY"]["quote"].loading
        clock[0] += .5
        future.set_result({"SPY": Snapshot({}, {}, inputs["now"])})
        clock[0] = 101.99
        assert not store.prices(["SPY"])["SPY"]["quote"].loading
        assert store._pools["quote"].submit.call_count == 1
        clock[0] = 102
        retry = Future()
        store._pools["quote"].submit.return_value = retry
        assert store.prices(["SPY"])["SPY"]["quote"].loading
        retry.set_exception(BudgetPause(60))
        clock[0] = 120
        assert store.prices(["SPY"])["SPY"]["quote"].snapshot
        assert store._pools["quote"].submit.call_count == 2
    finally:
        store.close()
        sandbox.close()


def test_first_completed_impulse_fires_before_five_minute_close():
    inputs = synthetic_inputs()
    rows = inputs["candles"][:4]
    now = time_or_none(rows[-1]["time_utc"]) + MINUTE
    assert early_price_move(rows, inputs["session"], now) is None
    rows[-1] = {**rows[-1], "high": 100.6, "close": 100.5}
    assert early_price_move(rows, inputs["session"], now - timedelta(seconds=1)) is None
    move = early_price_move(rows, inputs["session"], now)
    assert move["window"] == "1m" and move["direction"] == "UP"
    assert early_price_move(rows, inputs["session"], now + timedelta(seconds=76)) is None


def test_worker_stock_watch_precedes_flow_then_requires_fresh_option(tmp_path, monkeypatch):
    inputs = synthetic_inputs()
    now, day = inputs["now"], inputs["session"]
    expiry = "2026-11-09"  # NVDA fixture has a listed later expiry and no 0DTE.
    symbol = "NVDA"
    journal = Journal(tmp_path / "hybrid.sqlite3", dsn="")
    use_flow = [inputs["flow_rows"][:-1]]
    option_feed = [option_pair(symbol, expiry, now - 3 * MINUTE)]
    timeline_requests = []

    def get(name, **params):
        if name == "expirations":
            return snapshot([{"expiry": expiry}], now, symbol=symbol)
        if name == "timeline":
            timeline_requests.append(params["dte"])
            return snapshot(use_flow[0], now, symbol=symbol, interval="5m")
        return Feed()

    tradytics = SimpleNamespace(get=get)
    webull = SimpleNamespace(settings=Settings(), prices=lambda symbols: {symbol: webull_prices(inputs["candles"], now)},
                             options=lambda *_a, **_k: option_feed[0])
    monkeypatch.setattr("terminal_views.realized_regime", lambda _: inputs["regime"])
    monkeypatch.setattr("monitor_engine.model_summary", lambda *_: {
        "version": "test-model", "p_tp1": .55, "baseline_p_tp1": .5, "stress_p_tp1": .46,
        "ev_ci": (-.08, .25), "baseline_ev_ci": (-.13, .19),
    })
    worker = MonitorScan(tradytics, webull, "Auto", journal)
    first, _ = worker.run([symbol], now, 30)
    assert [alert["status"] for _, alert in first] == ["MOVE WATCH"]
    assert first[0][1]["source"] == "Webull" and first[0][1]["symbol"] == symbol
    assert worker.run([symbol], now + timedelta(seconds=2), 30)[0] == []

    use_flow[0] = inputs["flow_rows"]
    assert worker.run([symbol], now + timedelta(seconds=4), 30)[0] == []
    assert journal.records(symbol, "decision", scope="Webull:3", limit=1)[0]["payload"]["status"] == "GET READY"
    option_feed[0] = option_pair(symbol, expiry, now + timedelta(seconds=8))
    confirmed, _ = worker.run([symbol], now + timedelta(seconds=10), 30)
    assert [alert["status"] for _, alert in confirmed] == ["ENTER LONG"]
    alert = confirmed[0][1]
    assert alert["option_quote"]["ready"] and alert["option_quote"]["expiry"] == expiry
    assert alert["flow_confirmation"]["count"] == 2 and set(timeline_requests) == {3}


def test_previous_price_and_flow_closes_cannot_confirm_a_new_bucket():
    inputs = synthetic_inputs()
    later = inputs["now"] + 5 * MINUTE
    # Both providers are still showing the previous 5m close.
    webull = SimpleNamespace(settings=Settings())
    store = SimpleNamespace(get=lambda name, **_: snapshot(inputs["flow_rows"], later, interval="5m") if name == "timeline" else Feed())
    desk = Desk("SPY", later, store, False, False, 0, True, "Webull", webull_store=webull,
                webull_prices=webull_prices(inputs["candles"], later))
    read = read_workspace(desk)
    assert read["flow_state"] == "Flow late" and not read["bars_ready"]
    assert time_or_none(inputs["candles"][-1]["time_utc"]) + MINUTE == read["flow_stamp"]
