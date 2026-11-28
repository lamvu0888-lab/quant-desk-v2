"""Match a frozen Gemini scenario to a fresh Quant Desk setup for the paper simulator."""
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from data_utils import utc
from decision_engine import ENTRY_STATES, MIN_REWARD_R
from decision_flow import apply_model_gate, model_summary
from desk_analytics import FIVE_MINUTES, MINUTE, candles_on
from gemini_sessions import session_context, revision_reason, summarize
from gemini_advisor import FLOW_CHOICES, AdvisorQueue, evidence, packed
from v3_provenance import display_plan
from market_session import ET, calendar, rth_bounds, session_state
from movement_detector import ENTRY_COOLDOWN, MIN_ENTRY_SCORE
from research_journal import plan_scope
from terminal_data import positive, time_or_none
from trade_validation import paper_records, performance


def market_snapshot(plan, read):
    at = utc(plan["detected"])
    levels = {v.label: v.price for v in read["levels"] if positive(v.price)
              and (v.as_of is None or (time_or_none(v.as_of) and utc(v.as_of) <= at))}
    anchors = {"Session VWAP": plan.get("vwap"), **(plan.get("gamma_levels") or {})}
    box = plan.get("range_reversal") or {}
    anchors.update({"Range midpoint": box.get("midpoint")})
    if levels.get("Range low") and levels.get("Range high"):
        anchors["Range midpoint"] = (levels["Range low"] + levels["Range high"]) / 2
    for tf, ema in (plan.get("ema") or {}).items():
        if tf in ("3m", "5m"):
            for name in ("ema9", "ema21"):
                anchors[f"{tf} {name.upper()}"] = ema.get(name)
    bounds = rth_bounds(plan["session"])
    bars = candles_on(read["buckets"], at.astimezone(ET).date())
    if bars and bounds and utc(bars[0]["time_utc"]) == bounds[0] and bounds[0] + FIVE_MINUTES <= at:
        high, low = bars[0]["high"], bars[0]["low"]
        anchors.update({"Opening 5m high": high, "Opening 5m low": low,
                        "Opening range projection up (1x)": high + (high - low),
                        "Opening range projection down (1x)": low - (high - low)})
    levels.update({key: val for key, val in anchors.items() if positive(val)})
    return {"symbol": plan["symbol"], "session": plan["session"], "as_of": at.isoformat(),
            "source": plan["source"], "spot": plan["price"], "levels": {k: round(v, 2) for k, v in levels.items()},
            "atr_5m": read["regime"].get("atr"), "evidence": evidence(plan)}


def combined_geometry(plan, leg, side):
    """Tighten protection and cap the second target at the scenario boundary."""
    if not all(positive(plan.get(k)) for k in ("entry", "invalidation", "stop", "trigger", "tp1", "tp2")):
        return None
    tighter, nearer = (max, min) if side > 0 else (min, max)
    entry = plan["entry"]
    stop = tighter(plan["stop"], leg["invalidation"])
    trigger = tighter(plan["trigger"], leg["trigger"])
    tp1, tp2 = plan["tp1"], nearer(plan["tp2"], leg["target"])
    risk = side * (entry - stop)
    if (risk <= 0 or side * (entry - trigger) <= 0 or side * (tp2 - tp1) <= 0
            or side * (tp1 - entry) < MIN_REWARD_R * risk):
        return None
    return {"stop": stop, "invalidation": tighter(plan["invalidation"], leg["invalidation"]),
            "trigger": trigger, "tp1": tp1, "tp2": tp2, "stop_risk": risk,
            "rr": round(side * (tp1 - entry) / risk, 2), "rr2": round(side * (tp2 - entry) / risk, 2)}


