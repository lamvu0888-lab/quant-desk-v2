import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gemini_advisor import AdvisorQueue, OpenAIAdvisor, configured_advisor
from gemini_sessions import review_window
from gemini_strategy import abc_outcomes
from market_session import rth_bounds
from openai_intelligence import IntelligenceError, policy, reserve, structured, usage_cost
from research_journal import Journal
from test_gemini_paper import inputs, reply

DAY = "2026-11-20"
START = rth_bounds(DAY)[0]


def response(payload, status=200):
    return SimpleNamespace(status_code=status, json=lambda:payload)


def test_openai_abc_publication_is_schema_validated_and_locally_evaluable(tmp_path):
    from gemini_strategy import ScenarioMonitor, assess
    plan, read, now = inputs()
    j = Journal(tmp_path/"plans", dsn="")
    published = now - timedelta(minutes=2)
    monitor = ScenarioMonitor(j,clock=lambda:published)
    monitor.daily(dict(plan, detected=published.isoformat()),read,published)
    legs = reply("A",plan["setup"])
    for leg in legs.values():
        leg["hold_minutes"] = 5
    answer = {"bias":"Bullish","regime":"Trend","risk_factor":"Flow may reverse.","legs":legs}
    post = Mock(return_value=response({"status":"completed","usage":{"input_tokens":100,"output_tokens":200},
              "output":[{"content":[{"type":"output_text","text":json.dumps(answer)}]}]}))
    lookup = Mock(return_value=response({"id":"gpt-6.2-sol","object":"model"}))
    client = OpenAIAdvisor(j,"plan-fixture", {}.get, post=post,lookup=lookup)
    assert monitor.jobs.process_one(client, lambda:published)
    req = post.call_args.kwargs["json"]
    assert req["service_tier"] == "default" and req["text"]["format"]["strict"] and not req["store"]
    with j.connection() as db:
        daily = monitor.jobs.session_plan(db,plan["symbol"],plan["session"])
        assert daily["context"]["bias"] == "Bullish" and daily["model"] == "gpt-6.2-sol"
        assert daily["legs"]["A"]["hold_minutes"] == 5
        assert db.execute("SELECT status FROM qdv3_usage").fetchone()[0] == "reconciled"
    candidate, reasons = assess(plan,read,daily,now)
    assert not reasons and candidate["hold"] == 5
    assert post.call_count == lookup.call_count == 1
    monitor.close()


def test_shared_budget_cannot_be_overspent_by_concurrent_features(tmp_path):
    j=Journal(tmp_path/"budget", dsn="")
    cfg={"QD_V3_OPENAI_DAILY_USD":"0.10","QD_V3_OPENAI_MONTHLY_USD":"0.10"}.get
    with ThreadPoolExecutor(max_workers=6) as pool:
        got=list(pool.map(lambda n:reserve(j, DAY, .06, ident=f"mixed:{n}",get=cfg),range(6)))
    assert sum(got)==1
    assert not reserve(j,"2026-11-23",.05, ident="later", get=cfg)
    assert reserve(j,"2026-12-01",.06, ident="new", get=cfg)
    assert not reserve(j,"2026-12-01",-.01,ident="negative")


def test_model_access_refusals_and_unsupported_settings(tmp_path):
    j=Journal(tmp_path/"api", dsn="")
    post=Mock()
    def ask(key, lookup, ident):
        return structured(j,key,"gpt-6.2-astra","high","advice","review",{}, {},ident,
                          lookup=lookup,post=post,now=START)
    with pytest.raises(IntelligenceError,match="403"):
        ask("denied",lambda *a,**kw:response({},403),"no-access")
    post.assert_not_called()
    lookup=Mock(return_value=response({"id":"gpt-6.2-astra","object":"model"}))
    post.return_value=response({"status":"completed","usage":{"input_tokens":1,"output_tokens":1},
                                "output":[{"content":[{"type":"refusal"}]}]})
    with pytest.raises(IntelligenceError,match="declined"):
        ask("fixture",lookup,"refused")
    with pytest.raises(IntelligenceError,match="declined"):
        ask("fixture",lookup,"refused")
    assert post.call_count==lookup.call_count==1
    assert configured_advisor(j,{"GEMINI_API_KEY":"old"}.get) is None
    assert configured_advisor(j,{"QD_AI_PLANS":"true","GEMINI_API_KEY":"old"}.get) is None
    legacy=configured_advisor(j,{"QD_AI_PLANS":"true","QD_AI_PROVIDER":"gemini","GEMINI_API_KEY":"legacy"}.get)
    assert legacy.model.startswith("gemini-")
    with pytest.raises(IntelligenceError): policy({"QD_AI_ROUTINE_MODEL":"unknown"}.get,"routine")
    assert usage_cost("gpt-6.2-sol",{"input_tokens":100,"output_tokens":10,
        "input_tokens_details":{"cached_tokens":40,"cache_write_tokens":20}})==pytest.approx(.000234)


