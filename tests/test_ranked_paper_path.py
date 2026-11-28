from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import contract_recommendation
import paper_engine
from contract_plan_view import contract_panel
from decision_flow import finalize_contract
from discord_monitor import alert_deadline
from monitor_engine import notification
from paper_engine import attempt_entry, update_trade
from ranked_position import approved_series, reprice
from signal_explain import discord_text
from streamlit.testing.v1 import AppTest
from tests.test_paper_dashboard import PLAN, START

CODE = "SPY261116C00675000"


def ranked():
    selected = dict(contract=CODE, symbol="SPY", label="SPY 675 Call · 3DTE", expiry="2026-11-16",
                    dte=3, type="call", strike=675., source="Schwab", bid=2.30, ask=2.40,
                    quote_at=START.isoformat(), quantity=2, target_premium=3.10, stop_premium=1.70,
                    cost=481.30, max_loss=481.30, expected_net=20., expected_return=.04, iv=.12)
    return dict(version="test", status="qualified", origin=START.isoformat(), simulated=False,
                expires_at=(START+timedelta(seconds=30)).isoformat(), direction=1, symbol="SPY",
                target=677., stop=673., hold=8, arrival=(START+timedelta(minutes=8)).isoformat(),
                reason="Eligible ranked contract", selected=selected, candidates=[selected], alternatives=[])


def trade():
    return dict(plan=dict(PLAN, rank_contracts=True, contract_recommendation=ranked()), symbol="SPY",
                origin=START.isoformat(),session=str(START.date()),state="PENDING",reason="Awaiting receipt",fills=[])


def desk(now):
    return SimpleNamespace(ticker="SPY",now=now,chart_quote={"at":now,"price":675.},preview=False,journal=None)


def snap(now,bid=2.30,ask=2.40):
    return {CODE:{"at":now,"bid":bid,"ask":ask,"ask_size":2,"bid_size":2}}


def test_only_ranked_occ_series_fills_at_current_ask_and_stays_pinned(monkeypatch):
    order = trade()
    now = START + timedelta(seconds=2)
    checked = []
    def fresh(client,codes,clock):
        checked.append(codes)
        return snap(now)
    monkeypatch.setattr(paper_engine,"snapshot_quotes",fresh)
    attempt_entry(order,desk(now),None,False,lambda:now,remaining_risk=100)
    assert checked == [[CODE]]
    assert order["contract"]==CODE and order["qty"]==1 and order["expiry"]=="2026-11-16"
    assert order["entry_ask"] == 2.4 and order["state"]=="OPEN"
    monkeypatch.setattr(paper_engine,"read_price",lambda ctx:{"bars_usable":False,"candles":[]})
    ctx=desk(now+timedelta(seconds=3))
    update_trade(order,ctx,snap(ctx.now)[CODE],False)
    assert order["contract"]==CODE and order["state"]=="OPEN"
    assert order["position_scenario"]["iv"]>0 and order["position_scenario"]["bid"]==2.30
    assert reprice(order,desk(now+timedelta(minutes=2)),snap(now)[CODE],.65) is None


@pytest.mark.parametrize("fault",["wrong OCC","expired","preview","wrong direction","no winner"])
def test_ranked_identity_and_expiry_cannot_fall_back_to_another_strike(fault):
    order = trade()
    rec=order["plan"]["contract_recommendation"]
    if fault=="wrong OCC":rec["selected"]["contract"]=CODE.replace("675","676")
    if fault=="expired":rec["expires_at"]=START.isoformat()
    if fault=="preview":rec["simulated"]=True
    if fault=="wrong direction":rec["direction"]=-1
    if fault=="no winner":rec["selected"]=None
    now=START+timedelta(seconds=1)
    attempt_entry(order,desk(now),None,False,lambda:now)
    assert order["state"]=="SKIPPED" and not order.get("contract")


@pytest.mark.parametrize("fault",["too costly","old quote","wide","no remaining risk"])
def test_post_alert_quote_and_daily_capacity_required(monkeypatch,fault):
    now=START+timedelta(seconds=3)
    quotes=snap(now)
    row=quotes[CODE]
    if fault=="too costly":row["ask"]=2.41
    if fault=="old quote":row["at"]=START-timedelta(seconds=2)
    if fault=="wide":row["bid"]=.8
    monkeypatch.setattr(paper_engine,"snapshot_quotes",lambda *a:quotes)
    order=trade()
    attempt_entry(order,desk(now),None,False,lambda:now,remaining_risk=0. if fault=="no remaining risk" else 400.)
    assert order["state"]=="PENDING" and not order.get("contract")


