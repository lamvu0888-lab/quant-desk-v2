from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from rank_research import (KIND, MODEL, VERSION, _fit, _independent, comparable, daily_results,
                           end_of_day, probability, record_rank_scan, replay_rank_proposal)
from research_journal import Journal
from tradytics_feed import Feed, Snapshot

NOW = datetime(2026, 11, 20, 17, tzinfo=timezone.utc)
DAY = NOW.date().isoformat()
FIRST, SECOND = "SPY261120C00700000", "SPY261120C00701000"


def sample(code=FIRST, bid=1., ask=1.08, source="Webull"):
    return dict(contract=code, eligible=True, entry=ask, ask=ask, quantity=2, score=80,
                received_at=NOW.isoformat(), delay_minutes=0, ask_size=2,
                bid=bid, source=source, quote_at=NOW.isoformat(), type="call", expiry=DAY,
                dte=0, target_premium=1.6, features={k:.5 for k in
                ("expected_net","target_before_stop","downside","liquidity","sensitivity","carry","iv_risk")})


def plan():
    first,other = sample(),sample(SECOND)
    from rank_research import _cohort
    config = _cohort()
    return dict(regime="Trend",setup="break",movement_v3=dict(version=config["movement_version"],
                mode=config["movement_mode"]),contract_recommendation=dict(mode=config["mode"],
        simulated=False, version=VERSION, origin=NOW.isoformat(), status="qualified",
        symbol="SPY",direction=1,arrival=(NOW+timedelta(minutes=2)).isoformat(),
        candidates=[first,other], selected=first,alternatives=[other]))


def ctx(now, feed=None):
    return SimpleNamespace(now=now,scope="0DTE",session=NOW.date(),ticker="SPY",preview=False,paused=False,
        price_provider="Webull",option_max_age=45,option_chain=lambda *a,**k:(feed,"Webull"))


def test_worker_freezes_set_then_matches_observed_exit_bid(tmp_path,monkeypatch):
    monkeypatch.setenv("QD_V3_RECORD","1")
    db=Journal(path=tmp_path/"trial.db",dsn="")
    rec=plan()
    record_rank_scan(db,ctx(NOW),rec)
    rec["contract_recommendation"]["candidates"][0]["entry"]=.50
    record_rank_scan(db,ctx(NOW),rec)
    at=NOW+timedelta(minutes=2,seconds=10)
    quote_rows=[dict(contract=code, type="call",symbol="SPY",expiry=DAY,updated_at=at.isoformat(),
                     bid=bid,ask=bid+.04,bid_size=2) for code,bid in [(FIRST,1.48),(SECOND,1.22)]]
    feed=Feed(Snapshot(quote_rows,{"source":"Webull","delay_minutes":0},at))
    record_rank_scan(db,ctx(at,feed),{})
    row=db.records("SPY",KIND,scope=DAY)[0]
    trial=comparable(row)
    assert trial["rows"][0]["per_contract"]==pytest.approx((1.47-1.08)*100-1.30)
    assert trial["rows"][1]["per_contract"]==pytest.approx((1.21-1.08)*100-1.30)
    stats=end_of_day(db,"SPY",DAY,at)
    assert stats["complete"]==1 and stats["paired_origins"]==1
    assert stats["median_selected_minus_median_alternative_usd"]==pytest.approx(26.)
    assert len(db.records("SPY","contract_rank_training_day",scope=DAY))==1
    # Never convert partial markouts, a later quote or another provider into a comparison.
    mark=row["outcome"]["marks"].pop(SECOND)
    assert comparable(row) is None
    row["outcome"]["marks"][SECOND]=dict(mark,source="Schwab")
    assert comparable(row) is None
    row["outcome"]["marks"][SECOND]=dict(mark,at=(at+timedelta(minutes=2)).isoformat())
    assert comparable(row) is None


