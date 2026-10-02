"""Regression tests for the real C1 package, clocks, units and service boundaries."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from heatplan.chronology import chronology_quality_audit
from heatplan.io import load_config, load_inputs


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_competition_case", ROOT / "scripts/prepare_competition_case.py")
case = importlib.util.module_from_spec(spec)
spec.loader.exec_module(case)


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    output = tmp_path_factory.mktemp("competition_case")
    result = case.prepare(output)
    return output, result


@pytest.fixture(scope="module")
def annual(package):
    return pd.read_csv(package[0] / case.ANNUAL_FILE)


def test_exact_service_edges_leap_day_and_season_is_annual_slice(package, annual):
    output, result = package
    season = pd.read_csv(output / case.SEASON_FILE)
    assert result["service_year_rows"] == 8784
    assert result["season_rows"] == 2928
    assert annual["time"].iloc[0] == "2023-06-30T16:00:00Z"
    assert annual["time"].iloc[-1] == "2024-06-30T15:00:00Z"
    assert season["time"].iloc[0] == "2023-11-14T16:00:00Z"
    assert season["time"].iloc[-1] == "2024-03-15T15:00:00Z"
    assert season["interval_end_utc"].iloc[-1] == "2024-03-15T16:00:00Z"
    assert season["local_time"].str.startswith("2024-02-29").sum() == 24
    subset = annual.loc[annual["project_heating_season_flag"] == 1].reset_index(drop=True)
    pd.testing.assert_frame_equal(season.drop(columns="boundary_group"), subset.drop(columns="boundary_group"), check_dtype=False)
    assert annual["boundary_group"].nunique() == season["boundary_group"].nunique() == 1
    assert annual["period_weight"].eq(1).all()
    assert (pd.to_datetime(annual["time"], utc=True).diff().dropna() == pd.Timedelta(hours=1)).all()


def test_utc_calendar_year_has_actual_next_year_weather_not_wrapped_rows(package):
    calendar = pd.read_csv(package[0] / case.CALENDAR_FILE)
    raw = pd.read_csv(ROOT / "data/public_sources/scenario3_wuhan_weather_2024.csv")
    assert len(calendar) == 8760
    assert calendar["time"].iloc[0] == "2023-01-01T00:00:00Z"
    assert calendar["time"].iloc[-1] == "2023-12-31T23:00:00Z"
    assert calendar["source_time_lst"].iloc[-1] == "2024-01-01 07:00:00"
    np.testing.assert_array_equal(calendar["outdoor_temp"].tail(8), raw["outdoor_temp"].head(8))
    assert calendar["source_weather_file"].tail(8).str.endswith("weather_2024.csv").all()
    audit = chronology_quality_audit(calendar)
    assert audit["chronology_valid"] and audit["is_calendar_year_aligned"]


def test_chinese_civil_tariff_and_weekday_occupancy(package, annual):
    calendar = pd.read_csv(package[0] / case.CALENDAR_FILE)
    # UTC midnight is 08:00 in Shanghai: a position-modulo-24 schedule fails here.
    assert calendar["electricity_price"].iloc[0] == pytest.approx(.96)
    rows = annual.set_index("local_time")
    assert rows.loc["2023-07-03T08:00:00+0800", "occupancy_office_a"] == 1
    assert rows.loc["2023-07-02T08:00:00+0800", "occupancy_office_a"] == 0
    assert rows.loc["2023-07-03T18:00:00+0800", "occupancy_office_a"] == 0
    assert rows.loc["2023-07-03T02:00:00+0800", "occupancy_apartments_a"] == 1
    assert rows.loc["2023-07-03T09:00:00+0800", "occupancy_retail_b"] == 1
    assert rows.loc["2023-07-03T21:00:00+0800", "occupancy_retail_b"] == 0
    assert rows.loc["2023-07-03T08:00:00+0800", "max_temperature_office_a_c"] == 26
    assert rows.loc["2023-07-02T08:00:00+0800", "min_temperature_office_a_c"] == 16


def test_verified_clock_and_radiation_units(package, annual):
    clock = json.loads((package[0] / "clock_verification.json").read_text())
    assert clock["verified_lst_utc_offset_hours"] == 8
    assert clock["utc_lst_equal_comparisons"] == 160
    assert clock["archived_lst_equal_comparisons"] == 192
    assert clock["radiation_api_unit"] == "Wh/m^2"
    raw = pd.read_csv(ROOT / "data/public_sources/scenario3_wuhan_weather_2023.csv").set_index("time")
    day = annual.loc[(annual["source_time_lst"] == "2023-07-01 12:00:00")].iloc[0]
    radiation_wh = raw.loc["2023-07-01 12:00:00", "solar_irradiance_kwh_m2_hour"]
    assert day["solar_irradiance_w_m2"] == pytest.approx(radiation_wh)
    assert day["solar_irradiance_kwh_m2_hour"] == pytest.approx(radiation_wh / 1000)
    assert day["solar_gain_office_a_kw"] == pytest.approx(7000 * .015 * radiation_wh / 1000)
    assert annual["grid_emission_factor"].eq(.4044).all()


def test_inheritance_safety_initial_energy_and_policy_are_explicit(package, annual):
    output = package[0]
    config, loaded = load_inputs(output / "wuhan_rc_candidate.yaml", output / case.ANNUAL_FILE)
    parent = load_config(case.PARENT)
    assert len(loaded) == len(annual)
    for key in ("sources", "pipes", "grid_connection", "multi_energy_services", "policy"):
        assert config[key] == parent[key]
    assert config["policy"]["maximum_levelized_heat_cost_yuan_per_kwh"] == 2.0
    assert config["policy"]["joint_service_heat_affordability_reference_yuan_per_kwh"] == .4
    assert config["planning"]["annual_scope"]["expected_hours"] == 8784
    assert config["planning"]["firm_building_flexibility"]["enabled"] is False
    assert config["demand_response"]["max_shift_fraction"] == 0
    for storage in config["storages"]:
        assert storage["boundary_mode"] == "fixed"
        assert storage["initial_soc_fraction"] == 0
        assert storage["final_soc_min_fraction"] == 0
        assert storage["firm_capacity_credit"] == 0
    for building in config["building_thermal"]["buildings"]:
        assert building["initial_temperature_c"] == building["final_temperature_c"] == 20
        assert building["boundary_mode"] == "fixed"
        assert building["heat_grade"] == "low"
    assert sum(b["area_m2"] for b in config["building_thermal"]["buildings"]) == 44000
    calendar = load_config(output / "wuhan_rc_calendar_2023_candidate.yaml")
    assert calendar["planning"]["annual_scope"]["expected_hours"] == 8760


def test_old_cooling_reference_is_preserved_but_no_hard_zero_service_switch(package):
    calendar = pd.read_csv(package[0] / case.CALENDAR_FILE)
    legacy = pd.read_csv(ROOT / "data/public_scenario_multiyear/scenario3_wuhan/scenario3_wuhan_full_year_2023.csv")
    # UTC year starts at old LST row 8; the source-year tail is tested separately.
    for column in ("demand_a_low", "demand_b_low", "cooling_demand_a", "cooling_demand_b"):
        np.testing.assert_allclose(calendar[column].iloc[:-8], legacy[column].iloc[8:], rtol=1e-9, atol=1e-8)
    config = load_config(package[0] / "wuhan_rc_candidate.yaml")
    assert "heating_service_column" not in config["building_thermal"]
    assert all("heating_service_column" not in b for b in config["building_thermal"]["buildings"])


def test_input_hashes_completion_marker_and_no_false_solver_claim(package):
    output = package[0]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True
    assert manifest["validation"]["annual_solved"] is False
    assert manifest["validation"]["full_season_solved"] is False
    assert len(manifest["raw_weather_inputs"]) == 15
    for source in manifest["inputs"] + manifest["raw_weather_inputs"]:
        assert case.sha256(ROOT / source["file"]) == source["sha256"]
    for artifact in manifest["artifacts"]:
        assert case.sha256(output / artifact["file"]) == artifact["sha256"]


@pytest.mark.parametrize("corruption", ["gap", "duplicate", "fill", "nan", "humidity", "radiation"])
def test_raw_weather_rejects_bad_data(corruption):
    raw = pd.read_csv(ROOT / "data/public_sources/scenario3_wuhan_weather_2023.csv")
    if corruption == "gap":
        raw = raw.drop(index=10)
    elif corruption == "duplicate":
        raw.loc[1, "time"] = raw.loc[0, "time"]
    elif corruption == "fill":
        raw.loc[10, "outdoor_temp"] = -999
    elif corruption == "nan":
        raw.loc[10, "outdoor_temp"] = np.nan
    elif corruption == "humidity":
        raw.loc[10, "relative_humidity"] = 74
    else:
        raw.loc[10, "solar_irradiance_kwh_m2_hour"] = -1
    with pytest.raises(ValueError):
        case.validate_raw_weather(raw, 2023)


def test_missing_service_edge_is_not_padded_or_repeated():
    frame = pd.DataFrame({"outdoor_temp": [1, 2]}, index=pd.date_range("2023-01-01T00:00:00Z", periods=2, freq="h"))
    with pytest.raises(ValueError, match="missing weather"):
        case.select_window(frame, "2023-01-01T00:00:00Z", "2023-01-01T03:00:00Z")
    with pytest.raises(ValueError, match="timezone-aware"):
        case.select_window(frame, "2023-01-01", "2023-01-02")


def test_wrong_building_area_cannot_silently_change_rc_capacity(tmp_path):
    table = pd.read_csv(case.OUTPUT / "building_archetypes.csv")
    table.loc[0, "area_m2"] *= 2
    path = tmp_path / "wrong_scale.csv"
    table.to_csv(path, index=False)
    with pytest.raises(ValueError, match="area does not match"):
        case.load_buildings(path)


def test_building_module_accepts_real_package_without_any_solve(package, annual):
    # This checks the independently implemented C2 API, not a mock validator.
    from heatplan.building_thermal import validate_building_thermal_inputs
    config = load_config(package[0] / "wuhan_rc_candidate.yaml")
    validated = validate_building_thermal_inputs(config, annual)
    assert len(validated.buildings) == 4
    assert len(validated.frame) == 8784
    for building in validated.buildings:
        assert np.isfinite(building.heat_upper).all()
        assert np.isfinite(building.cooling_upper).all()
        assert (building.heating_service == 1).all()


def test_failed_rebuild_removes_old_completion_marker(tmp_path, monkeypatch):
    marker = tmp_path / "manifest.json"
    marker.write_text('{"complete":true}', encoding="utf-8")
    def broken(*args, **kwargs):
        raise ValueError("invalid source")
    monkeypatch.setattr(case, "verify_clock", broken)
    with pytest.raises(ValueError, match="invalid source"):
        case.prepare(tmp_path)
    assert not marker.exists()
