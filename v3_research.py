"""Matured, point-in-time barrier labels and chronological challenger evaluation."""
from v3_provenance import live_record
from collections import Counter, defaultdict
from datetime import timedelta
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from terminal_data import time_or_none
from v3_movement import HORIZONS, MINUTE, VERSION, active_mode

FEATURES=("price_z","acceleration","persistence","range_position","net_pressure","footprint_pressure","relative_volume")
SYMBOLS=("SPY","SPX","QQQ")


def same_model(row, version, mode):
    sig = row.get("payload", {}).get("signal", {})
    return live_record(row) and sig.get("version") == version and sig.get("mode") == mode


def scope_rows(records):
    groups=defaultdict(dict)
    for entry in sorted(records,key=lambda e:e["origin"]):
        p=entry["payload"]
        s=p["signal"]
        if not live_record(entry) or not p["ohlcv"] or not s["forecasts"]: continue
        # Never stitch different price sources, option scopes or model versions across a label path.
        key=(s["symbol"],s["session"],s["source"],p["scope"],s["version"],s["mode"])
        groups[key].setdefault(time_or_none(s["at"]),entry)
    return groups


def matured(records):
    examples=[]
    for key,by_time in scope_rows(records).items():
        for at,row in sorted(by_time.items()):
            p=row["payload"]; sig=p["signal"]
            processed=time_or_none(row["origin"])
            start=processed.replace(second=0,microsecond=0)
            if start<processed: start+=MINUTE
            for h in HORIZONS:
                future=[by_time.get(start+i*MINUTE) for i in range(1,h+1)]
                if any(e is None for e in future): continue
                # Start at the first complete observable post-decision minute, including its opening gap.
                bars=[e["payload"]["ohlcv"] for e in future]
                barrier=sig["forecasts"][str(h)]
                outcome,arrival="neither",None
                for index,b in enumerate(bars,1):
                    upper,lower=b["high"]>=barrier["upper"],b["low"]<=barrier["lower"]
                    if upper or lower:
                        outcome="ambiguous" if upper and lower else "up" if upper else "down"
                        arrival=(start+index*MINUTE-processed).total_seconds()
                        break
                direction=1 if outcome=="up" else -1 if outcome=="down" else 0
                examples.append(dict(symbol=key[0],session=key[1],source=key[2],scope=key[3],version=key[4],mode=key[5],at=row["origin"],end=(start+h*MINUTE).isoformat(),
                                     horizon=h,outcome=outcome,arrival_seconds=arrival,
                                     up_excursion=max(b["high"] for b in bars)-sig["price"],
                                     down_excursion=sig["price"]-min(b["low"] for b in bars),
                                     features=sig["features"],regime=sig.get("regime"),ignition=sig["scores"].get("ignition"),state=sig["state"],side=sig["side"],label=direction,
                                     legacy_warning=p.get("legacy_warning",0),baseline_side={"ENTER LONG":1,"ENTER SHORT":-1}.get(p["decision"],0)))
    return examples


def summary(examples,horizon=5):
    cases=[r for r in examples if r["horizon"]==horizon]
    classified=[r for r in cases if r["outcome"]!="ambiguous"]
    counts=Counter(r["outcome"] for r in cases)
    output={}
    for label in ("baseline_entry","baseline_move_watch","v3_ignition"):
        signals=[(r, r["baseline_side"] if label=="baseline_entry" else r["legacy_warning"] if label=="baseline_move_watch" else
                  r["side"] if any(k in r["state"] for k in ("IGNITION","EXPANSION")) else 0) for r in classified]
        positives=sum(bool(s) for _,s in signals)
        good=[r for r,s in signals if s and s==r["label"]]
        events=sum(bool(r["label"]) for r in classified)
        output[label]=dict(warning_origins=positives,correct=len(good),missed_or_wrong=events-len(good),
                           precision=len(good)/positives if positives else None,recall=len(good)/events if events else None,
                           median_seconds_to_touch=float(np.median([r["arrival_seconds"] for r in good])) if good else None)
    return dict(horizon=horizon,origins=len(cases),outcomes=dict(counts),comparison=output)


def matrix(rows, features):
    # Missing indicators get explicit missingness flags; no historic flow is synthesized.
    raw=np.array([[r["features"].get(k) if r["features"].get(k) is not None else np.nan for k in features] for r in rows])
    return np.column_stack((np.nan_to_num(raw),~np.isfinite(raw)))


