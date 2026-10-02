from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd
import yaml

from build_public_scenario_inputs import PROFILES, expand_profile
from heatplan.io import load_config, validate_inputs


ROOT = Path(__file__).resolve().parents[1]
WEATHER = ROOT / "data" / "public_sources"
OUTPUT = ROOT / "data" / "public_scenario_multiyear"
REPRESENTATIVE = ROOT / "outputs" / "v11_4_representative_days"

REPLAY_CONFIGS = {
    "scenario1_urumqi": (
        ROOT
        / "data"
        / "public_scenario_inputs"
        / "scenario1_urumqi_deterministic_public.yaml"
    ),
    "scenario2_guyuan": (
        ROOT
        / "data"
        / "public_scenario_inputs"
        / "scenario2_guyuan_evidence_public.yaml"
    ),
    "scenario3_wuhan": (
        ROOT
        / "data"
        / "public_scenario_inputs"
        / "scenario3_wuhan_joint_public.yaml"
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用多年度NASA POWER气象构建三类场景连续年度输入"
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=[2020, 2021, 2022, 2023, 2024],
    )
    args = parser.parse_args()
    years = sorted(set(args.years))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"profiles": []}
    metadata: dict[str, object] = {
        "weather_provider": "NASA POWER Hourly API",
        "time_standard": "local_solar_time",
        "years": years,
        "profiles": {},
        "caveat": "格点/再分析气象与规则化负荷代理；正式项目需用CMA站点和热表实测校准",
    }
    for name, profile in PROFILES.items():
        profile_output = OUTPUT / name
        profile_output.mkdir(parents=True, exist_ok=True)
        replay_config_path = REPLAY_CONFIGS[name]
        replay_config = load_config(replay_config_path)
        annual_rows = []
        profile_meta = []
        for year in years:
            weather_path = WEATHER / f"{name}_weather_{year}.csv"
            if not weather_path.exists():
                raise FileNotFoundError(weather_path)
            _, annual, heat_columns, service_columns = expand_profile(
                profile, weather_path=weather_path
            )
            validate_inputs(replay_config, annual)
            filename = f"{name}_full_year_{year}.csv"
            annual.to_csv(
                profile_output / filename,
                index=False,
                encoding="utf-8-sig",
            )
            cooling_columns = [
                column for column in service_columns if column.startswith("cooling_")
            ]
            annual_rows.append(
                {
                    "year": year,
                    "path": f"{name}/{filename}",
                    "timestamp_column": "time",
                }
            )
            profile_meta.append(
                {
                    "year": year,
                    "rows": int(len(annual)),
                    "minimum_outdoor_temp_c": float(annual["outdoor_temp"].min()),
                    "maximum_outdoor_temp_c": float(annual["outdoor_temp"].max()),
                    "annual_heat_demand_proxy_kwh": float(
                        annual[heat_columns].sum().sum()
                    ),
                    "peak_heat_demand_proxy_kw": float(
                        annual[heat_columns].sum(axis=1).max()
                    ),
                    "annual_cooling_demand_proxy_kwh": float(
                        annual[cooling_columns].sum().sum()
                    )
                    if cooling_columns
                    else 0.0,
                    "peak_cooling_demand_proxy_kw": float(
                        annual[cooling_columns].sum(axis=1).max()
                    )
                    if cooling_columns
                    else 0.0,
                }
            )
        representative_path = (
            REPRESENTATIVE / name / "representative_days_calibrated.csv"
        )
        manifest["profiles"].append(
            {
                "name": name,
                "config": Path(
                    os.path.relpath(replay_config_path, OUTPUT)
                ).as_posix(),
                "representative_timeseries": Path(
                    os.path.relpath(representative_path, OUTPUT)
                ).as_posix(),
                "annual_timeseries": annual_rows,
            }
        )
        metadata["profiles"][name] = profile_meta
    (OUTPUT / "three_scenario_multiyear_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (OUTPUT / "build_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
