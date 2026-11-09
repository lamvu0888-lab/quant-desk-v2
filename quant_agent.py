"""Analysis-only options exposure scanner. No trade APIs are imported or called."""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
import numpy as np
from dotenv import load_dotenv
from scipy.stats import norm

load_dotenv()
UTC = timezone.utc

from data_utils import DataError

def number(v, name, *, minimum=None):
    try:
        if isinstance(v, bool) or v is None: raise ValueError()
        n = float(v)
        if not math.isfinite(n) or (minimum is not None and n < minimum): raise ValueError()
        return n
    except (ValueError, TypeError):
        raise DataError(f"Invalid {name}: {v!r}")

def instant(v):
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v) / (1000 if v > 1e11 else 1), UTC)
    if isinstance(v, datetime):
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
    if not isinstance(v, str): raise DataError('Missing timestamp')
    try:
        dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
        if dt.tzinfo is None: raise ValueError('timezone missing')
        return dt.astimezone(UTC)
    except ValueError as e: raise DataError(f'Invalid timestamp {v}: {e}') from e

@dataclass(frozen=True)
class Contract:
    expiry: datetime
    strike: float
    right: str
    oi: float
    iv: float
    multiplier: float

@dataclass(frozen=True)
class Snapshot:
    ticker: str
    spot: float
    quote_time: datetime
    contracts: list[Contract]
    completeness_confirmed: bool
    delay_minutes: float

def validate(payload: dict, ticker: str, max_age: float, min_contracts: int, min_expiries: int) -> Snapshot:
    if not isinstance(payload, dict): raise DataError('Adapter must return a dictionary')
    if str(payload.get('ticker','')).upper() != ticker.upper(): raise DataError('Ticker mismatch')
    spot = number(payload.get('spot'), 'spot', minimum=0.000001)
    qt = instant(payload.get('quote_time_utc'))
    age = (datetime.now(UTC)-qt).total_seconds()
    if age < -5 or age > max_age: raise DataError(f'Stale/future quote: age {age:.1f}s')
    delay = number(payload.get('delay_minutes'), 'delay_minutes', minimum=0)
    if delay != 0: raise DataError(f'Delayed quote ({delay} min): not live')
    if payload.get('chain_complete') is not True: raise DataError('Chain pagination/completeness not confirmed')
    if payload.get('oi_asof_verified', True):
        if payload.get('open_interest_asof') is None: raise DataError('Missing open-interest as-of date')
        oi_asof = instant(payload['open_interest_asof'])
        if (datetime.now(UTC)-oi_asof).total_seconds() > 5*86400 or oi_asof > datetime.now(UTC)+timedelta(minutes=5):
            raise DataError('Open-interest snapshot too old or in future')
    raw = payload.get('contracts')
    if not isinstance(raw,list): raise DataError('contracts must be a list')
    contracts=[]; keys=set(); rejected=0
    for x in raw:
        try:
            expiry = instant(x['expiry_utc'])
            strike = number(x['strike'], 'strike', minimum=0.000001)
            right = str(x['right']).upper()
            if right not in ('C','P'): raise DataError('Invalid right')
            oi = number(x['open_interest'], 'open_interest', minimum=0)
            iv = number(x['iv_decimal'], 'iv_decimal', minimum=0.000001)
            if iv > 5: raise DataError('IV must be decimal, e.g. 0.25')
            multiplier = number(x['multiplier'], 'multiplier', minimum=0.000001)
            if expiry <= datetime.now(UTC): raise DataError('Expired contract')
            key=(expiry,strike,right)
            if key in keys: raise DataError('Duplicate contract')
            keys.add(key)
            contracts.append(Contract(expiry,strike,right,oi,iv,multiplier))
        except (KeyError,DataError,TypeError): rejected+=1
    if rejected: raise DataError(f'{rejected} malformed/duplicate/expired contracts; scan rejected to avoid distorted exposure')
    if len(contracts) < min_contracts: raise DataError(f'Only {len(contracts)} contracts (minimum {min_contracts})')
    if len({x.expiry for x in contracts}) < min_expiries: raise DataError('Insufficient expirations')
    rights={x.right for x in contracts}
    if rights != {'C','P'}: raise DataError('Both call and put contracts required')
    if not any(x.strike < spot for x in contracts) or not any(x.strike > spot for x in contracts):
        raise DataError('Chain does not span current spot')
    return Snapshot(ticker,spot,qt,contracts,True,delay)

