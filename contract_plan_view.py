"""Read-only presentation of a timestamped Trade Plan contract recommendation."""
import sqlite3

import psycopg
import streamlit as st

from terminal_data import et_time, numeric, time_or_none
from terminal_style import kpis

RANK_LABELS = {"risk_adjusted": "Risk adjusted", "net_profit": "Expected net profit",
               "probability": "Validated cohort probability", "net_return": "Expected net return",
               "conservative": "Conservative"}


def rank_mode():
    from contract_recommendation import configured_mode
    default = configured_mode()
    with st.expander("Contract comparison settings"):
        mode = st.selectbox("Ranking objective for this view", list(RANK_LABELS),
                            index=list(RANK_LABELS).index(default) if default in RANK_LABELS else 0,
                            format_func=RANK_LABELS.get, key="contract_ranking_mode",
                            help="The worker uses its saved ranking objective. This selector changes this analysis.")
        if mode == "probability":
            st.caption("Historical share of positive fixed-horizon net marks for this group, available only after validation.")
        return mode


def money(n):
    n = numeric(n)
    return "Unavailable" if n is None else f"${n:,.2f}"


def percent(n):
    n = numeric(n)
    return "Unavailable" if n is None else f"{n:.1%}"


def number(value, digits=2):
    value = numeric(value)
    return "Unavailable" if value is None else f"{value:.{digits}f}"


def pinned_positions(ctx):
    """Read the actual baseline paper ledger without initializing it or changing its state."""
    from gemini_advisor import BASELINE
    from paper_engine import PaperLedger
    if ctx.preview or not ctx.journal:
        return []
    ledger = PaperLedger(ctx.journal)
    try:
        with ctx.journal.connection() as db:
            rows = ledger.rows(db, "WHERE state='OPEN'")
    except (sqlite3.Error, psycopg.Error, OSError):
        st.caption("Paper position status unavailable from this journal.")
        return []
    return [row for row in rows if row.get("symbol") == ctx.ticker and row.get("track", BASELINE) == BASELINE]


def position_panel(trade, now):
    selected = ((trade["plan"].get("contract_recommendation") or {}).get("selected") or {})
    st.markdown(f"**{selected.get('label') or trade['contract']}** · {trade['remaining']} open · entry {money(trade['entry_ask'])}")
    if proxy := trade["plan"].get("futures_cvd"):
        from schwab_futures import proxy_caption
        if caption := proxy_caption(proxy):
            st.caption(f"At entry · {caption}")
    mark = trade.get("position_scenario") or {}
    at = time_or_none(mark.get("at") or trade.get("mark_at"))
    age = (now - at).total_seconds() if at else None
    fresh = age is not None and 0 <= age <= 30
    st.caption(f"{'Fresh bid' if fresh else 'Historical bid'} {money(mark.get('bid', trade.get('mark_bid')))} · {et_time(at)}")
    if not mark or not fresh:
        st.caption("Updated estimates require synchronized underlying and option quotes.")
        return
    st.write(f"Modeled target {money(mark['target_bid'])} · modeled stop {money(mark['stop_bid'])} · "
             f"remaining upside {money(mark['remaining_upside'])} · remaining reward/risk {number(mark['reward_risk'])}.")
    change = numeric(mark.get("iv_change"))
    iv_change = f" ({100*change:+.2f} points from entry)" if change is not None else ""
    st.caption(f"Midpoint-fitted IV {percent(mark.get('iv'))}{iv_change} · Theta {number(mark.get('theta_per_day'))} per day · "
               f"continuation {number(mark.get('continuation'), 0)} · exhaustion {number(mark.get('exhaustion'), 0)}")


def contract_panel(ctx, plan):
    rec = plan.get("contract_recommendation")
    if not rec:
        return
    row = rec.get("selected")
    st.subheader("Option contract")
    if not row:
        st.info(rec.get("reason") or "Waiting for a qualified direction and usable option quotes.")
        return
    until = time_or_none(rec.get("expires_at"))
    if not until or ctx.now >= until:
        st.info(f"The recommendation for {row['label']} expired. Waiting for new executable quotes.")
        return
    from contract_workspace import open_recommendation
    entry = plan["status"] in ("ENTER LONG", "ENTER SHORT") and rec["status"] == "qualified"
    label = "Preview scenario" if ctx.preview else "Qualified paper setup" if entry else "Developing candidate"
    st.markdown(f"**{row['label']}** · {label}")
    st.caption(f"{row['source']} · quote {et_time(row['quote_at'])} · evaluated {et_time(rec['origin'])} · "
               f"{row['quantity']} contract(s) · {row['expiry']}")
    if rec.get("daily_capacity_verified") is False:
        st.caption(f"{rec.get('capacity_issue') or 'Paper capacity unverified'}. Quantity and debit are scenario estimates.")
    elif rec.get("daily_capacity") is not None:
        st.caption(f"Available modeled daily loss capacity at evaluation: {money(rec['daily_capacity'])}.")
    target = row.get("target_premium")
    stop = row.get("stop_premium")
    kpis([("Bid / ask", f"{money(row['bid'])} / {money(row['ask'])}", f"Budgeted debit {money(row['cost'])}", ""),
          ("At target", money(target), f"Modeled net {money(row.get('target_net'))}", ""),
          ("At stop", money(stop), f"Modeled net {money(row.get('stop_net'))}", ""),
          ("Estimated net", money(row.get("expected_net")), f"Modeled return {percent(row.get('expected_return'))}", "")], small=True)
    st.write(row.get("reason") or rec.get("reason") or "Highest eligible result for this ranking objective.")
    st.caption(f"Entry limit {money(row['ask'])} (price improvement permitted) · hold {rec['hold']} min · maximum debit at risk {money(row.get('max_loss'))} · "
               f"target {rec['target']:,.2f} · stop {rec['stop']:,.2f}. A stop fill depends on the next executable bid.")
    st.button("Analyze contract", key="trade_analyze_contract", on_click=open_recommendation,
              args=(ctx.ticker, row["expiry"], row["type"], row["contract"], rec["target"], rec["arrival"], row["ask"]),
              kwargs={"preview": ctx.preview, "invalidation": plan.get("invalidation")})
    with st.expander("Contract comparison and assumptions"):
        st.caption(f"Contract score {number(row.get('score'), 0)}/100 · estimated reward/risk {number(row.get('risk_reward'))} · "
                   f"modeled downside {money(row.get('modeled_loss'))} · modeled tail loss {money(row.get('tail_loss'))} · "
                   f"Delta {number(row.get('delta'))} · Gamma {number(row.get('gamma'), 4)} · Theta {number(row.get('theta'))} per calendar day · "
                   f"IV {percent(row.get('iv'))}. Position size is checked again against the paper ledger before a fill.")
        for alt in rec.get("alternatives", [])[:3]:
            st.write(f"**{alt['label']}** · ask {money(alt.get('ask'))} · estimated net {money(alt.get('expected_net'))}")
        candidates = rec.get("candidates", [])
        if candidates:
            st.dataframe([{"Contract": r.get("label") or r.get("contract"), "Ask": money(r.get("ask")),
                           "Expected net": money(r.get("expected_net")), "Assessment": r.get("reason")}
                          for r in candidates], hide_index=True, width="stretch", height=245)
