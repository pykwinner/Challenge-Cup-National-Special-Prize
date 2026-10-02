"""External-source and leakage tests; analytic fixtures are program checks only."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("external_rc", ROOT / "scripts/validate_external_rc_benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


@pytest.fixture(scope="module")
def original_frames():
    benchmark.verified_members()
    return pd.read_excel(benchmark.EVIDENCE / benchmark.HOUSE_FILE), pd.read_excel(benchmark.EVIDENCE / benchmark.WEATHER_FILE)


@pytest.fixture(scope="module")
def observations(original_frames):
    return benchmark.prepare_observations(*original_frames)[0]


def test_original_measurement_archive_identity_and_members():
    records = benchmark.verified_members()
    assert len(records) == 2
    assert all(record["bytes"] > 100000 for record in records)
    assert {record["file"] for record in records} == {benchmark.HOUSE_FILE, benchmark.WEATHER_FILE}
    provenance = json.loads((benchmark.DATA / "source_provenance.json").read_text(encoding="utf-8"))
    assert provenance["selected_dataset"]["licence"] == "CC BY-SA 4.0"
    assert "processed" in provenance["selected_dataset"]["rawness"] or "failures" in provenance["selected_dataset"]["rawness"]


def test_measured_time_alignment_units_and_all_rows(original_frames, observations):
    house, weather = original_frames
    assert len(observations) == 11988
    assert observations["time_utc"].iloc[0] == "2018-12-07T17:10:00Z"
    assert observations["time_utc"].iloc[-1] == "2019-02-28T23:00:00Z"
    assert (pd.to_datetime(observations["time_utc"], utc=True).diff().dropna() == pd.Timedelta(minutes=10)).all()
    np.testing.assert_allclose(observations["outdoor_temperature_mean_c"], weather["AmbientAirTemperature"])
    heating = house[[f"n2_aroom_{room}_heat_elP" for room in benchmark.ROOM_AREAS]].sum(axis=1) / 1000
    np.testing.assert_allclose(observations["measured_room_heating_kw"], heating)
    assert sum(benchmark.ROOM_AREAS.values()) == pytest.approx(165.75)
    assert observations["indoor_temperature_mean_c"].iloc[0] == pytest.approx(
        sum(house[f"n2_aroom_{room}_110_AT"].iloc[0] * area for room, area in benchmark.ROOM_AREAS.items()) / 165.75)
    assert observations["solar_reference_kw_m2"].max() == pytest.approx(1.1507)


@pytest.mark.parametrize("corruption", ["gap", "duplicate", "unsorted", "mismatch", "nan", "negative_power"])
def test_missing_or_invalid_measured_inputs_fail_closed(original_frames, corruption):
    house, weather = [f.head(100).copy() for f in original_frames]
    if corruption == "gap":
        house, weather = house.drop(index=5), weather.drop(index=5)
    elif corruption == "duplicate":
        house.loc[5, "DATE"] = house.loc[4, "DATE"]
    elif corruption == "unsorted":
        house = house.iloc[::-1]
    elif corruption == "mismatch":
        weather["DATE"] += pd.Timedelta(minutes=1)
    elif corruption == "nan":
        house.loc[5, "n2_aroom_bath_110_AT"] = np.nan
    else:
        house.loc[5, "n2_aroom_bath_heat_elP"] = -1
    with pytest.raises(ValueError):
        benchmark.prepare_observations(house, weather)


def test_temporal_splits_cover_all_targets_without_random_leakage(observations):
    ranges = benchmark.split_ranges(len(observations))
    assert ranges == {"train": (1, 7192), "validation": (7192, 9589), "test": (9589, 11988)}
    assert len(set(range(*ranges["train"])) & set(range(*ranges["validation"]))) == 0
    assert ranges["validation"][1] == ranges["test"][0]
    with pytest.raises(ValueError, match="insufficient"):
        benchmark.split_ranges(50)


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
@pytest.mark.parametrize("missing_interval", [False, True])
def test_sampling_validation_is_independent_of_datetime_storage_unit(original_frames, unit, missing_interval):
    frames = [frame.head(100).copy() for frame in original_frames]
    for frame in frames:
        frame["DATE"] = frame["DATE"].astype(f"datetime64[{unit}]")
        assert np.datetime_data(frame["DATE"].dtype)[0] == unit
    if missing_interval:
        frames = [frame.drop(index=5) for frame in frames]
        with pytest.raises(ValueError, match="gaps or wrong sampling"):
            benchmark.prepare_observations(*frames)
    else:
        actual, _ = benchmark.prepare_observations(*frames)
        expected, _ = benchmark.prepare_observations(*[frame.head(100) for frame in original_frames])
        pd.testing.assert_frame_equal(actual, expected)


def test_interval_mean_equation_against_independent_continuous_integral():
    # This is a mathematical unit test, never used as benchmark observations.
    h, c, area, dt = .1, 2., 0., benchmark.DT
    initial = 18.
    eq0, eq1 = 20., 30.
    avg0 = quad(lambda t: eq0 + (initial - eq0) * np.exp(-h / c * t), 0, dt)[0] / dt
    end0 = eq0 + (initial - eq0) * np.exp(-h / c * dt)
    avg1 = quad(lambda t: eq1 + (end0 - eq1) * np.exp(-h / c * t), 0, dt)[0] / dt
    frame = pd.DataFrame({"indoor_temperature_mean_c": [avg0, -999.],
                          "outdoor_temperature_mean_c": [0., 0.],
                          "total_room_heat_input_kw": [h * eq0, h * eq1], "solar_reference_kw_m2": [0., 0.]})
    params = np.array([h, c, area])
    assert benchmark.one_step(frame, params)[1] == pytest.approx(avg1, abs=1e-10)
    assert benchmark.open_loop(frame, params)[1] == pytest.approx(avg1, abs=1e-10)
    assert abs(avg1 - (eq1 + (end0 - eq1) * np.exp(-h / c * dt))) > 1e-4


def test_open_loop_never_reads_later_temperature_and_one_step_reads_only_past(observations):
    sample = observations.head(200).copy()
    params = np.array([.14, 5.8, 1.8])
    before = benchmark.open_loop(sample, params)
    sample.loc[1:, "indoor_temperature_mean_c"] = 9999.
    np.testing.assert_allclose(benchmark.open_loop(sample, params), before, equal_nan=True)
    sample = observations.head(200).copy()
    before = benchmark.one_step(sample, params)
    sample.loc[100, "indoor_temperature_mean_c"] += 50
    after = benchmark.one_step(sample, params)
    np.testing.assert_allclose(before[:101], after[:101], equal_nan=True)
    assert before[101] != after[101]


def test_parameter_fit_does_not_read_validation_or_test(observations):
    sample = observations.head(500).copy()
    expected, fit = benchmark.fit_training_only(sample, 300)
    sample.loc[300:, ["indoor_temperature_mean_c", "outdoor_temperature_mean_c", "total_room_heat_input_kw", "solar_reference_kw_m2"]] = 1e9
    actual, _ = benchmark.fit_training_only(sample, 300)
    np.testing.assert_array_equal(expected, actual)
    assert np.all(actual >= benchmark.LOWER) and np.all(actual <= benchmark.UPPER)
    assert fit["training_pairs"] == 299


def test_final_report_recomputes_every_score_and_keeps_bad_open_loop_results():
    report = json.loads((benchmark.OUTPUT / "validation_report.json").read_text(encoding="utf-8"))
    trajectory = pd.read_csv(benchmark.OUTPUT / "predictions_and_residuals.csv")
    for split in ("train", "validation", "test"):
        subset = trajectory.loc[trajectory["split"] == split]
        for mode, scores in report["metrics"][split].items():
            errors = subset[mode + "_c"] - subset["indoor_temperature_mean_c"]
            assert scores["mae_c"] == pytest.approx(np.abs(errors).mean(), abs=1e-9)
            assert scores["rmse_c"] == pytest.approx(np.sqrt((errors ** 2).mean()), abs=1e-9)
            assert scores["bias_predicted_minus_observed_c"] == pytest.approx(errors.mean(), abs=1e-9)
    assert "rc_continuous_open_loop" in report["metrics"]["test"]
    assert "rc_segment_open_loop" in report["metrics"]["test"]
    assert report["no_Wuhan_parameter_transfer"] is True
    assert report["no_random_split"] is True


def test_final_manifest_hashes():
    manifest = json.loads((benchmark.OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True
    for item in manifest["inputs"]:
        # Historical experiment inputs remain bound to their original code
        # epoch. Only the two explicitly archived sources are resolved here;
        # data inputs still have to match their original paths and hashes.
        source = ROOT / item["file"]
        if item["file"] in {"scripts/validate_external_rc_benchmark.py", "tests/test_external_rc_benchmark.py"}:
            source = benchmark.OUTPUT / "code_version" / item["file"]
        assert benchmark.digest(source) == item["sha256"]
    for item in manifest["outputs"]:
        assert benchmark.digest(benchmark.OUTPUT / item["file"]) == item["sha256"]
