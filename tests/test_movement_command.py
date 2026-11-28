"""Presentation should accurately reflect direction, eligibility, freshness and provenance."""
from datetime import datetime, timedelta, timezone

import pytest

from movement_command import checklist, comparable_rows, current_explanation, levels, plan_summary

AT = datetime(2026,11,27,15,7,tzinfo=timezone.utc)


def signal(**custom):
    return dict({"symbol":"SPX","session":"2026-11-27","mode":"Balanced","source":"Schwab",
                "version":"3.test","at":AT.isoformat(),"preview":False,"state":"BULLISH IGNITION",
                "regime":"Expanding","price":101.2,"side":1,"invalidation":98.,
                "features":{"breakout":1,"range_high":100.7,"net_pressure":.78,"footprint_pressure":-.8,
                            "net_change":2000,"net_acceleration":300,"footprint_units":"contracts","velocity":2.1,
                            "relative_volume":1.45,"gex":-10000,"realized_volatility":.003},
                "quality":{"cvd":"ES footprint proxy"},"freshness":{"cvd":AT.isoformat(),"netflow":AT.isoformat()},
                "scores":{"ignition":82,"bearish":21,"bullish":79},
                "cross_market":{"direction":-1,"symbol":"ESZ6","change_pct":-.4,"at":AT.isoformat()},
                "forecasts":{"5":{"upper":102.,"lower":100.},"15":{"upper":104.,"lower":98.}}},**custom)


def test_cues_preserve_wait_and_distinguish_sign_vs_direction():
    sig=signal()
    plan={"status":"WAIT","blocks":["Option spread exceeds limit."],"setup":"EMA pullback","direction":1}
    rows=checklist(sig,plan)
    assert [r.title for r in rows]==["Volatility regime","GEX and dealer positioning","Netflow and CVD",
                                     "Momentum and relative volume","Cross-market confirmation","Price structure",
                                     "Trade Plan qualification"]
    assert rows[1].status=="Developing" and "either direction" in rows[1].meaning
    assert rows[2].status=="Conflicting" and "ES footprint proxy" in rows[2].meaning
    assert rows[4].status=="Conflicting" and rows[4].observed.endswith("ET")
    assert rows[-1].value.startswith("WAIT") and "spread" in rows[-1].meaning
    assert levels(sig,plan)["target"]=="102.00 → 104.00"
    assert "100.70" in rows[5].next_check


def test_absent_inputs_are_unavailable_and_independent_bearish_setup_keeps_its_direction():
    sig=signal(cross_market=None,features={},side=0,state="INSUFFICIENT DATA",scores={},reason="Waiting for a close.")
    plan={"status":"ENTER SHORT","setup":"Range reversal","direction":-1,"tp1":95}
    rows=checklist(sig,plan)
    assert rows[2].status==rows[4].status=="Unavailable"
    assert plan_summary(plan)==("ENTER SHORT","Range reversal · Short",rows[-1].meaning)
    assert "Waiting for a close" in current_explanation(sig,None)
    assert levels(sig,plan)["target"]=="Unavailable"


def test_prior_score_comparison_requires_same_scope_and_version():
    current=signal()
    prev=signal(at=(AT-timedelta(minutes=1)).isoformat(),scores={"ignition":60})
    def row(s,scope="All DTE"):
        return {"origin":s["at"],"payload":{"signal":s,"scope":scope}}
    records=[row(prev),row(dict(prev,preview=True)),row(dict(prev,source="Tradytics")),row(prev,"Other"),
             row(dict(prev,version="old")),row(dict(prev,at=(AT+timedelta(minutes=1)).isoformat()))]
    assert comparable_rows(records,current,"All DTE")==records[:1]
    explanation=current_explanation(current,prev)
    assert "60 to 82" in explanation and "above" in explanation
    assert comparable_rows(records,dict(current,preview=True),"All DTE")==[]
