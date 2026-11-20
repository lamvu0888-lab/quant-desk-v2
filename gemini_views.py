"""Read-only Streamlit views for the shared paper experiment."""
import hashlib
import json

import streamlit as st

from gemini_advisor import AdvisorQueue, BASELINE, SHADOW
from market_session import ET, session_state
from terminal_data import et_time


@st.cache_data(ttl=10, max_entries=80, show_spinner=False)
def session_view(symbol, day, identity, _journal):
    jobs = AdvisorQueue(_journal)
    with _journal.connection() as db:
        daily = jobs.session_plan(db, symbol, day)
        versions = jobs.run(db,"SELECT id,state,input,result FROM qd_gemini_job WHERE kind='plan' AND symbol=? AND session=? ORDER BY created DESC",symbol,day).fetchall()
    scans = _journal.records(symbol, "gemini_scan", scope=day, limit=60)
    return daily, scans, versions


def scenario_panel(ctx):
    with st.expander("Gemini A/B/C paper plan", expanded=True):
        if ctx.preview or ctx.journal is None:
            st.caption("Live scenarios appear here when Quant Desk can read the workers’ shared journal.")
            return
        try:
            identity = hashlib.sha256(f"{ctx.journal.dsn}\0{ctx.journal.path}".encode()).hexdigest()
            at = getattr(ctx,"now",None)
            day = at.astimezone(ET).date() if at and session_state(at)["status"] == "PREMARKET" else ctx.session
            daily, scans, versions = session_view(ctx.ticker, str(day), identity, ctx.journal)
        except Exception:
            st.caption("Gemini journal unavailable. Check the workers’ setup and the dashboard’s read access.")
            return
        if daily and daily["state"] == "ready":
            sample = daily["input"]
            st.caption(f"{sample['symbol']} · {sample['session']} · {sample['source']} snapshot "
                       f"{et_time(sample['as_of'])} · published {et_time(daily['at'])} · {daily['model']}")
            st.write(f"Version {sample.get('revision',1)} · {sample.get('stage','Daily')} · {sample.get('reason','')}")
            rows = [{"Scenario": name, "Setup": leg["setup"], "Trigger": leg.get("trigger"),
                     "Invalidation": leg.get("invalidation"), "Target": leg.get("target"),
                     "5m premium": leg["flow"] if leg["setup"] != "Wait" else None,
                     "Rationale": leg["reason"]} for name, leg in daily["legs"].items()]
            st.dataframe(rows, hide_index=True, width="stretch")
            reviews = sample.get("prior_reviews", [])
            if reviews:
                st.caption(f"This plan received {len(reviews)} earlier paper reviews for {ctx.ticker}.")
                st.dataframe([{"Previous experiment": r["change"], "Evidence": r["finding"],
                               "Measurement": r["measure"]} for r in reviews], hide_index=True, width="stretch")
        elif daily:
            st.info(f"Session plan {daily['state']}. {daily['issue'] or 'Waiting for a validated Gemini response.'}")
        else:
            st.caption("No plan recorded for this ticker and session. The monitor needs fresh completed bars and price levels.")
        if scans:
            items = []
            for row in scans:
                p = row["payload"]
                items.append({"Time ET": et_time(row["origin"]), "Gemini paper": p["status"],
                              "Scenario": p.get("leg"), "Setup": p.get("setup"), "Score": p.get("score"),
                              "Paper checks": " ".join(p["reasons"]) or "Candidate sent to the paper simulator.",
                              "Original decision": p["baseline"], "Original checks": " ".join(p["baseline_reasons"]),
                              "Source": p["source"]})
            st.caption("Recent decisions · latest scan per minute")
            st.dataframe(items, hide_index=True, width="stretch")
        if versions:
            with st.expander("Plan versions and session data"):
                for ident,state,raw,result in versions:
                    request = json.loads(raw)
                    st.write(f"{ident} · {state} · {request.get('reason','Daily plan')}")
                    st.json({"snapshot":request.get("as_of"),"sessions":request.get("sessions",{}),
                             "scenarios":json.loads(result) if state=="ready" and result else {}})


def paper_comparison(trades, table, read_reviews, realized, today, shadow_halted):
    days = sorted({t["session"] for t in trades} | {str(today)}, reverse=True)
    day = st.selectbox("Comparison session (ET)", days)
    day_rows = [t for t in trades if t["session"] == day]
    if shadow_halted and day == str(today):
        st.warning("Gemini paper book reached its daily loss stop.")
    summary = []
    for label in (BASELINE, SHADOW):
        cohort = [t for t in day_rows if t.get("track", BASELINE) == label]
        closed = [t for t in cohort if t["state"] == "CLOSED"]
        summary.append({"Paper strategy": label, "Filled": sum(bool(t.get("opened")) for t in cohort),
                        "Closed": len(closed), "Skipped": sum(t["state"] == "SKIPPED" for t in cohort),
                        "Unresolved": sum(t["state"] == "UNRESOLVED" for t in cohort),
                        "Net closed P/L ($)": round(sum(map(realized, closed)), 2) if closed else None,
                        "Win rate (%)": round(100 * sum(realized(t) > 0 for t in closed) / len(closed), 1) if closed else None})
    st.dataframe(summary, hide_index=True, width="stretch")
    st.caption("Each book has its own daily loss stop. Counts and net closed P/L use the entry session.")
    if day_rows:
        st.write("Entry timing, fills and skips")
        table(list(reversed(day_rows)), compare=True)
    try:
        reviews = read_reviews(day)
    except Exception:
        st.caption("Gemini reviews unavailable. Check the paper worker’s Gemini setup and the dashboard’s journal access.")
        return
    if not reviews:
        st.caption("Reviews appear after a paper position has fully closed with verified exit fills.")
        return
    index = {"review:" + t["id"]: t for t in day_rows}
    rows = []
    for ident, state, model, result, issue in reviews:
        trade = index.get(ident, {})
        answer = json.loads(result) if result else {}
        rows.append({"Exit ET": et_time(trade.get("closed")), "Ticker": trade.get("symbol"),
                     "Paper strategy": trade.get("track", BASELINE), "Setup": trade.get("plan", {}).get("setup"),
                     "Net P/L ($)": realized(trade) if trade else None, "Review": state, "Finding": answer.get("finding"),
                     "Proposed change": answer.get("change"), "Forward test": answer.get("experiment"),
                     "Measure": answer.get("measure"), "Model": model, "Issue": issue})
    st.write("Post-trade reviews")
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("The next session plan can use the five most recent completed reviews from earlier sessions for this ticker.")
