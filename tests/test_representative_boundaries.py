"""Small artificial counterexamples; these fixtures are not experimental data."""
import numpy as np
import pandas as pd
import pytest

from heatplan.scenarios import (
    calibrate_representative_day_weights,
    cluster_typical_days,
    extreme_aware_typical_days,
)


def frame(loads):
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=len(loads), freq="h"),
        "demand": loads,
    })


def select(data):
    return extreme_aware_typical_days(data, ["demand"], ["demand"], n_clusters=1)


def ramp_days(result):
    return set(result.loc[result.extreme_reasons.str.contains("maximum_system_ramp"), "original_day_index"])


def test_midnight_ramp_retains_both_observed_days_and_conserves_weights():
    data = frame(np.repeat([2., 3., 20., 19., 18., 17.], 24))
    result = select(data)
    assert ramp_days(result) == {1, 2}
    assert result.groupby("representative_day").period_weight.first().sum() == 6
    assert not result.attrs["interday_operational_constraints_added"]
    pd.testing.assert_series_equal(data.demand, pd.Series(np.repeat([2., 3., 20., 19., 18., 17.], 24), name="demand"))


@pytest.mark.parametrize("boundary", ["gap", "scenario", "boundary_group", "source_weather_year"])
def test_no_ramp_across_gap_or_independent_operating_boundaries(boundary):
    data = frame(np.repeat([1., 100., 100.], 24))
    data.loc[36:47, "demand"] = 102.
    if boundary == "gap":
        data.loc[24:, "time"] += pd.Timedelta(days=1)
    else:
        data[boundary] = ["a"] * 24 + ["b"] * 48
    assert ramp_days(select(data)) == {1}


def test_incomplete_day_cannot_be_disguised_as_24_rows():
    data = frame(np.arange(48, dtype=float))
    data.loc[12:, "time"] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="连续逐小时"):
        select(data)


def test_opaque_labels_only_support_within_block_ramps():
    data = frame(np.repeat([1., 100., 100.], 24))
    data["time"] = [f"h{i}" for i in range(len(data))]
    data.loc[36:47, "demand"] = 102.
    result = select(data)
    assert ramp_days(result) == {1}
    assert result.attrs["ramp_adjacency_mode"] == "opaque_labels_within_block_only"


def test_real_timestamp_takes_precedence_over_integer_model_keys():
    data = frame(np.repeat([1., 100., 100.], 24))
    data["timestamp"] = data["time"]
    data["time"] = np.arange(len(data))
    assert ramp_days(select(data)) == {0, 1}


def test_duplicate_profiles_return_actual_available_clusters():
    data = frame(np.repeat([1., 1., 1., 3., 3.], 24))
    days, weights, labels = cluster_typical_days(data, ["demand"], n_clusters=4)
    assert len(days) == 2
    assert sorted(weights) == [2, 3]
    assert set(labels) == {0, 1}
    assert sum(weights) == 5


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_missing_anchor_value_cannot_silently_hide_extreme(bad):
    data = frame(np.arange(48, dtype=float))
    data["outdoor_temp"] = 2.
    data.loc[5, "outdoor_temp"] = bad
    with pytest.raises(ValueError, match="有限数值"):
        select(data)


def calibration_inputs():
    representative = pd.DataFrame({
        "representative_day": np.repeat(["low", "high"], 24),
        "period_weight": 1.,
        "demand": np.repeat([2., 6.], 24),
    })
    return representative, pd.DataFrame({"demand": [3.] * 48})


@pytest.mark.parametrize("target", [True, False])
def test_calibration_rejects_missing_energy_instead_of_skipna_sum(target):
    rep, full = calibration_inputs()
    (full if target else rep).loc[3, "demand"] = np.nan
    with pytest.raises(ValueError, match="静默跳过"):
        calibrate_representative_day_weights(rep, full, ["demand"])


def test_calibration_matches_closed_form_zero_regularization_solution():
    rep, full = calibration_inputs()
    result = calibrate_representative_day_weights(rep, full, ["demand"], regularization=0.)
    # 2*w_low + 6*w_high = 6 and w_low + w_high = 2.
    np.testing.assert_allclose(result.weights.calibrated_weight, [1.5, .5], atol=1e-6)
    assert result.summary["weight_conservation_residual"] < 1e-8
    assert result.summary["energy_matching"] == "soft_regularized_least_squares"


