from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest
from streamlit.testing.v1 import AppTest

import paper_engine
from decision_engine import make_trade_plan
from desk_analytics import MINUTE, PriceLevel
from gemini_advisor import (AdvisorError, AdvisorQueue, BASELINE, CHANGES, FLOW_CHOICES, GeminiClient,
                            LEGS, SHADOW, trade_report, validate)
from gemini_strategy import ScenarioMonitor, assess, market_snapshot
from paper_engine import PaperLedger, PaperWorker, realized, stamp
from research_journal import Journal
from test_range_reversal import range_inputs
from test_v25 import synthetic_inputs
from tradytics_feed import Feed, Snapshot

OK_MODEL = {"p_tp1": .55, "baseline_p_tp1": .5, "stress_p_tp1": .52,
            "ev_ci": [-.1, .25], "baseline_ev_ci": [-.1, .25]}
REVIEW = {"finding": "The fill spread reduced the return.", "change": CHANGES[2],
          "experiment": "Compare two aligned closes on the next session.", "measure": "Net return after fees."}


def inputs(side=1, ranging=False):
    src = (range_inputs if ranging else synthetic_inputs)(side)
    src["cvd_proxy"] = {"delta": 12 * side, "quality": "footprint"}
    src["iv"].update(remaining_up=10, remaining_down=10)
    src["levels"].append(PriceLevel("Far wall", src["spot"] + 10 * side, "dealer"))
    src["flow_rows"][-2]["net_premium_usd"] = -5000 * side
    plan = make_trade_plan(**src)
    plan.update(symbol="SPY", source="Webull", data_reasons=[], price_context_ready=True,
                feed_timing={"flow_state": "Aligned"}, underlying_cost_bps=1)
    read = {"candles": src["candles"], "buckets": src["buckets"], "regime": src["regime"],
            "levels": src["levels"] + [PriceLevel("Planned trigger", plan["trigger"], "reference"),
                                       PriceLevel("Protective level", plan["stop"], "reference")]}
    return plan, read, src["now"]


def reply(name, setup):
    wait = {"setup": "Wait", "flow": FLOW_CHOICES[0], "trigger": "none", "invalidation": "none", "target": "none",
            "reason": "Wait for a suitable level."}
    result = {leg: wait.copy() for leg in LEGS}
    result[name] = dict(wait, setup=setup, trigger="Planned trigger", invalidation="Protective level",
                        target="Far wall", reason="Wait for a completed price trigger at the observed level.")
    return result


def daily_plan(plan, read, now, name):
    data = market_snapshot(plan, read)
    return {"id": "frozen", "state": "ready", "at": now - MINUTE, "input": data,
            "legs": validate("plan", data, reply(name, plan["setup"]))}


@pytest.mark.parametrize("side,ranging,name", [(1, False, "A"), (-1, False, "B"),
                                              (1, True, "C support"), (-1, True, "C resistance")])
def test_only_prospective_matching_price_flow_and_geometry_can_enter(side, ranging, name):
    plan, read, now = inputs(side, ranging)
    before = deepcopy(plan)
    daily = daily_plan(plan, read, now, name)
    result, reasons = assess(plan, read, daily, now)
    assert not reasons and result["gemini"]["leg"] == name
    assert result["status"] == ("ENTER LONG" if side > 0 else "ENTER SHORT")
    assert plan == before and plan["status"] == "GET READY"
    assert result["rr"] >= 1.35 and result["score"] > 70

    daily["legs"][name]["flow"] = FLOW_CHOICES[1]
    assert "two aligned" in assess(plan, read, daily, now)[1][0]
    daily["legs"][name]["flow"] = FLOW_CHOICES[0]
    second = deepcopy(plan)
    second["flow_confirmation"]["intervals"][-1]["net"] *= -1
    assert not assess(second, read, daily, now)[0]
    for change in ({"demo": True}, {"quote_at": stamp(now - MINUTE)}, {"score": 70},
                   {"price_context_ready": False}, {"data_reasons": ["Stale"]}, {"confirmed": False},
                   {"blocks": ["Stop too wide"]}, {"option_quote": {"ready": False}}, {"premium_review_required": True}):
        assert not assess(dict(plan, **change), read, daily, now)[0]
    assert not assess(plan, read, dict(daily, at=now), now)[0]
    assert not assess(plan, read, dict(daily, input=dict(daily["input"], symbol="QQQ")), now)[0]
    daily["legs"][name]["target"] = plan["entry"]
    assert not assess(plan, read, daily, now)[0]