def logistic(train,test,features=FEATURES):
    x,xt=matrix(train,features),matrix(test,features)
    mean,std=x.mean(0),np.maximum(x.std(0),1e-5)
    x=np.column_stack((np.ones(len(x)),np.clip((x-mean)/std,-8,8)))
    xt=np.column_stack((np.ones(len(xt)),np.clip((xt-mean)/std,-8,8)))
    y=np.array([r["label"]==1 for r in train],dtype=float)
    def loss(w):
        z=x@w
        reg=w.copy();reg[0]=0
        return (np.logaddexp(0,z)-y*z).mean()+.005*(reg@reg),x.T@(expit(z)-y)/len(x)+.01*reg
    fit=minimize(loss,np.zeros(x.shape[1]),jac=True,method="L-BFGS-B")
    if not fit.success: raise ValueError("Candidate did not converge")
    return expit(xt@fit.x)


def benchmark(examples):
    rows=sorted((r for r in examples if r["horizon"]==5 and r["outcome"]!="ambiguous"),key=lambda r:r["at"])
    days=sorted({r["session"] for r in rows})
    if len(days)<20 or len(rows)<500:
        return dict(status="Collecting observations",sessions=len(days),eligible=len(rows),minimum_sessions=20,minimum_origins=500)
    # Four complete sessions stay untouched. Session boundaries purge overlapping intraday labels.
    development=days[:-4]
    metrics=[]
    def evaluate(train,test,stage):
        if not test or len({r["label"]==1 for r in train})<2: return
        y=np.array([r["label"]==1 for r in test])
        base=np.mean([r["label"]==1 for r in train])
        p=logistic(train,test); price=logistic(train,test,FEATURES[:4])
        metrics.append(dict(stage=stage,n=len(y),first=test[0]["session"],last=test[-1]["session"],
                            brier=float(np.mean((p-y)**2)),climatology_brier=float(np.mean((base-y)**2)),
                            price_only_brier=float(np.mean((price-y)**2)),observed_frequency=float(y.mean()),
                            mean_prediction=float(p.mean())))
    for i in range(10,len(development),3):
        evaluate([r for r in rows if r["session"] in development[:i]],
                 [r for r in rows if r["session"] in development[i:i+3]],"walk-forward")
    evaluate([r for r in rows if r["session"] in development],[r for r in rows if r["session"] in days[-4:]],"holdout")
    from v3_movement import enabled
    if enabled("EXTRA_MODELS") and metrics and metrics[-1]["stage"]=="holdout":
        from v3_challenger import optional_models
        metrics[-1]["extra_models"]=optional_models([r for r in rows if r["session"] in development],
                                                    [r for r in rows if r["session"] in days[-4:]])
    from v3_challenger import component_models
    return dict(status="Research only",target="up barrier first within 5 minutes",sessions=len(days),folds=metrics,
                components=component_models(rows,development,days[-4:]),
                promotion=False)


def archive(records,root,symbol,day):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from tempfile import NamedTemporaryFile
    root=Path(root); root.mkdir(exist_ok=True,parents=True)
    target=root/f"{day}-{symbol}.parquet"
    rows=[dict(id=e["id"],origin=e["origin"],payload=json.dumps(e["payload"],allow_nan=False)) for e in records if live_record(e)]
    if rows:
        with NamedTemporaryFile(dir=root,suffix=".partial",delete=False) as temp: path=Path(temp.name)
        try:
            pq.write_table(pa.Table.from_pylist(rows),path,compression="zstd")
            path.replace(target)
        finally: path.unlink(missing_ok=True)
    return len(rows)


