"""Official Webull OpenAPI market-data adapter. Production data, read-only calls."""
from __future__ import annotations
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from data_utils import DataError, BudgetPause, field, number, records, reported_delay, utc
from market_session import ET, rth_bounds
from settings import Settings

UTC = timezone.utc
ETF_SYMBOLS = {'SPY','QQQ','IWM','DIA','VOO','IVV','SPLG','SPYM','VTI','TLT','GLD','IBIT','TQQQ','SQQQ','SOXL','SOXS'}


def category_for(ticker, configured='AUTO'):
    if ticker in ('SPX','SPXW','NDX','VIX','RUT'):
        raise DataError('Webull stock endpoints have no validated index category here. Use SPY/QQQ/IWM; no index price is fabricated or substituted.')
    return ('US_ETF' if ticker in ETF_SYMBOLS else 'US_STOCK') if configured == 'AUTO' else configured


def _rows(payload, symbol=None):
    return records(payload, symbol)


def _utc(value):
    return utc(value)


def normalize_quote(payload, ticker, *, now=None):
    now = utc(now or datetime.now(UTC))
    rows = records(payload, ticker)
    if len(rows) != 1 or rows[0].get('symbol') != ticker:
        raise DataError('Stock snapshot ticker mismatch')
    row = rows[0]
    price = number(row.get('price'), 'Webull last price', minimum=.000001)
    trade_time = utc(field(row, 'last_trade_time', 'quote_time'))
    quote_time = utc(field(row, 'quote_time', 'last_trade_time'))
    if min((now-trade_time).total_seconds(), (now-quote_time).total_seconds()) < -5:
        raise DataError('Snapshot timestamp is in the future; check the PC clock')
    def optional(key):
        return number(row[key], key, minimum=0) if row.get(key) not in (None,'') else None
    bid, ask = optional('bid'), optional('ask')
    midpoint = (bid+ask)/2 if bid and ask and ask >= bid else None
    return {'ticker':ticker,'spot':price,'last_price':price,'bid':bid,'ask':ask,'midpoint':midpoint,
            'spread':ask-bid if midpoint is not None else None,'price_time_utc':trade_time.isoformat(),
            'quote_time_utc':quote_time.isoformat(),'received_utc':now.isoformat(),
            'delay_minutes':reported_delay(payload,ticker),'pre_close':optional('pre_close'),
            'change':number(row['change'],'price change') if row.get('change') not in (None,'') else None,
            'trade_status':row.get('trade_status'),'source':'WEBULL_OPENAPI'}


@lru_cache(maxsize=64)
def _bounds(day):
    return rth_bounds(day)


def normalize_candles(payload,ticker,*,interval_minutes=1,now=None):
    now = utc(now or datetime.now(UTC))
    parsed=[]
    for row in records(payload,ticker):
        if not isinstance(row,dict): raise DataError('Invalid candle record')
        ts=utc(field(row,'time','timestamp','time_utc'))
        if ts.second or ts.microsecond: raise DataError('Bar timestamp is not a whole minute')
        values={k:number(row.get(k),k,minimum=0) for k in ('open','high','low','close','volume')}
        if min(values[k] for k in ('open','high','low','close')) <= 0 or values['high'] < max(values['open'],values['close'],values['low']) or values['low'] > min(values['open'],values['close'],values['high']):
            raise DataError('Invalid Webull OHLCV values')
        bounds=_bounds(ts.astimezone(ET).date().isoformat())
        if not bounds or ts < bounds[0] or ts+timedelta(minutes=interval_minutes) > bounds[1]: continue
        if ts+timedelta(minutes=interval_minutes) > now: continue
        parsed.append({'time_utc':ts.isoformat(),**values,'closed':True})
    parsed.sort(key=lambda x:x['time_utc'])
    if len({r['time_utc'] for r in parsed}) != len(parsed): raise DataError('Duplicate Webull candles')
    if not parsed: raise DataError('No completed RTH candles returned')
    return {'candles':parsed,'delay_minutes':reported_delay(payload,ticker),'received_utc':now.isoformat(),
            'last_bar_utc':parsed[-1]['time_utc'],'interval_minutes':interval_minutes}