def assess(plan, read, daily, now):
    """Return a prospective paper plan and the exact reasons an entry was withheld."""
    if display_plan(plan) or session_state(now)["status"] != "OPEN":
        return None, ["A live regular-session scan is required."]
    if not daily or daily["state"] != "ready":
        detail = (daily or {}).get("issue") or (daily or {}).get("state") or "waiting for fresh anchor levels"
        return None, [f"Daily A/B/C plan pending · {detail}."]
    data = daily["input"]
    if (data["symbol"] != plan["symbol"] or data["session"] != plan["session"]
            or now.astimezone(ET).date().isoformat() != data["session"]):
        return None, ["No published plan for this ticker and session."]
    reasons = list(plan.get("data_reasons") or ())
    quote_at = time_or_none(plan.get("quote_at"))
    if not quote_at or not timedelta(0) <= now - quote_at <= timedelta(seconds=45):
        reasons.append("Waiting for a verified underlying quote no more than 45 seconds old.")
    at = time_or_none(plan.get("entry_bar"))
    if not at or not daily["at"] < at <= now or now - at > timedelta(seconds=90):
        reasons.append("Waiting for a fresh 1m candle completed after plan publication.")
    if not plan.get("price_context_ready") or plan.get("confidence") != "Synchronized":
        reasons.extend(plan.get("missing", []) if plan.get("confidence") != "Synchronized" else [])
        reasons.append("Current quote and synchronized completed price bars are required.")
    if reasons:
        return None, list(dict.fromkeys(reasons))
    side = plan.get("direction")
    if side not in (1, -1) or not plan.get("confirmed"):
        return None, list(dict.fromkeys(["Waiting for a completed Quant Desk entry setup.", *(plan.get("missing") or ())]))
    box = plan.get("range_reversal")
    name = ("C support" if side > 0 else "C resistance") if box else ("A" if side > 0 else "B")
    leg = daily["legs"][name]
    if leg["setup"] != plan.get("setup"):
        return None, [f"{name} calls for {leg['setup']}. Quant Desk detected {plan.get('setup')}."]
    reasons.extend(plan.get("blocks") or [])
    option = plan.get("option_quote")
    if option is not None and not option.get("ready"):
        reasons.append(f"Waiting for an executable exact-expiry option quote · {option.get('status', 'unavailable')}.")
    if plan.get("premium_review_required"):
        reasons.append("Executable premium requires review.")
    if (plan.get("score") or 0) < MIN_ENTRY_SCORE:
        reasons.append(f"Evidence score {plan.get('score') or 0} is below {MIN_ENTRY_SCORE}.")
    if not 5 <= (plan.get("hold") or 0) <= 15:
        reasons.append("Insufficient session time for a paper entry.")
    if box:
        if not box.get("follow_through") or not box.get("ema_turn_confirmed"):
            reasons.append("Waiting for the range-edge rejection, subsequent 1m close and 1m/3m EMA turn.")
    else:
        move = plan["movement"]
        if any(plan["ema"][tf]["direction"] != side for tf in ("1m", "3m", "5m")):
            reasons.append("The 5m trend and 1m/3m EMA timing must agree.")
        if move["range_hold"] or move["momentum"] != side:
            reasons.append("Waiting for directional momentum outside the sideways range.")
    flow = plan.get("flow_confirmation") or {}
    intervals = flow.get("intervals") or []
    latest = intervals[-1] if intervals else {}
    floor = positive(flow.get("floor"))
    if (plan["feed_timing"]["flow_state"] != "Aligned" or plan["flow"].get("direction") != side
            or not floor or latest.get("close") != plan["bar"] or side * (latest.get("net") or 0) < floor):
        reasons.append("Latest synchronized 5m premium and recent net flow must both favor the setup.")
    elif leg["flow"] == FLOW_CHOICES[1] and not flow["confirmed"]:
        reasons.append("This scenario requires two aligned 5m flow closes.")
    last = next((bar for bar in reversed(read["candles"]) if utc(bar["time_utc"]) + MINUTE == at
                 and bar.get("closed", True) and not bar.get("forming")), None)
    if not last or side * (last["close"] - leg["trigger"]) <= 0:
        reasons.append(f"The completed 1m close must clear the scenario trigger at {leg['trigger']:,.2f}.")
    reference = box.get("edge") if box else plan.get("trigger")
    atr = positive(read["regime"].get("atr"))
    if not positive(reference) or not atr or abs(reference - leg["trigger"]) > atr:
        reasons.append("The detected setup is more than one 5m ATR from the planned level or lacks a reference.")
    geometry = combined_geometry(plan, leg, side)
    if not geometry:
        reasons.append(f"Current price, scenario target and protective stop must leave at least {MIN_REWARD_R}R to TP1 and room for TP2.")
    if reasons:
        return None, list(dict.fromkeys(reasons))
    details = {"plan_id": daily["id"], "revision": data.get("revision",1), "stage": data.get("stage","Daily"), "published_at": daily["at"].isoformat(), "model": daily.get("model"), "leg": name,
               "flow": leg["flow"], "trigger": leg["trigger"], "invalidation": leg["invalidation"],
               "target": leg["target"], "baseline_status": plan["status"]}
    return {**plan, **geometry, "gemini": details, "origin": now.isoformat(),
            "status": "ENTER LONG" if side > 0 else "ENTER SHORT"}, []


