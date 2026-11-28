from datetime import datetime, timedelta, timezone

import pytest

from terminal_charts import price_figure


def bar(stamp, close):
    return dict(time_utc=stamp, open=close-.1, high=close+.5, low=close-.4, close=close, volume=150)


def test_window_has_stable_x_values_and_keeps_actual_clock_and_market_gaps():
    first = datetime(2026,11,27,14,30,tzinfo=timezone.utc)
    # Friday closes at 13:00 ET; the next x slot belongs to Monday's opening.
    monday = datetime(2026,11,30,14,30,tzinfo=timezone.utc)
    bars = [bar(first+timedelta(minutes=n),400+n/100) for n in (207,208,209)]
    bars.extend([bar(monday+timedelta(minutes=n),403+n/10) for n in (0,1,3)])
    initial = price_figure(bars[:-1],"SPY","1m",show_ema=True)
    refreshed = price_figure(bars[1:],"SPY","1m",show_ema=True)
    assert list(initial.data[0].x[1:]) == list(refreshed.data[0].x[:-1])
    xs = refreshed.data[0].x
    assert xs[2]-xs[1]==1  # Friday last candle → Monday first candle.
    assert xs[-1]-xs[-2]==2  # A genuinely missing minute still occupies its slot.
    assert refreshed.layout.meta["labels"][-1].endswith("09:33")
    assert refreshed.layout.meta["initial_x"][1] == xs[-1]+5


def test_viewport_keeps_backscroll_and_values_of_nearby_levels():
    first = datetime(2026,11,25,14,30,tzinfo=timezone.utc)
    bars = [bar(first+timedelta(minutes=n),400+n/100) for n in range(200)]
    f = price_figure(bars,"SPY","1m",levels=[("Call wall",401.12,"red"),("Zero gamma",401.13,"blue")])
    assert len(f.data[0].x)==200
    assert f.layout.xaxis.range[0]==pytest.approx(f.data[0].x[-105]-.8)
    assert f.layout.uirevision=="SPY:1m"
    labels=[a for a in f.layout.annotations if a.name=="price-level"]
    assert [(a.y,a.xref) for a in labels]==[(401.12,"x domain"),(401.13,"x domain")]
    assert all(a.x>.9 and f"{a.y:g}" in a.text for a in labels)
