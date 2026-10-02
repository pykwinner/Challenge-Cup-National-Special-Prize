import numpy as np
import pandas as pd
import pytest
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from heatplan.time_registry import describe_time_axis, require_same_continuous_axis


def continuous(n=48):
    return pd.DataFrame({"time": pd.date_range("2024-12-31", periods=n, freq="h", tz="Asia/Shanghai"),
                         "period_weight": 1.0, "scenario_probability": 1.0})


def describe(frame, **kwargs):
    return describe_time_axis(frame, kind="continuous", dt_hours=1, **kwargs)


def test_cross_calendar_year_and_timezone_equivalence():
    frame = continuous()
    original = describe(frame)
    frame["time"] = frame.time.dt.tz_convert("UTC")
    assert describe(frame)["axis_sha256"] == original["axis_sha256"]
    assert original["continuous_horizon_hours"] == 48
    assert original["full_year_claim"] is False
    require_same_continuous_axis(original, describe(frame))


@pytest.mark.parametrize("mutation", ["gap", "reverse", "duplicate", "naive", "nan", "weight", "negative_weight", "infinite_weight", "probability", "different_step", "multiple_groups", "multiple_scenarios"])
def test_reject_invalid_continuous_axes(mutation):
    frame = continuous()
    if mutation == "gap": frame = frame.drop(12)
    elif mutation == "reverse": frame = frame.iloc[::-1]
    elif mutation == "duplicate": frame.loc[12, "time"] = frame.loc[11, "time"]
    elif mutation == "naive": frame["time"] = frame.time.dt.tz_localize(None)
    elif mutation == "nan": frame.loc[12, "time"] = pd.NaT
    elif mutation == "weight": frame.loc[12, "period_weight"] = 2
    elif mutation == "negative_weight": frame.loc[12, "period_weight"] = -1
    elif mutation == "infinite_weight": frame.loc[12, "period_weight"] = np.inf
    elif mutation == "probability": frame.loc[12, "scenario_probability"] = .5
    elif mutation == "different_step": frame["dt_hours"] = 2
    elif mutation == "multiple_groups": frame["boundary_group"] = ["a"] * 24 + ["b"] * 24
    elif mutation == "multiple_scenarios": frame["scenario"] = ["a"] * 24 + ["b"] * 24
    with pytest.raises(ValueError): describe(frame)


def representatives():
    frame = continuous()
    frame["period"] = ["A"] * 24 + ["B"] * 24
    frame["period_weight"] = [20] * 24 + [30] * 24
    frame.loc[24:, "time"] += pd.Timedelta(days=60)
    return frame


def describe_representatives(frame):
    return describe_time_axis(frame, kind="representative", dt_hours=1, period_column="period")


def test_weighted_hours_do_not_imply_continuous_state_or_elapsed_time():
    result = describe_representatives(representatives())
    assert result["sampled_interval_hours"] == 48
    assert result["accounting_weighted_hours"] == 1200
    assert result["calendar_envelope_hours"] == 1488
    assert result["continuous_horizon_hours"] is None
    assert len(result["blocks"]) == 2
    with pytest.raises(ValueError): require_same_continuous_axis(result, result)


@pytest.mark.parametrize("mutation", ["weights", "labels", "gap", "no_weight", "interleaved"])
def test_representative_period_invariants(mutation):
    frame = representatives()
    if mutation == "weights": frame.loc[2, "period_weight"] = 21
    elif mutation == "labels": frame.loc[2, "period"] = None
    elif mutation == "gap": frame = frame.drop(2)
    elif mutation == "no_weight": frame = frame.drop(columns="period_weight")
    elif mutation == "interleaved": frame.loc[25, "period"] = "A"
    with pytest.raises(ValueError): describe_representatives(frame)


def test_registered_window_checked_and_shifted_comparison_rejected():
    frame = continuous()
    left = describe(frame, expected_start="2024-12-31T00:00+08:00", expected_end="2025-01-02T00:00+08:00")
    with pytest.raises(ValueError): describe(frame, expected_end="2025-01-03T00:00+08:00")
    frame["time"] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError): require_same_continuous_axis(left, describe(frame))


def test_cross_block_overlap_rejected_even_for_individually_valid_singletons():
    frame = pd.DataFrame({"time": ["2025-01-01T00:00+08:00", "2025-01-01T00:30+08:00"],
                          "period": ["A", "B"], "period_weight": [1, 1]})
    with pytest.raises(ValueError, match="overlap"): describe_representatives(frame)


@pytest.mark.parametrize("column", ["interval_end", "time_utc"])
def test_alternate_clock_columns_are_consistent(column):
    frame = continuous()
    frame[column] = frame.time + (pd.Timedelta(hours=1) if column == "interval_end" else pd.Timedelta(0))
    describe(frame)
    frame.loc[12, column] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match=column): describe(frame)


@pytest.mark.parametrize("step", [0, -1, np.nan, np.inf, True, 1e-20])
def test_bad_physical_step(step):
    with pytest.raises(ValueError): describe_time_axis(continuous(), kind="continuous", dt_hours=step)


def test_registry_rejects_mutated_inputs_duplicate_ids_and_escaped_paths(tmp_path):
    path = tmp_path / "hourly.csv"
    continuous().to_csv(path, index=False)
    specification = {"entries": [{"id": "test", "path": "hourly.csv",
        "sha256": sha256(path.read_bytes()).hexdigest(), "kind": "continuous", "dt_hours": 1,
        "role": "synthetic validation fixture"}]}
    source = Path(__file__).resolve().parents[1] / "scripts/build_time_registry.py"
    spec = spec_from_file_location("time_registry_runner", source)
    runner = module_from_spec(spec)
    spec.loader.exec_module(runner)
    result = runner.build_registry(tmp_path, specification)
    assert result["passed"] and len(result["entries"]) == 1
    assert "48" in runner.render(result)
    with pytest.raises(ValueError, match="duplicate"):
        runner.build_registry(tmp_path, {"entries": specification["entries"] * 2})
    before = specification["entries"][0]["path"]
    specification["entries"][0]["path"] = "../outside.csv"
    with pytest.raises(ValueError, match="escapes"):
        runner.build_registry(tmp_path, specification)
    specification["entries"][0]["path"] = before
    path.write_text("time\nchanged\n", encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        runner.build_registry(tmp_path, specification)
