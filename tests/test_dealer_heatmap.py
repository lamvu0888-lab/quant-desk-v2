import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dealer_heatmap import heatmap_figure, nearby_levels, read_column, side_total, spot_reference
from terminal_demo import DEMO_AT, DemoStore
from terminal_views import Desk
from tradytics_feed import Feed, FeedError


def desk():
    return Desk("SPY", DEMO_AT, DemoStore("SPY"), True, False, 0, True)


def test_matrix_uses_actual_expiries_and_signed_values():
    ctx = desk()
    all_feed = ctx.get("exposure", symbol="SPY", greek="gamma", by="strike")
    cols = {"All": read_column(ctx, "gamma", None, all_feed),
            ctx.expiry: read_column(ctx, "gamma", ctx.expiry, ctx.exposure())}
    assert sum(cols["All"].values.values()) == all_feed.obj["total"]
    assert cols["All"].values != cols[ctx.expiry].values
    spot, _ = spot_reference(ctx, cols[ctx.expiry])
    fig = heatmap_figure(cols, ctx.expiry, spot, "gamma")
    assert fig.data[0].type == "heatmap"
    assert len(fig.data[0].z) == 17 and len(fig.layout.shapes) == 2
    assert {a.text for a in fig.layout.annotations} >= {"Brake", "Amplifier", "▶"}


@pytest.mark.parametrize("obj,meta", [
    ({"symbol": "QQQ"}, {}), ({"expiry": "2099-12-31"}, {}), ({}, {"symbol": "SPX"}),
    ({"unit": "shares"}, {}), ({}, {"delay_minutes": 15}), ({"time": (DEMO_AT-timedelta(minutes=8)).isoformat()}, {}),
    ({"time": (DEMO_AT+timedelta(seconds=1)).isoformat()}, {}), ({"rows": [{"strike": 100, "symbol": "QQQ", "net": 4}]}, {}),
    ({"rows": [{"strike": 100, "net": 2}, {"strike": 100, "net": 2}]}, {})])
def test_unusable_columns_are_suppressed(obj, meta):
    ctx = desk()
    snap = ctx.exposure().snapshot
    column = read_column(ctx, "gamma", ctx.expiry, Feed(replace(snap, data={**snap.data, **obj}, meta={**snap.meta, **meta})))
    assert column.issue and not column.values


def test_gaps_zero_totals_and_two_sided_markers():
    ctx = desk()
    snap = ctx.exposure().snapshot
    rows = [{"strike": k, "net": v} for k, v in [(98, -100), (99, -20), (100, 0), (101, 9), (102, None)]]
    active = read_column(ctx, "gamma", ctx.expiry, Feed(replace(snap, data={**snap.data, "rows": rows})))
    unavailable = read_column(ctx, "gamma", None, Feed(error=FeedError("COOLDOWN", "waiting")))
    assert nearby_levels(active.values, 100) == [(99, -20), (101, 9)]
    assert side_total(active.values, 100, False) == -120 and side_total(active.values, 100, True) == 9
    assert side_total(active.values, 101, True) is None
    assert side_total({99: 0}, 100, False) == 0
    fig = heatmap_figure({"All": unavailable, ctx.expiry: active}, ctx.expiry, 100, "vanna")
    assert all(row[0] is None for row in fig.data[0].z)
    assert fig.data[0].z[2][1] == 0
    assert {a.text for a in fig.layout.annotations} == {"−Vanna", "+Vanna", "▶"}
    assert heatmap_figure({"All": active}, "All", None, "gamma") is None
    assert read_column(replace(ctx, paused=True), "gamma", ctx.expiry, ctx.exposure()).issue == "Paused"


def test_dashboard_heatmap_and_price_markers(monkeypatch):
    for name in ("TRADYTICS_API_KEY", "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "DEFAULT_TICKER"):
        monkeypatch.delenv(name, raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "terminal.py"), default_timeout=40).run()
    def chart(key):
        return json.loads(next(c.proto.spec for c in app.get("plotly_chart") if key in c.proto.id))
    def has_marker():
        return any("Amplifier" in a["text"] or "Brake" in a["text"]
                   for a in chart("price_action_chart")["layout"]["annotations"])
    assert has_marker()
    app.toggle(key="price_gamma_markers").set_value(False).run()
    assert not has_marker()
    app.selectbox(key="desk_page").set_value("Positioning").run()
    app.segmented_control(key="positioning_view").set_value("Heat map").run()
    for symbol in ("SPY", "SPX", "QQQ"):
        app.button(key=f"watch_{symbol}").click().run()
        for kind in ("Gamma (GEX)", "Vanna (VEX)"):
            app.segmented_control(key="dealer_map_measure").set_value(kind).run()
            assert not app.exception
            fig = chart("dealer_exposure_matrix")
            assert len(fig["layout"]["xaxis"]["ticktext"]) == 4
            assert len(fig["layout"]["shapes"]) == 2
