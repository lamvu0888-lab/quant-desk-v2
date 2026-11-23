from datetime import datetime, timedelta, timezone
from copy import deepcopy
from types import SimpleNamespace

import pytest
from early_flow import confirmation, MINUTE, EARLY, STANDARD
from entry_comparison import paired_entries, summarize
from entry_score import entry_threshold
from paper_engine import valid_plan, PaperLedger
from research_journal import Journal
from terminal_data import scope_timeline
from tradytics_feed import Feed, Snapshot
from test_paper_dashboard import PLAN
from test_v25 import synthetic_inputs
from decision_engine import make_trade_plan


NOW = datetime(2026, 11, 20, 16, 3, tzinfo=timezone.utc)


def sample(side=1):
    args = synthetic_inputs()
    plan = make_trade_plan(**args)
    plan.update(symbol="SPX", score=60, direction=side, entry_bar=NOW.isoformat(), session=str(NOW.date()),
                demo=False, confirmed=True, trigger=100, price_context_ready=True, option_quote={"ready": True},
                missing=["Two consecutive completed 5m options-flow intervals in the setup direction."],
                blocks=[], data_reasons=[], rr=1.5, range_reversal=None, status="GET READY")
    plan["flow"]["floor"] = 25000
    plan["ema"]["5m"]["direction"] = side
    plan["movement"]["price_confirmed"] = True
    times = [NOW-i*MINUTE for i in (3,2,1,0)]
    candles = [{"time_utc": (t-MINUTE).isoformat(), "close": 100+side*x} for t,x in zip(times, (0,1,2,4))]
    flow = Feed(Snapshot([{"time": (t-MINUTE).isoformat(), "net_premium_usd": side*8000} for t in times[1:]],
                         {"interval": "1m"}, NOW))
    points = [{"time": t.isoformat(), "delta": side*100} for t in times[1:]]
    return plan, flow, points, candles


@pytest.mark.parametrize("side", [1,-1])
def test_early_threshold_leaves_standard_unchanged(side):
    plan, flow, points, candles = sample(side)
    original = deepcopy(plan)
    checks = confirmation(plan,NOW,flow,points,"ES footprint proxy",candles)
    assert checks["status"] == "EARLY PAPER CANDIDATE" and checks["early"]["CVD source"] == "ES footprint proxy"
    assert plan == original
    plan["score"] = 59
    assert "below 60" in " ".join(confirmation(plan,NOW,flow,points,"ES",candles)["reasons"])
    assert valid_plan(dict(PLAN, score=60)) and not valid_plan(dict(PLAN, score=59))


@pytest.mark.parametrize("fault", ["missing", "gap", "duplicate", "stale", "delayed", "five", "opposing",
                                   "missing_cvd", "weak", "quote", "risk", "feed", "premium", "trend"])
def test_early_fails_closed(fault):
    p,f,points,b = sample()
    if fault == "missing": f = Feed()
    if fault == "gap": f.snapshot.data.pop(1)
    if fault == "duplicate": points.append(points[-1])
    if fault == "delayed": f.snapshot.meta["delay_minutes"] = 15
    if fault == "five": f.snapshot.meta["interval"] = "5m"
    if fault == "opposing": points[0]["delta"] = -1
    if fault == "missing_cvd": points = []
    if fault == "weak": f.snapshot.data[1]["net_premium_usd"] = 0.01
    if fault == "quote": p["option_quote"] = {}
    if fault == "risk": p["blocks"] = ["Stop too wide"]
    if fault == "feed": p["data_reasons"] = ["5m flow delayed"]
    if fault == "premium": p["premium_review_required"] = True
    if fault == "trend": p["ema"]["5m"]["direction"] = -1
    out = confirmation(p,NOW+(MINUTE if fault=="stale" else timedelta()),f,points,"ES",b)
    assert out["reasons"] and out["status"] != "EARLY PAPER CANDIDATE"


def test_provider_interval_and_configuration(monkeypatch):
    _, f, _, _ = sample()
    f.snapshot.meta["interval"] = "5m"
    result = scope_timeline(lambda *a,**k:f,NOW,0,symbol="SPX",interval="1m")
    assert result.error
    monkeypatch.setenv("MIN_ENTRY_SCORE","64")
    assert entry_threshold() == 64
    monkeypatch.setenv("MIN_ENTRY_SCORE","101")
    with pytest.raises(ValueError): entry_threshold()


def test_early_paper_journal_and_comparison(tmp_path):
    j = Journal(tmp_path/"journal",dsn="")
    book = PaperLedger(j)
    book.start(NOW-MINUTE)
    p = dict(PLAN, symbol="SPY", score=60, session=str(NOW.date()),expiry="2026-11-20")
    assert j.record_early("early","SPY","scope",NOW,p,10*MINUTE)
    assert not j.record_early("retry","SPY","scope",NOW+MINUTE,p,10*MINUTE)
    assert not j.records("SPY","candidate") and not j.records("SPY","decision")
    with j.connection() as db:
        book.ingest(db)
        book.ingest(db)
        trades = book.rows(db)
        assert len(trades)==1 and trades[0]["track"]==EARLY
        assert db.execute("SELECT count(*) FROM qd25_delivery").fetchone()[0]==0
    early = dict(trades[0],state="CLOSED", qty=1, contract="x", entry_ask=2, spot=675,opened=str(NOW),
                 fills=[{"qty":1,"reason":"TP1","pnl":8.70}], mae_per_contract=-5.,mfe_per_contract=10.)
    signals = [{"id":"early","mode":EARLY,"plan":p,"symbol":"SPY","origin":NOW.isoformat()},
               {"id":"std","mode":STANDARD,"plan":p,"symbol":"SPY","origin":(NOW+MINUTE).isoformat()}]
    rows=summarize(signals,[early],"Former score gate",.01)
    assert rows[0]["Mean net P/L (empirical EV $)"]==6.70
    assert rows[0]["Former score gate"]=="Newly eligible (60–70)"
    assert rows[1]["Signals"]==1 and rows[1]["Closed"]==0
    pair=paired_entries(signals,[early])[0]
    assert pair["Early lead (seconds)"]==60 and pair["Early premium improvement ($/contract)"] is None


def test_early_only_worker_never_returns_delivery(tmp_path,monkeypatch):
    from monitor_engine import process_scan
    p, f, points, candles=sample()
    p.update(source="Schwab", confidence="Synchronized", price_context_ready=False)
    p["confirmation_modes"]={"status":"EARLY PAPER CANDIDATE","reasons":[]}
    monkeypatch.setattr("monitor_engine.build_decision",lambda *a,**k:(deepcopy(p),{}))
    model={"p_tp1":.6,"baseline_p_tp1":.6,"stress_p_tp1":.6,"ev_ci":(0,.1),"baseline_ev_ci":(0,.1)}
    monkeypatch.setattr("monitor_engine.model_summary",lambda *a:model)
    j=Journal(tmp_path/"journal",dsn="")
    ctx=SimpleNamespace(session=NOW.date(),ticker="SPX",now=NOW)
    for _ in range(2):
        alerts,_=process_scan(j,ctx,{"bars_usable":False,"candles":candles},check_move=False)
        assert not alerts
    assert len(j.records("SPX","early_paper_entry"))==1
