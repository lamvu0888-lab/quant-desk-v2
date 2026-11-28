"""Bounded post-close research feedback. External processing is explicit opt-in."""
import json
import math
from openai_intelligence import IntelligenceError, policy, reserve, structured
import os
from uuid import uuid4


from v3_movement import enabled, THRESHOLDS, active_mode
from rank_profiles import PROFILE_GUIDE, TILTS

BOUNDS={"ignition_threshold":(50,85),"persistence_minutes":(1,5),"cooldown_minutes":(1,15),"contract_rank_profile":(0,len(TILTS)-1)}
CURRENT={"ignition_threshold":65,"persistence_minutes":3,"cooldown_minutes":3,"contract_rank_profile":0}
FIELDS=("summary","weakness","evidence","ticker","regime","uncertainty","downside","required_test","promotion","rollback","recommendation")


def parameters():
    return {**CURRENT, "ignition_threshold": THRESHOLDS[active_mode()]}


def schema():
    props={key:{"type":"string"} for key in FIELDS}
    props["regime"]={"type":"string","enum":["ALL","Expanding","Compressing","Normal","Unknown"]}
    props.update(parameter={"type":"string","enum":list(BOUNDS)},current={"type":"number"},proposed={"type":"number"})
    props["proposed"]["description"] = f"Integer in the selected parameter bounds. contract_rank_profile IDs: {PROFILE_GUIDE}."
    return {"type":"object","properties":props,"required":list(props),"additionalProperties":False}


def validate(data):
    if not isinstance(data,dict) or set(data)!=set(schema()["required"]): raise ValueError("Unexpected proposal fields")
    if any(not isinstance(data[k],str) or not 1<=len(data[k])<=2000 for k in FIELDS):
        raise ValueError("Invalid research field")
    if data["ticker"] not in ("SPY","SPX","QQQ","ALL"): raise ValueError("Unknown ticker")
    if data["regime"] not in schema()["properties"]["regime"]["enum"]:raise ValueError("Unknown regime")
    key=data["parameter"]
    if key not in BOUNDS: raise ValueError("Parameter outside allowlist")
    a,b=BOUNDS[key]; x=data["proposed"]
    if (type(x) not in (int,float) or not math.isfinite(x) or x!=int(x) or not a<=x<=b
            or type(data["current"]) not in (int,float) or data["current"]!=parameters()[key]):
        raise ValueError("Invalid parameter value")
    return data


