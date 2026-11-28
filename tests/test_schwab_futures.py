from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from schwab_feed import SchwabMarketData, cadence
from schwab_futures import SampledFutures, comparison, context, fetch_context, leg_features, parse_quote, proxy_caption, side
from market_session import rth_bounds
from tradytics_feed import Feed, FeedError, Snapshot

OPEN=datetime(2026,11,23,14,30,tzinfo=timezone.utc)
MINUTE=timedelta(minutes=1)
META={"source":"Schwab","delay_minutes":0}

def quotes(at, total, *, es=100, mes=100, size=2, month="Z26"):
    def leg(root,last):
        clock=int(at.timestamp()*1000)
        return {"symbol":"/"+root,"assetMainType":"FUTURE","realtime":True,
                "reference":{"futureActiveSymbol":"/"+root+month,"futureMultiplier":50 if root=="ES" else 5},
                "quote":{"lastPrice":last,"lastSize":size,"tradeTime":clock,"quoteTime":clock,
                         "bidPrice":last-.25,"askPrice":last+.25,"totalVolume":total}}
    return {"/ES":leg("ES",es),"/MES":leg("MES",mes)}

def run(book, until=180, *, gap=(), months=None):
    for sec in range(0,until+1,3):
        if sec in gap:continue
        at=OPEN+timedelta(seconds=sec)
        body=quotes(at,200+sec*5,es=7000+sec/3*.25,mes=7000-sec/3*.25)
        if months:months(body,sec)
        last=book.observe(body,at)
    return Feed(Snapshot(last,dict(META),at))

def test_batched_provider_route_and_throttling(monkeypatch):
    api=SchwabMarketData(None)
    api._get=Mock(return_value=quotes(OPEN,400))
    clock=Mock()
    clock.now.return_value=OPEN
    monkeypatch.setattr("schwab_feed.datetime",clock)
    snap=api.get("futures_proxy")
    api._get.assert_called_once_with("/quotes",{"symbols":"/ES,/MES","fields":"quote,reference"})
    assert set(snap.data)=={"ES","MES"} and snap.meta==META
    assert api.refresh_delay(cadence("futures_proxy",snap))==3
    assert api.refresh_delay(cadence("quote",snap))==5

def test_quote_identity_entitlement_and_clock():
    bounds=rth_bounds(OPEN.date())
    body=quotes(OPEN,200)
    assert parse_quote(body,"ES",OPEN,bounds)
    for field, value in (("realtime", False), ("assetMainType","INDEX"),("symbol","/NQ")):
        alt=deepcopy(body)
        alt["/ES"][field]=value
        assert parse_quote(alt,"ES",OPEN,bounds) is None
    for field,val in (("futureActiveSymbol","/MESZ26"),("futureActiveSymbol","/ES"),("futureActiveSymbol","/ESH22"),("futureIsActive",False),("futureMultiplier",5)):
        alt=deepcopy(body)
        alt["/ES"]["reference"][field]=val
        assert parse_quote(alt,"ES",OPEN,bounds) is None
    assert parse_quote(body,"ES",OPEN+timedelta(seconds=30),bounds) is None
    assert parse_quote(body,"ES",OPEN-timedelta(seconds=1),bounds) is None

def test_ordered_prior_quotes_only_and_no_inside_tie_guess():
    prev=parse_quote(quotes(OPEN,200),"ES",OPEN,rth_bounds(OPEN.date()))
    same={**prev,"at":OPEN+timedelta(seconds=3)}
    assert side(prev,same)==0
    assert side(prev,{**same,"last":100.25})==1
    assert side(prev,{**same,"last":99.75})==-1
    # A future bid/ask cannot assign side to an unchanged last print.
    future={**prev,"bid":98,"ask":99,"bid_at":same["at"],"ask_at":same["at"]}
    assert side(future,same)==0

def test_partial_volume_is_never_promoted_to_observed_cvd_or_signed_in_full():
    at=OPEN+3*MINUTE
    feed=run(SampledFutures())
    proxy=context(feed,at,at)
    assert proxy["agreement"]=="Disagree" and proxy["composite"]==0 and proxy["quality"].startswith("Estimated")
    assert proxy["legs"]["ES"]["imbalance"]==1 and proxy["legs"]["MES"]["imbalance"]==-1
    assert proxy["paired_minutes"]==3 and proxy["composite_cvd"]==proxy["composite_acceleration"]==0
    es=proxy["legs"]["ES"]
    assert es["velocity"]==38 and es["observed_volume"]==285 and es["cvd"]==114
    assert es["change_pct"]>0 and proxy["legs"]["MES"]["change_pct"]<0
    assert es["acceleration"]==0 and es["persistence"]==1 and es["samples"]==19
    assert es["coverage"]==pytest.approx(38/285) and es["classified_notional"]>0
    assert "research only" in proxy_caption(proxy)
    assert "history" not in es
    history = context(feed, at, at, include_history=True)["legs"]["ES"]["history"]
    assert [point["delta"] for point in history] == [38, 38, 38]
    assert history[-1]["cvd"] == 114
    assert context(feed,at-MINUTE,at) is None
    assert context(Feed(feed.snapshot,FeedError("STALE","stale")),at,at) is None
    assert context(Feed(Snapshot(feed.obj,{**META,"preview":True},at)),at,at) is None
    assert context(feed,at,at+timedelta(seconds=15)) is None

