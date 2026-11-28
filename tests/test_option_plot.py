from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from market_session import ET, rth_bounds
from tradytics_feed import Feed, Snapshot
from v3_options import contract_spec, scenario, timeline
from v3_views import chart_history, combined, comparison_label


def option_fixture():
    day = date(2026,11,27)
    now = rth_bounds(day)[0] + timedelta(minutes=42)
    expiry = date(2026,12,4)
    row = dict(contract="SPXW261204C07000000", strike=7000., symbol="SPX", expiry=str(expiry),
               type="call", bid=40., ask=42., iv=.154, delta=.54, gamma=.031, updated_at=now.isoformat())
    spec = contract_spec(row,"SPX")
    feed = Feed(Snapshot([row],dict(symbol="SPX",expiry=str(expiry)),now))
    return day, now, row, spec, feed


def test_history_is_point_in_time_and_preview_is_isolated():
    day, now, row, spec, feed = option_fixture()
    before = now - timedelta(minutes=2)
    def recorded(stamp, preview=False, samples=None):
        return dict(origin=stamp.isoformat(),payload=dict(
            signal=dict(symbol="SPX",session=str(day),preview=preview,source="Webull"),
            quote_samples=samples or [dict(row, updated_at=stamp.isoformat())],
        ))
    records = [recorded(now+timedelta(seconds=30)),recorded(before),recorded(before-timedelta(minutes=1),True),
               recorded(before-timedelta(minutes=3),samples=[dict(row,contract="SPY…")])]
    journal = SimpleNamespace(records=lambda *a,**k:records)
    ctx = SimpleNamespace(preview=False,ticker="SPX",now=now,session=day,paused=False,option_max_age=90,journal=journal)
    pts = chart_history(ctx,spec,row,feed)
    assert [p["at"] for p in pts] == [before,now]
    assert pts[0]["premium"] == 41. and pts[0]["iv"] == pytest.approx(15.4)
    ctx.preview=True
    ctx.journal=SimpleNamespace(records=lambda *a,**k:pytest.fail("Preview read production journal"))
    assert len(chart_history(ctx,spec,row,feed))==1


@pytest.mark.parametrize("normalize",[True,False])
def test_combined_expiry_observations_and_costs(normalize):
    day, now, row, spec, feed = option_fixture()
    arrival=now+timedelta(hours=1)
    times=timeline(now,spec.expires,60,arrival=arrival)
    p,r,g=scenario(spec,now,times,7000.,7010.,arrival,.154,.15,.04,0.,extended=True)
    history=[dict(at=now-timedelta(minutes=3),premium=41.,theta=-.2,iv=15.4),
             dict(at=now,premium=42.,theta=-.19,iv=15.4)]
    fig=combined(spec,times,p,r,g,42.,1,.65,normalize,False,arrival,["vanna","charm"],
                 observed=history,spread=2.)
    modeled=[tr for tr in fig.data if tr.line.dash=="dash"]
    observed=[tr for tr in fig.data if tr.line.dash=="solid"]
    assert len(modeled)==6 and len(observed)==2
    assert modeled[0].x[0]==now and modeled[0].x[-1]==spec.expires
    assert fig.layout.xaxis.range[0]==history[0]["at"] and fig.layout.xaxis.range[-1]==spec.expires
    assert len(fig.layout.shapes)>3  # includes overnight/weekend shading
    assert all(tr.x[-1]<=now for tr in observed)
    assert np.isnan(observed[0].y[1]) # gap between genuine observations
    assert modeled[0].customdata[0][2]==pytest.approx((max(0,g["premium"][0]-1)-42)*100-1.3)
    assert fig.layout.hovermode=="x unified" and "EST" in modeled[1].customdata[0][0]
    assert len(set(tr.yaxis for tr in modeled))==(1 if normalize else 6)
    assert comparison_label("SPX",spec,now)=="SPX 7000 Call · 7DTE"


def test_latest_learning_comparison_uses_common_outcomes():
    from v3_views import detector_comparison
    def method(sent,correct):
        return dict(warning_origins=sent,correct=correct,precision=correct/sent if sent else None,
                    recall=correct/5,median_seconds_to_touch=75 if correct else None)
    recent={"scope":"2026-11-27","payload":{"SPY":{"recorded":240,"horizons":[
        {"horizon":5,"outcomes":{"up":2,"down":3,"ambiguous":4,"neither":20},
         "comparison":{"v3_ignition":method(3,2),"baseline_move_watch":method(0,0)}}]}}}
    old={"scope":"2026-11-25","payload":{"SPY":{"recorded":100,"horizons":[]}}}
    view=detector_comparison([old,recent],"SPY",5)
    assert view["session"]=="2026-11-27" and view["comparable"]==25 and view["directional"]==5
    assert view["rows"][0]["precision"]==pytest.approx(2/3)
    assert view["rows"][1]["precision"] is None
    assert detector_comparison([recent],"SPX",5) is None
    assert not detector_comparison([recent],"SPY",10)["comparable"]


def test_learning_preview_never_reads_worker_journal():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string("""
from types import SimpleNamespace
from v3_views import learning
class PrivateJournal:
    def records(self,*a,**kw):
        raise AssertionError("Preview accessed the worker journal")
learning(SimpleNamespace(preview=True,journal=PrivateJournal()))
""").run()
    assert not app.exception and "synthetic" in app.info[0].value.lower()


def test_learning_excludes_synthetic_reports_and_identifies_recorded_build():
    from v3_views import detector_comparison
    live = {"scope": "2026-11-23", "payload": {"SPY": {"recorded": 4},
            "research_config": {"version": "3.0-shadow.2", "mode": "Balanced"}}}
    synthetic = {"scope": "2026-11-24", "payload": {"SPY": {"recorded": 50}, "preview": True}}
    view = detector_comparison([synthetic, live], "SPY", 5)
    assert view["scans"] == 4
    assert view["config"] == live["payload"]["research_config"]


def test_combined_mobile_labels_keep_close_markers_separate_and_units_visible():
    from v3_views import _plot_time_axis
    import plotly.graph_objects as go

    _,now,_,spec,_=option_fixture()
    arrival=now+timedelta(minutes=5)
    times=timeline(now,spec.expires,arrival=arrival)
    prices,remaining,stats=scenario(spec,now,times,7000.,7010.,arrival,.154,.15,.04,0.)
    fig=combined(spec,times,prices,remaining,stats,42.,1,.65,False,False,arrival,[])
    labels={a.text:a for a in fig.layout.annotations}
    assert labels["Target"].yshift>labels["Now"].yshift
    assert labels["Expiry"].xanchor=="right"
    assert fig.layout.legend.xref==fig.layout.legend.yref=="container"
    assert fig.layout.legend.entrywidthmode=="pixels" and fig.layout.legend.entrywidth==50
    assert fig.layout.yaxis.tickprefix=="$" and fig.layout.yaxis4.tickprefix=="Θ"
    assert fig.layout.yaxis2.tickprefix=="Δ" and fig.layout.yaxis3.tickprefix=="Γ"
    assert len(fig.layout.xaxis.ticktext)==3 and all("<br>" not in t for t in fig.layout.xaxis.ticktext)
    intraday=go.Figure()
    _plot_time_axis(intraday,now,arrival)
    assert len(intraday.layout.xaxis.ticktext)==4
    assert all(len(t)==5 for t in intraday.layout.xaxis.ticktext)
