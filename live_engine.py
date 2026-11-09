"""Single in-process data worker; independent quotes, candles and options refreshes."""
from __future__ import annotations
import copy
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime,timedelta,timezone
from pathlib import Path
from data_utils import DataError,BudgetPause,safe_error,utc
from settings import Settings,ROOT
from market_session import session_state,ET
from my_webull_adapter import WebullSDKAdapter,aggregate_candles,category_for
from live_chain import ChainCollector
from rate_control import RequestBudget
from quant_agent import Contract,Snapshot,exposures
from volatility import analyze_volatility,CandleError
from confirmation import analyze_confirmation

UTC=timezone.utc


def age_seconds(stamp,now):
    return (now-utc(stamp)).total_seconds() if stamp else None


def data_quality(state,settings,*,now=None):
    """Evaluate market timestamps on EVERY render, even after a worker stops."""
    now=utc(now or datetime.now(UTC));session=session_state(now)
    q,h=state.get('quote') or {},state.get('history') or {}
    q_age=age_seconds(q.get('quote_time_utc'),now)
    trade_age=age_seconds(q.get('price_time_utc'),now)
    recv_age=age_seconds(q.get('received_utc'),now)
    bar_age=age_seconds(h.get('last_bar_utc'),now)
    price=q.get('last_price');price_stamp=q.get('price_time_utc');price_kind='Last trade'
    if (trade_age is None or trade_age>settings.quote_max_age) and q.get('midpoint') is not None and q_age is not None and -5<=q_age<=settings.quote_max_age:
        price=q['midpoint'];price_stamp=q['quote_time_utc'];price_kind='Bid/ask midpoint'
    price_age=age_seconds(price_stamp,now)
    delay=q.get('delay_minutes');source='Webull stock snapshot'
    if delay is None and age_seconds(h.get('received_utc'),now) is not None and age_seconds(h['received_utc'],now)<=300:
        delay=h.get('delay_minutes');source='Webull one-minute bars, same symbol'
    reasons=[]
    if state.get('running') is False: reasons.append('Data polling is paused or stopped')
    if session['status']!='OPEN': reasons.append('Regular session is '+session['status'].lower())
    if state.get('mode')=='SANDBOX': reasons.append('Sandbox endpoint is test data, not a verified production feed')
    if not q: reasons.append('Waiting for Webull stock snapshot')
    if delay is None: reasons.append('Zero-delay stock entitlement not reported by Webull')
    elif delay!=0: reasons.append(f'Webull reports {delay:g}-minute delayed stock data')
    if h.get('delay_minutes') not in (None,0): reasons.append('Candle endpoint reports delayed data')
    if price_age is None or price_age < -5 or price_age>settings.quote_max_age: reasons.append('Price timestamp is stale, missing or in the future')
    if recv_age is None or recv_age < -5 or recv_age>settings.quote_max_age: reasons.append('Quote polling is stale or stopped')
    if h.get('delay_minutes') is None: reasons.append('Candle delay metadata is unverified')
    if bar_age is None or bar_age < 60 or bar_age>180: reasons.append('Waiting for a recent completed RTH one-minute candle')
    if len(h.get('candles',[]))<121: reasons.append('Insufficient one-minute training history')
    if state.get('quote_error'): reasons.append('Latest stock request failed')
    if state.get('history_error'): reasons.append('Latest candle request failed')
    live=not reasons
    coverage=(state.get('exposure') or {}).get('coverage') or {}
    opt_age=age_seconds(coverage.get('oldest_option_quote_utc'),now)
    options_live=(session['status']=='OPEN' and coverage.get('option_delay_minutes')==0 and
                  opt_age is not None and -5<=opt_age<=settings.option_max_age and not state.get('chain_error') and state.get('mode')!='SANDBOX')
    return {'forecast_ready':live,'status':'LIVE · ZERO DELAY' if live else 'FORECAST PAUSED',
            'reasons':reasons,'delay_minutes':delay,'delay_source':source,'quote_age_seconds':q_age,
            'price_age_seconds':price_age,'candle_age_seconds':bar_age,'price':price,'price_kind':price_kind,
            'price_time_utc':price_stamp,'options_live':options_live,'option_age_seconds':opt_age,'session':session}


