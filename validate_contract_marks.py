"""Offline conditional repricing check for timestamped Quant Desk collector sessions."""
import argparse
from bisect import bisect_left
from datetime import datetime, timedelta
import json
from pathlib import Path
import re

import numpy as np

from terminal_data import positive
from v3_options import YEAR, calibrated_iv, contract_spec, value


def source_spec(code):
    match=re.fullmatch(r"(SPY|QQQ|SPXW|SPXO|SPX)(\d{6})([CP])(\d{8})",code)
    if not match:
        return None
    symbol="SPX" if match[1].startswith("SPX") else match[1]
    return contract_spec(dict(contract=code,symbol=symbol,expiry=datetime.strptime(match[2],"%y%m%d").date().isoformat(),
                              type="call" if match[3]=="C" else "put",strike=int(match[4])/1000),symbol)


def read_pairs(folder):
    events=folder/"collector_events.jsonl"
    ticks=folder/"dry_run_market_ticks.jsonl"
    if not events.is_file() or not ticks.is_file():
        return []
    with events.open(encoding="utf-8") as f:
        starts=[e for line in f if (e:=json.loads(line)).get("event")=="START"]
    if len(starts)!=1:
        return []
    with ticks.open(encoding="utf-8") as f:
        data=[json.loads(line) for line in f]
    stamps=[datetime.fromisoformat(row["at_utc"]) for row in data]
    if not stamps or any(t.tzinfo is None for t in stamps) or any(a>=b for a,b in zip(stamps,stamps[1:])):
        return []
    result=[]
    i=0
    while i<len(data):
        end=stamps[i]+timedelta(minutes=5)
        j=bisect_left(stamps,end,lo=i+1)
        if j==len(data):
            break
        elapsed=(stamps[j]-end).total_seconds()
        if elapsed<=10 and data[i].get("pairs")==data[j].get("pairs")==1:
            for side in ("call","put"):
                code=starts[0].get(side)
                spec=source_spec(code) if isinstance(code,str) else None
                mid=positive(data[i].get(side+"_mid")); next_mid=positive(data[j].get(side+"_mid"))
                spot=positive(data[i].get("spot")); next_spot=positive(data[j].get("spot"))
                if not (spec and mid and next_mid and spot and next_spot and stamps[j]<min(spec.last_trade,spec.expires)):
                    continue
                # Only the origin midpoint enters IV calibration. This diagnostic
                # conditions on the later observed underlying price to isolate repricing.
                iv=calibrated_iv(spec,spot,stamps[i],mid,.04,0.)
                if iv is None:
                    continue
                mark=float(value(spec,next_spot,(spec.expires-stamps[j]).total_seconds()/YEAR,iv,.04,0.))
                result.append((spec.code,stamps[i],side,mark-next_mid,mid-next_mid))
        i=j
    return result


def evaluate(root):
    accepted={}
    folders=0
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        rows=read_pairs(d)
        if rows:
            folders+=1
        for code,stamp,right,error,flat in rows:
            accepted.setdefault((code,stamp),(right,error,flat))
    def metrics(rows):
        a=np.array([(x,y) for _,x,y in rows])
        if not len(a):
            return {"observations":0}
        return {"observations":len(a),"repriced_mae":round(float(np.abs(a[:,0]).mean()),4),
                "carry_mid_mae":round(float(np.abs(a[:,1]).mean()),4),
                "repriced_bias":round(float(a[:,0].mean()),4)}
    return {"collector_sessions":folders,"trading_dates":len({dt.date() for _,dt in accepted}),
            "contracts":len({code for code,_ in accepted}),"all":metrics(accepted.values()),
            "call":metrics([r for r in accepted.values() if r[0]=="call"]),
            "put":metrics([r for r in accepted.values() if r[0]=="put"])}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collector_directory",type=Path)
    args=parser.parse_args()
    print(json.dumps(evaluate(args.collector_directory),indent=2))
