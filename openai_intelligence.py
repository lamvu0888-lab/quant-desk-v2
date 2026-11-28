"""Read-only model discovery and budgeted, schema-constrained OpenAI Responses."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import threading
import time
from typing import NamedTuple

import requests

from market_session import ET

# Standard synchronous short-context USD / million tokens, checked 2026-11-28.
# Each tuple holds uncached input, cached input, 30-minute cache writes, output.
RATES = {"gpt-6.2-sol": (2., .10, 2.50, 10.), "gpt-6.2-astra": (8., .40, 10., 40.)}
API = "https://api.openai.com/v1"
OUTPUT_LIMIT = 3200
MODELS = {}
MODEL_LOCK = threading.Lock()


class IntelligenceError(Exception):
    """Only fixed local status messages and HTTP status codes may reach a dashboard."""


class UnconfirmedRequest(IntelligenceError):
    """A previous request for this logical operation may have reached the provider."""


def policy(get, purpose):
    research = purpose == "research"
    prefix = "QD_AI_RESEARCH_" if research else "QD_AI_ROUTINE_"
    model = get(prefix+"MODEL", "gpt-6.2-astra" if research else "gpt-6.2-sol")
    effort = get(prefix+"EFFORT", "high" if research else "medium")
    if model not in RATES:
        raise IntelligenceError("Unpriced OpenAI model. Choose a documented, priced model from the configuration guide.")
    if effort not in {"low", "medium", "high", "xhigh"}:
        raise IntelligenceError("Unsupported OpenAI reasoning effort.")
    return model, effort


def verify_model(key, model, lookup=None):
    identity = (hashlib.sha256(key.encode()).digest(), model)
    with MODEL_LOCK:
        age = MODELS.get(identity, 0)
        if time.monotonic() < age:
            return
    try:
        response = (lookup or requests.get)(API+"/models/"+model, headers={"Authorization": "Bearer "+key},
                                             timeout=(3, 7), allow_redirects=False)
        if response.status_code != 200:
            raise IntelligenceError(f"OpenAI model access check returned HTTP {int(response.status_code)}.")
        body = response.json()
        if body.get("id") != model or body.get("object") != "model":
            raise IntelligenceError("OpenAI model access could not be verified.")
    except (ValueError, AttributeError, requests.RequestException):
        raise IntelligenceError("OpenAI model access check unavailable.") from None
    with MODEL_LOCK:
        MODELS[identity] = time.monotonic() + 3600


class Receipt(NamedTuple):
    result: str | None
    issue: str | None
    cost: float

    def read(self):
        if self.issue:
            raise IntelligenceError(self.issue)
        return json.loads(self.result), self.cost


def lock_ledger(db, journal):
    if journal.dsn:
        db.execute("SELECT pg_advisory_xact_lock(73851402)")
    else:
        db.execute("BEGIN IMMEDIATE")


def reserve(journal, day, amount, *, ident=None, group=None, get=os.getenv, fingerprint=None):
    if amount <= 0 or not math.isfinite(amount):
        return False
    ident = ident or "openai:"+str(day)
    with journal.connection() as db:
        lock_ledger(db, journal)
        db.execute("CREATE TABLE IF NOT EXISTS qdv3_usage (id TEXT PRIMARY KEY, day TEXT NOT NULL, amount REAL NOT NULL,status TEXT NOT NULL)")
        if fingerprint:
            db.execute("""CREATE TABLE IF NOT EXISTS qdv3_ai_result (
                operation TEXT NOT NULL, fingerprint TEXT NOT NULL, charge_id TEXT UNIQUE NOT NULL,
                result TEXT, issue TEXT, cost DOUBLE PRECISION NOT NULL, PRIMARY KEY(operation,fingerprint))""")
            saved = db.execute(journal.sql("SELECT result,issue,cost FROM qdv3_ai_result "
                                           "WHERE operation=? AND fingerprint=?"), (group or ident,fingerprint)).fetchone()
            if saved:
                return Receipt(*saved)
            if group and db.execute(journal.sql("SELECT 1 FROM qdv3_usage u WHERE substr(u.id,1,?)=? "
                    "AND u.status='reconciled' AND NOT EXISTS (SELECT 1 FROM qdv3_ai_result r "
                    "WHERE r.charge_id=u.id) LIMIT 1"),(len(group),group)).fetchone():
                raise UnconfirmedRequest("A prior attempt was billed without a recoverable local result.")
        if group:
            previous = db.execute(journal.sql("SELECT 1 FROM qdv3_usage WHERE substr(id,1,?)=? AND status='reserved' LIMIT 1"),
                                  (len(group), group)).fetchone()
            if previous:
                raise UnconfirmedRequest("Prior OpenAI delivery unconfirmed. Review the usage ledger.")
        for prefix, key, default in ((str(day), "DAILY", 5.), (str(day)[:7], "MONTHLY", 150.)):
            try:
                limit = float(get("QD_V3_OPENAI_"+key+"_USD", str(default)))
            except (ValueError, TypeError):
                return False
            spent = db.execute(journal.sql("SELECT COALESCE(SUM(amount),0) FROM qdv3_usage WHERE day LIKE ?"),
                               (prefix+"%",)).fetchone()[0]
            if not math.isfinite(limit) or limit <= 0 or spent + amount > limit:
                return False
        return db.execute(journal.sql("INSERT INTO qdv3_usage VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING"),
                          (ident, str(day), amount, "reserved")).rowcount == 1


def usage_cost(model, usage):
    rate, cached_rate, write_rate, output_rate = RATES[model]
    count, out = usage["input_tokens"], usage["output_tokens"]
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0)
    written = details.get("cache_write_tokens", count-cached)
    if (any(type(n) is not int or n < 0 for n in (count, out, cached, written))
            or cached + written > count):
        raise ValueError("Invalid usage")
    return ((count-cached-written)*rate + cached*cached_rate + written*write_rate + out*output_rate)/1e6


def matches_schema(value, schema):
    """Check the subset used by the two strict, locally defined response formats."""
    expected = schema.get("type")
    if expected == "object":
        props = schema.get("properties", {})
        return (isinstance(value, dict) and set(schema.get("required", ())) <= value.keys()
                and (schema.get("additionalProperties", True) or value.keys() <= props.keys())
                and all(matches_schema(value[k],props[k]) for k in value.keys() & props.keys()))
    valid = {"integer": type(value) is int, "number": type(value) in (float,int), "string": isinstance(value,str)}
    return valid.get(expected,False) and ("enum" not in schema or value in schema["enum"])


def completed_result(obj, schema):
    if obj.get("status") != "completed":
        raise IntelligenceError("OpenAI response incomplete.")
    parts = [part for item in obj["output"] for part in item.get("content", ())]
    if any(part.get("type") == "refusal" for part in parts):
        raise IntelligenceError("OpenAI declined this request.")
    text = "".join(part["text"] for part in parts if part.get("type") == "output_text")
    value = json.loads(text)
    if not matches_schema(value,schema):
        raise IntelligenceError("OpenAI result failed the expected response schema.")
    return json.dumps(value,separators=(",", ":"),allow_nan=False)


def structured(journal, key, model, effort, name, instructions, data, schema, ident,
               *, get=os.getenv, post=None, lookup=None, now=None, group=None):
    if not key:
        raise IntelligenceError("OpenAI key unavailable.")
    content = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=True)
    request = {"model": model, "service_tier": "default", "store": False,
               "reasoning": {"effort": effort}, "max_output_tokens": OUTPUT_LIMIT,
               "instructions": instructions, "input": content, "prompt_cache_options": {"ttl": "30m"},
               "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}}
    size = len(json.dumps(request, ensure_ascii=False).encode())
    if size > 48000:
        raise IntelligenceError("Structured AI snapshot exceeds the allowed size.")
    fingerprint = hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    # Use bytes as a conservative token ceiling and allow for protocol framing.
    cap = ((size+2048)*RATES[model][2] + OUTPUT_LIMIT*RATES[model][3])/1e6
    day = (now or datetime.now(timezone.utc)).astimezone(ET).date()
    claim = reserve(journal, day, cap, ident=ident, group=group, get=get, fingerprint=fingerprint)
    if isinstance(claim, Receipt):
        return claim.read()
    if not claim:
        raise IntelligenceError("AI request already reserved or budget unavailable.")
    try:
        verify_model(key, model, lookup)
    except IntelligenceError:
        # Discovery has no inference side effect. Only release this request's own claim.
        with journal.connection() as db:
            db.execute(journal.sql("DELETE FROM qdv3_usage WHERE id=? AND status='reserved'"),(ident,))
        raise
    try:
        response = (post or requests.post)(API+"/responses", headers={"Authorization": "Bearer "+key},
                                          json=request, timeout=(4, 45), allow_redirects=False)
        if response.status_code != 200:
            # Preserve the conservative amount when billed usage is absent. An explicit
            # HTTP error ends this attempt; transport timeouts remain uncertain.
            with journal.connection() as db:
                db.execute(journal.sql("UPDATE qdv3_usage SET status='http_error' WHERE id=?"), (ident,))
            raise IntelligenceError(f"OpenAI HTTP {int(response.status_code)}.")
        obj = response.json()
        actual = usage_cost(model, obj["usage"])
        try:
            receipt = Receipt(completed_result(obj,schema), None, actual)
        except IntelligenceError as exc:
            receipt = Receipt(None,str(exc),actual)
        except (KeyError, TypeError, AttributeError, ValueError):
            receipt = Receipt(None,"Invalid OpenAI response body for this snapshot.",actual)
        # Serializing both writers and readers prevents a second worker observing
        # the reconciled charge before its recoverable answer (or rejection).
        with journal.connection() as db:
            lock_ledger(db, journal)
            db.execute(journal.sql("UPDATE qdv3_usage SET amount=?,status='reconciled' WHERE id=?"),
                       (actual, ident))
            db.execute(journal.sql("INSERT INTO qdv3_ai_result(operation,fingerprint,charge_id,result,issue,cost) "
                                   "VALUES(?,?,?,?,?,?)"), (group or ident,fingerprint,ident,*receipt))
        return receipt.read()
    except requests.RequestException:
        raise UnconfirmedRequest("OpenAI delivery unconfirmed. Review the usage ledger.") from None
    except (KeyError, ValueError, TypeError, AttributeError):
        raise IntelligenceError("Invalid or unconfirmed OpenAI response. Check the usage ledger.") from None
