"""Conditional option scenarios on an absolute calendar-time axis."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import re

import numpy as np
from scipy.optimize import brentq
from scipy.special import ndtr

from market_session import ET, calendar, rth_bounds
from terminal_data import valid_date
from option_payoff import price_option

YEAR = 365.25 * 86400
UNITS = {"premium":"$/share","delta":"Δ per $","gamma":"Δ per $²","iv":"%",
         "theta":"$/calendar day","vega":"$/IV point","rho":"$/rate point","vanna":"Δ/IV point",
         "vomma":"$/IV point²","speed":"gamma/$","charm":"Δ/day","color":"gamma/day"}


@dataclass(frozen=True)
class Spec:
    code: str
    strike: float
    right: str
    american: bool
    last_trade: datetime
    expires: datetime
    settlement: str
    multiplier: int = 100


def contract_spec(row, symbol):
    code = str(row.get("contract","")).replace(" ","").upper()
    m = re.fullmatch(r"(SPXW|SPXO|SPX|SPY|QQQ)(\d{6})([CP])(\d{8})",code)
    day = valid_date(row.get("expiry"))
    if not m or not day or day.strftime("%y%m%d")!=m[2] or row.get("standard") is False:
        raise ValueError("Missing or inconsistent standard contract identity")
    root = m[1]
    strike = int(m[4])/1000
    right = "call" if m[3]=="C" else "put"
    if (root not in ({"SPX","SPXO","SPXW"} if symbol=="SPX" else {symbol})
            or row.get("symbol") not in (None,symbol) or row.get("strike")!=strike or strike<=0
            or row.get("type")!=right or float(row.get("multiplier",100) or 0)!=100):
        raise ValueError("Adjusted or mismatched contract")
    bounds = rth_bounds(day)
    if not bounds:
        raise ValueError("Expiration is not an exchange session")
    opened, closed = bounds
    if root in ("SPX","SPXO"):
        prev = calendar().previous_session(day.isoformat()).date()
        prev_close = rth_bounds(prev)[1]
        last = prev_close + timedelta(hours=1) if prev_close.astimezone(ET).hour==16 else prev_close
        return Spec(code,strike,right,False,last,opened,"AM · opening settlement reference")
    if root=="SPXW":
        return Spec(code,strike,right,False,closed,closed,"PM · cash settlement")
    # ETF expiration exercise decisions may extend past the closing reference.
    last = closed+timedelta(minutes=15)
    return Spec(code,strike,right,True,last,closed,"Physical · closing-price payoff reference")


def value(spec, spot, years, iv, rate=.04, dividend=0., steps=72):
    s,t,v = np.broadcast_arrays(np.asarray(spot,float),np.asarray(years,float),np.asarray(iv,float))
    if (not all(np.all(np.isfinite(a)) for a in (s,t,v)) or np.any(s<=0) or np.any(t<0) or np.any(v<=0)
            or not np.isfinite([rate,dividend]).all() or not 4<=steps<=512):
        raise ValueError("Invalid scenario inputs")
    if not spec.american:
        return price_option(s,spec.strike,t,v,spec.right,rate,dividend)
    # CRR backward induction across all scenario points, including early exercise at every node.
    n = steps
    dt = np.maximum(t,1e-14)/n
    up = np.exp(v*np.sqrt(dt))
    down = 1/up
    prob = np.expm1((rate-dividend)*dt+np.log(up))/np.expm1(2*np.log(up))
    if np.any((prob<0)|(prob>1)):
        raise ValueError("CRR grid unstable at this carry and IV")
    disc = np.exp(-rate*dt)[...,None]
    sign = 1 if spec.right=="call" else -1
    node = s[...,None] * up[...,None]**(2*np.arange(n+1)-n)
    values = np.maximum(sign*(node-spec.strike),0)
    p=prob[...,None]
    for _ in range(n):
        node = node[...,:-1]*up[...,None]
        values = np.maximum(sign*(node-spec.strike),disc*((1-p)*values[...,:-1]+p*values[...,1:]))
    return np.where(t>0,values[...,0],np.maximum(sign*(s-spec.strike),0))


def calibrated_iv(spec, spot, now, mid, rate, dividend):
    years=(spec.expires-now).total_seconds()/YEAR
    if years<=0 or mid<=0:
        return None
    try:
        return float(brentq(lambda vol: float(value(spec,spot,years,vol,rate,dividend))-mid,.02,4.,xtol=1e-6))
    except ValueError:
        return None


def greeks(spec,s,t,v,r=.04,q=0.,extended=False):
    s,t,v=np.broadcast_arrays(np.asarray(s,float),np.asarray(t,float),np.asarray(v,float))
    base=value(spec,s,t,v,r,q)
    # Use a bump comparable to the lattice spacing for American derivatives.
    h=np.maximum(s*.0001, s*v*np.sqrt(t/72) if spec.american else .0001)
    h=np.minimum(h,s*.1)
    dv=np.minimum(.005,v/4)
    dt=np.minimum(t/2,1/(365.25*24))
    def spatial(spot,time,vol):
        p=value(spec,spot,time,vol,r,q)
        up=value(spec,spot+h,time,vol,r,q)
        down=value(spec,spot-h,time,vol,r,q)
        return (up-down)/(2*h),(up-2*p+down)/h**2
    delta,gamma=spatial(s,t,v)
    vp,vm=value(spec,s,t,v+dv,r,q),value(spec,s,t,v-dv,r,q)
    tomorrow=value(spec,s,t-dt,v,r,q)
    days=np.where(dt>0,dt*365.25,1.)
    result=dict(premium=base,iv=v*100,delta=delta,gamma=gamma,vega=(vp-vm)/(2*dv)*.01,theta=(tomorrow-base)/days)
    if extended:
        dp,_=spatial(s,t,v+dv)
        dm,_=spatial(s,t,v-dv)
        td,tg=spatial(s,t-dt,v)
        _,gu=spatial(s+h,t,v)
        _,gd=spatial(s-h,t,v)
        result.update(rho=(value(spec,s,t,v,r+.001,q)-value(spec,s,t,v,r-.001,q))*5,
                      vanna=(dp-dm)/(2*dv)*.01,vomma=(vp-2*base+vm)/dv**2*.0001,
                      speed=(gu-gd)/(2*h),charm=(td-delta)/days,color=(tg-gamma)/days)
    return {k:(val if k=="premium" else np.where(t>0,val,np.nan)) for k,val in result.items()}


def timeline(now, expiry, minutes=0, *, arrival=None):
    seconds=(expiry-now).total_seconds()
    if seconds<=0: raise ValueError("Contract payoff reference has passed")
    days=seconds/86400
    step = minutes or next(m for limit,m in [(1,3),(2,10),(4,20),(8,60),(15,180),(31,360),(float("inf"),1440)] if days<=limit)
    if step<=0 or seconds/(60*step)>5000:
        raise ValueError("Select a coarser interval (at most 5,000 points).")
    times=[now+i*timedelta(minutes=step) for i in range(int(seconds//(60*step))+1)]
    return sorted(set(times+[expiry]+([arrival] if arrival and now<arrival<expiry else [])))


def scenario(spec, now, times, spot, target, arrival, iv, target_iv, rate, dividend, custom=None):
    if arrival<=now or any(x<now or x>spec.expires for x in times):
        raise ValueError("Scenario time outside contract lifetime")
    seconds=np.array([(t-now).total_seconds() for t in times])
    knots=[(0,spot),*(custom or []),((arrival-now).total_seconds(),target)]
    if any(a[0]>=b[0] or a[1]<=0 for a,b in zip(knots,knots[1:])) or target<=0:
        raise ValueError("Custom price knots must increase in time and have positive prices")
    prices=np.interp(seconds,*zip(*knots))
    fraction=np.minimum(seconds/(arrival-now).total_seconds(),1)
    vols=iv+(target_iv-iv)*fraction
    remaining=np.array([(spec.expires-at).total_seconds()/YEAR for at in times])
    return prices,remaining,greeks(spec,prices,remaining,vols,rate,dividend)


def research_rank(rows,spot,target,arrival,now,rate,dividend,budget,cost,symbol):
    """Conditional net P&L at the ask, with a modeled exit concession. No probability or entry authorization."""
    output=[]
    for row in rows:
        try: spec=contract_spec(row,symbol)
        except (ValueError,TypeError): continue
        if not now < arrival <= min(spec.last_trade,spec.expires) or row["ask"]*spec.multiplier+cost>budget:
            continue
        from iv_decision import iv_value
        iv=iv_value(row.get("iv"))
        if not iv or not .02<=iv<=4: continue
        try:
            mark=float(value(spec,target,(spec.expires-arrival).total_seconds()/YEAR,iv,rate,dividend))
        except ValueError: continue
        concession=(row["ask"]-row["bid"])/2 if arrival<spec.expires else 0
        pnl=(max(0,mark-concession)-row["ask"])*spec.multiplier-2*cost
        output.append(dict(contract=spec.code,strike=spec.strike,expiry=row["expiry"],conditional_pnl=round(pnl,2),fair_value=mark,
                           debit=row["ask"]*spec.multiplier+cost,spread=round(concession*2,4)))
    return sorted(output,key=lambda x:(-x["conditional_pnl"],x["spread"],x["contract"]))


def block_simulation(spec,candles,now,spot,iv,entry,*,minutes=5,rate=.04,dividend=0.,spread=.1,fee=.65,seed=17):
    """Empirical whole intraday blocks preserve the observed return sequence and jumps."""
    from terminal_data import time_or_none
    from collections import deque
    window=deque(maxlen=minutes+1); blocks=[]; previous=None
    for b in candles:
        stamp=time_or_none(b.get("time_utc"))
        if not stamp or stamp+timedelta(minutes=1)>now or not b.get("closed",True) or b.get("forming"):continue
        if previous is not None and stamp-previous!=timedelta(minutes=1):window.clear()
        window.append(b["close"]);previous=stamp
        if len(window)==minutes+1:blocks.append(np.array(window)/window[0])
    end=now+timedelta(minutes=minutes)
    if len(blocks)<100 or end>min(spec.last_trade,spec.expires):return None
    # Blocks overlap. N paths is not the number of independent sessions.
    rng=np.random.default_rng(seed)
    paths=np.array(blocks)[rng.integers(len(blocks),size=min(400,len(blocks)))]*spot
    times=np.array([(spec.expires-now-timedelta(minutes=j)).total_seconds()/YEAR for j in range(minutes+1)])
    marks=value(spec,paths,times,iv,rate,dividend)
    pnl=(np.maximum(0,marks-spread/2)-entry)*spec.multiplier-2*fee
    ends=pnl[:,-1];tail=ends[ends<=np.quantile(ends,.05)]
    return dict(blocks=len(blocks),paths=len(paths),minutes=minutes,mean_net_pnl=float(ends.mean()),
                loss_fraction=float(np.mean(ends<0)),p05=float(np.quantile(ends,.05)),p95=float(np.quantile(ends,.95)),
                tail_mean=float(tail.mean()),average_mae=float(np.minimum(pnl.min(1),0).mean()),
                average_mfe=float(np.maximum(pnl.max(1),0).mean()),assumptions="Empirical overlapping price blocks; constant IV and quoted spread; uncalibrated.")


def skew_scenario(rows, strike, spot, target):
    """Sticky-moneyness interpolation within one expiry/right. Refuse extrapolation."""
    from iv_decision import iv_value
    knots={}
    for row in rows:
        iv=iv_value(row.get("iv"))
        k=row.get("strike")
        if iv and isinstance(k,(int,float)) and k>0:
            knots[k]=iv
    if len(knots)<3 or min(spot,target)<=0:return None
    axis=sorted(knots)
    equivalent_strike=strike*spot/target
    if not axis[0]<=min(strike,equivalent_strike) or max(strike,equivalent_strike)>axis[-1]:return None
    vols=[knots[k] for k in axis]
    return float(np.interp(equivalent_strike,axis,vols)-np.interp(strike,axis,vols))