def test_missing_and_discontinuous_windows_prevent_cvd_carry():
    at=OPEN+3*MINUTE
    result=context(run(SampledFutures(),gap=range(85,110)),at,at)
    assert result["legs"]["ES"]["cvd"]==38 and result["legs"]["ES"]["acceleration"] is None
    tracker=SampledFutures()
    run(tracker,60)
    stamp=OPEN+timedelta(seconds=63)
    msg=quotes(stamp,515)
    tracker.observe(msg,stamp)
    # Ensure duplicate trade polls do not increment signed contracts.
    before=dict(tracker.buckets["ES"][-1])
    tracker.observe(msg,stamp+timedelta(seconds=1))
    assert dict(tracker.buckets["ES"][-1])==before
    stamp=OPEN+timedelta(days=1)
    assert tracker.observe(quotes(stamp,1000),stamp)["ES"]["points"]==[]
    assert tracker.observe(quotes(OPEN-MINUTE,1000),OPEN-MINUTE)=={}

def test_roll_or_partial_entitlement_cannot_form_cross_month_composite():
    def roll_es(body,sec):
        if sec>=60:body["/ES"]["reference"]["futureActiveSymbol"]="/ESH27"
    at=OPEN+3*MINUTE
    proxy=context(run(SampledFutures(),months=roll_es),at,at)
    assert proxy["agreement"]=="Different delivery months" and proxy["composite"] is None
    assert proxy["legs"]["ES"]["cvd"]==76 and proxy["legs"]["MES"]["cvd"]==-114
    def no_es(body,sec):body["/ES"]["realtime"]=False
    partial=context(run(SampledFutures(),months=no_es),at,at)
    assert partial["primary"]=="MES" and partial["composite"] is None

def test_preview_paused_and_outside_session_do_not_acquire():
    base=dict(ticker="SPX",preview=False,paused=False,schwab_store=Mock(),session=OPEN.date(),now=OPEN)
    for diff in ({"preview":True},{"paused":True},{"ticker":"QQQ"},{"now":OPEN-MINUTE}):
        ctx=SimpleNamespace(**{**base,**diff})
        assert not fetch_context(ctx,OPEN)
    base["schwab_store"].get.assert_not_called()

def test_no_lookahead_proxy_comparison_uses_only_shared_labelled_origins():
    vals={"proxy_es":1,"proxy_mes":-.5,"proxy_composite":.25,"proxy_webull":-1}
    def row(outcome,label,**fields):
        return {"symbol":"SPX","outcome":outcome,"label":label,"features":{**vals,**fields}}
    results=comparison([row("up",1),row("neither",0),row("up",1,proxy_mes=None),row("ambiguous",0)])
    assert results["coverage"]["mes"]==2 and results["coverage"]["es"]==3
    assert results["paired_origins"]==2
    assert results["methods"]["es"]=={"calls":2,"correct":1,"directional_precision":.5}
    assert results["methods"]["webull"]["correct"]==0


def test_rolling_composite_accumulates_dimensionless_flow_and_is_carried_to_daily_report():
    def same_direction(body, sec):
        for key in ("bidPrice","askPrice","lastPrice"):
            body["/MES"]["quote"][key] = body["/ES"]["quote"][key]
        body["/MES"]["quote"]["lastSize"] = 3
    at=OPEN+3*MINUTE
    proxy=context(run(SampledFutures(),months=same_direction),at,at)
    assert proxy["composite"]==1 and proxy["composite_cvd"]==3 and proxy["agreement"]=="Aligned"
    assert proxy["legs"]["ES"]["cvd"]==114 and proxy["legs"]["MES"]["cvd"]==171
    from v3_research import summary
    rows=[dict(symbol="SPX",horizon=5,outcome="up",at=at.isoformat(),baseline_side=0,legacy_warning=0,
               state="QUIET",side=0,label=1,arrival_seconds=60,features=dict(proxy_es=1,proxy_mes=1,
               proxy_composite=1,proxy_webull=-1))]
    result=summary(rows,5)["futures_cvd"]
    assert result["paired_origins"]==1 and result["methods"]["composite"]["correct"]==1


def test_shared_trade_plan_carries_shadow_proxy_without_modifying_entry_gates():
    from dataclasses import replace
    from decision_flow import build_decision
    from test_range_reversal import schwab_workspace
    ctx, read, _ = schwab_workspace()
    before,_=build_decision(ctx,read)
    target=read["minute_close"]
    book=SampledFutures()
    for s in range(0,181,3):
        at=target-3*MINUTE+timedelta(seconds=s)
        result=book.observe(quotes(at,1000+s*5,es=7000+s*.25,mes=7000-s*.25),at)
    original=ctx.schwab_store
    feed=Feed(Snapshot(result,META,ctx.now))
    store=SimpleNamespace(client=SimpleNamespace(supports_futures_proxy=True),
                          get=lambda name,**kw:feed if name=="futures_proxy" else original.get(name,**kw))
    plan,_=build_decision(replace(ctx,schwab_store=store),read)
    assert plan["futures_cvd"]["agreement"]=="Disagree"
    assert plan["status"]==before["status"] and plan["movement_v3"]["scores"]==before["movement_v3"]["scores"]
    assert plan["movement_v3"]["features"]["proxy_es"]==1
    assert plan["movement_v3"]["cross_market"]["source"]=="Schwab"
    assert plan["cvd_proxy"] is None  # Existing observed-flow gate cannot consume an estimate.
