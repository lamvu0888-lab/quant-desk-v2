"""Post-close research pipeline driven by the existing monitor worker. Can also run locally."""
from v3_provenance import live_record, display_plan
from datetime import datetime, timezone
import os
from market_session import active_session, calendar, rth_bounds
from v3_challenger import delayed_online, lead_lag, profile, replay_proposal, threshold_search
import json
from v3_feedback import claim_stage, finish_stage, feedback
from v3_movement import enabled, HORIZONS, VERSION, active_mode
from v3_research import SYMBOLS, archive, benchmark, matured, summary, pricing_errors, event_summary, market_events, training_records, same_model


def paper_results(journal,scope):
    from paper_engine import realized
    rows=[]
    with journal.connection() as db:
        for table in ("qd_paper_trade","qd_exit_trial"):
            check = (f"SELECT to_regclass('{table}')" if journal.dsn else
                     "SELECT name FROM sqlite_master WHERE type='table' AND name=?")
            found=db.execute(check,() if journal.dsn else (table,)).fetchone()
            if found and found[0]:
                rows.extend(db.execute(journal.sql(f"SELECT body FROM {table} WHERE session=? ORDER BY updated"),(scope,)).fetchall())
    stats={}
    for (raw,) in rows:
        trade=json.loads(raw)
        symbol=trade.get("symbol")
        if symbol not in SYMBOLS or display_plan(trade) or display_plan(trade.get("plan")):continue
        group=stats.setdefault(symbol,{}).setdefault(trade.get("track","baseline"),{"closed":0,"skipped":0,"net":0.})
        if trade.get("state")=="CLOSED":
            group["closed"]+=1
            group["net"]+=realized(trade)
        elif trade.get("state")=="SKIPPED":group["skipped"]+=1
    return stats


def send_daily(journal, scope, webhook, *, sender=None):
    from discord_monitor import post_message, webhook_url, WebhookError
    latest=journal.records("DESK","v3_report",scope=scope,limit=1)
    if not webhook or not latest:
        return {"status":"Missing report or webhook"}
    endpoint=webhook_url(webhook)
    lines=[f"Quant Desk shadow research · {scope}"]
    for sym in SYMBOLS:
        block=latest[0]["payload"].get(sym,{})
        episodes=block.get("episodes",{})
        paper=block.get("paper",{})
        totals=", ".join(f"{track} {v['closed']} closed, ${v['net']:.2f}" for track,v in paper.items()) or "no closed trades recorded"
        lines.append(f"{sym} · {block.get('recorded',0)} scans · {episodes.get('episodes',0)} price events · "
                     f"{episodes.get('classification',{}).get('missed',0)} missed · "
                     f"{episodes.get('unmatched_warnings',0)} unmatched warnings · {totals}")
    proposal=journal.records("DESK","v3_feedback",scope=scope,limit=1)
    lines.append("Research review · "+(proposal[0]["payload"].get("status","Pending") if proposal else "unavailable"))
    lines.append("Counts cover observed minutes. Changes require independent validation.")
    ident="v3:discord-summary:"+scope
    if not journal.claim_delivery(ident):
        return {"status":"Already claimed"}
    try:
        (sender or post_message)(endpoint,"\n".join(lines)[:1900])
    except WebhookError as exc:
        if exc.retry_after is not None:
            journal.release_throttled_delivery(ident)
            raise
        journal.delivery_state(ident,"unacknowledged")
        return {"status":"Unacknowledged"}
    journal.delivery_state(ident,"sent")
    return {"status":"HTTP acknowledged"}


