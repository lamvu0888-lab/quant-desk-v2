"""Timestamped observations, paper outcomes and Discord delivery claims."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from data_utils import utc

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS qd25_observation (
        id TEXT PRIMARY KEY, symbol TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,
        origin TEXT NOT NULL, payload TEXT NOT NULL, outcome TEXT)""",
    "CREATE INDEX IF NOT EXISTS qd25_search ON qd25_observation (symbol, kind, scope, origin)",
    "CREATE TABLE IF NOT EXISTS qd25_delivery (id TEXT PRIMARY KEY, state TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS qd26_entry_cooldown (
        symbol TEXT NOT NULL, setup TEXT NOT NULL, origin TEXT NOT NULL, PRIMARY KEY (symbol, setup))""",
)


class Journal:
    def __init__(self, path=None, dsn=None):
        self.dsn = dsn if dsn is not None else os.getenv("MONITOR_JOURNAL_DSN", "")
        self.path = Path(path or os.getenv("MONITOR_JOURNAL_PATH") or
                         Path(__file__).resolve().parent / "monitor_data" / "runtime" / "quant_desk.sqlite3")
        if not self.dsn:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            for statement in SCHEMA:
                db.execute(statement)

    @contextmanager
    def connection(self):
        if self.dsn:
            import psycopg

            db = psycopg.connect(self.dsn, connect_timeout=4, options="-c statement_timeout=4500")
        else:
            db = sqlite3.connect(self.path, timeout=4)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def sql(self, statement):
        return statement.replace("?", "%s") if self.dsn else statement

    def insert(self, event_id, symbol, kind, scope, origin, payload):
        with self.connection() as db:
            return self._insert(db, event_id, symbol, kind, scope, origin, payload)

    def _insert(self, db, event_id, symbol, kind, scope, origin, payload):
        cur = db.execute(self.sql("INSERT INTO qd25_observation (id,symbol,kind,scope,origin,payload) "
                                  "VALUES (?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING"),
                         (event_id, symbol, kind, scope, utc(origin).isoformat(timespec="microseconds"),
                          json.dumps(payload, allow_nan=False, separators=(",", ":"))))
        return cur.rowcount == 1

    def record_candidate(self, event_id, symbol, scope, origin, payload, *, alert_id, cooldown):
        """Record a paper candidate and reserve its entry alert in one transaction."""
        payload = {**payload, "entry_alert_id": None}
        with self.connection() as db:
            inserted = self._insert(db, event_id, symbol, "candidate", scope, origin, payload)
            if not inserted or not alert_id:
                return inserted, False
            reserved = self._claim_entry_window(db, symbol, payload["plan"]["setup"], origin, cooldown)
            if reserved:
                payload["entry_alert_id"] = alert_id
                db.execute(self.sql("UPDATE qd25_observation SET payload = ? WHERE id = ?"),
                           (json.dumps(payload, allow_nan=False, separators=(",", ":")), event_id))
            return True, reserved

    def settle(self, event_id, outcome):
        with self.connection() as db:
            cur = db.execute(self.sql("UPDATE qd25_observation SET outcome = ? WHERE id = ? AND outcome IS NULL"),
                             (json.dumps(outcome, allow_nan=False, separators=(",", ":")), event_id))
            return cur.rowcount == 1

    def records(self, symbol, kind, *, scope=None, limit=200, pending=False, settled=False):
        query = "SELECT id,origin,payload,outcome FROM qd25_observation WHERE symbol = ? AND kind = ?"
        args = [symbol, kind]
        if scope is not None:
            query += " AND scope = ?"
            args.append(scope)
        if pending:
            query += " AND outcome IS NULL"
        if settled:
            query += " AND outcome IS NOT NULL"
        with self.connection() as db:
            rows = db.execute(self.sql(query + " ORDER BY origin DESC, id DESC LIMIT ?"), (*args, limit)).fetchall()
        return [{"id": event_id, "origin": origin, "payload": json.loads(payload),
                 "outcome": json.loads(outcome) if outcome else None} for event_id, origin, payload, outcome in rows]

    def claim_delivery(self, event_id):
        """A single claimant sends. An ambiguous timeout retains its claim."""
        with self.connection() as db:
            cur = db.execute(self.sql("INSERT INTO qd25_delivery (id,state) VALUES (?,?) ON CONFLICT (id) DO NOTHING"),
                             (event_id, "claimed"))
            return cur.rowcount == 1

    def _claim_entry_window(self, db, symbol, setup, now, cooldown):
        """Reserve one entry alert per ticker and setup in a shared database."""
        now = utc(now)
        timestamp = now.isoformat(timespec="microseconds")
        cutoff = (now - cooldown).isoformat(timespec="microseconds")
        cur = db.execute(self.sql("INSERT INTO qd26_entry_cooldown (symbol,setup,origin) VALUES (?,?,?) "
                                  "ON CONFLICT (symbol,setup) DO UPDATE SET origin = excluded.origin "
                                  "WHERE qd26_entry_cooldown.origin <= ?"),
                         (symbol, setup, timestamp, cutoff))
        return cur.rowcount == 1

    def was_delivered(self, event_id):
        with self.connection() as db:
            row = db.execute(self.sql("SELECT state FROM qd25_delivery WHERE id = ?"), (event_id,)).fetchone()
        return bool(row and row[0] == "sent")

    def delivery_state(self, event_id, state):
        with self.connection() as db:
            db.execute(self.sql("UPDATE qd25_delivery SET state = ? WHERE id = ?"), (state, event_id))

    def release_throttled_delivery(self, event_id):
        with self.connection() as db:
            db.execute(self.sql("DELETE FROM qd25_delivery WHERE id = ? AND state = 'claimed'"), (event_id,))


def plan_scope(plan):
    dte = plan.get("dte")
    return f"{plan['source']}:{dte if dte is not None else 'all'}"
