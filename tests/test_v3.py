"""Deterministic acceptance fixtures, deliberately separate from market validation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from market_session import ET, rth_bounds
from research_journal import Journal
from tradytics_feed import Feed, Snapshot
from v3_challenger import change_probability, delayed_online, lead_lag, replay_proposal
from v3_daily import daily
from v3_feedback import CURRENT, FIELDS, claim_stage, feedback, finish_stage, reserve, validate
from v3_movement import HORIZONS, MINUTE, VERSION, provisional, record, signal, transition
from v3_options import YEAR, block_simulation, calibrated_iv, contract_spec, greeks, research_rank, scenario, timeline, value
from v3_research import archive, benchmark, event_summary, matured, pricing_errors, summary

DAY=date(2026,11,27)
OPEN,CLOSE=rth_bounds(DAY)


def observations(n=25,sign=1):
    return [dict(time_utc=(OPEN+i*MINUTE).isoformat(),open=100.,high=100.1,low=99.9,close=100.,volume=100,closed=True)
            for i in range(n-1)]+[dict(time_utc=(OPEN+(n-1)*MINUTE).isoformat(),open=100,high=max(100,100+sign),
                                       low=min(100,100+sign),close=100+sign,volume=500,closed=True)]


def desk(direction=1):
    candles=observations(sign=direction)
    close=OPEN+len(candles)*MINUTE
    ctx=SimpleNamespace(now=close+timedelta(seconds=2),session=DAY,price_provider="Webull",ticker="SPY",scope="All",expiry=None,paused=False,preview=False)
    read=dict(minute_close=close,bars_ready=True,bars=Feed(Snapshot({}, {},close)),candles=candles)
    plan=dict(score=60,status="WAIT",regime="Normal")
    return ctx,read,plan


@pytest.mark.parametrize("direction,gex",[(1,3e8),(-1,-3e8)])
def test_independent_ignition_no_flow_no_entry(direction,gex):
    ctx,read,plan=desk(direction)
    s=signal(ctx,read,plan,Feed(),[],"observed",{"total":gex})
    assert s["state"].endswith("IGNITION") and s["side"]==direction
    assert s["quality"]["netflow"]=="unavailable" and s["quality"]["cvd"]=="unavailable"
    assert s["probability"] is None and plan["status"]=="WAIT"
    read["candles"].pop(-2)
    assert signal(ctx,read,plan,Feed(),[],"x",{})["state"]=="INSUFFICIENT DATA"


def test_pressure_conflict_and_stale():
    ctx,read,plan=desk()
    close=read["minute_close"]
    flow=[dict(time=(close-i*MINUTE).isoformat(),net_premium_usd=-10) for i in (3,2,1)]
    points=[dict(time=(close-i*MINUTE).isoformat(),delta=40) for i in (2,1,0)]
    snap=Feed(Snapshot(flow,{"interval":"1m"},ctx.now))
    s=signal(ctx,read,plan,snap,points,"ES footprint proxy",{})
    assert s["state"]=="CONFLICTING PRESSURE" and s["quality"]["cvd"]=="ES footprint proxy"
    ctx.now+=MINUTE
    assert signal(ctx,read,plan,snap,points,"observed",{})["state"]=="INSUFFICIENT DATA"


def test_recorder_persistence_and_provisional(monkeypatch,tmp_path):
    monkeypatch.setenv("QD_V3_RECORD","1");monkeypatch.setenv("QD_V3_PROVISIONAL","true")
    j=Journal(tmp_path/"j",dsn="")
    ctx,read,plan=desk()
    plan["movement_v3"]=signal(ctx,read,plan,Feed(),[],"none",{})
    key=record(j,ctx,read,plan)
    assert key and transition(j,ctx,plan,key) is None
    assert record(Journal(tmp_path/"j",dsn=""),ctx,read,plan) is None
    ctx.chart_quote={"at":ctx.now,"price":100,"source":"Webull"}
    assert provisional(j,ctx,read)["state"]=="OBSERVED QUOTE"
    assert provisional(j,ctx,read) is None
    ctx.now+=timedelta(seconds=10);ctx.chart_quote.update(at=ctx.now,price=102.)
    assert provisional(j,ctx,read)["state"]=="PROVISIONAL IMPULSE"
    assert len(j.records("SPY","v3_scan"))==1 and len(j.records("SPY","v3_transition"))==1


def contract(root="SPXW",day=DAY,strike=100.,side="call"):
    return dict(contract=f"{root}{day:%y%m%d}{side[0].upper()}{int(strike*1000):08d}",type=side,expiry=str(day),
                symbol="SPX" if root.startswith("SPX") else root,strike=strike,multiplier=100,iv=.2,bid=1.,ask=1.1)


def test_clocks_and_identity():
    for root in ("SPX","SPXO","SPXW","SPY","QQQ"):
        r=contract(root)
        s=contract_spec(r,r["symbol"])
        if root in ("SPX","SPXO"):
            assert s.expires==OPEN and s.last_trade.astimezone(ET).date()==date(2026,11,25)
        elif root=="SPXW":assert s.expires==s.last_trade==CLOSE
        else:assert s.expires==CLOSE and s.last_trade==CLOSE+timedelta(minutes=15)
    assert CLOSE.astimezone(ET).hour==13
    with pytest.raises(ValueError):contract_spec({**contract(),"multiplier":10},"SPX")
    with pytest.raises(ValueError):contract_spec(contract(day=date(2026,11,26)),"SPX")


@pytest.mark.parametrize("dte",[0,1,3,7,14,30,60,90])
def test_full_horizon(dte):
    start=OPEN+MINUTE
    end=CLOSE+timedelta(days=dte)
    seq=timeline(start,end)
    assert seq[0]==start and seq[-1]==end and seq==sorted(set(seq))
    if dte>2:assert any(x.date()>start.date() for x in seq)


def test_price_and_iv_greeks():
    spec=contract_spec(contract(),"SPX")
    assert value(spec,100,1,.2,.05,0)==pytest.approx(10.45058357,rel=1e-7)
    g=greeks(spec,100,1,.2,.05,0,True)
    assert g["delta"]==pytest.approx(.63683,abs=1e-5)
    assert g["gamma"]==pytest.approx(.018762,abs=1e-5)
    assert g["vega"]==pytest.approx(.37524,abs=.001)
    assert value(spec,105,0,.2)==5 and np.isnan(greeks(spec,105,0,.2)["gamma"])
    now=OPEN+MINUTE
    mid=value(spec,100,(spec.expires-now).total_seconds()/YEAR,.25)
    assert calibrated_iv(spec,100,now,float(mid),.04,0)==pytest.approx(.25,abs=1e-5)
    etf=contract_spec(contract("SPY",side="put"),"SPY")
    assert value(etf,50,1,.2)>=50-1e-7
    times=timeline(now,CLOSE,3,arrival=now+5*MINUTE)
    p,t,g=scenario(spec,now,times,100,105,now+5*MINUTE,.2,.3,.04,0,[(120,99.)])
    assert p[-1]==105 and t[-1]==0 and g["premium"][-1]==5
    assert research_rank([contract()],100,105,now+5*MINUTE,now,.04,0,10,.65,"SPX")==[]
    assert research_rank([contract()],100,105,now+5*MINUTE,now,.04,0,1000,.65,"SPX")[0]["conditional_pnl"]>0
    assert block_simulation(etf,observations(120),OPEN+121*MINUTE,100,.2,1)["blocks"]>=100


def fake_records(n=40):
    ctx,read,plan=desk()
    sig=signal(ctx,read,plan,Feed(),[],"none",{})
    rows=[]
    for i in range(n):
        t=OPEN+(i+25)*MINUTE
        s={**sig,"price":100.,"at":t.isoformat(),"processed":(t+timedelta(seconds=1)).isoformat(),
           "forecasts":{str(h):dict(upper=100.5,lower=99.5) for h in HORIZONS}}
        bar=dict(open=100.,high=101.,low=99.8,close=100.,volume=20)
        rows.append(dict(id=str(i),scope=str(DAY),origin=s["processed"],payload=dict(signal=s,ohlcv=bar,scope="All",decision="WAIT")))
    return rows


def test_no_lookahead_and_ambiguous_mature_labels(tmp_path):
    rows=fake_records()
    assert not matured(rows[:4])
    # 3-minute label starts one minute AFTER a late scan, dropping the partly observed candle.
    first=matured(rows[:5])[0]
    assert first["outcome"]=="up" and first["arrival_seconds"]==119
    rows[2]["payload"]["ohlcv"]["low"]=99.
    assert matured(rows[:5])[0]["outcome"]=="ambiguous"
    assert not matured(rows[:2]+rows[3:5])
    assert summary(matured(rows),5)["origins"]>0 and event_summary(rows)["episodes"]>0
    assert benchmark(matured(rows))["status"]=="Collecting observations"
    assert pricing_errors(rows)=={"matched":0}
    assert archive(rows,tmp_path,"SPY",DAY)==len(rows)
    assert len(list(tmp_path.glob("*.parquet")))==1
    assert delayed_online(matured(rows))["n"]>0


def test_research_validation_and_atomic_budget(tmp_path,monkeypatch):
    j=Journal(tmp_path/"db",dsn="")
    proposal=dict.fromkeys(FIELDS,"test");proposal.update(ticker="SPY",regime="ALL",parameter="ignition_threshold",current=65,proposed=60)
    assert validate(proposal)==proposal
    for override in ({"proposed":float("nan")},{"current":42},{"parameter":"risk"},{"extra":"unknown"}):
        with pytest.raises(ValueError):validate({**proposal,**override})
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(lambda _:reserve(j,DAY,.01),range(5)))==1
    job=claim_stage(j,DAY,"report",OPEN)
    assert not claim_stage(j,DAY,"report",OPEN+MINUTE)
    finish_stage(j,DAY,"report",job)
    assert not claim_stage(j,DAY,"report",CLOSE)
    monkeypatch.setenv("QD_V3_OPENAI_DAILY_USD","0")
    assert not reserve(j,date(2026,11,30),.01)
    assert change_probability([0.]*20+[7])>=0
    assert lead_lag({},"SPY","SPX")["1"]["pairs"]==0
    assert replay_proposal([],proposal)["status"]=="Insufficient samples"


def test_daily_and_retry_dont_double_count(tmp_path,monkeypatch):
    j=Journal(tmp_path/"db",dsn="")
    monkeypatch.setenv("QD_V3_RECORD","1");monkeypatch.setenv("QD_V3_ML","1")
    for row in fake_records(): j.insert(row["id"],"SPY","v3_scan",str(DAY),datetime.fromisoformat(row["origin"]),row["payload"])
    daily(j,CLOSE+timedelta(minutes=16))
    assert len(j.records("DESK","v3_report"))==1 and not j.records("DESK","v3_training")
    daily(j,CLOSE+timedelta(minutes=61));daily(j,CLOSE+timedelta(minutes=62))
    assert len(j.records("DESK","v3_training"))==1 and len(j.records("DESK","v3_report"))==1


def test_contract_page_and_risk_limits(monkeypatch):
    from paper_engine import MAX_DEBIT,DAILY_STOP,PREMIUM_STOP
    assert (MAX_DEBIT,DAILY_STOP,PREMIUM_STOP)==(1000,-500,.20)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"terminal.py"),default_timeout=40).run()
    app.selectbox(key="desk_page").set_value("Contracts").run()
    assert not app.exception and app.get("plotly_chart")
    for ticker in ("SPX","QQQ"):
        app.button(key="watch_"+ticker).click().run()
        assert not app.exception
    app.selectbox(key="desk_page").set_value("Learning").run()
    assert not app.exception


def test_price_events_exist_without_any_valid_signals():
    from v3_research import market_events
    history=fake_records()
    for row in history:
        row["payload"]["signal"].update(state="INSUFFICIENT DATA",side=0,scores={},price=None,forecasts={})
        row["payload"]["ohlcv"].update(low=100.,high=100.8)
    events=market_events([],history)
    assert events["eligible_origins"] and events["events"]
    assert all(e["classification"]=="missed" for e in events["events"])
    assert all(e["direction"]==1 for e in events["events"])
    for row in history:row["payload"]["ohlcv"]["low"]=99.
    assert market_events([],history)["ambiguous"]>0


def test_summary_delivery_and_feedback_without_network(monkeypatch,tmp_path):
    from v3_daily import send_daily
    journal=Journal(tmp_path/"j",dsn="")
    day=str(DAY)
    report={"SPY":{"recorded":30,"horizons":[]}}
    journal.insert("x","DESK","v3_report",day,CLOSE,report)
    calls=[]
    url="https://discord.com/api/webhooks/1/fixture"
    assert send_daily(journal,day,url,sender=lambda u,m:calls.append(m))["status"]=="HTTP acknowledged"
    assert send_daily(journal,day,url,sender=lambda u,m:calls.append(m))["status"]=="Already claimed"
    assert len(calls)==1 and "SPY" in calls[0]
    monkeypatch.setenv("QD_V3_OPENAI","1")
    monkeypatch.setenv("QD_V3_EXTERNAL_AGGREGATES","1")
    monkeypatch.setenv("OPENAI_API_KEY","offline-fixture")
    proposal=dict.fromkeys(FIELDS,"test")
    proposal.update(ticker="SPY",regime="ALL",parameter="ignition_threshold",current=65,proposed=60)
    body={"usage":{"input_tokens":100,"output_tokens":50,"input_tokens_details":{"cached_tokens":0,"cache_write_tokens":0}},"status":"completed",
          "output":[{"content":[{"type":"output_text","text":json.dumps(proposal)}]}]}
    def transport(url,**request):
        assert not request["json"]["store"]
        assert "raw-secret-test" not in request["json"]["input"]
        return SimpleNamespace(status_code=200,json=lambda:body)
    report["SPY"]["raw"]="raw-secret-test"
    lookup=lambda *a,**kw: SimpleNamespace(status_code=200,json=lambda:{"id":"gpt-6.2-astra","object":"model"})
    result=feedback(journal,DAY,report,transport=transport,lookup=lookup,now=CLOSE)
    assert result["proposal"]==proposal and result["cost_usd"]==pytest.approx(.0028)
    assert feedback(journal,DAY,report,transport=lambda *a,**kw: pytest.fail("duplicate inference"),lookup=lookup,now=CLOSE) == result
    monkeypatch.setenv("QD_V3_OPENAI_MONTHLY_USD",".0001")
    assert not reserve(journal,date(2026,11,30),.01)


def test_walkforward_on_synthetic_sessions():
    from v3_research import FEATURES
    from v3_challenger import threshold_search
    rows=[]
    for d in range(20):
        for i in range(26):
            dt=OPEN+timedelta(days=d,minutes=i)
            side=1 if i%2 else -1
            rows.append(dict(session=str(dt.date()),at=dt.isoformat(),label=side,outcome="up" if side>0 else "down",
                             horizon=5,features={k:side for k in FEATURES},state="IGNITION",side=side,ignition=70))
    result=benchmark(rows)
    assert result["folds"][-1]["stage"]=="holdout" and result["folds"][-1]["n"]==104
    assert result["folds"][-1]["brier"]<result["folds"][-1]["climatology_brier"]
    assert result["components"]["up"]["test"]==104 and result["components"]["neither"]["status"]=="Insufficient class support"
    assert threshold_search(rows)["promotion"] is False


def test_skew_uses_only_bracketed_strikes_and_current_expiry_input():
    from v3_options import skew_scenario
    rows=[dict(strike=k,iv=v) for k,v in [(90,.3),(100,.2),(110,.18)]]
    assert skew_scenario(rows,100,100,102)>0
    assert skew_scenario(rows,100,100,150) is None
    assert skew_scenario(rows[:2],100,100,102) is None


def test_pricing_learning_matches_contract_even_after_it_drops_from_shortlist():
    records=fake_records()
    src=records[0]
    actual=records[5]
    stamp=actual["payload"]["signal"]["at"]
    item=dict(contract("SPY"),target=102.,arrival=stamp,forecast=1.5,updated_at=src["origin"])
    src["payload"].update(options=[item],chosen=item["contract"])
    actual["payload"].update(options=[],quote_samples=[{**contract("SPY"),"updated_at":stamp,"delta":.51}])
    rows=pricing_errors(records)
    assert rows["matched"]==1 and rows["total_forecast_mae"]==pytest.approx(.45)
    assert rows["groups"]["Recommended"]["matched"]==1 and rows["delta_conditional_mae"]>=0


def test_private_archive_replay_and_paired_bootstrap(tmp_path):
    from v3_evaluate import bootstrap_difference,evaluate
    rows=fake_records()
    archive(rows,tmp_path,"SPY",DAY)
    result=evaluate(tmp_path,"SPY")
    assert result["days"][0]["recorded"]==40 and result["days"][0]["ambiguous"]>0
    assert result["promotion"] is False and result["paired_origin_recall"]["sessions"]==1
    paired=bootstrap_difference([[1,2,4]]*10)
    assert paired["interval95"]==pytest.approx([.25,.25]) and paired["directional_origins"]==40



def test_strategy_journal_capture_controls(monkeypatch):
    from monitor_engine import strategy_record
    plan={"status":"WAIT","movement_v3":{"scores":{}},"v3_quote_samples":[1],"v3_contracts":{}}
    monkeypatch.setenv("QD_V3_RECORD","false")
    assert strategy_record(plan)=={"status":"WAIT"}
    monkeypatch.setenv("QD_V3_RECORD","true")
    assert "movement_v3" in strategy_record(plan) and "v3_quote_samples" not in strategy_record(plan)
