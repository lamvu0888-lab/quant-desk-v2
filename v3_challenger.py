"""Offline shadow experiments. No production model loader or trading permissions."""
from collections import defaultdict, deque
from math import log, sqrt
import statistics
import numpy as np
from scipy.special import logsumexp
from terminal_data import time_or_none


def change_probability(returns,hazard=.04):
    """Gaussian mean-shift BOCPD on standardized increments; P(run length <= 1)."""
    logp=np.array([0.]); means=np.array([0.]); counts=np.array([0.])
    for x in returns[-24:]:
        var=1+1/(counts+1)
        predictive=-.5*(np.log(2*np.pi*var)+(np.clip(x,-12,12)-means)**2/var)
        new=np.r_[logsumexp(logp+predictive)+log(hazard),logp+predictive+log(1-hazard)]
        logp=new-logsumexp(new)
        means=np.r_[0.,((counts+1)*means+np.clip(x,-12,12))/(counts+2)]
        counts=np.r_[0.,counts+1]
    return float(np.exp(logp[:2]).sum())


def lead_lag(series,leader,follower):
    """Use only leader values processed before the follower return begins."""
    pairs={}
    for sym in (leader,follower):
        rows=sorted(series.get(sym,[]),key=lambda x:x["origin"])
        obs={}
        for e in rows:
            s=e["payload"]["signal"]
            if s.get("preview") or not s["price"]:continue
            stamp=time_or_none(s["at"])
            obs.setdefault(stamp,(s["price"],time_or_none(e["origin"]),s["source"]))
        pairs[sym]=obs
    from datetime import timedelta
    width=timedelta(minutes=1);a,b=pairs[leader],pairs[follower]
    out={}
    for lag in (1,2,3):
        xy=[]
        for t,(price,received,src) in a.items():
            t0=t+(lag-1)*width;t1=t0+width
            if (t-width not in a or t0 not in b or t1 not in b or a[t-width][2]!=src
                    or b[t1][2]!=b[t0][2] or received>t0 or a[t-width][1]>t0):continue
            xy.append((log(price/a[t-width][0]),log(b[t1][0]/b[t0][0])))
        arr=np.asarray(xy)
        corr=float(np.corrcoef(arr.T)[0,1]) if len(xy)>=30 and np.min(arr.std(0))>1e-12 else None
        out[str(lag)]={"pairs":len(xy),"correlation":corr}
    return out


def profile(examples):
    from market_session import ET
    groups=defaultdict(list)
    for r in examples:
        if r["horizon"]!=5 or r["outcome"]=="ambiguous":continue
        t=time_or_none(r["at"]).astimezone(ET)
        part="opening" if t.hour<10 else "morning" if t.hour<12 else "midday" if t.hour<14 else "afternoon" if t.hour<15 else "closing"
        groups[(part,r.get("regime") or "Unknown")].append(r)
    return [{"time":k[0],"regime":k[1],"n":len(rows),"up":sum(r["label"]==1 for r in rows),
             "down":sum(r["label"]==-1 for r in rows),"neither":sum(r["label"]==0 for r in rows)} for k,rows in groups.items()]


