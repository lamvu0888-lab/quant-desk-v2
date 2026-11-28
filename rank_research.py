"""Forward-only contract markouts from the worker's original opportunity set."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
import json
from statistics import fmean, median

from contract_recommendation import DEFAULT_MODE, QUOTE_SECONDS, VERSION, configured_mode
from contract_sources import reported_realtime
from market_session import ET
from option_quotes import option_rows, quote_status
from rank_profiles import TILTS, pick as _pick, profile_inputs
from terminal_data import numeric, positive, time_or_none, valid_date
from v3_movement import VERSION as MOVEMENT_VERSION, active_mode, enabled
from v3_provenance import display_only, display_plan, simulated_feed

KIND = "contract_rank_origin"
DEADLINE = "contract_rank_due"
MODEL = "contract_rank_validation"
WINDOW = timedelta(seconds=60)
SOURCES = {"Webull", "Schwab"}
UNDERLYING_SOURCES = SOURCES | {"Tradytics"}


def _save(journal, ctx, plan, rec):
    at, arrival = time_or_none(rec.get("origin")), time_or_none(rec.get("arrival"))
    if (display_only(rec) or rec.get("version") != VERSION or rec.get("simulated") is not False
            or rec.get("symbol") != ctx.ticker or not at or not arrival
            or at.astimezone(ET).date() != ctx.session or arrival.astimezone(ET).date() != ctx.session
            or not at <= ctx.now < arrival or ctx.now-at > timedelta(seconds=90)):
        return
    if rec.get("status") not in ("qualified", "developing") and not rec.get("candidates"):
        return
    # One set per signal minute, status, direction and selected series. Freeze the first observation.
    selected = (rec.get("selected") or {}).get("contract") or "none"
    movement = plan.get("movement_v3") or {}
    config = f"{rec.get('mode')}:{movement.get('version')}:{movement.get('mode')}"
    ident = f"rank:{VERSION}:{config}:{ctx.ticker}:{ctx.scope}:{at:%Y%m%d%H%M}:{rec['status']}:{rec.get('direction')}:{selected}"
    record = dict(rec, spot_source=ctx.price_provider, regime=plan.get("regime") or "Unknown", setup=plan.get("setup") or "Unknown",
                  scope=ctx.scope, movement_version=movement.get("version"), movement_mode=movement.get("mode"))
    record.pop("alternatives", None)
    if isinstance(record.get("selected"), dict):
        record["selected"] = selected
    scope = str(ctx.session)
    with journal.connection() as db:
        if journal._insert(db, ident, ctx.ticker, KIND, scope, at, record):
            journal._insert(db, ident+":due", ctx.ticker, DEADLINE, scope, arrival, {"origin_id": ident})


def _real_quote(ctx, source, expiry, side):
    if source == "Schwab":
        if not ctx.schwab_store:
            return {}
        feed = ctx.schwab_store.get("option_chain", symbol=ctx.ticker, expiry=expiry, right=side, paused=ctx.paused)
    else:
        feed, label = ctx.option_chain(expiry, provider=source, right=side, near=False)
        if source != label:
            return {}
    snap = getattr(feed, "snapshot", None)
    if not snap or simulated_feed(feed) or snap.meta.get("source") not in (None, source):
        return {}
    received = time_or_none(snap.received_at)
    if not received or received > ctx.now:
        return {}
    rows = option_rows(feed, ctx.ticker, expiry)
    identities = Counter(str(r.get("contract") or "").replace(" ", "").upper() for r in rows)
    quotes = {}
    for row in rows:
        observed = time_or_none(row.get("updated_at"))
        if (row.get("type") != side or display_only(row) or row.get("simulated")
                or row.get("source") not in (None, source) or not observed or observed > received
                or not reported_realtime(row, feed)
                or quote_status(row, feed, ctx.now, max_age=min(ctx.option_max_age, QUOTE_SECONDS),
                                paused=ctx.paused) != "Fresh quote"):
            continue
        code = str(row.get("contract") or "").replace(" ", "").upper()
        if code and identities[code] == 1:
            quotes[code] = dict(row, received_at=received.isoformat())
    return quotes


def _collect_due(journal, ctx):
    """Query only small due IDs on routine polls. Share one provider chain across overlapping deadlines."""
    now = ctx.now
    stamp = lambda dt: dt.astimezone(timezone.utc).isoformat(timespec="microseconds")
    with journal.connection() as db:
        due = db.execute(journal.sql("SELECT id,origin,payload FROM qd25_observation WHERE symbol=? AND kind=? "
                                     "AND scope=? AND origin BETWEEN ? AND ? AND outcome IS NULL ORDER BY origin"),
                         (ctx.ticker, DEADLINE, str(ctx.session), stamp(now-WINDOW), stamp(now))).fetchall()
    cache = {}
    for due_id, deadline, info in due:
        ident = json.loads(info)["origin_id"]
        arrival = time_or_none(deadline)
        with journal.connection() as db:
            row = db.execute(journal.sql("SELECT payload FROM qd25_observation WHERE id=?"), (ident,)).fetchone()
        if not row: continue
        rec = json.loads(row[0])
        if (rec.get("version") != VERSION or rec.get("symbol") != ctx.ticker or display_only(rec)
                or rec.get("simulated") is not False or time_or_none(rec.get("arrival")) != arrival):
            continue
        candidates = [c for c in rec["candidates"] if c.get("eligible")]
        if (any(c.get("source") not in SOURCES or not c.get("contract") for c in candidates)
                or len({c["contract"] for c in candidates}) != len(candidates)):
            continue
        marks = {}
        for c in candidates:
            key = c["source"], c["expiry"], c["type"]
            if key not in cache:
                cache[key] = _real_quote(ctx, *key)
            quote = cache[key].get(c["contract"]) or {}
            at = time_or_none(quote.get("updated_at"))
            bid_size, qty = numeric(quote.get("bid_size")), positive(c.get("quantity"))
            if (at and arrival <= at <= now and at <= arrival+WINDOW and positive(quote.get("bid"))
                    and bid_size is not None and qty and bid_size >= qty):
                marks[c["contract"]] = {"bid": quote["bid"], "ask": quote["ask"], "at": at.isoformat(),
                                         "source": c["source"], "delay_minutes": 0, "bid_size": bid_size,
                                         "received_at": quote["received_at"]}
        times = [time_or_none(mark["at"]) for mark in marks.values()]
        if len(marks) != len(candidates) or times and max(times)-min(times) > timedelta(seconds=15):
            continue
        # Retry partial sets until the deadline. Only freeze marks seen together in this poll.
        # No later high, last trade, or stale midpoint can stand in for an executable bid.
        with journal.connection() as db:
            underlying = getattr(ctx, "chart_quote", None) or {}
            obs = time_or_none(underlying.get("at"))
            spot = None
            if (not display_only(underlying) and not underlying.get("simulated")
                    and underlying.get("source")==rec.get("spot_source") and obs and arrival <= obs <= now
                    and now-obs <= timedelta(seconds=30) and positive(underlying.get("price"))):
                spot = {"spot": underlying["price"], "at": obs.isoformat(), "source": underlying["source"]}
            outcome = json.dumps({"observed_at": stamp(now), "marks": marks, "underlying": spot}, allow_nan=False)
            db.execute(journal.sql("UPDATE qd25_observation SET outcome=? WHERE id=? AND outcome IS NULL"),
                       (outcome,ident))
            db.execute(journal.sql("UPDATE qd25_observation SET outcome=? WHERE id=? AND outcome IS NULL"),
                       ('{"processed":true}',due_id))


def record_rank_scan(journal, ctx, plan):
    if (not enabled("RECORD") or not journal or ctx.preview or ctx.paused or display_plan(plan)
            or getattr(ctx, "price_provider", None) not in UNDERLYING_SOURCES):
        return
    if rec := plan.get("contract_recommendation"):
        _save(journal, ctx, plan, rec)
    _collect_due(journal, ctx)


def band(dte):
    return "0" if dte == 0 else "1–3" if 0 < dte <= 3 else "4–7" if 3 < dte <= 7 else "8+"


def _context(origin, rec):
    hour = origin.astimezone(ET).hour
    period = "open" if hour < 11 else "midday" if hour < 14 else "late"
    return [period, rec.get("regime") or "Unknown", rec.get("setup") or "Unknown"]


def _conditional_error(c, mark, underlying, rec):
    from v3_options import contract_spec, value, YEAR
    if not underlying or underlying["source"]!=rec.get("spot_source"):
        return None
    at = time_or_none(mark.get("at"))
    spot_at = time_or_none(underlying.get("at"))
    iv = positive(c.get("iv"))
    rate, dividend = numeric(c.get("rate")), numeric(c.get("dividend"))
    bid, ask = positive(mark.get("bid")), positive(mark.get("ask"))
    spot = positive(underlying.get("spot"))
    if (not at or not spot_at or abs((at-spot_at).total_seconds())>30 or not iv or rate is None
            or dividend is None or not spot or not bid or not ask or ask<bid):
        return None
    try:
        spec = contract_spec(c, rec["symbol"])
        years = (spec.expires-at).total_seconds()/YEAR
        if years<=0 or at>=spec.last_trade:
            return None
        model = float(value(spec, spot, years, iv, rate, dividend))
    except (TypeError, ValueError):
        return None
    return abs(model-(bid+ask)/2)


def comparable(record):
    rec = record["payload"]
    if (display_only(rec) or rec.get("simulated") is not False or rec.get("version") != VERSION
            or rec.get("status") != "qualified" or not rec.get("selected")):
        return None
    at = time_or_none(record["origin"])
    arrival = time_or_none(rec.get("arrival"))
    outcome = record.get("outcome") or {}
    captured = time_or_none(outcome.get("observed_at"))
    if (not at or time_or_none(rec.get("origin")) != at or str(at.astimezone(ET).date()) != record.get("scope")
            or display_only(outcome) or outcome.get("simulated") or not arrival or not captured
            or not at < arrival <= captured <= arrival+WINDOW):
        return None
    candidates = [c for c in rec.get("candidates", []) if c.get("eligible")]
    rows, stamps = [], []
    for c in candidates:
        code = c["contract"]
        mark = outcome.get("marks", {}).get(code) or {}
        observed = time_or_none(mark.get("at"))
        bought = time_or_none(c.get("quote_at"))
        bid = positive(mark.get("bid"))
        ask = positive(c.get("entry"))
        qty = numeric(c.get("quantity"))
        entry_receipt = time_or_none(c.get("received_at"))
        receipt = time_or_none(mark.get("received_at"))
        exit_ask = positive(mark.get("ask"))
        entry_bid = positive(c.get("bid"))
        ask_size, bid_size = numeric(c.get("ask_size")), numeric(mark.get("bid_size"))
        if (display_only(c) or c.get("simulated") or display_only(mark) or mark.get("simulated")
                or numeric(mark.get("delay_minutes")) != 0 or numeric(c.get("delay_minutes")) != 0
                or not entry_receipt or not bought or not bought <= entry_receipt <= at or not receipt or not observed
                or not at-timedelta(seconds=QUOTE_SECONDS)<=bought<=at or not arrival<=observed<=receipt<=captured
                or not bid or not exit_ask or exit_ask < bid or not ask or positive(c.get("ask")) != ask
                or not entry_bid or entry_bid>ask or not qty or qty!=int(qty) or not 1<=qty<=2
                or ask_size is None or bid_size is None or min(ask_size,bid_size)<qty or
                not mark.get("source") in SOURCES or c.get("source") != mark["source"]):
            return None
        # Research uses an additional one-cent exit stress beyond crossing the observed spread.
        from paper_engine import FEE
        fee = numeric(c.get("fee_per_side", (rec.get("diagnostics") or {}).get("fees_per_side")))
        fee = FEE if fee is None else fee
        if fee < 0:
            return None
        entry = ask
        sell = max(0, bid - .01)
        net = 100 * (sell-entry) - 2*fee
        rows.append(dict(code=code, dte=c["dte"], per_contract=net, position_net=qty*net,
                         ret=net/(100*entry+fee), features=c.get("features") or {}, score=c.get("score"),
                         profile_inputs=profile_inputs(c, rec),
                         price_error=_conditional_error(c,mark,outcome.get("underlying"),rec)))
        stamps.append(observed)
    if (not rows or rec["selected"] not in {c["code"] for c in rows}
            or len({row["code"] for row in rows}) != len(rows) or max(stamps)-min(stamps) > timedelta(seconds=15)):
        return None
    return {"origin": at.isoformat(), "end": captured.isoformat(), "session": record["scope"],
            "version": VERSION, "symbol": rec.get("symbol"), "mode": rec.get("mode"),
            "movement_version": rec.get("movement_version"), "movement_mode": rec.get("movement_mode"),
            "context": _context(at,rec), "selected": rec["selected"], "rows": rows}


def daily_results(records):
    reasons, dtes = Counter(), defaultdict(list)
    full = []
    rejection = Counter()
    misses = defaultdict(list)
    for record in sorted(records,key=lambda row:row["origin"]):
        rec = record["payload"]
        if rec.get("simulated") is not False or rec.get("version") != VERSION or display_only(rec):
            continue
        reasons[rec.get("status") or "unknown"] += 1
        rejection.update(c.get("reason") or "Unspecified" for c in rec.get("candidates",[]) if not c.get("eligible"))
        if trial := comparable(record):
            full.append(trial)
            for row in trial["rows"]:
                dtes[str(row["dte"])].append(row["per_contract"])
                if row["price_error"] is not None:
                    misses[str(row["dte"])].append(row["price_error"])
    paired = []
    for trial in full:
        winner = next(r for r in trial["rows"] if r["code"]==trial["selected"])
        others = [r["per_contract"] for r in trial["rows"] if r["code"]!=trial["selected"]]
        if others:
            paired.append(winner["per_contract"] - median(others))
    return {"status_counts": dict(reasons), "complete": len(full), "qualified": reasons["qualified"],
            "incomplete": reasons["qualified"]-len(full), "paired_origins": len(paired),
            "median_selected_minus_median_alternative_usd": median(paired) if paired else None,
            "rejection_counts": dict(rejection.most_common(6)),
            "conditional_midpoint_error": {k:{"n":len(v),"mae":round(fmean(v),4)} for k,v in misses.items()},
            "dte": {k:{"observed_contracts":len(v),"mean_net_per_contract":round(fmean(v),2)}
                    for k,v in sorted(dtes.items())},
            "basis":"Recorded ask to matched bid less exit cushion and fees at planned arrival. Research markout."}, full


def _independent(trials):
    """Nonoverlapping windows within a session. Adjacent scans share much of the future tape."""
    output = []
    for _, group in _by(trials, lambda r:r["session"]).items():
        until = None
        valid = [(time_or_none(row.get("origin")), time_or_none(row.get("end")), row) for row in group]
        for start, end, trial in sorted((item for item in valid if item[0] and item[1]), key=lambda item:item[0]):
            if start < end and (until is None or start >= until):
                output.append(trial)
                until = end
    return output


def _by(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return groups


def _paired(trials, profile):
    daily = defaultdict(list)
    for trial in trials:
        picked = _pick(trial, profile)
        if picked:
            champion = next(c for c in trial["rows"] if c["code"] == trial["selected"])
            daily[trial["session"]].append(picked["position_net"] - champion["position_net"])
    return [fmean(v) for v in daily.values()]


def _probabilities(train, test):
    """Evaluate each predeclared bucket's fixed probability versus the pooled training base rate."""
    def samples(trials):
        for t in trials:
            period, regime, setup = t["context"]
            # One representative per decision and expiry group. No ex-post best strike.
            for bucket, group in _by(t["rows"], lambda r:band(r["dte"])).items():
                row = next((r for r in group if r["code"] == t["selected"]), min(group,key=lambda r:r["code"]))
                yield (bucket,regime,setup), int(row["per_contract"] > 0), t["session"]
    fitted = list(samples(train))
    observed = list(samples(test))
    if not fitted: return []
    pooled = (1+sum(y for _,y,_ in fitted))/(2+len(fitted))
    fit_groups = _by(fitted,lambda s:s[0])
    eval_groups = _by(observed,lambda s:s[0])
    output = []
    for key, group in fit_groups.items():
        rows = eval_groups.get(key,[])
        if (len(group)<40 or len(rows)<25 or len({s[2] for s in group})<12
                or len({s[2] for s in rows})<5 or len({s[1] for s in rows})<2):
            continue
        p = (1+sum(y for _,y,_ in group))/(2+len(group))
        losses = [(y-p)**2 for _,y,_ in rows]
        base_losses = [(y-pooled)**2 for _,y,_ in rows]
        # Day-balanced holdout; overlapping ticker scans cannot confer extra session votes.
        gain = [(s[2],b-a) for s,a,b in zip(rows,losses,base_losses)]
        skill = fmean(fmean(v for _,v in session) for session in _by(gain,lambda s:s[0]).values())
        if skill > 0:
            output.append(dict(dte_band=key[0],regime=key[1],setup=key[2],probability=p,
                               holdout_brier=fmean(losses),baseline_brier=fmean(base_losses),
                               holdout_n=len(rows),train_n=len(group),holdout_day_balanced_skill=skill))
    return output


