"""Decision-facing option workspace regressions using a labeled, deterministic preview."""
from dataclasses import replace
from datetime import timedelta

import pytest
from streamlit.testing.v1 import AppTest

from contract_sources import contract_chain
from contract_workspace import auto_scenario, contract_label, listed_chain, moneyness, number
from terminal_demo import DEMO_AT, DemoStore
from terminal_views import Desk


def preview(ticker="SPY"):
    return Desk(ticker, DEMO_AT, DemoStore(ticker), True, False, 0, True)


def test_auto_price_uses_directional_forecasts_and_qualified_plan():
    levels = {"forecasts": {"5": {"upper": 105, "lower": 94}}}
    assert auto_scenario({}, 100)[:2] == (100, 5)
    for state in ("CONFLICTING PRESSURE", "INSUFFICIENT DATA", "FAILED MOVE", "EXHAUSTION"):
        assert auto_scenario({"movement_v3": dict(levels, side=1, state=state)}, 100)[0] == 100
    for side, state, expected in [(-1,"BEARISH IGNITION",94),(1,"BULLISH PRESSURE",105)]:
        assert auto_scenario({"movement_v3": dict(levels, side=side, state=state)},100)[:2] == (expected,5)
    plan = {"direction": -1, "tp1":99, "confirmed":True, "status":"ENTER SHORT",
            "price_context_ready":True, "hold":9, "movement_v3":dict(levels,side=1,state="BULLISH EXPANSION")}
    assert auto_scenario(plan,100)[:2] == (99,9)
    assert auto_scenario(dict(plan, validation="Prospectively evaluated · TP1", model={"horizon":12,"p_tp1":.5}),100)[:2] == (99,12)
    assert auto_scenario(dict(plan,blocks=["Liquidity"]),100)[0] == 105


@pytest.mark.parametrize("ticker",["SPY","SPX","QQQ"])
@pytest.mark.parametrize("side",["call","put"])
def test_chain_default_nearest_strike_and_provider_freshness(ticker,side):
    ctx=preview(ticker)
    spot=ctx.chart_quote["price"]
    expiry=str(ctx.session)
    feed,_=contract_chain(ctx,expiry,side)
    rows=listed_chain(ctx,feed,expiry,side,spot)
    assert rows and all(spec.right==side and status=="Fresh quote" for spec,_,status in rows)
    assert contract_label(rows[0][0],ticker,ctx.session).startswith(ticker+" ")
    assert contract_label(rows[0][0],ticker,ctx.session).endswith(f"{side.title()} · 0DTE")
    assert rows[0][0].strike == min((s.strike for s,_,_ in rows), key=lambda strike:(abs(spot-strike),strike))
    assert all(s=="Stale quote" for _,_,s in listed_chain(replace(ctx,now=ctx.now+timedelta(minutes=10)),feed,expiry,side,spot))
    assert all(s=="Paused" for _,_,s in listed_chain(replace(ctx,paused=True),feed,expiry,side,spot))
    assert listed_chain(ctx,feed,"1970-01-01",side,spot)==[]


def test_readable_classification_and_missing_values():
    assert moneyness(100,"call",100.1,100)=="ATM"
    assert moneyness(98,"put",100.1,100)=="OTM"
    assert moneyness(98,"call",100.1,100)=="ITM"
    assert number(None,percent=True)=="Unavailable"
    assert number(.194,1,percent=True)=="19.4%"


def test_all_detail_panels_and_manual_analysis():
    # Drive the tracked expander state before the controls are created. AppTest
    # has no click helper for an expander; real components still render here.
    app=AppTest.from_string("""
import streamlit as st
from terminal_demo import DEMO_AT, DemoStore
from terminal_views import Desk
from contract_workspace import contracts
for key in ("opc_comparisons","opc_fullchain","opc_greek_grid","opc_arrivals","v3_mc","opc_iv_skew"):
    st.session_state[key]=True
contracts(Desk("SPX",DEMO_AT,DemoStore("SPX"),True,False,0,True))
""",default_timeout=60).run()
    assert not app.exception, [e.message for e in app.exception]
    assert len(app.get("plotly_chart")) >= 3
    assert app.caption and any("local Taylor estimate" in c.value for c in app.caption)
    assert any("Flat underlying" in c.value or "Trade Plan TP1" in c.value or "V3 directional" in c.value for c in app.caption)
    assert app.number_input(key="opc_qty").value==1
    app.segmented_control(key="opc_mode").set_value("Manual").run()
    target=next(i for i in app.number_input if i.label=="Underlying target ($)")
    target.set_value(target.value+1).run()
    assert not app.exception, [e.message for e in app.exception]
    assert any("Synthetic preview" in c.value for c in app.caption)