def test_selected_quote_shared_across_ui_delivery_and_ledger_snapshot(monkeypatch):
    monkeypatch.setattr(paper_engine, "ranking_capacity", lambda journal, now, track: (400., ""))
    for result in (ranked(), {**ranked(),"selected":None,"status":"unavailable","reason":"Spread too wide"}):
        monkeypatch.setattr(contract_recommendation,"recommend",lambda *a,**kw:deepcopy(result))
        plan = finalize_contract(desk(START),dict(PLAN,rank_contracts=True,detected=START.isoformat(),symbol="SPY"),{})
        if not result["selected"]:
            assert plan["status"]=="GET READY" and "Spread too wide" in plan["missing"]
            continue
        assert plan["option_quote"]["contract"]==CODE
        notice=notification(plan)
        assert notice["contract_recommendation"]["selected"]==result["selected"]
        assert "candidates" not in notice["contract_recommendation"]
        assert "SPY 675 Call · 3DTE" in discord_text(notice)
        assert "3.10/1.70" in discord_text(notice) and "481.30" in discord_text(notice)
        assert alert_deadline(notice,START)==START+timedelta(seconds=30)


def test_ranked_panel_opens_exact_contract_analysis():
    app=AppTest.from_string("""
import streamlit as st
from types import SimpleNamespace
from contract_plan_view import contract_panel
from tests.test_paper_dashboard import START
contract_panel(SimpleNamespace(preview=False,ticker="SPY",now=START),st.session_state["plan"])
""")
    app.session_state["plan"]=trade()["plan"]
    app.run()
    assert not app.exception
    app.button(key="trade_analyze_contract").click().run()
    assert app.session_state["desk_page"]=="Contracts" and not app.exception


def test_current_observed_exhaustion_is_shadow_only(monkeypatch):
    order = trade()
    start_signal = {"mode": "balanced", "version": "v3"}
    order["plan"]["movement_v3"] = start_signal
    filled = START + timedelta(seconds=2)
    monkeypatch.setattr(paper_engine, "snapshot_quotes", lambda *args: snap(filled))
    attempt_entry(order, desk(filled), None, False, lambda: filled)
    assert order["state"] == "OPEN"
    now = filled + timedelta(seconds=10)
    ctx = desk(now)
    ctx.session = now.date()
    ctx.price_provider = "Webull"
    signal = dict(start_signal, source="Webull", symbol="SPY", preview=False, at=now.isoformat(),
                  state="EXHAUSTION", scores={"exhaustion": 80., "continuation": 25.})
    newer = {"origin": now.isoformat(), "payload": {"signal": signal}}
    ctx.journal = SimpleNamespace(records=lambda *a, **k: [newer])
    market_quote = snap(now, bid=2.53, ask=2.58)[CODE]
    monkeypatch.setattr(paper_engine, "read_price", lambda ctx: {"bars_usable": False, "candles": []})
    trial = dict(deepcopy(order), track=paper_engine.POLICY)
    update_trade(order, ctx, market_quote, False)
    update_trade(trial, ctx, market_quote, False)
    assert order["state"] == "OPEN" and order["position_scenario"]["exhaustion"] == 80.
    assert trial["state"] == "CLOSED" and trial["reason"] == "EXHAUSTION · shadow exit"
    assert trial["fills"][-1]["bid"] == 2.53
    for field, bad_value in (("preview", True), ("version", "old"), ("at", START.isoformat())):
        signal[field] = bad_value
        bad = reprice(order, ctx, market_quote, .65)
        assert bad and bad["movement"] is None
        signal.update(start_signal, preview=False, at=now.isoformat())


def test_dashboard_holds_baseline_contract_and_labels_stale_marks(tmp_path):
    from research_journal import Journal
    from paper_engine import PaperLedger, dump

    journal = Journal(tmp_path / "held-position.db", dsn="")
    ledger = PaperLedger(journal)
    ledger.start(START)
    pos = dict(trade(), id="baseline", contract=CODE, remaining=1, entry_ask=2.40,
               mark_bid=2.30, mark_at=START.isoformat(), state="OPEN")
    with journal.connection() as db:
        for record in (pos, dict(pos, id="shadow", track=paper_engine.SHADOW)):
            ledger.execute(db, "INSERT INTO qd_paper_trade VALUES (?,?,?,?,?,?)", record["id"],
                           record["origin"], record["session"], record["origin"], record["state"], dump(record))
    app = AppTest.from_string("""
import streamlit as st
from types import SimpleNamespace
from datetime import timedelta
from tests.test_paper_dashboard import START
from research_journal import Journal
from contract_plan_view import pinned_positions, position_panel
ctx = SimpleNamespace(ticker="SPY", preview=False, journal=Journal(st.session_state["path"], dsn=""))
for trade in pinned_positions(ctx):
    position_panel(trade, START + timedelta(seconds=31))
""")
    app.session_state["path"] = str(tmp_path / "held-position.db")
    app.run()
    assert not app.exception
    assert len([m for m in app.markdown if "675 Call" in m.value]) == 1
    assert any("Historical bid" in item.value for item in app.caption)


