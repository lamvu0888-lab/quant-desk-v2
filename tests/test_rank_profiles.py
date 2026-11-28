from datetime import datetime, timedelta, timezone

import pytest

from rank_profiles import FEATURES, TILTS, pick, profile_inputs
from rank_research import VERSION, _cohort, forward_profile_review, replay_rank_proposal
from research_journal import Journal
from v3_feedback import BOUNDS, FIELDS, validate


def choice(code, dte=0, strength=.5, **changes):
    row = dict(ask=1.04, bid=1.0, delta=-.55, gamma=.06, midpoint_iv=.22,
               provider_iv=.20, ask_size=2, quantity=2)
    row.update(changes)
    return dict(code=code, dte=dte, features=dict.fromkeys(FEATURES, strength),
                profile_inputs=profile_inputs(row, {"spot": 700.}), position_net=22.)


def opportunity(*rows, at=None):
    at = at or datetime(2026, 11, 20, 16, tzinfo=timezone.utc)
    return dict(rows=list(rows), selected=rows[0]["code"], session=str(at.date()),
                origin=at.isoformat(), end=(at+timedelta(minutes=10)).isoformat(),
                context=["midday","Expanding","breakout"])


def test_profile_ids_backward_compatible_and_bounded():
    assert list(TILTS)==["base","execution","downside","carry","same_day","short_dated",
                         "balanced_delta","bounded_gamma","provider_mid_iv","tight_execution"]
    assert BOUNDS["contract_rank_profile"]==(0,9)
    base=dict.fromkeys(FIELDS, "paper evidence")
    base.update(ticker="SPY", regime="Expanding", parameter="contract_rank_profile", current=0)
    for i in range(10):
        assert validate(dict(base, proposed=i))["proposed"]==i
    for invalid in [-1,10,5.5,True,float("nan")]:
        with pytest.raises(ValueError):
            validate(dict(base, proposed=invalid))


@pytest.mark.parametrize(("name","bad"),[
    ("same_day",{"dte":1}),("short_dated",{"dte":8}),("balanced_delta",{"delta":.1}),
    ("bounded_gamma",{"gamma":.5}),("provider_mid_iv",{"provider_iv":.35}),
    ("tight_execution",{"ask":1.5}),("tight_execution",{"ask_size":1})])
def test_strict_profile_can_choose_cheaper_markout_and_can_explicitly_abstain(name,bad):
    high=choice("A",strength=.8,**bad)
    good=choice("B", dte=1 if name=="short_dated" else 0)
    good["position_net"]=-3000
    assert pick(opportunity(high,good),name)["code"]=="B"
    assert pick(opportunity(high),name)==dict(code=None,position_net=0.,research_abstention=True)
    assert pick(opportunity(high,good),"base")["code"]=="A"


def test_origin_only_inputs_no_silent_fill_and_tied_choice_is_stable():
    good=choice("D")
    old=choice("A")
    old.pop("profile_inputs")
    for name in ["balanced_delta","bounded_gamma","provider_mid_iv","tight_execution"]:
        assert pick(opportunity(good,old),name) is None
    assert pick(opportunity(good,old),"same_day")["code"]=="A"
    for name in [*TILTS]:
        dte=1 if name=="short_dated" else 0
        assert pick(opportunity(choice("D",dte),choice("A",dte)),name)["code"]=="A"
    assert profile_inputs({"iv":.2,"provider_iv":.20}, {"spot":700})["iv_gap"] is None
    assert profile_inputs({"delta":True,"ask_size":1.3,"gamma":float("inf")},{})["gamma_10bp"] is None


def test_matching_forward_and_replay_do_not_promote_and_count_explicit_skips(tmp_path):
    now = datetime(2026, 11, 20, 16, tzinfo=timezone.utc)
    earlier = now-timedelta(days=1)
    today = str(now.date())
    db = Journal(path=tmp_path/"new-profiles.db",dsn="")
    trial=dict(opportunity(choice("A",ask_size=1),at=now), version=VERSION, symbol="SPY", **_cohort())
    db.insert("trial","SPY","contract_rank_training_day",today,now+timedelta(minutes=15),
              dict(version=VERSION,symbol="SPY",trials=[trial]))
    prop=dict(proposed=9,parameter="contract_rank_profile",ticker="SPY",regime="ALL")
    db.insert("proposal","DESK","v3_feedback",str(earlier.date()),earlier,dict(proposal=prop))
    prev=replay_rank_proposal(db,"SPY",today,prop)
    forward=forward_profile_review(db,"SPY",today,[trial])
    assert prev["profile"]==forward["profile"]=="tight_execution"
    assert prev["mean_paired_session_gain_usd"]==forward["mean_selected_advantage_usd"]==-22.
    assert not prev["promotion"] and not forward["promotion"]