def test_checkpoint_uses_actual_session_and_keeps_prior_periods_separate():
    opened,closed=rth_bounds("2026-11-27")
    assert review_window(opened,opened,closed) is None
    assert review_window(opened+(closed-opened)*.5,opened,closed)=="Midday"
    assert review_window(opened+(closed-opened)*.75,opened,closed)=="Afternoon"
    assert review_window(closed,opened,closed) is None


def test_geometry_review_uses_postpublication_bars_and_excludes_intrabar_ambiguity(tmp_path):
    j=Journal(tmp_path/"research",dsn="")
    assert abc_outcomes(j,"SPY",DAY,[])["published_versions"]==0
    q=AdvisorQueue(j);q.start()
    levels={"source":"Webull","symbol":"SPY","session":DAY}
    legs={"A":{"setup":"Breakout close","trigger":100,"invalidation":98,"target":104,"hold_minutes":5}}
    with j.connection() as db:
        q.enqueue(db,"abc","plan","SPY",DAY,START,levels)
        db.execute("UPDATE qd_gemini_job SET state='ready',updated=?,result=?",
                   (START.timestamp()+30,json.dumps(legs)))
    def obs(minutes, h, l, c):
        at=START+timedelta(minutes=minutes)
        return {"payload":{"ohlcv":{"high":h,"low":l,"close":c},"signal":{
            "symbol":"SPY","session":DAY,"at":at.isoformat(),"source":"Webull","preview":False}}}
    rows=[obs(1,999,1,105),obs(2,102,99,101),obs(3,105,97,101)]
    assert abc_outcomes(j,"SPY",DAY,rows)["leg_outcomes"]=={"ambiguous":1}
    rows[-1]["payload"]["ohlcv"]["low"]=99
    assert abc_outcomes(j,"SPY",DAY,rows)["leg_outcomes"]=={"target_first":1}
    assert abc_outcomes(j,"SPY",DAY,rows[2:])["leg_outcomes"]=={"coverage_gap":1}


def test_large_daily_feedback_keeps_all_research_categories_with_counts():
    from v3_feedback import research_aggregate, wire_size
    groups = {str(i): {"matched":i, "mae":.35, "gamma_mae":.03, "theta_mae":.04} for i in range(1500)}
    block = {"recorded":600, "pricing":{"matched":2000, "groups":groups}, "contract_selection":{
        "complete":12, "dte":groups}, "abc_outcomes":{"leg_outcomes":{"ambiguous":2}},
        "futures_cvd":{"paired_origins":90}, "raw":"private test"}
    result = research_aggregate(DAY, {k:block for k in ("QQQ","SPX","SPY")})
    assert wire_size(result) <= 24000
    assert "private test" not in json.dumps(result)
    for block in result["symbols"].values():
        assert block["futures_cvd"]["paired_origins"] == 90
        assert block["abc_outcomes"]["leg_outcomes"]["ambiguous"] == 2
        detail = block["pricing"]["values"]["groups"]
        assert "1499" in detail["values"] and 0 < detail["shown"] < detail["total"] == 1500


def test_uncertain_delivery_survives_restart_without_second_paid_attempt(tmp_path):
    from requests import Timeout
    from openai_intelligence import UnconfirmedRequest
    j = Journal(tmp_path/"after-timeout", dsn="")
    origin = "openai:abc:plan:2026-11-20:SPX:v4:"
    lookup = lambda *a,**kw:response({"id":"gpt-6.2-sol","object":"model"})
    post = Mock(side_effect=Timeout("test"))
    def run(n):
        return structured(j,"timeout-fixture","gpt-6.2-sol","medium","test","plan",{}, {},
                          origin+str(n), post=post,lookup=lookup,now=START,group=origin)
    with pytest.raises(UnconfirmedRequest, match="delivery unconfirmed"):
        run(1)
    with pytest.raises(UnconfirmedRequest, match="Prior"):
        run(2)
    assert post.call_count == 1
    with j.connection() as db:
        assert db.execute("SELECT amount FROM qdv3_usage WHERE status='reserved'").fetchone()[0] > 0


