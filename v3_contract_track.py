"""Shadow selection against the already fetched exact-expiry chain."""
from datetime import timedelta
from option_quotes import fresh_option_rows
from v3_options import research_rank


def comparison(ctx,feed,sig,plan):
    from paper_engine import MAX_DEBIT
    side=sig["side"];spot=sig["price"]
    arrival=ctx.now+timedelta(minutes=5)
    target=plan.get("tp1")
    if plan.get("direction")!=side or not target or side*(target-spot)<=0:
        target=sig["forecasts"]["5"]["upper" if side==1 else "lower"]
    eligible=fresh_option_rows(feed,ctx.ticker,ctx.expiry,ctx.now,max_age=ctx.option_max_age,paused=ctx.paused)
    by_code={r.get("contract"):r for r in eligible if r.get("type")==("call" if side==1 else "put")}
    ranked=research_rank(by_code.values(),spot,target,arrival,ctx.now,.04,0.,MAX_DEBIT,.65,ctx.ticker)
    quotes=[]
    for rank in ranked[:8]:
        row=by_code[rank["contract"]]
        sample={k:row.get(k) for k in ("contract","symbol","expiry","strike","type","iv","bid","ask","updated_at","delta","gamma","vega","theta")}
        sample.update(arrival=arrival.isoformat(),target=target,forecast=rank["fair_value"],conditional_pnl=rank["conditional_pnl"])
        quotes.append(sample)
    return dict(research_only=True,choice=ranked[0]["contract"] if ranked and ranked[0]["conditional_pnl"]>0 else None,
                target=target,arrival=arrival.isoformat(),alternatives=quotes,
                reason="Compared positive conditional P&L after spread and fees. Portfolio risk and net EV remain independent checks.")