def test_recording_respects_preview_scope_and_censors_stale_quotes(tmp_path,monkeypatch):
    monkeypatch.setenv("QD_V3_RECORD","1")
    db=Journal(path=tmp_path/"trial.db",dsn="")
    c=ctx(NOW)
    c.preview=True
    record_rank_scan(db,c,plan())
    c.preview=False
    rec=plan()
    rec["contract_recommendation"]["simulated"]=True
    record_rank_scan(db,c,rec)
    assert not db.records("SPY",KIND)
    record_rank_scan(db,c,plan())
    at=NOW+timedelta(minutes=2,seconds=5)
    q=dict(contract=FIRST, type="call",symbol="SPY",expiry=DAY,updated_at=NOW.isoformat(),bid=.90,ask=1.)
    record_rank_scan(db,ctx(at,Feed(Snapshot([q],{},at))),{})
    record_rank_scan(db,ctx(at,Feed(Snapshot([dict(q,updated_at=at.isoformat())],{"synthetic":True},at))),{})
    rows=db.records("SPY",KIND,scope=DAY)
    stats,trials=daily_results(rows)
    assert stats["qualified"]==stats["incomplete"]==1 and not trials


def test_holdout_and_probability_never_use_future_or_mixed_model(tmp_path):
    trials=[]
    for i in range(25):
        for j in range(16):
            t=NOW-timedelta(days=35-i)+timedelta(minutes=j*10)
            a=sample();b=sample(SECOND)
            aff=1 if j<13 else -1
            trial=dict(origin=t.isoformat(),session=str(t.date()),end=(t+timedelta(minutes=5)).isoformat(),
                        selected=FIRST,context=["midday","Trend","break"],rows=[
                dict(code=FIRST,dte=0,per_contract=12*aff,position_net=24*aff,features=a["features"]),
                dict(code=SECOND,dte=3,per_contract=-15*aff,position_net=-30*aff,features=b["features"])])
            trials.append(trial)
    research=_fit(_independent(trials+trials))
    assert research["status"]=="Shadow comparison" and research["independent_origins"]==len(trials)
    assert research["holdout_sessions"]>=5 and research["promotion"] is False and len(research["probabilities"])==2
    db=Journal(path=tmp_path/"prob.db",dsn="")
    published=NOW-timedelta(days=1)
    from rank_research import _cohort
    report=dict(research,symbol="SPY",version=VERSION,trained_through=str(published.date()),
                published=published.isoformat(),cohort=_cohort())
    db.insert("prob","SPY",MODEL,str(published.date()),published,report)
    def p(now,version=VERSION,setup="break"):
        return probability(db,"SPY",now,version,dte=0,regime="Trend",setup=setup)
    assert .5<p(NOW)<1 and p(published) is None and p(NOW+timedelta(days=9)) is None
    assert p(NOW,"other-version") is None and p(NOW,setup="mean reversion") is None
    assert replay_rank_proposal(db,"SPY",DAY,{"proposed":99})["promotion"] is False


def test_pricing_error_holds_entry_iv_and_uses_realized_spot_at_synchronized_mark():
    from rank_research import _conditional_error
    from v3_options import contract_spec, value, YEAR
    c=dict(sample(),iv=.21,rate=.04,dividend=.013,symbol="SPY",strike=700.)
    mark_at=NOW+timedelta(minutes=10)
    spot={"spot":701.,"at":mark_at.isoformat(),"source":"Webull"}
    rec={"symbol":"SPY","spot_source":"Webull","target":706.}
    spec=contract_spec(c,"SPY")
    fair=float(value(spec,701.,(spec.expires-mark_at).total_seconds()/YEAR,.21,.04,.013))
    mark={"at":mark_at.isoformat(),"bid":fair-.02,"ask":fair+.02}
    assert _conditional_error(c,mark,spot,rec)==pytest.approx(0.)
    assert _conditional_error(c,mark,dict(spot,at=NOW.isoformat()),rec) is None
    assert _conditional_error(c,mark,dict(spot,source="Schwab"),rec) is None


