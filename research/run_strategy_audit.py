"""Run the supplied Strategy Lab over completed sessions and export a dashboard audit."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path


def parameter_grid(strategy, kind):
    """Small, predeclared grids of settings the corresponding module consumes."""
    if strategy in {"MR02", "MR05", "MR07", "MR08", "MR09", "MR10", "MR11",
                    "MR12", "MR13", "MR14", "MR15", "MR16", "MR17", "MR18", "MR20"}:
        return ({"lookback": 15, "threshold": 1.5}, {"lookback": 30, "threshold": 2.5})
    if strategy in {"MR06", "TF01", "TF07", "TF09", "TF10", "TF12", "TF15", "TF16", "TF18", "TF20"}:
        return ({"lookback": 12}, {"lookback": 30})
    if strategy == "TF02":
        return ({"fast": 5, "slow": 13}, {"fast": 10, "slow": 21})
    if strategy == "TF19":
        return ({"fast": 5, "slow": 13, "lookback": 15}, {"fast": 10, "slow": 21, "lookback": 30})
    if strategy in {"TF14", "TF17"}:
        return ({"fast": 5}, {"fast": 13})
    if strategy in {"TF06", "TF13"}:
        return ({"atr_multiple": 2.0}, {"atr_multiple": 4.0})
    if strategy == "TF11":
        return ({"lookback": 10, "roc_threshold": .001}, {"lookback": 30, "roc_threshold": .0025})
    if strategy == "MR01":
        return ({"lookback": 15, "deviation": .003}, {"lookback": 30, "deviation": .007})
    if strategy in {"ML01", "ML03", "ML07", "ML09", "ML11", "ML12", "ML16", "ML17", "ML18"}:
        return ({"probability": .53, "ml_refit": 60}, {"probability": .58, "ml_refit": 100})
    if strategy in {"ML02", "ML04", "ML05", "ML08", "ML19", "ML20"}:
        return ({"prediction_threshold": .0001}, {"prediction_threshold": .0004})
    if kind == "allocation" and strategy in {"PR01", "PR02", "PR05", "PR08", "PR09", "PR13",
                                           "PR14", "PR15", "PR16", "PR20"}:
        return ({"portfolio_window": 30}, {"portfolio_window": 80})
    return ()


def measured(result):
    metrics = result.get("metrics", {})
    return {name: metrics[name] for name in ("net_pnl", "max_drawdown", "trades", "fills", "sessions")
            if name in metrics}


def activity(metrics):
    return max(metrics.get("trades", 0), metrics.get("fills", 0) / 2)


def run_folds(ds, sessions, spec, attempt, risk):
    configs = [({}, {}), *((parameters, {}) for parameters in parameter_grid(spec.id, spec.kind))]
    if spec.kind in ("signal", "model"):
        configs += [({}, {"stop_atr": 1.5, "target_atr": 2.5, "max_hold": 15}),
                    ({}, {"stop_atr": 2.5, "target_atr": 4.0, "max_hold": 30})]
    folds = []
    for i in range(2, len(sessions)):
        validation_start, validation_end = sessions[i - 1]
        test_start, test_end = sessions[i]
        before = ds.slice(end=validation_end)
        observed = []
        for parameters, changes in configs:
            result = attempt(before, spec.id, parameters, replace(risk, **changes),
                             start=validation_start, end=validation_end)
            metrics = measured(result)
            score = None
            if result["status"] == "completed" and activity(metrics) >= 4 and "net_pnl" in metrics:
                score = metrics["net_pnl"] - 1.5 * metrics.get("max_drawdown", 0)
            observed.append({"parameters": parameters, "risk": changes, "score": score,
                             "status": result["status"], **metrics})
        eligible = [row for row in observed if row["score"] is not None]
        selected = max(eligible, key=lambda row: row["score"], default=None)
        cut = ds.slice(end=test_end)
        base = attempt(cut, spec.id, risk=risk, start=test_start, end=test_end)
        result = {"validation_end": str(validation_end), "test_start": str(test_start), "test_end": str(test_end),
                  "baseline": measured(base), "baseline_status": base["status"], "trials": observed}
        if selected:
            chosen_risk = replace(risk, **selected["risk"])
            args = (cut, spec.id, selected["parameters"])
            trial = attempt(*args, chosen_risk, start=test_start, end=test_end)
            stress = attempt(*args, replace(chosen_risk, cost_bps=2 * chosen_risk.cost_bps,
                                           order_fee=2 * chosen_risk.order_fee), start=test_start, end=test_end)
            result.update(selection={"parameters": selected["parameters"], "risk": selected["risk"]},
                          selected=measured(trial), selected_status=trial["status"],
                          cost_stress=measured(stress), cost_stress_status=stress["status"])
        folds.append(result)
    return folds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", type=Path, required=True, help="Extracted Strategy_Research_Lab_v3 directory")
    parser.add_argument("--data", type=Path, help="Strategy Lab historical input directory")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("strategy_audit.json"))
    args = parser.parse_args()
    lab = args.lab.resolve()
    sys.path.insert(0, str(lab))
    from researchlab.catalog import CATALOG
    from researchlab.data import load_folder
    from researchlab.engine import Risk
    from researchlab.report import safe
    from researchlab.runner import attempt

    import csv

    ds = load_folder(args.data or lab / "data" / "saved_webull")
    index = ds.primary.index
    groups = {}
    for ts in index:
        groups.setdefault(ts.tz_convert("America/New_York").date(), []).append(ts)
    sessions = [(stamps[0], stamps[-1]) for stamps in groups.values()]
    historical = lab / "reports" / "webull"
    archived = {}
    for key, filename in (("baseline", "comparison.csv"), ("optimization", "optimization_summary.csv")):
        path = historical / filename
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as source:
                archived[key] = {row["id"]: row for row in csv.DictReader(source)}
    risk = Risk()
    rows = []
    same_archive_data = ds.fingerprint() == "f57ca904bbb0fa42893e6fe876efd294c8f6e5c2c1d6b9a9dd0555c04d50cb9d"
    for spec in CATALOG.values():
        baseline = attempt(ds, spec.id, risk=risk)
        row = {**asdict(spec), "status": baseline["status"], "reason": baseline.get("reason"),
               "baseline": measured(baseline), "folds": [], "decision": "Research only"}
        if same_archive_data:
            row["archive_baseline"] = archived.get("baseline", {}).get(spec.id)
            row["archive_optimization"] = archived.get("optimization", {}).get(spec.id)
        if row["status"] == "completed" and "net_pnl" in row["baseline"]:
            row["folds"] = run_folds(ds, sessions, spec, attempt, risk)
        chosen = [fold for fold in row["folds"] if fold.get("selected_status") == "completed"]
        row["selected_folds"] = len(chosen)
        for output, metric in (("selected_pnl", "selected"), ("comparison_pnl", "baseline"), ("stress_pnl", "cost_stress")):
            values = [fold.get(metric, {}).get("net_pnl") for fold in chosen]
            row[output] = sum(values) if values and all(value is not None for value in values) else None
        rows.append(row)
        print(spec.id, row["status"], len(chosen), flush=True)
    report = {"dataset": ds.label, "fingerprint": ds.fingerprint(), "symbols": list(ds.symbols),
              "bars": len(index), "sessions": {str(day): len(stamps) for day, stamps in groups.items()},
              "status_counts": dict(Counter(row["status"] for row in rows)), "strategy_count": len(rows),
              "selected_strategies": sum(row["selected_folds"] > 0 for row in rows),
              "selected_folds": sum(row["selected_folds"] for row in rows),
              "risk": asdict(risk), "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(safe(report), allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