def aggregate_candles(candles,minutes):
    """Align to each session open; reject missing or incomplete buckets."""
    if minutes == 1: return candles
    if minutes not in (3,5,15,60): raise DataError('Unsupported candle aggregation')
    buckets={}
    for bar in candles:
        ts=utc(bar['time_utc']);bounds=_bounds(ts.astimezone(ET).date().isoformat())
        if not bounds: continue
        offset=int((ts-bounds[0]).total_seconds()//60)
        key=bounds[0]+timedelta(minutes=(offset//minutes)*minutes)
        buckets.setdefault(key,[]).append(bar)
    result=[]
    for key,group in sorted(buckets.items()):
        group.sort(key=lambda x:x['time_utc'])
        if len(group)!=minutes or any(utc(row['time_utc'])!=key+timedelta(minutes=i) for i,row in enumerate(group)): continue
        result.append({'time_utc':key.isoformat(),'open':group[0]['open'],'high':max(x['high'] for x in group),
                       'low':min(x['low'] for x in group),'close':group[-1]['close'],
                       'volume':sum(x['volume'] for x in group),'closed':True})
    return result


class WebullSDKAdapter:
    def __init__(self,settings=None,*,data=None):
        self.settings=settings or Settings.load()
        if data is not None: self.data=data;return
        key,secret=os.getenv('WEBULL_APP_KEY'),os.getenv('WEBULL_APP_SECRET')
        if not key or not secret or 'your_' in key.lower() or 'your_' in secret.lower():
            raise DataError('Add your existing WEBULL_APP_KEY and WEBULL_APP_SECRET to the local .env, then reconnect.')
        if os.getenv('WEBULL_REGION','us')!='us': raise DataError('This adapter supports the Webull US region')
        from webull.core.client import ApiClient
        from webull.data.data_client import DataClient
        api=ApiClient(key,secret,'us',connect_timeout=3,timeout=8,auto_retry=False,
                      token_check_duration_seconds=20,token_check_interval_seconds=2)
        api.add_endpoint('us',self.settings.endpoint)
        # SDK defaults can log signed requests. Silence before initialization.
        logger=logging.getLogger('webull.core')
        logger.handlers.clear();logger.addHandler(logging.NullHandler());logger.propagate=False;logger.disabled=True
        api.set_stream_logger(log_level=logging.CRITICAL+1)
        api.set_logger(logger)
        self.data=DataClient(api)

    def request(self,label,method,limiter,**kwargs):
        limiter.acquire()
        response=None
        try:
            response=method(**kwargs);status=getattr(response,'status_code',None)
        except Exception as exc:
            getter=getattr(exc,'get_http_status',None)
            status=getter() if callable(getter) else None
            if status not in (401,403,429):
                from data_utils import safe_error
                raise DataError(safe_error(exc)) from None
        if status==429:
            try: wait=max(5,min(300,float(getattr(response,'headers',{}).get('Retry-After',60))))
            except (ValueError,TypeError): wait=60
            if callable(getattr(limiter,'cooldown',None)): limiter.cooldown(wait)
            raise BudgetPause(wait,f'{label}: Webull HTTP 429; automatic cooldown')
        if status!=200:
            hint='Check API key/secret and token authorization.' if status==401 else 'Check the separate OpenAPI market-data subscription.' if status==403 else 'Check endpoint permissions and SDK version.'
            raise DataError(f'{label}: HTTP {status}. {hint}')
        try: return response.json()
        except (ValueError,TypeError) as exc: raise DataError(f'{label}: invalid JSON') from exc

    def fetch_quote(self,ticker,limiter):
        raw=self.request('Stock snapshot',self.data.market_data.get_snapshot,limiter,symbols=[ticker],
                         category=category_for(ticker,self.settings.category),extend_hour_required=False,overnight_required=False)
        return normalize_quote(raw,ticker)

    def fetch_history(self,ticker,limiter):
        raw=self.request('One-minute candles',self.data.market_data.get_batch_history_bar,limiter,symbols=[ticker],
                         category=category_for(ticker,self.settings.category),timespan='M1',count=str(self.settings.history_count),
                         real_time_required=False,trading_sessions='RTH')
        return normalize_candles(raw,ticker)

    def fetch(self,ticker,limiter):
        return {**self.fetch_quote(ticker,limiter),'data_status':'PRICE_ONLY',
                'reason':'V1.9 dashboard schedules option collection independently.'}

    def fetch_candles(self,ticker,interval,count,limiter):
        minutes={'1m':1,'3m':3,'5m':5,'15m':15,'1h':60}.get(interval)
        if minutes is None: raise DataError('Unsupported candle interval')
        return aggregate_candles(self.fetch_history(ticker,limiter)['candles'],minutes)[-count:]


def create_adapter(): return WebullSDKAdapter()
