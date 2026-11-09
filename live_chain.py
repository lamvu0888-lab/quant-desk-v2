"""Incremental scoped option discovery. start_date is EXACT expiry; end_date is
an expiry LOWER bound, so never pass them as a minimum/maximum date window.
Each step makes one request, allowing quote updates between option batches.
"""
from __future__ import annotations
import re
from datetime import datetime,timedelta,timezone
from data_utils import DataError,field,number,reported_delay,utc
from market_session import ET,rth_bounds
from settings import Settings

UTC=timezone.utc
OCC=re.compile(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$')


def unwrap(payload):
    if isinstance(payload,list): return payload,None
    if not isinstance(payload,dict): raise DataError('Unknown option response type')
    cursor=field(payload,'pagination_key','paginationKey','next_page_key','nextPageKey')
    if payload.get('has_more') is True and not cursor:
        raise DataError('Option response reports more pages but omits its pagination cursor')
    for key in ('list','items','contracts','rows','data','result'):
        if isinstance(payload.get(key),list): return payload[key],cursor
        if isinstance(payload.get(key),dict):
            rows,inner=unwrap(payload[key]);return rows,cursor or inner
    raise DataError('Unknown option list response schema')


def occ_info(symbol):
    match=OCC.fullmatch(str(symbol).replace(' ',''))
    if not match: raise DataError('Unrecognized OCC option symbol')
    root,date,right,strike=match.groups()
    return root,datetime.strptime(date,'%y%m%d').date(),right,int(strike)/1000


def contract_symbol(row):
    return field(row,'symbol','option_symbol','optionSymbol','ticker','option_code','optionCode')


class ChainCollector:
    def __init__(self,adapter,ticker,spot,*,cached=None,now=None):
        self.adapter,self.ticker,self.spot=adapter,ticker,float(spot)
        self.settings=adapter.settings
        today=utc(now or datetime.now(UTC)).astimezone(ET).date()
        self.dates=[(today+timedelta(days=i)).isoformat() for i in range(self.settings.dte_days+1)
                    if rth_bounds((today+timedelta(days=i)).isoformat())]
        self.root=self.settings.root_symbol or ticker
        self.contracts=dict(cached or {})
        self.phase='SNAPSHOTS' if cached else 'DISCOVERY'
        self.date_index=0;self.cursor=None;self.cursors=set();self.pages=0
        self.snapshots={};self.offset=0;self.done=False;self.result=None

    @property
    def progress(self):
        return f'{self.phase}: {len(self.contracts)} contracts / {len(self.snapshots)} snapshots'

    def step(self,limiter):
        if self.done: return
        if self.phase=='DISCOVERY': self._discover(limiter)
        elif self.phase=='SNAPSHOTS': self._snapshots(limiter)

    def _discover(self,limiter):
        if self.date_index>=len(self.dates):
            if not self.contracts: raise DataError('No standard contracts in the requested expiry/strike window')
            self.phase='SNAPSHOTS';return
        if self.pages>=self.settings.max_pages:
            raise DataError('Option page cap reached; narrow DTE/strike window. Incomplete chain not published.')
        expiry=self.dates[self.date_index]
        raw=self.adapter.request('Option discovery',self.adapter.data.instrument.list_option_contracts,limiter,
            category='US_OPTION',underlying_symbols=self.ticker,status='LISTING',start_date=expiry,root_symbol=self.root,
            strike_price_gte=str(round(self.spot*(1-self.settings.strike_width),3)),
            strike_price_lte=str(round(self.spot*(1+self.settings.strike_width),3)),pagination_key=self.cursor)
        rows,nxt=unwrap(raw);self.pages+=1
        for row in rows:
            if not isinstance(row,dict): raise DataError('Invalid option contract record')
            sym=contract_symbol(row);root,exp,_,strike=occ_info(sym)
            if root!=self.root or exp.isoformat()!=expiry or not self.spot*(1-self.settings.strike_width)<=strike<=self.spot*(1+self.settings.strike_width):
                raise DataError('Option discovery ignored expiry/root/strike filters')
            if sym in self.contracts: raise DataError('Repeated option contract across discovery pages')
            self.contracts[sym]=row
        if len(self.contracts)>self.settings.max_contracts:
            raise DataError('Option contract cap reached; narrow CHAIN_DTE_DAYS or CHAIN_STRIKE_WIDTH_PCT')
        if nxt:
            key=(expiry,str(nxt))
            if not rows or key in self.cursors or nxt==self.cursor: raise DataError('Invalid/repeated option pagination cursor')
            self.cursors.add(key);self.cursor=nxt
        else: self.date_index+=1;self.cursor=None

    def _snapshots(self,limiter):
        symbols=sorted(self.contracts)
        if self.offset>=len(symbols): self.result=self._finish();self.done=True;return
        batch=symbols[self.offset:self.offset+20]
        raw=self.adapter.request('Option snapshots',self.adapter.data.option_market_data.get_option_snapshot,limiter,
                                 symbols=','.join(batch),category='US_OPTION')
        rows,_=unwrap(raw);found={}
        for row in rows:
            sym=contract_symbol(row)
            if sym not in batch or sym in found: raise DataError('Unexpected or duplicate option snapshot')
            found[sym]=dict(row)
            if found[sym].get('delay_minutes') is None:
                delay=reported_delay(raw,sym)
                if delay is not None: found[sym]['delay_minutes']=delay
        if set(found)!=set(batch): raise DataError('Missing option snapshots; incomplete collection not published')
        self.snapshots.update(found);self.offset+=len(batch)

    def _finish(self):
        out,excluded,times,delays=[],{},[],[];now=datetime.now(UTC)
        for sym,meta in sorted(self.contracts.items()):
            _,exp,right,strike=occ_info(sym);bounds=rth_bounds(exp.isoformat())
            if not bounds or bounds[1]<=now: excluded['expired']=excluded.get('expired',0)+1;continue
            snap=self.snapshots[sym]
            try:
                iv=number(field(snap,'imp_vol','implied_volatility','iv'),'IV',minimum=.000001)
                oi=number(field(snap,'open_interest','oi'),'open interest',minimum=0)
                mult=number(field(meta,'multiplier','contract_multiplier'),'contract multiplier',minimum=.000001)
                stamp=utc(field(snap,'quote_time','last_trade_time'))
                if iv>5: raise DataError('IV units must be decimal')
                if (now-stamp).total_seconds() < -5: raise DataError('Option timestamp is in the future')
            except DataError:
                key='missing_or_invalid_iv_oi_multiplier_timestamp';excluded[key]=excluded.get(key,0)+1;continue
            times.append(stamp);delays.append(reported_delay(snap,sym))
            out.append({'expiry_utc':bounds[1].isoformat(),'strike':strike,'right':right,'open_interest':oi,
                        'iv_decimal':iv,'multiplier':mult,'symbol':sym,'quote_time_utc':stamp.isoformat(),
                        'bid':snap.get('bid'),'ask':snap.get('ask'),'webull_gamma':snap.get('gamma'),'webull_delta':snap.get('delta')})
        if len(out)<10 or {r['right'] for r in out}!={'C','P'}:
            raise DataError('Too few valid calls/puts: missing IV/OI/multiplier or timestamps are not replaced with zeros')
        if not any(r['strike']<self.spot for r in out) or not any(r['strike']>self.spot for r in out):
            raise DataError('Usable option window does not span spot')
        coverage={'scope':'SCOPED_WINDOW','dte_days':self.settings.dte_days,'strike_width_pct':self.settings.strike_width*100,
                  'root':self.root,'contracts_discovered':len(self.contracts),'contracts_modeled':len(out),'excluded':excluded,
                  'pages_fetched':self.pages,'oldest_option_quote_utc':min(times).isoformat(),
                  'newest_option_quote_utc':max(times).isoformat(),
                  'option_delay_minutes':max(delays) if delays and all(d is not None for d in delays) else None,
                  'oi_timestamp_verified':False,'computed_at_utc':now.isoformat(),
                  'note':'Bounded, fully paginated window; missing inputs explicitly excluded. Daily OI as-of and actual dealer inventory are unknown.'}
        return out,coverage


def fetch_scoped_chain(data,ticker,spot,limiter):
    from my_webull_adapter import WebullSDKAdapter
    collector=ChainCollector(WebullSDKAdapter(Settings.load(),data=data),ticker,spot)
    while not collector.done: collector.step(limiter)
    return collector.result