def test_invalid_model_output_and_allowlisted_request(monkeypatch):
    plan, read, _ = inputs()
    data = market_snapshot(plan, read)
    base = reply("A", plan["setup"])
    for change in ({"trigger": "invented"}, {"invalidation": "Far wall"}, {"setup": "Sell 10 contracts"}):
        bad = deepcopy(base)
        bad["A"].update(change)
        with pytest.raises(ValueError):
            validate("plan", data, bad)
    fake = Mock(status_code=200)
    fake.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(REVIEW)}]}}]}
    post = Mock(return_value=fake)
    monkeypatch.setattr("gemini_advisor.requests.post", post)
    client = GeminiClient("fixture-only")
    assert validate("review", {}, client.generate("review", {"simulation": True})) == REVIEW
    args, kw = post.call_args
    assert "fixture-only" not in args[0] and kw["headers"]["x-goog-api-key"] == "fixture-only"
    assert kw["allow_redirects"] is False and "tools" not in kw["json"]
    fake.status_code = 429
    with pytest.raises(AdvisorError, match="HTTP 429"):
        client.generate("review", {})
    with pytest.raises(ValueError):
        validate("review", {}, dict(REVIEW, change="Increase real size"))


def test_durable_queue_retries_deduplicates_and_uses_prior_sessions_only(tmp_path):
    _, _, now = inputs()
    clock = [now]
    dbj = Journal(tmp_path/"q.sqlite", dsn="")
    q = AdvisorQueue(dbj)
    q.start()
    client = Mock(model="fixture")
    client.generate.side_effect = [AdvisorError("Gemini HTTP 429"), REVIEW]
    with dbj.connection() as db:
        for _ in range(2):
            q.enqueue(db, "review:1", "review", "SPY", str(now.date()), now, {"simulation": True})
    assert q.process_one(client, lambda: clock[0])
    assert not q.process_one(client, lambda: clock[0])
    clock[0] += MINUTE
    assert AdvisorQueue(dbj).process_one(client, lambda: clock[0])
    assert not q.process_one(client, lambda: clock[0])
    with dbj.connection() as db:
        assert db.execute("SELECT state,attempt,issue FROM qd_gemini_job").fetchone() == ("ready", 2, None)
        assert not q.prior_reviews(db, "SPY", str(now.date()), clock[0])
        assert not q.prior_reviews(db, "QQQ", "2026-11-09", clock[0])
        assert not q.prior_reviews(db, "SPY", "2026-11-09", now)
        assert q.prior_reviews(db, "SPY", "2026-11-09", clock[0])[0]["change"] == REVIEW["change"]


def test_concurrent_processing_has_one_owner_and_expired_plan_cannot_publish(tmp_path):
    _, _, now = inputs()
    journal = Journal(tmp_path/"q.sqlite", dsn="")
    queue = AdvisorQueue(journal)
    queue.start()
    began, resume = Event(), Event()
    def respond(kind, data):
        began.set()
        assert resume.wait(5)
        return REVIEW
    client = SimpleNamespace(model="fixture", generate=respond)
    with journal.connection() as db:
        queue.enqueue(db, "review:one", "review", "SPY", str(now.date()), now, {})
        queue.enqueue(db, "plan:old", "plan", "SPY", str(now.date()), now, {}, now.timestamp()-1)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(queue.process_one, client, lambda: now)
        assert began.wait(5)
        assert not queue.process_one(client, lambda: now)
        resume.set()
        assert future.result()
    with journal.connection() as db:
        assert dict(db.execute("SELECT id,state FROM qd_gemini_job").fetchall()) == {"plan:old": "expired", "review:one": "ready"}