def replay_proposal(examples,proposal):
    """Matched, entry-independent rule replay. Evaluate proposals on sessions AFTER their creation in validation."""
    cases=sorted((r for r in examples if r["horizon"]==5 and r["outcome"]!="ambiguous"),key=lambda r:r["at"])
    ticker=proposal.get("ticker","ALL")
    regime=proposal.get("regime","ALL")
    cases=[r for r in cases if (ticker=="ALL" or r["symbol"]==ticker) and (regime.upper()=="ALL" or r.get("regime")==regime)]
    days=sorted({r["session"] for r in cases})
    if len(days)<20 or len(cases)<500:return {"status":"Insufficient samples","sessions":len(days),"n":len(cases)}
    key=proposal["parameter"]
    def metrics(rows,param):
        past={};pending=defaultdict(deque);correct=sent=events=0
        for r in rows:
            side=r["side"]; k=(r["symbol"],r["session"]);t=time_or_none(r["at"])
            threshold=param if key=="ignition_threshold" else 65
            length=param if key=="persistence_minutes" else 1
            cooldown=param if key=="cooldown_minutes" else 3
            history=pending[k]
            if history and ((t-history[-1][0]).total_seconds()>90 or history[-1][1]!=side):history.clear()
            if side and r.get("ignition",0)>=threshold and r["state"] not in ("CONFLICTING PRESSURE","FAILED MOVE","EXHAUSTION"):
                history.append((t,side))
            else:history.clear()
            events+=bool(r["label"])
            if len(history)<length or k in past and (t-past[k]).total_seconds()<60*cooldown:continue
            sent+=1;correct+=side==r["label"];past[k]=t
        return {"sent":sent,"precision":correct/sent if sent else None,"recall":correct/events if events else None}
    holdout=[r for r in cases if r["session"] in days[-4:]]
    return {"status":"Retrospective shadow comparison","current":metrics(holdout,int(proposal["current"])),
            "proposed":metrics(holdout,int(proposal["proposed"])),"n":len(holdout),"approval":False}


def delayed_online(examples):
    """Predict first; update per-ticker logistic weights only after a label's end timestamp."""
    import heapq
    weights=np.zeros(5);queue=[];errors=[];losses=deque(maxlen=128);drift=0
    for i,r in enumerate(sorted((r for r in examples if r["horizon"]==5 and r["outcome"]!="ambiguous"),key=lambda r:r["at"])):
        at=time_or_none(r["at"])
        while queue and queue[0][0]<at:
            _,_,x,y,p=heapq.heappop(queue)
            weights+=.02*(y-p)*x
            losses.append((y-p)**2)
            if len(losses)==128:
                a,b=list(losses)[:64],list(losses)[64:]
                if np.mean(b)-np.mean(a)>sqrt(log(200)/64):drift+=1;losses.clear()
        f=r["features"]
        x=np.array([1.,np.tanh(f["velocity"]),f["persistence"],f["range_position"],f["net_pressure"] or 0.])
        prob=1/(1+np.exp(-np.clip(weights@x,-20,20)))
        y=float(r["label"]==1)
        errors.append((y-prob)**2)
        heapq.heappush(queue,(time_or_none(r["end"]),i,x,y,prob))
    return {"prequential_brier":float(np.mean(errors)) if errors else None,"n":len(errors),"loss_shift_flags":drift}


def optional_models(train,test):
    """Local research extras only. Each optional family is scored on identical test origins."""
    from importlib.util import find_spec
    from v3_research import matrix, FEATURES
    x,xt=matrix(train,FEATURES),matrix(test,FEATURES)
    y=np.array([r["label"]==1 for r in train],int)
    yt=np.array([r["label"]==1 for r in test],int)
    output={}
    for family in ("lightgbm","xgboost","river"):
        if not find_spec(family):
            output[family]={"status":"Package unavailable"}
            continue
        if family=="lightgbm":
            from lightgbm import LGBMClassifier
            model=LGBMClassifier(n_estimators=40,num_leaves=7,min_child_samples=60,random_state=37,n_jobs=1,verbosity=-1)
        elif family=="xgboost":
            from xgboost import XGBClassifier
            model=XGBClassifier(n_estimators=40,max_depth=2,learning_rate=.06,n_jobs=1,random_state=37)
        else:
            from river import forest, drift
            model=forest.ARFClassifier(n_models=5,seed=37,drift_detector=drift.ADWIN())
            for features,target in zip(x,y):model.learn_one(dict(enumerate(features)),bool(target))
            p=np.array([model.predict_proba_one(dict(enumerate(v))).get(True,0.) for v in xt])
            output[family]={"brier":float(np.mean((p-yt)**2)),"n":len(yt)}
            continue
        model.fit(x,y)
        p=model.predict_proba(xt)[:,1]
        output[family]={"brier":float(np.mean((p-yt)**2)),"n":len(yt),
                        "feature_importance":list(map(float,model.feature_importances_[:len(FEATURES)]))}
    return output


