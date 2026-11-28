"""Read private V3 Parquet archives and run a local, matched-session comparison."""
import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from v3_research import SYMBOLS, benchmark, matured, market_events, pricing_errors
from v3_challenger import threshold_search


def bootstrap_difference(days, seed=1979):
    """Paired session-block bootstrap, preserving within-day dependence and class counts."""
    if len(days)<10:
        return {"sessions":len(days),"status":"At least ten paired sessions required"}
    counts=np.asarray(days,dtype=float)
    rng=np.random.default_rng(seed)
    draws=counts[rng.integers(0,len(days),size=(2000,len(days)))].sum(1)
    def calculate(x):
        return (x[...,1]-x[...,0])/x[...,2]
    draws=draws[draws[:,2]>0]
    if len(draws)<1900:
        return {"sessions":len(days),"status":"Insufficient event coverage"}
    return {"sessions":len(days),"baseline_correct":int(counts[:,0].sum()),"v3_correct":int(counts[:,1].sum()),
            "directional_origins":int(counts[:,2].sum()),"recall_difference":float(calculate(counts.sum(0))),
            "interval95":list(map(float,np.quantile(calculate(draws),[.025,.975]))),"draws":len(draws)}


def archive_rows(path):
    seen=set()
    for batch in pq.ParquetFile(path).iter_batches(batch_size=128,columns=["id","origin","payload"]):
        for row in batch.to_pylist():
            if row["id"] in seen:continue
            seen.add(row["id"])
            row["payload"]=json.loads(row["payload"])
            row["scope"]=row["payload"]["signal"]["session"]
            yield row


def evaluate(root,symbol):
    files=sorted(Path(root).glob(f"*-{symbol}.parquet"))
    result={"symbol":symbol,"days":[]}
    labels=[];daily_counts=[]
    for path in files:
        records=list(archive_rows(path))
        if not records:continue
        scopes={r["scope"] for r in records}
        if len(scopes)!=1 or any(r["payload"]["signal"]["symbol"]!=symbol for r in records):
            raise ValueError("Archive contains mixed symbols or sessions")
        session=scopes.pop()
        if path.name!=f"{session}-{symbol}.parquet":raise ValueError("Archive identity differs from filename")
        cases=matured(records)
        labels.extend(r for r in cases if r["horizon"]==5)
        directional=[r for r in cases if r["horizon"]==5 and r["label"]]
        counts=[sum(r["legacy_warning"]==r["label"] for r in directional),
                sum(r["side"]==r["label"] and r["state"].endswith(("IGNITION","EXPANSION")) for r in directional),len(directional)]
        daily_counts.append(counts)
        independent=market_events(records,[])
        result["days"].append({"session":session,"recorded":len(records),"events":len(independent["events"]),
                               "ambiguous":independent["ambiguous"],"eligible_origins":independent["eligible_origins"],
                               "pricing":pricing_errors(records)})
    result["paired_origin_recall"]=bootstrap_difference(daily_counts)
    result["development_benchmark"]=benchmark(labels)
    result["threshold_sensitivity"]=threshold_search(labels)
    result["promotion"]=False
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_directory",type=Path)
    parser.add_argument("--symbol",choices=SYMBOLS,required=True)
    args=parser.parse_args()
    if not args.archive_directory.is_dir():parser.error("Provide a readable private archive directory.")
    print(json.dumps(evaluate(args.archive_directory,args.symbol),allow_nan=False,indent=2))