def pricing_errors(records):
    """Pair original scenarios with later eligible observed quotes. Never forward-fill outcomes."""
    from v3_options import contract_spec, value, YEAR, greeks
    residuals=defaultdict(list)
    for group in scope_rows(records).values():
        observations=defaultdict(list)
        predictions=[]
        for at,e in sorted(group.items()):
            p=e["payload"]
            for o in p.get("quote_samples",p.get("options",[])):
                stamp=time_or_none(o.get("updated_at"))
                if stamp and stamp<=time_or_none(e["origin"]):
                    observations[o["contract"]].append((stamp,e,o))
            predictions.extend((e,o) for o in p.get("options",[]))
        for e,o in predictions:
            target=time_or_none(o["arrival"])
            found=next(((t,ev,u) for t,ev,u in observations[o["contract"]] if target<=t<target+MINUTE and ev["origin"]>e["origin"]),None)
            if not found:continue
            t,ev,u=found
            try:
                sig=e["payload"]["signal"]
                spec=contract_spec(o,sig["symbol"])
                spot=ev["payload"]["signal"]["price"]
                if not spot or not u.get("iv") or abs((time_or_none(ev["payload"]["signal"]["at"])-t).total_seconds())>60:continue
                years=max(0,(spec.expires-t).total_seconds()/YEAR)
                model=greeks(spec,spot,years,u["iv"])
                mid=(u["ask"]+u["bid"])/2
                item={"conditional_mae":abs(float(model["premium"])-mid),"total_forecast_mae":abs(o["forecast"]-mid),
                      "iv_mae_points":100*abs(u["iv"]-o["iv"]),"underlying_target_error":abs(spot-o["target"]),
                      "net_bid_ask_pnl":100*(u["bid"]-o["ask"])-1.30}
                for name in ("delta","gamma","theta","vega"):
                    if u.get(name) is not None and np.isfinite(model[name]):
                        item[name+"_conditional_mae"]=abs(float(model[name])-u[name])
                dte=(spec.expires.date()-time_or_none(e["origin"]).date()).days
                money=abs(spec.strike/sig["price"]-1)
                groups=("all","DTE "+str(dte),"ATM within 0.5%" if money<=.005 else "Outside 0.5%",
                        "Regime "+str(sig.get("regime")),"Recommended" if e["payload"].get("chosen")==o["contract"] else "Alternative")
                for key in groups:residuals[key].append(item)
            except (TypeError,ValueError,KeyError):continue
    if not residuals:return {"matched":0}
    def describe(rows):
        return {"matched":len(rows),**{k:float(np.mean([v[k] for v in rows if k in v])) for k in set().union(*rows)}}
    overall=describe(residuals.pop("all"))
    return {**overall,"groups":{key:describe(v) for key,v in residuals.items()}}


def event_summary(records,horizon=5):
    """Collapse overlapping same-side touches into episodes; attribute earlier warnings using processing time."""
    examples=matured(records)
    origins=[r for r in examples if r["horizon"]==horizon]
    by=defaultdict(list)
    for r in origins:by[(r["symbol"],r["session"])].append(r)
    outcomes=Counter();latencies=[];episodes=alerts=0;covered=set(); false=0
    for (sym,day),rows in by.items():
        rows.sort(key=lambda r:r["at"])
        alerts_list=[];events=[];end=None;last_side=0;last_warning=0
        for r in rows:
            t=time_or_none(r["at"])
            covered.add((sym,day,t.replace(second=0,microsecond=0)))
            side=r["side"] if any(x in r["state"] for x in ("PRESSURE","IGNITION","EXPANSION")) and r["state"]!="CONFLICTING PRESSURE" else 0
            if side and side!=last_warning:alerts_list.append((t,side))
            last_warning=side
            if r["label"] and (end is None or t>end or r["label"]!=last_side):
                touch=t+timedelta(seconds=r["arrival_seconds"])
                events.append((t,touch,r["label"]))
                end=touch;last_side=r["label"]
        matched=set()
        for start,touch,direction in events:
            candidates=[(i,t,s) for i,(t,s) in enumerate(alerts_list) if start-timedelta(minutes=horizon)<=t<=touch and i not in matched]
            good=next(((i,t) for i,t,s in candidates if s==direction),None)
            if good:
                matched.add(good[0]);latencies.append((touch-good[1]).total_seconds())
                outcomes["early" if good[1]<=start else "after_origin_before_touch"]+=1
            elif candidates:outcomes["wrong_direction"]+=1
            else:outcomes["missed"]+=1
        episodes+=len(events);alerts+=len(alerts_list);false+=len(alerts_list)-len(matched)
    return dict(episodes=episodes,warning_transitions=alerts,unmatched_warnings=false,classification=dict(outcomes),
                false_per_covered_hour=60*false/len(covered) if covered else None,
                median_lead_to_touch=float(np.median(latencies)) if latencies else None)