def greeks(s, k, t, sigma, r, q):
    """Gamma, vanna=d(delta)/d(sigma), charm=d(delta)/d(calendar day).
    Dividend-adjusted Black-Scholes, calendar-time T in years.
    """
    root=math.sqrt(t)
    d1=(math.log(s/k)+(r-q+0.5*sigma*sigma)*t)/(sigma*root)
    d2=d1-sigma*root
    pdf=norm.pdf(d1)
    disc=math.exp(-q*t)
    gamma=disc*pdf/(s*sigma*root)
    vanna=-disc*pdf*d2/sigma
    # d(delta)/dT for call or put: -q*delta + e^-qT phi(d1)*d(d1)/dT
    dd1dt=(r-q+0.5*sigma*sigma)/(2*sigma*root)-math.log(s/k)/(2*sigma*t*root)
    return gamma,vanna,disc,pdf,dd1dt,d1

def exposures(snapshot, r=.04, q=0.0, grid_points=81):
    """OI sign convention: calls +, puts -. Outputs hypothetical signed exposure.
    GEX: dollars per 1% spot move. VEX: delta shares per +1 vol point.
    Charm: delta shares per elapsed calendar day.
    """
    s=snapshot.spot
    rows={}; per_exp={}; terms=[]
    for c in snapshot.contracts:
        t=(c.expiry-datetime.now(UTC)).total_seconds()/(365.25*86400)
        t=max(t,1/(365.25*24*60))
        sign=1 if c.right=='C' else -1
        gamma,vanna,disc,pdf,dd1dt,d1=greeks(s,c.strike,t,c.iv,r,q)
        delta=disc*(norm.cdf(d1) if c.right=='C' else norm.cdf(d1)-1)
        # calendar time passes -> dT/d(day)=-1/365.25
        ddelta_dT=-q*delta+disc*pdf*dd1dt
        charm=-ddelta_dT/365.25
        signed_delta=sign*delta*c.oi*c.multiplier
        gex=sign*gamma*c.oi*c.multiplier*s*s*.01
        vex=sign*vanna*c.oi*c.multiplier*.01
        chex=sign*charm*c.oi*c.multiplier
        row=rows.setdefault(c.strike,{'strike':c.strike,'gex':0.,'vex':0.,'charm':0.,'delta':0.,'call_oi':0.,'put_oi':0.})
        row['gex']+=gex;row['vex']+=vex;row['charm']+=chex;row['delta']+=signed_delta
        row['call_oi' if c.right=='C' else 'put_oi']+=c.oi
        exp=c.expiry.date().isoformat()
        per_exp[exp]=per_exp.get(exp,0.)+gex
        terms.append((c,sign,t))
    ranked=sorted(rows.values(),key=lambda x:x['strike'])
    call_wall=max(ranked,key=lambda x:x['call_oi'])
    put_wall=max(ranked,key=lambda x:x['put_oi'])
    below=[x for x in ranked if x['strike']<s]
    trap=min(below,key=lambda x:x['gex']) if below else None
    if trap is not None and trap['gex']>=0: trap=None
    # Price-sweep gamma-zero crossings; assume fixed OI and IV across sweep.
    prices=np.linspace(s*.8,s*1.2,grid_points)
    totals=[]
    for p in prices:
        total=0.
        for c,sign,t in terms:
            g,*_=greeks(float(p),c.strike,t,c.iv,r,q)
            total+=sign*g*c.oi*c.multiplier*p*p*.01
        totals.append(total)
    flips=[]
    for i in range(len(prices)-1):
        a,b=totals[i],totals[i+1]
        if a==0: flips.append(float(prices[i]))
        elif a*b<0: flips.append(float(prices[i]+(prices[i+1]-prices[i])*(-a)/(b-a)))
    return {'ticker':snapshot.ticker,'spot':s,'quote_time_utc':snapshot.quote_time.isoformat(),
            'contracts':len(snapshot.contracts),'expiries':len(per_exp),
            'net_gex_usd_per_1pct':sum(x['gex'] for x in ranked),
            'net_vex_delta_shares_per_vol_point':sum(x['vex'] for x in ranked),
            'net_charm_delta_shares_per_day':sum(x['charm'] for x in ranked),
            'net_delta_proxy_shares':sum(x['delta'] for x in ranked),
            'call_oi_wall':call_wall['strike'],'put_oi_wall':put_wall['strike'],
            'gamma_flips':flips,'nearest_gamma_flip':min(flips,key=lambda x:abs(x-s)) if flips else None,
            'downside_negative_gex_strike':trap['strike'] if trap else None,
            'downside_negative_gex':trap['gex'] if trap else None,
            'downside_distance_pct':100*(s-trap['strike'])/s if trap else None,
            'gex_by_expiry':per_exp,'by_strike':ranked,
            'assumption':'Calls positive / puts negative OI-sign proxy; NOT observed dealer positioning',
            'units':'GEX USD per 1% spot; VEX delta shares per +1 vol point; Charm delta shares per elapsed calendar day'}

