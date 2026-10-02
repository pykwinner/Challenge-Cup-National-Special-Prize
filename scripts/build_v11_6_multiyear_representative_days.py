from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from heatplan.chronology import annual_replay_preflight
from heatplan.io import load_config, validate_inputs
from heatplan.representative import build_multiyear_representative_days


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT
    / "data"
    / "public_scenario_multiyear"
    / "three_scenario_multiyear_manifest.yaml"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "v11_6_multiyear_representative_days"


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def demand_minimum(audit: pd.DataFrame, column: str) -> float | None:
    values = audit.loc[audit["series_kind"] == "demand", column].dropna()
    return float(values.min()) if len(values) else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build V11.6 multi-weather-year extreme-aware representative days"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--clusters", type=int, default=8)
    parser.add_argument("--regularization", type=float, default=1e-6)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    profiles = payload.get("profiles", [])
    if not profiles:
        raise ValueError("跨年度清单至少需要一个 profile")

    summaries: list[dict[str, Any]] = []
    robust_manifest: dict[str, Any] = {
        "method": "five_year_joint_extreme_aware_representative_days",
        "source_manifest": str(manifest_path),
        "profiles": [],
    }
    for profile in profiles:
        name = str(profile["name"])
        config_path = resolve(manifest_path.parent, str(profile["config"]))
        original_path = resolve(
            manifest_path.parent, str(profile["representative_timeseries"])
        )
        config = load_config(config_path)
        original = pd.read_csv(original_path)
        annual_paths = {
            str(item["year"]): resolve(manifest_path.parent, str(item["path"]))
            for item in profile.get("annual_timeseries", [])
        }
        annual = {
            year: pd.read_csv(path) for year, path in annual_paths.items()
        }
        artifacts = build_multiyear_representative_days(
            config,
            annual,
            n_clusters=args.clusters,
            regularization=args.regularization,
        )
        calibrated = artifacts.calibration.timeseries
        validate_inputs(config, calibrated)

        case_output = output / name
        case_output.mkdir(parents=True, exist_ok=True)
        artifacts.candidate_timeseries.to_csv(
            case_output / "representative_days_multiyear_extremes.csv",
            index=False,
            encoding="utf-8-sig",
        )
        calibrated_path = case_output / "representative_days_calibrated.csv"
        calibrated.to_csv(calibrated_path, index=False, encoding="utf-8-sig")
        artifacts.calibration.weights.to_csv(
            case_output / "weight_calibration_audit.csv",
            index=False,
            encoding="utf-8-sig",
        )
        artifacts.calibration.diagnostics.to_csv(
            case_output / "weight_calibration_diagnostics.csv",
            index=False,
            encoding="utf-8-sig",
        )
        (case_output / "weight_calibration_summary.json").write_text(
            json.dumps(
                artifacts.calibration.summary, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )

        coverage_blocks: list[pd.DataFrame] = []
        review_before = 0
        review_after = 0
        minimum_peak_after: list[float] = []
        minimum_ramp_after: list[float] = []
        for year, chronology in annual.items():
            before = annual_replay_preflight(
                config,
                original,
                chronology,
                timestamp_column="time",
                require_full_year=True,
            ).representative_coverage_audit
            after = annual_replay_preflight(
                config,
                calibrated,
                chronology,
                timestamp_column="time",
                require_full_year=True,
            ).representative_coverage_audit
            for variant, audit in (("before", before), ("after", after)):
                coverage_blocks.append(
                    audit.assign(weather_year=year, variant=variant)
                )
            review_before += int((before["coverage_status"] == "review").sum())
            review_after += int((after["coverage_status"] == "review").sum())
            peak = demand_minimum(after, "peak_capture_ratio")
            ramp = demand_minimum(after, "ramp_capture_ratio")
            if peak is not None:
                minimum_peak_after.append(peak)
            if ramp is not None:
                minimum_ramp_after.append(ramp)
        coverage = pd.concat(coverage_blocks, ignore_index=True)
        coverage.to_csv(
            case_output / "five_year_coverage_before_after.csv",
            index=False,
            encoding="utf-8-sig",
        )

        day_rows = calibrated.groupby("representative_day", sort=False).first()
        weights = day_rows["period_weight"].astype(float)
        extreme = day_rows[day_rows["is_extreme_day"].astype(bool)]
        summary = {
            "profile": name,
            "weather_years": ";".join(artifacts.years),
            "weather_year_count": len(artifacts.years),
            "source_days": int(len(artifacts.combined_chronology) / 24),
            "representative_days": int(len(day_rows)),
            "extreme_days": int(len(extreme)),
            "extreme_source_years": ";".join(
                sorted(set(extreme["source_weather_year"].astype(str)))
            ),
            "average_year_weight_days": float(weights.sum()),
            "review_count_before_across_years": review_before,
            "review_count_after_across_years": review_after,
            "minimum_demand_peak_capture_after": min(minimum_peak_after),
            "minimum_demand_ramp_capture_after": min(minimum_ramp_after),
            "maximum_absolute_energy_bias_after": artifacts.calibration.summary[
                "maximum_absolute_calibrated_bias"
            ],
            "candidate_file": str(calibrated_path),
        }
        summaries.append(summary)
        (case_output / "representative_quality_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        robust_manifest["profiles"].append(
            {
                "name": name,
                "config": str(config_path),
                "representative_timeseries": str(calibrated_path),
                "annual_timeseries": [
                    {
                        "year": int(year),
                        "path": str(path),
                        "timestamp_column": "time",
                    }
                    for year, path in annual_paths.items()
                ],
            }
        )

    summary_frame = pd.DataFrame(summaries)
    summary_frame.to_csv(
        output / "representative_quality_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    manifest_output = output / "robust_multiyear_manifest.yaml"
    manifest_output.write_text(
        yaml.safe_dump(robust_manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(summary_frame.to_string(index=False))
    print(f"\nmanifest={manifest_output}")


if __name__ == "__main__":
    main()
