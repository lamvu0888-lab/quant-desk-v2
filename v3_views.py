"""Movement, research and contract workspaces. Read-only in Streamlit."""
from datetime import datetime, timedelta, timezone
import json

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from market_session import ET, calendar, rth_bounds
from option_quotes import option_rows, quote_status
from terminal_data import first_listed_dte, listed_dtes, positive
from terminal_style import style_chart
from v3_movement import enabled
from v3_options import (UNITS, YEAR, block_simulation, calibrated_iv, contract_spec, greeks, research_rank, scenario, timeline, value, skew_scenario)


def movement_panel(ctx,plan):
    sig=plan.get("movement_v3")
    if not sig:return
    st.subheader("Movement Intelligence V3")
    st.markdown(f"**{sig['state']}** · {sig['action']}")
    st.caption(f"{sig['source']} · {sig['at'] or 'Waiting for a close'} · {sig['mode']} · Experimental evidence scores")
    if sig["scores"]:
        st.bar_chart(sig["scores"],horizontal=True)
        st.caption(sig["reason"])
    if ctx.journal and not ctx.preview:
        recent=ctx.journal.records(ctx.ticker,"v3_scan",scope=str(ctx.session),limit=180)
        rows=[x["payload"]["signal"] for x in reversed(recent) if x["payload"]["signal"].get("price")]
        if rows:
            fig=go.Figure(go.Scatter(x=[s["at"] for s in rows],y=[s["price"] for s in rows],name="Recorded close"))
            changes=[s for i,s in enumerate(rows) if i==0 or rows[i-1]["state"]!=s["state"]]
            fig.add_scatter(x=[s["at"] for s in changes],y=[s["price"] for s in changes],mode="markers",text=[s["state"] for s in changes],name="State change")
            st.plotly_chart(style_chart(fig,270),width="stretch")
            with st.expander("Movement score history"):
                history=go.Figure()
                for key in ("readiness","bullish","bearish","ignition","continuation","exhaustion","data_confidence"):
                    history.add_scatter(x=[s["at"] for s in rows],y=[s["scores"].get(key) for s in rows],name=key.replace("_"," ").title())
                history.update_yaxes(range=[0,100])
                st.plotly_chart(style_chart(history,300),width="stretch")
    with st.expander("Contributors, barrier horizons and missing data"):
        st.json({k:sig.get(k) for k in ("features","quality","invalidation","forecasts","probability","version")})


def learning(ctx):
    st.subheader("Daily research")
    if not ctx.journal or ctx.preview:
        st.info("Open Live with the worker’s shared journal to see recorded research.")
        return
    with st.expander("Independent price events"):
        for row in ctx.journal.records(ctx.ticker,"v3_events",limit=3):
            st.write(row["scope"])
            st.dataframe(row["payload"].get("events",[]),hide_index=True)
    for stage,label in (("report","Session coverage and outcomes"),("training","Challenger validation"),("feedback","OpenAI research proposals"),("replay","Proposal replay"),("discord","Daily summary delivery")):
        recent=ctx.journal.records("DESK","v3_"+stage,limit=5)
        with st.expander(label,expanded=stage=="report"):
            if not recent:st.caption("No completed run in this journal.")
            for row in recent:
                st.write(row["scope"])
                st.json(row["payload"],expanded=False)
    from v3_feedback import budget_status
    usage=budget_status(ctx.journal,ctx.now.astimezone(ET).date())
    if usage:st.dataframe(usage,hide_index=True)
    st.caption("Post-close schedule · +15 min outcomes · +30 min training · +45 min aggregate feedback. Production promotion disabled.")


def intervals(now,end):
    previous=now
    gaps=[]
    for s in calendar().sessions_in_range(now.astimezone(ET).date().isoformat(),end.astimezone(ET).date().isoformat()):
        op,cl=rth_bounds(s.date())
        if cl<=now or op>=end:continue
        if op>previous:gaps.append((previous,min(op,end)))
        previous=max(previous,min(cl,end))
    if previous<end:gaps.append((previous,end))
    return gaps


