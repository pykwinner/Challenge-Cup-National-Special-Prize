from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from heatplan.io import load_config, validate_inputs
from heatplan.model import HeatPlanningModel
from heatplan.reporting import export_results


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "data" / "public_scenario_inputs"
CANDIDATES = ROOT / "outputs" / "v11_4_representative_days"
DEFAULT_OUTPUT = ROOT / "outputs" / "v11_4_investment_stability"

CASES = {
    "scenario1_urumqi": "scenario1_urumqi_deterministic_public.yaml",
    "scenario2_guyuan": "scenario2_guyuan_evidence_public.yaml",
    "scenario3_wuhan": "scenario3_wuhan_joint_public.yaml",
}


def solve_case(
    config: dict,
    frame: pd.DataFrame,
    *,
    mip_gap: float,
    time_limit: float,
):
    validate_inputs(config, frame)
    model = HeatPlanningModel(config, frame)
    started = perf_counter()
    artifacts = model.solve(mip_gap=mip_gap, time_limit=time_limit)
    elapsed = perf_counter() - started
    return model, artifacts, elapsed


def investment_comparison(
    original: pd.DataFrame, candidate: pd.DataFrame
) -> pd.DataFrame:
    keys = ["category", "asset", "option", "unit"]
    comparison = original[keys + ["value"]].merge(
        candidate[keys + ["value"]],
        on=keys,
        how="outer",
        suffixes=("_original", "_candidate"),
    )
    comparison[["value_original", "value_candidate"]] = comparison[
        ["value_original", "value_candidate"]
    ].fillna(0.0)
    comparison["absolute_change"] = (
        comparison["value_candidate"] - comparison["value_original"]
    )
    scale = np.maximum.reduce(
        [
            comparison["value_original"].abs().to_numpy(),
            comparison["value_candidate"].abs().to_numpy(),
            np.ones(len(comparison)),
        ]
    )
    comparison["relative_change_to_larger_plan"] = (
        comparison["absolute_change"].abs().to_numpy() / scale
    )
    comparison["binary_changed"] = (
        comparison["unit"].eq("binary")
        & comparison["absolute_change"].abs().gt(0.5)
    )
    comparison["material_change"] = (
        comparison["absolute_change"].abs().gt(1e-5)
        & comparison["relative_change_to_larger_plan"].gt(0.10)
    )
    return comparison.sort_values(
        ["material_change", "relative_change_to_larger_plan"],
        ascending=[False, False],
    ).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare original and V11.4 candidate representative-day investments"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mip-gap", type=float, default=0.01)
    parser.add_argument("--time-limit", type=float, default=180.0)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for name, config_name in CASES.items():
        config = load_config(PUBLIC / config_name)
        original = pd.read_csv(
            PUBLIC / f"{name}_representative_days_2023.csv"
        )
        candidate = pd.read_csv(
            CANDIDATES / name / "representative_days_calibrated.csv"
        )
        original_model, original_result, original_seconds = solve_case(
            config,
            original,
            mip_gap=args.mip_gap,
            time_limit=args.time_limit,
        )
        candidate_model, candidate_result, candidate_seconds = solve_case(
            config,
            candidate,
            mip_gap=args.mip_gap,
            time_limit=args.time_limit,
        )
        case_output = output / name
        export_results(
            case_output / "original",
            original_result,
            config,
            original_model.ts,
        )
        export_results(
            case_output / "candidate",
            candidate_result,
            config,
            candidate_model.ts,
        )
        comparison = investment_comparison(
            original_result.investments, candidate_result.investments
        )
        comparison.to_csv(
            case_output / "investment_comparison.csv",
            index=False,
            encoding="utf-8-sig",
        )
        units = (
            comparison.groupby("unit", dropna=False)["absolute_change"]
            .apply(lambda values: float(values.abs().sum()))
            .to_dict()
        )
        row = {
            "profile": name,
            "original_solver_status": original_result.summary["solver_status"],
            "candidate_solver_status": candidate_result.summary["solver_status"],
            "original_mip_gap": original_result.summary["mip_gap"],
            "candidate_mip_gap": candidate_result.summary["mip_gap"],
            "original_total_seconds": original_seconds,
            "candidate_total_seconds": candidate_seconds,
            "original_objective_yuan_per_year": original_result.summary[
                "objective_yuan_per_year"
            ],
            "candidate_objective_yuan_per_year": candidate_result.summary[
                "objective_yuan_per_year"
            ],
            "objective_relative_change": (
                candidate_result.summary["objective_yuan_per_year"]
                / original_result.summary["objective_yuan_per_year"]
                - 1.0
            ),
            "original_unserved_rate": original_result.summary["unserved_rate"],
            "candidate_unserved_rate": candidate_result.summary["unserved_rate"],
            "binary_change_count": int(comparison["binary_changed"].sum()),
            "material_investment_change_count": int(
                comparison["material_change"].sum()
            ),
            "maximum_relative_investment_change": float(
                comparison["relative_change_to_larger_plan"].max()
            ),
            "absolute_change_by_unit": json.dumps(
                units, ensure_ascii=False, sort_keys=True
            ),
            "comparison_status": (
                "stable"
                if not bool(comparison["material_change"].any())
                else "review_material_changes"
            ),
        }
        (case_output / "investment_stability_summary.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rows.append(row)

    result = pd.DataFrame(rows)
    result.to_csv(
        output / "investment_stability_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
