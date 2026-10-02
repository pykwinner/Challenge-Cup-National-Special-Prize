"""Calendar/source identity and weather-only prototype comparison guards."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("three_city_common", ROOT / "scripts/prepare_three_city_common_reference.py")
case = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(case)


@pytest.fixture(scope="module")
def frames():
    evidence = ROOT / case.EVIDENCE
    source = evidence if all((evidence / name).is_file() for city in case.CITIES for name in case.source_names(city)) else case.DEFAULT_SOURCES
    case.verify_source_hashes(source)
    return {city: case.build_frame(city, source) for city in case.CITIES}


def test_exact_common_window_and_daily_coverage(frames):
    for city, frame in frames.items():
        assert len(frame) == 2880
        assert frame.time.iloc[0] == "2024-11-16T00:00:00+08:00"
        assert frame.time.iloc[-1] == "2025-03-15T23:00:00+08:00"
        assert frame.interval_end.iloc[-1] == "2025-03-16T00:00:00+08:00"
        assert frame.source_time_utc.iloc[0] == "2024111516"
        assert frame.groupby(frame.time.str[:10]).size().eq(24).all()
        case.validate_frame(frame, city, frame)
    assert frames["urumqi"].time.equals(frames["guyuan"].time)
    assert frames["guyuan"].time.equals(frames["wuhan"].time)


def test_wuhan_weather_matches_prior_admitted_reference(frames):
    existing = pd.read_csv(ROOT / "data/derived/evidence_case_20261002/wuhan_2024_2025_hourly.csv")
    columns = [column for column, _ in case.PARAMETERS.values()]
    pd.testing.assert_frame_equal(frames["wuhan"][columns], existing[columns], check_dtype=False, rtol=0, atol=1e-10)
    # No financial/carbon transfer to another region.
    assert all("electricity_price" not in frame and "grid_emission_factor" not in frame for frame in frames.values())


def test_same_reference_and_declared_load_derivation(frames):
    physics = case.common_physics()
    building = physics["building_thermal"]["buildings"][0]
    assert case.H == pytest.approx(.1043, abs=1e-12)
    assert case.C == pytest.approx(.535576, abs=1e-12)
    assert building["initial_temperature_c"] == building["final_temperature_c"] == 20
    assert (building["min_temperature_c"], building["max_temperature_c"]) == (20, 27)
    assert physics["geometry"]["floor_area_m2"] == 48
    for frame in frames.values():
        assert frame.internal_gain_kw.eq(.2).all()
        assert frame.solar_gain_kw.eq(0).all()
        assert frame.solar_irradiation_wh_m2.max() > 0
        assert np.allclose(frame.steady_heat_demand_kw, np.maximum(case.H * (20 - frame.outdoor_temp) - .2, 0), rtol=0, atol=1e-12)
        assert np.allclose(frame.steady_cooling_demand_kw, np.maximum(case.H * (frame.outdoor_temp - 27) + .2, 0), rtol=0, atol=1e-12)
    # Same physical object under distinct real weather, not cloned city drivers.
    assert not frames["urumqi"].outdoor_temp.equals(frames["guyuan"].outdoor_temp)
    assert not frames["guyuan"].outdoor_temp.equals(frames["wuhan"].outdoor_temp)


@pytest.mark.parametrize("damage", ["gap", "duplicate", "naive", "utc_relabel", "dt", "probability", "weather", "internal_gain", "nan", "price", "carbon", "solar_as_gain", "city"])
def test_reject_incomplete_clock_or_changed_comparison(frames, damage):
    expected = frames["guyuan"]
    frame = expected.copy()
    if damage == "gap":
        frame = frame.drop(index=25)
    elif damage == "duplicate":
        frame.loc[25, "time"] = frame.loc[24, "time"]
    elif damage == "naive":
        frame["time"] = pd.to_datetime(frame.time, utc=True).dt.tz_localize(None).astype(str)
    elif damage == "utc_relabel":
        frame.loc[0, "time"] = "2024-11-16T00:00:00+00:00"
    elif damage == "dt":
        frame.loc[0, "dt_hours"] = 3
    elif damage == "probability":
        frame.loc[0, "scenario_probability"] = 1/3
    elif damage == "weather":
        frame.loc[0, "outdoor_temp"] += 1
    elif damage == "internal_gain":
        frame.loc[0, "internal_gain_kw"] = 2
    elif damage == "nan":
        frame.loc[0, "outdoor_temp"] = np.nan
    elif damage == "price":
        frame["electricity_price"] = .58
    elif damage == "carbon":
        frame["grid_emission_factor"] = .4044
    elif damage == "solar_as_gain":
        frame["solar_gain_kw"] = frame.solar_irradiation_wh_m2
    else:
        frame["city_id"] = "wuhan"
    with pytest.raises(ValueError):
        case.validate_frame(frame, "guyuan", expected)


@pytest.mark.parametrize("damage", ["LST", "unit", "point", "missing_hour", "fill", "url_clock", "url_place"])
def test_source_identity_missingness_and_time_standard_guard(damage):
    source = ROOT / case.EVIDENCE
    if not (source / case.source_names("wuhan")[0]).exists():
        source = case.DEFAULT_SOURCES
    raw = deepcopy(case.read_json(source / case.source_names("wuhan")[0]))
    receipt = deepcopy(case.read_json(source / case.source_names("wuhan")[1]))
    if damage == "LST":
        raw["header"]["time_standard"] = receipt["header"]["time_standard"] = "LST"
    elif damage == "unit":
        raw["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] = receipt["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] = "kWh/m^2"
    elif damage == "point":
        raw["geometry"]["coordinates"][0] += .1
    elif damage == "missing_hour":
        raw["properties"]["parameter"]["T2M"].pop("2024111600")
    elif damage == "fill":
        raw["properties"]["parameter"]["T2M"]["2024111600"] = -999
    elif damage == "url_clock":
        receipt["url"] = receipt["url"].replace("time-standard=UTC", "time-standard=LST")
    else:
        receipt["url"] = receipt["url"].replace("latitude=30.5928", "latitude=40.5928")
    with pytest.raises(ValueError):
        case.validate_original(raw, receipt, "wuhan")


def test_duplicate_json_keys_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        json.loads('{"2024111600":1,"2024111600":2}', object_pairs_hook=case.reject_duplicate_keys)


def test_provenance_complete_and_no_false_holdout_claim(frames):
    fields = case.provenance(frames)
    assert len(fields) == sum(len(frame.columns) for frame in frames.values()) == 63
    assert not fields[["artifact", "field"]].duplicated().any()
    rows = case.confirmation_rows()
    assert not rows.held_out_confirmation_certified.any()
    assert rows.loc[rows.city_id.eq("wuhan"), "window_role"].str.contains("development").all()
    assert rows.loc[rows.city_id.ne("wuhan"), "new_method_development_use_status"].str.contains("pending").all()
    protocol = case.protocol(case.common_physics())
    assert not protocol["scada_required_for_progress"]
    assert not protocol["time"]["local_heating_season_claimed"]
    assert not protocol["solver_run_performed"]
    assert len(protocol["score_mapping"]) == 7


def test_exact_source_hash_lock_rejects_replaced_original(tmp_path):
    (tmp_path / case.source_names("urumqi")[0]).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Original source"):
        case.verify_source_hashes(tmp_path)
