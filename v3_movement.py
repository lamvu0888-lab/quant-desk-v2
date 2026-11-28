"""Point-in-time movement research. Scores express evidence, never calibrated probabilities."""
from datetime import timedelta
from math import sqrt, tanh
import os
from statistics import median

from early_flow import window
from terminal_data import numeric, time_or_none, feed_has_delay
from market_session import ET, rth_bounds

VERSION = "3.0-shadow.1"
HORIZONS = (3, 5, 10, 15)
MINUTE = timedelta(minutes=1)
THRESHOLDS = {"Aggressive": 55, "Balanced": 65, "Conservative": 75}


def enabled(name, default=False):
    return os.getenv("QD_V3_" + name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def score(value):
    return round(min(100, max(0, value)), 1)


def robust_scale(values, floor):
    center = median(values)
    return max(floor, 1.4826 * median(abs(x-center) for x in values))


def signal(ctx, read, plan, flow, points, cvd_source, gamma):
    now, close = ctx.now, read.get("minute_close")
    scope = str(ctx.session)
    mode = os.getenv("QD_V3_MODE", "Balanced").title()
    threshold = THRESHOLDS.get(mode, 65)
    event = dict(version=VERSION, symbol=ctx.ticker, session=scope, mode=mode if mode in THRESHOLDS else "Balanced",
                 state="INSUFFICIENT DATA", side=0, scores={}, features={}, forecasts={}, price=None,
                 at=close.isoformat() if close else None, processed=now.isoformat(), source=ctx.price_provider,
                 probability=None, reason="Waiting for current, contiguous completed regular-session prices.",
                 action="Monitor independent setups", preview=ctx.preview, regime=plan.get("regime"))
    bounds = rth_bounds(ctx.session)
    if not (bounds and bounds[0] <= now <= bounds[1] and read.get("bars_ready") and close and
            timedelta(0) <= now-close < MINUTE and not ctx.paused):
        return event
    candles = []
    expected = close
    for bar in reversed(read["candles"][-35:]):
        stamp = time_or_none(bar.get("time_utc"))
        if not stamp or stamp < bounds[0] or stamp + MINUTE != expected or bar.get("forming") or not bar.get("closed", True):
            break
        candles.append(bar)
        expected = stamp
    candles.reverse()
    if len(candles) < 7:
        return event
    prices = [b["close"] for b in candles]
    changes = [b-a for a,b in zip(prices, prices[1:])]
    spot, previous = prices[-1], candles[-7:-1]
    scale = robust_scale(changes[:-1], spot*.00012)
    historic=[]
    days=set()
    minute_of_day=close.astimezone(ET).hour*60+close.astimezone(ET).minute
    history = read["candles"][-1601:]
    for a,b in zip(history,history[1:]):
        t=time_or_none(b["time_utc"]); loc=t.astimezone(ET)
        if loc.date()>=ctx.session or abs(loc.hour*60+loc.minute-minute_of_day)>5:continue
        if t-time_or_none(a["time_utc"])==MINUTE and b.get("closed",True) and not b.get("forming"):
            historic.append(b["close"]-a["close"]);days.add(loc.date())
    if len(days)>=3 and len(historic)>=20:
        scale=.65*scale+.35*robust_scale(historic,spot*.00012)
    z = (changes[-1]-median(changes[:-1]))/scale
    velocity = changes[-1]/scale
    impulse = tanh(velocity/2)
    persist = sum(1 if x>0 else -1 if x<0 else 0 for x in changes[-3:])/3
    hi, lo = max(b["high"] for b in previous), min(b["low"] for b in previous)
    last = candles[-1]
    position = max(-1,min(1,(2*spot-hi-lo)/max(scale, hi-lo)))
    breakout = 1 if spot > hi else -1 if spot < lo else 0
    sweep = 1 if last["low"] < lo and spot > lo and changes[-1]>0 else -1 if last["high"]>hi and spot<hi and changes[-1]<0 else 0
    times = [close-i*MINUTE for i in (2,1,0)]
    snap = flow.snapshot
    usable = (snap and not flow.error and not feed_has_delay(flow) and snap.meta.get("interval")=="1m"
              and snap.meta.get("symbol") in (None,ctx.ticker) and snap.meta.get("expiry") in (None,ctx.expiry)
              and not snap.meta.get("delayed") and not snap.meta.get("sandbox")
              and 0 <= (now-snap.received_at).total_seconds() <= 90)
    premium = window(flow.rows, "net_premium_usd", times, starts=True) if usable else []
    delta = window(points, "delta", times)
    def pressure(values):
        if len(values)!=3 or None in values:
            return None
        return sum(values)/max(1,sum(map(abs,values)))
    net, cvd = pressure(premium), pressure(delta)
    volume = [numeric(b.get("volume")) for b in previous]
    vol_med = median(volume) if None not in volume else None
    relvol = last.get("volume") / vol_med if vol_med and last.get("volume") is not None else None
    gex = numeric(gamma.get("total"))
    ranges = [b["high"]-b["low"] for b in candles[:-1]]
    compressed = len(ranges)>=10 and median(ranges[-3:]) < .7*median(ranges[:-3])
    flow_parts = [v for v in (net,cvd) if v is not None]
    balance = sum(flow_parts)/len(flow_parts) if flow_parts else 0
    # Treat net option premium and signed footprint as one capped family.
    regime=plan.get("regime")
    weights=(.40,.25,.15,.20)
    if regime=="Expanding": weights=(.40,.20,.20,.20)
    elif regime=="Compressing": weights=(.30,.30,.15,.25)
    families = [(impulse,weights[0]),(position,weights[1]),(persist,weights[2])]
    if flow_parts: families.append((balance,weights[3]))
    denominator = sum(w for _,w in families)
    bull, bear = [score(100*sum(w*max(0,sign*x) for x,w in families)/denominator) for sign in (1,-1)]
    side = 1 if bull>bear else -1 if bear>bull else 0
    pressure_score = max(bull,bear)
    shock = abs(z)>=3 and abs(velocity)>=2
    ignition = score(max(pressure_score * (.85 + .15*abs(breakout)), 82 if shock else 0))
    continuation = score(50 + 27*side*persist + 18*side*position)
    exhaustion = score(40*(abs(velocity)>2 and abs(persist)<.5) + 32*bool(sweep) + 28*(balance*impulse<-.35))
    cusum_up=cusum_down=0.
    ewma=0.
    for change in changes[-12:]:
        scaled=change/scale
        cusum_up=max(0,cusum_up+scaled-.5)
        cusum_down=max(0,cusum_down-scaled-.5)
        ewma=.35*scaled+.65*ewma
    confidence = score(55+15*(net is not None)+15*(cvd is not None)+10*(relvol is not None)+5*(gex is not None))
    readiness = score(30+25*compressed+20*min(abs(velocity)/2,1)+15*min(relvol or 0,2)/2+10*(gex is not None))
    word = "BULLISH" if side>0 else "BEARISH"
    conflict = (net is not None and cvd is not None and net*cvd<-.55) or balance*impulse < -.65
    failed = (candles[-2]["close"]>max(b["high"] for b in candles[-7:-2]) and spot<max(b["high"] for b in candles[-7:-2]) and velocity < -1
              or candles[-2]["close"]<min(b["low"] for b in candles[-7:-2]) and spot>min(b["low"] for b in candles[-7:-2]) and velocity > 1)
    if conflict: state = "CONFLICTING PRESSURE"
    elif failed: state = "FAILED MOVE"
    elif exhaustion>=60: state = "EXHAUSTION"
    elif ignition>=threshold and side:
        state = word + (" EXPANSION" if abs(persist)==1 and continuation>=85 and not shock else " IGNITION")
    elif pressure_score>=threshold-15 and side: state = word + " PRESSURE"
    else: state = "BUILDING" if readiness>=50 else "QUIET"
    path = ("Failed breakout" if failed else "Liquidity sweep" if sweep else "Sudden impulse" if shock else
            "Compression breakout" if breakout and compressed else "Trend continuation" if abs(persist)==1 else
            "Flow divergence" if conflict else "Momentum acceleration" if abs(velocity)>1 else "Range")
    action = ("Evaluate existing directional setup now" if "IGNITION" in state else
              "Review continuation and position risk" if "EXPANSION" in state else
              "Review invalidation and protected exits" if state in ("FAILED MOVE","EXHAUSTION") else
              "Wait for agreement" if conflict else "Prepare directional plan" if "PRESSURE" in state else event["action"])
    # Barriers depend only on prior changes and spot. Equal definitions used for all challengers.
    forecasts = {str(h): {"distance":round(scale*sqrt(h),6), "upper":spot+scale*sqrt(h),
                           "lower":spot-scale*sqrt(h), "probability":None} for h in HORIZONS}
    from v3_challenger import change_probability
    signed_pressure=bool(flow_parts) and abs(balance)>.7
    absorption=bool(signed_pressure and abs(velocity)<.5)
    if absorption and path=="Range":path="Possible absorption"
    vwap=numeric(plan.get("vwap"))
    features = dict(pressure_weights=list(weights),absorption=absorption,
                    vwap_distance=(spot-vwap)/scale if vwap else None,atm_iv=numeric(plan.get("atm_iv")),
                    realized_volatility=float((sum(v*v for v in changes)/len(changes))**.5/spot),
                    aligned_flow_inputs=len(flow_parts),change_point_mass=change_probability([v/scale for v in changes]),cusum_up=cusum_up,cusum_down=cusum_down,ewma_velocity=ewma,seasonal_sessions=len(days),price_z=z, velocity=velocity, acceleration=(changes[-1]-changes[-2])/scale,
                    persistence=persist, range_position=position, breakout=breakout, sweep=sweep,
                    net_pressure=net, footprint_pressure=cvd, net_change=premium[-1]-premium[-2] if net is not None else None,
                    footprint_change=delta[-1]-delta[-2] if cvd is not None else None,
                    relative_volume=relvol, gex=gex, volatility_scale=scale)
    return {**event,"state":state,"side":side,"scores":dict(readiness=readiness,bullish=bull,bearish=bear,
            ignition=ignition,continuation=continuation,exhaustion=exhaustion,data_confidence=confidence),
            "features":features,"forecasts":forecasts,"price":spot,"type":path,"reason":f"{path} · {state.lower()}",
            "action":action,"invalidation":lo if side==1 else hi if side==-1 else None,
            "quality":{"price":"observed completed 1m","netflow":"observed" if net is not None else "unavailable",
                       "cvd":cvd_source if cvd is not None else "unavailable","gamma":"modeled" if gex is not None else "unavailable",
                       "book":"unavailable","cross_market_lead":"unvalidated"}}


def record(journal, ctx, read, plan):
    sig = plan.get("movement_v3")
    if not sig or ctx.preview or not enabled("RECORD") or not sig["at"]:
        return None
    key = f"v3:{sig['version']}:{sig['mode']}:{ctx.ticker}:{ctx.scope}:{sig['at']}:{ctx.price_provider}"
    fresh = read.get("bars_ready") and read["minute_close"] and timedelta(0) <= ctx.now-read["minute_close"] < MINUTE
    bar = read["candles"][-1] if fresh and read["candles"] else {}
    if bar and (bar.get("forming") or not bar.get("closed",True)):
        bar = {}
    feed = read["bars"].snapshot
    watch=plan.get("price_watch") or {}
    data = dict(legacy_warning={"UP":1,"DOWN":-1}.get(watch.get("direction"),0),signal=sig, ohlcv={k:bar.get(k) for k in ("open","high","low","close","volume")} if bar else None,
                received=feed.received_at.isoformat() if feed else None, decision=plan["status"], score=plan["score"],
                blockers=list(plan.get("blocks",[]))+list(plan.get("missing",[])), option=plan.get("option_quote"),
                quote_at=plan.get("quote_at"), flow_at=plan.get("flow_at"), scope=ctx.scope,
                options=(plan.get("v3_contracts") or {}).get("alternatives",[]),
                setup=plan.get("setup"), model=plan.get("rule_version"), gamma_levels=plan.get("gamma_levels"),
                quote_samples=plan.get("v3_quote_samples",[]),chosen=(plan.get("v3_contracts") or {}).get("choice"),
                context={k:plan.get(k) for k in ("atm_iv","iv_ratio","iv_valuation","gex","vwap","ema","break_direction","cvd_proxy","flow_confirmation","adaptive_exit")})
    if sig["price"] is None:
        journal.insert(key+":unavailable",ctx.ticker,"v3_health",sig["session"],ctx.now,data)
        return None
    if journal.insert(key,ctx.ticker,"v3_scan",sig["session"],ctx.now,data):
        return key
    return None


def transition(journal,ctx,plan,ident):
    sig=plan["movement_v3"]
    old=journal.records(ctx.ticker,"v3_transition",scope=sig["session"]+"|"+ctx.scope,limit=1)
    before=old[0]["payload"] if old else {}
    if (before.get("invalidation") is not None and before.get("side") and sig.get("price")
            and before.get("state","").endswith(("IGNITION","EXPANSION"))
            and before["side"]*(sig["price"]-before["invalidation"])<0):
        sig={**sig,"state":"INVALIDATED","action":"Review structural invalidation of the prior movement"}
    if before.get("state")==sig["state"] and before.get("source")==sig["source"]:return None
    key=ident+":transition"
    if not journal.insert(key,ctx.ticker,"v3_transition",sig["session"]+"|"+ctx.scope,ctx.now,sig):
        return None
    if not enabled("DISCORD") or sig["state"] in ("QUIET","INSUFFICIENT DATA"):return None
    return key,dict(symbol=ctx.ticker,status="V3 WATCH",movement_state=sig["state"],source=sig["source"],
                    triggered_at_utc=sig["processed"],bar=sig["at"],price=sig["price"],explanation=sig["action"])


def provisional(journal,ctx,prices):
    """Only compare two genuinely distinct observed quotes. No synthetic five-second candles."""
    if not enabled("RECORD") or not enabled("PROVISIONAL") or ctx.preview or not prices.get("bars_ready"):
        return None
    q=ctx.chart_quote
    if not q or not 0<=(ctx.now-q["at"]).total_seconds()<=15:return None
    scope=f"{ctx.session}|{ctx.price_provider}"
    prior=journal.records(ctx.ticker,"v3_quote",scope=scope,limit=1)
    if prior and time_or_none(prior[0]["payload"]["at"])>=q["at"]:return None
    bars=prices["candles"][-22:]
    if len(bars)<7:return None
    scale=robust_scale([b["close"]-a["close"] for a,b in zip(bars,bars[1:])],q["price"]*.00012)
    payload={"at":q["at"].isoformat(),"processed":ctx.now.isoformat(),"source":q["source"],
             "price":q["price"],"state":"OBSERVED QUOTE","version":VERSION,"side":0}
    if prior:
        prev=prior[0]["payload"]
        elapsed=(q["at"]-time_or_none(prev["at"])).total_seconds()
        if 0<elapsed<=30:
            change=q["price"]-prev["price"]
            payload.update(seconds=elapsed,change=change)
            if abs(change)>2.5*scale:
                payload.update(state="PROVISIONAL IMPULSE",side=1 if change>0 else -1)
    ident=f"v3quote:{ctx.ticker}:{scope}:{payload['at']}"
    return payload if journal.insert(ident,ctx.ticker,"v3_quote",scope,ctx.now,payload) else None
