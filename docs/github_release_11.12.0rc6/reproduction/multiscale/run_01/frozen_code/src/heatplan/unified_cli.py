from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pandas as pd
import yaml

from .io import load_inputs, validate_inputs
from .model import HeatPlanningModel
from .multistage import solve_joint_multistage
from .policy_alignment import evaluate_policy_alignment, load_policy_profile
from .reporting import export_results
from .scenarios import build_correlated_northern_scenario_bundle


def _path(base: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified planner for the three heating-transition archetypes")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", default="outputs/unified_three_scenarios")
    parser.add_argument("--time-limit", type=float, default=180.0)
    parser.add_argument(
        "--mip-gap",
        type=float,
        help=(
            "explicit relative MILP gap for all profiles; when omitted, each "
            "configuration uses its own planning.mip_gap"
        ),
    )
    parser.add_argument(
        "--policy-profile",
        help="optional policy-profile YAML; overrides manifest policy_profile",
    )
    args = parser.parse_args()
    if args.mip_gap is not None and not 0.0 <= args.mip_gap <= 1.0:
        parser.error("--mip-gap must be between 0 and 1")

    manifest_path = Path(args.manifest).resolve()
    base = manifest_path.parent
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    comparison: list[dict] = []
    alignment_rows: list[pd.DataFrame] = []
    profile_value = args.policy_profile or manifest.get("policy_profile")
    policy_profile_path = _path(base, profile_value)
    policy_profile = (
        load_policy_profile(policy_profile_path)
        if policy_profile_path is not None
        else None
    )

    for profile in manifest["profiles"]:
        name = str(profile["name"])
        profile_output = output_root / name
        profile_output.mkdir(parents=True, exist_ok=True)
        config_path = _path(base, profile["config"])
        timeseries_path = _path(base, profile["timeseries"])
        scenario_specs_path = _path(base, profile.get("scenario_specs"))
        pathway_path = _path(base, profile.get("policy_pathway"))
        feasibility_path = _path(base, profile.get("feasibility"))

        if pathway_path:
            artifacts = solve_joint_multistage(
                config_path, timeseries_path, scenario_specs_path, pathway_path,
                feasibility_path=feasibility_path,
                time_limit=args.time_limit,
                mip_gap=args.mip_gap,
            )
            artifacts.stage_summary.to_csv(profile_output / "stage_summary.csv", index=False)
            (profile_output / "joint_summary.json").write_text(
                json.dumps(artifacts.summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            artifacts.build_schedule.to_csv(profile_output / "build_schedule.csv", index=False)
            artifacts.cumulative_investments.to_csv(profile_output / "cumulative_investments.csv", index=False)
            artifacts.dispatch.to_csv(profile_output / "dispatch.csv", index=False)
            artifacts.hydraulic_postcheck.to_csv(profile_output / "hydraulic_postcheck.csv", index=False)
            if artifacts.finance:
                artifacts.finance.cashflow.to_csv(profile_output / "finance_cashflow.csv", index=False)
                (profile_output / "finance_metrics.json").write_text(
                    json.dumps(artifacts.finance.metrics, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            summary = artifacts.stage_summary.iloc[-1].to_dict()
        else:
            config, frame = load_inputs(config_path, timeseries_path)
            if scenario_specs_path:
                specs = yaml.safe_load(scenario_specs_path.read_text(encoding="utf-8"))
                demand_columns = [
                    column for node in config["nodes"]
                    for column in node.get("demand_columns", {}).values()
                ]
                frame = build_correlated_northern_scenario_bundle(
                    frame, demand_columns, specs["scenarios"],
                    period_weight=float(specs.get("period_weight", config["planning"].get("representative_day_weight", 1.0))),
                )
                config = copy.deepcopy(config)
                config["planning"]["risk"] = dict(specs.get("risk", {}))
                validate_inputs(config, frame)
            model = HeatPlanningModel(config, frame)
            artifacts = model.solve(
                mip_gap=args.mip_gap,
                time_limit=args.time_limit,
            )
            export_results(profile_output, artifacts, config, model.ts)
            summary = {key: value for key, value in artifacts.summary.items() if not isinstance(value, dict)}
            (profile_output / "scenario_results.json").write_text(
                json.dumps(artifacts.summary.get("scenario_results", {}), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        comparison.append({
            "profile": name,
            "archetype": profile.get("archetype", ""),
            **summary,
        })
        if policy_profile is not None:
            alignment = evaluate_policy_alignment(summary, policy_profile)
            alignment.insert(0, "profile", name)
            alignment.to_csv(
                profile_output / "policy_alignment.csv",
                index=False,
                encoding="utf-8-sig",
            )
            alignment_rows.append(alignment)

    result = pd.json_normalize(comparison)
    result.to_csv(output_root / "three_scenario_comparison.csv", index=False)
    (output_root / "run_configuration.json").write_text(
        json.dumps(
            {
                "time_limit_seconds_per_profile": args.time_limit,
                "requested_mip_gap": args.mip_gap,
                "mip_gap_scope": (
                    "explicit_cli_override"
                    if args.mip_gap is not None
                    else "profile_configuration_default"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if alignment_rows:
        pd.concat(alignment_rows, ignore_index=True).to_csv(
            output_root / "three_scenario_policy_alignment.csv",
            index=False,
            encoding="utf-8-sig",
        )
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
