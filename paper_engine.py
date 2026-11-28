"""Paper execution over confirmed Quant Desk journal entries."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3

import psycopg

from adaptive_exits import POLICY
from data_utils import DataError, reported_delay
from decision_engine import MIN_REWARD_R
from entry_score import MIN_ENTRY_SCORE
from early_flow import EARLY
from gemini_advisor import BASELINE, SHADOW
from live_chain import contract_symbol, occ_info, unwrap
from v3_provenance import display_plan
from market_session import ET, rth_bounds, session_state
from option_quotes import fresh_option_rows, spread_fraction
from ranked_position import approved_series, new_fill_quantity, open_risk, reprice
from terminal_data import first_listed_dte, listed_dtes, numeric, positive, time_or_none
from terminal_views import Desk, read_price
from webull_feed import supports_options

UTC = timezone.utc
MINUTE = timedelta(minutes=1)
QUOTE_AGE = 30
ENTRY_WINDOW = 90
FEE = 0.65
MAX_DEBIT = 1000
DAILY_STOP = -500
PREMIUM_STOP = 0.20
ENTRY = {"ENTER LONG": 1, "ENTER SHORT": -1}
LOSS_KEYS = {POLICY: "adaptive_exit_loss_stop", BASELINE: "loss_stop", SHADOW: "gemini_loss_stop", EARLY: "early_loss_stop"}
HEARTBEAT_MAX_AGE = 60


def clock():
    return datetime.now(UTC)


def stamp(at):
    return at.astimezone(UTC).isoformat(timespec="microseconds")


def ceil_minute(at):
    start = at.replace(second=0, microsecond=0)
    return start if at == start else start + MINUTE


def dump(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


class PaperLedger:
    def __init__(self, journal, advisor=None):
        self.journal, self.advisor = journal, advisor

    def start(self, now):
        with self.journal.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS qd_paper_trade (
                id TEXT PRIMARY KEY, origin TEXT NOT NULL, session TEXT NOT NULL,
                updated TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS qd_exit_trial (
                id TEXT PRIMARY KEY, origin TEXT NOT NULL, session TEXT NOT NULL,
                updated TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS qd_exit_active ON qd_exit_trial(state,updated)")
            db.execute("CREATE INDEX IF NOT EXISTS qd_paper_active ON qd_paper_trade(state,updated)")
            db.execute("CREATE TABLE IF NOT EXISTS qd_paper_meta (key TEXT PRIMARY KEY, body TEXT NOT NULL)")
            self.execute(db, "INSERT INTO qd_paper_meta VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                         "activated", dump(stamp(now)))
            if self.advisor:
                self.execute(db, "INSERT INTO qd_paper_meta VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                             "gemini_activated", dump(stamp(now)))

    def queue_reviews(self):
        """Closed positions are the durable outbox even if advisory tables or Google are unavailable."""
        with self.journal.connection() as db:
            since = self.meta(db, "gemini_activated")
            if since:
                missed = self.execute(db, """SELECT p.body FROM qd_paper_trade p
                    WHERE p.state='CLOSED' AND p.updated>=? AND NOT EXISTS (SELECT 1 FROM qd_gemini_job g
                    WHERE g.id=('review:' || p.id)) ORDER BY p.updated,p.id LIMIT 50""", since).fetchall()
                for (body,) in missed:
                    trade = json.loads(body)
                    self.advisor.review(db, trade, time_or_none(trade["closed"]))

    def execute(self, db, sql, *args):
        return db.execute(self.journal.sql(sql), args)

    @contextmanager
    def lock(self):
        if self.journal.dsn:
            with self.journal.connection() as db:
                db.autocommit = True
                if not db.execute("SELECT pg_try_advisory_lock(283931485)").fetchone()[0]:
                    raise RuntimeError("Another paper worker is already active.")
                yield lambda: db.execute("SELECT 1")
        else:
            with closing(sqlite3.connect(str(self.journal.path) + ".paper-lock.sqlite3", timeout=0)) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    yield lambda: None
                finally:
                    db.rollback()

    def meta(self, db, key, default=None):
        row = self.execute(db, "SELECT body FROM qd_paper_meta WHERE key=?", key).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, db, key, value):
        self.execute(db, """INSERT INTO qd_paper_meta VALUES (?,?)
                     ON CONFLICT(key) DO UPDATE SET body=excluded.body""", key, dump(value))

    def ingest(self, db):
        alert = ("CAST(o.payload AS jsonb)->>'entry_alert_id'" if self.journal.dsn
                 else "json_extract(o.payload,'$.entry_alert_id')")
        rows = self.execute(db, f"""SELECT d.id,o.symbol,o.origin,o.payload
            FROM qd25_observation o JOIN qd25_delivery d ON d.id=({alert}) AND d.state='sent'
            WHERE o.kind='candidate' AND o.origin>=?
            AND NOT EXISTS (SELECT 1 FROM qd_paper_trade p WHERE p.id=d.id)
            ORDER BY o.origin,o.id LIMIT 200""", self.meta(db, "activated")).fetchall()
        entries = [(ident, symbol, origin, json.loads(raw), BASELINE) for ident, symbol, origin, raw in rows]
        early = self.execute(db, """SELECT o.id,o.symbol,o.origin,o.payload FROM qd25_observation o
            WHERE o.kind='early_paper_entry' AND o.origin>=?
            AND NOT EXISTS (SELECT 1 FROM qd_paper_trade p WHERE p.id=o.id)
            ORDER BY o.origin,o.id LIMIT 200""", self.meta(db, "activated")).fetchall()
        entries.extend((ident, symbol, origin, json.loads(raw), EARLY) for ident, symbol, origin, raw in early)
        if self.advisor:
            shadow = self.execute(db, """SELECT o.id,o.symbol,o.origin,o.payload FROM qd25_observation o
                WHERE o.kind='gemini_entry' AND o.origin>=?
                AND NOT EXISTS (SELECT 1 FROM qd_paper_trade p WHERE p.id=o.id)
                ORDER BY o.origin,o.id LIMIT 200""", self.meta(db, "activated")).fetchall()
            entries.extend((ident, symbol, origin, json.loads(raw), SHADOW) for ident, symbol, origin, raw in shadow)
        for ident, symbol, origin, payload, track in entries:
            if track == BASELINE and payload.get("decision_status") not in ENTRY:
                continue
            day = time_or_none(origin).astimezone(ET).date().isoformat()
            trade = {"id": ident, "symbol": symbol, "origin": origin, "session": day, "track": track,
                     "plan": {**payload["plan"], **({"model": payload["model"]} if payload.get("model") else {})},
                     "state": "PENDING",
                     "reason": "Waiting for a paper fill", "fills": []}
            self.execute(db, "INSERT INTO qd_paper_trade VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                         ident, origin, day, origin, trade["state"], dump(trade))

    def rows(self, db, where="", *args, trials=False):
        table = "qd_exit_trial" if trials else "qd_paper_trade"
        result = self.execute(db, f"SELECT body FROM {table} " + where + " ORDER BY origin,id", *args)
        return [json.loads(row[0]) for row in result.fetchall()]

    def save(self, db, row, now):
        table = "qd_exit_trial" if row.get("track") == POLICY else "qd_paper_trade"
        self.execute(db, f"UPDATE {table} SET updated=?,state=?,body=? WHERE id=?",
                     stamp(now), row["state"], dump(row), row["id"])


def begin_exit_trial(book, db, trade, halted, now):
    proposal = trade["plan"].get("adaptive_exit")
    if trade.get("track", BASELINE) != BASELINE or trade["state"] != "OPEN" or not proposal:
        return None
    plan = dict(trade["plan"])
    side, spot = plan["direction"], trade["spot"]
    reason = "Daily paper loss stop" if halted else proposal.get("reason")
    if not reason:
        if side * (proposal["tp1"] - spot) + 1e-9 < MIN_REWARD_R * side * (spot - plan["stop"]):
            reason = "Adaptive TP1 below minimum reward/risk at fill"
        else:
            plan.update({k: proposal[k] for k in ("tp1", "tp2", "rr", "rr2")})
    trial = {**trade, "track": POLICY, "fills": [], "plan": plan}
    if reason:
        # Keep the signal link. A skipped trial has no simulated position.
        trial = {k: trial[k] for k in ("id", "origin", "session", "symbol", "track", "plan", "fills")}
        skip(trial, reason)
    book.execute(db, "INSERT INTO qd_exit_trial VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                 trial["id"], trial["origin"], trial["session"], stamp(now), trial["state"], dump(trial))
    return trial


def fast_setup(plan):
    movement = plan.get("movement", {})
    return (not movement.get("range_hold") and movement.get("market") != "SIDEWAYS"
            and bool(movement.get("accelerating") or (
                plan.get("regime") == "Expanding" and movement.get("price_confirmed")
                and movement.get("momentum") == plan.get("direction"))))


def valid_plan(plan):
    direction = ENTRY.get(plan.get("status"))
    prices = [positive(plan.get(k)) for k in ("stop", "entry", "tp1", "tp2")]
    return bool(direction and direction == plan.get("direction") and not display_plan(plan)
                and (numeric(plan.get("score")) or 0) >= MIN_ENTRY_SCORE
                and 0 < (numeric(plan.get("hold")) or 0) <= 15 and all(prices)
                and all(direction * (b - a) > 0 for a, b in zip(prices, prices[1:]))
                and positive(plan.get("trigger")) and positive(plan.get("invalidation")))


def expiry_for(ctx, day, fast):
    if fast:
        return day.isoformat()
    feed = ctx.get("expirations", symbol=ctx.ticker)
    if first_listed_dte(feed, ctx.ticker, day, ctx.now) is not None:
        dte = next((value for value in listed_dtes(feed, day) if value > 0), None)
        if dte:
            return (day + timedelta(days=dte)).isoformat()
    return None


def candidate_contracts(chain, symbol, expiry, spot, direction, fast, now):
    right, root = (("call", "C") if direction > 0 else ("put", "P")), ("SPXW" if symbol == "SPX" else symbol)
    choices = []
    for row in fresh_option_rows(chain, symbol, expiry, now, max_age=QUOTE_AGE):
        code = row.get("contract")
        if row.get("type") != right[0] or row.get("multiplier") != 100 or not row.get("standard"):
            continue
        try:
            series, expires, side, strike = occ_info(code)
        except (DataError, ValueError, TypeError):
            continue
        if series != root or str(expires) != expiry or side != right[1] or strike != row.get("strike"):
            continue
        distance = direction * (strike / spot - 1)
        if (fast and not 0 < distance <= .005) or (not fast and abs(distance) > .0075):
            continue
        choices.append((abs(distance), spread_fraction(row), code, strike))
    return sorted(choices)


def snapshot_quotes(webull, codes, get_now=clock):
    """Fetch exact option codes so an open strike remains visible as spot moves."""
    result = {}
    if getattr(getattr(webull, "settings", None), "endpoint", None) == "api.sandbox.webull.com":
        return result
    codes = sorted(set(codes))
    for offset in range(0, len(codes), 20):
        group = codes[offset:offset + 20]
        raw = webull._option_request("Paper option snapshots",
                                     webull._client().data.option_market_data.get_option_snapshot,
                                     symbols=",".join(group), category="US_OPTION")
        rows, _ = unwrap(raw)
        now = get_now()
        seen, duplicates = set(), set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(contract_symbol(row) or "").replace(" ", "").upper()
            if code not in group:
                continue
            if code in seen:
                duplicates.add(code)
            seen.add(code)
            at = time_or_none(row.get("quote_time"))
            delay = reported_delay(row)
            if delay is None:
                delay = reported_delay(raw, code)
            if not at or delay != 0 or not 0 <= (now - at).total_seconds() <= QUOTE_AGE:
                continue
            bid = positive(row.get("bid"))
            bid_size = numeric(row.get("bid_size"))
            ask = positive(row.get("ask"))
            if bid and (bid_size is None or bid_size >= 1) and (ask is None or ask >= bid):
                result[code] = {"at": at, "bid": bid, "ask": ask,
                                "bid_size": bid_size, "ask_size": numeric(row.get("ask_size"))}
        for code in duplicates:
            result.pop(code, None)
    return result


def skip(trade, reason):
    trade.update(state="SKIPPED", reason=reason)


def attempt_entry(trade, ctx, webull, halted, get_now=clock, *, remaining_risk=None):
    plan, symbol, now = trade["plan"], trade["symbol"], ctx.now
    alert = time_or_none(trade["origin"])
    if getattr(ctx, "preview", False) or display_plan(plan):
        return skip(trade, "Synthetic data excluded from paper execution")
    if halted:
        return skip(trade, "Daily paper loss stop")
    if not valid_plan(plan) or not supports_options(symbol):
        return skip(trade, "Invalid Trade Plan or unsupported option root")
    bounds = rth_bounds(trade["session"])
    if not bounds or not bounds[0] <= now < bounds[1] or (now - alert).total_seconds() >= ENTRY_WINDOW:
        return skip(trade, "Fill window elapsed · " + trade["reason"])
    quote = ctx.chart_quote
    if not quote or not 0 <= (now - quote["at"]).total_seconds() <= 45:
        trade["reason"] = "Waiting for current underlying price"
        return
    side, spot = plan["direction"], quote["price"]
    if (side * (spot - plan["trigger"]) <= 0 or side * (spot - plan["stop"]) <= 0
            or side * (plan["tp1"] - spot) <= 0):
        return skip(trade, "Underlying no longer inside entry setup")
    if trade.get("track") in (SHADOW, EARLY) and side * (plan["tp1"] - spot) < MIN_REWARD_R * side * (spot - plan["stop"]):
        return skip(trade, "Scenario reward/risk fell below the minimum at fill time")
    day, fast = now.astimezone(ET).date(), fast_setup(plan)
    ranking = plan.get("contract_recommendation") or {}
    ranked = bool(plan.get("rank_contracts") or ranking)
    selected = ranking.get("selected") or {}
    if ranked:
        spec, issue = approved_series(trade, now)
        if issue:
            return skip(trade, issue)
        expiry = selected["expiry"]
        choices = [(0., 0., spec.code, spec.strike)]
        trade["reason"] = "Waiting for a fresh post-alert executable quote on the ranked contract"
    else:
        # A signal written by a prior release can finish under its original contract rule.
        expiry = expiry_for(ctx, day, fast)
        if not expiry:
            trade["reason"] = "Waiting for next listed expiration"
            return
        chain = webull.options(symbol, expiry, spot, now)
        choices = candidate_contracts(chain, symbol, expiry, spot, side, fast, now)
        trade["reason"] = "Waiting for exact-expiry Webull contracts" if not choices else "Waiting for executable Webull quotes"
        if not choices:
            return
    quotes = snapshot_quotes(webull, [c[2] for c in choices], get_now)
    filled_at = get_now()
    deadline = min(ceil_minute(alert) + timedelta(minutes=plan["hold"]), bounds[1] - timedelta(seconds=30))
    if ranked:
        if filled_at >= time_or_none(ranking["expires_at"]):
            return skip(trade, "Ranked contract expired during executable quote verification")
        deadline = min(deadline, spec.last_trade, spec.expires)
    if not quote["at"] <= filled_at < min(alert + timedelta(seconds=ENTRY_WINDOW), deadline):
        return skip(trade, "Entry window elapsed during lookup")
    if (filled_at - quote["at"]).total_seconds() > 45:
        return
    for _, _, code, strike in choices:
        row = quotes.get(code)
        if not row or row["at"] < alert or not 0 <= (filled_at-row["at"]).total_seconds() <= QUOTE_AGE or not row["ask"]:
            continue
        spread = spread_fraction(row)
        cost = row["ask"] * 100 + FEE
        size = row["ask_size"]
        qty = min(2, math.floor(MAX_DEBIT / cost), math.floor(size) if size is not None else 1)
        if ranked:
            if row["at"] < time_or_none(ranking["origin"]):
                continue
            if row["ask"] > selected["ask"]:
                trade["reason"] = "Executable ask exceeds the ranked entry limit"
                continue
            if spread is None or spread > .20:
                trade["reason"] = "Executable spread exceeds the 20% maximum"
                continue
            if qty < 1:
                trade["reason"] = "Executable ask size or per-trade budget cannot cover one contract"
                continue
            capacity = abs(DAILY_STOP) if remaining_risk is None else remaining_risk
            qty = new_fill_quantity(selected, row, capacity, MAX_DEBIT, FEE, PREMIUM_STOP)
            if qty < 1:
                trade["reason"] = "Remaining daily modeled loss capacity cannot cover one ranked contract"
                continue
        if spread is None or spread > .20 or qty < 1:
            continue
        trade.update(state="OPEN", reason="Tracking Trade Plan", qty=qty, remaining=qty,
                     fast=fast, dte=(datetime.fromisoformat(expiry).date() - day).days,
                     contract=code, expiry=expiry, strike=strike, spot=spot, spread=spread,
                     cost=round(cost * qty, 2), entry_ask=row["ask"], opened=stamp(filled_at),
                     entry_quote_at=stamp(row["at"]), next_bar=stamp(ceil_minute(filled_at)),
                     mark_bid=row["bid"], mark_at=stamp(row["at"]), deadline=stamp(deadline))
        if ranked:
            trade["last_trade_at"] = stamp(min(spec.last_trade, spec.expires))
        return
    if not ranked:
        trade["reason"] = "No fresh post-alert quote met spread, size and premium limits"


def queue_exit(trade, at, reason):
    qty = 1 if reason == "TP1" else trade["remaining"]
    if reason == "TP1":
        trade["tp1_seen"] = True
    trade.update(exit_qty=qty, exit_at=stamp(at), exit_reason=reason, reason=f"{reason} · waiting for bid")


def full_exit_pending(trade):
    return trade.get("exit_qty", 0) >= trade["remaining"]


def price_exit(trade, ctx):
    now, plan = ctx.now, trade["plan"]
    if full_exit_pending(trade):
        return
    direction = plan["direction"]
    cursor, deadline = (time_or_none(trade[k]) for k in ("next_bar", "deadline"))
    tightened_at = time_or_none(trade.get("runner_stop_from"))
    def protective_stop(observed):
        if tightened_at and observed >= tightened_at:
            return trade["spot"]
        return plan["stop"]

    prices = read_price(ctx)
    for bar in prices["candles"] if prices["bars_usable"] else ():
        at = time_or_none(bar.get("time_utc"))
        if not at or at < cursor:
            continue
        closed = at + MINUTE
        if closed > min(now, deadline) or not bar.get("closed", True):
            break
        # Accept known later minutes even after a feed gap. Never invent a touch in a gap.
        trade["next_bar"] = stamp(closed)
        adverse, favorable = (bar["low"], bar["high"]) if direction == 1 else (bar["high"], bar["low"])
        if direction * (adverse - protective_stop(at)) <= 0:
            queue_exit(trade, closed, "STOP")
        elif direction * (favorable - plan["tp2"]) >= 0:
            queue_exit(trade, closed, "TP2")
        elif direction * (bar["close"] - plan["invalidation"]) < 0:
            queue_exit(trade, closed, "INVALIDATED")
        elif not trade.get("tp1_seen") and direction * (favorable - plan["tp1"]) >= 0:
            queue_exit(trade, closed, "TP1")
        if full_exit_pending(trade):
            return
    if now >= deadline:
        return queue_exit(trade, deadline, "TIME LIMIT")
    quote = ctx.chart_quote
    if not quote or not time_or_none(trade["opened"]) <= quote["at"] < deadline:
        return
    price = quote["price"]
    if direction * (price - protective_stop(quote["at"])) <= 0:
        queue_exit(trade, quote["at"], "STOP")
    elif direction * (price - plan["tp2"]) >= 0:
        queue_exit(trade, quote["at"], "TP2")
    elif not trade.get("tp1_seen") and direction * (price - plan["tp1"]) >= 0:
        queue_exit(trade, quote["at"], "TP1")


def option_pnl(trade, bid, qty):
    return round(qty * ((bid - trade["entry_ask"]) * 100 - 2 * FEE), 2)


def update_trade(trade, ctx, quote, halted):
    now = ctx.now
    if now >= (time_or_none(trade.get("last_trade_at")) or rth_bounds(trade["expiry"])[1]):
        trade.update(state="UNRESOLVED", reason="Contract expired without a verified exit bid")
        return
    price_exit(trade, ctx)
    if halted and not full_exit_pending(trade):
        queue_exit(trade, now, "DAILY LOSS STOP")
    if not quote or quote["at"] < time_or_none(trade["opened"]):
        return
    at, bid = quote["at"], quote["bid"]
    if at < time_or_none(trade["mark_at"]) or not 0 <= (now - at).total_seconds() <= QUOTE_AGE:
        return
    trade.update(mark_bid=bid, mark_at=stamp(at))
    if trade["plan"].get("contract_recommendation"):
        try:
            view = reprice(trade, ctx, quote, FEE)
        except (TypeError, ValueError, ArithmeticError, DataError):
            view = None
        trade["position_scenario"] = view
        # Challenger only. Require a post-fill exhaustion reading and a fresh bid before
        # studying this exit. Baseline, partial fills and protective stops stay independent.
        if (trade.get("track") == POLICY and view and view.get("movement") == "EXHAUSTION"
                and not trade.get("exit_qty") and bid > trade["entry_ask"] and
                time_or_none(view["movement_at"]) <= at):
            queue_exit(trade, at, "EXHAUSTION · shadow exit")
    if "mae_per_contract" in trade:
        excursion = (bid - trade["entry_ask"]) * 100
        trade["mae_per_contract"] = min(trade["mae_per_contract"], excursion)
        trade["mfe_per_contract"] = max(trade["mfe_per_contract"], excursion)
    if bid <= trade["entry_ask"] * (1 - PREMIUM_STOP) and not full_exit_pending(trade):
        queue_exit(trade, at, "20% PREMIUM STOP")
    last_exit = time_or_none(trade.get("last_exit_quote"))
    if not trade.get("exit_qty") or at < time_or_none(trade["exit_at"]) or (last_exit and at <= last_exit):
        return
    size = quote["bid_size"]
    qty = min(trade["exit_qty"], math.floor(size) if size is not None else 1)
    trade["fills"].append({"at": stamp(now), "quote_at": stamp(at), "qty": qty, "bid": bid,
                           "pnl": option_pnl(trade, bid, qty), "reason": trade["exit_reason"]})
    trade["last_exit_quote"] = stamp(at)
    trade["remaining"] -= qty
    trade["exit_qty"] -= qty
    if not trade["remaining"]:
        trade.update(state="CLOSED", closed=stamp(now), reason=trade["exit_reason"])
    elif not trade["exit_qty"]:
        trade["reason"] = "TP1 filled · tracking remaining contract"
        if (trade.get("track") == POLICY and trade["exit_reason"] == "TP1"
                and trade["plan"]["adaptive_exit"]["profile"] in ("Ordinary trend", "Range rotation")):
            # Use the actual underlying fill reference and only later observations.
            trade["runner_stop_from"] = stamp(now + timedelta(microseconds=1))
            trade["reason"] = "TP1 filled · runner protected at underlying entry"


def realized(trade):
    return round(sum(fill["pnl"] for fill in trade["fills"]), 2)


def unrealized(trade, now):
    at = time_or_none(trade.get("mark_at"))
    if trade["state"] == "OPEN" and at and 0 <= (now - at).total_seconds() <= QUOTE_AGE:
        return option_pnl(trade, trade["mark_bid"], trade["remaining"])
    return None


class SessionRisk:
    """Realized P/L today, current bid marks, and uncommitted modeled stop risk for each paper book."""

    def __init__(self):
        self.pnl = dict.fromkeys(LOSS_KEYS, 0.)
        self.reserved = dict.fromkeys(LOSS_KEYS, 0.)
        self.unmarked = set()

    def include(self, row, now):
        track = row.get("track", BASELINE)
        day = now.astimezone(ET).date()
        self.pnl[track] += sum(fill["pnl"] for fill in row["fills"]
                               if time_or_none(fill["at"]).astimezone(ET).date() == day)
        if row["state"] == "OPEN":
            mark = unrealized(row, now)
            if mark is None:
                self.unmarked.add(track)
            else:
                self.pnl[track] += mark
            self.reserved[track] += open_risk(row, PREMIUM_STOP, FEE)

    def remaining(self, track, halted):
        books = (BASELINE, EARLY) if track == EARLY else (track,)
        combined = sum(self.pnl[t] for t in books)
        if any(halted[t] for t in books) or combined <= DAILY_STOP or self.pnl[track] <= DAILY_STOP:
            return 0., "Daily paper loss stop"
        if self.unmarked.intersection(books):
            return None, "Waiting for fresh executable bid marks on open paper positions"
        alone = abs(DAILY_STOP) + min(0., self.pnl[track]) - self.reserved[track]
        pooled = abs(DAILY_STOP) + min(0., combined) - sum(self.reserved[t] for t in books)
        amount = max(0., min(alone, pooled))
        return amount, "" if amount else "Open paper risk consumes the remaining daily loss capacity"


def ledger_risk(book, db, now):
    midnight = now.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0)
    totals = SessionRisk()
    for trials in (False, True):
        for row in book.rows(db, "WHERE state='OPEN' OR updated>=?", stamp(midnight), trials=trials):
            totals.include(row, now)
    return totals


def ranking_capacity(journal, now, track):
    if not journal:
        return None, "Paper risk ledger unavailable"
    book = PaperLedger(journal)
    try:
        with journal.connection() as db:
            day = str(now.astimezone(ET).date())
            halted = {name: book.meta(db, key) == day for name, key in LOSS_KEYS.items()}
            # A recorded daily halt survives disconnections and worker restarts.
            if halted[track] or (track == EARLY and halted[BASELINE]):
                return 0., "Daily paper loss stop"
            heartbeat = time_or_none(book.meta(db, "heartbeat"))
            if not heartbeat or not 0 <= (now - heartbeat).total_seconds() <= HEARTBEAT_MAX_AGE:
                return None, "Waiting for a current paper worker heartbeat"
            return ledger_risk(book, db, now).remaining(track, halted)
    except (OSError, sqlite3.Error, psycopg.Error, ValueError, TypeError, KeyError):
        return None, "Paper risk ledger unreadable"


class PaperWorker:
    def __init__(self, ledger, webull, tradytics, get_now=clock, *, schwab=None):
        self.ledger, self.webull, self.tradytics, self.get_now = ledger, webull, tradytics, get_now
        self.schwab = schwab

    def poll(self, assert_lock=lambda: None):
        book, now = self.ledger, self.get_now()
        day = now.astimezone(ET).date()
        with book.journal.connection() as db:
            book.ingest(db)
            active = book.rows(db, "WHERE state IN ('OPEN','PENDING')")
            active.extend(book.rows(db, "WHERE state='OPEN'", trials=True))
            halted = {track: book.meta(db, key) == str(day) for track, key in LOSS_KEYS.items()}
        open_rows = [trade for trade in active if trade["state"] == "OPEN"]
        symbols = [t["symbol"] for t in active if t["plan"].get("source") == "Webull"]
        snapshots = self.webull.prices(symbols) if symbols else {}
        try:
            quotes = (snapshot_quotes(self.webull, [row["contract"] for row in open_rows], self.get_now)
                      if open_rows and session_state(now)["status"] == "OPEN" else {})
        except DataError:
            quotes = {}

        def desk(trade):
            symbol = trade["symbol"]
            return Desk(symbol, self.get_now(), self.tradytics, False, False, None, True,
                        trade["plan"].get("source") or "Tradytics",
                        journal=book.journal, webull_store=self.webull, webull_prices=snapshots.get(symbol), schwab_store=self.schwab)

        for trade in open_rows:
            try:
                track = trade.get("track", BASELINE)
                stop = halted[track] or (track in (EARLY, POLICY) and halted[BASELINE])
                update_trade(trade, desk(trade), quotes.get(trade["contract"]), stop)
            except DataError:
                trade["reason"] = "Waiting for verified market data"
        assert_lock()
        with book.journal.connection() as db:
            for trade in open_rows:
                book.save(db, trade, self.get_now())
            risk = ledger_risk(book, db, self.get_now())
            pnl = risk.pnl
            if pnl[BASELINE] + pnl[EARLY] <= DAILY_STOP:
                halted[EARLY] = True
                book.set_meta(db, LOSS_KEYS[EARLY], str(day))
            for track, value in pnl.items():
                if value <= DAILY_STOP:
                    halted[track] = True
                    book.set_meta(db, LOSS_KEYS[track], str(day))

        shadow_open = {t["symbol"] for t in open_rows if t["state"] == "OPEN" and t.get("track") == SHADOW}
        early_open = {t["symbol"] for t in open_rows if t["state"] == "OPEN" and t.get("track") == EARLY}
        for trade in active:
            if trade["state"] != "PENDING":
                continue
            track = trade.get("track", BASELINE)
            try:
                if track == SHADOW and trade["symbol"] in shadow_open:
                    skip(trade, "Another Gemini paper position is open for this ticker")
                elif track == EARLY and trade["symbol"] in early_open:
                    skip(trade, "Another Early paper position is open for this ticker")
                else:
                    stop = halted[track] or (track == EARLY and halted[BASELINE])
                    available, issue = risk.remaining(track, halted)
                    if available is None and trade["plan"].get("rank_contracts"):
                        trade["reason"] = issue
                        if now - time_or_none(trade["origin"]) >= timedelta(seconds=ENTRY_WINDOW):
                            skip(trade, "Fill window elapsed · " + issue)
                    else:
                        attempt_entry(trade, desk(trade), self.webull, stop, self.get_now, remaining_risk=available or 0.)
                    if trade["state"] == "OPEN":
                        if track == EARLY:
                            early_open.add(trade["symbol"])
                        excursion = (trade["mark_bid"] - trade["entry_ask"]) * 100
                        trade.update(mae_per_contract=min(0., excursion), mfe_per_contract=max(0., excursion))
            except DataError:
                trade["reason"] = "Webull or market feed temporarily unavailable"
            assert_lock()
            with book.journal.connection() as db:
                scenario = trade["plan"].get("gemini") or {}
                if track == SHADOW and scenario and book.advisor:
                    book.advisor.lock_plan(db,trade["symbol"],trade["session"])
                    active_plan = book.advisor.session_plan(db,trade["symbol"],trade["session"])
                    if not active_plan or active_plan["state"] != "ready" or active_plan["id"] != scenario["plan_id"]:
                        trade = json.loads(book.execute(db,"SELECT body FROM qd_paper_trade WHERE id=?",trade["id"]).fetchone()[0])
                        skip(trade,"Scenario superseded before the paper fill")
                if track == SHADOW and trade["state"] == "OPEN":
                    shadow_open.add(trade["symbol"])
                risk.include(trade, self.get_now())
                halted[track] = halted[track] or pnl[track] <= DAILY_STOP
                if pnl[BASELINE] + pnl[EARLY] <= DAILY_STOP:
                    halted[EARLY] = True
                    book.set_meta(db, LOSS_KEYS[EARLY], str(day))
                book.save(db, trade, self.get_now())
                trial = begin_exit_trial(book, db, trade, halted[POLICY] or halted[BASELINE], self.get_now())
                if trial and trial["state"] == "OPEN":
                    risk.include(trial, self.get_now())
                    if pnl[POLICY] <= DAILY_STOP:
                        halted[POLICY] = True
                        book.set_meta(db, LOSS_KEYS[POLICY], str(day))
                if halted[track]:
                    book.set_meta(db, LOSS_KEYS[track], str(day))
        with book.journal.connection() as db:
            book.set_meta(db, "heartbeat", stamp(self.get_now()))