def market_events(records, health_rows, horizon=5):
    """Independent close-origin price events, including warm-up WAITs. Never bridge gaps."""
    from math import sqrt
    from v3_movement import robust_scale
    grouped=defaultdict(dict)
    for r in sorted([*records,*health_rows],key=lambda v:v["origin"]):
        p=r["payload"]
        s=p["signal"]
        if not live_record(r) or not p.get("ohlcv") or not s.get("at"):
            continue
        t=time_or_none(s["at"])
        bar=p["ohlcv"]
        if not all(isinstance(bar.get(k),(float,int)) and bar[k]>0 for k in ("high","low","close")):
            continue
        # One physical bar per provider and ticker even if multiple option scopes were observed.
        group=grouped[(s["symbol"],s["session"],s["source"])]
        if t not in group or not group[t]["payload"]["signal"].get("scores") and s.get("scores"):
            group[t]=r
    events=[]
    eligible=ambiguous=0
    for (symbol,session,source),bars in grouped.items():
        last_touch=None
        for t,r in sorted(bars.items()):
            if last_touch is not None and t<last_touch:continue
            past=[bars.get(t-i*MINUTE) for i in reversed(range(7))]
            future=[bars.get(t+i*MINUTE) for i in range(1,horizon+1)]
            if any(v is None for v in [*past,*future]):continue
            closes=[x["payload"]["ohlcv"]["close"] for x in past]
            threshold=robust_scale([b-a for a,b in zip(closes,closes[1:])],closes[-1]*.00012)*sqrt(horizon)
            eligible+=1
            for mark in future:
                bar=mark["payload"]["ohlcv"]
                up=bar["high"]-closes[-1]>=threshold
                down=closes[-1]-bar["low"]>=threshold
                if not up and not down:continue
                touch=time_or_none(mark["payload"]["signal"]["at"])
                last_touch=touch
                if up and down:
                    ambiguous+=1
                    break
                side=1 if up else -1
                watches=[]
                for i in range(-horizon,horizon+1):
                    row=bars.get(t+i*MINUTE)
                    if not row:continue
                    observed=time_or_none(row["origin"])
                    sig=row["payload"]["signal"]
                    if not t-horizon*MINUTE<=observed<=touch or sig.get("state")=="CONFLICTING PRESSURE":continue
                    if any(tag in sig["state"] for tag in ("IGNITION","EXPANSION","PRESSURE")):
                        watches.append((observed,sig))
                hit=next(((at,s) for at,s in sorted(watches,key=lambda x:x[0]) if s["side"]==side),None)
                category=("early" if hit[0]<=t else "late") if hit else "wrong_direction" if watches else "missed"
                seen=[v["payload"]["ohlcv"] for v in future if time_or_none(v["payload"]["signal"]["at"])<=touch]
                magnitude=(max(v["high"] for v in seen)-closes[-1] if up else closes[-1]-min(v["low"] for v in seen))
                events.append(dict(symbol=symbol,session=session,source=source,origin=t.isoformat(),touch=touch.isoformat(),
                                   direction=side,barrier_points=threshold,magnitude_to_touch=magnitude,
                                   classification=category,lead_to_touch_seconds=(touch-hit[0]).total_seconds() if hit else None,
                                   delay_from_origin_seconds=(hit[0]-t).total_seconds() if hit else None,
                                   excursion_at_warning=side*(hit[1]["price"]-closes[-1]) if hit else None,
                                   origin_state=r["payload"]["signal"]["state"],decision=r["payload"]["decision"],
                                   blockers=r["payload"].get("blockers",[]),data_quality=r["payload"]["signal"].get("quality",{})))
                break
    return {"events":events,"eligible_origins":eligible,"ambiguous":ambiguous,"horizon":horizon}


def training_records(journal,symbol,through,limit=18000,*,version=VERSION,mode=None):
    """Bound memory on the small hosted worker. Historical contract chains are irrelevant to barrier fitting."""
    rows=[]
    mode=mode or active_mode()
    with journal.connection() as db:
        cur=db.execute(journal.sql("SELECT id,scope,origin,payload FROM qd25_observation WHERE symbol=? "
                                   "AND kind='v3_scan' AND scope<=? ORDER BY origin DESC,id DESC LIMIT ?"),
                       (symbol,through,limit))
        while batch:=cur.fetchmany(32):
            for ident,scope,origin,raw in batch:
                payload=json.loads(raw)
                if not same_model({"payload": payload}, version, mode):
                    continue
                slim={k:payload[k] for k in ("signal","ohlcv","scope","decision","legacy_warning") if k in payload}
                rows.append(dict(id=ident,scope=scope,origin=origin,payload=slim))
    return rows