def test_full_plan_entry_exit_review_and_next_plan(tmp_path, monkeypatch):
    plan, read, trigger_time = inputs()
    now = [trigger_time - 2*MINUTE]
    journal = Journal(tmp_path/"desk.sqlite", dsn="")
    monitor = ScenarioMonitor(journal, clock=lambda: now[0])
    q = monitor.jobs
    book = PaperLedger(journal, advisor=q)
    book.start(now[0])
    earlier, feed = deepcopy(plan), deepcopy(read)
    earlier.update(detected=stamp(now[0]), quote_at=stamp(now[0]), entry_bar=stamp(now[0]))
    feed["candles"] = feed["candles"][:-2]
    client = Mock(model="fixture")
    client.generate.side_effect = [reply("A", plan["setup"]), REVIEW, reply("A", plan["setup"])]
    # The first scan requests a daily plan. No historical entry while it is still queued.
    monitor.record(earlier, feed, now[0])
    assert q.process_one(client, lambda: now[0])
    monkeypatch.setattr("gemini_strategy.model_summary", lambda *a: OK_MODEL)
    now[0] = trigger_time
    for _ in range(2):
        monitor.record(plan, read, now[0])
    with journal.connection() as db:
        assert db.execute("SELECT count(*) FROM qd25_delivery").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM qd25_observation WHERE kind='gemini_entry'").fetchone() == (1,)
        assert q.session_plan(db,"SPY",plan["session"])["at"] < now[0]
    assert journal.records("SPY", "gemini_scan", limit=1)[0]["payload"]["baseline"] == "GET READY"
    assert journal.records("SPY", "gemini_scan", limit=1)[0]["payload"]["status"] == "PAPER CANDIDATE"

    price, bid = [plan["price"]], [2.30]
    code = "SPY" + trigger_time.strftime("%y%m%d") + "C00105000"
    class Quotes:
        def prices(self, symbols):
            return {}
        def options(self, symbol, expiry, spot, at):
            row = {"contract": code, "strike": 105, "type": "call", "multiplier": 100, "standard": True,
                   "symbol": symbol, "expiry": expiry, "bid": bid[0], "ask": 2.40, "updated_at": stamp(at)}
            return Feed(Snapshot([row], {"symbol": symbol, "expiry": expiry}, at))
        def _client(self):
            return SimpleNamespace(data=SimpleNamespace(option_market_data=SimpleNamespace(get_option_snapshot=None)))
        def _option_request(self, label, method, symbols, **params):
            return [{"symbol": code, "bid": bid[0], "ask": max(bid[0]+.01, 2.40), "bid_size": 2,
                     "ask_size": 2, "delay_minutes": 0, "quote_time": stamp(now[0])}]
    monkeypatch.setattr(paper_engine, "Desk", lambda *a, **kw: SimpleNamespace(
        ticker="SPY", now=now[0], chart_quote={"price": price[0], "at": now[0]}))
    monkeypatch.setattr(paper_engine, "read_price", lambda _: {"bars_usable": False, "candles": []})
    worker = PaperWorker(book, Quotes(), None, lambda: now[0])
    worker.poll()
    with journal.connection() as db:
        t, = book.rows(db)
        assert t["track"] == SHADOW and t["state"] == "OPEN" and t["qty"] == 2
    now[0] += timedelta(seconds=10)
    price[0], bid[0] = plan["tp1"], 2.80
    worker.poll()
    book.queue_reviews()
    assert not q.process_one(client, lambda: now[0])
    now[0] += timedelta(seconds=10)
    price[0], bid[0] = plan["stop"]-.01, 1.75
    worker.poll()
    with journal.connection() as db:
        t, = book.rows(db)
        assert t["state"] == "CLOSED" and len(t["fills"]) == 2
        book.save(db, t, now[0])
        assert db.execute("SELECT count(*) FROM qd_gemini_job WHERE kind='review'").fetchone() == (0,)
    # The trade is durably closed before even a failed/restarted advisor runs.
    for _ in range(2):
        PaperLedger(journal, q).queue_reviews()
    with journal.connection() as db:
        assert db.execute("SELECT count(*) FROM qd_gemini_job WHERE kind='review'").fetchone() == (1,)
    assert q.process_one(client, lambda: now[0])
    report = client.generate.call_args.args[1]
    assert report["net_pnl_usd"] == realized(t)
    assert report["estimated_fees_usd"] == 2.60 and report["holding_seconds"] == 20
    assert report["scenario"]["leg"] == "A"
    assert report["trade"]["entry_ask"] == 2.40 and report["exit_fills"][-1]["reason"] == "STOP"
    assert "private-value" not in json.dumps(trade_report(dict(t, credentials="private-value",
                                                            plan=dict(t["plan"], webhook="private-value"))))
    next_day = trigger_time + timedelta(days=3)
    now[0] = next_day
    tomorrow = dict(plan, detected=stamp(next_day), session=str(next_day.date()))
    monitor.daily(tomorrow, read, next_day)
    assert q.process_one(client, lambda: now[0])
    prior, = client.generate.call_args.args[1]["prior_reviews"]
    assert prior["change"] == CHANGES[2] and prior["net_pnl_usd"] == realized(t)
    checks = client.generate.call_args.args[1]["previous_session_checks"]
    assert checks["observed_minutes"] == 2 and checks["session"] == plan["session"]
    monitor.close()


