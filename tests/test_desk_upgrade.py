from datetime import timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

from desk_analytics import mark_orders, order_rows, price_levels, realized_regime, setup_read, share_cvd
from market_session import rth_bounds
from terminal_data import chart_buckets, exact_timeline, price_bars
from terminal_demo import DEMO_AT, DemoStore
from terminal_views import Desk
from tradytics_feed import Feed, Snapshot
from tradingview_levels import dealer_levels, export_payload, pine_script


def test_exact_expiry_reuses_tape_and_exposure_date():
    class RecordingStore(DemoStore):
        def __init__(self, ticker):
            super().__init__(ticker)
            self.calls = []

        def get(self, name, **params):
            self.calls.append((name, params))
            return super().get(name, **params)

    store = RecordingStore("SPX")
    desk = Desk("SPX", DEMO_AT, store, True, False, 3, False)
    rows, _ = desk.tape()
    assert rows and {row["expiry"] for row in rows} == {desk.expiry}
    assert desk.exposure().rows
    assert [p["expiry"] for name, p in store.calls if name in ("flow", "exposure")] == [desk.expiry, desk.expiry]
    assert all("dte" not in p for name, p in store.calls if name in ("flow", "exposure"))
    assert desk.timeline().rows
    assert [p["dte"] for name, p in store.calls if name == "timeline"] == [3, 2]


def test_nonzero_dte_uses_only_matching_closed_additive_intervals():
    demo = DemoStore("SPY")
    upper = demo.get("timeline", dte=3, interval="5m")
    lower = demo.get("timeline", dte=2, interval="5m")
    # A missing lower bucket cannot silently be interpreted as zero.
    lower = Feed(Snapshot(lower.rows[1:], {}, DEMO_AT))
    result = exact_timeline(upper, lower, demo.day, DEMO_AT, "5m")
    assert len(result.rows) == len(upper.rows) - 1
    for high, low, row in zip(upper.rows[1:], lower.rows, result.rows):
        assert row["net_premium_usd"] == high["net_premium_usd"] - low["net_premium_usd"]
        assert row["prints"] == high["prints"] - low["prints"]
        assert "put_call_ratio" not in row
    late = Feed(Snapshot(lower.rows, {}, DEMO_AT - timedelta(minutes=3)))
    assert exact_timeline(upper, late, demo.day, DEMO_AT, "5m").error


def test_spx_regime_works_without_share_volume():
    demo = DemoStore("SPX")
    bars = price_bars(demo.get("bars"), "SPX", DEMO_AT)
    result = realized_regime(chart_buckets(bars, 5))
    assert result["label"] in {"Compressing", "Normal", "Expanding"}
    assert result["volume_ratio"] is None
    assert not share_cvd(bars, demo.day)


def test_share_cvd_resets_and_uses_only_observed_volume():
    day = DemoStore("SPY").day
    opened, _ = rth_bounds(day)
    bars = [
        {"time_utc": (opened + timedelta(minutes=i)).isoformat(), "open": o, "close": c, "volume": v}
        for i, o, c, v in [(0, 100, 101, 90), (1, 101, 100, 30), (2, 100, 100, 10),
                           (3, 100, 101, None), (4, 101, 101, 50), (5, 101, 102, 100)]
    ]
    assert [row["cvd"] for row in share_cvd(bars, day)] == [90, 60, 50, 50, 150]


def test_order_markers_never_use_a_later_close_for_missing_spot():
    day = DemoStore("SPY").day
    opened, _ = rth_bounds(day)
    bars = [{"time_utc": opened.isoformat(), "close": 99},
            {"time_utc": (opened + timedelta(minutes=1)).isoformat(), "close": 100}]
    trade = {"id": 1, "symbol": "SPY", "type": "put", "side": "sold", "premium_usd": 1_000_000,
             "time": (opened + timedelta(minutes=1, seconds=30)).isoformat()}
    prints = order_rows([trade, trade, {**trade, "id": 2, "symbol": "SPX"}], "SPY", DEMO_AT, session=day)
    matched = mark_orders(prints, bars, 12)
    assert len(matched) == 1
    assert matched[0]["spot"] == 99
    assert matched[0]["directional"] == 1
    assert "Prior" in matched[0]["spot_basis"]


