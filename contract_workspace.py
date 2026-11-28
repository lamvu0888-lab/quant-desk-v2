"""Decision-oriented contract scenarios using the desk's verified quotes and listed series."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from iv_decision import iv_value
from market_session import ET, session_state
from option_quotes import option_rows, quote_status, spread_fraction
from terminal_data import et_time, first_listed_dte, listed_dtes, numeric, positive, time_or_none
from terminal_style import kpis, style_chart
from v3_movement import enabled
from v3_provenance import live_record, simulated_feed
from v3_options import (UNITS, YEAR, block_simulation, calibrated_iv, contract_spec, greeks,
                        scenario, skew_scenario, timeline, value)


def number(val, digits=2, *, percent=False, dollar=False):
    val=numeric(val)
    if val is None:
        return "Unavailable"
    if percent:
        return f"{val:.{digits}%}"
    return ("$" if dollar else "")+f"{val:,.{digits}f}"


def format_strike(strike):
    return f"{strike:,.3f}".rstrip("0").rstrip(".")


def contract_label(spec, ticker, today):
    dte=(spec.expires.astimezone(ET).date()-today).days
    return f"{ticker} {format_strike(spec.strike)} {spec.right.title()} · {dte}DTE"


def auto_scenario(plan, spot):
    """Choose a scenario from current evidence; eligibility and trade execution stay independent."""
    sig=plan.get("movement_v3") or {}
    direction=plan.get("direction")
    tp1=positive(plan.get("tp1"))
    if (tp1 and direction in (-1,1) and direction*(tp1-spot)>0 and plan.get("confirmed") and
            plan.get("status") in ("ENTER LONG","ENTER SHORT","GET READY") and
            plan.get("price_context_ready") and not plan.get("blocks") and not plan.get("data_reasons")):
        hold=positive(plan.get("hold")) or 5
        model=plan.get("model") or {}
        if (str(plan.get("validation","")).startswith("Prospectively evaluated") and
                positive(model.get("horizon")) and model.get("p_tp1") is not None):
            return tp1,model["horizon"],"Trade Plan TP1 · evaluated Monte Carlo horizon"
        return tp1,hold,"Trade Plan TP1 · planned holding window"
    if sig.get("state","").endswith(("PRESSURE","IGNITION","EXPANSION")) and sig.get("state")!="CONFLICTING PRESSURE":
        side=sig.get("side")
        barrier=sig.get("forecasts",{}).get("5",{})
        target=positive(barrier.get("upper" if side==1 else "lower"))
        if side in (-1,1) and target and side*(target-spot)>0:
            return target,5,"V3 directional barrier · five-minute scenario"
    return spot,5,"Flat underlying · no qualifying directional target"


def listed_chain(ctx, feed, expiry, side, spot):
    if not ctx.preview and simulated_feed(feed):
        return []
    latest={}
    for row in option_rows(feed,ctx.ticker,expiry):
        if row.get("type")!=side:
            continue
        try:
            spec=contract_spec(row,ctx.ticker)
        except (TypeError,ValueError):
            continue
        if min(spec.expires,spec.last_trade)<=ctx.now:
            continue
        status=quote_status(row,feed,ctx.now,paused=ctx.paused,max_age=ctx.option_max_age)
        at=time_or_none(row.get("updated_at"))
        previous=latest.get(spec.code)
        if previous and previous[0] is not None and (at is None or at<previous[0]):
            continue
        latest[spec.code]=(at,(spec,row,status))
    rows=[entry for _,entry in latest.values()]
    rows.sort(key=lambda x:(abs(x[0].strike-spot),x[0].strike,x[0].code))
    return rows


def moneyness(strike, side, spot, atm):
    if strike==atm:
        return "ATM"
    return "ITM" if (spot-strike)*(1 if side=="call" else -1)>0 else "OTM"


def chain_selection(rows, ctx, spot, expiry, side):
    prefix=f"opc_{ctx.ticker}_{expiry}_{side}"
    atm=rows[0][0].strike
    with st.expander("Filter strikes"):
        mny=st.segmented_control("Moneyness",["All","ATM","ITM","OTM"],default="All",key=prefix+"_money") or "All"
        sort=st.selectbox("Sort strikes",["Nearest price","Tightest spread","Highest volume","Lowest ask","Highest absolute Delta"],key=prefix+"_sort")
        bounds=st.columns(3)
        max_ask=bounds[0].number_input("Maximum ask ($)",min_value=.01,value=None,key=prefix+"_ask")
        delta=bounds[1].number_input("Minimum |Delta|",min_value=0.,max_value=1.,value=None,key=prefix+"_delta")
        max_width=bounds[2].number_input("Maximum spread (%)",min_value=0.,value=None,key=prefix+"_spread")
        fresh=st.checkbox("Fresh quotes only",key=prefix+"_fresh")
    def order(entry):
        spec,row,_=entry
        if sort=="Tightest spread":
            val=spread_fraction(row)
        elif sort=="Highest volume":
            volume=numeric(row.get("volume"));val=-volume if volume is not None else None
        elif sort=="Lowest ask":
            val=positive(row.get("ask"))
        elif sort=="Highest absolute Delta":
            d=numeric(row.get("delta"));val=-abs(d) if d is not None else None
        else:
            val=abs(spec.strike-spot)
        return val is None,val or 0.,abs(spec.strike-spot),spec.code
    filtered=[]
    for entry in rows:
        spec,row,status=entry
        category=moneyness(spec.strike,side,spot,atm)
        ask=positive(row.get("ask")); width=spread_fraction(row); d=numeric(row.get("delta"))
        if mny!="All" and category!=mny or fresh and status!="Fresh quote":
            continue
        if max_ask is not None and (ask is None or ask>max_ask):
            continue
        if delta is not None and (d is None or abs(d)<delta):
            continue
        if max_width is not None and (width is None or width*100>max_width):
            continue
        filtered.append(entry)
    if not filtered:
        st.info("No listed strike matches these filters.")
        return None
    filtered.sort(key=order)
    counts=Counter(entry[0].strike for entry in rows)
    def strike_label(spec):
        label=format_strike(spec.strike)
        if counts[spec.strike]>1:
            root=spec.code.split(next(c for c in spec.code if c.isdigit()),1)[0]
            label+=f" · {spec.settlement.split(' · ')[0]} ({root})"
        return label
    labels=[{"Strike":strike_label(spec),"Type":moneyness(spec.strike,side,spot,atm),
             "Bid":number(row.get("bid"),dollar=True),"Ask":number(row.get("ask"),dollar=True)} for spec,row,_ in filtered]
    # A new list identity prevents a stale row index picking another contract after quotes reorder.
    digest=sha256("|".join(v[0].code for v in filtered).encode()).hexdigest()[:12]
    selkey=prefix+"_chosen"
    selected=st.session_state.get(selkey)
    idx=next((i for i,e in enumerate(filtered) if e[0].code==selected),0)
    table=st.dataframe(labels,hide_index=True,width="stretch",row_height=28,
                       height=min(5,len(filtered))*28+35,key=prefix+"_chain_"+digest,
                       on_select="rerun",selection_mode="single-row-required",selection_default={"selection":{"rows":[idx]}})
    picked=table.selection.rows
    idx=picked[0] if picked and picked[0]<len(filtered) else idx
    st.session_state[selkey]=filtered[idx][0].code
    return filtered[idx]


def financial_defaults(ctx):
    from paper_engine import FEE
    from settings import Settings
    settings=getattr(ctx.webull_store,"settings",None) or Settings()
    def setting(name,fallback,low,high):
        value=numeric(os.getenv(name))
        return value if value is not None and low<=value<=high else fallback
    return (setting("RISK_FREE_RATE",settings.risk_free_rate,-.1,1),
            setting("DIVIDEND_YIELD",settings.dividend_yield,0.,1),
            setting("QD_OPTION_FEE_PER_SIDE",FEE,0.,100.))


def advanced(ctx,spec,ask,chain_rate):
    fallback_rate,fallback_div,fallback_fee=financial_defaults(ctx)
    if chain_rate is not None:
        fallback_rate=chain_rate
    st.caption(f"Interest rate uses {'the current Schwab chain' if chain_rate is not None else 'the desk carry assumption'}. Dividend yield uses the desk carry assumption. Fees use the configured per-side charge or paper ledger default.")
    a,b,c=st.columns(3)
    rate=a.number_input("Annual rate (%)",-10.,100.,100*fallback_rate,key=f"opc_rate_{fallback_rate}")/100
    dividend=b.number_input("Annual dividend yield (%)",0.,100.,100*fallback_div,key="opc_dividend")/100
    fee=c.number_input("Fee / contract / side ($)",0.,100.,fallback_fee,key="opc_fee")
    a,b,c=st.columns(3)
    qty=a.number_input("Contracts",1,2,1,key="opc_qty")
    shock=b.number_input("Extra IV change (points)",-95.,200.,0.,key="opc_shock")/100
    use_skew=c.toggle("Apply observed strike skew",value=True,key="opc_skew")
    custom_entry=st.checkbox("Override current ask",key="opc_override_"+spec.code)
    entry=st.number_input("Entry premium ($)",min_value=.01,value=float(ask),key="opc_entry_"+spec.code) if custom_entry else ask
    a,b,c=st.columns(3)
    step=a.selectbox("Chart interval (minutes)",[0,1,3,5,15,30,60,240,1440],format_func=lambda x:str(x) if x else "Auto",key="opc_interval")
    norm=b.toggle("Normalize chart",key="opc_norm")
    rth=c.toggle("Regular sessions only",key="opc_sessions")
    extra=st.multiselect("Additional chart overlays",["underlying","theta","vega","vanna","charm"],key="opc_overlays")
    path=[]
    if st.toggle("Custom intermediate price path",key="opc_custom"):
        knots=st.text_input("Elapsed minutes, underlying price",placeholder="2,701.50; 4,702.00",key="opc_path_"+spec.code)
        for pair in knots.split(";") if knots.strip() else []:
            try:
                minute,price=map(float,pair.split(","))
                if not np.isfinite([minute,price]).all():
                    raise ValueError
            except ValueError:
                raise ValueError("Enter elapsed minutes and price as pairs, separated by semicolons.") from None
            path.append((minute*60,price))
    return rate,dividend,fee,qty,shock,use_skew,entry,step,norm,rth,extra,path


def contracts(ctx):
    from contract_sources import contract_chain,contract_expirations
    from decision_ui import dashboard_decision
    from terminal_views import read_workspace
    from paper_engine import MAX_DEBIT
    from v3_views import combined,compare_contracts,chart_history
    if not enabled("OPTIONS",True):
        st.info("Contract Intelligence is disabled.")
        return
    if ctx.ticker not in ("SPX","SPY","QQQ"):
        st.info("Select SPX, SPY or QQQ to analyze listed option contracts.")
        return
    listing=contract_expirations(ctx)
    if (not ctx.preview and simulated_feed(listing)) or first_listed_dte(listing,ctx.ticker,ctx.session,ctx.now) is None:
        st.info("Waiting for a current listing for this ticker.")
        return
    today=ctx.now.astimezone(ET).date()
    dates=[ctx.session+timedelta(days=n) for n in listed_dtes(listing,ctx.session) if ctx.session+timedelta(days=n)>=today]
    if not dates:
        st.info("No current listed expirations.")
        return
    quote=ctx.chart_quote
    if not quote:
        st.info("Waiting for a fresh regular-session underlying quote.")
        return
    spot=quote["price"]
    a,b=st.columns(2)
    exp=a.selectbox("Expiration",dates,format_func=lambda d:f"{(d-today).days}DTE · {d:%b %d, %Y}",key="opc_exp_"+ctx.ticker)
    side=b.segmented_control("Side",["call","put"],default="call",format_func=str.title,key="opc_side") or "call"
    feed,source=contract_chain(ctx,exp.isoformat(),side)
    rows=listed_chain(ctx,feed,exp.isoformat(),side,spot)
    if not rows:
        st.info(f"{source} · no verified {side} series is available for {exp:%b %d}. Check quote access in Data Health.")
        return
    selection=chain_selection(rows,ctx,spot,exp,side)
    if selection is None:
        return
    spec,row,status=selection
    code=spec.code
    st.markdown(f"**{contract_label(spec,ctx.ticker,today)}**")
    source_label="Synthetic preview" if ctx.preview else source
    stamp=et_time(row.get("updated_at"))
    fallback=" · fallback after Webull" if not ctx.preview and source!="Webull" and ctx.has_webull_options else ""
    if not ctx.preview and source=="Tradytics" and ctx.schwab_store:
        fallback+=" · Schwab chain unavailable"
    if not ctx.preview and feed.snapshot and feed.snapshot.meta.get("delay_minutes") is None:
        fallback+=" · provider delay unverified"
    st.caption(f"{source_label}{fallback} · {status} · {stamp}")
    bid,ask=positive(row.get("bid")),positive(row.get("ask"))
    if status!="Fresh quote" or not (bid and ask):
        st.info(f"Pricing paused. {source_label} reports {status.lower()}. A fresh two-sided quote is required.")
        return
    selected_info=st.container()
    controls=st.container()
    chart=st.container()
    results=st.container()
    choices=st.container()
    with st.expander("Advanced settings"):
        try:
            rate,dividend,fee,qty,shock,use_skew,entry,step,norm,rth,extra,path=advanced(ctx,spec,ask,numeric(feed.snapshot.meta.get("risk_free_rate")) if source=="Schwab" else None)
        except ValueError as exc:
            results.info(str(exc));return
    observed_iv=iv_value(row.get("iv"))
    mid=(bid+ask)/2
    iv=observed_iv or calibrated_iv(spec,spot,ctx.now,mid,rate,dividend)
    if iv is None:
        selected_info.info("No usable chain IV. The midpoint also fell outside the supported pricing range.")
        return
    iv_source=f"{source_label} chain" if observed_iv else "fit to quoted midpoint"
    with controls:
        mode=st.segmented_control("Analysis",["Auto","Manual"],default="Auto",key="opc_mode") or "Auto"
        if mode=="Auto":
            ticker_ctx=replace(ctx,dte=(exp-ctx.session).days,expiry_waiting=False)
            plan,_=dashboard_decision(ticker_ctx,read_workspace(ticker_ctx))
            suggested,minutes,basis=auto_scenario(plan,spot)
        else:
            suggested,minutes,basis=spot,5,"Manual underlying and arrival scenario"
        # Date and time inputs are minute-resolution ET and refresh automatically in Auto.
        default=min(spec.expires,(ctx.now+timedelta(minutes=minutes)).replace(second=0,microsecond=0))
        if default<=ctx.now:default=spec.expires
        if mode=="Auto":
            target,arrival=suggested,default
            st.caption(f"{basis} · target {target:,.2f} by {arrival.astimezone(ET):%b %d, %I:%M %p %Z}.")
        else:
            a,b,c=st.columns(3)
            target=a.number_input("Underlying target ($)",min_value=.01,value=round(spot,2),key="opc_target_"+code)
            day=b.date_input("Arrival date",value=default.astimezone(ET).date(),min_value=today,max_value=spec.expires.astimezone(ET).date(),key="opc_date_"+code)
            at=c.time_input("Arrival time (ET)",value=default.astimezone(ET).time().replace(second=0,microsecond=0),step=60,key="opc_at_"+code)
            arrival=datetime.combine(day,at,tzinfo=ET).astimezone(timezone.utc)
        if not ctx.now<arrival<=spec.expires:
            st.info("Choose an arrival after now and through the contract’s payoff reference.")
            return
    fresh=[r for other,r,q in rows if q=="Fresh quote" and other.expires==spec.expires]
    skew=skew_scenario(fresh,spec.strike,spot,target) if use_skew else None
    future_iv=iv+(skew or 0)+shock
    if not .001<=future_iv<=5:
        results.info("The target IV is outside the supported range. Adjust the advanced IV assumption.")
        return
    try:
        times=timeline(ctx.now,spec.expires,step,arrival=arrival)
        prices,remaining,stats=scenario(spec,ctx.now,times,spot,target,arrival,iv,future_iv,rate,dividend,path,extended=any(key in extra for key in ("vanna","charm")))
    except ValueError as exc:
        results.info(str(exc));return
    with selected_info:
        selected_metrics(spec,row,ask,iv,iv_source,stats)
    with chart:
        history=chart_history(ctx,spec,row,feed)
        fig=combined(spec,times,prices,remaining,stats,entry,qty,fee,norm,rth,arrival,extra,
                     observed=history,preview=ctx.preview,spread=ask-bid)
        st.plotly_chart(fig,width="stretch",theme=None,key="opc_combined",config={"displayModeBar":True,"scrollZoom":True})
        st.caption(f"Solid points and lines show available {'synthetic preview' if ctx.preview else 'observed'} quotes. Dashed lines show modeled values. Shading marks hours outside regular underlying trading.")
    with results:
        result_metrics(spec,times,stats,ctx.now,spot,target,arrival,entry,iv,future_iv,qty,fee,ask-bid,rate,dividend,skew,shock)
    with choices:
        if enabled("SELECTION",True):
            rank_scenario(rows,ctx,spot,target,arrival,rate,dividend,fee,qty,future_iv-iv,MAX_DEBIT,today)
        with st.expander("Compare contracts under this scenario",key="opc_comparisons",on_change="rerun") as panel:
            if panel.open:
                compare_contracts(ctx,dates,exp,side,spec,times,stats,spot,target,arrival,future_iv-iv,rate,dividend,fee,path,iv_basis="provider",primary_entry=entry,primary_spread=ask-bid,qty=qty)
    details(ctx,spec,spot,bid,ask,iv,iv_source,entry,qty,fee,rate,dividend,future_iv,
            target,arrival,times,remaining,stats,fresh,rows)


def selected_metrics(spec,row,ask,iv,iv_source,stats):
    items=[("Current ask",number(ask,dollar=True),f"{number(ask*spec.multiplier,dollar=True)} / contract","")]
    for label,key,digits in (("Delta","delta",3),("Gamma","gamma",4),("IV","iv",1),("Theta","theta",2)):
        if key=="iv":
            items.append((label,number(iv,1,percent=True),iv_source,""))
            continue
        reported=numeric(row.get(key))
        value=stats[key][0] if reported is None else reported
        items.append((label,number(value,digits),"Calculated" if reported is None else "Chain Greek",""))
    kpis(items,small=True)


def result_metrics(spec,times,stats,now,spot,target,arrival,entry,iv,future_iv,qty,fee,spread,rate,dividend,skew,shock):
    index=times.index(arrival)
    fair=float(stats["premium"][index])
    concession=spread/2 if arrival<spec.expires else 0
    exit_mark=max(0.,fair-concession)
    pnl=(exit_mark-entry)*spec.multiplier*qty-2*fee*qty
    debit=(entry*spec.multiplier+fee)*qty
    years=max(0.,(spec.expires-arrival).total_seconds()/YEAR)
    unchanged=float(value(spec,spot,years,iv,rate,dividend))
    price_only=float(value(spec,target,years,iv,rate,dividend))
    hours=(spec.expires-now).total_seconds()/3600
    regular=session_state(arrival)["status"]=="OPEN" and arrival<=spec.last_trade
    estimate="Exit estimate" if regular else "Theoretical mark"
    st.markdown("**At your target**")
    if not regular:
        st.caption("Arrival is outside the regular trading window or after the last trading reference. Modeled P&L uses a theoretical mark. An executable exit quote is unavailable.")
    kpis([("Estimated premium",number(fair,dollar=True),f"{estimate} {number(exit_mark,dollar=True)}",""),
          ("Modeled P&L",number(pnl,dollar=True),f"{pnl/debit:+.1%} on estimated debit","up" if pnl>0 else "down" if pnl<0 else ""),
          ("Target Delta / Gamma",f"{number(stats['delta'][index],3)} / {number(stats['gamma'][index],4)}","At the selected arrival","")],small=True)
    time_effect=unchanged-stats["premium"][0]
    st.caption(f"Per share, elapsed time contributes {time_effect:+.2f}, the underlying move from {spot:,.2f} to {target:,.2f} "
               f"contributes {price_only-unchanged:+.2f}, and changing IV from {iv:.1%} to {future_iv:.1%} contributes {fair-price_only:+.2f}.")
    iv_note="interpolated strike skew" if skew is not None else "constant IV base"
    if shock:iv_note+=" plus manual shock"
    breakeven=spec.strike+(entry if spec.right=="call" else -entry)
    st.caption(f"The long option can lose its full {number(debit,dollar=True)} entry debit including opening fees. "
               f"Expiration break-even before fees is {breakeven:,.2f}.")
    st.caption(f"IV scenario uses {iv_note}. Remaining to payoff {hours:.1f}h. Round-trip fees {number(qty*2*fee,dollar=True)} "
               f"for {qty} contract{'s' if qty!=1 else ''}. Exit allowance {number(concession,dollar=True)} per share. "
               "Future spreads and a calibrated uncertainty interval are unavailable.")


def rank_scenario(rows,ctx,spot,target,arrival,rate,dividend,fee,qty,iv_shift,budget,today):
    ranked=[]
    for spec,row,status in rows:
        if status!="Fresh quote" or not ctx.now<arrival<=min(spec.expires,spec.last_trade):
            continue
        ask,bid=row["ask"],row["bid"]
        debit=qty*(ask*spec.multiplier+fee)
        if debit>budget:
            continue
        iv=iv_value(row.get("iv")) or calibrated_iv(spec,spot,ctx.now,(bid+ask)/2,rate,dividend)
        if iv is None or not .001<=iv+iv_shift<=5:
            continue
        try:
            premium=float(value(spec,target,(spec.expires-arrival).total_seconds()/YEAR,iv+iv_shift,rate,dividend))
        except ValueError:
            continue
        exit_mark=max(0.,premium-((ask-bid)/2 if arrival<spec.expires else 0.))
        net=(exit_mark-ask)*spec.multiplier*qty-2*fee*qty
        ranked.append((net,debit,spec))
    ranked.sort(key=lambda r:(-r[0],r[1],r[2].code))
    st.markdown("**Contract comparison**")
    if not ranked:
        st.caption(f"No fresh {rows[0][0].right} series has a usable scenario within the {number(budget,dollar=True)} debit cap and trading reference.")
        return
    pnl,debit,spec=ranked[0]
    if pnl>0 and session_state(arrival)["status"]=="OPEN":
        st.write(f"{contract_label(spec,ctx.ticker,today)} · estimated debit {number(debit,dollar=True)}, "
                 f"scenario P&L {number(pnl,dollar=True)}.")
        st.caption("Highest net scenario P&L among eligible strikes at this expiry. Each strike uses the same target, arrival and IV-point change.")
    elif pnl<=0:
        st.caption("Every eligible strike has a flat or negative modeled return at this target.")
    else:
        st.caption("The chosen arrival falls outside regular trading hours. The strike comparison uses theoretical values.")
    with st.expander("Compare strike values"):
        st.dataframe([{"Strike":format_strike(s.strike),"Debit":number(d,dollar=True),"Scenario P&L":number(net,dollar=True)}
                      for net,d,s in ranked[:20]],width="stretch",hide_index=True)


def details(ctx,spec,spot,bid,ask,iv,iv_source,entry,qty,fee,rate,dividend,future_iv,
            target,arrival,times,remaining,stats,fresh,rows):
    code=spec.code
    hours=(spec.expires-ctx.now).total_seconds()/3600
    premium=float(stats["premium"][times.index(arrival)])
    shock=(future_iv-iv)*100
    with st.expander("Contract details and full chain",key="opc_fullchain",on_change="rerun") as panel:
        if panel.open:
            st.write(f"{code} · {'American' if spec.american else 'European'} · ×{spec.multiplier} · {spec.settlement}")
            st.caption(f"Last trading reference {et_time(spec.last_trade)} · Payoff reference {et_time(spec.expires)} · "
                       f"IV {iv:.1%} ({iv_source}).")
            st.dataframe([{"Strike":format_strike(s.strike),"Delta":number(r.get("delta"),3),"IV":number(iv_value(r.get("iv")),1,percent=True),
                           "Volume":number(r.get("volume"),0),"Open interest":number(r.get("open_interest"),0),
                           "Spread":number(spread_fraction(r),1,percent=True),"Quote":status}
                          for s,r,status in rows],width="stretch",hide_index=True)
    with st.expander("Greeks and price/time P&L",key="opc_greek_grid",on_change="rerun") as grid_panel:
        if grid_panel.open:
            g=greeks(spec,spot,hours*3600/YEAR,iv,rate,dividend,extended=True)
            ds=target-spot; dv=shock; elapsed=(arrival-ctx.now).total_seconds()/86400
            approx=float(g["premium"]+g["delta"]*ds+.5*g["gamma"]*ds**2+g["vega"]*dv+.5*g["vomma"]*dv**2+g["vanna"]*ds*dv+g["theta"]*elapsed)
            st.caption(f"Target full reprice ${premium:.2f} · local Taylor estimate ${approx:.2f}. Calendar theta and IV shocks are applied once.")
            st.dataframe([{"Measure":k,"Value":float(v),"Units":UNITS[k]} for k,v in g.items()],hide_index=True)
            index=np.unique(np.linspace(0,len(times)-1,min(32,len(times)),dtype=int)); spots=np.linspace(spot*.98,spot*1.02,21)
            marks=value(spec,spots[:,None],remaining[index],np.where(remaining[index]>0,stats["iv"][index]/100,iv+shock/100),rate,dividend)
            pnl_grid=(np.maximum(0,marks-np.where(remaining[index]>0,(ask-bid)/2,0))-entry)*spec.multiplier*qty-2*fee*qty
            measure=st.selectbox("Heatmap",["Net P&L","Fair premium","Delta","Gamma","Calendar theta","Vega"])
            if measure=="Fair premium":pnl_grid=marks
            elif measure not in ("Net P&L","Fair premium"):
                key={"Delta":"delta","Gamma":"gamma","Calendar theta":"theta","Vega":"vega"}[measure]
                pnl_grid=greeks(spec,spots[:,None],remaining[index],np.where(remaining[index]>0,stats["iv"][index]/100,iv+shock/100),rate,dividend)[key]
            grid=go.Figure(go.Heatmap(x=[times[j] for j in index],y=spots,z=pnl_grid,colorscale="RdBu",zmid=0,colorbar_title=measure))
            st.plotly_chart(style_chart(grid,330,legend=False),width="stretch")
    with st.expander("Same target at different times",key="opc_arrivals",on_change="rerun") as time_panel:
        if time_panel.open:
            alternatives=sorted({ctx.now+timedelta(seconds=(spec.expires-ctx.now).total_seconds()*f) for f in (.1,.5,.9)}|{arrival})
            comparison=[]
            for at in alternatives:
                fair=float(value(spec,target,(spec.expires-at).total_seconds()/YEAR,iv+shock/100,rate,dividend))
                exit_est=max(0,fair-((ask-bid)/2 if at<spec.expires else 0))
                comparison.append({"Arrival ET":at.astimezone(ET).strftime("%b %d %H:%M"),"Target":target,"Fair premium":round(fair,3),
                                   "Scenario P&L ($)":round((exit_est-entry)*spec.multiplier*qty-2*fee*qty,2)})
            st.dataframe(comparison,hide_index=True)
            st.caption("Identical target and target IV. Outside market hours each value is theoretical.")
    if ctx.journal and not ctx.preview:
        with st.expander("Recorded premium forecasts and subsequent quotes",key="v3_actual_quotes",on_change="rerun") as history:
            if history.open:
                forecasts=[];observed=[]
                for r in reversed(ctx.journal.records(ctx.ticker,"v3_scan",scope=str(ctx.session),limit=450)):
                    payload=r["payload"]
                    if not live_record(r):continue
                    for item in payload.get("options",[]):
                        if item.get("contract")==code:forecasts.append((item["arrival"],item["forecast"]))
                    for item in payload.get("quote_samples",[]):
                        if item.get("contract")==code:observed.append((item["updated_at"],(item["bid"]+item["ask"])/2))
                chart=go.Figure()
                for points,name,dash in ((forecasts,"Prior target scenario","dash"),(observed,"Observed midpoint","solid")):
                    if points:chart.add_scatter(x=[x for x,y in points],y=[y for x,y in points],name=name,line=dict(dash=dash))
                if chart.data:st.plotly_chart(style_chart(chart,280),width="stretch")
                else:st.caption("No recorded observations for this series.")
    with st.expander("Empirical 5-minute option scenarios", key="v3_mc", on_change="rerun") as mc:
        if mc.open:
            result=block_simulation(spec,ctx.history()[0],ctx.now,spot,iv,entry,rate=rate,dividend=dividend,spread=ask-bid,fee=fee)
            if result:st.json(result)
            else:st.caption("At least 100 contiguous historical 5-minute price paths and five tradable minutes remaining are required.")
    with st.expander("Current IV by strike",key="opc_iv_skew",on_change="rerun") as panel:
        if not panel.open:
            return
        skew=sorted((r["strike"],iv_value(r.get("iv"))*100) for r in fresh if iv_value(r.get("iv")) and r.get("strike"))
        if len(skew)>=2:
            st.plotly_chart(style_chart(go.Figure(go.Scatter(x=[v[0] for v in skew],y=[v[1] for v in skew],mode="lines+markers",name="Provider IV (%)",
                             hovertemplate="Strike %{x}<br>IV %{y:.2f}%<extra></extra>")),260),width="stretch")
        else:st.caption("Insufficient fresh strikes.")
