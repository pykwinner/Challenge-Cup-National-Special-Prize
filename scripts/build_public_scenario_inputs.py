from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from heatplan.io import load_config, validate_inputs
from heatplan.scenarios import extreme_aware_typical_days


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "data" / "public_sources"
OUTPUT = ROOT / "data" / "public_scenario_inputs"


PROFILES = {
    "scenario1_urumqi": {
        "scenario": "scenario1_existing_network",
        "config": OUTPUT / "scenario1_urumqi_public.yaml",
        "template": ROOT / "data" / "scenario1_existing_network_timeseries.csv",
        "weather": PUBLIC / "scenario1_urumqi_weather_2023.csv",
        "scenario_specs": ROOT / "data" / "scenario1_scenario_specs.yaml",
    },
    "scenario2_guyuan": {
        "scenario": "scenario2_distributed_clean",
        "config": ROOT / "data" / "scenario2_distributed_clean.yaml",
        "template": ROOT / "data" / "scenario2_timeseries.csv",
        "weather": PUBLIC / "scenario2_guyuan_weather_2023.csv",
        "scenario_specs": ROOT / "data" / "scenario2_scenario_specs.yaml",
        "alternative_configs": [OUTPUT / "scenario2_guyuan_evidence_public.yaml"],
        "resident_heating_tariff": {
            "valley_yuan_per_kwh": 0.2486,
            "peak_yuan_per_kwh": 0.4986,
            "valley_hours": [0, 1, 2, 3, 4, 5, 6, 7, 13, 14, 15, 16, 20, 21, 22, 23],
        },
    },
    "scenario3_wuhan": {
        "scenario": "scenario3_high_quality_newbuild",
        "config": OUTPUT / "scenario3_wuhan_public.yaml",
        "alternative_configs": [OUTPUT / "scenario3_wuhan_joint_public.yaml"],
        "template": ROOT / "data" / "scenario3_timeseries.csv",
        "weather": PUBLIC / "scenario3_wuhan_weather_2023.csv",
        "scenario_specs": ROOT / "data" / "scenario3_scenario_specs.yaml",
        "cooling_proxy": {
            "base_temperature_c": 24.0,
            "design_outdoor_temperature_c": 35.0,
            "natural_ventilation_temperature_c": 28.0,
            "natural_ventilation_relative_humidity": 0.70,
            "district_a_peak_kw": 440.0,
            "district_b_peak_kw": 360.0,
        },
    },
}


