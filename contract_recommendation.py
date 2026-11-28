"""Point-in-time comparisons for long, standard listed options. No order or journal writes."""
from collections import Counter
from datetime import timedelta
from math import floor
import os
import time

import numpy as np

from contract_sources import contract_chain, contract_expirations, live_feed, reported_realtime
from contract_calibration import calibrate_quote
from data_utils import DataError
from market_session import ET, rth_bounds
from option_quotes import quote_status, spread_fraction
from terminal_data import first_listed_dte, listed_dtes, numeric, positive, time_or_none
from trade_probability import fitting_sample, simulate_paths
from v3_options import YEAR, contract_spec, greeks, value
from v3_provenance import display_plan, simulated_context, simulated_feed

VERSION = "qd-contract-rank.1"
DEFAULT_MODE = "risk_adjusted"
MODES = {"risk_adjusted": "Risk-adjusted opportunity", "net_profit": "Expected position profit",
         "probability": "Validated cohort probability of a positive fixed-time net markout",
         "net_return": "Expected net return", "conservative": "Conservative"}
WEIGHTS = {"expected_net": .25, "target_before_stop": .20, "downside": .15, "liquidity": .15,
           "sensitivity": .10, "carry": .10, "iv_risk": .05}
ENTRY = {"ENTER LONG": 1, "ENTER SHORT": -1}
QUOTE_SECONDS = 30
MAX_SYNC_GAP = 30
MAX_RELATIVE_SPREAD = .20
SIM_PATHS = 256


def _dtes(listing, ctx):
    if first_listed_dte(listing, ctx.ticker, ctx.session, ctx.now) is None:
        return []
    days = listed_dtes(listing, ctx.session)
    # Short-dated strategies compare every listing through two weeks, the next
    # series beyond, and the nearest available monthly tenors. Dates always come from the provider.
    picks = {n for n in days if n <= 14}
    for target in (15, 30, 60, 90):
        if days and any(n >= target for n in days):
            picks.add(min(n for n in days if n >= target))
    scoped = getattr(ctx, "dte", None)
    if scoped in days:
        picks.add(scoped)
    return sorted(picks)