def test_revised_abc_geometry_requires_its_own_contract_quote(tmp_path, monkeypatch):
    from gemini_strategy import ScenarioMonitor
    from test_gemini_paper import OK_MODEL
    plan, read, now = inputs()
    j = Journal(tmp_path/"rerank", dsn="")
    start = now - timedelta(minutes=2)
    monitor = ScenarioMonitor(j, clock=lambda:now)
    monitor.daily(dict(plan, detected=start.isoformat()),read,start)
    client = Mock(model="fixture")
    client.generate.return_value = reply("A",plan["setup"])
    assert monitor.jobs.process_one(client, lambda:start)
    plan.update(rank_contracts=True, contract_recommendation={"status":"qualified","selected":{"contract":"original"}})
    monkeypatch.setattr("gemini_strategy.model_summary",lambda *args:OK_MODEL)
    monitor.record(plan,read,now)
    assert not j.records("SPY","gemini_entry")
    assert "fresh contract" in j.records("SPY","gemini_scan",limit=1)[0]["payload"]["reasons"][0].lower()
    ctx = SimpleNamespace()
    def no_quote(context, candidate, reader, *, track):
        from gemini_advisor import SHADOW
        assert track == SHADOW
        assert context is ctx and reader is read and "gemini" in candidate
        return {**candidate,"status":"GET READY","missing":["Revised target cannot cover the spread."]}
    monkeypatch.setattr("decision_flow.finalize_contract",no_quote)
    monitor.record(plan,read,now,ctx=ctx)
    assert not j.records("SPY","gemini_entry")
    assert j.records("SPY","gemini_scan",limit=1)[0]["payload"]["reasons"] == ["Revised target cannot cover the spread."]
    monitor.close()


def test_crash_after_billing_before_job_publish_recovers_the_checked_answer(tmp_path):
    from test_gemini_paper import REVIEW
    j = Journal(tmp_path/"recover",dsn="")
    queue = AdvisorQueue(j)
    queue.start()
    with j.connection() as db:
        queue.enqueue(db,"review:spx:1","review","SPX",DAY,START,{"simulation":True})
    model = "gpt-6.2-sol"
    post = Mock(return_value=response({"status":"completed","usage":{"input_tokens":12,"output_tokens":30},
             "output":[{"content":[{"type":"output_text","text":json.dumps(REVIEW)}]}]}))
    lookup = Mock(return_value=response({"object":"model","id":model}))
    first = OpenAIAdvisor(j,"api-fixture",{}.get,post=post,lookup=lookup)
    with pytest.raises(SystemExit):
        queue.process_one(first,Mock(side_effect=[START,SystemExit("lost worker before publish")]))
    new = Journal(j.path,dsn="")
    # A new worker can publish the receipt even if model discovery would be offline.
    from openai_intelligence import MODELS
    MODELS.clear()
    no_network = Mock(side_effect=AssertionError("recovery contacted the provider"))
    again = OpenAIAdvisor(new,"another-private-key",{}.get,post=no_network,lookup=no_network)
    assert AdvisorQueue(new).process_one(again,lambda:START + timedelta(seconds=121))
    with new.connection() as db:
        state, answer, attempts = db.execute("SELECT state,result,attempt FROM qd_gemini_job").fetchone()
        assert state=="ready" and json.loads(answer)==REVIEW and attempts==2
        assert db.execute("SELECT COUNT(*) FROM qdv3_usage").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM qdv3_ai_result").fetchone()[0]==1
    assert post.call_count==1
    no_network.assert_not_called()


def test_unexpected_response_fields_are_never_cached_as_a_valid_plan(tmp_path):
    j = Journal(tmp_path/"checked",dsn="")
    model = "gpt-6.2-sol"
    lookup = lambda *args,**kwargs: response({"object":"model","id":model})
    def packet(value):
        return response({"status":"completed","usage":{"input_tokens":1,"output_tokens":2},
            "output":[{"content":[{"type":"output_text","text":json.dumps(value)}]}]})
    post = Mock(side_effect=[packet({"x":"unexpected input"}),packet({"number":4})])
    schema = {"type":"object","properties":{"number":{"type":"integer"}},"required":["number"],"additionalProperties":False}
    def submit(n,data):
        return structured(j,"schema-test",model,"medium","test","plan",data,schema,"test:"+str(n),
                          group="test:",lookup=lookup,post=post,now=START)
    with pytest.raises(IntelligenceError,match="schema"):
        submit(1,{})
    with pytest.raises(IntelligenceError,match="schema"):
        submit(2,{})
    assert submit(3,{"previous_validation_error":"repair"})[0]=={"number":4}
    assert post.call_count==2
    with j.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM qdv3_ai_result WHERE result LIKE '%unexpected%'").fetchone()[0]==0
