"""Bounded post-close research feedback. External processing is explicit opt-in."""
import json
import math
import os
from uuid import uuid4

import requests

from v3_movement import enabled

MODEL="gpt-5-mini"
INPUT_RATE=.25/1_000_000
OUTPUT_RATE=2/1_000_000
MAX_OUTPUT=2500
BOUNDS={"ignition_threshold":(50,85),"persistence_minutes":(1,5),"cooldown_minutes":(1,15)}
CURRENT={"ignition_threshold":65,"persistence_minutes":3,"cooldown_minutes":3}
FIELDS=("summary","weakness","evidence","ticker","regime","uncertainty","downside","required_test","promotion","rollback","recommendation")


def schema():
    props={key:{"type":"string"} for key in FIELDS}
    props["regime"]={"type":"string","enum":["ALL","Expanding","Compressing","Normal","Unknown"]}
    props.update(parameter={"type":"string","enum":list(BOUNDS)},current={"type":"number"},proposed={"type":"number"})
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
            or type(data["current"]) not in (int,float) or data["current"]!=CURRENT[key]):
        raise ValueError("Invalid parameter value")
    return data


def reserve(journal,day,amount):
    ident="openai:"+str(day)
    with journal.connection() as db:
        if journal.dsn: db.execute("SELECT pg_advisory_xact_lock(73851402)")
        else: db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS qdv3_usage (id TEXT PRIMARY KEY,day TEXT NOT NULL,amount REAL NOT NULL,status TEXT NOT NULL)")
        sql=journal.sql
        for prefix,key,default in ((str(day),"DAILY",5.),(str(day)[:7],"MONTHLY",150.)):
            limit=float(os.getenv("QD_V3_OPENAI_"+key+"_USD",default))
            spent=db.execute(sql("SELECT COALESCE(SUM(amount),0) FROM qdv3_usage WHERE day LIKE ?"),(prefix+"%",)).fetchone()[0]
            if not math.isfinite(limit) or limit<=0 or spent+amount>limit:return False
        return db.execute(sql("INSERT INTO qdv3_usage VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING"),
                          (ident,str(day),amount,"reserved")).rowcount==1


def budget_status(journal,day):
    with journal.connection() as db:
        table = (db.execute("SELECT to_regclass('qdv3_usage')").fetchone() if journal.dsn else
                 db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='qdv3_usage'").fetchone())
        if not table or not table[0]:return []
        output=[]
        for label,prefix in (("Session",str(day)),("Month",str(day)[:7])):
            rows=db.execute(journal.sql("SELECT status,SUM(amount) FROM qdv3_usage WHERE day LIKE ? GROUP BY status"),(prefix+"%",)).fetchall()
            amounts=dict(rows)
            output.append({"OpenAI usage":label,"Reserved USD":amounts.get("reserved",0),"Reconciled USD":amounts.get("reconciled",0)})
        return output


def feedback(journal,day,report,*,transport=requests.post):
    if not (enabled("OPENAI") and enabled("EXTERNAL_AGGREGATES") and os.getenv("OPENAI_API_KEY")):
        return {"status":"Disabled or external aggregate permission missing"}
    # Only round-trip our own aggregate fields; never serialize rows, trade text, environment or credentials.
    aggregate={"session":str(day),"symbols":{s:{k:v for k,v in block.items() if k in ("recorded","unavailable","independent_events","model_metrics","heldout","horizons","pricing","episodes","paper","profile")}
               for s,block in report.items() if s in ("SPY","SPX","QQQ")},"current":CURRENT}
    if not any(x.get("recorded") for x in aggregate["symbols"].values()):return {"status":"No observations"}
    content=json.dumps(aggregate,allow_nan=False)
    if len(content)>24000: return {"status":"Report too large"}
    prompt="Review only these aggregate counts. Recommend one bounded research experiment. Never change risk, execution or production. Return the required fields. "+content
    cost=(len(prompt.encode())+len(json.dumps(schema()).encode())+1024)*INPUT_RATE+MAX_OUTPUT*OUTPUT_RATE
    if not reserve(journal,day,cost):return {"status":"Already reserved or budget unavailable"}
    try:
        response=transport("https://api.openai.com/v1/responses",headers={"Authorization":"Bearer "+os.environ["OPENAI_API_KEY"]},
                           json={"model":MODEL,"store":False,"input":prompt,"max_output_tokens":MAX_OUTPUT,"reasoning":{"effort":"minimal"},
                                 "text":{"format":{"type":"json_schema","name":"research_review","strict":True,"schema":schema()}}},
                           timeout=45)
        response.raise_for_status()
        obj=response.json()
        usage=obj["usage"]
        actual=int(usage["input_tokens"])*INPUT_RATE+int(usage["output_tokens"])*OUTPUT_RATE
        if actual<0: raise ValueError("Bad usage")
        with journal.connection() as db:
            db.execute(journal.sql("UPDATE qdv3_usage SET amount=?,status='reconciled' WHERE id=?"),(actual,"openai:"+str(day)))
        if obj.get("status")!="completed":return {"status":"Incomplete response","cost_usd":actual}
        text="".join(p["text"] for item in obj["output"] for p in item.get("content",[]) if p.get("type")=="output_text")
        return {"status":"Pending offline evaluation","proposal":validate(json.loads(text)),"cost_usd":actual}
    except (ValueError,KeyError,TypeError,requests.RequestException):
        return {"status":"Rejected or unconfirmed response; consult usage ledger"}


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