def _market_paths(candles, now, plan, spot, minutes, opened):
    fit = fitting_sample(candles[-2400:], now)
    flow = plan.get("flow") or {}
    tilt = min(1., abs(numeric(flow.get("momentum")) or 0) / (3 * max(1., numeric(flow.get("floor")) or 1.)))
    gamma = numeric(plan.get("gex")) or 0.
    paths = simulate_paths(fit, spot, minutes, int((now-opened).total_seconds()//60), n=SIM_PATHS,
                           regime=plan.get("regime"), flow_pressure=tilt, gamma_pressure=-np.sign(gamma))
    return paths, fit["count"], fit["sessions"]


def _terminal_market(paths, spot, side, target, stop):
    close, lo, hi = (paths[key] for key in ("close", "low", "high"))
    stop_hits = lo <= stop if side == 1 else hi >= stop
    target_hits = hi >= target if side == 1 else lo <= target
    crossed = stop_hits | target_hits
    # A minute that touched both boundaries has unknown ordering. Charge the stop.
    first = np.where(crossed.any(axis=1), crossed.argmax(axis=1), crossed.shape[1]-1)
    row = np.arange(len(close))
    stopped = stop_hits[row, first]
    reached = target_hits[row, first] & ~stopped
    end_spot = np.where(reached, target, close[row, first+1])
    end_spot = np.where(stopped, np.minimum(stop, end_spot) if side==1 else np.maximum(stop, end_spot), end_spot)
    return first+1, end_spot, float(reached.mean())


def _path_marks(spec, args, iv, rate, dividend, half_spread, ask, fee, premium_stop):
    """Price common 1-minute paths on a compact spot/time grid; every contract sees the same paths."""
    paths, stop_idx, end_spot, years, spot, target, stop = args
    closes = paths["close"]
    horizon = closes.shape[1]-1
    # Insert strike, plan barriers and current price exactly in the interpolation grid.
    lower = min(end_spot.min(), closes.min(), target, stop)
    upper = max(end_spot.max(), closes.max(), target, stop)
    span = np.linspace(lower, upper, 65)
    anchors = np.unique(np.r_[span, [p for p in (spot, target, stop, spec.strike) if lower <= p <= upper]])
    knots = np.unique(np.r_[np.arange(0,horizon+1,max(1,horizon//8)),horizon])
    life = np.maximum(0., years - knots[:,None]*60/YEAR)
    prices = value(spec, anchors[None,:], life, iv, rate, dividend)
    rr = np.arange(len(closes))
    out = np.zeros(len(rr))
    exit_spot = np.zeros(len(rr))
    exit_minute = np.zeros(len(rr))
    mae = np.zeros(len(rr))
    active = np.ones(len(rr), dtype=bool)
    for minute in range(1, horizon+1):
        hi_idx = np.searchsorted(knots,minute)
        lo_idx = hi_idx-1
        t_weight = (minute-knots[lo_idx])/(knots[hi_idx]-knots[lo_idx])
        curve = prices[lo_idx]*(1-t_weight) + prices[hi_idx]*t_weight
        # Structural stop/target on this bar takes priority over the end-of-minute mark.
        structural = minute == stop_idx
        obs = np.where(structural, end_spot, closes[:,minute])
        bid = np.maximum(0., np.interp(obs, anchors, curve)-half_spread)
        net = (bid-ask)*spec.multiplier-2*fee
        mae = np.where(active, np.minimum(mae,net), mae)
        exit_now = active & (structural | (bid <= ask*(1-premium_stop)))
        out[exit_now] = net[exit_now]
        exit_spot[exit_now] = obs[exit_now]
        exit_minute[exit_now] = minute
        active &= ~exit_now
    return out, float(-mae.mean()), exit_spot, exit_minute


def _positive_scale(gain, risk):
    return gain/(gain+risk) if gain>0 and risk>0 else 0.


def _suitability(delta, plan, hold):
    sig = plan.get("movement_v3") or {}
    state = str(sig.get("state",""))
    fast = hold<=15 and state.endswith(("IGNITION","EXPANSION"))
    target_delta = .48 if fast else .62
    if "reversal" in str(plan.get("setup","")).lower():
        target_delta = .58
    return float(max(0.,1.-abs(abs(delta)-target_delta)))


def _estimate(spec, row, base, shared, ctx, plan, settings, calibration):
    from paper_engine import PREMIUM_STOP
    spot, target, stop, hold = (base[key] for key in ("spot","target","stop","hold"))
    fee, rate, dividend, budget, remaining_loss = settings
    bid, ask = row["bid"], row["ask"]
    iv = calibration.iv
    half = (ask-bid)/2
    years = (spec.expires-ctx.now).total_seconds()/YEAR
    remaining = max(0., years-hold*60/YEAR)
    stress_iv = max(.005,iv*(.85 if plan.get("premium_review_required") else .90))
    xs = np.array([target,stop,spot,target])
    marks = value(spec, xs, remaining, np.array([iv,iv,iv,stress_iv]), rate, dividend)
    target_bid, stop_bid, flat_bid, stress_bid = np.maximum(0., marks-half)
    debit = ask*spec.multiplier + fee
    benchmark_loss = max(0., (ask-stop_bid)*spec.multiplier+2*fee, ask*spec.multiplier*PREMIUM_STOP+2*fee)
    size = numeric(row.get("ask_size"))
    size_cap = 1 if size is None else max(0,floor(size))
    qty = min(2, size_cap, floor(budget/debit), floor(remaining_loss/max(.01,benchmark_loss)))
    if qty < 1:
        raise ValueError("Ask size, buying budget or remaining modeled loss capacity cannot cover one contract.")
    g = greeks(spec, spot, years, iv, rate, dividend)
    delta, gamma, theta, vega = [float(g[key]) for key in ("delta","gamma","theta","vega")]
    paths, end_idx, end_spot, p_target = shared
    net, mae, exit_spot, exit_minute = _path_marks(spec, (paths,end_idx,end_spot,years,spot,target,stop),
                           iv,rate,dividend,half,ask,fee,PREMIUM_STOP)
    avg = float(np.mean(net))
    # Monte Carlo numerical precision only. It excludes uncertainty in historical fit, execution and IV.
    mc_se = float(np.std(net, ddof=1) / np.sqrt(len(net)))
    sampling_floor = avg - 1.645*mc_se
    stress_life = np.maximum(0, years-exit_minute*60/YEAR)
    compressed_iv = iv + (stress_iv-iv)*exit_minute/hold
    stressed = value(spec,exit_spot,stress_life,compressed_iv,rate,dividend)
    stressed_bid = np.maximum(0., stressed - half*1.5 - .01)
    stressed_avg = float(((stressed_bid-ask)*spec.multiplier-2*fee).mean())
    target_net = float((target_bid-ask)*spec.multiplier-2*fee)
    stop_net = float((stop_bid-ask)*spec.multiplier-2*fee)
    stress_net = float((stress_bid-ask)*spec.multiplier-2*fee)
    # Worst 10% conditional modeled outcomes, including spread and fees.
    tail = np.partition(net, max(1,len(net)//10)-1)[:max(1,len(net)//10)]
    loss = max(0.,-float(tail.mean()))
    spread = spread_fraction(row)
    rr = target_net/max(.01,benchmark_loss)
    components = dict(expected_net=_positive_scale(avg, max(.01,loss)), target_before_stop=p_target,
                      downside=_positive_scale(max(0.,target_net), max(.01,loss,benchmark_loss)),
                      liquidity=max(0.,1.-spread/MAX_RELATIVE_SPREAD), sensitivity=_suitability(delta,plan,hold),
                      carry=1-min(1.,max(0., ask-flat_bid)/(max(.01,target_bid-ask)+ask)),
                      iv_risk=1-min(1.,max(0.,target_net-stress_net)/max(1.,abs(target_net),debit*.1)))
    features = {k: float(np.clip(v,0.,1.)) for k,v in components.items()}
    score = 100*sum(features[k]*w for k,w in WEIGHTS.items())
    premium_path = []
    for f in (0.,.5,1.):
        minute = hold*f
        mark = float(value(spec,spot+(target-spot)*f,max(0.,years-minute*60/YEAR),iv,rate,dividend))
        premium_path.append({"minutes":minute,"spot":spot+(target-spot)*f,"fair":mark})
    rejection = None
    if avg <= 0 or target_net <= 0 or rr <= 0:
        rejection = "Expected net profit or target reward is nonpositive after spread and fees."
    elif sampling_floor <= 0:
        rejection = "The modeled gain is within Monte Carlo sampling noise."
    elif base["mode"] == "conservative" and stressed_avg <= 0:
        rejection = "Expected net turns negative under IV contraction and wider exit spreads."
    explanation = (f"Modeled net ${avg*qty:,.2f} for {qty} contract(s), quoted spread {spread:.1%}, "
                   f"and modeled target reward of {rr:.2f} times the estimated stop loss.")
    return dict(eligible=rejection is None, reason=rejection or explanation,
                features=features, score=round(score,2), quantity=qty, cost=round(debit*qty,2),
                max_loss=round(debit*qty,2), delta=delta, gamma=gamma, theta=theta, vega=vega,
                greek_basis="Modeled at synchronized quote IV",
                iv=iv, iv_basis=calibration.basis, provider_iv=calibration.provider_iv, midpoint_iv=calibration.midpoint_iv,
                target_premium=float(target_bid), stop_premium=float(stop_bid),
                target_net=round(target_net*qty,2), stop_net=round(stop_net*qty,2),
                expected_per_contract=round(avg,4),expected_net=round(avg*qty,4), expected_return=avg/debit,
                fee_per_side=fee, rate=rate, dividend=dividend, model_fair=float(value(spec,spot,years,iv,rate,dividend)),
                projected_sampling_se=round(mc_se*qty,4), stressed_expected_net=round(stressed_avg*qty,4),
                modeled_loss=round(benchmark_loss*qty,2),tail_loss=round(loss*qty,2),
                expected_loss=round(float(np.maximum(-net,0).mean())*qty,2),
                mean_adverse_excursion=round(mae*qty,2), risk_reward=float(rr), target_before_stop=p_target,
                iv_stress=stress_iv,stress_target_net=round(stress_net*qty,2), flat_premium=float(flat_bid),
                premium_path=premium_path, spread=spread, probability=None)


def configured_mode():
    name = os.getenv("QD_CONTRACT_RANK_MODE", DEFAULT_MODE).strip().lower()
    return name if name in MODES else DEFAULT_MODE


def _unavailable(result, reason):
    result["reason"] = reason
    return result


def _opportunity(plan):
    status = plan.get("status")
    direction = plan.get("direction")
    sig = plan.get("movement_v3") or {}
    state = sig.get("state") or ""
    if (plan.get("blocks") or plan.get("data_reasons") or plan.get("price_context_ready") is False
            or status in ("AVOID","EXIT") or direction not in (-1,1)):
        return None
    if state in ("CONFLICTING PRESSURE","FAILED MOVE","INVALIDATED"):
        return None
    # Independent reversal strategies keep their entry rules, even when the
    # trend lifecycle is quiet. A contradictory directional V3 read is explicit.
    if sig.get("side") in (-1,1) and sig["side"] != direction and state.endswith(("PRESSURE","IGNITION","EXPANSION")):
        return None
    if ENTRY.get(status) == direction:
        return "qualified"
    if status == "GET READY" or (status == "WAIT" and sig.get("side")==direction and state.endswith(("PRESSURE","IGNITION","EXPANSION"))):
        return "developing"
    return None


def _quote_record(raw, ctx, day, source, feed):
    spec = contract_spec(raw, ctx.ticker)
    code = spec.code
    stamp = time_or_none(raw.get("updated_at"))
    bid,ask = positive(raw.get("bid")),positive(raw.get("ask"))
    dte = (spec.expires.astimezone(ET).date()-ctx.session).days
    out = dict(contract=code,label=f"{ctx.ticker} {spec.strike:g} {spec.right.title()} · {dte}DTE",
               symbol=ctx.ticker,expiry=day,dte=dte,type=spec.right,strike=spec.strike,source=source,
               quote_at=stamp.isoformat() if stamp else None,bid=bid,ask=ask,entry=ask,
               ask_size=numeric(raw.get("ask_size")),bid_size=numeric(raw.get("bid_size")),
               delay_minutes=0 if reported_realtime(raw,feed) else None,
               received_at=feed.snapshot.received_at.isoformat() if feed.snapshot else None,
               volume=numeric(raw.get("volume")),open_interest=numeric(raw.get("open_interest")),
               provider_greeks={key:numeric(raw.get(key)) for key in ("delta","gamma","theta","vega")},
               eligible=False,quantity=0,reason=None)
    quality = quote_status(raw,feed,ctx.now,max_age=min(ctx.option_max_age,QUOTE_SECONDS),paused=ctx.paused)
    if stamp and feed.snapshot and stamp > feed.snapshot.received_at:
        out["reason"]="Quote timestamp follows its receipt."
    elif quality!="Fresh quote":
        out["reason"]=quality
    elif not ctx.preview:
        if not reported_realtime(raw, feed):
            out["reason"]="Option quote delay is unverified or nonzero."
    return spec,out


def _rank_key(row, mode):
    if mode == "net_profit":
        value = row["expected_net"]
    elif mode == "net_return":
        value = row["expected_return"]
    elif mode == "probability":
        value = row["probability"]
    elif mode == "conservative":
        value = row["stressed_expected_net"]/(row["quantity"]+row["tail_loss"])
    else:
        value = row["score"]
    return -value, -row["score"], row["spread"],row["contract"]


def _chain_sources(ctx, expiry, right):
    feed, source = contract_chain(ctx, expiry, right, broker_only=True)
    yield feed, source
    if ctx.preview or source != "Webull" or not feed.snapshot or not getattr(ctx, "schwab_store", None):
        return
    meta = feed.snapshot.meta
    # Webull's established collector quotes a limited strike window. Supplement
    # a disclosed truncated result through the asynchronous authenticated Schwab store.
    if (numeric(meta.get("discovered")) or 0) > (numeric(meta.get("requested")) or 0):
        other = ctx.schwab_store.get("option_chain", symbol=ctx.ticker, expiry=expiry, right=right, paused=ctx.paused)
        yield live_feed(other), "Schwab"


def recommend(ctx, plan, read, mode=None, *, remaining_loss=None):
    """Compare the accessible listings against one as-of, target, stop, hold and common set of price paths.

    An unavailable result never chooses a substitute. The paper worker must
    independently verify its account state and actual post-alert ask/size.
    """
    from paper_engine import DAILY_STOP, FEE, MAX_DEBIT
    from settings import Settings
    started = time.monotonic()
    mode = mode or configured_mode()
    result = dict(version=VERSION,origin=ctx.now.isoformat(),expires_at=ctx.now.isoformat(),symbol=ctx.ticker,
                  status="unavailable",reason="",simulated=bool(ctx.preview),mode=mode,selected=None,
                  alternatives=[],candidates=[],diagnostics={}, daily_capacity_verified=remaining_loss is not None)
    if not ctx.preview and (display_plan(plan) or simulated_context(ctx,read)):
        result["simulated"] = True
        return _unavailable(result,"Synthetic decision context excluded.")
    if mode not in MODES:
        return _unavailable(result,"Unknown contract ranking mode.")
    status = _opportunity(plan)
    if not status:
        return _unavailable(result,"Waiting for an aligned directional plan with no active block.")
    if ctx.ticker not in ("SPY","QQQ","SPX") or ctx.paused:
        return _unavailable(result,"A supported ticker and active feeds are required.")
    bounds = rth_bounds(ctx.session)
    quote = ctx.chart_quote
    if not bounds or not bounds[0]<=ctx.now<bounds[1] or not quote:
        return _unavailable(result,"Waiting for an open regular session and a verified underlying quote.")
    at = time_or_none(quote.get("at"))
    spot = positive(quote.get("price"))
    if not at or not spot or not 0<=(ctx.now-at).total_seconds()<QUOTE_SECONDS:
        return _unavailable(result,"Waiting for a current underlying quote.")
    target,stop,hold = [positive(plan.get(k)) for k in ("tp1","stop","hold")]
    side = plan["direction"]
    if not target or not stop or not hold or hold!=int(hold) or hold>60 or not side*(target-spot)>0 or not side*(spot-stop)>0:
        return _unavailable(result,"Missing or expired target, structural stop or intraday holding window.")
    hold = int(hold)
    arrival = ctx.now + timedelta(minutes=hold)
    if arrival > bounds[1]:
        return _unavailable(result,"The planned holding window exceeds today's session.")
    result.update(spot=spot,spot_at=at.isoformat(),direction=side,target=target,stop=stop,hold=hold,
                  arrival=arrival.isoformat(),setup=plan.get("setup"),regime=plan.get("regime"),candidates=[])
    if remaining_loss is None:
        capacity = abs(DAILY_STOP)
    else:
        capacity = numeric(remaining_loss)
        if capacity is None or capacity<=0:
            return _unavailable(result,"No verified remaining daily paper loss capacity.")
        capacity = min(abs(DAILY_STOP),capacity)
    listing = contract_expirations(ctx)
    dtes = _dtes(listing,ctx)
    if not dtes:
        return _unavailable(result,"Waiting for a verified listing of expirations.")
    try:
        paths, count, sessions = _market_paths(read.get("candles") or [],ctx.now,plan,spot,hold,bounds[0])
    except (DataError,KeyError,ValueError,TypeError,OverflowError):
        return _unavailable(result,"Insufficient completed price history for the shared path model.")
    end_idx,end_spot,p_target = _terminal_market(paths,spot,side,target,stop)
    shared = paths,end_idx,end_spot,p_target
    result["diagnostics"] = diag = {"training_bars":count,"training_sessions":sessions,"paths":len(end_idx),
        "probability_basis":"Validated prior outcomes for the ticker, DTE band, regime and setup. Each original timestamp contributes one representative to a cohort.",
        "target_before_stop":p_target,"dtes":dtes,"listings":[],"rejected":{}, "fees_per_side":FEE,
        "assumptions":"Conditioned RTH price paths, constant base IV, ask entry, modeled bid exits and fees. "
                      "Estimates use one-minute monitoring and are not a calibrated contract win rate."}
    settings = getattr(getattr(ctx,"webull_store",None),"settings",None) or Settings()
    rate = numeric(os.getenv("RISK_FREE_RATE")) if os.getenv("RISK_FREE_RATE") else numeric(getattr(settings, "risk_free_rate", .04))
    div = numeric(os.getenv("DIVIDEND_YIELD")) if os.getenv("DIVIDEND_YIELD") else numeric(getattr(settings, "dividend_yield", 0.))
    if rate is None or div is None or not -.10<=rate<=1 or not 0<=div<=1:
        return _unavailable(result,"Invalid configured interest rate or dividend yield.")
    right = "call" if side==1 else "put"
    probability_cache = {}
    for dte in dtes:
        expiry = (ctx.session+timedelta(days=dte)).isoformat()
        assessed = set()
        for feed,source in _chain_sources(ctx,expiry,right):
            meta = feed.snapshot.meta if feed.snapshot else {}
            rows = [r for r in feed.rows if r.get("type")==right
                    and str(r.get("contract","")).replace(" ","").upper() not in assessed]
            summary = {"expiry":expiry,"source":source,"returned":len(rows),"eligible":0,
                       "series_discovered":meta.get("discovered"),"series_requested":meta.get("requested")}
            diag["listings"].append(summary)
            if not ctx.preview and (simulated_feed(feed) or source not in ("Webull","Schwab")):
                summary["issue"]="Broker quote coverage unavailable."
                continue
            if feed.error or not feed.snapshot:
                summary["issue"]=getattr(feed.error,"status",None) or "Waiting for chain"
                continue
            receipt = time_or_none(feed.snapshot.received_at)
            if not receipt or receipt > ctx.now:
                summary["issue"]="Chain was not available at the decision timestamp."
                continue
            if meta.get("symbol") not in (None,ctx.ticker) or meta.get("expiry") not in (None,expiry):
                summary["issue"]="Mismatched chain identity."
                continue
            codes=Counter(str(r.get("contract","")).replace(" ","").upper() for r in rows)
            carry = numeric(meta.get("risk_free_rate")) if source=="Schwab" else None
            carry = carry if carry is not None and -.10<=carry<=1 else rate
            for raw in rows:
                try:
                    spec,item = _quote_record(raw,ctx,expiry,source,feed)
                except (ValueError,TypeError):
                    item=dict(contract=str(raw.get("contract","")),source=source,symbol=ctx.ticker,expiry=expiry,
                              reason="Unsupported or mismatched contract specification.",eligible=False)
                    result["candidates"].append(item)
                    continue
                result["candidates"].append(item)
                if codes[spec.code]>1 or raw.get("expiry")!=expiry:
                    item["reason"]="Duplicate or mismatched series."
                elif min(spec.last_trade,spec.expires)<=arrival:
                    item["reason"]="Exit window extends beyond tradable life."
                if item["reason"]:
                    continue
                stamp = time_or_none(item["quote_at"])
                if abs((stamp-at).total_seconds())>MAX_SYNC_GAP:
                    item["reason"]="Option and underlying timestamps are too far apart."
                    continue
                if spread_fraction(raw)>MAX_RELATIVE_SPREAD or item["ask"]*spec.multiplier+FEE>MAX_DEBIT:
                    item["reason"]="Spread or single-contract debit exceeds the paper limit."
                    continue
                try:
                    calibrated=calibrate_quote(spec,raw,spot,at,ctx.now,carry,div,max_quote_age=QUOTE_SECONDS)
                    if not calibrated.iv or not calibrated.midpoint_iv:
                        item["reason"]="No valid midpoint IV from synchronized option and underlying observations."
                        continue
                    item.update(_estimate(spec,item,result,shared,ctx,plan,(FEE,carry,div,MAX_DEBIT,capacity),calibrated))
                except ValueError as exc:
                    item["reason"] = str(exc) if str(exc) in ("Ask size, buying budget or remaining modeled loss capacity cannot cover one contract.", "CRR grid unstable at this carry and IV", "Invalid scenario inputs") else "Option repricing could not produce a valid scenario."
                    continue
                except ArithmeticError:
                    item["reason"]="Option repricing could not produce a finite result."
                    continue
                assessed.add(spec.code)
                if mode=="probability" and item["eligible"]:
                    if dte not in probability_cache:
                        from rank_research import probability
                        probability_cache[dte] = None if ctx.preview or not ctx.journal else probability(
                            ctx.journal,ctx.ticker,ctx.now,VERSION,dte=dte,regime=plan.get("regime"),setup=plan.get("setup"))
                    p = numeric(probability_cache[dte])
                    if p is None or not 0<=p<=1:
                        item.update(eligible=False,reason="No independently validated fixed-horizon markout probability for this cohort.")
                    else:
                        item["probability"]=p
                summary["eligible"]+=int(item["eligible"])
    eligible=sorted((r for r in result["candidates"] if r["eligible"]),key=lambda row:_rank_key(row,mode))
    # If a secondary quote priced a series skipped by the first broker, keep one
    # economically comparable candidate and retain rejected venue counts in coverage.
    chosen = {row["contract"] for row in result["candidates"] if "expected_net" in row}
    result["candidates"] = [row for row in result["candidates"] if row["contract"] not in chosen or "expected_net" in row]
    diag["rejected"] = dict(Counter(r["reason"] for r in result["candidates"] if not r["eligible"]))
    if not eligible:
        top=Counter(diag["rejected"]).most_common(1)
        return _unavailable(result,top[0][0] if top else "No usable broker option chain for the listed expirations.")
    best=eligible[0]
    expires = min(at,time_or_none(best["quote_at"]))+timedelta(seconds=QUOTE_SECONDS)
    if expires<=ctx.now+timedelta(seconds=time.monotonic()-started):
        return _unavailable(result,"Selected contract quote expired while evaluating this snapshot.")
    if len(eligible)>1:
        median_spread = float(np.median([row["spread"] for row in eligible]))
        best["reason"] += f" The median eligible spread is {median_spread:.1%}; this series ranks first by {MODES[mode].lower()}."
    result.update(status=status,reason=f"{best['label']} ranks first by {MODES[mode].lower()} among {len(eligible)} eligible series.",
                  selected=best,alternatives=eligible[1:4],expires_at=expires.isoformat())
    return result
