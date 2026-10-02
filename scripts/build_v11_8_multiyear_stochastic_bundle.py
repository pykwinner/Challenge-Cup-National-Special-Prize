from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from heatplan.io import load_config, validate_inputs
from heatplan.representative import (
    adapt_config_to_weather_year_scenarios,
    build_multiyear_stochastic_representative_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT
    / "data"
    / "public_scenario_multiyear"
    / "three_scenario_multiyear_manifest.yaml"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "v11_8_multiyear_stochastic_bundle"


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def recency_probabilities(years: list[str], decay: float) -> dict[str, float]:
    if not 0.0 < decay <= 1.0:
        raise ValueError("recency_decay 必须位于 (0,1]")
    raw = [decay ** (len(years) - 1 - index) for index in range(len(years))]
    total = sum(raw)
    return {year: value / total for year, value in zip(years, raw)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build V11.8 weather-year scenarios for two-stage CVaR planning"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--clusters-per-year", type=int, default=4)
    parser.add_argument("--regularization", type=float, default=1e-6)
    parser.add_argument("--cvar-alpha", type=float, default=0.80)
    parser.add_argument("--cvar-weight", type=float, default=0.05)
    parser.add_argument(
        "--recency-decay",
        type=float,
        default=1.0,
        help="1.0 gives equal weather-year probabilities; smaller values weight recent years more",
    )
    args = parser.parse_args()
    if not 0.0 < args.cvar_alpha < 1.0:
        raise ValueError("cvar_alpha 必须位于 (0,1)")
    if args.cvar_weight < 0.0:
        raise ValueError("cvar_weight 不得为负")

    manifest_path = args.manifest.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    profiles = payload.get("profiles", [])
    if not profiles:
        raise ValueError("跨年度清单至少需要一个 profile")

    generated_manifest: dict[str, Any] = {
        "method": "weather_year_scenarios_shared_investment_cvar",
        "source_manifest": str(manifest_path),
        "cvar_alpha": args.cvar_alpha,
        "cvar_weight": args.cvar_weight,
        "recency_decay": args.recency_decay,
        "profiles": [],
    }
    summaries: list[dict[str, Any]] = []
    for profile in profiles:
        name = str(profile["name"])
        config_path = resolve(manifest_path.parent, str(profile["config"]))
        config = deepcopy(load_config(config_path))
        annual_paths = {
            str(item["year"]): resolve(manifest_path.parent, str(item["path"]))
            for item in profile.get("annual_timeseries", [])
        }
        annual = {
            year: pd.read_csv(path) for year, path in annual_paths.items()
        }
        probabilities = recency_probabilities(
            list(annual), args.recency_decay
        )
        risk = dict(config["planning"].get("risk", {}))
        risk.update(
            {
                "cvar_alpha": args.cvar_alpha,
                "cvar_weight": args.cvar_weight,
            }
        )
        config["planning"]["risk"] = risk
        artifacts = build_multiyear_stochastic_representative_bundle(
            config,
            annual,
            scenario_probabilities=probabilities,
            n_clusters_per_year=args.clusters_per_year,
            regularization=args.regularization,
        )
        bundle = artifacts.scenario_bundle
        config, scenario_mapping_audit = adapt_config_to_weather_year_scenarios(
            config, bundle
        )
        validate_inputs(config, bundle)

        case_output = output / name
        case_output.mkdir(parents=True, exist_ok=True)
        bundle_path = case_output / "weather_year_scenario_bundle.csv"
        config_output = case_output / "stochastic_planning_config.yaml"
        bundle.to_csv(bundle_path, index=False, encoding="utf-8-sig")
        config_output.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        artifacts.scenario_summary.to_csv(
            case_output / "weather_year_scenario_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )
        (case_output / "weather_year_policy_scope_audit.json").write_text(
            json.dumps(
                scenario_mapping_audit, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        for year, year_artifacts in artifacts.year_artifacts.items():
            year_output = case_output / year
            year_output.mkdir(exist_ok=True)
            year_artifacts.calibration.timeseries.to_csv(
                year_output / "representative_days_calibrated.csv",
                index=False,
                encoding="utf-8-sig",
            )
            year_artifacts.calibration.weights.to_csv(
                year_output / "weight_calibration_audit.csv",
                index=False,
                encoding="utf-8-sig",
            )
            year_artifacts.calibration.diagnostics.to_csv(
                year_output / "weight_calibration_diagnostics.csv",
                index=False,
                encoding="utf-8-sig",
            )
            (year_output / "weight_calibration_summary.json").write_text(
                json.dumps(
                    year_artifacts.calibration.summary,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        scenario_summary = artifacts.scenario_summary
        summaries.append(
            {
                "profile": name,
                "weather_years": len(artifacts.years),
                "scenario_bundle_rows": int(len(bundle)),
                "representative_days": int(
                    scenario_summary["representative_days"].sum()
                ),
                "extreme_days": int(scenario_summary["extreme_days"].sum()),
                "expected_calendar_days": float(
                    (
                        scenario_summary["occurrence_weight_days"]
                        * scenario_summary["scenario_probability"]
                    ).sum()
                ),
                "maximum_absolute_energy_bias": float(
                    scenario_summary["maximum_absolute_energy_bias"].max()
                ),
                "cvar_alpha": args.cvar_alpha,
                "cvar_weight": args.cvar_weight,
                "recency_decay": args.recency_decay,
                "bundle_file": str(bundle_path),
            }
        )
        generated_manifest["profiles"].append(
            {
                "name": name,
                "config": str(config_output),
                "representative_timeseries": str(bundle_path),
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

    summary = pd.DataFrame(summaries)
    summary.to_csv(
        output / "stochastic_bundle_quality_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    manifest_output = output / "stochastic_multiyear_manifest.yaml"
    manifest_output.write_text(
        yaml.safe_dump(
            generated_manifest, allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )
    print(summary.to_string(index=False))
    print(f"\nmanifest={manifest_output}")


if __name__ == "__main__":
    main()
