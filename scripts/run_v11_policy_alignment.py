from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from heatplan.io import load_inputs, validate_inputs  # noqa: E402
from heatplan.model import HeatPlanningModel  # noqa: E402
from heatplan.policy_alignment import (  # noqa: E402
    build_policy_action_register,
    build_policy_readiness_summary,
    evaluate_policy_alignment,
    load_policy_profile,
)
from heatplan.scenarios import build_correlated_northern_scenario_bundle  # noqa: E402


PUBLIC = ROOT / "data" / "public_scenario_inputs"
PROFILE = (
    ROOT
    / "data"
    / "policy_profiles"
    / "national_15th_fyp_reference_2026.yaml"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "v11_11_policy_alignment"


@dataclass(frozen=True)
class Case:
    name: str
    scenario_id: str
    config: Path
    timeseries: Path
    scenario_specs: Path | None = None


CASES = (
    Case(
        "scenario1_urumqi",
        "scenario1_existing_network",
        PUBLIC / "scenario1_urumqi_deterministic_public.yaml",
        PUBLIC / "scenario1_urumqi_representative_days_2023.csv",
    ),
    Case(
        "scenario2_guyuan",
        "scenario2_distributed_clean",
        PUBLIC / "scenario2_guyuan_evidence_public.yaml",
        PUBLIC / "scenario2_guyuan_representative_days_2023.csv",
        ROOT / "data" / "scenario2_scenario_specs.yaml",
    ),
    Case(
        "scenario3_wuhan",
        "scenario3_high_quality_newbuild",
        PUBLIC / "scenario3_wuhan_joint_public.yaml",
        PUBLIC / "scenario3_wuhan_representative_days_2023.csv",
    ),
)


def apply_scenario_bundle(
    config: dict,
    frame: pd.DataFrame,
    specification_path: Path,
) -> pd.DataFrame:
    specification = yaml.safe_load(
        specification_path.read_text(encoding="utf-8")
    )
    demand_columns = [
        column
        for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    bundle = build_correlated_northern_scenario_bundle(
        frame,
        demand_columns,
        list(specification["scenarios"]),
        period_weight=float(specification.get("period_weight", 1.0)),
    )
    config["planning"]["risk"] = dict(specification.get("risk", {}))
    validate_inputs(config, bundle)
    return bundle


def solve_case(
    case: Case,
    profile: dict,
    output: Path,
    *,
    as_of_date: str,
) -> dict[str, object]:
    config, frame = load_inputs(case.config, case.timeseries)
    if case.scenario_specs is not None:
        frame = apply_scenario_bundle(config, frame, case.scenario_specs)
    artifacts = HeatPlanningModel(config, frame).solve(time_limit=120.0)
    summary = artifacts.summary

    destination = output / case.name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    alignment = evaluate_policy_alignment(
        summary,
        profile,
        scenario_id=case.scenario_id,
        as_of_date=as_of_date,
    )
    alignment.insert(0, "case", case.name)
    alignment.to_csv(
        destination / "policy_alignment.csv",
        index=False,
        encoding="utf-8-sig",
    )
    alignment_records = (
        alignment.astype(object)
        .where(pd.notna(alignment), None)
        .to_dict(orient="records")
    )
    (destination / "policy_alignment.json").write_text(
        json.dumps(
            alignment_records,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "case": case.name,
        "solver_status": summary["solver_status"],
        "objective_yuan_per_year": summary["objective_yuan_per_year"],
        "clean_heat_share": summary["clean_heat_share"],
        "non_fossil_heat_share": summary["non_fossil_heat_share"],
        "waste_heat_share": summary["waste_heat_share"],
        "fossil_heat_share": summary["fossil_heat_share"],
        "emission_intensity_kg_per_kwh": summary[
            "emission_intensity_kg_per_kwh"
        ],
        "direct_fossil_emissions_kg_per_year": summary[
            "direct_fossil_emissions_kg_per_year"
        ],
        "heat_metering_coverage_fraction": summary[
            "heat_metering_coverage_fraction"
        ],
        "unserved_rate": summary["unserved_rate"],
        "worst_scenario_unserved_rate": summary[
            "worst_scenario_unserved_rate"
        ],
    }


def run_policy_alignment(
    output: Path = DEFAULT_OUTPUT,
    *,
    as_of_date: str = "2026-07-20",
) -> pd.DataFrame:
    profile = load_policy_profile(PROFILE)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = [
        solve_case(
            case,
            profile,
            output,
            as_of_date=as_of_date,
        )
        for case in CASES
    ]
    comparison = pd.DataFrame(rows)
    comparison.to_csv(
        output / "policy_alignment_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (output / "policy_alignment_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    alignment_frames = [
        pd.read_csv(output / case.name / "policy_alignment.csv")
        for case in CASES
    ]
    combined_alignment = pd.concat(alignment_frames, ignore_index=True)
    build_policy_readiness_summary(combined_alignment).to_csv(
        output / "policy_readiness_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    build_policy_action_register(combined_alignment).to_csv(
        output / "policy_action_register.csv",
        index=False,
        encoding="utf-8-sig",
    )
    return comparison


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Solve all three public cases and audit central-policy alignment"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of-date", default="2026-07-20")
    args = parser.parse_args()
    comparison = run_policy_alignment(
        args.output,
        as_of_date=args.as_of_date,
    )
    print(comparison.to_string(index=False))


if __name__ == "__main__":
    main()
