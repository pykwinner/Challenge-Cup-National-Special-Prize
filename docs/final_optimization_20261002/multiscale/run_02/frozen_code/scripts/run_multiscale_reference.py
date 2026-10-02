"""Frozen two-building shared-grid reference mechanism, using the existing model.

The second building is an explicitly declared design variant, not a measured site.
Freeze before solving; retain every failed or unproven result without replacement.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
from time import perf_counter

import numpy as np
import pandas as pd
import scipy
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from heatplan.io import validate_inputs
from heatplan.model import HeatPlanningModel
from heatplan.building_audit import audit_building_trajectory, building_audit_identity
from audit_multiscale_reference import audit_case

CONFIG = Path("configs/evidence_case_20261002/wuhan_bestest600_rc_tou.yaml")
SERIES = Path("data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv")
ADMISSION = Path("docs/implementation_20261002/data/admission_tou.json")
START, END = "2024-11-16T00:00:00+08:00", "2025-03-16T00:00:00+08:00"
CASES = ("independent_A", "independent_B", "joint_unlimited", "coupled_flexible",
         "coupled_A_fixed20", "coupled_B_fixed20", "coupled_all_fixed20")


def require(condition, message):
    if not bool(condition):
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    def convert(item):
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, (np.ndarray, pd.Index)):
            return item.tolist()
        if isinstance(item, Path):
            return item.as_posix()
        raise TypeError(type(item).__name__)
    def finite_tree(item):
        if isinstance(item, np.generic):
            return finite_tree(item.item())
        if isinstance(item, dict):
            return {str(k): finite_tree(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [finite_tree(v) for v in item]
        if isinstance(item, float) and not np.isfinite(item):
            return None
        return item
    Path(path).write_text(json.dumps(finite_tree(value), ensure_ascii=False, indent=2,
                                    allow_nan=False, default=convert) + "\n", encoding="utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def input_reference():
    config = yaml.safe_load((ROOT / CONFIG).read_text(encoding="utf-8"))
    frame = pd.read_csv(ROOT / SERIES)
    admission = read(ROOT / ADMISSION)
    require(admission["conditional_operating_tariff_reference_admitted"] and
            admission["physical_reference_admitted"], "reference not admitted")
    for path in (CONFIG, SERIES):
        require(sha(ROOT / path) == admission["input_sha256"][path.as_posix()],
                f"reference input changed: {path}")
    expected = pd.date_range(START, END, freq="h", inclusive="left").tz_convert("UTC")
    actual = pd.DatetimeIndex(pd.to_datetime(frame.time, utc=True, errors="raise"))
    require(actual.equals(expected) and len(frame) == 2880, "complete 2880h chronology required")
    require((frame.period_weight == 1).all() and (frame.scenario_probability == 1).all(),
            "unit physical weights required")
    require(config["building_thermal"]["boundary_mode"] == "fixed" and
            config["building_thermal"]["simultaneous_mode"] == "exclusive", "fixed exclusive RC required")
    return config, frame


def make_case(reference, frame, name, grid_capacity):
    """Keep independent physical nodes; only the global grid limit couples them."""
    require(name in CASES, "unknown case")
    config, data = deepcopy(reference), frame.copy(deep=True)
    prototype = reference["building_thermal"]["buildings"][0]
    source = reference["sources"][0]
    cold = reference["multi_energy_services"]["cooling"]["sources"][source["name"]]
    members = ("A",) if name == "independent_A" else ("B",) if name == "independent_B" else ("A", "B")
    config["planning"]["region_profile"] = "two_BESTEST_derived_design_variants_Wuhan"
    config["planning"]["design_outdoor_temperature_c"] = float(data.outdoor_temp.min())
    config["nodes"], config["sources"] = [], []
    config["building_thermal"]["buildings"] = []
    config["multi_energy_services"]["cooling"]["demand_columns"] = {}
    config["multi_energy_services"]["cooling"]["sources"] = {}
    config.pop("grid_connection", None)
    for member in members:
        node, asset, building = f"node_{member}", f"ideal_{member}", f"variant_{member}"
        spec = deepcopy(prototype)
        spec.update(name=building, node=node,
                    heat_loss_kw_per_c=prototype["heat_loss_kw_per_c"] * (1 if member == "A" else 1.5),
                    thermal_capacity_kwh_per_c=prototype["thermal_capacity_kwh_per_c"] * (1 if member == "A" else 4),
                    internal_gain_column=f"internal_gain_{member}_kw",
                    solar_gain_column=f"solar_gain_{member}_kw")
        fixed = name == "coupled_all_fixed20" or name == f"coupled_{member}_fixed20"
        if fixed:
            spec["max_temperature_c"] = 20.0
        data[f"internal_gain_{member}_kw"] = 0.2
        data[f"solar_gain_{member}_kw"] = 0.0
        data[f"heat_reference_{member}_kw"] = spec["heat_loss_kw_per_c"] * np.maximum(20 - data.outdoor_temp, 0)
        data[f"steady_cooling_{member}_kw"] = np.maximum(spec["heat_loss_kw_per_c"] * (data.outdoor_temp - 27) + .2, 0)
        config["nodes"].append({"name": node, "demand_columns": {spec["heat_grade"]: f"heat_reference_{member}_kw"}})
        equipment = deepcopy(source)
        equipment.update(name=asset, node=node)
        config["sources"].append(equipment)
        config["building_thermal"]["buildings"].append(spec)
        config["multi_energy_services"]["cooling"]["demand_columns"][node] = f"steady_cooling_{member}_kw"
        config["multi_energy_services"]["cooling"]["sources"][asset] = deepcopy(cold)
    if name.startswith("coupled_"):
        config["grid_connection"] = {"existing_capacity_kw": float(grid_capacity),
                                     "max_new_capacity_kw": 0.0, "reserve_margin": 0.0,
                                     "firm_capacity_method": "portfolio_dispatch", "firm_simultaneity": 1.0}
    validate_inputs(config, data)
    return config, data


def freeze(out, time_limit=120):
    require(not out.exists(), "choose a fresh directory; preserve previous evidence")
    require(np.isfinite(time_limit) and time_limit > 0, "invalid budget")
    config, frame = input_reference()
    first = config["building_thermal"]["buildings"][0]
    h_sum = first["heat_loss_kw_per_c"] * 2.5
    capacity = h_sum * max(20 - float(frame.outdoor_temp.min()), 0)
    fixed_load = sum(np.abs(first["heat_loss_kw_per_c"] * mult * (20 - frame.outdoor_temp) - .2)
                     for mult in (1, 1.5))
    require(float(fixed_load.max()) <= capacity + 1e-8, "fixed20 witness exceeds shared capacity")
    out.mkdir(parents=True)
    inputs = out / "input_cases"
    inputs.mkdir()
    case_files = {}
    for name in CASES:
        path = inputs / name
        path.mkdir()
        cfg, data = make_case(config, frame, name, capacity)
        (path / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        data.to_csv(path / "input.csv", index=False)
        case_files[name] = {f"input_cases/{name}/{f}": sha(path / f) for f in ("config.yaml", "input.csv")}
    originals = [ROOT / CONFIG, ROOT / SERIES, ROOT / ADMISSION]
    code_paths = sorted((ROOT / "src/heatplan").glob("*.py")) + [Path(__file__), ROOT / "scripts/audit_multiscale_reference.py"]
    snapshots = {}
    for path in originals + code_paths:
        relative = path.relative_to(ROOT).as_posix()
        target = out / "frozen_code" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        snapshots[relative] = {"path": target.relative_to(out).as_posix(), "sha256": sha(path)}
    protocol = {
        "schema_version": "multiscale_reference.v1", "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "cases": list(CASES), "start_inclusive": START, "end_exclusive": END,
        "hours_per_building": 2880, "dt_hours": 1, "unit_physical_weights": True,
        "time_limit_seconds_per_case": float(time_limit), "mip_gap_target": 0.0,
        "solver": "existing HeatPlanningModel/scipy HiGHS MILP, no new acceleration or relaxation",
        "reference_inputs": {p.relative_to(ROOT).as_posix(): sha(p) for p in originals},
        "source_snapshots": snapshots, "case_files": case_files,
        "variant_design": {"A": "48m2 original BESTEST600-derived H=.1043,C=.535576", "B": "explicit design variant: same48m2, H=1.5H_A, C=4C_A; not a validated BESTEST900 or measured building"},
        "common_service": "20..27C all2880h, initial/final20C, exclusive heat/cool; fixed20 ablations restrict operation within this contract",
        "shared_resource": {"kind": "system electric import", "capacity_kw": capacity,
                            "rule": "(H_A+H_B)*max(20-min(full-window Tout),0), before any optimizer results",
                            "maximum_fixed20_feasible_witness_kw": float(fixed_load.max()),
                            "firm_method": "portfolio_dispatch, firm_simultaneity1, reserve0; retained static firm constraints"},
        "cost_scope": "120-day electricity energy charge only; audited Wuhan conditional reference account and fixed average EF",
        "investment_scope": "existing ideal sources, no free additions/retirements/grid investment/storage/pipe",
        "counterfactuals": "independent_A/B summed under common grid, joint_unlimited removes only shared grid; fixed20 changes operation not comfort contract",
        "success_criteria": ["independent exported RC/source/grid/ledger audit", "joint_unlimited vs independent cost identity within solver intervals", "independent sum violates grid or explicit negative finding", "coupled operation truly binds grid or explicit negative finding", "per-building trajectories respond to shared constraint or explicit negative finding"],
        "failure_policy": "Every attempted exception/limit/no incumbent retained; no result-driven capacity or parameter replacement; feasible unproven labeled as such",
        "field_validation": False, "new_theory": False, "local_account_eligibility": False,
        "independent_confirmation": False, "unknown_costs_filled_with_zero": False,
        "reference_ideal_om_excluded": True, "python": platform.python_version(), "platform": platform.platform(),
        "package_versions": {"numpy": np.__version__, "pandas": pd.__version__,
                             "scipy": scipy.__version__, "pyyaml": yaml.__version__},
    }
    save(out / "protocol.json", protocol)
    save(out / "protocol_lock.json", {"protocol.json": sha(out / "protocol.json")})
    print(json.dumps({"frozen": True, "capacity_kw": capacity, "protocol_sha256": sha(out / "protocol.json")}))


def verify(out, *, check_current_sources=True):
    require(sha(out / "protocol.json") == read(out / "protocol_lock.json")["protocol.json"], "protocol changed")
    protocol = read(out / "protocol.json")
    for original, snapshot in protocol["source_snapshots"].items():
        require(sha(out / snapshot["path"]) == snapshot["sha256"], f"frozen source changed: {original}")
        if check_current_sources:
            require(sha(ROOT / original) == snapshot["sha256"], f"current source differs: {original}")
    for files in protocol["case_files"].values():
        for name, digest in files.items():
            require(sha(out / name) == digest, f"case input changed: {name}")
    if (out / "manifest.json").exists():
        for name, digest in read(out / "manifest.json")["files"].items():
            require(sha(out / name) == digest, f"output changed: {name}")
    return protocol


def run(out):
    protocol = verify(out)
    require(not (out / "results").exists(), "preserve prior run")
    (out / "results").mkdir()
    records = []
    for name in protocol["cases"]:
        dest = out / "results" / name
        dest.mkdir()
        for f in ("config.yaml", "input.csv"):
            shutil.copy2(out / "input_cases" / name / f, dest / f)
        started = perf_counter()
        record = {"name": name, "status": "started"}
        try:
            config = yaml.safe_load((dest / "config.yaml").read_text(encoding="utf-8"))
            frame = pd.read_csv(dest / "input.csv")
            model = HeatPlanningModel(config, frame)
            identity = building_audit_identity(model.config, model.ts)
            save(dest / "input_identity.json", identity)
            result = model.solve(time_limit=protocol["time_limit_seconds_per_case"], mip_gap=0.0)
            result.dispatch.to_csv(dest / "dispatch.csv", index=False)
            result.investments.to_csv(dest / "investments.csv", index=False)
            result.building_trajectory.to_csv(dest / "building_trajectory.csv", index=False)
            save(dest / "summary.json", result.summary)
            save(dest / "numerical_audit.json", {k:v for k,v in result.summary.items() if k.startswith("numerical_")})
            core_audit = audit_building_trajectory(model.config, model.ts, result.building_trajectory,
                result.dispatch, result.summary, require_continuous_fixed=True, expected_input_identity=identity)
            save(dest / "core_building_audit.json", core_audit)
            independent = audit_case(config, frame, result.building_trajectory, result.dispatch, result.summary)
            save(dest / "independent_audit.json", independent)
            require(result.summary["numerical_audit_passed"] and core_audit["passed"] and independent["passed"], "numerical/physical/ledger audit failed")
            quality = result.summary["solution_quality"]
            proven = quality["quality"] == "solver_proven_optimal_within_registered_tolerance"
            record.update(status="passed" if proven else "feasible_unproven", solver_status=result.summary["solver_status"],
                          mip_gap=result.summary.get("mip_gap"), solution_quality=quality, audit=independent,
                          globally_proven_within_registered_solver_tolerance=proven)
        except Exception as error:
            summary = getattr(error, "summary", None)
            status = "infeasible" if summary and summary.get("solver_status") == 2 else "failed"
            failure = {"name": name, "status": status, "exception": type(error).__name__, "reason": str(error),
                       "solver_evidence": summary, "wall_seconds": perf_counter() - started}
            save(dest / "failure.json", failure)
            record.update(failure)
        record["wall_seconds"] = perf_counter() - started
        records.append(record)
        save(out / "comparison.json", records)
        save(out / "failures.json", [r for r in records if r["status"] in ("failed", "infeasible")])
        print(json.dumps({"case": name, "status": record["status"], "wall_seconds": record["wall_seconds"]}), flush=True)
    verify(out)
    save(out / "manifest.json", {"completed_utc": datetime.now(timezone.utc).isoformat(),
        "requested_cases": len(protocol["cases"]), "attempted_cases": len(records),
        "physically_audited_cases": sum(r["status"] in ("passed", "feasible_unproven") for r in records),
        "failures": [r["name"] for r in records if r["status"] in ("failed", "infeasible")],
        "files": {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*")) if p.is_file()}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "verify"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--time-limit", type=float, default=120.)
    args = parser.parse_args()
    if args.action == "freeze":
        freeze(args.out.resolve(), args.time_limit)
    elif args.action == "run":
        run(args.out.resolve())
    else:
        verify(args.out.resolve(), check_current_sources=False)