def test_paper_books_have_separate_loss_stops_and_reviews_include_original(tmp_path, monkeypatch):
    _, _, now = inputs()
    j = Journal(tmp_path/"desk.sqlite", dsn="")
    q = AdvisorQueue(j)
    q.start()
    b = PaperLedger(j,q)
    b.start(now - MINUTE)
    def row(ident, track, state):
        return {"id": ident, "track": track, "symbol": "SPY", "origin": stamp(now), "session": str(now.date()),
                "plan": {"setup": "test"}, "state": state, "fills":[]}
    with j.connection() as db:
        for item in (row("loss", SHADOW, "CLOSED"), row("a", BASELINE, "PENDING"), row("b", SHADOW, "PENDING")):
            if item["id"] == "loss":
                item["fills"] = [{"at":stamp(now), "pnl":-510}]
            b.execute(db, "INSERT INTO qd_paper_trade VALUES (?,?,?,?,?,?)", item["id"], item["origin"],
                      item["session"], stamp(now), item["state"], json.dumps(item))
    received = {}
    def attempt(item, ctx, webull, halted, get_now):
        received[item["track"]] = halted
        item.update(state="SKIPPED",reason="fixture")
    monkeypatch.setattr(paper_engine, "attempt_entry", attempt)
    PaperWorker(b, None, None, lambda: now).poll()
    assert received == {BASELINE:False, SHADOW:True}
    t = row("closed-original", BASELINE, "CLOSED")
    t["fills"] = [{"pnl":12.00}]
    with j.connection() as db:
        q.review(db,t,now)
        assert not b.meta(db, "loss_stop")
        assert b.meta(db, "gemini_loss_stop") == str(now.date())
        assert json.loads(db.execute("SELECT input FROM qd_gemini_job WHERE kind='review'").fetchone()[0])["track"] == BASELINE


def test_rendered_paper_journal_handles_existing_and_new_schema(tmp_path, monkeypatch):
    from pathlib import Path
    _, _, now = inputs()
    j = Journal(tmp_path/"public.sqlite", dsn="")
    b = PaperLedger(j)
    b.start(now)
    monkeypatch.setenv("MONITOR_JOURNAL_PATH", str(j.path))
    monkeypatch.setenv("PAPER_HIDE_DEMO", "1")
    app = AppTest.from_file(Path(__file__).resolve().parents[1]/"paper_dashboard.py").run()
    assert not app.exception
    assert any("Gemini reviews unavailable" in c.value for c in app.caption)
    AdvisorQueue(j).start()
    app.run()
    assert not app.exception and any("verified exit fills" in c.value for c in app.caption)


def test_model_veto_is_recorded_and_dashboard_shows_published_scenario(tmp_path, monkeypatch):
    plan, read, now = inputs()
    journal = Journal(tmp_path/"veto.sqlite", dsn="")
    monitor = ScenarioMonitor(journal, clock=lambda: now)
    early = now - 2 * MINUTE
    client = Mock(model="fixture")
    client.generate.return_value = reply("A", plan["setup"])
    monitor.daily(dict(plan, detected=stamp(early)), read, early)
    assert monitor.jobs.process_one(client, lambda: early)
    bad = dict(OK_MODEL, ev_ci=[-1,-.3], baseline_ev_ci=[-.6,-.1])
    monkeypatch.setattr("gemini_strategy.model_summary", lambda *a: bad)
    monitor.record(plan, read, now)
    assert not journal.records("SPY", "gemini_entry")
    first, = journal.records("SPY", "gemini_scan")
    assert "negative expectancy" in first["payload"]["reasons"][0]
    app = AppTest.from_string(
        "import streamlit as st\nfrom gemini_views import scenario_panel\n"
        "scenario_panel(st.session_state.test_ctx)")
    app.session_state["test_ctx"] = SimpleNamespace(preview=False, ticker="SPY", session=now.date(), journal=journal)
    app.run()
    assert not app.exception
    assert set(app.dataframe[0].value["Scenario"]) == set(LEGS)
    assert "negative expectancy" in app.dataframe[1].value["Paper checks"].iloc[0]
    monitor.close()


def test_failed_plan_response_retries_with_fresh_snapshot_and_freezes_after_success(tmp_path):
    plan, read, now = inputs()
    journal = Journal(tmp_path/"retry.sqlite", dsn="")
    monitor = ScenarioMonitor(journal, clock=lambda: now)
    monitor.daily(plan, read, now)
    client = Mock(model="fixture")
    client.generate.side_effect = [{}, reply("A", plan["setup"])]
    assert monitor.jobs.process_one(client, lambda: now)
    with journal.connection() as db:
        assert monitor.jobs.session_plan(db,"SPY",plan["session"])["state"] == "retry"
    later = now + timedelta(minutes=11)
    assert not monitor.jobs.process_one(client, lambda: later)
    newer = dict(plan, detected=stamp(later), price=plan["price"]+.01)
    monitor.daily(newer, read, later)
    assert monitor.jobs.process_one(client, lambda: later)
    saved = monitor.daily(plan, read, later+MINUTE)
    assert saved["input"]["spot"] == newer["price"]
    assert saved["at"] == later and saved["state"] == "ready" and client.generate.call_count == 2
    monitor.close()
