"""True core lexicographic endpoints and five audited epsilon incumbents.

Independent orchestration script: does not edit frozen runner or core sources.
The reported frontier is representative-period sampling, not annual certification.
"""
from argparse import ArgumentParser
from copy import deepcopy
from pathlib import Path
from time import perf_counter
import json
import traceback

import numpy as np
import pandas as pd

from heatplan.building_audit import audit_building_trajectory, building_audit_identity
from heatplan.chronology import model_structure_profile
from heatplan.competition_runner import (
    OUTPUT, code_identity, file_hash, fingerprint, load_case, run_solve,
    solver_lock, verify_manifest, write_json,
    scenario_evidence_flags, cost_reporting_fields,
)
from heatplan.experiments import nondominated_cost_emission_points
from heatplan.model import HeatPlanningModel


def endpoint(root, directory, case, config, frame, primary, budget, gap):
    directory.mkdir(parents=True, exist_ok=False)
    before = code_identity(root)
    started = perf_counter()
    meta = {"complete": False, "scope": "representative_lexicographic_endpoint",
            **scenario_evidence_flags(), "formal_scope_semantics": "temporal_extent_only_not_empirical_evidence",
            "primary": primary, "code_sha256": before,
            "orchestrator_sha256": file_hash(Path(__file__)),
            "source_sha256": case["source_identities"], "config_sha256": fingerprint(config),
            "service_contract_sha256": case["service_contract_sha256"],
            "spatial_sha256": case["spatial_sha256"], "time_limit_per_stage_seconds": budget,
            "requested_mip_gap": gap, "hours": len(frame)}
    write_json(directory / "run_record.json", meta)
    try:
        model = HeatPlanningModel(config, frame)
        identity = building_audit_identity(config, model.ts)
        anchors = root / OUTPUT / "input_anchors"
        anchors.mkdir(parents=True, exist_ok=True)
        anchor_path = anchors / (fingerprint({"directory": str(directory), **identity}) + ".json")
        if anchor_path.exists():
            raise FileExistsError("endpoint input anchor already exists")
        write_json(anchor_path, identity)
        meta["input_anchor_file"] = str(anchor_path.relative_to(root))
        meta["input_anchor_sha256"] = file_hash(anchor_path)
        write_json(directory / "model_config.json", config)
        model.ts.to_csv(directory / "model_timeseries.csv", index=False)
        build_started = perf_counter()
        model.build()
        build_seconds = perf_counter() - build_started
        # This invokes the existing two-stage core method directly. It retains
        # the primary bound and both actual solver states/gaps in summary.
        artifacts = model.solve_lexicographic(primary=primary, time_limit=budget, mip_gap=gap)
        summary = artifacts.summary
        summary.update(scenario_evidence_flags())
        summary.update(cost_reporting_fields(summary))
        for service in ("heat", "cooling"):
            coefs = model.metric_coefficients[f"building_{service}_delivered_kwh"]
            summary[f"building_{service}_delivered_kwh_per_year"] = float(sum(
                coefficient * artifacts.values[index] for index, coefficient in coefs.items()))
        audit = audit_building_trajectory(config, model.ts, artifacts.building_trajectory,
            dispatch=artifacts.dispatch, summary=summary, expected_input_identity=identity)
        summary["independent_building_audit_passed"] = audit["passed"]
        summary["independent_building_audit_metrics"] = audit["metrics"]
        summary["endpoint_claim"] = "two_stage_incumbent_with_explicit_primary_allowance_and_reported_gaps"
        summary["both_stages_meet_requested_gap"] = bool(
            summary["primary_solver_status"] == 0 and summary["solver_status"] == 0
            and summary.get("primary_mip_gap") is not None and summary.get("mip_gap") is not None
            and summary["primary_mip_gap"] <= gap and summary["mip_gap"] <= gap)
        artifacts.investments.to_csv(directory / "investments.csv", index=False)
        artifacts.dispatch.to_csv(directory / "dispatch.csv", index=False)
        artifacts.building_trajectory.to_csv(directory / "building_trajectory.csv", index=False)
        artifacts.building_comfort.to_csv(directory / "building_comfort.csv", index=False)
        np.savez_compressed(directory / "solution.npz", values=artifacts.values)
        pd.DataFrame({"variable": model.problem.names}).to_csv(directory / "variables.csv.gz", index=False)
        write_json(directory / "c6_building_audit.json", audit)
        write_json(directory / "summary.json", summary)
        profile = model_structure_profile(model, build_seconds=build_seconds,
                                         solve_seconds=summary["endpoint_solver_elapsed_seconds"])
        profile["wall_seconds"] = perf_counter() - started
        profile["binary_variables"] = sum(bool(i and lo >= 0 and hi <= 1) for i, lo, hi in zip(
            model.problem.integrality, model.problem.lower, model.problem.upper))
        write_json(directory / "model_profile.json", profile)
        if not (audit["passed"] and summary["numerical_audit_passed"]
                and summary["primary_numerical_audit_passed"] and before == code_identity(root)):
            raise ValueError("lexicographic incumbent failed audit / code stability")
        write_json(directory / "run_record.json", {**meta, "state": "audited_incumbent",
            "primary_status": summary["primary_solver_status"], "primary_gap": summary["primary_mip_gap"],
            "secondary_status": summary["solver_status"], "secondary_gap": summary["mip_gap"],
            "wall_seconds": perf_counter() - started})
        files = {str(p.relative_to(directory)): file_hash(p) for p in directory.rglob("*") if p.is_file()}
        write_json(directory / "manifest.json", {**meta, "complete": True, "files": files})
        return summary
    except Exception as error:
        write_json(directory / "failure.json", {**meta, "state": "failed", "message": str(error),
                                                "traceback": traceback.format_exc()})
        raise


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--time-limit", type=float, default=600.)
    parser.add_argument("--mip-gap", type=float, default=.005)
    parser.add_argument("--short-validation", action="store_true")
    parser.add_argument("--allow-heavy", action="store_true")
    args = parser.parse_args()
    if Path(args.run_id).name != args.run_id or not 0 < args.time_limit or not 0 <= args.mip_gap <= 1:
        parser.error("valid single-directory run-id, positive budget and gap in [0,1] required")
    if args.short_validation and args.time_limit > 60:
        parser.error("short validation is limited to 60 seconds per stage")
    if not args.short_validation and not args.allow_heavy:
        parser.error("formal representative frontier requires C0 frozen-epoch release")
    root = Path(__file__).resolve().parents[1]
    case = load_case(root)
    config = deepcopy(case["config"])
    config["planning"].pop("annual_scope", None)
    frame = case["representative"]
    if args.short_validation:
        annual = case["annual"]
        pieces = []
        for label, index in (("cold", annual.outdoor_temp.idxmin()), ("hot", annual.outdoor_temp.idxmax())):
            start = int(index // 24 * 24)
            piece = annual.iloc[start:start + 24].copy()
            piece["boundary_group"] = "short_lex_" + label
            piece["period_weight"] = 183.
            piece["scenario"] = "base"
            piece["scenario_probability"] = 1.
            pieces.append(piece)
        frame = pd.concat(pieces).sort_values("time").reset_index(drop=True)
    destination = root / OUTPUT / args.run_id
    destination.mkdir(parents=True, exist_ok=False)
    rows, failures = [], []
    frozen = code_identity(root)
    case["technology_changes"] += [{"orchestrator": str(Path(__file__).relative_to(root)),
        "orchestrator_sha256": file_hash(Path(__file__)), "endpoint_method": "core.solve_lexicographic",
        "short_validation": args.short_validation, "policy_changes": "none"}]
    with solver_lock(root):
        endpoints = {}
        for name, primary in (("cost_endpoint", "total_cost"), ("carbon_endpoint", "emissions")):
            try:
                summary = endpoint(root, destination / name, case, config, frame, primary, args.time_limit, args.mip_gap)
                endpoints[name] = summary
                rows.append({"run": name, "epsilon_kg": None, **summary})
            except Exception as error:
                failures.append({"run": name, "message": str(error)})
        if len(endpoints) == 2:
            low = float(endpoints["carbon_endpoint"]["total_service_emissions_kg_per_year"])
            high = float(endpoints["cost_endpoint"]["total_service_emissions_kg_per_year"])
            if low <= high + 1e-6:
                for index, cap in enumerate(np.linspace(low, high, 5), start=1):
                    name = f"epsilon_{index}"
                    effective = float(cap + 1e-7 + abs(cap) * 1e-8)
                    try:
                        if frozen != code_identity(root):
                            raise ValueError("core epoch changed between frontier points")
                        summary = run_solve(root, destination / name, case, config, frame,
                            scope="short_frontier_validation" if args.short_validation else "frontier_representative",
                            time_limit=args.time_limit, mip_gap=args.mip_gap, epsilon=effective)
                        rows.append({"run": name, "epsilon_kg": effective, **summary})
                    except Exception as error:
                        failures.append({"run": name, "message": str(error)})
            else:
                failures.append({"run": "epsilon_grid", "message": "incumbent endpoint carbon range inverted"})
    keys = ["run", "epsilon_kg", "objective_yuan_per_year", "total_service_emissions_kg_per_year",
            "solver_status", "mip_gap", "primary_objective", "primary_solver_status", "primary_mip_gap",
            "primary_objective_allowance", "both_stages_meet_requested_gap"]
    table = pd.DataFrame([{key: row.get(key) for key in keys} for row in rows], columns=keys)
    table.to_csv(destination / "all_points.csv", index=False)
    front = nondominated_cost_emission_points(table) if not table.empty else table
    front.to_csv(destination / "nondominated.csv", index=False)
    report = {"requested_epsilon_points": 5, "actual_epsilon_points": sum(r["run"].startswith("epsilon_") for r in rows),
              **scenario_evidence_flags(),
              "actual_nondominated_points": len(front), "failures": failures,
              "scope": "48h_method_validation" if args.short_validation else "sampled_816h_representative_frontier",
              "annual_frontier_certification": False,
              "endpoint_method": "actual_core_two_stage_lexicographic_with_reported_allowances_and_gaps",
              "cost_objective": "inherited_total_objective_including_carbon_and_exergy_preferences",
              "code_sha256": frozen, "orchestrator_sha256": file_hash(Path(__file__))}
    write_json(destination / "frontier_report.json", report)
    if not failures:
        files = {str(p.relative_to(destination)): file_hash(p) for p in destination.rglob("*") if p.is_file()}
        write_json(destination / "manifest.json", {"complete": True, "files": files, **report})
    print(json.dumps(report, ensure_ascii=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
