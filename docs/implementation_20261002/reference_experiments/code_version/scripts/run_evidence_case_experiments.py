"""Run admitted reference-case experiments without upgrading them to field data.

Writes a protocol before solving, each failure separately, and a final manifest.
E1 is a complete chronological planning/replay pair. E2 tightens the temperature
band to the original lower bound, while retaining the original service checks.
Optional endpoint/epsilon runs retain a limited electricity-bill objective when
full equipment/lifecycle economics have not been admitted.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from heatplan.building_audit import audit_building_trajectory, building_audit_identity
from heatplan.chronology import investment_freeze_audit
from heatplan.experiments import freeze_investment_plan, nondominated_cost_emission_points
from heatplan.io import load_inputs
from heatplan.model import HeatPlanningModel


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jsonable(value):
    if isinstance(value, dict): return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [jsonable(v) for v in value]
    if isinstance(value, np.ndarray): return jsonable(value.tolist())
    if isinstance(value, np.generic): return jsonable(value.item())
    if isinstance(value, float) and not np.isfinite(value): return None
    if isinstance(value, (Path, pd.Timestamp)): return str(value)
    return value


def save(path, value):
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def verify_admission(path, config_path, series_path, *, rc):
    report = json.loads(path.read_text(encoding="utf-8"))
    if not report.get("rc_reference_admitted" if rc else "physical_reference_admitted", False):
        raise ValueError("the selected reference model has not passed source admission")
    hashes = report.get("input_sha256", {})
    expected_inputs = {config_path.resolve(), series_path.resolve()}
    checked = set()
    for name, digest in hashes.items():
        source = Path(name)
        if not source.is_absolute(): source = ROOT / source
        if not source.is_file() or sha(source) != digest:
            raise ValueError(f"admitted input identity changed: {name}")
        checked.add(source.resolve())
    if not expected_inputs.issubset(checked):
        raise ValueError("admission hash map must cover the selected config and hourly data")
    return report


def chronology_check(frame, config, start, end):
    if not np.isclose(float(config["planning"].get("time_step_hours", 1)), 1.):
        raise ValueError("this competition protocol requires 1-hour steps")
    stamps = pd.DatetimeIndex(pd.to_datetime(frame["time"], errors="raise"))
    if stamps.tz is None:
        raise ValueError("reference chronology must declare timezone")
    expected = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="h", inclusive="left")
    if not stamps.tz_convert("UTC").equals(expected.tz_convert("UTC")):
        raise ValueError("input does not cover the entire registered heating season")
    if "period_weight" in frame and not np.allclose(frame["period_weight"], 1, rtol=0, atol=1e-12):
        raise ValueError("complete-season replay cannot use representative-hour weights")
    if "scenario" in frame and frame["scenario"].nunique() != 1:
        raise ValueError("reference season must be a single chronological scenario")
    return {"passed": True, "hours": len(frame), "start_inclusive": start, "end_exclusive": end, "step_hours": 1}


def code_identity():
    # Freeze the actual model/audit dependencies. Independent development of an
    # unused benchmark module does not change this experiment's mathematical identity.
    modules = ("model", "physical_extensions", "linear", "io", "policy_alignment",
               "building_thermal", "building_audit", "chronology", "experiments", "accounting")
    paths = [ROOT / "src/heatplan" / f"{name}.py" for name in modules] + [Path(__file__)]
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def run_one(name, config, frame, output, budget, gap, *, primary=None):
    destination = output / name
    destination.mkdir()
    start = perf_counter()
    try:
        model = HeatPlanningModel(config, frame)
        identity = building_audit_identity(model.config, model.ts)
        save(destination / "input_identity.json", identity)
        (destination / "effective_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if primary:
            result = model.solve_lexicographic(primary=primary, time_limit=budget, mip_gap=gap)
        else:
            result = model.solve(time_limit=budget, mip_gap=gap)
        result.dispatch.to_csv(destination / "dispatch.csv", index=False)
        result.investments.to_csv(destination / "investments.csv", index=False)
        save(destination / "summary.json", result.summary)
        audit = {"numerical_passed": bool(result.summary["numerical_audit_passed"]),
                 "carbon_cap_passed": bool(result.summary["emissions_cap_pass"]),
                 "solver_quality_passed": bool(result.summary["solver_status"] == 0 or (
                     result.summary.get("mip_gap") is not None and float(result.summary["mip_gap"]) <= gap))}
        if primary:
            audit["primary_numerical_passed"] = bool(result.summary["primary_numerical_audit_passed"])
            audit["primary_solver_quality_passed"] = bool(result.summary["primary_solver_status"] == 0 or (
                result.summary.get("primary_mip_gap") is not None and float(result.summary["primary_mip_gap"]) <= gap))
        if result.building_trajectory is not None:
            result.building_trajectory.to_csv(destination / "building_trajectory.csv", index=False)
            independent = audit_building_trajectory(model.config, model.ts, result.building_trajectory,
                result.dispatch, result.summary, require_continuous_fixed=True, expected_input_identity=identity)
            save(destination / "independent_building_audit.json", independent)
            audit["independent_building_passed"] = bool(independent["passed"])
        audit["passed"] = all(audit.values())
        audit["wall_seconds"] = perf_counter() - start
        save(destination / "audit.json", audit)
        if not audit["passed"]:
            raise ValueError("run failed a physical/numerical acceptance check")
        return result, audit
    except Exception as error:
        save(destination / "failure.json", {"type": type(error).__name__, "reason": str(error), "wall_seconds": perf_counter() - start})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--timeseries", type=Path, required=True)
    p.add_argument("--admission", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--season-start", default="2024-11-16T00:00:00+08:00")
    p.add_argument("--season-end", default="2025-03-16T00:00:00+08:00")
    p.add_argument("--time-limit", type=float, default=120.)
    p.add_argument("--gap", type=float, default=.0001)
    p.add_argument("--frontier", action="store_true")
    args = p.parse_args()
    if not np.isfinite(args.time_limit) or args.time_limit <= 0 or not 0 <= args.gap < 1:
        p.error("invalid solve budget or gap")
    if args.out.exists(): p.error("output exists; choose a new directory to preserve prior evidence")
    config, frame = load_inputs(args.config, args.timeseries)
    rc = config.get("building_thermal", {}).get("enabled", False)
    admission = verify_admission(args.admission, args.config, args.timeseries, rc=rc)
    chronology = chronology_check(frame, config, args.season_start, args.season_end)
    # Preserve admitted prices and physics. Policy/penalty removal is not a
    # free relabeling as pure cost; the experimental objective is explicit.
    code_before = code_identity()
    args.out.mkdir(parents=True)
    protocol = {"frozen_utc": datetime.now(timezone.utc).isoformat(), "chronology": chronology,
                "input_sha256": {str(args.config): sha(args.config), str(args.timeseries): sha(args.timeseries), str(args.admission): sha(args.admission)},
                "code_sha256": code_before, "time_limit_per_stage_seconds": args.time_limit, "mip_gap_target": args.gap,
                "empirical_validation": False, "full_economics_admitted": bool(admission.get("full_economics_admitted", False)),
                "objective_scope": "admitted_model_objective; see components, not automatically whole-project cash cost",
                "experiments": ["E1_complete_season", "E1_fixed_investment_replay"] + (["E2_fixed_lower_temperature"] if rc else []),
                "frontier_requested": args.frontier, "epsilon_positions": 5 if args.frontier else 0,
                "success_criteria": "source identity, complete chronology, numerical feasibility, hard service, independent RC audit when active"}
    save(args.out / "protocol.json", protocol)
    started = perf_counter()
    records, failed = [], []
    baseline, base_audit = run_one("baseline", config, frame, args.out, args.time_limit, args.gap)
    fixed = freeze_investment_plan(config, baseline, relax_reliability=False)
    freeze = investment_freeze_audit(config, fixed, baseline)
    freeze.to_csv(args.out / "investment_freeze_audit.csv", index=False)
    if not freeze.empty and not freeze.freeze_ok.all(): raise ValueError("investment freeze failed")
    replay, _ = run_one("fixed_replay", fixed, frame, args.out, args.time_limit, args.gap)
    for name, artifact in (("baseline", baseline), ("fixed_replay", replay)):
        records.append({"experiment": name, **artifact.summary})
    if rc:
        tighter = deepcopy(fixed)
        changed = []
        for building in tighter["building_thermal"]["buildings"]:
            before = building["max_temperature_c"]
            building["max_temperature_c"] = building["min_temperature_c"]
            building.pop("max_temperature_column", None)
            if building.get("min_temperature_column"):
                building["max_temperature_column"] = building["min_temperature_column"]
            changed.append({"building": building["name"], "old_upper": before,
                            "new_upper": building["max_temperature_c"], "basis": "predeclared stricter control, not observed behavior"})
        save(args.out / "comfort_ablation_changes.json", changed)
        try:
            controlled, _ = run_one("fixed_lower_temperature", tighter, frame, args.out, args.time_limit, args.gap)
            records.append({"experiment": "fixed_lower_temperature", **controlled.summary})
        except Exception as error:
            failed.append({"experiment": "fixed_lower_temperature", "reason": str(error)})
    if args.frontier:
        economic = deepcopy(config)
        economic["planning"]["carbon_price_yuan_per_kg"] = 0.
        economic["planning"].pop("emissions_cap_kg_per_year", None)
        endpoints = {}
        for name, primary in (("cost_endpoint", "total_cost"), ("emission_endpoint", "emissions")):
            result, _ = run_one(name, economic, frame, args.out, args.time_limit, args.gap, primary=primary)
            endpoints[name] = result.summary
        save(args.out / "endpoints.json", endpoints)
        lo = endpoints["emission_endpoint"]["total_service_emissions_kg_per_year"]
        hi = endpoints["cost_endpoint"]["total_service_emissions_kg_per_year"]
        if lo > hi + 1e-6 + abs(hi) * 1e-8: raise ValueError("unreliable endpoint ordering")
        caps = np.linspace(lo, max(lo, hi), 5)
        save(args.out / "epsilon_caps.json", {"caps": caps, "derived_from_endpoints": True,
             "tradeoff_range_degenerate": bool(abs(hi-lo) <= 1e-6 + abs(hi)*1e-8)})
        samples = []
        for number, cap in enumerate(caps):
            capped = deepcopy(economic)
            capped["planning"]["emissions_cap_kg_per_year"] = float(cap) + 1e-7 + abs(float(cap))*1e-8
            name = f"epsilon_{number}"
            try:
                result, _ = run_one(name, capped, frame, args.out, args.time_limit, args.gap)
                samples.append({"experiment": name, "requested_cap": float(cap), **result.summary})
            except Exception as error:
                failed.append({"experiment": name, "reason": str(error)})
        save(args.out / "all_epsilon_samples.json", samples)
        if samples:
            front = nondominated_cost_emission_points(pd.json_normalize(samples))
            front.to_csv(args.out / "sampled_nondominated.csv", index=False)
    save(args.out / "experiment_records.json", records)
    save(args.out / "failures.json", failed)
    stable = code_before == code_identity()
    verify_admission(args.admission, args.config, args.timeseries, rc=rc)
    save(args.out / "manifest.json", {"baseline_and_fixed_replay_passed": True,
        "all_requested_experiments_passed": not failed, "empirical_validation": False,
        "full_economics_admitted": protocol["full_economics_admitted"], "code_stable_during_run": stable,
        "wall_seconds": perf_counter() - started, "failed_experiments": failed,
        "files": {str(f.relative_to(args.out)): sha(f) for f in sorted(args.out.rglob("*")) if f.is_file()}})
    if not stable: raise RuntimeError("code changed during execution; evidence requires rerun")
    print(json.dumps({"output": str(args.out), "baseline_passed": True, "failed": failed,
                      "full_economics_admitted": protocol["full_economics_admitted"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