def expand_profile(
    profile: dict,
    *,
    weather_path: str | Path | None = None,
) -> tuple[dict, pd.DataFrame, list[str], list[str]]:
    config = load_config(profile["config"])
    template = pd.read_csv(profile["template"])
    weather = pd.read_csv(weather_path or profile["weather"])
    if len(template) != 24 or len(weather) not in {8760, 8784}:
        raise ValueError("模板必须为24小时，公开气象必须为8760或8784小时")
    repeated = template.iloc[np.arange(len(weather)) % 24].reset_index(drop=True).copy()
    repeated["time"] = weather["time"].astype(str)
    repeated["outdoor_temp"] = weather["outdoor_temp"].astype(float)
    if "relative_humidity" in repeated.columns:
        repeated["relative_humidity"] = weather["relative_humidity"].astype(float)
    if "solar_heat_availability" in repeated.columns:
        repeated["solar_heat_availability"] = weather["solar_heat_availability"].astype(float)
    tariff = profile.get("resident_heating_tariff")
    if tariff:
        hours = np.arange(len(repeated)) % 24
        repeated["resident_heating_electricity_price"] = np.where(
            np.isin(hours, np.asarray(tariff["valley_hours"], dtype=int)),
            float(tariff["valley_yuan_per_kwh"]),
            float(tariff["peak_yuan_per_kwh"]),
        )

    demand_columns = [
        column for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    reference_temperature = template["outdoor_temp"].astype(float).to_numpy()
    reference_degree = np.maximum(18.0 - reference_temperature, 1.0)
    actual_degree = np.maximum(18.0 - weather["outdoor_temp"].astype(float).to_numpy(), 0.0)
    demand_scale = np.clip(actual_degree / reference_degree[np.arange(len(weather)) % 24], 0.0, 1.8)
    for column in demand_columns:
        repeated[column] = repeated[column].astype(float) * demand_scale

    service_demand_columns = list(demand_columns)
    cooling_proxy = profile.get("cooling_proxy")
    if cooling_proxy:
        temperature = weather["outdoor_temp"].astype(float).to_numpy()
        humidity = weather["relative_humidity"].astype(float).to_numpy()
        base_temperature = float(cooling_proxy["base_temperature_c"])
        design_temperature = float(cooling_proxy["design_outdoor_temperature_c"])
        cooling_fraction = np.clip(
            (temperature - base_temperature)
            / max(design_temperature - base_temperature, 1e-9),
            0.0,
            1.20,
        )
        # Wuhan's draft low-energy residential standard explicitly prioritises
        # natural ventilation when T<=28 C and RH<=70%.  The proxy therefore
        # suppresses mechanical cooling only when both conditions are met.
        naturally_ventilated = (
            (temperature <= float(cooling_proxy["natural_ventilation_temperature_c"]))
            & (humidity <= float(cooling_proxy["natural_ventilation_relative_humidity"]))
        )
        cooling_fraction[naturally_ventilated] = 0.0
        for district, occupancy_column, setback in (
            ("a", "occupancy_a", 0.18),
            ("b", "occupancy_b", 0.20),
        ):
            occupancy = repeated[occupancy_column].astype(float).to_numpy()
            schedule = setback + (1.0 - setback) * occupancy
            column = f"cooling_demand_{district}"
            repeated[column] = (
                float(cooling_proxy[f"district_{district}_peak_kw"])
                * cooling_fraction
                * schedule
            )
            service_demand_columns.append(column)
    if "chp_minimum_electricity_kw" in repeated.columns:
        # The template describes a heating-season CHP obligation.  Repeating it
        # unchanged through summer forces heat production when space-heating
        # demand is zero and makes the annual model physically infeasible.
        repeated["chp_minimum_electricity_kw"] = (
            repeated["chp_minimum_electricity_kw"].astype(float)
            * np.clip(demand_scale, 0.0, 1.0)
        )

    referenced_source_columns = {
        str(source[key])
        for source in config["sources"]
        for key in (
            "availability_column", "source_temp_column",
            "minimum_electricity_output_column", "relative_humidity_column",
        )
        if source.get(key)
    }
    for legacy_column in ("waste_source_temp", "waste_heat_availability"):
        if legacy_column in repeated.columns and legacy_column not in referenced_source_columns:
            repeated = repeated.drop(columns=legacy_column)

    # Keep industrial/wind/resource patterns from the audited 24-hour template.
    # Weather-dependent temperature, humidity and solar availability are replaced
    # by the public hourly series above.
    repeated["timestamp"] = weather["time"].astype(str)
    return config, repeated, demand_columns, service_demand_columns


def representative_days(
    full_year: pd.DataFrame,
    demand_columns: list[str],
    clusters: int = 6,
) -> pd.DataFrame:
    features = ["outdoor_temp", *demand_columns]
    for optional in (
        "relative_humidity", "solar_heat_availability",
        "waste_heat_availability", "electricity_price",
    ):
        if optional in full_year.columns:
            features.append(optional)
    result = extreme_aware_typical_days(
        full_year,
        feature_columns=list(dict.fromkeys(features)),
        demand_columns=demand_columns,
        n_clusters=clusters,
        random_state=42,
    )
    result["boundary_group"] = result["representative_day"].astype(str)
    return result


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = {"profiles": []}
    metadata: dict[str, object] = {"profiles": {}, "assumptions": {
        "base_temperature_c": 18.0,
        "demand_method": "24-hour audited shape multiplied by hourly heating-degree ratio",
        "representative_day_clusters": 6,
        "weather_source": "data/public_sources NASA POWER 2023 prototype",
        "formal_use_warning": "replace/calibrate with CMA station and measured heat-load data",
    }}
    for name, profile in PROFILES.items():
        config, full_year, demand_columns, service_demand_columns = expand_profile(profile)
        selected = representative_days(full_year, service_demand_columns)
        validate_inputs(config, selected)
        alternative_configs = [
            Path(path) for path in profile.get("alternative_configs", [])
            if Path(path).exists()
        ]
        for alternative_config in alternative_configs:
            validate_inputs(load_config(alternative_config), selected)
        full_name = f"{name}_full_year_2023.csv"
        representative_name = f"{name}_representative_days_2023.csv"
        full_year.to_csv(OUTPUT / full_name, index=False, encoding="utf-8-sig")
        selected.to_csv(OUTPUT / representative_name, index=False, encoding="utf-8-sig")
        weights = selected.groupby("representative_day")["period_weight"].first()
        if not np.isclose(float(weights.sum()), 365.0):
            raise RuntimeError(f"{name} 典型日权重之和不是365天")
        manifest_row = {
            "name": name,
            "archetype": profile["scenario"],
            "config": Path(os.path.relpath(profile["config"], OUTPUT)).as_posix(),
            "timeseries": representative_name,
            "scenario_specs": Path(os.path.relpath(profile["scenario_specs"], OUTPUT)).as_posix(),
        }
        if alternative_configs:
            manifest_row["alternative_configs"] = [
                Path(os.path.relpath(path, OUTPUT)).as_posix()
                for path in alternative_configs
            ]
        manifest["profiles"].append(manifest_row)
        metadata["profiles"][name] = {
            "full_year_file": full_name,
            "full_year_rows": int(len(full_year)),
            "representative_file": representative_name,
            "representative_rows": int(len(selected)),
            "representative_days": int(selected["representative_day"].nunique()),
            "weight_days": float(weights.sum()),
            "extreme_days": selected.loc[selected["is_extreme_day"], "representative_day"].drop_duplicates().tolist(),
            "annual_demand_proxy_kwh": float(full_year[demand_columns].sum().sum()),
            "annual_cooling_proxy_kwh": float(
                full_year[
                    [column for column in service_demand_columns if column.startswith("cooling_")]
                ].sum().sum()
            ) if any(column.startswith("cooling_") for column in service_demand_columns) else 0.0,
        }
    (OUTPUT / "three_scenario_public_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (OUTPUT / "build_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