@pytest.mark.parametrize("option", ["regularization", "minimum_day_weight", "minimum_extreme_weight"])
def test_calibration_rejects_nonfinite_controls(option):
    rep, full = calibration_inputs()
    with pytest.raises(ValueError):
        calibrate_representative_day_weights(rep, full, ["demand"], **{option: np.nan})


def minimal_config():
    return {
        "planning": {"discount_rate": .05, "asset_lifetime_years": 20, "time_step_hours": 1},
        "grades": [{"name": "low", "temperature_c": 45}],
        "nodes": [{"name": "n", "demand_columns": {"low": "demand"}}],
        "sources": [], "pipes": [], "storages": [],
    }


def test_multiyear_keeps_opaque_integer_keys_backward_compatible():
    from heatplan.representative import build_multiyear_representative_days
    data = frame(np.ones(48))
    data["time"] = np.arange(48)
    data["outdoor_temp"] = 0.
    result = build_multiyear_representative_days(minimal_config(), {"case": data}, n_clusters=1)
    assert result.candidate_timeseries.attrs["ramp_adjacency_mode"] == "opaque_labels_within_block_only"


def test_multiyear_rc_calendar_round_trip_preserves_real_time_and_daily_boundaries():
    from heatplan.representative import build_multiyear_representative_days
    from heatplan.building_thermal import validate_building_thermal_inputs
    from heatplan.model import HeatPlanningModel
    config = minimal_config()
    config["building_thermal"] = {"enabled": True, "buildings": [{
        "name": "b", "node": "n", "heat_grade": "low", "area_m2": 100,
        "heat_loss_kw_per_c": 1, "thermal_capacity_kwh_per_c": 3,
        "initial_temperature_c": 20, "min_temperature_c": 18,
        "max_temperature_c": 24, "max_cooling_kw": 0,
    }]}
    data = frame(np.repeat([1., 2.], 24))
    data["time"] = pd.to_datetime(data["time"], utc=True)
    data["outdoor_temp"] = np.repeat([0., -5.], 24)
    data["electricity_price"] = .5
    data["grid_emission_factor"] = 1.
    result = build_multiyear_representative_days(config, {"2024": data}, n_clusters=1)
    output = result.calibration.timeseries
    rc = validate_building_thermal_inputs(config, output)
    assert rc is not None
    assert all(len(times) == 24 for times in rc.boundary_groups.values())
    assert output["representative_time_id"].is_unique
    assert set(pd.to_datetime(output["time"], utc=True)).issubset(set(data["time"]))
    model = HeatPlanningModel(config, output)
    model.build()
    assert result.calibration.summary["energy_target_scope"] == "exogenous_reference_columns_not_endogenous_RC_dispatch"


def test_multiyear_rejects_nonhourly_step_for_24_hour_daily_aggregation():
    from heatplan.representative import build_multiyear_representative_days
    config = minimal_config()
    config["planning"]["time_step_hours"] = .5
    data = frame(np.ones(48))
    data["outdoor_temp"] = 0.
    with pytest.raises(ValueError, match="time_step_hours=1"):
        build_multiyear_representative_days(config, {"case": data}, n_clusters=1)


@pytest.mark.parametrize("case", ["gap", "year", "interleaved_group"])
def test_coverage_ramp_never_bridges_unobserved_or_independent_edges(case):
    from heatplan.chronology import _maximum_grouped_ramp
    data = frame(np.repeat([1., 100.], 24))
    if case == "gap":
        data.loc[24:, "time"] += pd.Timedelta(days=1)
    elif case == "year":
        data["source_weather_year"] = np.repeat(["a", "b"], 24)
    else:
        data["boundary_group"] = ["a", "b"] * 24
    assert _maximum_grouped_ramp(data, "demand") == 0.


@pytest.mark.parametrize("step", [1., 2.])
def test_prepared_chronology_explicitly_checks_configured_time_step(step):
    from heatplan.chronology import prepare_full_year_timeseries
    data = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=12, freq=f"{int(step)}h")})
    assert len(prepare_full_year_timeseries(data, expected_step_hours=step)) == 12
    with pytest.raises(ValueError, match="time_step_hours"):
        prepare_full_year_timeseries(data, expected_step_hours=step + 1.)


def test_coverage_ramp_counts_configured_two_hour_edges_but_no_cyclic_wrap():
    from heatplan.chronology import _maximum_grouped_ramp
    data = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=12, freq="2h"), "demand": np.arange(12.)})
    assert _maximum_grouped_ramp(data, "demand", expected_step_hours=2.) == 1.