def combined(spec,times,prices,remaining,stats,entry,qty,cost,normalize,session_only,arrival,extra):
    fig=go.Figure()
    colors={"premium":"#16a079","delta":"#317fd4","gamma":"#d97d18","iv":"#9860c7","theta":"#c54450","vega":"#279bad","underlying":"#708594"}
    keys=["premium","delta","gamma","iv",*extra]
    data={**stats,"underlying":prices}
    pnl=(stats["premium"]-entry)*spec.multiplier*qty-2*cost*qty
    cd=[[at.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z"),*v] for at,v in zip(times,zip(prices,stats["premium"],stats["delta"],stats["gamma"],stats["iv"],stats["theta"],stats["vega"],remaining*YEAR/3600,pnl))]
    template=("%{customdata[0]}<br>Underlying $%{customdata[1]:.2f} · premium $%{customdata[2]:.2f}"
              "<br>Δ %{customdata[3]:.3f} · Γ %{customdata[4]:.5f} · IV %{customdata[5]:.2f}%"
              "<br>Θ/day %{customdata[6]:.3f} · Vega/point %{customdata[7]:.3f}"
              "<br>Remaining %{customdata[8]:.2f} h · modeled P&L $%{customdata[9]:.2f}<extra></extra>")
    for i,k in enumerate(keys):
        raw=data[k];axis="y" if i==0 or normalize else "y"+str(i+1)
        vals=raw
        if normalize:
            finite=raw[np.isfinite(raw)]
            vals=(raw-np.min(finite))/(np.ptp(finite) or 1)*100 if len(finite) else raw
        fig.add_scatter(x=times,y=vals,yaxis=axis,name=k.title(),line=dict(dash="dash",color=colors[k]),
                        customdata=cd,hovertemplate=template if i==0 else "%{fullData.name}<extra></extra>")
    style_chart(fig,470,hover="x unified")
    if not normalize:
        for i,k in enumerate(keys[1:],2):
            fig.update_layout(**{"yaxis"+str(i):dict(overlaying="y",side="right",anchor="free",autoshift=True,
                                                     showgrid=False,title=dict(text=k.title(),font=dict(color=colors[k])))})
    fig.update_yaxes(title_text="Relative scale 0–100" if normalize else "Premium ($)",selector=dict(anchor="x"))
    gaps=intervals(times[0],times[-1]);breaks=[]
    for a,b in gaps:
        if session_only:
            breaks.append(dict(values=[a],dvalue=(b-a).total_seconds()*1000))
        else:
            fig.add_vrect(x0=a,x1=b,line_width=0,fillcolor="gray",opacity=.10,layer="below")
    for t,label in ((times[0],"Now"),(arrival,"Target"),(times[-1],"Expiry")):
        fig.add_shape(type="line",x0=t,x1=t,y0=0,y1=1,yref="paper",line=dict(dash="dot",width=1))
        fig.add_annotation(x=t,y=1,yref="paper",text=label,showarrow=False,yshift=9)
    ticks=np.linspace(0,len(times)-1,min(6,len(times)),dtype=int)
    fig.update_xaxes(type="date",range=[times[0],times[-1]],rangebreaks=breaks,rangeslider_visible=True,
                     tickvals=[times[i] for i in ticks],ticktext=[times[i].astimezone(ET).strftime("%b %d<br>%H:%M") for i in ticks],
                     title_text="Eastern time · conditional scenario",showspikes=True,spikemode="across")
    return fig


def contracts(ctx):
    from paper_engine import MAX_DEBIT
    st.subheader("Options Contract Intelligence")
    if not enabled("OPTIONS",True):st.info("Contract Intelligence is disabled.");return
    listing=ctx.get("expirations",symbol=ctx.ticker)
    if first_listed_dte(listing,ctx.ticker,ctx.session,ctx.now) is None:
        st.info("Waiting for verified listed expirations.");return
    dates=[ctx.session+timedelta(days=n) for n in listed_dtes(listing,ctx.session) if ctx.session+timedelta(days=n)>=ctx.now.astimezone(ET).date()]
    if not dates:st.info("No current listed expiration.");return
    exp=st.selectbox("Listed expiry · calendar DTE",dates,format_func=lambda x:f"{x} · {(x-ctx.now.astimezone(ET).date()).days} DTE")
    side=st.segmented_control("Contract side",["call","put"],default="call") or "call"
    feed,source=ctx.option_chain(exp.isoformat(),right=side,near=False)
    quotes=option_rows(feed,ctx.ticker,exp.isoformat())
    quote=ctx.chart_quote
    if not quote:st.info("Waiting for a fresh regular-session underlying quote.");return
    spot=quote["price"]
    money=st.selectbox("Moneyness",["All","ATM","ITM","OTM"])
    chain={}
    for row in quotes:
        if row.get("type")!=side:continue
        try: spec=contract_spec(row,ctx.ticker)
        except (ValueError,TypeError):continue
        if ctx.now>=min(spec.last_trade,spec.expires): continue
        chain[spec.code]=(spec,row)
    if money=="ATM" and chain:
        nearest=min(abs(spec.strike-spot) for spec,r in chain.values())
        chain={k:v for k,v in chain.items() if abs(v[0].strike-spot)==nearest}
    elif money in ("ITM","OTM"):
        sign=(1 if side=="call" else -1)*(1 if money=="ITM" else -1)
        chain={k:v for k,v in chain.items() if sign*(spot-v[0].strike)>0}
    if not chain:st.info("No listed contract matches the selected filters and trading clock.");return
    code=st.selectbox("Contract / strike",sorted(chain,key=lambda c:abs(chain[c][0].strike-spot)))
    spec,row=chain[code]
    status=quote_status(row,feed,ctx.now,paused=ctx.paused,max_age=ctx.option_max_age)
    bid,ask=positive(row.get("bid")),positive(row.get("ask"))
    st.caption(f"{source} · {status} · {row.get('updated_at','—')} · {spec.settlement} · {'American' if spec.american else 'European'} · ×{spec.multiplier}")
    st.write(f"Last trading reference {spec.last_trade.astimezone(ET):%b %d %H:%M %Z} · Payoff reference {spec.expires.astimezone(ET):%b %d %H:%M %Z}")
    st.dataframe([{k:row.get(k) for k in ("bid","ask","last","volume","open_interest","iv","delta","gamma","theta","vega")} | {"mid":(bid+ask)/2 if bid and ask else None}],hide_index=True)
    if status!="Fresh quote" or not bid or not ask:
        st.info("Scenario calibration requires a current two-sided quote.");return
    a,b,c=st.columns(3)
    target_basis=st.selectbox("Underlying target source",["Manual target","Trade Plan TP1","Movement upper barrier (5m)","Movement lower barrier (5m)"])
    suggested=spot
    if target_basis!="Manual target":
        from terminal_views import read_workspace
        from decision_flow import build_decision
        plan,_=build_decision(ctx,read_workspace(ctx),warm_options=False)
        barriers=(plan.get("movement_v3") or {}).get("forecasts",{}).get("5",{})
        suggested=plan.get("tp1") if target_basis=="Trade Plan TP1" else barriers.get("upper" if "upper" in target_basis else "lower")
        if not positive(suggested):st.info("This target is unavailable for the current decision.");return
    target=a.number_input("Target underlying ($)",min_value=.01,value=round(float(suggested),2),key=f"v3_target_{ctx.ticker}_{target_basis}_{suggested if target_basis!='Manual target' else 0}")
    hours=(spec.expires-ctx.now).total_seconds()/3600
    default=ctx.now+timedelta(seconds=min(hours*1800,3600))
    target_date=b.date_input("Arrival date (ET)",value=default.astimezone(ET).date(),min_value=ctx.now.astimezone(ET).date(),max_value=spec.expires.astimezone(ET).date(),key=f"v3_day_{code}")
    target_time=c.time_input("Arrival time (ET)",value=default.astimezone(ET).time().replace(microsecond=0),step=60,key=f"v3_time_{code}")
    arrival=datetime.combine(target_date,target_time,tzinfo=ET).astimezone(timezone.utc)
    if not ctx.now<arrival<=spec.expires:st.info("Choose an arrival after now and through the payoff reference.");return
    a,b,c=st.columns(3)
    shock=a.number_input("IV change (points)",-95.,200.,0.)
    rate=b.number_input("Annual rate (%)",-5.,30.,4.)/100
    div=c.number_input("Annual dividend yield (%)",0.,30.,0.)/100
    mid=(bid+ask)/2
    iv=calibrated_iv(spec,spot,ctx.now,mid,rate,div)
    if iv is None:st.info("The observed midpoint is outside the supported IV bracket for this model.");return
    fresh_side=[r for r in quotes if r.get("type")==side and quote_status(r,feed,ctx.now,paused=ctx.paused,max_age=ctx.option_max_age)=="Fresh quote"]
    if st.toggle("Use observed strike skew for the target IV"):
        skew=skew_scenario(fresh_side,spec.strike,spot,target)
        if skew is None:
            st.info("The target needs at least three fresh strikes bracketing both moneyness points.");return
        shock+=skew*100
        st.caption(f"Sticky-moneyness scenario · current expiry contributes {skew*100:+.2f} IV points.")
    if iv+shock/100<=0:st.info("Target IV must be positive.");return
    a,b,c=st.columns(3)
    entry=a.number_input("Entry premium ($)",min_value=.01,value=float(ask),key=f"v3_premium_{code}")
    qty=b.number_input("Scenario contracts",1,2,1)
    fee=c.number_input("Cost per contract per side ($)",0.,25.,.65)
    controls=st.columns(3)
    step=controls[0].selectbox("Minutes per point",[0,1,3,5,15,30,60,240,1440],format_func=lambda x:str(x) if x else "Auto")
    norm=controls[1].toggle("Normalize chart",value=True)
    rth=controls[2].toggle("Regular sessions only")
    extra=st.multiselect("Additional overlays",["underlying","theta","vega"])
    custom=[]
    if st.toggle("Custom intermediate price path"):
        knots=st.text_input("Elapsed minutes and price pairs",placeholder="5,701.50; 12,703.00")
        try:
            for item in knots.split(";") if knots.strip() else []:
                minute,level=map(float,item.split(","))
                if not np.isfinite([minute,level]).all():raise ValueError()
                custom.append((minute*60,level))
        except ValueError:st.info("Enter pairs of elapsed minutes and underlying price, separated by semicolons.");return
    try:
        times=timeline(ctx.now,spec.expires,step,arrival=arrival)
        prices,remaining,stats=scenario(spec,ctx.now,times,spot,target,arrival,iv,iv+shock/100,rate,div,custom)
    except ValueError as exc:st.info(str(exc));return
    fig=combined(spec,times,prices,remaining,stats,entry,qty,fee,norm,rth,arrival,extra)
    if not norm:
        for key in ("premium","delta","gamma","iv"):
            observed=mid if key=="premium" else row.get(key)
            if observed is not None:
                fig.add_scatter(x=[row["updated_at"]],y=[observed*100 if key=="iv" else observed],mode="markers",
                                name="Observed midpoint" if key=="premium" else "Provider "+key,yaxis={"premium":"y","delta":"y2","gamma":"y3","iv":"y4"}[key])
    st.plotly_chart(fig,width="stretch",config={"displayModeBar":True,"scrollZoom":True})
    i=times.index(arrival)
    premium=float(stats["premium"][i]); concession=(ask-bid)/2 if arrival<spec.expires else 0
    pnl=(max(0,premium-concession)-entry)*100*qty-2*fee*qty
    summary=st.columns(3)
    summary[0].metric("Modeled P&L at target",f"${pnl:,.2f}",help="Includes entry premium, estimated exit concession and both fees.")
    summary[1].metric("Return on modeled debit",f"{100*pnl/((entry*spec.multiplier+fee)*qty):+.1f}%")
    summary[2].metric("Modeled position value",f"${premium*spec.multiplier*qty:,.2f}")
    st.caption(f"Target fair value ${premium:.2f} · scenario exit ${max(0,premium-concession):.2f} · expiration break-even before fees {spec.strike+(entry if side=='call' else -entry):,.2f}. "
               f"IV fitted to midpoint {iv:.1%}. Dashed lines are conditional estimates. Gray bands mark hours outside underlying RTH.")
    with st.expander("Greeks and price/time P&L"):
        g=greeks(spec,spot,hours*3600/YEAR,iv,rate,div,extended=True)
        ds=target-spot; dv=shock; elapsed=(arrival-ctx.now).total_seconds()/86400
        approx=float(g["premium"]+g["delta"]*ds+.5*g["gamma"]*ds**2+g["vega"]*dv+.5*g["vomma"]*dv**2+g["vanna"]*ds*dv+g["theta"]*elapsed)
        st.caption(f"Target full reprice ${premium:.2f} · local Taylor estimate ${approx:.2f}. Calendar theta and IV shocks are applied once.")
        st.dataframe([{"Measure":k,"Value":float(v),"Units":UNITS[k]} for k,v in g.items()],hide_index=True)
        index=np.unique(np.linspace(0,len(times)-1,min(32,len(times)),dtype=int)); spots=np.linspace(spot*.98,spot*1.02,21)
        marks=value(spec,spots[:,None],remaining[index],np.where(remaining[index]>0,stats["iv"][index]/100,iv+shock/100),rate,div)
        pnl_grid=(np.maximum(0,marks-np.where(remaining[index]>0,(ask-bid)/2,0))-entry)*100*qty-2*fee*qty
        measure=st.selectbox("Heatmap",["Net P&L","Fair premium","Delta","Gamma","Calendar theta","Vega"])
        if measure=="Fair premium":pnl_grid=marks
        elif measure not in ("Net P&L","Fair premium"):
            key={"Delta":"delta","Gamma":"gamma","Calendar theta":"theta","Vega":"vega"}[measure]
            pnl_grid=greeks(spec,spots[:,None],remaining[index],np.where(remaining[index]>0,stats["iv"][index]/100,iv+shock/100),rate,div)[key]
        grid=go.Figure(go.Heatmap(x=[times[j] for j in index],y=spots,z=pnl_grid,colorscale="RdBu",zmid=0,colorbar_title=measure))
        st.plotly_chart(style_chart(grid,330,legend=False),width="stretch")
    with st.expander("Same target at different times"):
        alternatives=sorted({ctx.now+timedelta(seconds=(spec.expires-ctx.now).total_seconds()*f) for f in (.1,.5,.9)}|{arrival})
        comparison=[]
        for at in alternatives:
            fair=float(value(spec,target,(spec.expires-at).total_seconds()/YEAR,iv+shock/100,rate,div))
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
            result=block_simulation(spec,ctx.history()[0],ctx.now,spot,iv,entry,rate=rate,dividend=div,spread=ask-bid,fee=fee)
            if result:st.json(result)
            else:st.caption("At least 100 contiguous historical 5-minute price paths and five tradable minutes remaining are required.")
    with st.expander("Current IV by strike"):
        from iv_decision import iv_value
        skew=sorted((r["strike"],iv_value(r.get("iv"))*100) for r in fresh_side if iv_value(r.get("iv")) and r.get("strike"))
        if len(skew)>=2:
            st.plotly_chart(style_chart(go.Figure(go.Scatter(x=[v[0] for v in skew],y=[v[1] for v in skew],mode="lines+markers",name="Provider IV (%)",
                             hovertemplate="Strike %{x}<br>IV %{y:.2f}%<extra></extra>")),260),width="stretch")
        else:st.caption("Insufficient fresh strikes.")
    compare_contracts(ctx,dates,exp,side,spec,times,stats,spot,target,arrival,shock/100,rate,div,fee,custom)
    if enabled("SELECTION",True):
        fresh=[r for _,r in chain.values() if quote_status(r,feed,ctx.now,max_age=ctx.option_max_age,paused=ctx.paused)=="Fresh quote"]
        ranks=research_rank(fresh,spot,target,arrival,ctx.now,rate,div,MAX_DEBIT,fee,ctx.ticker)
        st.write("Conditional comparison · one contract, same target and arrival")
        if not ranks or ranks[0]["conditional_pnl"]<=0:st.info("No suitable contract for a positive modeled return under this scenario.")
        if ranks:st.dataframe(ranks[:20],hide_index=True)


def compare_contracts(ctx,dates,exp,side,primary_spec,primary_times,primary_stats,spot,target,arrival,shift,rate,div,fee,custom):
    enabled_compare=st.toggle("Compare another strike or expiration",key="v3_compare")
    if not enabled_compare:return
    other_day=st.selectbox("Comparison expiry",dates,index=dates.index(exp),key="v3_compare_expiry")
    feed,source=ctx.option_chain(other_day.isoformat(),right=side,near=False)
    entries={}
    for row in option_rows(feed,ctx.ticker,other_day.isoformat()):
        if row.get("type")!=side or quote_status(row,feed,ctx.now,max_age=ctx.option_max_age,paused=ctx.paused)!="Fresh quote":continue
        try: other=contract_spec(row,ctx.ticker)
        except (ValueError,TypeError):continue
        if other.code!=primary_spec.code and min(other.expires,other.last_trade)>ctx.now: entries[other.code]=(other,row)
    if not entries:st.info("No second fresh matching series.");return
    selected=st.selectbox("Comparison contract",sorted(entries,key=lambda k:abs(entries[k][0].strike-spot)))
    other,row=entries[selected]
    fitted=calibrated_iv(other,spot,ctx.now,(row["bid"]+row["ask"])/2,rate,div)
    if fitted is None or fitted+shift<=0:st.info("Unable to calibrate comparison IV.");return
    times=timeline(ctx.now,other.expires,arrival=arrival)
    _,_,stats=scenario(other,ctx.now,times,spot,target,arrival,fitted,fitted+shift,rate,div,custom)
    field=st.selectbox("Comparison measure",["premium","delta","gamma","iv","theta","vega"])
    fig=go.Figure()
    for spec,axis,data in ((primary_spec,primary_times,primary_stats),(other,times,stats)):
        fig.add_scatter(x=axis,y=data[field],name=spec.code,mode="lines",line=dict(dash="dash"),
                        customdata=[x.astimezone(ET).strftime("%b %d %H:%M %Z") for x in axis],
                        hovertemplate="%{customdata}<br>%{y:.3f}<extra>%{fullData.name}</extra>")
    fig.update_yaxes(title=UNITS[field])
    st.plotly_chart(style_chart(fig,360),width="stretch")
    st.caption(f"{source} comparison · same underlying and IV paths · each series ends at its own payoff reference.")