class ScenarioMonitor:
    def __init__(self, journal, clock=None, webull=None, schwab=None):
        self.providers = webull, schwab
        self.next_pre = {}
        self.journal, self.jobs = journal, AdvisorQueue(journal)
        self.jobs.start()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gemini-scan")
        self.pending = {}

    def submit(self, plan, read, now):
        if display_plan(plan):
            return
        # Coalesce slow scans per ticker instead of delaying regular alerts or replaying a backlog.
        for symbol, task in list(self.pending.items()):
            if task.done():
                try:
                    task.result()
                except Exception as exc:
                    print(f"Gemini paper scan unavailable for {symbol} ({type(exc).__name__}).", flush=True)
                del self.pending[symbol]
        symbol = plan["symbol"]
        if symbol not in self.pending:
            self.pending[symbol] = self.pool.submit(self.record, plan, read, now)

    def premarket(self, symbols, now):
        day = now.astimezone(ET).date()
        if now < rth_bounds(day)[0] - timedelta(minutes=30):
            return
        for symbol in symbols:
            if now >= self.next_pre.get(symbol, now) and (symbol not in self.pending or self.pending[symbol].done()):
                self.next_pre[symbol] = now + timedelta(minutes=2)
                self.pending[symbol] = self.pool.submit(self.draft, symbol, str(day), now)

    def draft(self, symbol, day, now):
        with self.journal.connection() as db:
            latest = self.jobs.session_plan(db,symbol,day)
        if latest and latest["state"] != "expired":
            return
        context, levels = session_context(symbol,now,*self.providers)
        spot = levels.get("PRE close") or levels.get("RTH close")
        if spot and len(set(levels.values())) >= 3:
            snap = dict(session=day,symbol=symbol,as_of=now.isoformat(),spot=spot,levels=levels,
                        source=context["PRE" if "PRE close" in levels else "RTH"]["source"],
                        sessions=context,stage="Premarket",reason="Scheduled premarket draft")
            self.publish_request(snap,latest,now,rth_bounds(day)[0])

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)

    def previous_checks(self, db, symbol, day):
        prior = str(calendar().previous_session(day).date())
        rows = self.jobs.run(db, """SELECT payload FROM qd25_observation WHERE symbol=? AND kind='gemini_scan'
                             AND scope=? ORDER BY origin DESC LIMIT 400""", symbol, prior).fetchall()
        regular, shadow = Counter(), Counter()
        for (raw,) in rows:
            check = json.loads(raw)
            regular.update(dict.fromkeys(check.get("baseline_reasons") or [], 1))
            shadow.update(dict.fromkeys(check.get("reasons") or [], 1))
        return {"session": prior, "observed_minutes": len(rows),
                "original_checks": dict(regular.most_common(5)), "gemini_checks": dict(shadow.most_common(5))}

    def daily(self, plan, read, now):
        symbol, session = plan["symbol"], plan["session"]
        with self.journal.connection() as db:
            daily = self.jobs.session_plan(db, symbol, session)
        if not plan.get("price_context_ready") or not positive(plan.get("price")):
            return daily
        opened, close = rth_bounds(session)
        opening = summarize(read["buckets"], opened, min(now, opened+3*FIVE_MINUTES), FIVE_MINUTES)
        complete = opening["bars"] == 3
        market = (plan.get("movement") or {}).get("market")
        stage = "Opening range" if complete else "Regular session"
        reason = "First three completed 5m candles" if complete else "First usable regular-session scan"
        if daily and daily["state"] != "expired":
            if complete and daily["input"].get("stage") in ("Premarket","Regular session",None):
                pass
            elif daily["state"] == "ready":
                bar = time_or_none(plan.get("bar"))
                previous = None
                if bar and bar <= now and now-bar < FIVE_MINUTES:
                    key = f"gemini-market:{symbol}:"
                    self.journal.insert(key+bar.isoformat(),symbol,"gemini_market",session,bar,{"market":market})
                    with self.journal.connection() as db:
                        row = self.jobs.run(db,"SELECT payload FROM qd25_observation WHERE id=?",key+(bar-FIVE_MINUTES).isoformat()).fetchone()
                    previous = json.loads(row[0])["market"] if row else None
                reason = revision_reason(daily, read["buckets"], market, now, previous)
                if not reason:
                    return daily
                stage = "Intraday revision"
            else:
                return daily
        snapshot = market_snapshot(plan, read)
        sessions = (daily or {}).get("input",{}).get("sessions",{})
        if any(self.providers) and (not sessions or stage == "Opening range"):
            sessions, anchors = session_context(symbol,now,*self.providers)
            snapshot["levels"].update(anchors)
        snapshot.update(stage=stage, reason=reason, market=market, sessions=sessions)
        if complete:
            snapshot["levels"].update({"Opening 15m high":opening["high"],"Opening 15m low":opening["low"]})
        if len(set(snapshot["levels"].values())) < 3:
            return daily
        return self.publish_request(snapshot,daily,now,close-timedelta(minutes=5))

    def publish_request(self,snapshot,previous,now,cutoff):
        symbol, session = snapshot["symbol"], snapshot["session"]
        deadline = min(now+timedelta(minutes=10),cutoff).timestamp()
        if deadline <= max(now,self.clock()).timestamp():
            return previous
        with self.journal.connection() as db:
            self.jobs.lock_plan(db,symbol,session)
            current = self.jobs.session_plan(db,symbol,session)
            if (current or {}).get("id") != (previous or {}).get("id"):
                return current
            revision = current["input"].get("revision",1)+1 if current else 1
            snapshot.update(revision=revision,replaces=(current or {}).get("id"))
            if current:
                snapshot["prior_scenarios"] = current["legs"]
                self.jobs.run(db,"UPDATE qd_gemini_job SET state='superseded', owner=NULL WHERE id=? AND state IN ('working','queued','retry')",current["id"])
            snapshot["prior_reviews"] = self.jobs.prior_reviews(db,symbol,session,now)
            snapshot["previous_session_checks"] = self.previous_checks(db,symbol,session)
            self.jobs.enqueue(db,f"plan:{session}:{symbol}:v{revision}", "plan",symbol,session,now,snapshot,deadline)
            return self.jobs.session_plan(db,symbol,session)

    def record(self, plan, read, now):
        if display_plan(plan) or session_state(now)["status"] != "OPEN":
            return
        symbol, day = plan["symbol"], plan["session"]
        daily = self.daily(plan, read, now)
        candidate, reasons = assess(plan, read, daily, now)
        ident = f"gemini-entry:{day}:{symbol}:{plan.get('entry_bar')}"
        if candidate:
            rows = self.journal.records(symbol, "candidate", scope=plan_scope(plan), settled=True, limit=1200)
            history = paper_records(rows, before=now, hold=candidate["hold"], setup=candidate["setup"])
            model = model_summary(read["candles"], candidate, now)
            gated = apply_model_gate({**candidate, "missing": []}, model, performance(history))
            if gated["status"] not in ENTRY_STATES:
                reasons = gated["blocks"] or gated["missing"]
                candidate = None
            else:
                candidate = gated
        captured = max(now, self.clock())
        if candidate and (session_state(captured)["status"] != "OPEN"
                          or captured - utc(plan["entry_bar"]) > timedelta(seconds=90)
                          or captured - utc(plan["quote_at"]) > timedelta(seconds=45)):
            candidate, reasons = None, ["Quote or completed trigger aged out during the paper comparison."]
        with self.journal.connection() as db:
            if candidate:
                self.jobs.lock_plan(db,symbol,day)
                current = self.jobs.session_plan(db,symbol,day)
                if not current or current["id"] != daily["id"] or current["state"] != "ready":
                    candidate, reasons = None, ["Scenario superseded during evaluation."]
            if candidate:
                seen = self.jobs.run(db, "SELECT 1 FROM qd25_observation WHERE id=?", ident).fetchone()
                if not seen:
                    if self.journal._claim_entry_window(db, symbol, "Gemini paper comparison", captured, ENTRY_COOLDOWN):
                        self.journal._insert(db, ident, symbol, "gemini_entry", day, captured, {"plan": candidate})
                    else:
                        reasons = ["A Gemini paper entry was recorded for this ticker in the last 10 minutes."]
            state = "WAIT" if reasons else "PAPER CANDIDATE"
            check = {"plan_id": (daily or {}).get("id"), "status": state, "reasons": reasons, "baseline": plan["status"],
                     "baseline_reasons": list(dict.fromkeys(plan["data_reasons"] + plan["blocks"] + plan["missing"])),
                     "setup": plan.get("setup"), "score": plan.get("score"), "source": plan["source"],
                     "entry_bar": plan.get("entry_bar"), "leg": candidate["gemini"]["leg"] if candidate else None}
            key = f"gemini-scan:{symbol}:{now.replace(second=0, microsecond=0).isoformat()}"
            # One row per ticker/minute. Preserve a captured candidate if another scan waits on that minute.
            if not self.journal._insert(db, key, symbol, "gemini_scan", day, now, check):
                old = self.jobs.run(db, "SELECT payload FROM qd25_observation WHERE id=?", key).fetchone()[0]
                if json.loads(old)["status"] != "PAPER CANDIDATE":
                    self.jobs.run(db, "UPDATE qd25_observation SET origin=?,payload=? WHERE id=? AND payload=?",
                                  utc(now).isoformat(timespec="microseconds"), packed(check), key, old)