def demo(ticker):
    now=datetime.now(UTC)
    s={'SPY':600,'SPX':6000,'QQQ':500,'AVGO':330,'NVDA':180}.get(ticker,100)
    exp=(now+timedelta(days=7)).replace(hour=20,minute=0,second=0,microsecond=0)
    contracts=[]
    for strike in [s*x for x in (.90,.94,.97,.99,1.01,1.03,1.06,1.10)]:
        for right in ('C','P'):
            contracts.append({'expiry_utc':exp.isoformat(),'strike':strike,'right':right,
                              'open_interest':int(1000+500*abs(strike/s-1)),'iv_decimal':.25,'multiplier':100})
    return {'ticker':ticker,'spot':s,'quote_time_utc':now.isoformat(),'delay_minutes':0,
            'chain_complete':True,'open_interest_asof':now.isoformat(),'contracts':contracts}

def run():
    """Optional headless collector. The normal entry point is the dashboard."""
    from dataclasses import replace
    from live_engine import LiveEngine
    from settings import Settings
    from data_utils import safe_error
    parser=argparse.ArgumentParser(description='Quant Options Agent 1.9 read-only collector')
    parser.add_argument('--ticker',default='SPY')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--output',default='reports')
    parser.add_argument('--interval',type=int,default=None)
    parser.add_argument('--volatility',action='store_true',help='Retained compatibility flag; candle analytics are always enabled')
    parser.add_argument('--demo',action='store_true',help='Generate one explicitly labeled synthetic math test report')
    args=parser.parse_args();ticker=args.ticker.strip().upper()
    if args.demo:
        result=exposures(validate(demo(ticker),ticker,90,10,1))
        result['data_status']='SYNTHETIC_DEMO';result['source']='SYNTHETIC'
        out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
        (out/(ticker+'_SYNTHETIC_DEMO.json')).write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
        print('Synthetic math test only. No Webull connection or live forecast.');return 0
    try:
        settings=Settings.load()
        if args.interval is not None:
            if not 60<=args.interval<=1800: parser.error('Option scan interval must be 60–1800 seconds')
            settings=replace(settings,chain_seconds=args.interval)
        engine=LiveEngine(ticker,settings,output_dir=args.output);engine.start()
        started=time.monotonic();last=''
        while True:
            state=engine.snapshot()
            status=state['connection']+' | '+state.get('chain_progress','')
            if status!=last: print(status,flush=True);last=status
            if state['quote_error']: print(state['quote_error'],flush=True)
            if not engine.alive(): return 2
            if args.once and state.get('exposure') and state.get('history'): engine.stop();return 0
            if args.once and time.monotonic()-started>120:
                engine.stop();print('Collection did not complete. Inspect dashboard data health.');return 2
            time.sleep(1)
    except KeyboardInterrupt:
        if 'engine' in locals(): engine.stop()
        return 0
    except Exception as exc:
        print(safe_error(exc),file=sys.stderr);return 2

if __name__=='__main__': sys.exit(run())