def model_exposure(contracts,coverage,ticker,spot,settings,*,now=None):
    now=utc(now or datetime.now(UTC))
    parsed=[Contract(utc(x['expiry_utc']),x['strike'],x['right'],x['open_interest'],x['iv_decimal'],x['multiplier'])
            for x in contracts if utc(x['expiry_utc'])>now]
    if len(parsed)<10: raise DataError('Not enough unexpired contracts for the exposure model')
    snap=Snapshot(ticker,float(spot),now,parsed,True,0.)
    result=exposures(snap,settings.risk_free_rate,settings.dividend_yield)
    result.update({'coverage':coverage,'data_status':'SCOPED_MODEL_UNVERIFIED_OI_ASOF','computed_at_utc':now.isoformat(),
                   'source':'WEBULL_OPENAPI','american_option_approximation':True})
    return result


class LiveEngine:
    def __init__(self,ticker,settings=None,*,adapter_factory=None,output_dir=None):
        self.settings=settings or Settings.load();self.ticker=ticker.strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}',self.ticker): raise DataError('Invalid ticker')
        category_for(self.ticker,self.settings.category)
        self.adapter_factory=adapter_factory or (lambda:WebullSDKAdapter(self.settings))
        self.output_dir=Path(output_dir) if output_dir else ROOT/'reports'
        self.lock=threading.RLock();self.stop_event=threading.Event();self.refresh_event=threading.Event()
        self.thread=None;self.budget=RequestBudget(self.settings.max_requests)
        self.events=deque(maxlen=100)
        self.state={'ticker':self.ticker,'version':'1.9','mode':'SANDBOX' if 'sandbox' in self.settings.endpoint else 'PRODUCTION',
                    'endpoint':self.settings.endpoint,'connection':'DISCONNECTED','quote':None,'history':None,'exposure':None,
                    'volatility':{'status':'UNAVAILABLE'},'confirmation':{'status':'UNAVAILABLE'},
                    'quote_error':None,'history_error':None,'chain_error':None,'chain_progress':'Not started','saved_report':None}

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.stop_event.clear();self.thread=threading.Thread(target=self._run,name=f'webull-data-{self.ticker}',daemon=True)
        self.thread.start()

    def stop(self): self.stop_event.set()
    def refresh(self): self.refresh_event.set()
    def alive(self): return bool(self.thread and self.thread.is_alive())

    def snapshot(self):
        with self.lock:
            result=copy.deepcopy(self.state)
            result['events']=list(self.events);result['budget']=self.budget.status();result['running']=self.alive()
            return result

    def _set(self,**kwargs):
        with self.lock: self.state.update(kwargs)

    def _event(self,message):
        with self.lock: self.events.append({'time_utc':datetime.now(UTC).isoformat(),'message':message})

    def _write_report(self):
        report=self.snapshot();report.pop('events',None)
        report['saved_at_utc']=datetime.now(UTC).isoformat()
        exposure=report.get('exposure') or {}
        # Keep the original report fields for downstream V1.8 consumers.
        report.update(exposure);report['quote']=self.snapshot().get('quote')
        try:
            self.output_dir.mkdir(parents=True,exist_ok=True)
            name=f'{self.ticker}_{datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")}.json'
            target=self.output_dir/name;temp=target.with_suffix('.tmp')
            temp.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8');os.replace(temp,target)
            self._set(saved_report=name)
            old=sorted(self.output_dir.glob(f'{self.ticker}_*.json'))[:-1000]
            for f in old: f.unlink(missing_ok=True)
        except (OSError,ValueError,TypeError) as exc: self._event('Report save failed: '+safe_error(exc))

    def _run(self):
        self._set(connection='CONNECTING');self._event('Connecting to official Webull market-data API')
        try: adapter=self.adapter_factory()
        except Exception as exc:
            self._set(connection='SETUP REQUIRED',quote_error=safe_error(exc));self._event(safe_error(exc));return
        self._set(connection='CONNECTED')
        quote_due=history_due=chain_due=0.;collector=None;cache=None;cache_at=0.;cache_spot=None;cache_day=None
        next_chain_step=0.;next_save=0.;previous_vol=None
        while not self.stop_event.is_set():
            now=time.monotonic()
            if self.refresh_event.is_set():
                quote_due=history_due=chain_due=0.;self.refresh_event.clear()
            if now>=quote_due:
                try:
                    q=adapter.fetch_quote(self.ticker,self.budget)
                    self._set(quote=q,quote_error=None,connection='CONNECTED')
                    quote_due=time.monotonic()+self.settings.quote_seconds
                except BudgetPause as exc: quote_due=now+exc.seconds
                except Exception as exc:
                    message=safe_error(exc);self._set(quote_error=message,connection='RETRYING');self._event(message)
                    quote_due=now+max(15,self.settings.quote_seconds)
            if self.stop_event.is_set(): break
            if now>=history_due:
                try:
                    h=adapter.fetch_history(self.ticker,self.budget);five=aggregate_candles(h['candles'],5)
                    try: vol=analyze_volatility(five)
                    except (CandleError,DataError,ValueError) as exc: vol={'status':'UNAVAILABLE','reason':safe_error(exc)}
                    self._set(history=h,history_error=None,volatility=vol,candles_5m=five)
                    history_due=time.monotonic()+self.settings.candle_seconds
                    exp=self.state.get('exposure')
                    if exp:
                        conf=analyze_confirmation(vol,exp,previous_volatility=previous_vol,
                            one_minute_candles=h['candles'][-4:])
                        self._set(confirmation=conf)
                    previous_vol=vol
                except BudgetPause as exc: history_due=now+exc.seconds
                except Exception as exc:
                    message=safe_error(exc);self._set(history_error=message);self._event(message)
                    history_due=now+max(30,self.settings.candle_seconds)
            if self.stop_event.is_set(): break
            q=self.state.get('quote') or {}
            if collector is None and now>=chain_due and q:
                today=datetime.now(UTC).astimezone(ET).date()
                use_cache=cache and now-cache_at<self.settings.discovery_seconds and cache_day==today and abs(q['spot']/cache_spot-1)<self.settings.strike_width/3
                collector=ChainCollector(adapter,self.ticker,q['spot'],cached=cache if use_cache else None)
                self._set(chain_progress=collector.progress)
            if collector is not None and now>=next_chain_step:
                try:
                    collector.step(self.budget);self._set(chain_progress=collector.progress)
                    next_chain_step=time.monotonic()+.05
                    if collector.done:
                        contracts,coverage=collector.result
                        # Model spot and option snapshots are explicitly timestamped separately.
                        exp=model_exposure(contracts,coverage,self.ticker,(self.state.get('quote') or q)['spot'],self.settings)
                        conf=analyze_confirmation(self.state['volatility'],exp,previous_volatility=previous_vol,
                            one_minute_candles=(self.state.get('history') or {}).get('candles',[])[-4:])
                        self._set(exposure=exp,confirmation=conf,chain_error=None,chain_progress='Complete')
                        if collector.pages:
                            cache=collector.contracts;cache_at=time.monotonic();cache_spot=collector.spot;cache_day=today
                        collector=None;chain_due=time.monotonic()+self.settings.chain_seconds
                        self._write_report()
                except BudgetPause as exc:
                    next_chain_step=now+exc.seconds
                    if '429' in str(exc): self._event(str(exc))
                except Exception as exc:
                    message=safe_error(exc);self._set(chain_error=message,chain_progress='Retry scheduled');self._event(message)
                    collector=None;cache=None;chain_due=now+max(60,self.settings.chain_seconds)
            if now>=next_save and self.state.get('history'):
                self._write_report();next_save=now+60
            self.stop_event.wait(.2)
        self._set(connection='STOPPED');self._event('Data polling stopped')