def daily(journal,now,*,webhook=None):
    if not enabled("RECORD"): return
    day=active_session(now)
    days=calendar().sessions_in_range(calendar().session_offset(day.isoformat(),-2),day.isoformat())
    for session in days:
        d=session.date()
        elapsed=(now-rth_bounds(d)[1]).total_seconds()/60
        if elapsed<15:continue
        scope=str(d)
        mode = active_mode()
        for stage,delay in (("report",15),("training",30),("feedback",45),("replay",60),("discord",65)):
            if elapsed<delay or not enabled("ML") and stage=="training" or not enabled("OPENAI") and stage in ("feedback","replay"):continue
            if stage=="discord" and (not enabled("DISCORD_REPORT") or not webhook):continue
            if stage in ("feedback","discord") and d!=day:continue
            if journal.records("DESK","v3_"+stage,scope=scope,limit=1):continue
            if stage == "report":
                coverage = {(s,kind): [r for r in journal.records(s,kind,scope=scope,limit=6000) if live_record(r)]
                            for s in SYMBOLS for kind in ("v3_scan","v3_health")}
                if not any(coverage.values()):break
            else:
                reports = journal.records("DESK","v3_report",scope=scope,limit=1)
                if not reports or reports[0]["payload"].get("research_config") != {"version":VERSION, "mode":mode}:
                    continue
            lease=claim_stage(journal,d,stage,now)
            if not lease:continue
            if stage=="report":
                value={"research_config":{"version":VERSION, "mode":mode}}
                tracks={s:[r for r in coverage[s,"v3_scan"] if same_model(r,VERSION,mode)] for s in SYMBOLS}
                paper=paper_results(journal,scope)
                for s,rows in tracks.items():
                    labels=matured(rows)
                    health=[r for r in coverage[s,"v3_health"] if same_model(r,VERSION,mode)]
                    events=market_events(rows,health)
                    journal.insert(f"v3:events:{scope}:{s}",s,"v3_events",scope,now,events)
                    counts={k:sum(e["classification"]==k for e in events["events"]) for k in ("early","late","wrong_direction","missed")}
                    value[s]=dict(recorded=len(rows),unavailable=len(health),independent_events=counts,paper=paper.get(s,{}),pricing=pricing_errors(rows),episodes=event_summary(rows),profile=profile(labels),horizons=[summary(labels,h) for h in HORIZONS])
                    from rank_research import end_of_day
                    from gemini_strategy import abc_outcomes
                    value[s]["contract_selection"] = end_of_day(journal,s,scope,now)
                    value[s]["abc_outcomes"] = abc_outcomes(journal,s,scope,rows)
                    if s == "SPX":
                        hourly = next((item for item in value[s]["horizons"] if item["horizon"]==5), {})
                        value[s]["futures_cvd"] = hourly.get("futures_cvd", {"status":"No matched futures observations"})
                    root=os.getenv("QD_V3_ARCHIVE_DIR")
                    if root:archive([*coverage[s,"v3_scan"],*coverage[s,"v3_health"]],root,s,d)
                value["cross_market"]={a+"/"+b:lead_lag(tracks,a,b) for a,b in (("SPY","SPX"),("QQQ","SPY"),("SPX","SPY"))}
            elif stage=="training":
                value={}
                for s in SYMBOLS:
                    labels=matured(training_records(journal,s,scope))
                    value[s]=benchmark(labels)
                    value[s]["threshold_search"]=threshold_search(labels)
                    if enabled("ONLINE"):value[s]["online"]=delayed_online(labels)
            elif stage=="feedback":
                reports=journal.records("DESK","v3_report",scope=scope,limit=1)
                if not reports:continue
                training=journal.records("DESK","v3_training",scope=scope,limit=1)
                aggregate=dict(reports[0]["payload"])
                if training:
                    for sym in SYMBOLS:
                        model=training[0]["payload"].get(sym,{})
                        aggregate[sym]=dict(aggregate.get(sym,{}),model_metrics={
                            k:model[k] for k in ("status","sessions","target","online","threshold_search") if k in model},
                            heldout=model.get("folds",[])[-1:] )
                value=feedback(journal,d,aggregate)
            elif stage=="discord":
                value=send_daily(journal,scope,webhook)
            else:
                previous=journal.records("DESK","v3_feedback",scope=scope,limit=1)
                proposal=previous[0]["payload"].get("proposal") if previous else None
                if not proposal:
                    value={"status":"No proposal to evaluate"}
                else:
                    symbols=SYMBOLS if proposal["ticker"]=="ALL" else [proposal["ticker"]]
                    if proposal["parameter"] == "contract_rank_profile":
                        from rank_research import replay_rank_proposal
                        value = {s:replay_rank_proposal(journal,s,scope,proposal) for s in symbols}
                    else:
                        value={s:replay_proposal(matured(training_records(journal,s,scope)),proposal) for s in symbols}
            journal.insert("v3:"+stage+":"+scope,"DESK","v3_"+stage,scope,now,value)
            finish_stage(journal,d,stage,lease)


if __name__=="__main__":
    from research_journal import Journal
    daily(Journal(),datetime.now(timezone.utc))
