"""Prospective convergence refinement of the retained first climate diagnostic.

All nine optimization cases receive the same zero requested relative MIP gap.
Unchanged audited feedback baselines are reused by identity. This is a numerical
follow-up after seeing run_01, not a new holdout or a new physical experiment.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
from time import perf_counter

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_three_city_climate_dispatch as base


def freeze(prior, out):
    base.verify(prior)
    base.require(not out.exists(), "output exists")
    protocol = deepcopy(base.read(prior / "protocol.json"))
    protocol.update(frozen_utc=datetime.now(timezone.utc).isoformat(), mip_gap_target=0.,
        refinement_reason="run_01 Urumqi primary carbon incumbent was about0.26047kg above a known feasible feedback policy within requested1e-4 relative gap; uniformly tighten all nine optimization cases, keep all earlier evidence",
        baseline_reused_by_identity=True, independent_confirmation=False,
        reference_run=prior.relative_to(base.ROOT).as_posix(),
        known_feasible_bound_gate="endpoint returned cost/carbon <= feedback corresponding objective + declared primary allowance +1e-6; also check primary anchor; never convert MIP gap into a different KPI interval")
    protocol["code_sha256"][Path(__file__).relative_to(base.ROOT).as_posix()] = base.sha(Path(__file__))
    for filename in ("manifest.json", "comparison.json"):
        path = prior / filename
        protocol["input_sha256"][path.relative_to(base.ROOT).as_posix()] = base.sha(path)
    for path in prior.glob("results/*/*"):
        if path.name in ("chronology.csv", "common_config.yaml", "baseline_delta0.csv", "baseline_audit.json"):
            protocol["input_sha256"][path.relative_to(base.ROOT).as_posix()] = base.sha(path)
    out.mkdir(parents=True)
    base.save(out / "protocol.json", protocol)
    base.save(out / "protocol_lock.json", {"protocol.json": base.sha(out / "protocol.json")})
    print("Refinement protocol frozen: " + base.sha(out / "protocol.json"))


def run(out):
    protocol = base.verify(out)
    prior = base.ROOT / protocol["reference_run"]
    base.require(not (out / "results").exists(), "preserve existing run")
    (out / "results").mkdir()
    originals = base.read(prior / "comparison.json")
    rows, failures, started = [], [], perf_counter()
    for city in base.CITIES:
        folder = out / "results" / city
        folder.mkdir()
        frame = pd.read_csv(prior / "results" / city / "chronology.csv")
        config = yaml.safe_load((prior / "results" / city / "common_config.yaml").read_text(encoding="utf-8"))
        baseline = next(row for row in originals if row["city"] == city and row["case"] == "baseline_delta0")
        rows.append({**baseline, "origin": "unchanged_audited_run_01_feedback"})
        endpoints = {}
        for name, primary in (("cost_endpoint", "total_cost"), ("carbon_endpoint", "emissions"), ("midpoint_cap", None)):
            candidate, cap = deepcopy(config), None
            if name == "midpoint_cap":
                if len(endpoints) != 2:
                    failures.append({"city": city, "case": name, "reason": "missing accepted endpoint"})
                    continue
                low, high = endpoints["carbon_endpoint"], endpoints["cost_endpoint"]
                if low > high + 1e-6 + abs(high) * 1e-8:
                    failures.append({"city": city, "case": name, "reason": "invalid endpoint order"})
                    continue
                requested = (low + high) / 2
                cap = requested + 1e-7 + abs(requested) * 1e-8
                candidate["planning"]["emissions_cap_kg_per_year"] = cap
                base.save(folder / "midpoint_cap_rule.json", {"requested_cap_kg": requested, "applied_cap_kg": cap})
            try:
                result, audit = base.run_one(name, candidate, frame, folder, 60, 0., primary=primary)
                trajectory = result.building_trajectory.rename(columns={"temperature_start_c": "T_start", "temperature_end_c": "T_end", "cooling_kw": "cool_kw"})
                metrics = base.independent_metrics(candidate, frame, trajectory, cap=cap, summary=result.summary)
                if primary:
                    key = "bill_yuan" if primary == "total_cost" else "carbon_kg"
                    allowance = result.summary["primary_objective_allowance"]
                    metrics["checks"]["known_feasible_primary_bound"] = result.summary["primary_incumbent_value"] <= baseline[key] + 1e-6
                    metrics["checks"]["known_feasible_returned_bound"] = metrics[key] <= baseline[key] + allowance + 1e-6
                metrics["passed"] = all(metrics["checks"].values())
                base.save(folder / name / "independent_ledger_audit.json", metrics)
                base.require(metrics["passed"], "independent state/ledger/known feasible bound audit failed")
                if primary:
                    endpoints[name] = metrics["carbon_kg"]
                rows.append({"city": city, "case": name, "result_attribute": "fixed_existing_assets_optimized_dispatch",
                    "solution_quality": result.summary["solution_quality"], "solver_status": result.summary["solver_status"],
                    "mip_gap": result.summary.get("mip_gap"), "wall_seconds": audit["wall_seconds"], **metrics})
            except Exception as error:
                failures.append({"city": city, "case": name, "reason": str(error)})
            print(f"{city}/{name}: rows={len(rows)}, failures={len(failures)}", flush=True)
    base.save(out / "comparison.json", rows)
    pd.json_normalize(rows).to_csv(out / "comparison.csv", index=False)
    base.save(out / "failures.json", failures)
    base.verify(out)
    base.save(out / "manifest.json", {"all_requested_cases_passed": not failures and len(rows) == 12,
        "accepted_rows": len(rows), "failures": failures, "wall_seconds": perf_counter() - started,
        "baseline_rows_reused": 3, "new_optimization_cases": 9, "free_investment_variables": 0,
        "files": {p.relative_to(out).as_posix(): base.sha(p) for p in sorted(out.rglob("*")) if p.is_file()}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "verify"))
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "freeze":
        if args.prior is None:
            parser.error("--prior is required for freeze")
        freeze(args.prior.resolve(), args.out.resolve())
    elif args.action == "run":
        run(args.out.resolve())
    else:
        base.verify(args.out.resolve())
