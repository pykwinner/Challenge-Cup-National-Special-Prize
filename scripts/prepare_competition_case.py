"""Build traceable Wuhan RC candidate inputs without solving or changing raw data.

Run: python scripts/prepare_competition_case.py
The two small NASA clock responses are cached in the output evidence directory;
normal regeneration is offline. See that directory's README for evidence limits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from heatplan.io import load_config, validate_inputs  # noqa: E402

OUTPUT = ROOT / "data" / "competition_20261001"
PARENT = ROOT / "data/public_scenario_inputs/scenario3_wuhan_joint_public.yaml"
LOCAL_TZ = "Asia/Shanghai"
ANNUAL_START = "2023-07-01T00:00:00+08:00"
ANNUAL_END = "2024-07-01T00:00:00+08:00"
SEASON_START = "2023-11-15T00:00:00+08:00"
SEASON_END = "2024-03-16T00:00:00+08:00"
ANNUAL_FILE = "wuhan_service_year_2023_2024_utc.csv"
CALENDAR_FILE = "wuhan_calendar_year_2023_utc.csv"
CALENDAR_START = "2023-01-01T00:00:00Z"
CALENDAR_END = "2024-01-01T00:00:00Z"
SEASON_FILE = "wuhan_heating_season_2023_2024_utc.csv"
PARAMETERS = {
    "T2M": "outdoor_temp", "RH2M": "relative_humidity_pct",
    "WS10M": "wind_speed_10m_m_s",
    "ALLSKY_SFC_SW_DWN": "solar_irradiance_kwh_m2_hour",
}
REQUIRED_BUILDING_FIELDS = {
    "name", "node", "heat_grade", "area_m2", "heat_loss_kw_per_c",
    "thermal_capacity_kwh_per_c", "initial_temperature_c",
    "min_temperature_c", "max_temperature_c", "internal_gain_column",
    "solar_gain_column", "min_temperature_column", "max_temperature_column",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def validate_raw_weather(frame: pd.DataFrame, year: int) -> None:
    """Reject duplicates, gaps, fill values and malformed local-solar years."""
    missing = {"time", *PARAMETERS.values(), "relative_humidity", "solar_heat_availability"} - set(frame)
    if missing:
        raise ValueError(f"weather missing columns: {sorted(missing)}")
    time = pd.DatetimeIndex(pd.to_datetime(frame["time"], errors="raise"))
    expected = pd.date_range(f"{year}-01-01", f"{year+1}-01-01", freq="h", inclusive="left")
    if time.tz is not None or not time.equals(expected):
        raise ValueError(f"weather {year} must be a complete ordered naive LST year without duplicates or gaps")
    columns = list(PARAMETERS.values()) + ["relative_humidity", "solar_heat_availability"]
    numeric = frame[columns].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numeric.to_numpy()).all() or (numeric == -999).any().any():
        raise ValueError("weather contains nonfinite/fill values")
    if not numeric["outdoor_temp"].between(-80, 65).all():
        raise ValueError("weather temperature out of range")
    if not numeric["relative_humidity_pct"].between(0, 100).all():
        raise ValueError("weather humidity out of range")
    if not np.allclose(numeric["relative_humidity"], numeric["relative_humidity_pct"] / 100, atol=1e-10):
        raise ValueError("weather humidity fraction disagrees with percent")
    if not numeric["wind_speed_10m_m_s"].between(0, 100).all():
        raise ValueError("weather wind out of range")
    if not numeric["solar_irradiance_kwh_m2_hour"].between(0, 1500).all():
        raise ValueError("legacy raw hourly radiation must be plausible Wh/m2")
    if not numeric["solar_heat_availability"].between(0, 1).all():
        raise ValueError("solar availability out of range")


def verify_clock(evidence: Path, archived_sample: pd.DataFrame) -> dict:
    payloads = {clock: json.loads((evidence / f"wuhan_clock_20230101_20230102_{clock}.json").read_text(encoding="utf-8"))
                for clock in ("utc", "lst")}
    for clock, payload in payloads.items():
        if payload["header"]["time_standard"] != clock.upper():
            raise ValueError("NASA clock metadata mismatch")
        if payload["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] != "Wh/m^2":
            raise ValueError("NASA radiation unit needs review")
    utc = payloads["utc"]["properties"]["parameter"]
    lst = payloads["lst"]["properties"]["parameter"]
    matches = []
    comparisons = {}
    for offset in range(-12, 15):
        errors = []
        for parameter in PARAMETERS:
            for key, value in lst[parameter].items():
                utc_key = (pd.to_datetime(key, format="%Y%m%d%H") - pd.Timedelta(hours=offset)).strftime("%Y%m%d%H")
                if utc_key in utc[parameter]:
                    errors.append(abs(float(value) - float(utc[parameter][utc_key])))
        if len(errors) >= 120 and max(errors) < 1e-9:
            matches.append(offset)
            comparisons[offset] = len(errors)
    if len(matches) != 1:
        raise ValueError(f"NASA clock offset not uniquely verified: {matches}")
    offset = matches[0]
    archived = archived_sample.set_index("time")
    archived_comparisons = 0
    for parameter, column in PARAMETERS.items():
        for key, value in lst[parameter].items():
            label = pd.to_datetime(key, format="%Y%m%d%H").strftime("%Y-%m-%d %H:%M:%S")
            if not np.isclose(float(archived.loc[label, column]), value, atol=1e-9, rtol=0):
                raise ValueError(f"archived NASA value differs from clock sample: {parameter} {key}")
            archived_comparisons += 1
    return {
        "verified_lst_utc_offset_hours": offset,
        "utc_lst_equal_comparisons": comparisons[offset],
        "archived_lst_equal_comparisons": archived_comparisons,
        "sample_start": "2023-01-01", "sample_end_inclusive": "2023-01-02",
        "api_version": payloads["utc"]["header"]["api"]["version"],
        "radiation_api_unit": "Wh/m^2",
        "scope": "Same Wuhan point only; clock/units check, not weather accuracy validation. Historical API version is not preserved in old CSVs.",
    }


def load_wuhan_weather(root: Path, offset: int) -> pd.DataFrame:
    pieces = []
    for year in range(2020, 2025):
        path = root / f"data/public_sources/scenario3_wuhan_weather_{year}.csv"
        frame = pd.read_csv(path)
        validate_raw_weather(frame, year)
        frame["source_time_lst"] = frame["time"]
        frame["source_weather_file"] = path.relative_to(root).as_posix()
        # NASA swath offset, verified above: not an arbitrary longitude/15 shift.
        frame["utc"] = (pd.to_datetime(frame["time"]) - pd.Timedelta(hours=offset)).dt.tz_localize("UTC")
        pieces.append(frame)
    return pd.concat(pieces, ignore_index=True).set_index("utc", verify_integrity=True)


def select_window(weather: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    first, stop = pd.Timestamp(start), pd.Timestamp(end)
    if first.tzinfo is None or stop.tzinfo is None or stop <= first:
        raise ValueError("window needs increasing timezone-aware half-open endpoints")
    expected = pd.date_range(first.tz_convert("UTC"), stop.tz_convert("UTC"), freq="h", inclusive="left")
    if len(expected) != (stop-first)/pd.Timedelta(hours=1):
        raise ValueError("window endpoints must align to full hours")
    missing = expected.difference(weather.index)
    if len(missing):
        raise ValueError(f"missing weather at service boundary/interior: {list(missing[:3])}")
    return weather.loc[expected].copy()


def load_buildings(path: Path) -> pd.DataFrame:
    buildings = pd.read_csv(path)
    if not REQUIRED_BUILDING_FIELDS.issubset(buildings):
        raise ValueError("building table missing contract fields")
    if buildings["name"].duplicated().any() or buildings.isna().any().any():
        raise ValueError("building table has duplicate names or missing values")
    for column in ("area_m2", "heat_loss_kw_per_c", "thermal_capacity_kwh_per_c"):
        if not np.isfinite(buildings[column]).all() or (buildings[column] <= 0).any():
            raise ValueError(f"building {column} must be positive and finite")
    for aggregate, intensity in (("heat_loss_kw_per_c", "heat_loss_w_per_m2_c"),
                                 ("thermal_capacity_kwh_per_c", "effective_capacity_wh_per_m2_c")):
        if not np.allclose(buildings[aggregate], buildings["area_m2"] * buildings[intensity] / 1000):
            raise ValueError(f"building area does not match {aggregate}")
    if not (buildings["min_temperature_c"] <= buildings["initial_temperature_c"]).all() or not (buildings["initial_temperature_c"] <= buildings["max_temperature_c"]).all():
        raise ValueError("building initial temperature outside bounds")
    return buildings


def occupancy(archetype: str, local: pd.DatetimeIndex) -> np.ndarray:
    hour = local.hour.to_numpy()
    weekday = local.dayofweek.to_numpy() < 5
    if archetype == "synthetic_apartments":
        # Home presence, including sleeping residents; deliberately differs from
        # the old demand-modulation template's near-zero night occupancy.
        return np.where((hour < 8) | (hour >= 18), 1.0, np.where(weekday, 0.35, 0.70))
    if archetype == "synthetic_office":
        return ((hour >= 8) & (hour < 18) & weekday).astype(float)
    if archetype == "synthetic_retail":
        return ((hour >= 9) & (hour < 21)).astype(float)
    raise ValueError(f"unknown synthetic archetype {archetype}")


def build_frame(weather: pd.DataFrame, buildings: pd.DataFrame, template: pd.DataFrame, group: str) -> pd.DataFrame:
    local = weather.index.tz_convert(LOCAL_TZ)
    template_hours = pd.to_datetime(template["time"], format="%H:%M").dt.hour
    if sorted(template_hours.tolist()) != list(range(24)):
        raise ValueError("legacy template must contain every civil hour exactly once")
    template = template.assign(_hour=template_hours).set_index("_hour").loc[local.hour].reset_index(drop=True)
    frame = pd.DataFrame({"time": weather.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "timestamp": weather.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "local_time": local.strftime("%Y-%m-%dT%H:%M:%S%z"),
                          "interval_end_utc": (weather.index+pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "time_step_hours": 1.0, "period_weight": 1.0, "boundary_group": group})
    for column in ("outdoor_temp", "relative_humidity", "wind_speed_10m_m_s", "solar_heat_availability", "source_time_lst", "source_weather_file"):
        frame[column] = weather[column].to_numpy()
    radiation_wh = weather["solar_irradiance_kwh_m2_hour"].to_numpy(dtype=float)
    frame["solar_irradiance_w_m2"] = radiation_wh  # Wh over the one-hour interval / 1 h
    frame["solar_irradiance_kwh_m2_hour"] = radiation_wh / 1000
    frame["electricity_price"] = template["electricity_price"]
    factor = json.loads((ROOT / "data/grid_emission_factors_2023_official.json").read_text(encoding="utf-8"))
    frame["grid_emission_factor"] = float(factor["profiles"]["scenario3_wuhan"]["factor"])
    frame["legacy_grid_emission_factor_reference"] = template["grid_emission_factor"]
    degree_ratio = np.clip(np.maximum(18-frame["outdoor_temp"], 0) / np.maximum(18-template["outdoor_temp"], 1), 0, 1.8)
    for district in ("a", "b"):
        for grade in ("high", "low"):
            column = f"demand_{district}_{grade}"
            frame[column] = template[column] * degree_ratio
        frame[f"legacy_occupancy_{district}_reference"] = template[f"occupancy_{district}"]
        frame[f"legacy_behavior_{district}_reference"] = template[f"behavior_{district}"]
        frame[f"behavior_{district}"] = 1.0
    cooling_fraction = np.clip((frame["outdoor_temp"]-24)/11, 0, 1.2)
    cooling_fraction[(frame["outdoor_temp"] <= 28) & (frame["relative_humidity"] <= .70)] = 0
    for district, peak, setback in (("a", 440, .18), ("b", 360, .20)):
        frame[f"cooling_demand_{district}"] = peak * cooling_fraction * (setback + (1-setback)*template[f"occupancy_{district}"])
    gain_parameters = {"synthetic_apartments": (.8, 3), "synthetic_office": (1, 8), "synthetic_retail": (1, 10)}
    for row in buildings.to_dict("records"):
        presence = occupancy(row["archetype"], local)
        frame[row["occupancy_column"]] = presence
        idle, full = gain_parameters[row["archetype"]]
        frame[row["internal_gain_column"]] = row["area_m2"]*(idle+(full-idle)*presence)/1000
        # Effective irradiated aperture/floor area=0.015 includes all geometry,
        # transmission and shading effects as a declared synthetic assumption.
        frame[row["solar_gain_column"]] = row["area_m2"] * .015 * radiation_wh / 1000
        occupied = presence >= .5
        frame[row["min_temperature_column"]] = np.where(occupied, 18., 16.)
        frame[row["max_temperature_column"]] = np.where(occupied, 26., 28.)
    for district in ("a", "b"):
        group_buildings = buildings[buildings["node"] == f"district_{district}"]
        frame[f"occupancy_{district}"] = sum(frame[b["occupancy_column"]]*b["area_m2"] for b in group_buildings.to_dict("records")) / group_buildings["area_m2"].sum()
    start, stop = pd.Timestamp(SEASON_START), pd.Timestamp(SEASON_END)
    frame["project_heating_season_flag"] = ((weather.index >= start) & (weather.index < stop)).astype(int)
    return frame


def _path_reference(path: Path, base: Path) -> str:
    """Prefer a relocatable same-drive reference; support Windows cross-drive output."""
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def candidate_config(buildings: pd.DataFrame, parent_relative: str) -> dict:
    parent = load_config(PARENT)
    rows = []
    for building in buildings.to_dict("records"):
        row = {key: building[key] for key in sorted(REQUIRED_BUILDING_FIELDS)}
        row.update(boundary_mode="fixed", final_temperature_c=20.,
                   parameter_identity=building["parameter_identity"])
        rows.append(row)
    return {
        "extends": parent_relative,
        "planning": {"region_profile": "wuhan_synthetic_rc_public_weather_candidate",
                     "time_step_hours": 1, "representative_day_weight": 1,
                     "annual_scope": {"start_inclusive": ANNUAL_START, "end_exclusive": ANNUAL_END,
                                      "timezone": LOCAL_TZ, "expected_hours": 8784},
                     "firm_building_flexibility": {"enabled": False, "availability_factor": 0.0}},
        "nodes": [{"name": node, "thermal_inertia": {"high": None, "low": None},
                   "intermittent_heating": {"setback_fraction": 1.0},
                   "user_behavior": {"multiplier_column": None, "scenario_multiplier_weight_column": None}}
                  for node in ("district_a", "district_b")],
        "demand_response": {"max_shift_fraction": 0.0},
        "storages": [{"name": s["name"], "boundary_mode": "fixed", "initial_soc_fraction": 0.0,
                      "final_soc_min_fraction": 0.0, "firm_capacity_credit": 0.0}
                     for s in parent["storages"]],
        "building_thermal": {"enabled": True, "outdoor_temperature_column": "outdoor_temp",
                             "boundary_mode": "fixed", "buildings": rows},
        "competition_data": {
            "status": "candidate_requires_integrated_rc_and_full_horizon_audit",
            "input_timezone": "UTC", "schedule_timezone": LOCAL_TZ,
            "heat_demand_columns_role": "legacy_reference_and_backup_capacity_scale_not_fixed_rc_service",
            "cooling_demand_columns_role": "legacy_reference_and_cooling_module_activation_not_fixed_rc_service",
            "heating_season_flag_role": "reporting_scope_only_not_year_round_rc_service_switch",
            "dhw": "excluded; no domestic hot water load claimed",
            "ground_balance_scope": "full_8784_hour_service_year; seasonal_slice_must_not_reimpose_annual_ratio",
            "storage_initial_energy_source": "empty_at_window_start; all useful stored energy must be charged inside the window",
            "storage_terminal_rule": "SOC >= 0; no terminal energy credit; positive leftover allowed and reported",
            "temperature_initial_source": "fixed 20 C synthetic conditioned start; preconditioning energy outside comparison boundary",
            "temperature_terminal_rule": "fixed 20 C requested; C2/C0 must enforce final_temperature_c",
        },
    }


def quality_report(frame: pd.DataFrame, buildings: pd.DataFrame, start: str, end: str) -> dict:
    time = pd.DatetimeIndex(pd.to_datetime(frame["time"], utc=True))
    expected = pd.date_range(pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC"), freq="h", inclusive="left")
    if not time.equals(expected):
        raise ValueError("output time grid or service edges are incorrect")
    if not (frame["period_weight"] == 1).all() or frame["boundary_group"].nunique() != 1:
        raise ValueError("continuous inputs require unit weights and one boundary group")
    numeric = frame.select_dtypes(include=np.number)
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("output contains nonfinite values")
    district_scale = {}
    for node, group in buildings.groupby("node"):
        heat_loss = float(group["heat_loss_kw_per_c"].sum())
        district_scale[node] = {"area_m2": float(group["area_m2"].sum()), "heat_loss_kw_per_c": heat_loss,
                                "thermal_capacity_kwh_per_c": float(group["thermal_capacity_kwh_per_c"].sum()),
                                "design_heat_kw_at_18_indoor_minus7_outdoor_no_gains": heat_loss*25,
                                "historical_max_heat_kw_at_18_indoor_no_gains": heat_loss*max(18-float(frame["outdoor_temp"].min()), 0)}
    return {"passed": True, "rows": len(frame), "hours": len(frame),
            "start_inclusive": start, "end_exclusive": end,
            "start_inclusive_local": pd.Timestamp(start).tz_convert(LOCAL_TZ).isoformat(),
            "end_exclusive_local": pd.Timestamp(end).tz_convert(LOCAL_TZ).isoformat(),
            "first_utc": frame["time"].iloc[0], "last_utc": frame["time"].iloc[-1],
            "end_exclusive_utc": frame["interval_end_utc"].iloc[-1],
            "leap_day_hours_local": int(pd.to_datetime(frame["local_time"], utc=True).dt.tz_convert(LOCAL_TZ).dt.strftime("%m-%d").eq("02-29").sum()),
            "missing_hours": 0, "duplicate_hours": 0, "nonfinite_numeric_values": 0,
            "minimum_outdoor_temperature_c": float(frame["outdoor_temp"].min()),
            "maximum_outdoor_temperature_c": float(frame["outdoor_temp"].max()),
            "source_weather_files": sorted(frame["source_weather_file"].unique().tolist()),
            "legacy_reference_heat_kwh": float(frame[["demand_a_low", "demand_b_low"]].sum().sum()),
            "legacy_reference_cooling_kwh": float(frame[["cooling_demand_a", "cooling_demand_b"]].sum().sum()),
            "district_scale": district_scale,
            "meaning": "Data QA and static scale checks only; no dispatch, feasibility, economics or annual RC validation asserted."}


def prepare(output: Path = OUTPUT) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Invalidate the previous completion marker before any writes. It is written
    # last only after all products and input checks finish successfully.
    marker = output / "manifest.json"
    marker.unlink(missing_ok=True)
    evidence = OUTPUT / "evidence"
    building_path = OUTPUT / "building_archetypes.csv"
    buildings = load_buildings(building_path)
    raw_2023 = pd.read_csv(ROOT / "data/public_sources/scenario3_wuhan_weather_2023.csv")
    clock = verify_clock(evidence, raw_2023)
    inventory = []
    for profile in ("scenario1_urumqi", "scenario2_guyuan", "scenario3_wuhan"):
        for year in range(2020, 2025):
            path = ROOT / f"data/public_sources/{profile}_weather_{year}.csv"
            raw = pd.read_csv(path)
            validate_raw_weather(raw, year)
            inventory.append({"file": path.relative_to(ROOT).as_posix(), "sha256": sha256(path),
                              "profile": profile, "year": year, "rows": len(raw),
                              "clock_identity": "NASA LST as documented by original weather_manifest.yaml",
                              "utc_conversion_in_this_package": profile == "scenario3_wuhan"})
    weather = load_wuhan_weather(ROOT, clock["verified_lst_utc_offset_hours"])
    template_path = ROOT / "data/scenario3_timeseries.csv"
    annual = build_frame(select_window(weather, ANNUAL_START, ANNUAL_END), buildings,
                         pd.read_csv(template_path), "service_year_2023_2024")
    season = annual.loc[annual["project_heating_season_flag"] == 1].copy().reset_index(drop=True)
    season["boundary_group"] = "heating_season_2023_2024"
    calendar_year = build_frame(select_window(weather, CALENDAR_START, CALENDAR_END), buildings,
                               pd.read_csv(template_path), "utc_calendar_year_2023")
    config = candidate_config(buildings, _path_reference(PARENT, output))
    config_path = output / "wuhan_rc_candidate.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    calendar_config_path = output / "wuhan_rc_calendar_2023_candidate.yaml"
    calendar_config = {"extends": config_path.name,
                       "planning": {"annual_scope": {"start_inclusive": CALENDAR_START,
                                                       "end_exclusive": CALENDAR_END,
                                                       "timezone": "UTC", "expected_hours": 8760}},
                       "competition_data": {"ground_balance_scope": "full_8760_hour_UTC_calendar_year"}}
    calendar_config_path.write_text(yaml.safe_dump(calendar_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    resolved = load_config(config_path)
    validate_inputs(resolved, annual)
    validate_inputs(resolved, season)
    validate_inputs(load_config(calendar_config_path), calendar_year)
    annual.to_csv(output / ANNUAL_FILE, index=False, float_format="%.10g", encoding="utf-8")
    season.to_csv(output / SEASON_FILE, index=False, float_format="%.10g", encoding="utf-8")
    calendar_year.to_csv(output / CALENDAR_FILE, index=False, float_format="%.10g", encoding="utf-8")
    reports = {"service_year": quality_report(annual, buildings, ANNUAL_START, ANNUAL_END),
               "utc_calendar_year": quality_report(calendar_year, buildings, CALENDAR_START, CALENDAR_END),
               "heating_season": quality_report(season, buildings, SEASON_START, SEASON_END)}
    write_json(output / "quality_report.json", reports)
    write_json(output / "clock_verification.json", clock)
    write_json(output / "source_inventory.json", inventory)
    scopes = {"profile": "scenario3_wuhan_competition_rc", "timezone": LOCAL_TZ,
              "date_identity": "project_assumed_service_period_not_city_statutory_schedule",
              "start_inclusive": SEASON_START, "end_exclusive": SEASON_END,
              "expected_hours": len(season), "annual_start_inclusive": ANNUAL_START,
              "annual_end_exclusive": ANNUAL_END, "annual_expected_hours": len(annual),
              "annual_identity": "continuous_July_to_June_service_year_not_calendar_year",
              "utc_calendar_year_start_inclusive": CALENDAR_START,
              "utc_calendar_year_end_exclusive": CALENDAR_END,
              "utc_calendar_year_expected_hours": len(calendar_year),
              "calendar_year_role": "legacy_UTC_calendar_audit_compatible; does_not_contain_whole_2023_2024_heating_season",
              "selection_rule": "latest complete July-to-June service year in existing 2020-2024 archive; not selected by optimized performance"}
    write_json(output / "service_scope.json", scopes)
    # Resolve the inheritance chain and record every upstream config identity.
    parent_paths = []
    next_path = PARENT
    while True:
        parent_paths.append(next_path)
        spec = yaml.safe_load(next_path.read_text(encoding="utf-8"))
        if not spec.get("extends"):
            break
        next_path = (next_path.parent / spec["extends"]).resolve()
    source_paths = [*parent_paths, template_path, building_path, ROOT / "data/public_sources/weather_manifest.yaml",
                    ROOT / "data/grid_emission_factors_2023_official.json", Path(__file__),
                    ROOT / "tests/test_competition_data.py",
                    *sorted(evidence.glob("*.json"))]
    manifest = {"schema_version": 1, "complete": True, "status": "data_prepared_not_solver_certified",
                "date": "2026-10-01", "baseline": "11.11.1", "clock": clock, "scope": scopes,
                "config": config_path.name, "annual_timeseries": ANNUAL_FILE,
                "utc_calendar_year_timeseries": CALENDAR_FILE,
                "utc_calendar_year_config": calendar_config_path.name,
                "heating_season_timeseries": SEASON_FILE,
                "building_archetypes": _path_reference(building_path, output),
                "inputs": [{"file": _path_reference(p, ROOT), "sha256": sha256(p)} for p in source_paths],
                "raw_weather_inputs": inventory,
                "artifacts": [{"file": p.name, "sha256": sha256(p)} for p in sorted(output.iterdir()) if p.is_file() and p.name != "manifest.json"],
                "validation": {"legacy_load_inputs_passed": True, "data_quality_passed": True,
                               "full_season_solved": False, "annual_solved": False,
                               "rc_integration_certified": False},
                "limitations": ["Synthetic building parameters and schedules; no measured indoor temperature calibration.",
                                "Annual sensible heating/cooling only; humidity, latent cooling and DHW excluded.",
                                "Season requires a ground-balance scope decision or boundary ledger from annual run.",
                                "RC columns require core integration; successful legacy loading does not prove RC enforcement.",
                                "Price is legacy synthetic TOU shape, not verified Wuhan historical tariff; 2023 Hubei average carbon factor is a scenario constant for both weather years."]}
    write_json(marker, manifest)
    return {"output": str(output), "utc_calendar_year_rows": len(calendar_year), "service_year_rows": len(annual), "season_rows": len(season), "clock_offset_hours": clock["verified_lst_utc_offset_hours"], "data_quality_passed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
