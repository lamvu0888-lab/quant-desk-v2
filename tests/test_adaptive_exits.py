from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from adaptive_exits import propose
from paper_engine import PaperLedger, begin_exit_trial, stamp, POLICY
from research_journal import Journal


def plan(side=1):
    return dict(entry=100., stop=100.-side, direction=side, confirmed=True, price_context_ready=True,
                hold=15, regime="Expanding", detected="2026-11-23T17:05:00+00:00",
                movement={"accelerating": True, "price_confirmed": True, "momentum": side},
                flow_confirmation={"confirmed": True})


@pytest.mark.parametrize("side", [-1, 1])
def test_profiles_and_boundary(side):
    p = plan(side)
    a = propose(p, 2., [])
    assert (a["rr"], a["rr2"]) == (2., 3.5)
    plain = propose({**p, "regime": "Normal"}, 2., [])
    assert (plain["rr"], plain["rr2"]) == (1.7, 2.5)
    wall = SimpleNamespace(kind="dealer", label="Wall", price=100+side*2.123, as_of=p["detected"])
    capped = propose(p, 2., [wall])
    assert capped["rr2"] == 2.12
    wall.as_of = "2026-11-23T17:05:01+00:00"
    assert propose(p, 2., [wall]) == a
    assert "reason" in propose({**p, "expected_remaining": 1.1}, 2., [])
    assert propose({**p, "price_context_ready": False}, 2., []) is None
    assert propose(p, None, []) is None
    box = {"midpoint": 100+side*1.5, "low": 98., "high": 102.}
    rotation = propose({**p, "range_reversal": box}, 2., [])
    assert (rotation["rr"], rotation["rr2"]) == (1.5, 2.)
    assert propose({**p, "hold": 5}, 2., [])["rr2"] == 2.5


def test_trial_rechecks_risk_at_fill_and_respects_daily_stop(tmp_path):
    now = datetime(2026, 11, 23, 17, 5, tzinfo=timezone.utc)
    journal = Journal(tmp_path / "paper.sqlite3", dsn="")
    book = PaperLedger(journal)
    book.start(now)
    p = plan()
    p["adaptive_exit"] = propose(p, 2., [])
    trade = dict(id="origin", session="2026-11-23", origin=stamp(now), symbol="QQQ",
                 state="OPEN", plan=p, fills=[], spot=100.9)
    with journal.connection() as db:
        t = begin_exit_trial(book, db, trade, False, now)
        assert t["state"] == "SKIPPED" and "minimum" in t["reason"]
        t = begin_exit_trial(book, db, {**trade, "id": "halted", "spot": 100.}, True, now)
        assert t["reason"] == "Daily paper loss stop"
        assert len(book.rows(db, trials=True)) == 2
        assert book.rows(db) == []
        assert begin_exit_trial(book, db, dict(trade, track=POLICY), False, now) is None


def test_runner_stop_requires_a_filled_tp1_and_later_observation(monkeypatch):
    from datetime import timedelta
    import paper_engine
    at = datetime(2026, 11, 23, 17, 5, tzinfo=timezone.utc)
    p = plan()
    p.update(tp1=101.7, tp2=102.5, invalidation=99.2, adaptive_exit={"profile": "Ordinary trend"})
    trade = dict(plan=p, track=POLICY, spot=100., state="OPEN", remaining=2, fills=[],
                 expiry="2026-11-23", next_bar=stamp(at), opened=stamp(at-timedelta(minutes=2)),
                 deadline=stamp(at+timedelta(minutes=8)), mark_at=stamp(at-timedelta(seconds=2)), entry_ask=2.)
    monkeypatch.setattr(paper_engine, "read_price", lambda ctx: {"candles": [], "bars_usable": False})
    ctx = SimpleNamespace(now=at, chart_quote={"at": at, "price": 101.8})
    paper_engine.update_trade(trade, ctx, None, False)
    assert "runner_stop_from" not in trade and trade["remaining"] == 2
    paper_engine.update_trade(trade, ctx, {"at": at, "bid": 2.3, "bid_size": 1}, False)
    assert trade["remaining"] == 1 and trade["runner_stop_from"] > stamp(at)
    ctx.chart_quote = {"at": at, "price": 99.9}
    paper_engine.price_exit(trade, ctx)
    assert not trade["exit_qty"]
    ctx.now += timedelta(seconds=1)
    ctx.chart_quote["at"] = ctx.now
    paper_engine.update_trade(trade, ctx, {"at": ctx.now, "bid": 1.92, "bid_size": 1}, False)
    assert trade["state"] == "CLOSED" and trade["fills"][-1]["reason"] == "STOP"


def test_opportunity_cost_includes_skips_and_excludes_unresolved():
    from adaptive_exits import performance_by_profile
    def row(state, pnl):
        return {"state": state, "pnl": pnl, "plan": {"adaptive_exit": {"profile": "Ordinary trend"}}}
    pairs = [(row("CLOSED", 10), row("CLOSED", 15)), (row("CLOSED", -5), row("CLOSED", -5)),
             (row("CLOSED", 25), row("SKIPPED", 0)), (row("OPEN", 0), row("CLOSED", 30))]
    result, = performance_by_profile(pairs, lambda t: t["pnl"])
    assert result["Paired trades"] == 2 and result["Adaptive avg win / avg loss"] == 3
    assert result["Baseline $ / opportunity"] == 10 and result["Adaptive $ / opportunity"] == 3.33
