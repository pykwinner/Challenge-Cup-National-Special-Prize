"""Predeclared pure-C by shared-grid factorial reference; preserve older runs.

The factor C_B/C_A is a research design axis, not measured construction data.
The original ideal equipment, real Wuhan weather, prices and comfort stay fixed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
from time import perf_counter

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from heatplan.model import HeatPlanningModel
from heatplan.io import validate_inputs
from heatplan.time_registry import describe_time_axis
from run_multiscale_reference import input_reference, make_case as original_case, save, read, sha, require, CONFIG, SERIES, ADMISSION, START, END
from audit_multiscale_reference import audit_case
from audit_multiscale_capacity_axis import CASES, design_contract, verify_run, thermal_ledger

OLD = ROOT / "docs/implementation_20261002_followup/parallel_stage_03/multiscale/run_01"
LITERATURE = [
    {"id": "Reynders2015", "title": "A generic quantification method for the active demand response potential of structural storage in buildings",
     "authors": "G. Reynders, J. Diriken, D. Saelens", "year": 2015,
     "url": "https://publications.ibpsa.org/proceedings/bs/2015/papers/bs2015_2475.pdf",
     "access": "Original IBPSA proceedings full text, pp1986-1993, read2026-10-02",
     "support": "Storage capacity, efficiency and power-duration are distinct. Design H and available mass, dynamic boundary conditions and emitter power matter.",
     "implementation": "Hold H, gains, weather and service fixed; vary C alone; report bill, energy, CO2, inventory and true hourly shared-power constraints.",
     "not_claimed": "This factorial experiment does not reproduce the paper's event-defined CADR or storage efficiency, or validate its detailed construction model."},
    {"id": "KircherZhang2015", "title": "On the lumped capacitance approximation accuracy in RC network building models",
     "authors": "Kevin J. Kircher, K. Max Zhang", "year": 2015, "doi": "10.1016/j.enbuild.2015.09.053",
     "url": "https://www.sciencedirect.com/science/article/pii/S0378778815302930",
     "access": "Publisher abstract/highlights from search2026-10-02; full text not obtained",
     "support": "A lumped RC approximation can have appreciable envelope heat-flux error; its validity depends on dimensionless envelope conditions.",
     "implementation": "Distinguish exact solution of registered1R1C equations from evidence that1R1C is an accurate actual building model.",
     "not_claimed": "No Biot-number verification, calibrated envelope, or measured-site temperature prediction is supplied."},
    {"id": "Guo2020", "title": "Aggregation and Data Driven Identification of Building Thermal Dynamic Model and Unmeasured Disturbance",
     "authors": "Zhong Guo, Austin R. Coffman, Jeffrey Munk, Piljae Im, Teja Kuruganti, Prabir Barooah", "year": 2020,
     "doi": "10.48550/arXiv.2001.09141", "url": "https://arxiv.org/abs/2001.09141",
     "access": "Authors' original arXiv abstract read2026-10-02; not a fresh full-text reproduction",
     "support": "Aggregation is not generally a trivial average; unknown disturbances affect equivalent-model identification.",
     "implementation": "Retain two individual temperature states and audit each, rather than claim an equivalent aggregate RC model.",
     "not_claimed": "No aggregation-identification algorithm or aggregate-equivalence validation is implemented here."},
    {"id": "Klatzer2025", "title": "Towards Exact Temporal Aggregation of Time-Coupled Energy Storage Models via Active Constraint Set Identification and Machine Learning",
     "authors": "Thomas Klatzer, David Cardona-Vasquez, Luca Santosuosso, Sonja Wogrin", "year": 2025,
     "url": "https://arxiv.org/html/2510.14451v1", "status": "preprintv1,2025-10-16",
     "access": "Original HTML sectionsIII-IV read2026-10-02",
     "support": "Perfect-information construction uses prior active sets and duals. The adjacent-period aggregation observation has its formal proof left for future work.",
     "implementation": "Keep the complete continuous2880h chronology; do not transfer an exact-TSA guarantee to this binary heat/cool model.",
     "not_claimed": "No ML/ACS/dual TSA implementation, speedup or exact aggregation theorem here."},
]


def make_case(reference, frame, name, capacity):
    require(name in CASES, "unknown pure-C case")
    config, data = original_case(reference, frame, "coupled_flexible" if name.endswith("shared") else "joint_unlimited", capacity)
    multiplier = 1. if name.startswith("C1_") else 4.
    config["building_thermal"]["buildings"][1]["thermal_capacity_kwh_per_c"] = reference["building_thermal"]["buildings"][0]["thermal_capacity_kwh_per_c"] * multiplier
    validate_inputs(config, data)
    return config, data


def freeze(out, time_limit=120.):
    require(not out.exists(), "choose fresh directory; preserve previous evidence")
    require(not isinstance(time_limit, bool) and np.isfinite(time_limit) and time_limit > 0, "invalid budget")
    reference, frame = input_reference()
    h = float(reference["building_thermal"]["buildings"][0]["heat_loss_kw_per_c"])
    capacity = h * 2.5 * max(20. - float(frame.outdoor_temp.min()), 0.)
    configs, frames = {}, {}
    for name in CASES:
        configs[name], frames[name] = make_case(reference, frame, name, capacity)
    protocol = {
        "schema_version": "multiscale_capacity_axis.v1", "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "cases": list(CASES), "hours_per_building": 2880, "dt_hours": 1,
        "start_inclusive": START, "end_exclusive": END, "unit_physical_weights": True,
        "time_limit_seconds_per_case": float(time_limit), "mip_gap_target": 0.,
        "capacity_kw": capacity, "capacity_rule": "2.5H_A*(20-min(full-windowTout)), chosen before solving and independent of C",
        "factors": {"H_A": h, "H_B": 1.5*h, "C_A": .535576, "C_B_multipliers": [1., 4.], "shared_grid": [False, True]},
        "common_service": "Two48m2 design nodes,20..27C,initial/final20C,2880 continuous hours,exclusive heat/cooling",
        "hypotheses": ["Pure-C difference conditional on fixed H/equipment/weather/service, including zero or adverse outcomes", "Shared-grid effect at each C and factorial bill interaction; no universal monotonic-C theorem"],
        "metrics_predeclared": ["120-day energy-charge bill", "electricity and fixed-average-factor CO2", "actual grid peak/binding/excess hours", "per-building heat/temperature changes", "analytic integrated envelope loss and sensible inventory"],
        "cost_scope": "Conditional Wuhan energy charges only; no CAPEX/OM/ROI or marginal-emissions claim",
        "equipment_scope": "Original existing1000kW ideal heat/cool equipment,efficiency=EER=1; no investment/storage/network; finite shared electricity cap is sole coupling",
        "literature": LITERATURE, "field_validation": False, "three_city_claim": False,
        "aggregation_equivalence_claim": False, "new_theory": False,
        "failure_policy": "Retain every attempted failure, limit and incumbent; no replacement/weather/capacity retuning",
    }
    protocol["time_axis"] = describe_time_axis(frame, kind="continuous", dt_hours=1., expected_start=START, expected_end=END)
    require(design_contract(configs, frames, protocol)["passed"], "factorial design contract failed")
    out.mkdir(parents=True)
    originals = [ROOT / CONFIG, ROOT / SERIES, ROOT / ADMISSION]
    # This new runner does not import the concurrently developed saved-evidence
    # registry. Keep all other historical source snapshots and time_registry.
    # This explicit exclusion is not a claim of a general dependency closure.
    code = [f for f in sorted((ROOT / "src/heatplan").glob("*.py")) if f.name != "solution_evidence.py"] + [ROOT / "scripts" / f for f in (
        "run_multiscale_reference.py", "audit_multiscale_reference.py", "run_multiscale_capacity_axis.py", "audit_multiscale_capacity_axis.py", "report_multiscale_capacity_axis.py")]
    protocol["source_snapshot_scope"] = "All current heatplan.py modules except unused solution_evidence.py; selected runner/auditor/report scripts. Explicit import scan checked before run, not a dynamic dependency-completeness claim."
    protocol["source_snapshots"] = {}
    for f in originals + code:
        rel = f.relative_to(ROOT).as_posix()
        target = out / "frozen_code" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        protocol["source_snapshots"][rel] = {"path": target.relative_to(out).as_posix(), "sha256": sha(f)}
    protocol["reference_inputs"] = {f.relative_to(ROOT).as_posix(): sha(f) for f in originals}
    protocol["old_frozen_files"] = {f.relative_to(ROOT).as_posix(): sha(f) for f in sorted(OLD.rglob("*")) if f.is_file()}
    protocol["case_files"] = {}
    for name in CASES:
        path = out / "input_cases" / name
        path.mkdir(parents=True)
        (path / "config.yaml").write_text(yaml.safe_dump(configs[name], allow_unicode=True, sort_keys=False), encoding="utf-8")
        frames[name].to_csv(path / "input.csv", index=False)
        protocol["case_files"][name] = {f"input_cases/{name}/{f}": sha(path/f) for f in ("config.yaml", "input.csv")}
    save(out / "protocol.json", protocol)
    save(out / "protocol_lock.json", {"protocol.json": sha(out/"protocol.json")})
    print({"frozen": True, "cases": len(CASES), "capacity_kw": capacity}, flush=True)


def verify_inputs(out):
    protocol = read(out/"protocol.json")
    require(sha(out/"protocol.json") == read(out/"protocol_lock.json")["protocol.json"], "protocol changed")
    for rel, entry in protocol["source_snapshots"].items():
        require(sha(out/entry["path"]) == entry["sha256"], f"snapshot changed:{rel}")
        require(sha(ROOT/rel) == entry["sha256"], f"current source changed:{rel}")
    for rel, digest in protocol["old_frozen_files"].items():
        require(sha(ROOT/rel) == digest, f"old frozen evidence changed:{rel}")
    for entries in protocol["case_files"].values():
        for rel, digest in entries.items():
            require(sha(out/rel) == digest, f"input changed:{rel}")
    return protocol


def run(out):
    protocol = verify_inputs(out)
    require(not (out/"results").exists(), "preserve previous attempts")
    records=[]
    for name in CASES:
        started=perf_counter(); path=out/"results"/name; path.mkdir(parents=True)
        record={"name": name, "status": "started"}
        try:
            for filename in ("config.yaml", "input.csv"):
                shutil.copy2(out/"input_cases"/name/filename, path/filename)
            config=yaml.safe_load((path/"config.yaml").read_text(encoding="utf-8")); frame=pd.read_csv(path/"input.csv")
            model=HeatPlanningModel(config,frame)
            result=model.solve(time_limit=protocol["time_limit_seconds_per_case"],mip_gap=0.)
            np.save(path/"solution_values.npy",result.values,allow_pickle=False)
            save(path/"variable_names.json",model.problem.names)
            for filename, data in (("dispatch.csv", result.dispatch),("building_trajectory.csv", result.building_trajectory),("investments.csv",result.investments)):
                data.to_csv(path/filename,index=False)
            save(path/"summary.json",result.summary)
            independent=audit_case(config,frame,result.building_trajectory,result.dispatch,result.summary)
            save(path/"independent_audit.json",independent)
            save(path/"thermal_ledger.json",thermal_ledger(config,frame,result.building_trajectory))
            require(result.summary["numerical_audit_passed"] and independent["passed"],"physical/numerical/ledger failure")
            quality=result.summary["solution_quality"]
            proven=quality["quality"]=="solver_proven_optimal_within_registered_tolerance"
            record.update(status="passed" if proven else "feasible_unproven",solver_status=result.summary["solver_status"],mip_gap=result.summary.get("mip_gap"),solution_quality=quality,metrics=independent["metrics"])
        except Exception as exc:
            evidence=getattr(exc,"summary",None)
            record.update(status="infeasible" if evidence and evidence.get("solver_status")==2 else "failed",exception=type(exc).__name__,reason=str(exc),solver_evidence=evidence)
            save(path/"failure.json",record)
        record["wall_seconds"]=perf_counter()-started; records.append(record)
        save(out/"comparison.json",records)
        save(out/"failures.json",[r for r in records if r["status"] in ("failed","infeasible")])
        print({"case":name,"status":record["status"],"seconds":record["wall_seconds"]},flush=True)
    verify_inputs(out)
    save(out/"manifest.json",{"completed_utc":datetime.now(timezone.utc).isoformat(),"requested_cases":4,"attempted_cases":len(records),"audited_cases":sum(r["status"] in ("passed","feasible_unproven") for r in records),"files":{f.relative_to(out).as_posix():sha(f) for f in sorted(out.rglob("*")) if f.is_file()}})


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("freeze","run","audit"))
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--time-limit",type=float,default=120.)
    args=parser.parse_args(); out=args.out.resolve()
    if args.action=="freeze": freeze(out,args.time_limit)
    elif args.action=="run": run(out)
    else:
        report=verify_run(out);save(out/"independent_verification.json",report)
        print({"passed":report["passed"],"cases":len(report["cases"])})
        raise SystemExit(0 if report["passed"] else 1)