def _fit(trials):
    days = sorted({t["session"] for t in trials})
    out = {"sessions":len(days),"independent_origins":len(trials),"status":"Collecting matched forward quotes",
           "champion":"Recorded selection","promotion":False}
    if len(days)<20 or len(trials)<100: return out
    cut = days[-max(5,len(days)//4)]
    train = [t for t in trials if t["session"]<cut]
    test = [t for t in trials if t["session"]>=cut]
    # Compare profiles on identical opportunities, including any explicit abstentions.
    usable = {name: [t for t in train if _pick(t, name) is not None] for name in TILTS}
    train_days = {t["session"] for t in train}
    eligible = [name for name, rows in usable.items()
                if len(rows) >= .75*len(train) and {t["session"] for t in rows} == train_days]
    if not eligible:
        return out
    common = [t for t in train if all(_pick(t, name) is not None for name in eligible)]
    if len(common) < .75*len(train) or {t["session"] for t in common} != train_days:
        return out
    profiles = {name:_paired(common, name) for name in eligible}
    chosen = max(profiles,key=lambda name:fmean(profiles[name]))
    delta = _paired(test,chosen)
    out.update(status="Shadow comparison",selected_on_training=chosen,
               training_matched=len(common), training_available=len(train), holdout_available=len(test),
               holdout_matched=sum(_pick(t, chosen) is not None for t in test),
               training_sessions=sum(d<cut for d in days),holdout_start=cut,holdout_sessions=len(delta),
               mean_paired_session_gain_usd=fmean(delta) if delta else None,
               positive_holdout_days=sum(x>0 for x in delta),
               probabilities=_probabilities(train,test))
    return out


def _cohort():
    # Probability mode consumes an independently measured default reference cohort.
    # Requiring probability-selected origins to validate itself creates a cold-start loop.
    mode = configured_mode()
    return dict(mode=DEFAULT_MODE if mode == "probability" else mode,
                movement_version=MOVEMENT_VERSION, movement_mode=active_mode())


def _training_trials(journal, symbol, scope, cutoff):
    cutoff = time_or_none(cutoff)
    if not cutoff:
        return []
    cohort = _cohort()
    trials = []
    for record in journal.records(symbol, "contract_rank_training_day", limit=80):
        payload = record["payload"]
        day = valid_date(record["scope"])
        published = time_or_none(record["origin"])
        if (not day or record["scope"] > scope or not published or published > cutoff or display_only(payload)
                or payload.get("simulated") or payload.get("version") != VERSION or payload.get("symbol") != symbol):
            continue
        for t in payload.get("trials", []):
            start, end = time_or_none(t.get("origin")), time_or_none(t.get("end"))
            if (t.get("version") == VERSION and t.get("symbol") == symbol and t.get("session") == record["scope"]
                    and all(t.get(k)==v for k,v in cohort.items()) and not display_only(t) and not t.get("simulated")
                    and start and end and start.astimezone(ET).date() == end.astimezone(ET).date() == day
                    and start < end <= published and t.get("rows")):
                trials.append(t)
    return trials


def train_ranker(journal, symbol, scope, now):
    trials = _training_trials(journal, symbol, scope, now)
    independent = _independent(trials)
    report = _fit(independent)
    report.update(version=VERSION, symbol=symbol, trained_through=scope, published=now.isoformat(), cohort=_cohort())
    contexts = _by(independent,lambda t:tuple(t["context"]))
    report["conditional_profiles"] = [{"time":k[0],"regime":k[1],"setup":k[2],**_fit(v)}
                                      for k,v in contexts.items() if len({t["session"] for t in v})>=20]
    journal.insert(f"rank:validation:{VERSION}:{symbol}:{scope}",symbol,MODEL,scope,now,report)
    return {k:report[k] for k in ("status","sessions","independent_origins","promotion",
            "selected_on_training","holdout_sessions","mean_paired_session_gain_usd") if k in report}


def probability(journal,symbol,origin,version,*,dte,regime,setup):
    if not journal or version!=VERSION: return None
    now = time_or_none(origin)
    if not now: return None
    for saved in journal.records(symbol,MODEL,limit=4):
        model = saved["payload"]
        stamp = time_or_none(saved["origin"])
        through = valid_date(model.get("trained_through"))
        published = time_or_none(model.get("published"))
        if (display_only(model) or model.get("simulated") or model.get("cohort") != _cohort()
                or not through or saved["scope"] != str(through) or not published or published != stamp
                or model.get("version")!=version or model.get("symbol")!=symbol or not stamp or
                not timedelta(0)<=now-stamp<=timedelta(days=7) or
                through > stamp.astimezone(ET).date() or through >= now.astimezone(ET).date()):
            continue
        for row in model.get("probabilities",[]):
            if (row["dte_band"],row["regime"],row["setup"])==(band(dte),regime,setup):
                p = numeric(row.get("probability"))
                return p if p is not None and 0 <= p <= 1 else None
        return None
    return None


def replay_rank_proposal(journal,symbol,scope,proposal):
    """Use the incumbent that was actually selected and identical recorded alternatives."""
    key = proposal.get("proposed", proposal.get("value"))
    if type(key) not in (float,int) or key not in range(len(TILTS)):
        return {"status":"Unknown contract ranking profile","promotion":False}
    name = list(TILTS)[int(key)]
    # A proposal replay sees only training-day records published by that session's end.
    day = valid_date(scope)
    if not day or proposal.get("version",VERSION) != VERSION:
        return {"status":"Incompatible contract research session or version","promotion":False}
    end = datetime.combine(day+timedelta(days=1), time.min, tzinfo=ET)-timedelta(microseconds=1)
    trials = _independent([t for t in _training_trials(journal,symbol,scope,end)
                           if proposal.get("regime","ALL") in ("ALL",t["context"][1])])
    gains = _paired(trials,name)
    return {"status":"Historical research only" if gains else "Waiting for matched marks",
            "profile":name,"matched_days":len(gains), "available_origins":len(trials),
            "matched_origins":sum(_pick(t,name) is not None for t in trials),
            "mean_paired_session_gain_usd":fmean(gains) if gains else None,
            "forward_validation":"Requires subsequent independent sessions and shadow review","promotion":False}


def forward_profile_review(journal, symbol, scope, trials):
    """Score a previously frozen proposal on observations from a later session only."""
    for event in journal.records("DESK", "v3_feedback", limit=30):
        proposed_at = time_or_none(event["origin"])
        proposal = event["payload"].get("proposal") or {}
        if (not proposed_at or event["scope"]>=scope or proposal.get("parameter")!="contract_rank_profile"
                or proposal.get("ticker") not in (symbol, "ALL")):
            continue
        key = proposal.get("proposed")
        if type(key) not in (int,float) or key not in range(len(TILTS)):
            continue
        name = list(TILTS)[int(key)]
        cases = [t for t in trials if t.get("symbol")==symbol and t.get("version")==VERSION
                 and all(t.get(k)==v for k,v in _cohort().items()) and time_or_none(t["origin"]) > proposed_at
                 and proposal.get("regime","ALL") in ("ALL", t["context"][1])]
        changes = _paired(_independent(cases),name)
        return {"proposed_on":event["scope"],"profile":name,"matched_session":bool(changes),
                "mean_selected_advantage_usd":fmean(changes) if changes else None,"promotion":False}
    return {"status":"Waiting for an earlier contract-ranking proposal","promotion":False}


def end_of_day(journal,symbol,scope,now):
    rows = journal.records(symbol,KIND,scope=scope,limit=5000)
    stats,trials = daily_results(rows)
    # Store only inputs needed for matched outcome validation. Do not send raw contracts to an AI service.
    if trials:
        # Retain independent windows separately within each observed policy.
        groups = _by(trials, lambda t:(t["mode"], t["movement_version"], t["movement_mode"]))
        compact = [t for group in groups.values() for t in _independent(group)]
        journal.insert(f"rank:training:{VERSION}:{symbol}:{scope}",symbol,"contract_rank_training_day",
                       scope,now,{"version":VERSION,"symbol":symbol,"trials":compact})
    if trials:
        stats["prospective_proposal"] = forward_profile_review(journal,symbol,scope,trials)
    if enabled("ML"):
        stats["learning"]=train_ranker(journal,symbol,scope,now)
    return stats
