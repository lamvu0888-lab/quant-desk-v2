"""Opt-in tests using QUANT_DESK_TEST_PG_DSN on a dedicated, disposable database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import os
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from openai_intelligence import UnconfirmedRequest, reserve, structured
from research_journal import Journal


def target(dsn):
    params = conninfo_to_dict(dsn)
    host = params.get("hostaddr") or params.get("host") or os.getenv("PGHOST","")
    if host in ("localhost","::1"):
        host = "127.0.0.1"
    return (host,params.get("port") or os.getenv("PGPORT","5432"),
            params.get("dbname") or params.get("user") or os.getenv("PGDATABASE",""),
            params.get("service") or os.getenv("PGSERVICE",""))


@pytest.fixture
def worker_journals(monkeypatch):
    dsn = os.getenv("QUANT_DESK_TEST_PG_DSN")
    if not dsn:
        pytest.skip("Set QUANT_DESK_TEST_PG_DSN to a dedicated disposable PostgreSQL database")
    production = os.getenv("MONITOR_JOURNAL_DSN")
    if production and target(dsn) == target(production):
        pytest.skip("The test and configured journal DSNs point to the same database")
    connect = psycopg.connect
    with connect(dsn, autocommit=True) as admin:
        name = "qd_ai_test_" + uuid4().hex
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        try:
            # Journal opens a new real connection for each ledger operation. Preserve its
            # production timeouts and add only a per-test schema to the connection options.
            def isolated_connection(uri, **kwargs):
                assert uri == dsn
                kwargs["options"] = kwargs.get("options", "") + " -c search_path=" + name
                return connect(uri, **kwargs)
            monkeypatch.setattr(psycopg, "connect", isolated_connection)
            workers = [Journal(dsn=dsn) for _ in range(6)]
            with workers[0].connection() as db:
                assert db.execute("SELECT current_schema()").fetchone()[0] == name
            yield workers
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def test_pg_shared_group_reservation_and_daily_monthly_caps(worker_journals):
    workers = worker_journals
    day = date(2026, 11, 24)
    cfg = {"QD_V3_OPENAI_DAILY_USD": ".15", "QD_V3_OPENAI_MONTHLY_USD": ".15"}.get
    group = "openai:abc:plan:2026-11-24:SPX:v1:"
    gate = Barrier(len(workers))

    def compete(index):
        gate.wait(timeout=10)
        try:
            return reserve(workers[index],day,.06,ident=group+str(index),group=group,get=cfg)
        except UnconfirmedRequest:
            return False

    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        assert sum(pool.map(compete,range(len(workers)))) == 1

    def distinct_jobs(index):
        gate.wait(timeout=10)
        return reserve(workers[index],day,.08,ident="research:"+str(index),get=cfg)

    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        assert sum(pool.map(distinct_jobs,range(len(workers)))) == 1
    with workers[-1].connection() as db:
        count, pending = db.execute("SELECT COUNT(*), SUM(amount) FROM qdv3_usage").fetchone()
        assert count == 2 and pending == pytest.approx(.14)
    assert not reserve(workers[0], date(2026,11,25),.03,ident="new-day",get=cfg)
    assert reserve(workers[1], date(2026,12,1),.03,ident="new-month",get=cfg)


def test_pg_reconciled_usage_releases_room_for_other_worker(worker_journals):
    producer, reader = worker_journals[:2]
    model = "gpt-6.2-sol"
    now = datetime(2026,11,24,16,tzinfo=timezone.utc)
    day = now.date()
    cfg = {"QD_V3_OPENAI_DAILY_USD": ".052", "QD_V3_OPENAI_MONTHLY_USD": ".052"}.get
    schema = {"type":"object","properties":{},"required":[],"additionalProperties":False}
    lookup = Mock(return_value=SimpleNamespace(status_code=200,json=lambda:{"id":model,"object":"model"}))
    def post_response(*args,**kwargs):
        with reader.connection() as db:
            state, amount = db.execute("SELECT status, amount FROM qdv3_usage WHERE id='first'").fetchone()
        assert state == "reserved" and .032 < amount < .052
        assert not reserve(reader,day,.022,ident="would-exceed-cap",get=cfg)
        return SimpleNamespace(status_code=200,json=lambda:{
            "status":"completed","usage":{"input_tokens":100,"input_tokens_details":{"cached_tokens":0,
              "cache_write_tokens":0},"output_tokens":20},"output":[{"content":[{"type":"output_text","text":"{}"}]}]})
    post = Mock(side_effect=post_response)
    kwargs = dict(get=cfg, post=post,lookup=lookup,now=now)
    result, amount = structured(producer,"postgres-test",model,"medium","plan","test",{},schema,"first",**kwargs)
    assert result == {} and amount == pytest.approx(.0004)
    with reader.connection() as db:
        state, spent = db.execute("SELECT status,amount FROM qdv3_usage WHERE id='first'").fetchone()
        assert state == "reconciled" and spent == pytest.approx(amount)
    assert structured(reader,"postgres-test",model,"medium","plan","test",{},schema,"first",**kwargs) == (result, amount)
    assert reserve(reader,day,.022,ident="now-within-cap",get=cfg)
    assert post.call_count == 1


def test_pg_ranked_origin_survives_reconnect_and_matures_to_matched_mark(worker_journals,monkeypatch):
    from datetime import timedelta
    from rank_research import KIND, end_of_day, record_rank_scan
    from test_rank_research import DAY, FIRST, NOW, SECOND, ctx, plan
    from tradytics_feed import Feed, Snapshot

    producer, collector, analyst = worker_journals[:3]
    monkeypatch.setenv("QD_V3_RECORD","1")
    monkeypatch.setenv("QD_V3_ML","0")
    frozen = plan()
    record_rank_scan(producer,ctx(NOW),frozen)
    assert collector.records("SPY",KIND,scope=DAY)[0]["outcome"] is None
    at = NOW + timedelta(minutes=2,seconds=6)
    marks = [{"contract":code, "type":"call", "symbol":"SPY", "expiry":DAY, "updated_at":at.isoformat(),
              "bid":bid,"ask":bid+.04,"bid_size":2} for code,bid in ((FIRST,1.48),(SECOND,1.22))]
    feed = Feed(Snapshot(marks,{"source":"Webull","delay_minutes":0},at))
    record_rank_scan(collector,ctx(at,feed),{})
    result = end_of_day(analyst,"SPY",DAY,at)
    assert result["complete"] == result["paired_origins"] == 1
    assert result["median_selected_minus_median_alternative_usd"] == pytest.approx(26.)
    assert len(producer.records("SPY","contract_rank_training_day",scope=DAY)) == 1