def test_read_only_rank_capacity_matches_closed_and_open_book_exposure(tmp_path):
    from paper_engine import BASELINE, DAILY_STOP, EARLY, SHADOW, PaperLedger, dump, ranking_capacity
    from research_journal import Journal

    journal = Journal(tmp_path / "positions.db", dsn="")
    book = PaperLedger(journal)
    book.start(START)

    def save(db, obj):
        book.execute(db, "INSERT INTO qd_paper_trade VALUES (?,?,?,?,?,?)", obj["id"], START.isoformat(),
                     str(START.date()), START.isoformat(), obj["state"], dump(obj))

    assert ranking_capacity(journal, START, BASELINE)[0] is None
    with journal.connection() as db:
        book.set_meta(db, "heartbeat", START.isoformat())
        save(db, {"id":"base-closed","track":BASELINE,"state":"CLOSED","fills":[{"at":START.isoformat(),"pnl":-80.}]})
        save(db, {"id":"shadow-closed","track":SHADOW,"state":"CLOSED","fills":[{"at":START.isoformat(),"pnl":DAILY_STOP}]})
        save(db, {"id":"early-closed","track":EARLY,"state":"CLOSED","fills":[{"at":START.isoformat(),"pnl":-100.}]})
        save(db, {"id":"base-open","track":BASELINE,"state":"OPEN","fills":[],"entry_ask":5.,
                  "remaining":1,"mark_at":START.isoformat(),"mark_bid":4.75,"plan":{}})
    # Baseline realized = -80, marked = -26.30; additional reserved stop risk = 76.30.
    expected = abs(DAILY_STOP) - 80. - 26.30 - 76.30
    assert ranking_capacity(journal, START, BASELINE)[0] == pytest.approx(expected)
    assert ranking_capacity(journal, START, SHADOW)[0] == 0.
    assert ranking_capacity(journal, START, EARLY)[0] == pytest.approx(expected - 100.)
    assert ranking_capacity(journal, START + timedelta(seconds=31), BASELINE)[0] is None
    assert ranking_capacity(journal, START + timedelta(seconds=61), BASELINE)[0] is None
    with journal.connection() as db:
        book.set_meta(db, "loss_stop", str(START.date()))
    assert ranking_capacity(journal, START, BASELINE) == (0., "Daily paper loss stop")
    assert ranking_capacity(journal, START, EARLY) == (0., "Daily paper loss stop")


def test_capacity_propagates_to_sizing_and_withholds_unverified_alerts(monkeypatch):
    expected = [100., 0., None]
    cap = iter(expected)
    def capacity(journal, now, track):
        amount = next(cap)
        return amount, "No verified paper loss capacity" if not amount else ""
    monkeypatch.setattr(paper_engine, "ranking_capacity", capacity)
    received = []
    def recommend(ctx, plan, read, *, mode, remaining_loss):
        received.append(remaining_loss)
        result = ranked()
        if remaining_loss == 100.:
            result["selected"]["quantity"] = 1
        return result
    monkeypatch.setattr(contract_recommendation, "recommend", recommend)
    decisions = [finalize_contract(desk(START), dict(PLAN, rank_contracts=True), {}) for _ in expected]
    assert received == expected
    actual, zero, unknown = [p["contract_recommendation"] for p in decisions]
    assert decisions[0]["status"] == PLAN["status"] and actual["selected"]["quantity"] == 1
    assert actual["daily_capacity_verified"] and actual["daily_capacity"] == 100.
    assert zero["selected"] is None and zero["status"] == "unavailable" and decisions[1]["status"] == "GET READY"
    assert unknown["selected"] and unknown["status"] == "developing" and decisions[2]["status"] == "GET READY"
    assert unknown["daily_capacity_verified"] is False and "provisional" in unknown["reason"]
    assert approved_series({**trade(), "plan":decisions[2]}, START)[0] is None
