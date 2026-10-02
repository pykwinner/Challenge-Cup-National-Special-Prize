from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from heatplan.chronology import annual_replay_preflight
from heatplan.io import load_config, validate_inputs
from heatplan.model import HeatPlanningModel
from heatplan.scenarios import (
    calibrate_representative_day_weights,
    extreme_aware_typical_days,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "data" / "public_scenario_inputs"
DEFAULT_OUTPUT = ROOT / "outputs" / "v11_4_representative_days"

CASES = {
    "scenario1_urumqi": "scenario1_urumqi_deterministic_public.yaml",
    "scenario2_guyuan": "scenario2_guyuan_evidence_public.yaml",
    "scenario3_wuhan": "scenario3_wuhan_joint_public.yaml",
}


def service_demand_columns(config: dict) -> list[str]:
    columns = [
        str(column)
        for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    cooling = config.get("multi_energy_services", {}).get("cooling", {})
    columns.extend(str(value) for value in cooling.get("demand_columns", {}).values())
    return list(dict.fromkeys(columns))


def feature_columns(full_year: pd.DataFrame, service_columns: list[str]) -> list[str]:
    columns = ["outdoor_temp", *service_columns]
    for optional in (
        "relative_humidity",
        "solar_heat_availability",
        "waste_heat_availability",
        "electricity_price",
        "resident_heating_electricity_price",
        "grid_emission_factor",
    ):
        if optional in full_year.columns:
            columns.append(optional)
    return list(dict.fromkeys(columns))


def minimum_capture(audit: pd.DataFrame, field: str) -> float | None:
    demand = audit[audit["series_kind"] == "demand"][field].dropna()
    return float(demand.min()) if len(demand) else None


def add_model_derived_anchor_columns(
    config: dict, full_year: pd.DataFrame
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Add effective-load and worst-COP scores used only for anchor selection."""

    frame = full_year.copy()
    probe = HeatPlanningModel(config, full_year)
    effective_columns: list[str] = []
    for node_name, node in probe.nodes.items():
        for grade_name, column in node.get("demand_columns", {}).items():
            proxy = f"__effective_demand__{node_name}__{grade_name}"
            frame[proxy] = [
                probe._demand_value(node, str(column), t) for t in probe.times
            ]
            effective_columns.append(proxy)
    score_columns: list[str] = []
    for source_name, source in probe.sources.items():
        if source.get("type") != "heat_pump":
            continue
        allowed_grades = source.get("allowed_grades", list(probe.grades))
        proxy = f"__worst_electric_ratio__{source_name}"
        frame[proxy] = [
            max(
                1.0 / probe._cop(source, probe.grades[grade_name], t)
                for grade_name in allowed_grades
            )
            for t in probe.times
        ]
        score_columns.append(proxy)
    return frame, effective_columns, score_columns


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build V11.4 series-extreme and weight-calibrated candidates"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--clusters", type=int, default=6)
    parser.add_argument("--regularization", type=float, default=1e-6)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    summaries: list[dict] = []
    candidate_manifest = {"profiles": []}
    for name, config_name in CASES.items():
        config_path = PUBLIC / config_name
        original_path = PUBLIC / f"{name}_representative_days_2023.csv"
        annual_path = PUBLIC / f"{name}_full_year_2023.csv"
        config = load_config(config_path)
        original = pd.read_csv(original_path)
        full_year = pd.read_csv(annual_path)
        service_columns = service_demand_columns(config)
        anchor_frame, effective_columns, score_columns = (
            add_model_derived_anchor_columns(config, full_year)
        )
        candidate = extreme_aware_typical_days(
            anchor_frame,
            feature_columns=feature_columns(full_year, service_columns),
            demand_columns=[*service_columns, *effective_columns],
            n_clusters=args.clusters,
            random_state=42,
            anchor_score_columns=score_columns,
        )
        candidate = candidate.drop(
            columns=[*effective_columns, *score_columns], errors="ignore"
        )
        candidate["boundary_group"] = candidate["representative_day"].astype(str)
        calibration = calibrate_representative_day_weights(
            candidate,
            full_year,
            service_columns,
            time_step_hours=float(
                config["planning"].get("time_step_hours", 1.0)
            ),
            regularization=args.regularization,
        )
        calibrated = calibration.timeseries
        validate_inputs(config, calibrated)

        case_output = output / name
        case_output.mkdir(parents=True, exist_ok=True)
        candidate.to_csv(
            case_output / "representative_days_series_extremes.csv",
            index=False,
            encoding="utf-8-sig",
        )
        calibrated_path = case_output / "representative_days_calibrated.csv"
        calibrated.to_csv(calibrated_path, index=False, encoding="utf-8-sig")
        calibration.weights.to_csv(
            case_output / "weight_calibration_audit.csv",
            index=False,
            encoding="utf-8-sig",
        )
        calibration.diagnostics.to_csv(
            case_output / "weight_calibration_diagnostics.csv",
            index=False,
            encoding="utf-8-sig",
        )
        (case_output / "weight_calibration_summary.json").write_text(
            json.dumps(calibration.summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        before = annual_replay_preflight(
            config,
            original,
            full_year,
            timestamp_column="time",
        ).representative_coverage_audit
        after = annual_replay_preflight(
            config,
            calibrated,
            full_year,
            timestamp_column="time",
        ).representative_coverage_audit
        before.assign(variant="before").to_csv(
            case_output / "coverage_before.csv", index=False, encoding="utf-8-sig"
        )
        after.assign(variant="after").to_csv(
            case_output / "coverage_after.csv", index=False, encoding="utf-8-sig"
        )
        comparison = pd.concat(
            [before.assign(variant="before"), after.assign(variant="after")],
            ignore_index=True,
        )
        comparison.to_csv(
            case_output / "coverage_before_after.csv",
            index=False,
            encoding="utf-8-sig",
        )
        original_weights = original.groupby("representative_day")[
            "period_weight"
        ].first()
        calibrated_weights = calibrated.groupby("representative_day")[
            "period_weight"
        ].first()
        summary = {
            "profile": name,
            "original_representative_days": int(original["representative_day"].nunique()),
            "candidate_representative_days": int(calibrated["representative_day"].nunique()),
            "original_extreme_days": int(
                original.groupby("representative_day")["is_extreme_day"].first().sum()
            ),
            "candidate_extreme_days": int(
                calibrated.groupby("representative_day")["is_extreme_day"].first().sum()
            ),
            "original_weight_sum_days": float(original_weights.sum()),
            "candidate_weight_sum_days": float(calibrated_weights.sum()),
            "review_count_before": int((before["coverage_status"] == "review").sum()),
            "review_count_after": int((after["coverage_status"] == "review").sum()),
            "minimum_demand_peak_capture_before": minimum_capture(before, "peak_capture_ratio"),
            "minimum_demand_peak_capture_after": minimum_capture(after, "peak_capture_ratio"),
            "minimum_demand_ramp_capture_before": minimum_capture(before, "ramp_capture_ratio"),
            "minimum_demand_ramp_capture_after": minimum_capture(after, "ramp_capture_ratio"),
            "maximum_absolute_energy_bias_before": float(
                before.loc[before["series_kind"] == "demand", "total_relative_bias"].abs().max()
            ),
            "maximum_absolute_energy_bias_after": float(
                after.loc[after["series_kind"] == "demand", "total_relative_bias"].abs().max()
            ),
            "calibration_maximum_bias_before": calibration.summary[
                "maximum_absolute_original_bias"
            ],
            "calibration_maximum_bias_after": calibration.summary[
                "maximum_absolute_calibrated_bias"
            ],
            "source_file_modified": False,
            "candidate_file": str(calibrated_path),
        }
        (case_output / "representative_quality_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summaries.append(summary)
        candidate_manifest["profiles"].append(
            {
                "name": name,
                "config": str(config_path.resolve()),
                "representative_timeseries": str(calibrated_path.resolve()),
                "annual_timeseries": str(annual_path.resolve()),
                "timestamp_column": "time",
            }
        )

    result = pd.DataFrame(summaries)
    result.to_csv(
        output / "representative_quality_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (output / "candidate_annual_preflight_manifest.yaml").write_text(
        yaml.safe_dump(candidate_manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
