"""Cyclic representative planning, then the unchanged fixed-state service year.

Await a frozen new code epoch and C0's exact-input provenance admission.
This is an approximation study; it does not replace the historical investment.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter
import traceback

import numpy as np
import pandas as pd

from heatplan.building_audit import audit_building_trajectory, building_audit_identity
from heatplan.chronology import investment_freeze_audit, model_structure_profile
from heatplan.competition_runner import (OUTPUT, code_identity, file_hash, fingerprint,
    load_case, run_solve, solver_lock, write_json, scenario_evidence_flags, cost_reporting_fields)
from heatplan.experiments import freeze_investment_plan
from heatplan.model import HeatPlanningModel


def make_cyclic_planning_config(config):
    result = deepcopy(config)
    result["planning"].pop("annual_scope", None)
    section = result["building_thermal"]
    section["boundary_mode"] = "cyclic"
    for building in section["buildings"]:
        building["boundary_mode"] = "cyclic"
        for key in ("final_temperature_c", "final_min_temperature_c", "final_max_temperature_c", "boundary_conditions"):
            building.pop(key, None)
    return result


def nonboundary_building_contract(section):
    excluded = {"boundary_mode", "boundary_conditions", "final_temperature_c",
                "final_min_temperature_c", "final_max_temperature_c"}
    result = {key: deepcopy(value) for key, value in section.items() if key not in excluded}
    result["buildings"] = [{key: deepcopy(value) for key, value in building.items() if key not in excluded}
                           for building in section["buildings"]]
    return fingerprint(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allow-heavy", action="store_true")
    parser.add_argument("--planning-time-limit", type=float, default=600.)
    parser.add_argument("--annual-time-limit", type=float, default=1800.)
    args = parser.parse_args()
    if not args.allow_heavy:
        parser.error("await C0 source admission and new frozen-epoch release")
    if Path(args.run_id).name != args.run_id:
        parser.error("run-id must be one directory name")
    root = Path(__file__).resolve().parents[3]
    case = load_case(root)
    replay_contract = deepcopy(case["config"]["building_thermal"])
    planning_config = make_cyclic_planning_config(case["config"])
    if nonboundary_building_contract(replay_contract) != nonboundary_building_contract(planning_config["building_thermal"]):
        raise ValueError("cyclic ablation changed building physics or services")
    destination = root / OUTPUT / args.run_id
    destination.mkdir(parents=True, exist_ok=False)
    frozen_code = code_identity(root)
    contracts = {"planning_boundary_contract_sha256": fingerprint(planning_config["building_thermal"]),
                 **scenario_evidence_flags(),
                 "replay_service_contract_sha256": case["service_contract_sha256"],
                 "nonboundary_building_contract_sha256": nonboundary_building_contract(replay_contract),
                 "planning_boundary": "independent_daily_cyclic_with_no_forced_20C_daily_reset",
                 "replay_boundary": "complete_C1_service_year_fixed_initial_and_final_20C",
                 "orchestrator_sha256": file_hash(Path(__file__)), "code_sha256": frozen_code}
    write_json(destination / "boundary_contracts.json", contracts)
    planning_dir = destination / "cyclic_plan"
    planning_dir.mkdir()
    try:
        with solver_lock(root):
            started = perf_counter()
            model = HeatPlanningModel(planning_config, case["representative"])
            identity = building_audit_identity(planning_config, model.ts)
            anchor_dir = root / OUTPUT / "input_anchors"
            anchor_dir.mkdir(parents=True, exist_ok=True)
            anchor_path = anchor_dir / (fingerprint({"directory": str(planning_dir), **identity}) + ".json")
            if anchor_path.exists():
                raise FileExistsError("cyclic input anchor already exists")
            write_json(anchor_path, identity)
            write_json(planning_dir / "model_config.json", planning_config)
            model.ts.to_csv(planning_dir / "model_timeseries.csv", index=False)
            build_started = perf_counter()
            model.build()
            build_seconds = perf_counter() - build_started
            artifacts = model.solve(time_limit=args.planning_time_limit, mip_gap=.005)
            summary = artifacts.summary
            summary.update(scenario_evidence_flags())
            summary.update(cost_reporting_fields(summary))
            for service in ("heat", "cooling"):
                coefs = model.metric_coefficients[f"building_{service}_delivered_kwh"]
                summary[f"building_{service}_delivered_kwh_per_year"] = float(sum(
                    coefficient * artifacts.values[index] for index, coefficient in coefs.items()))
            audit = audit_building_trajectory(planning_config, model.ts, artifacts.building_trajectory,
                dispatch=artifacts.dispatch, summary=summary, expected_input_identity=identity)
            summary["independent_building_audit_passed"] = audit["passed"]
            summary["independent_building_audit_metrics"] = audit["metrics"]
            write_json(planning_dir / "summary.json", summary)
            write_json(planning_dir / "c6_building_audit.json", audit)
            artifacts.investments.to_csv(planning_dir / "investments.csv", index=False)
            artifacts.dispatch.to_csv(planning_dir / "dispatch.csv", index=False)
            artifacts.building_trajectory.to_csv(planning_dir / "building_trajectory.csv", index=False)
            artifacts.building_comfort.to_csv(planning_dir / "building_comfort.csv", index=False)
            np.savez_compressed(planning_dir / "solution.npz", values=artifacts.values)
            pd.DataFrame({"variable": model.problem.names}).to_csv(planning_dir / "variables.csv.gz", index=False)
            profile = model_structure_profile(model, build_seconds=build_seconds,
                                               solve_seconds=summary["solver_elapsed_seconds"])
            profile["wall_seconds"] = perf_counter() - started
            write_json(planning_dir / "model_profile.json", profile)
            if not (audit["passed"] and summary["numerical_audit_passed"] and frozen_code == code_identity(root)):
                raise ValueError("cyclic planning incumbent failed audit or epoch stability")
            fixed = freeze_investment_plan(planning_config, artifacts, relax_reliability=False)
            fixed["building_thermal"] = replay_contract  # Explicit study mapping, never edit old evidence.
            fixed["planning"]["annual_scope"] = deepcopy(case["config"]["planning"]["annual_scope"])
            freeze = investment_freeze_audit(planning_config, fixed, artifacts)
            freeze.to_csv(planning_dir / "investment_freeze_audit.csv", index=False)
            if not freeze.freeze_ok.all():
                raise ValueError("cyclic study investment did not freeze")
            parent = {"planning_summary": summary,
                      "investment_sha256": fingerprint(artifacts.investments.to_dict("records")),
                      "fixed_config": fixed, "fixed_config_sha256": fingerprint(fixed), **contracts}
            # This distinct format intentionally cannot be consumed as a
            # normal same-boundary fixed_plan.json by the frozen runner.
            write_json(planning_dir / "ablation_plan.json", parent)
            files = {str(p.relative_to(planning_dir)): file_hash(p) for p in planning_dir.rglob("*") if p.is_file()}
            write_json(planning_dir / "manifest.json", {"complete": True, "files": files,
                "scope": "cyclic_representative_planning_only", "input_anchor": str(anchor_path.relative_to(root)),
                "investment_sha256": parent["investment_sha256"], **contracts})
            annual = run_solve(root, destination / "annual_fixed_replay", case, fixed, case["annual"],
                scope="annual", time_limit=args.annual_time_limit, mip_gap=.005, parent=parent)
        comparisons = {}
        for service in ("heat", "cooling"):
            key = f"building_{service}_delivered_kwh_per_year"
            predicted, actual = float(summary[key]), float(annual[key])
            comparisons[service] = {"representative_prediction_kwh": predicted, "annual_actual_dispatch_kwh": actual,
                                   "relative_prediction_bias": (predicted - actual) / actual if actual else None}
        write_json(destination / "ablation_report.json", {**contracts, "service_prediction": comparisons,
            "investment_sha256": parent["investment_sha256"], "adopted_as_recommendation": False,
            "winter_replay_required_before_adoption": True,
            "comparison_to_historical_fixed_daily_case_requires_matching_input_provenance": True})
        files = {str(p.relative_to(destination)): file_hash(p) for p in destination.rglob("*") if p.is_file()}
        write_json(destination / "manifest.json", {"complete": True, "files": files,
                                                   "scope": "planning_boundary_approximation_ablation", **contracts})
    except Exception as error:
        write_json(destination / "failure.json", {"complete": False, "message": str(error),
                                                "traceback": traceback.format_exc(), **contracts})
        raise


if __name__ == "__main__":
    main()
