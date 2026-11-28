import json
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from chart_studies import (ANCHOR, BANDS, MACD, MINUTE, MTF, OPENING, PIVOTS, RSI, close_studies, completed_five_minute_ema,
                           heikin_ashi, intraday_levels, swing_levels, trend_strip)
from market_session import rth_bounds


DAY = date(2026, 11, 27)
OPEN, CLOSE = rth_bounds(DAY)


def bars(n):
    return [{"time_utc": (OPEN + i * MINUTE).isoformat(), "close": 100+i, "high": 101+i, "low": 99+i,
             "open": 100+i, "closed": True, "volume": 10} for i in range(n)]


def test_opening_range_and_record_volume_anchor_use_only_known_minutes():
    minute = bars(17)
    minute[10]["volume"] = 20
    minute[-1]["forming"] = True
    levels = intraday_levels(minute, DAY, OPEN + 18*MINUTE)
    assert levels[OPEN + 10*MINUTE] == (None, None, 104.5)
    assert levels[OPEN + 11*MINUTE] == (None, None, 110)
    assert levels[OPEN + 15*MINUTE][:2] == (115, 99)
    assert len(levels) == 16
    partial = intraday_levels(minute[1:], DAY, OPEN + 18*MINUTE)
    assert all(v == (None, None, None) for v in partial.values())
    minute[2]["volume"] = None
    assert intraday_levels(minute, DAY, OPEN + 16*MINUTE)[OPEN + 16*MINUTE] == (115, 99, None)


def test_higher_timeframe_warmup_close_and_early_close():
    minute = bars(210)
    states = dict(trend_strip(minute, DAY, OPEN + 4*MINUTE))
    assert states[5] == "Waiting for close" and states[3] == "Warming up"
    missing = dict(trend_strip(minute[:-25], DAY, CLOSE))
    assert missing[5] == "Missing close"
    assert dict(trend_strip(minute, DAY, CLOSE + 30*MINUTE))[5] == "Up"
    result = completed_five_minute_ema(minute, DAY, OPEN + 109*MINUTE)
    assert OPEN + 104*MINUTE not in result
    assert result[OPEN + 105*MINUTE] == result[OPEN + 109*MINUTE]
    assert OPEN + 110*MINUTE not in result


def test_rsi_bands_macd_and_gap_warmup():
    minute = bars(55)
    readings = close_studies(minute, MINUTE, True, RSI)
    assert readings["RSI"][:14] == [None]*14 and readings["RSI"][14] == 100
    assert readings["Upper band"][19] == pytest.approx(109.5 + 2*(33.25**.5))
    assert readings["Lower band"][18] is None
    macd = close_studies(minute, MINUTE, False, MACD)
    assert macd["MACD"][25] == pytest.approx(7)
    assert macd["Signal"][32] is None and macd["Signal"][33] == pytest.approx(7)
    gapped = close_studies(minute[:20]+minute[21:], MINUTE, False, RSI)
    assert gapped["RSI"][20:34] == [None]*14


def test_chart_workspace_normal_routes(monkeypatch, overview_spec):
    for name in ("WEBULL_APP_KEY", "WEBULL_APP_SECRET", "TRADYTICS_API_KEY", "DEFAULT_TICKER"):
        monkeypatch.delenv(name, raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"terminal.py"), default_timeout=35).run()
    def traces():
        return {t["name"]: t for t in overview_spec(app)["data"]}
    app.button(key="desk_map").click().run()
    assert app.session_state["desk_page"] == "Positioning"
    assert app.segmented_control(key="positioning_view").value == "Heat map"
    for page in ("Movement Detector","Trade Plan","Overview"):
        app.selectbox(key="desk_page").set_value(page).run()
        assert not app.exception and app.session_state["desk_page"] == page
    app.segmented_control(key="overview_timeframe").set_value("3m").run()
    app.multiselect(key="chart_session_studies").set_value([OPENING, ANCHOR, BANDS, MTF]).run()
    app.selectbox(key="chart_oscillator").set_value(RSI).run()
    assert not app.exception
    assert {"Upper band", "Lower band", "Opening high", "Opening low", "Anchored VWAP estimate",
            "Completed 5m EMA 21", "RSI"} <= traces().keys()
    assert traces()["RSI"]["yaxis"] != traces()["SPY"]["yaxis"]
    for style, base_type in (("Hollow up candles", "candlestick"), ("OHLC bars", "ohlc"),
                             ("Close line", "scatter"), ("Heikin-Ashi", "candlestick")):
        app.selectbox(key="chart_price_style").set_value(style).run()
        assert not app.exception
        assert traces()["SPY · Heikin-Ashi" if style == "Heikin-Ashi" else "SPY"]["type"] == base_type
    app.button(key="watch_SPX").click().run()
    assert "Anchored VWAP estimate" not in traces()
    app.selectbox(key="chart_oscillator").set_value(MACD).run()
    assert {"MACD", "Signal"} <= traces().keys()
    app.segmented_control(key="overview_timeframe").set_value("1D").run()
    assert not app.exception and app.multiselect(key="chart_daily_studies").options == [BANDS, PIVOTS]

def test_swing_confirmation_and_synthetic_prices():
    minute = bars(7)
    minute[2]["high"] = 122
    minute[2]["low"] = 88
    levels = swing_levels(minute, MINUTE)
    assert levels["Swing resistance"][:5] == [None]*4 + [122]
    assert levels["Swing support"][:5] == [None]*4 + [88]
    assert swing_levels(minute[:3] + minute[4:], MINUTE)["Swing support"] == [None]*6
    synthetic = heikin_ashi(minute, MINUTE)
    assert synthetic[2]["close"] == pytest.approx((102+122+88+102)/4)
    assert synthetic[3]["open"] == (synthetic[2]["open"]+synthetic[2]["close"])/2