def threshold_search(examples):
    """Predeclared bounded search on development sessions. Hold out the last four days."""
    valid=sorted((r for r in examples if r["horizon"]==5 and r["outcome"]!="ambiguous"),key=lambda r:r["at"])
    days=sorted({r["session"] for r in valid})
    if len(days)<20 or len(valid)<500:return {"status":"Insufficient samples"}
    def assess(rows,cut):
        predictions=[r for r in rows if r["side"] and r["ignition"]>=cut and r["state"] not in ("EXHAUSTION","FAILED MOVE","CONFLICTING PRESSURE")]
        correct=sum(r["side"]==r["label"] for r in predictions)
        total=sum(bool(r["label"]) for r in rows)
        return {"signals":len(predictions),"correct":correct,"f1":2*correct/max(1,total+len(predictions))}
    dev=[r for r in valid if r["session"] in days[8:-4]]
    held=[r for r in valid if r["session"] in days[-4:]]
    cuts=(55,60,65,70,75)
    chosen=max(cuts,key=lambda k:(assess(dev,k)["f1"],-abs(k-65)))
    return {"status":"Shadow only","trials":len(cuts),"cut":chosen,"baseline":assess(held,65),"challenger":assess(held,chosen),"promotion":False}


def component_models(rows, development, held):
    """Calibrate on development sessions only. Training labels are independent price outcomes."""
    from v3_research import logistic
    from scipy.special import expit
    from scipy.optimize import minimize
    def label(row, task):
        side=row["label"]
        trend=np.sign(row["features"].get("persistence") or 0)
        if task=="up":return int(side==1)
        if task=="down":return int(side==-1)
        if task=="neither":return int(side==0)
        if task=="expansion":return int(side!=0)
        if task=="ignition_within_2m":
            arrival=row.get("arrival_seconds")
            return int(bool(side and arrival is not None and arrival<=120))
        if not trend:return None
        if task=="continuation":return int(side==trend)
        return int(side==-trend)
    def subset(days,task):
        return [dict(r,label=y) for r in rows if r["session"] in days and (y:=label(r,task)) is not None]
    result={}
    for task in ("up","down","neither","expansion","ignition_within_2m","continuation","reversal"):
        fit,cal,test=(subset(days,task) for days in (development[:-3],development[-3:],held))
        if len(test)<30 or any(sum(r["label"]==a for r in split)<10 for a in (0,1) for split in (fit,cal)):
            result[task]={"status":"Insufficient class support","train":len(fit),"calibration":len(cal),"test":len(test)}
            continue
        scores=logistic(fit,cal+test)
        logits=np.log(np.clip(scores,1e-7,1-1e-7)/np.clip(1-scores,1e-7,1))
        x=logits[:len(cal)]
        y=np.array([r["label"] for r in cal])
        def loss(params):
            z=params[0]+params[1]*x
            return (np.logaddexp(0,z)-y*z).mean()+.001*(params[1]-1)**2
        params=minimize(loss,[0.,1.],bounds=[(-10,10),(0,8)])
        if not params.success:
            result[task]={"status":"Calibration unavailable"}
            continue
        p=expit(params.x[0]+params.x[1]*logits[len(cal):])
        target=np.array([r["label"] for r in test])
        result[task]={"test":len(test),"events":int(target.sum()),"raw_brier":float(np.mean((scores[len(cal):]-target)**2)),
                      "calibrated_brier":float(np.mean((p-target)**2)),"mean_prediction":float(p.mean()),
                      "observed_frequency":float(target.mean()),"calibration_sessions":len(development[-3:])}
    return result
