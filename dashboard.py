"""Quant Options Agent V1.9 — one dashboard, official Webull data, model forecasts."""
from __future__ import annotations
import html
import json
import os
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from data_utils import DataError,safe_error,utc
from settings import Settings,ROOT
from market_session import ET
from live_engine import LiveEngine,data_quality
from my_webull_adapter import aggregate_candles
from monte_carlo import forecast,walk_forward,ForecastError

st.set_page_config(page_title='Quant | Options Research',page_icon='◈',layout='wide',initial_sidebar_state='expanded')
st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
:root{color-scheme:light}
.stApp,[data-testid="stAppViewContainer"],header[data-testid="stHeader"]{background:#F7F9FB;color:#172B3A}
html,body,[class*="css"],.stApp{font-family:'DM Sans',sans-serif}
.block-container{max-width:1640px;padding-top:1.8rem;padding-bottom:3rem}
h1,h2,h3{font-family:Manrope,sans-serif!important;letter-spacing:-.035em;color:#172B3A}
[data-testid="stSidebar"]{background:#FFF;border-right:1px solid #E3E9ED}
[data-testid="stSidebar"] .block-container{padding-top:1.8rem}
[data-testid="stMetric"]{background:#FFF;border:1px solid #E2E9ED;border-radius:12px;padding:18px 20px;min-height:112px}
[data-testid="stMetricLabel"]{font-size:12px;color:#607482!important}
[data-testid="stMetricValue"]{font-family:Manrope,sans-serif;font-weight:700;font-size:27px;color:#172B3A}
[data-testid="stMetricDelta"]{font-size:12px}
[data-testid="stCaptionContainer"]{color:#647786}
[data-testid="stVerticalBlockBorderWrapper"]{border-color:#E2E9ED}
.stButton button{border-radius:8px;font-weight:600}
button[kind="primary"]{background:#087F76;border-color:#087F76;color:white}
button[data-baseweb="tab"]{font-weight:600;padding:12px 18px;color:#536B79}
button[data-baseweb="tab"][aria-selected="true"]{color:#087F76}
[data-baseweb="tab-highlight"]{background:#087F76}
[data-testid="stAlert"]{border-radius:10px}
.brand{font-family:Manrope,sans-serif;font-size:25px;font-weight:800;letter-spacing:-1px;color:#163545}
.brand span{color:#0E8E82}.eyebrow{font-size:10px;letter-spacing:2px;font-weight:700;color:#708491;margin-bottom:8px}
.page-title{font-family:Manrope,sans-serif;font-size:31px;font-weight:800;letter-spacing:-1.2px;margin:0;color:#173443}
.subtitle{font-size:13px;color:#738592;margin:6px 0 24px}
.pill{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.6px;border-radius:5px;padding:6px 9px;background:#E6F5F0;color:#087C65}
.pill.amber{background:#FFF2DB;color:#976413}.pill.gray{background:#ECF0F3;color:#607480}
.section-label{font-size:12px;font-weight:700;color:#6C8190;text-transform:uppercase;letter-spacing:1.3px;margin:20px 0 8px}
.insight{background:#EBF5F3;border-left:3px solid #119487;border-radius:4px;padding:15px 18px;font-size:13px;color:#254B50;margin:12px 0 20px}
.help{font-size:12px;line-height:1.6;color:#758894}.status-row{display:flex;gap:10px;align-items:center;margin:14px 0 20px}
</style>''',unsafe_allow_html=True)

TEAL='#09887D';RED='#CB6170';INK='#234352';BLUE='#4B78A7'


def fmt(x,digits=2):
    try: return f'{float(x):,.{digits}f}' if x is not None else '—'
    except (TypeError,ValueError): return '—'


def compact(x):
    if x is None: return '—'
    x=float(x)
    for unit,scale in [('B',1e9),('M',1e6),('K',1e3)]:
        if abs(x)>=scale: return f'{x/scale:+.2f}{unit}'
    return f'{x:+,.1f}'


def local_time(value):
    return utc(value).astimezone(ET).strftime('%b %d · %I:%M:%S %p ET') if value else 'Waiting for data'


def chart_style(fig,height=380):
    fig.update_layout(template='plotly_white',height=height,paper_bgcolor='white',plot_bgcolor='white',
        font=dict(family='DM Sans, sans-serif',color=INK,size=12),margin=dict(l=10,r=12,t=26,b=12),
        legend=dict(orientation='h',y=1.11,x=0),hovermode='x unified',
        xaxis=dict(gridcolor='#EFF3F5',zeroline=False),yaxis=dict(gridcolor='#EFF3F5',zeroline=False))
    return fig


@st.cache_resource
def registry():
    return {'engine':None}


@st.cache_data(show_spinner=False,max_entries=6,ttl=30)
def get_forecast(candles,spot,horizons,paths,model,drift,band,levels,asof):
    return forecast(candles,spot,horizons=horizons,paths=paths,model=model,drift_mode=drift,
                    deadband_bps=band,levels=levels,asof=asof)


@st.cache_data(show_spinner=False,max_entries=4,ttl=300)
def validate_model(candles,horizon,model,drift):
    return walk_forward(candles,horizon=horizon,model=model,drift_mode=drift)


try: settings=Settings.load()
except DataError as exc: st.error(str(exc));st.stop()

with st.sidebar:
    st.markdown('<div class="brand">quant<span>.</span></div><div class="eyebrow">OPTIONS RESEARCH / V1.9</div>',unsafe_allow_html=True)
    st.divider()
    with st.form('symbol_form'):
        symbol=st.text_input('Market symbol',value=st.session_state.get('active_symbol',os.getenv('DEFAULT_TICKER','SPY')),max_chars=12).strip().upper()
        apply_symbol=st.form_submit_button('Open market',width='stretch')
    if apply_symbol or 'active_symbol' not in st.session_state: st.session_state['active_symbol']=symbol
    ticker=st.session_state['active_symbol']
    st.caption('US stocks & ETFs · SPY, QQQ, NVDA, AVGO')
    st.markdown('<div class="section-label">Connection</div>',unsafe_allow_html=True)
    st.caption(('Production' if 'sandbox' not in settings.endpoint else 'Sandbox test endpoint')+' · Official Webull OpenAPI')
    connect=st.button('Connect / reconnect Webull',type='primary',width='stretch')
    c1,c2=st.columns(2)
    stop=c1.button('Pause feed',width='stretch')
    refresh=c2.button('Refresh',width='stretch')
    st.caption(f'Price {settings.quote_seconds}s · Candles {settings.candle_seconds}s · Options {settings.chain_seconds}s')
    st.markdown('<div class="section-label">Forecast settings</div>',unsafe_allow_html=True)
    model_label=st.selectbox('Monte Carlo model',['Historical block bootstrap','Lognormal (GBM)'],key='mc_model')
    model='bootstrap' if model_label.startswith('Historical') else 'gbm'
    drift_label=st.selectbox('Direction assumption',['Neutral','Recent trend (experimental)'])
    drift='neutral' if drift_label=='Neutral' else 'recent_trend'
    paths=st.select_slider('Simulated paths',options=[5000,10000,20000],value=10000)
    band=st.number_input('Neutral zone (basis points)',min_value=0.,max_value=100.,value=2.,step=1.,help='2 bps = ±0.02% of current price. Up/down probabilities exclude this zone.')
    with st.expander('Your price levels'):
        upper=st.number_input('Upper level (0 = off)',min_value=0.,value=0.,step=.5)
        lower=st.number_input('Lower level (0 = off)',min_value=0.,value=0.,step=.5)
    st.divider()
    st.caption('Read-only research. No trade client or order controls are included.')
    st.caption('Model ranges are conditional estimates. Directional skill has not been established.')

reg=registry();engine=reg['engine']
if engine and (engine.ticker!=ticker or engine.settings!=settings):
    engine.stop()
    if engine.thread: engine.thread.join(timeout=12)
    if engine.alive(): st.warning('Finishing the previous Webull request. Try again shortly.');st.stop()
    engine=None;reg['engine']=None
if engine is None:
    try: engine=LiveEngine(ticker,settings);reg['engine']=engine
    except DataError as exc: st.error(str(exc));st.stop()
    if os.getenv('WEBULL_APP_KEY') and os.getenv('WEBULL_APP_SECRET'): engine.start()
if connect:
    if engine.alive():
        engine.stop();engine.thread.join(timeout=12)
    if not engine.alive():
        engine=LiveEngine(ticker,settings);reg['engine']=engine;engine.start()
    else: st.warning('Finishing the current Webull request. Reconnect again shortly.')
if stop: engine.stop()
if refresh: engine.refresh()

st.markdown('<div class="eyebrow">MARKET INTELLIGENCE</div><div class="page-title">Options Command Center</div><div class="subtitle">Live market observations. Transparent exposure models. A measured view of what could happen next.</div>',unsafe_allow_html=True)


def price_chart(candles):
    tf=st.segmented_control('Candle timeframe',['1m','3m','5m','15m','1h'],default='5m',key='candle_tf')
    if not candles: st.info('Completed Webull candles appear after connection.');return
    rows=aggregate_candles(candles,{'1m':1,'3m':3,'5m':5,'15m':15,'1h':60}[tf])[-140:]
    if not rows: st.info('Not enough completed candles for this timeframe.');return
    frame=pd.DataFrame(rows);frame['time']=pd.to_datetime(frame['time_utc'],utc=True).dt.tz_convert(ET)
    fig=go.Figure(go.Candlestick(x=frame['time'],open=frame.open,high=frame.high,low=frame.low,close=frame.close,
        increasing_line_color=TEAL,decreasing_line_color=RED,increasing_fillcolor=TEAL,decreasing_fillcolor=RED,name=ticker))
    fig.add_scatter(x=frame['time'],y=frame.close.ewm(span=9,adjust=False).mean(),name='EMA 9',line=dict(color=BLUE,width=1))
    fig.add_scatter(x=frame['time'],y=frame.close.ewm(span=21,adjust=False).mean(),name='EMA 21',line=dict(color='#C59954',width=1))
    chart_style(fig,430);fig.update_layout(xaxis_rangeslider_visible=False)
    st.plotly_chart(fig,width='stretch',key='price_candles')
    st.caption('Actual Webull one-minute OHLCV, grouped from each regular-session open. Incomplete buckets and gaps are omitted. Times shown in New York time.')


def fan_chart(result,horizon):
    rows=[r for r in result['fan'] if r['minute']<=horizon]
    frame=pd.DataFrame(rows);fig=go.Figure()
    for low,high,color,name in [('low_95','high_95','rgba(9,136,125,.08)','95% model range'),('low_68','high_68','rgba(9,136,125,.18)','68% model range')]:
        fig.add_scatter(x=frame.minute,y=frame[high],mode='lines',line=dict(width=0),showlegend=False,hoverinfo='skip')
        fig.add_scatter(x=frame.minute,y=frame[low],mode='lines',line=dict(width=0),fill='tonexty',fillcolor=color,name=name)
    for path in result['sample_paths'][:15]:
        fig.add_scatter(x=list(range(horizon+1)),y=path[:horizon+1],mode='lines',line=dict(width=.6,color='rgba(60,116,136,.13)'),showlegend=False,hoverinfo='skip')
    fig.add_scatter(x=frame.minute,y=frame['median'],name='Median path',line=dict(color=TEAL,width=2.4))
    fig.add_hline(y=result['spot'],line_dash='dot',line_color='#99ABB6')
    chart_style(fig,420);fig.update_xaxes(title='Minutes from forecast origin');fig.update_yaxes(title='Underlying price ($)')
    st.plotly_chart(fig,width='stretch',key='mc_fan')


@st.fragment(run_every='3s')
def main_view():
    state=engine.snapshot();quality=data_quality(state,settings)
    q=state.get('quote') or {};h=state.get('history') or {};exp=state.get('exposure') or {}
    vol=state.get('volatility') or {};conf=state.get('confirmation') or {};coverage=exp.get('coverage') or {}
    candles=h.get('candles',[]);live=quality['forecast_ready']
    pill='pill' if live else 'pill amber'
    st.markdown(f'<div class="status-row"><span class="{pill}">{html.escape(quality["status"])}</span><span class="pill gray">{html.escape(ticker)}</span><span class="pill gray">{quality["session"]["status"]} · ET</span><span class="help">{html.escape(local_time(q.get("received_utc")))}</span></div>',unsafe_allow_html=True)
    if not q:
        st.info('Connect your Webull data feed to begin. Add your existing API key and secret to .env on your computer; the dashboard connects automatically when they are present.')
        if state.get('quote_error'): st.error(state['quote_error'])
    elif not live:
        st.warning('Live forecast paused: '+' · '.join(quality['reasons']))
    cols=st.columns(5)
    cols[0].metric(f'{ticker} · '+quality['price_kind'],('$'+fmt(quality['price'])) if quality['price'] else '—',
                   (fmt(q.get('change'))+' from prior close') if q.get('change') is not None and quality['price_kind']=='Last trade' else None)
    cols[1].metric('Volatility regime',vol.get('status','UNAVAILABLE'))
    cols[2].metric('Modeled Gamma / 1% move',compact(exp.get('net_gex_usd_per_1pct')),help='Calls positive / puts negative OI scenario. Unit: USD per 1% underlying move.')
    cols[3].metric('Vanna · shares / IV point',compact(exp.get('net_vex_delta_shares_per_vol_point')))
    cols[4].metric('Charm · shares / day',compact(exp.get('net_charm_delta_shares_per_day')))
    if exp:
        option_label='fresh, zero-delay option snapshots' if quality['options_live'] else 'historical or unverified option snapshots'
        st.caption(f'Options: {option_label} · {coverage.get("contracts_modeled","?")}/{coverage.get("contracts_discovered","?")} contracts modeled · {coverage.get("dte_days","?")} calendar days · ±{fmt(coverage.get("strike_width_pct"),1)}% strikes. Daily OI as-of and dealer positions are unverified.')
    if state.get('chain_error'): st.caption('Options update: '+state['chain_error'])
    result=None
    if live:
        horizons=tuple(m for m in (5,15,30,60) if m<=quality['session']['minutes_left'])
        levels={}
        if quality['options_live']:
            levels.update({'Call OI wall':exp.get('call_oi_wall'),'Put OI wall':exp.get('put_oi_wall'),'Gamma flip':exp.get('nearest_gamma_flip')})
        if upper: levels['Your upper level']=upper
        if lower: levels['Your lower level']=lower
        try:
            if not horizons: raise ForecastError('Fewer than five minutes remain in the regular session')
            origin=max(q['received_utc'],h['received_utc'])
            result=get_forecast(candles,quality['price'],horizons,paths,model,drift,band,levels,origin)
        except (ForecastError,DataError,ValueError) as exc: st.warning('Forecast unavailable: '+safe_error(exc))
    tab1,tab2,tab3,tab4=st.tabs(['Market overview','Monte Carlo forecast','Gamma · Vanna · Charm','Data health & export'])
    with tab1:
        left,right=st.columns([2.5,1])
        with left:
            with st.container(border=True):
                st.markdown('**Price action**');price_chart(candles)
        with right:
            with st.container(border=True):
                st.markdown('**Next 15 minutes**')
                if result:
                    row=next((r for r in result['horizons'] if r['minutes']==15),result['horizons'][0])
                    st.metric(f'{row["minutes"]}-minute 68% range',f'${fmt(row["low_68"])} – ${fmt(row["high_68"])}')
                    st.write(f'Up **{row["p_up"]:.1%}** · Down **{row["p_down"]:.1%}** · Neutral **{row["p_neutral"]:.1%}**')
                    st.caption(f'Neutral zone: ±{band:g} bps. Model probabilities; directional accuracy unproven.')
                    st.caption('Open Monte Carlo forecast for the full distribution.')
                else: st.caption('The model starts when fresh, zero-delay stock data and completed one-minute candles are available during the regular session.')
            with st.container(border=True):
                st.markdown('**Market context**')
                st.metric('ATR ratio',fmt(vol.get('atr_ratio'))+'×')
                st.metric('Relative volume',fmt(vol.get('volume_ratio'))+'×')
                st.metric('BB width percentile',fmt(vol.get('bb_width_percentile'),1)+'%')
            with st.expander('How to read the dashboard'):
                st.write('Price and candles are market observations. Gamma, Vanna and Charm are option-pricing scenarios based on observed IV and open interest. Monte Carlo probabilities describe the selected model; they are not buy/sell instructions or win rates.')
    with tab2:
        st.markdown('**Possible outcomes for the next move**')
        st.caption('Fitted to completed Webull one-minute returns. Overnight returns and missing-minute returns are excluded. Forecast horizons stop at today’s regular-session close.')
        if result:
            horizon=st.selectbox('Forecast horizon (minutes)',[r['minutes'] for r in result['horizons']],index=min(1,len(result['horizons'])-1),key='forecast_horizon')
            row=next(r for r in result['horizons'] if r['minutes']==horizon)
            a,b,c,d=st.columns(4)
            a.metric('Up beyond neutral zone',f'{row["p_up"]:.1%}');b.metric('Down beyond neutral zone',f'{row["p_down"]:.1%}')
            c.metric('Inside neutral zone',f'{row["p_neutral"]:.1%}');d.metric('Median end price','$'+fmt(row['median']))
            st.markdown('<div class="insight">'+('Neutral direction assumption: this estimates the size and range of possible moves. It does not establish which direction is more likely in the real market.' if drift=='neutral' else 'Experimental trend scenario: a small part of the recent observed drift is projected forward. The displayed probabilities are not calibrated directional predictions.')+'</div>',unsafe_allow_html=True)
            with st.container(border=True): fan_chart(result,horizon)
            table=[{'Horizon':f'{r["minutes"]} min','Up':f'{r["p_up"]:.1%}','Down':f'{r["p_down"]:.1%}',
                    'Neutral':f'{r["p_neutral"]:.1%}','Median':fmt(r['median']),
                    '68% range':fmt(r['low_68'])+' – '+fmt(r['high_68']),'95% range':fmt(r['low_95'])+' – '+fmt(r['high_95'])} for r in result['horizons']]
            st.dataframe(pd.DataFrame(table),hide_index=True,width='stretch')
            if row['levels']:
                st.markdown('**Chance of reaching a level within this horizon**')
                st.dataframe(pd.DataFrame([{'Level':r['name'],'Price':r['price'],'Touch probability':f'{r["touch_probability"]:.1%}'} for r in row['levels']]),hide_index=True,width='stretch')
                st.caption('Measured at one-minute checkpoints, including the origin. Intraminute touches can be missed; an OI wall is not proven support or resistance.')
            st.caption(f'{result["paths"]:,} paths · {result["training_returns"]} valid returns · Random seed {result["seed"]} · Monte Carlo sampling error up to ±{row["max_mc_standard_error_pct"]:.2f} percentage points (one standard error). This excludes model error.')
            with st.expander('Model assumptions and limitations'):
                for note in result['notes']: st.write('• '+note)
                st.write('The fan shows marginal price quantiles at each minute, not a confidence band for an entire price path. No news or observed dealer flow is included. More paths reduce simulation noise, not model risk.')
            if st.button('Check recent out-of-sample calibration',key='run_validation'):
                with st.spinner('Replaying forecasts using only data available at each historical origin…'):
                    st.session_state['validation_result']={**validate_model(candles,horizon,model,drift),'ticker':ticker,'last_bar_utc':candles[-1]['time_utc']}
            validation=st.session_state.get('validation_result')
            if validation and validation.get('ticker')==ticker and validation['model']==model and validation['drift_mode']==drift and validation['horizon']==horizon:
                st.markdown('**Recent rolling validation**')
                a,b,c,d=st.columns(4)
                a.metric('Forecasts tested',validation['forecasts']);b.metric('68% range coverage',f'{validation["coverage_68"]:.1%}' if validation['coverage_68'] is not None else '—')
                c.metric('95% range coverage',f'{validation["coverage_95"]:.1%}' if validation['coverage_95'] is not None else '—')
                d.metric('Brier score',fmt(validation['brier_score'],3),help='Lower is better. A 50/50 baseline scores 0.250 on non-flat binary outcomes.')
                st.caption('Validation through '+local_time(validation.get('last_bar_utc'))+'. '+validation['note']+' 50/50 Brier baseline = 0.250. Flats excluded from Brier, not range coverage.')
            st.download_button('Export forecast JSON',json.dumps(result,indent=2,allow_nan=False),file_name=f'{ticker}_monte_carlo.json',mime='application/json',key='download_forecast')
        else: st.info('Forecast is paused until its live-data checks pass. The price and options panels remain available for inspection.')
    with tab3:
        st.markdown('**Modeled options exposure**')
        st.caption('Gamma, Vanna and Charm use dividend-adjusted Black–Scholes. American stock/ETF options are approximated. Calls-positive / puts-negative is a positioning scenario, not measured dealer inventory.')
        if exp.get('by_strike'):
            metric=st.segmented_control('Exposure by strike',['Gamma','Vanna','Charm','Delta'],default='Gamma',key='exposure_metric')
            col={'Gamma':'gex','Vanna':'vex','Charm':'charm','Delta':'delta'}[metric]
            units={'Gamma':'USD per 1% spot move','Vanna':'Delta shares per +1 IV point','Charm':'Delta shares per elapsed calendar day','Delta':'Underlying share equivalents'}[metric]
            left,right=st.columns([2.5,1]);frame=pd.DataFrame(exp['by_strike']).sort_values('strike')
            with left:
                fig=go.Figure(go.Bar(x=frame.strike,y=frame[col],marker_color=[TEAL if v>=0 else RED for v in frame[col]],name=metric))
                fig.add_vline(x=exp['spot'],line_color=INK,line_dash='dash',annotation_text='Model spot')
                if metric=='Gamma' and exp.get('nearest_gamma_flip') is not None: fig.add_vline(x=exp['nearest_gamma_flip'],line_color='#C59954',line_dash='dot',annotation_text='Modeled flip')
                chart_style(fig,420);fig.update_layout(yaxis_title=units,xaxis_title='Option strike')
                st.plotly_chart(fig,width='stretch',key='exposure_strikes')
            with right:
                st.metric('Call open-interest wall',fmt(exp.get('call_oi_wall')));st.metric('Put open-interest wall',fmt(exp.get('put_oi_wall')))
                st.metric('Nearest modeled Gamma flip',fmt(exp.get('nearest_gamma_flip')));st.metric('Modeled Delta shares',compact(exp.get('net_delta_proxy_shares')))
            a,b=st.columns(2)
            a.markdown('**Vanna scenario**');a.write('For a +1 percentage-point IV change, the signed delta exposure changes by approximately '+compact(exp.get('net_vex_delta_shares_per_vol_point'))+' shares, holding other inputs fixed.')
            b.markdown('**Charm scenario**');b.write('For one elapsed calendar day, the local signed delta sensitivity is '+compact(exp.get('net_charm_delta_shares_per_day'))+' shares/day. Near expiry this is a local derivative, not a full-day projection.')
            st.caption('These are changes in the hypothetical option delta. The counter-hedge would have the opposite sign. Actual hedging demand is unknown.')
            with st.expander('Scope, exclusions and timestamps'): st.json(coverage)
        else: st.info('Options: '+state.get('chain_progress','Waiting for collection'))
    with tab4:
        st.markdown('**Connection and data provenance**')
        rows=[('Endpoint',state['endpoint']),('Worker',state['connection']),('Stock price time',local_time(quality['price_time_utc'])),
              ('Quote age (seconds)',fmt(quality['quote_age_seconds'],1)),('Price age (seconds)',fmt(quality['price_age_seconds'],1)),
              ('Server-reported stock delay',fmt(quality['delay_minutes'],0)+' min' if quality['delay_minutes'] is not None else 'Unverified'),
              ('Delay evidence',quality['delay_source']),('Last completed 1m candle',local_time(h.get('last_bar_utc'))),
              ('Candle count',len(candles)),('Oldest option quote age',fmt(quality['option_age_seconds'],1)+' sec'),
              ('Requests in last minute',f'{state["budget"]["requests_last_minute"]} / {state["budget"]["limit"]}'),
              ('Cooldown',fmt(state['budget']['cooldown_seconds'],1)+' sec'),('Option collection',state.get('chain_progress'))]
        st.dataframe(pd.DataFrame([(a,str(b)) for a,b in rows],columns=['Check','Observed value']),hide_index=True,width='stretch')
        for key in ('quote_error','history_error','chain_error'):
            if state.get(key): st.warning(key.replace('_',' ').title()+': '+state[key])
        if quality['reasons']:
            with st.expander('Forecast readiness checks',expanded=True):
                for reason in quality['reasons']: st.write('• '+reason)
        with st.expander('Connection setup'):
            st.write('Copy your existing Webull key and secret into .env, set WEBULL_ENDPOINT=api.webull.com, and click Connect / reconnect. Keys remain on your computer. Webull Desktop subscriptions do not automatically provide OpenAPI data permissions.')
            st.write('A zero-delay report from the candle response can establish stock-feed entitlement for the same ticker when its snapshot omits the field. Missing metadata stays unverified; a fresh timestamp alone is not proof of a real-time entitlement.')
        if candles:
            st.download_button('Export Webull 1m candles CSV',pd.DataFrame(candles).to_csv(index=False),file_name=f'{ticker}_webull_1m.csv',mime='text/csv',key='download_candles')
        st.download_button('Export data diagnostics JSON',json.dumps(state,indent=2,allow_nan=False),file_name=f'{ticker}_data_diagnostics.json',mime='application/json',key='download_diagnostics')
        if state['events']:
            with st.expander('Recent events'): st.dataframe(pd.DataFrame(list(reversed(state['events']))),hide_index=True,width='stretch')
    st.caption('Quant Options Agent 1.9 · Webull observations + explicitly labeled models · No order execution')

main_view()