def budget_status(journal,day):
    with journal.connection() as db:
        table = (db.execute("SELECT to_regclass('qdv3_usage')").fetchone() if journal.dsn else
                 db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='qdv3_usage'").fetchone())
        if not table or not table[0]:return []
        output=[]
        for label,prefix in (("Session",str(day)),("Month",str(day)[:7])):
            rows=db.execute(journal.sql("SELECT status,SUM(amount) FROM qdv3_usage WHERE day LIKE ? GROUP BY status"),(prefix+"%",)).fetchall()
            amounts=dict(rows)
            output.append({"OpenAI usage":label,"Reserved USD":amounts.get("reserved",0) + amounts.get("http_error",0),"Reconciled USD":amounts.get("reconciled",0)})
        return output


# Maximum bytes per aggregate section in a large session report. Each symbol retains
# room for all categories even when another symbol has many option-expiry cohorts.
REPORT_LIMITS = {"recorded":64, "unavailable":64, "independent_events":280, "episodes":500,
                 "paper":460, "abc_outcomes":480, "futures_cvd":800, "horizons":1200,
                 "pricing":950, "contract_selection":1400, "profile":460, "heldout":340,
                 "model_metrics":400}


def wire_size(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())


def sample_count(group):
    if isinstance(group, dict):
        for key in ("n", "matched", "observed_contracts", "origins", "calls", "closed", "holdout_n"):
            if type(group.get(key)) is int:
                return group[key]
    return 0


def bounded_section(value, limit):
    if wire_size(value) <= limit:
        return value
    if not isinstance(value, (dict, list)):
        return {"omitted": "Text exceeds section budget."}
    mapping = isinstance(value, dict)
    items = list(value.items()) if mapping else list(enumerate(value))
    # Preserve totals first; choose the largest observed cohorts with a stable tie break.
    items.sort(key=lambda item:(isinstance(item[1], (list, dict)), -sample_count(item[1]), str(item[0])))
    summary = {} if mapping else []
    def described():
        return {"selection": "Totals then largest available samples", "shown": len(summary),
                "total": len(items), "values": summary}
    for key, item in items:
        # Compact large nested groups individually, preserving room for peers.
        item_limit = max(96, (limit - 100) // min(3, max(1,len(items))))
        if wire_size(item) > item_limit and isinstance(item,(list,dict)) and item_limit < limit:
            item = bounded_section(item, item_limit)
        if mapping:
            summary[key] = item
        else:
            summary.append(item)
        if wire_size(described()) > limit:
            if mapping:
                del summary[key]
            else:
                summary.pop()
    return described()


def research_aggregate(day, report):
    symbols = {s:{k:v for k,v in block.items() if k in REPORT_LIMITS}
               for s,block in report.items() if s in ("SPY","SPX","QQQ")}
    data = {"session":str(day),"symbols":symbols,"current":parameters()}
    if wire_size(data) > 24000:
        data["symbols"] = {s:{k:bounded_section(v, REPORT_LIMITS[k]) for k,v in block.items()}
                           for s,block in symbols.items()}
        data["coverage"] = "Excess detail summarized under per-category byte budgets. Full aggregates remain in the journal."
    return data


def feedback(journal,day,report,*,transport=None,lookup=None,now=None):
    if not (enabled("OPENAI") and enabled("EXTERNAL_AGGREGATES") and os.getenv("OPENAI_API_KEY")):
        return {"status":"Disabled or external aggregate permission missing"}
    # Only round-trip our own aggregate fields; never serialize rows, trade text, environment or credentials.
    try:
        aggregate = research_aggregate(day, report)
    except (TypeError,ValueError,OverflowError):
        return {"status":"Aggregate contains invalid observations"}
    if not any(x.get("recorded") for x in aggregate["symbols"].values()):return {"status":"No observations"}
    prompt = ("Review these aggregate counts and distinguish observed outcomes from forecasts. "
              f"For contract_rank_profile, the only valid experiments are {PROFILE_GUIDE}. "
              "Propose a profile only with matched-outcome evidence. Frozen quotes, Greeks and IV fits are required for their respective profiles. "
              "A missing measurement is unscorable. A fully measured filter declining every candidate represents a zero-dollar no-trade. "
              "Propose one bounded offline experiment. Do not change risk limits or production.")
    try:
        model, effort = policy(os.getenv, "research")
        answer, actual = structured(journal, os.environ["OPENAI_API_KEY"], model, effort,
                                    "research_review", prompt, aggregate, schema(), "openai:"+str(day),
                                    post=transport, lookup=lookup, now=now)
        return {"status":"Pending offline evaluation", "proposal":validate(answer), "cost_usd":actual, "model":model}
    except IntelligenceError as exc:
        return {"status":str(exc)}
    except (ValueError, KeyError, TypeError):
        return {"status":"Rejected invalid research proposal"}


def claim_stage(journal,day,stage,now):
    token=uuid4().hex
    with journal.connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS qdv3_stage (id TEXT PRIMARY KEY,owner TEXT NOT NULL,lease DOUBLE PRECISION NOT NULL,done INTEGER NOT NULL)")
        cur=db.execute(journal.sql("INSERT INTO qdv3_stage VALUES(?,?,?,0) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, lease=excluded.lease "
                                   "WHERE qdv3_stage.done=0 AND qdv3_stage.lease < ?"),
                       (f"{day}:{stage}",token,now.timestamp()+1800,now.timestamp()))
        return token if cur.rowcount==1 else None


def finish_stage(journal,day,stage,token):
    with journal.connection() as db:
        db.execute(journal.sql("UPDATE qdv3_stage SET done=1 WHERE id=? AND owner=?"),(f"{day}:{stage}",token))
