from datetime import timedelta

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from contract_calibration import calibrate_quote
from contract_sources import contract_chain
from contract_sweep import price_figure, price_range, sweep_grid
from contract_workspace import listed_chain
from terminal_demo import DEMO_AT, DemoStore
from terminal_views import Desk
from v3_options import YEAR, value


def selected():
    ctx=Desk("SPY",DEMO_AT,DemoStore("SPY"),True,False,0,True)
    quote=ctx.chart_quote
    feed,_=contract_chain(ctx,str(ctx.session),"call")
    spec,row,_=listed_chain(ctx,feed,str(ctx.session),"call",quote["price"])[0]
    return ctx,quote,spec,row


def test_screenshot_quote_reproduction_and_synchronized_midpoint():
    ctx,q,spec,row=selected()
    assert row["bid"]==1.56 and row["ask"]==1.62 and row["iv"]==.194
    c=calibrate_quote(spec,row,q["price"],q["at"],ctx.now,.04,0.)
    assert c.synchronized and c.age_gap==20
    assert c.provider_fair==pytest.approx(.64263,abs=.001)
    assert c.iv==c.midpoint_iv==pytest.approx(.3515,abs=.001)
    mark=value(spec,q["price"],(spec.expires-ctx.now).total_seconds()/YEAR,c.iv,.04,0.)
    assert mark==pytest.approx(1.59,abs=.001)
    aligned=calibrate_quote(spec,{**row,"iv":c.iv},q["price"],q["at"],ctx.now,.04,0.)
    assert aligned.basis=="Chain IV prices inside the spread"


@pytest.mark.parametrize("changes,seconds", [({},45),({"bid":2.,"ask":1.},0),({"updated_at":"bad"},0),
                                               ({"bid":.01,"ask":1.},0),({},-4)])
def test_midpoint_calibration_rejects_price_or_clock_defects(changes,seconds):
    ctx,q,spec,row=selected()
    c=calibrate_quote(spec,{**row,**changes},q["price"],ctx.now-timedelta(seconds=seconds),ctx.now,.04,0.)
    assert c.iv is None and not c.synchronized


def test_price_axis_has_fixed_clock_payoff_and_same_target_time_value():
    ctx,q,spec,row=selected()
    spot=q["price"]; target=spec.strike+2; at=ctx.now+timedelta(minutes=7)
    lm={"Invalidation":spot-1.25}
    lo,hi=price_range(spot,spec.strike,target,7*60/YEAR,.2,lm.values())
    grid=sweep_grid(lo,hi,.1,(spot,spec.strike,target,*lm.values()))
    f=price_figure(spec,grid,spot,target,at,.2,.04,0.,row["ask"],2,.65,.06,levels=lm)
    assert all(np.array_equal(tr.x,grid) for tr in f.data)
    assert [tr.name for tr in f.data]==["Premium ($)","Delta","Gamma","Theta ($/day)"]
    expected=float(value(spec,target,(spec.expires-at).total_seconds()/YEAR,.2,.04,0.))
    i=list(grid).index(target)
    assert f.data[0].y[i]==pytest.approx(expected)
    assert f.data[0].customdata[i][1]==pytest.approx((max(0.,expected-.03)-row["ask"])*200-2*.65*2)
    assert set(x.text for x in f.layout.annotations)=={"Spot","Target","Strike","Invalidation"}
    assert all(tr.line.color for tr in f.data) and f.layout.hovermode=="x unified"
    exp=price_figure(spec,grid,spot,target,spec.expires,.2,.04,0.,1.,1,.5,.1,extra=["iv"])
    assert exp.data[0].y[i]==pytest.approx(2.) and "IV" not in exp.data[0].hovertemplate
    assert all(np.isnan(tr.y).all() for tr in exp.data[1:])


def test_sweep_limits_reject_oversized_or_reversed_ranges():
    with pytest.raises(ValueError):sweep_grid(100,100)
    with pytest.raises(ValueError):sweep_grid(1,1000,.001)
    assert np.array_equal(sweep_grid(1.,2.,.4,[1.55]),[1.,1.4,1.55,1.8,2.])


def test_trade_plan_link_selects_exact_contract_with_order_snapshot_defaults():
    app=AppTest.from_string("""
from datetime import timedelta
from contract_workspace import open_recommendation,contracts
from terminal_demo import DEMO_AT,DemoStore
from terminal_views import Desk
import streamlit as st
d=Desk("SPY",DEMO_AT,DemoStore("SPY"),True,False,0,True)
if 'launched' not in st.session_state:
    from contract_sources import contract_chain
    from contract_workspace import listed_chain
    rows=listed_chain(d,contract_chain(d,str(d.session),"call")[0],str(d.session),"call",d.chart_quote["price"])
    spec=rows[1][0]
    st.session_state['expected']=spec.code
    open_recommendation("SPY",spec.expires.date().isoformat(),"call",spec.code,782.,DEMO_AT+timedelta(minutes=10),2.45,preview=True)
    st.session_state['launched']=True
contracts(d)
""",default_timeout=30).run()
    assert not app.exception
    code=app.session_state['expected'];link=app.session_state['opc_link']
    assert link['code']==code and not link['pending']
    assert app.number_input(key="opc_target_"+code).value==782.
    assert app.number_input(key="opc_entry_"+code).value==2.45
    assert len(app.get("plotly_chart"))==2