def test_rank_retries_partial_and_skewed_exit_sets_within_fixed_window(tmp_path, monkeypatch):
    monkeypatch.setenv("QD_V3_RECORD","1")
    db = Journal(path=tmp_path/"retry.db", dsn="")
    record_rank_scan(db, ctx(NOW), plan())
    arrival = NOW+timedelta(minutes=2)
    def quote(code, at, bid):
        return dict(contract=code, type="call",symbol="SPY",expiry=DAY,
                    updated_at=at.isoformat(),bid=bid,ask=bid+.02,bid_size=2)
    def collect(at, rows):
        feed = Feed(Snapshot(rows,{"source":"Webull","delay_minutes":0},at))
        record_rank_scan(db,ctx(at,feed),{})
        return db.records("SPY",KIND,scope=DAY)[0]
    assert collect(arrival, [quote(FIRST,arrival,8.)])["outcome"] is None
    assert collect(arrival,[quote(FIRST,arrival,8.),dict(quote(SECOND,arrival,.9),bid_size=1)])["outcome"] is None
    later = arrival+timedelta(seconds=18)
    assert collect(later,[quote(FIRST,arrival,8.),quote(SECOND,later,.9)])["outcome"] is None
    later += timedelta(seconds=2)
    accepted = collect(later,[quote(FIRST,later,.8),quote(SECOND,later,.9)])
    assert accepted["outcome"]["marks"][FIRST]["bid"]==.8
    assert comparable(accepted)["rows"][0]["per_contract"] < 0
    # Missing entry provenance, backdated receipt or historical quote must abstain.
    candidate = accepted["payload"]["candidates"][0]
    candidate.pop("delay_minutes")
    assert comparable(accepted) is None
    candidate["delay_minutes"] = 0
    candidate["quote_at"]=(NOW-timedelta(seconds=31)).isoformat()
    assert comparable(accepted) is None
    candidate["quote_at"]=NOW.isoformat()
    accepted["outcome"]["marks"][FIRST]["received_at"]=arrival.isoformat()
    assert comparable(accepted) is None


@pytest.mark.parametrize(("q_extra","meta","received_shift"), [
    ({},{},0), ({"delay_minutes":15},{"delay_minutes":0},0),
    ({"synthetic":True},{"delay_minutes":0},0), ({},{"delay_minutes":0,"source":"Schwab"},0),
    ({"delay_minutes":0},{},-1), ({"delay_minutes":True},{},0), ({"ask":.8},{"delay_minutes":0},0)])
def test_rank_exit_quote_rejects_unverified_data(q_extra,meta,received_shift):
    from rank_research import _real_quote
    row = dict(contract=FIRST, type="call",symbol="SPY",expiry=DAY,updated_at=NOW.isoformat(),bid=1.,ask=1.05)
    feed = Feed(Snapshot([dict(row,**q_extra)],meta,
                         NOW+timedelta(seconds=received_shift)))
    assert not _real_quote(ctx(NOW,feed),"Webull",DAY,"call")


def test_training_inputs_respect_publication_time_origin_session_and_version(tmp_path):
    from rank_research import _cohort, _training_trials
    db = Journal(path=tmp_path/"train.db", dsn="")
    end = NOW+timedelta(minutes=3)
    published = NOW+timedelta(hours=4)
    trial = dict(_cohort(),version=VERSION,symbol="SPY",session=DAY,origin=NOW.isoformat(),
                 end=end.isoformat(),rows=[{"code":FIRST}])
    invalid = [dict(trial,version="old"),dict(trial,movement_version="old"),dict(trial,mode="foreign"),
           dict(trial,symbol="QQQ"),dict(trial,end=(published+timedelta(seconds=1)).isoformat()),
           dict(trial,session="2026-11-19"),dict(trial, synthetic=True)]
    payload=dict(version=VERSION,symbol="SPY",trials=[trial,*invalid])
    db.insert("training-day","SPY","contract_rank_training_day",DAY,published,payload)
    assert _training_trials(db,"SPY",DAY,end) == []
    assert _training_trials(db,"SPY",DAY,published)==[trial]
    db.insert("future-day","SPY","contract_rank_training_day",DAY,published+timedelta(seconds=1),payload)
    assert _training_trials(db,"SPY",DAY,published)==[trial]
