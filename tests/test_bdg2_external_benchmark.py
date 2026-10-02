"""Focused adapter tests: original timestamps, missingness, budgets, metrics."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bdg2_benchmark_tests_adapter", ROOT / "scripts/bdg2_external_benchmark.py")
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)


def hourly(dates, loads=None):
    times = pd.DatetimeIndex(np.concatenate([pd.date_range(d, periods=24, freq="h").to_numpy() for d in dates]))
    values = np.ones(len(times)) if loads is None else np.asarray(loads, dtype=float)
    return pd.DataFrame({"time": times, "load_kw": values, "energy_kwh": values, "outdoor_temp": np.zeros(len(times))})


def candidate(building, site, coverage=1.0):
    return {"building_id": building, "site_id": site, "sqm": 10, "primaryspaceusage": "test",
            "years": {str(y): {"rows": 8784 if y == 2016 else 8760, "finite": 8784 if y == 2016 else 8760,
                     "coverage": coverage, "negative": 0, "positive": 100, "weather_join_valid_count": 8000,
                     "weather_join_coverage": coverage, "sum_observed_values": 500} for y in (2016, 2017)}}


def test_midnight_ramp_is_real_and_needs_both_endpoint_days():
    x = hourly(["2016-01-01", "2016-01-02"], [0]*24+[10]*24)
    m, events = b.compute_metrics(x, [0], [2])
    assert m["maximum_real_ramp_kw_per_hour"] == 10
    assert m["maximum_real_ramp_crosses_midnight"] is True
    assert m["maximum_real_ramp_both_days_selected"] is False
    assert events[0]["start"] == "2016-01-01 23:00:00"
    both, _ = b.compute_metrics(x, [0, 1], [1, 1])
    assert both["maximum_real_ramp_retention_ratio"] == 1
    assert both["ramp_tail_event_coverage"] == 1


def test_missing_day_does_not_create_fake_ramp():
    x = hourly(["2016-01-01", "2016-01-03"], [0]*24+[10]*24)
    m, events = b.compute_metrics(x, [0, 1], [1, 1])
    assert m["real_hour_edges"] == 46
    assert m["positive_ramp_edges"] == 0
    assert m["maximum_real_ramp_retention_ratio"] is None
    assert events == []


def test_nan_excludes_whole_day_and_preserves_actual_date_and_value():
    x = hourly(["2016-01-01", "2016-01-02", "2016-01-03"])
    x.loc[25, "energy_kwh"] = np.nan
    full, retained, excluded, q = b.prepare_year(x, 2016)
    assert pd.isna(full.loc[25, "energy_kwh"])
    assert q["retained_days"] == 2
    assert retained.iloc[24]["time"] == pd.Timestamp("2016-01-03")
    assert next(d for d in excluded if d["date"] == "2016-01-02")["missing_or_nonfinite_meter_hours"] == 1


@pytest.mark.parametrize("mode", ["duplicate", "reversed", "unaligned"])
def test_invalid_source_times_rejected(mode):
    x = hourly(["2016-01-01"])
    if mode == "duplicate":
        x.loc[1, "time"] = x.loc[0, "time"]
    elif mode == "reversed":
        x = x.iloc[::-1]
    else:
        x.loc[0, "time"] += pd.Timedelta(minutes=30)
    with pytest.raises(ValueError):
        b.prepare_year(x, 2016)


def test_partial_day_peak_exclusion_remains_visible():
    x = hourly(["2016-01-01", "2016-01-02"])
    x.loc[25, "energy_kwh"] = 100
    x.loc[26, "outdoor_temp"] = np.nan
    _, retained, _, quality = b.prepare_year(x, 2016)
    assert quality["original_observed_peak_kw"] == 100
    assert quality["retained_scope_peak_kw"] == 1
    assert quality["original_peak_excluded_from_retained_scope"] is True
    assert len(retained) == 24


def test_known_weighted_energy_and_ldc_are_exact():
    x = hourly(["2016-01-01", "2016-01-02", "2016-01-03"], [1]*48+[4]*24)
    m, _ = b.compute_metrics(x, [0, 2], [2, 1])
    assert m["retained_scope_energy_bias"] == 0
    assert m["ldc_wasserstein1_kw"] == 0
    assert m["weight_sum_days"] == 3
    assert m["capacity_only_deficit_hours"] == 0


def test_capacity_only_is_scoped_hourly_deficit():
    x = hourly(["2016-01-01", "2016-01-02"], [1]*24+[4]*24)
    m, _ = b.compute_metrics(x, [0], [2])
    assert m["capacity_only_k_kw"] == 1
    assert m["capacity_only_deficit_hours"] == 24
    assert m["capacity_only_deficit_energy_kwh"] == 72
    assert m["capacity_only_deficit_energy_fraction"] == pytest.approx(0.6)


def test_zero_load_undefined_ratios_are_null():
    x = hourly(["2016-01-01"], [0]*24)
    m, _ = b.compute_metrics(x, [0], [1])
    assert m["retained_scope_energy_bias"] is None
    assert m["peak_retention_ratio"] is None
    assert m["ldc_w1_over_mean_load"] is None
    assert m["null_reasons"]


def test_budget_degeneration_is_explicit():
    x = hourly(["2016-01-01", "2016-01-02"])
    core = SimpleNamespace(cluster_typical_days=lambda *args, **kwargs: ([0], [2], np.zeros(2)))
    with pytest.raises(ValueError, match="budget 1 differs from requested 2"):
        b.execute_method(core, x, "ordinary", 2)
    with pytest.raises(ValueError, match="unique"):
        b.validate_representatives([0, 0], [1, 1], 2)
    with pytest.raises(ValueError, match="finite and nonnegative"):
        b.validate_representatives([0], [np.nan], 2)
    with pytest.raises(ValueError, match="sum"):
        b.validate_representatives([0], [1], 2)


def test_legacy_repeated_profiles_failure_is_not_hidden():
    legacy = b.load_core(ROOT / "docs/execution_20261002/algorithm/scenarios_before.py", "legacy_test")
    x = hourly(["2016-01-01", "2016-01-02"])
    with pytest.warns(UserWarning):
        with pytest.raises(ValueError):
            b.execute_method(legacy, x, "ordinary", 2)


def test_selection_stable_and_2017_performance_is_not_a_filter():
    items = [candidate("z", "B"), candidate("b", "A"), candidate("a", "A"), candidate("c", "C")]
    inventories = {cat: [{**r, "building_id": cat+r["building_id"]} for r in items] for cat in b.CATEGORIES}
    first = b.select_cases(inventories, target=2)
    for records in inventories.values():
        records.reverse()
        for r in records:
            r["years"]["2017"]["positive"] = 0
            r["years"]["2017"]["sum_observed_values"] = 0
    second = b.select_cases(inventories, target=2)
    assert [x["building_id"] for x in first["cases"]] == [x["building_id"] for x in second["cases"]]
    assert [x["site_id"] for x in first["cases"][:2]] == ["A", "B"]


def test_2017_quality_rejection_is_recorded_and_follows_fixed_order():
    bad = candidate("a", "A")
    bad["years"]["2017"]["weather_join_coverage"] = 0.94
    inventories = {cat: [{**r, "building_id": cat+r["building_id"]} for r in [bad, candidate("b", "A")]] for cat in b.CATEGORIES}
    result = b.select_cases(inventories, target=1)
    assert result["cases"][0]["building_id"] == "hotwaterb"
    assert result["decisions"][0]["reasons"] == ["joint_temperature_coverage_below_95pct"]


def test_calibration_uses_same_days_and_respects_extreme_lower_bound():
    core = b.load_core(ROOT / "src/heatplan/scenarios.py", "calibration_test")
    x = hourly(["2016-01-01", "2016-01-02", "2016-01-03"], [1]*24+[2]*24+[10]*24)
    selection = {"days": [0, 2], "weights": [2.0, 1.0], "reasons": {"0": "", "2": "maximum_peak"}, "requested_k": 1}
    result = b.execute_calibration(core, x, selection)
    assert result["days"] == [0, 2]
    assert sum(result["weights"]) == pytest.approx(3)
    assert result["weights"][1] >= 1
    assert result["calibration_summary"]["solver_success"]
