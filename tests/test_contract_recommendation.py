"""Broker-shaped chains and controlled common market paths for decision-time comparisons."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import numpy as np
import pytest

import contract_recommendation as engine
from tradytics_feed import Feed, Snapshot
from v3_options import YEAR, contract_spec, value

NOW = datetime(2026,11,24,16,0,tzinfo=timezone.utc)
SPOT = 100.
PLAN = {"status":"ENTER LONG","direction":1,"tp1":101., "stop":99.,"hold":10,"setup":"Breakout","regime":"Expanding",
        "movement_v3":{"state":"BULLISH IGNITION","side":1,"preview":False}}


def quote_feed(dte, ctx, side, *, source="Webull", spot=SPOT):
    expiry=(NOW.date()+timedelta(days=dte))
    exp=expiry.isoformat()
    rows=[]
    for strike in (99,100,101):
        code=f"SPY{expiry:%y%m%d}{'C' if side=='call' else 'P'}{strike*1000:08}"
        row=dict(contract=code,type=side,strike=strike,expiry=exp,symbol="SPY",multiplier=100,
                 standard=True,updated_at=ctx.now.isoformat(),delay_minutes=0,ask_size=5,iv=.24)
        spec=contract_spec(row,"SPY")
        fair=float(value(spec,spot,(spec.expires-ctx.now).total_seconds()/YEAR,.24,.04,0))
        mid=round(fair,3)
        row.update(bid=max(.005,mid-.005),ask=max(.015,mid+.005))
        rows.append(row)
    return Feed(Snapshot(rows,{"symbol":"SPY","expiry":exp,"delay_minutes":0,"source":source},ctx.now))


@pytest.fixture
def inputs(monkeypatch):
    ctx=SimpleNamespace(now=NOW,ticker="SPY",session=NOW.date(),dte=0,preview=False,paused=False,
                        chart_quote={"at":NOW,"price":SPOT},option_max_age=120,webull_store=None,journal=None)
    dtes=(0,1,7)
    dates=[{"symbol":"SPY","expiry":(ctx.session+timedelta(days=d)).isoformat()} for d in dtes]
    listing=Feed(Snapshot(dates,{"symbol":"SPY"},NOW))
    monkeypatch.setattr(engine,"contract_expirations",lambda ctx:listing)
    def get_chain(c,exp,right,*,broker_only=False):
        assert broker_only
        return quote_feed((datetime.fromisoformat(exp).date()-ctx.session).days,c,right),"Webull"
    monkeypatch.setattr(engine,"contract_chain",get_chain)
    def scenarios(candles,now,plan,spot,horizon,opened):
        side=plan["direction"]
        change=np.tile(np.linspace(0,1.1,horizon+1),(100,1))
        change[-10:]*=-1
        prices=spot+side*change
        return dict(close=prices,low=np.minimum(prices[:,1:],prices[:,:-1]),high=np.maximum(prices[:,1:],prices[:,:-1])),300,3
    monkeypatch.setattr(engine,"_market_paths",scenarios)
    monkeypatch.delenv('QD_CONTRACT_RANK_MODE',raising=False)
    return ctx,get_chain


def test_same_scenario_multi_dte_sources_risk_and_auditable_returns(inputs):
    ctx,_=inputs
    result=engine.recommend(ctx,PLAN,{})
    assert result["status"]=="qualified" and not result["simulated"]
    assert result["arrival"]==(NOW+timedelta(minutes=10)).isoformat()
    assert set(v["dte"] for v in result["candidates"])=={0,1,7}
    assert len(result["candidates"])==9 and len(result["alternatives"])==3
    item=result["selected"]
    assert item["entry"]==item["ask"]>item["bid"]
    assert item["target_premium"]>item["stop_premium"] and item["target_net"]>0>=item["stop_net"]
    assert item["cost"]==round((100*item["ask"]+.65)*item["quantity"],2) and item["cost"]<=1000
    assert item["max_loss"]==item["cost"] and item["quantity"]<=2 and item["expected_net"]>0
    assert all(0<=v<=1 for v in item["features"].values()) and item["score"]<=100
    assert item["probability"] is None and item["target_before_stop"]==.90
    assert item["premium_path"][-1]["spot"]==101 and item["premium_path"][0]["minutes"]==0
    assert result["expires_at"]==(NOW+timedelta(seconds=30)).isoformat()
    assert json.loads(json.dumps(result,allow_nan=False))["selected"]["contract"]==item["contract"]


@pytest.mark.parametrize("mode",["risk_adjusted","net_profit","net_return","conservative"])
def test_paired_bearish_mode_and_no_retroactive_probability(inputs,mode):
    ctx,_=inputs
    result=engine.recommend(ctx,{**PLAN,"direction":-1,"status":"ENTER SHORT","tp1":99,"stop":101,
                                "movement_v3":{"state":"BEARISH IGNITION","side":-1,"preview":False}}, {}, mode)
    assert result["selected"]["type"]=="put" and result["status"]=="qualified"
    assert result["selected"]["quantity"] in (1,2)
    assert engine.recommend(ctx,PLAN,{},"probability")["selected"] is None


def test_rejects_conflict_or_missing_scope_even_if_model_would_favor_it(inputs):
    ctx,_=inputs
    for changes in ({"direction":-1},{"status":"AVOID"},{"blocks":["Stale price"]},{"data_reasons":["Missing flow"]},
                    {"stop":101},{"tp1":99},{"hold":200},{"demo":True}):
        assert engine.recommend(ctx,{**PLAN,**changes},{})["selected"] is None
    developing=engine.recommend(ctx,{**PLAN,"status":"GET READY"},{})
    assert developing["status"]=="developing"
    ctx.chart_quote["at"]-=timedelta(seconds=31)
    assert engine.recommend(ctx,PLAN,{})["selected"] is None


def test_bad_quotes_unprofitable_paths_day_capacity_and_size(inputs,monkeypatch):
    ctx,get_chain=inputs
    assert engine.recommend(ctx,PLAN,{},remaining_loss=0)["selected"] is None
    assert engine.recommend(ctx,PLAN,{},remaining_loss=.01)["selected"] is None
    def invalid(c,e,r,**kw):
        feed,label=get_chain(c,e,r,**kw)
        feed.rows[0]["updated_at"]=(ctx.now-timedelta(minutes=2)).isoformat()
        feed.rows[1]["ask_size"]=0
        feed.rows[2]["delay_minutes"]=15
        return feed,label
    monkeypatch.setattr(engine,"contract_chain",invalid)
    result=engine.recommend(ctx,PLAN,{})
    reasons=result["diagnostics"]["rejected"]
    assert result["selected"] is None and "Stale quote" in reasons and "Delayed quote" in reasons
    monkeypatch.setattr(engine,"contract_chain",get_chain)
    def flat(c,now,p,s,m,o):
        close=np.full((100,m+1),s)
        return dict(close=close,low=close[:,1:],high=close[:,1:]),250,2
    monkeypatch.setattr(engine,"_market_paths",flat)
    assert engine.recommend(ctx,PLAN,{})["selected"] is None


def test_dual_touch_is_adverse_and_session_close_rejects(inputs):
    ctx,_=inputs
    paths=dict(close=np.array([[100,100.4,100.]]),low=np.array([[98.9,99.5]]),high=np.array([[101.2,100.5]]))
    minutes,spots,p=engine._terminal_market(paths,100,1,101,99)
    assert minutes.tolist()==[1] and spots.tolist()==[99] and p==0.
    ctx.now=NOW.replace(hour=20,minute=55)
    ctx.chart_quote.update(at=ctx.now)
    assert "session" in engine.recommend(ctx,PLAN,{})["reason"]


def test_live_selection_skips_unknown_delay_then_uses_verified_schwab(inputs):
    from unittest.mock import Mock
    from contract_sources import contract_chain
    ctx,_=inputs
    ctx.has_webull_options=True
    weak=quote_feed(0,ctx,"call")
    weak.snapshot.meta.pop("delay_minutes")
    for r in weak.rows:r.pop("delay_minutes")
    good=quote_feed(0,ctx,"call",source="Schwab")
    ctx.schwab_store=SimpleNamespace(get=Mock(return_value=good))
    ctx.option_chain=Mock(return_value=(weak,"Webull"))
    assert contract_chain(ctx,str(NOW.date()),"call",broker_only=True)==(good,"Schwab")
    assert ctx.option_chain.call_count==1


def test_schwab_supplements_limited_webull_window_with_unique_series(inputs,monkeypatch):
    ctx,get_chain=inputs
    def limited(c,exp,right,**kw):
        full,_=get_chain(c,exp,right,**kw)
        return Feed(Snapshot(full.rows[:1],{**full.snapshot.meta,"discovered":70,"requested":20},NOW)),"Webull"
    def additional(name,*,symbol,expiry,right,paused):
        return get_chain(ctx,expiry,right,broker_only=True)[0]
    ctx.schwab_store=SimpleNamespace(get=additional)
    monkeypatch.setattr(engine,"contract_chain",limited)
    result=engine.recommend(ctx,PLAN,{})
    assert {r["source"] for r in result["candidates"]}=={"Webull","Schwab"}
    assert len(result["candidates"])==len({r["contract"] for r in result["candidates"]})==9
    assert len(result["diagnostics"]["listings"])==6


def test_probability_ranking_uses_only_prior_validated_cohort(inputs,monkeypatch):
    import rank_research
    ctx,_=inputs
    ctx.journal=object()
    calls=[]
    def lookup(j,s,t,v,*,dte,regime,setup):
        calls.append((dte,regime,setup))
        return .67 if dte==7 else None
    monkeypatch.setattr(rank_research,"probability",lookup)
    result=engine.recommend(ctx,PLAN,{},mode="probability")
    assert result["selected"]["dte"]==7 and result["selected"]["probability"]==.67
    assert len(calls)==len(set(calls)) and all((r,s)==("Expanding","Breakout") for _,r,s in calls)
    assert "cohort" in result["reason"]


def test_replay_excludes_late_receipts_even_with_earlier_quote_timestamps(inputs,monkeypatch):
    ctx,chain=inputs
    def late(*args,**kwargs):
        f,source=chain(*args,**kwargs)
        return Feed(Snapshot(f.rows,f.snapshot.meta,NOW+timedelta(seconds=5))),source
    monkeypatch.setattr(engine,"contract_chain",late)
    result=engine.recommend(ctx,PLAN,{})
    assert not result["selected"] and not result["candidates"]
    assert all("decision timestamp" in s["issue"] for s in result["diagnostics"]["listings"])
