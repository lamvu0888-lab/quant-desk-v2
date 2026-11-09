"""Render the full UI with injected observations; never touch a brokerage API."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import copy
import pytest
from streamlit.testing.v1 import AppTest
from my_webull_adapter import normalize_quote
from settings import Settings
import live_engine


def test_dashboard_renders_when_disconnected(monkeypatch):
    monkeypatch.delenv('WEBULL_APP_KEY',raising=False);monkeypatch.delenv('WEBULL_APP_SECRET',raising=False)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'dashboard.py'),default_timeout=30).run()
    assert not app.exception
    assert len(app.tabs)==4
    assert any('Connect your Webull' in i.value for i in app.info)


def test_full_live_forecast_ui_with_injected_data(monkeypatch,candles):
    from volatility import analyze_volatility
    from my_webull_adapter import aggregate_candles
    from quant_agent import demo,validate,exposures
    now=datetime(2026,11,3,17,tzinfo=timezone.utc)
    bars=[b for b in candles if b['time_utc']<now.isoformat()]
    q=normalize_quote([{'symbol':'SPY','price':bars[-1]['close'],'last_trade_time':now.isoformat(),
                       'quote_time':now.isoformat(),'delay_minutes':0}],'SPY',now=now)
    exp=exposures(validate(demo('SPY'),'SPY',90,10,1))
    exp['coverage']={'contracts_modeled':16,'contracts_discovered':16,'dte_days':7,'strike_width_pct':2,
                     'oldest_option_quote_utc':now.isoformat(),'option_delay_minutes':0}
    state={'ticker':'SPY','version':'1.9','endpoint':'api.webull.com','mode':'PRODUCTION','running':True,
           'connection':'CONNECTED','quote':q,'history':{'candles':bars,'last_bar_utc':bars[-1]['time_utc'],
                       'received_utc':now.isoformat(),'delay_minutes':0},'exposure':exp,
           'volatility':analyze_volatility(aggregate_candles(bars,5)),'confirmation':{},
           'budget':{'requests_last_minute':10,'limit':45,'cooldown_seconds':0},'chain_progress':'Complete','events':[]}
    class InjectedEngine:
        def __init__(self,ticker,settings): self.ticker=ticker;self.settings=settings;self.thread=None
        def snapshot(self): return copy.deepcopy(state)
        def alive(self): return False
        def start(self): pass
        def stop(self): pass
        def refresh(self): pass
    original_quality=live_engine.data_quality
    monkeypatch.setattr(live_engine,'LiveEngine',InjectedEngine)
    monkeypatch.setattr(live_engine,'data_quality',lambda s,c:original_quality(s,c,now=now))
    monkeypatch.delenv('WEBULL_APP_KEY',raising=False);monkeypatch.delenv('WEBULL_APP_SECRET',raising=False)
    import streamlit as st
    st.cache_resource.clear()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'dashboard.py'),default_timeout=30).run()
    assert not app.exception
    assert any('Median end price'==m.label for m in app.metric)
    assert len(app.get('plotly_chart'))==3
    assert any('LIVE' in m.value for m in app.markdown)
    app.selectbox(key='mc_model').set_value('Lognormal (GBM)').run()
    assert not app.exception
    app.button(key='run_validation').click().run()
    assert not app.exception
    assert any(m.label=='Forecasts tested' for m in app.metric)
    st.cache_resource.clear()