def test_range_break_requires_fresh_price_and_flow_agreement():
    day = DemoStore("SPY").day
    opened, _ = rth_bounds(day)
    bars = [{"time_utc": (opened + timedelta(minutes=i * 5)).isoformat(),
             "close": 100 + .05*i if i < 7 else 102, "high": 101 if i < 7 else 103, "low": 99} for i in range(8)]
    rows = [{"time": bar["time_utc"], "net_premium_usd": 0 if i < 5 else 100_000} for i, bar in enumerate(bars)]
    regime = {"label": "Expanding", "atr": 1, "volume_ratio": 1.4}
    levels = price_levels(bars, day)
    result = setup_read(bars, rows, day, regime, levels)
    assert result["trigger"] == 101 and result["state"] == "Triggered" and result["agreement"] == 2
    assert setup_read(bars, rows, day, regime, levels, ready=False)["score"] is None
    opposite = [{**row, "net_premium_usd": -row["net_premium_usd"]} for row in rows]
    assert setup_read(bars, opposite, day, regime, levels)["state"] == "No trade"


def test_pine_export_is_ticker_bound_and_forward_from_snapshot():
    demo = DemoStore("SPY")
    obj = demo.get("exposure", greek="gamma", expiry=demo.expiries[0]).obj
    levels = dealer_levels(obj, demo.spot, 5)
    assert any(level.kind in ("gex_pos", "gex_neg") for level in levels)
    payload = export_payload("SPY", demo.day, demo.expiries[0], DEMO_AT, levels, preview=True, gamma=obj["total"])
    script = pine_script(payload)
    assert script.startswith("//@version=6")
    assert 'str.upper(syminfo.ticker) == "SPY"' in script
    assert f"int snapshotTime = {int(DEMO_AT.timestamp() * 1000)}" in script
    assert "extend=extend.right" in script and "apiKey" not in script


def test_upgrade_preview_pages_expiry_and_exports(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "dotenv_values", lambda *args, **kwargs: {})
    for key in ("TRADYTICS_API_KEY", "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "DEFAULT_TICKER"):
        monkeypatch.delenv(key, raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "terminal.py"), default_timeout=30).run()
    assert not app.exception
    assert any("Volatility regime" in value.value and "Waiting for bars" not in value.value for value in app.markdown)
    assert len(app.get("plotly_chart")) >= 4
    app.selectbox(key="desk_expiry_mode").set_value("Listed expiration").run()
    assert len(app.selectbox(key="desk_listed_SPY").options) == 5
    app.selectbox(key="desk_listed_SPY").set_value(DemoStore("SPY").expiries[1]).run()
    assert not app.exception
    assert any("3 DTE" in value.value for value in app.markdown)
    for page in ("Money Map", "Trade Plan", "Scanner", "TradingView", "Options Flow"):
        app.radio(key="desk_page").set_value(page).run()
        assert not app.exception, page
        if page == "TradingView":
            app.button(key="generate_tv").click().run()
            assert not app.exception
            assert any("//@version=6" in code.value and "Dark pool" in code.value for code in app.code)
        if page == "Money Map":
            assert len(app.get("plotly_chart")) == 1
            app.selectbox(key="map_universe").set_value("Watchlist").run()
            assert not app.exception and len(app.get("plotly_chart")) == 1
        if page == "Options Flow":
            app.toggle(key="flow_contract_cvd").set_value(True).run()
            assert not app.exception and len(app.get("plotly_chart")) >= 3
    app.radio(key="desk_page").set_value("History").run()
    next(widget for widget in app.get("segmented_control") if widget.key == "history_workspace").set_value("Event replay").run()
    assert not app.exception
    assert app.selectbox(key="replay_type").value == "Large options prints"
    app.selectbox(key="replay_type").set_value("Range-break triggers").run()
    assert not app.exception
