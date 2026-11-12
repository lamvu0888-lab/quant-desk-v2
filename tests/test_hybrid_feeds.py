"""Hybrid provider regressions at the quote, dashboard and worker boundaries."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from discord_monitor import MonitorScan
from desk_analytics import MINUTE
from market_session import rth_bounds
from my_webull_adapter import WebullSDKAdapter
from movement_detector import early_price_move
from option_quotes import atm_iv_sample
from research_journal import Journal
from settings import Settings
from terminal_data import time_or_none
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
        assert price_source("SPX", "Auto", store) == "Tradytics"
        assert atm_iv_sample(Feed(chain), "SPX", expiry, 5200, now)["value"] == .18
    finally:
        store.close()


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
