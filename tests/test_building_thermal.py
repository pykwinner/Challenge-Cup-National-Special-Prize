"""Small real HiGHS solves and independent analytical checks for building RC."""
from copy import deepcopy
from math import exp
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from heatplan.building_thermal import (
    add_building_thermal_constraints,
    building_heat_reference,
    exact_rc_coefficients,
    validate_building_thermal_inputs,
)
from heatplan.linear import LinearMILP


def make_model(outdoor=(0.0,) * 6, *, dt=1.0, buildings=None, section=None):
    building = {
        "name": "home", "node": "district", "heat_grade": "low",
        "area_m2": 100.0, "heat_loss_kw_per_c": 1.0,
        "thermal_capacity_kwh_per_c": 10.0, "initial_temperature_c": 20.0,
        "min_temperature_c": -100.0, "max_temperature_c": 100.0,
        "final_min_temperature_c": -100.0,
    }
    config = {
        "nodes": [{"name": "district"}, {"name": "other"}],
        "grades": [{"name": "low"}, {"name": "high"}],
        "planning": {"time_step_hours": dt},
        "building_thermal": {"enabled": True, "buildings": buildings or [building]},
    }
    config["building_thermal"].update(section or {})
    frame = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=len(outdoor), freq=pd.Timedelta(hours=dt), tz="UTC"),
        "outdoor_temp": outdoor, "boundary_group": "winter", "scenario": "weather",
    })
    return SimpleNamespace(config=config, ts=frame, dt=dt, problem=LinearMILP(), variables={})


def spec(model):
    return model.config["building_thermal"]["buildings"][0]


def set_power(model, block, category, values, name="home"):
    mapping = getattr(block, category)
    for t, value in enumerate(values):
        model.problem.add_constraint({mapping[name, t]: 1.0}, lb=value, ub=value)


def solve(model):
    result = model.problem.solve(mip_gap=1e-9, time_limit=10)
    assert result.status == 0, result.message
    assert result.x is not None
    # Recompute all actual constraint rows and bounds, independent of the block exporter.
    for row, lo, hi in zip(model.problem.rows, model.problem.row_lower, model.problem.row_upper):
        activity = sum(result.x[index] * coefficient for index, coefficient in row.items())
        assert lo - 1e-6 <= activity <= hi + 1e-6
    assert np.all(result.x >= np.asarray(model.problem.lower) - 1e-6)
    assert np.all(result.x <= np.asarray(model.problem.upper) + 1e-6)
    return result.x


def test_heat_outage_matches_continuous_analytical_decay_with_half_hour_steps():
    model = make_model(outdoor=(-5.0,) * 8, dt=0.5)
    spec(model).update(heat_loss_kw_per_c=2.0, thermal_capacity_kwh_per_c=10.0)
    block = add_building_thermal_constraints(model)
    set_power(model, block, "heat", [0.0] * 8)
    set_power(model, block, "cooling", [0.0] * 8)
    values = solve(model)
    temperatures = [values[block.temperature["home", t]] for t in range(8)]
    expected = [-5.0 + 25.0 * exp(-2.0 * (t + 1) * 0.5 / 10.0) for t in range(8)]
    np.testing.assert_allclose(temperatures, expected, atol=1e-9)
    assert block.comfort_summary(values).iloc[0]["building_physics_passed"]


def test_recovery_after_outage_uses_actual_heat_and_meets_fixed_terminal():
    model = make_model()
    spec(model).pop("final_min_temperature_c")
    spec(model)["final_temperature_c"] = 20.0
    block = add_building_thermal_constraints(model)
    for t in range(3):
        model.problem.add_constraint({block.heat["home", t]: 1.0}, ub=0.0)
    set_power(model, block, "cooling", [0.0] * 6)
    for index in block.heat.values():
        model.problem.add_to_objective(index, 1.0)
    values = solve(model)
    assert values[block.temperature["home", 2]] == pytest.approx(20.0 * exp(-0.3))
    assert values[block.temperature["home", 5]] == pytest.approx(20.0)
    assert sum(values[block.heat["home", t]] for t in range(3, 6)) > 0
    trajectory = block.export_trajectory(values)
    for row in trajectory.itertuples():
        equilibrium = row.outdoor_temperature_c + row.heat_kw / row.heat_loss_kw_per_c
        expected = equilibrium + (row.temperature_start_c - equilibrium) * exp(-0.1)
        assert row.temperature_end_c == pytest.approx(expected)


@pytest.mark.parametrize("heat,cooling,direction", [(10.0, 0.0, 1.0), (0.0, 10.0, -1.0)])
def test_heat_and_cooling_have_opposite_correct_signs(heat, cooling, direction):
    model = make_model(outdoor=[20.0])
    block = add_building_thermal_constraints(model)
    set_power(model, block, "heat", [heat])
    set_power(model, block, "cooling", [cooling])
    values = solve(model)
    assert values[block.temperature["home", 0]] == pytest.approx(20.0 + direction * 10.0 * (1.0 - exp(-0.1)))


def test_different_h_and_c_produce_distinct_decay_without_area_rescaling():
    model = make_model(outdoor=[0.0] * 3)
    buildings = [dict(spec(model), name=name, heat_loss_kw_per_c=h, thermal_capacity_kwh_per_c=c)
                 for name, h, c in [("base", 1, 10), ("leaky", 2, 10), ("heavy", 1, 20)]]
    model.config["building_thermal"]["buildings"] = buildings
    block = add_building_thermal_constraints(model)
    for building in buildings:
        set_power(model, block, "heat", [0.0] * 3, name=building["name"])
        set_power(model, block, "cooling", [0.0] * 3, name=building["name"])
    values = solve(model)
    for building in buildings:
        assert values[block.temperature[building["name"], 2]] == pytest.approx(
            20.0 * exp(-3.0 * building["heat_loss_kw_per_c"] / building["thermal_capacity_kwh_per_c"]))
    assert values[block.temperature["leaky", 2]] < values[block.temperature["base", 2]] < values[block.temperature["heavy", 2]]


def test_hourly_internal_solar_gains_and_comfort_columns_drive_required_heat():
    model = make_model(outdoor=[0.0, 0.0, 30.0])
    spec(model).update(min_temperature_column="lo", max_temperature_column="hi",
                       internal_gain_column="people", solar_gain_column="sun", heating_service_column="heating")
    model.ts["lo"] = model.ts["hi"] = [20.0, 21.0, 22.0]
    model.ts["people"], model.ts["sun"], model.ts["heating"] = [2.0, 3.0, 4.0], [1.0, 5.0, 2.0], [1, 1, 0]
    block = add_building_thermal_constraints(model)
    values = solve(model)
    frame = block.export_trajectory(values)
    starts = [20.0, 20.0, 21.0]
    for t, row in enumerate(frame.itertuples()):
        expected_net = (row.temperature_end_c - exp(-0.1) * starts[t]) / (1 - exp(-0.1)) - row.outdoor_temperature_c - row.internal_gain_kw - row.solar_gain_kw
        assert row.heat_kw - row.cooling_kw == pytest.approx(expected_net)
    assert frame.iloc[2]["cooling_kw"] > 0
    assert block.comfort_summary(values).iloc[0]["heating_service_hours"] == 2.0


def test_constant_internal_gain_reduces_steady_heating():
    model = make_model(outdoor=[0.0])
    spec(model).update(min_temperature_c=20.0, max_temperature_c=20.0, internal_gain_kw=3.0)
    block = add_building_thermal_constraints(model)
    values = solve(model)
    assert values[block.heat["home", 0]] == pytest.approx(17.0)


def test_fixed_groups_use_separate_initial_and_terminal_conditions():
    model = make_model(outdoor=[0.0] * 4)
    model.ts["boundary_group"] = ["day_a", "day_a", "day_b", "day_b"]
    spec(model)["boundary_conditions"] = {
        "day_a": {"initial_temperature_c": 10.0, "final_min_temperature_c": -100.0},
        "day_b": {"initial_temperature_c": 30.0, "final_min_temperature_c": -100.0},
    }
    block = add_building_thermal_constraints(model)
    set_power(model, block, "heat", [0.0] * 4)
    set_power(model, block, "cooling", [0.0] * 4)
    values = solve(model)
    actual = [values[block.temperature["home", t]] for t in range(4)]
    np.testing.assert_allclose(actual, [10 * exp(-0.1), 10 * exp(-0.2), 30 * exp(-0.1), 30 * exp(-0.2)])
    assert len(block.comfort_summary(values)) == 2


def test_terminal_requirement_applies_to_each_fixed_group():
    model = make_model(outdoor=[0.0] * 4)
    model.ts["boundary_group"] = ["day_a", "day_a", "day_b", "day_b"]
    spec(model).pop("final_min_temperature_c")  # Default recovery to initial T.
    block = add_building_thermal_constraints(model)
    set_power(model, block, "cooling", [0.0] * 4)
    for t in [0, 1]:
        model.problem.add_constraint({block.heat["home", t]: 1.0}, ub=0.0)
    assert model.problem.solve(time_limit=10).status == 2


@pytest.mark.parametrize("steps", [1, 2])
def test_cyclic_groups_wrap_only_within_their_own_period_including_single_step(steps):
    model = make_model(outdoor=[0.0] * steps + [30.0] * steps)
    spec(model).pop("final_min_temperature_c")
    spec(model)["boundary_mode"] = "cyclic"
    model.ts["boundary_group"] = ["cold"] * steps + ["hot"] * steps
    block = add_building_thermal_constraints(model)
    set_power(model, block, "heat", [0.0] * (2 * steps))
    set_power(model, block, "cooling", [0.0] * (2 * steps))
    values = solve(model)
    actual = [values[block.temperature["home", t]] for t in range(2 * steps)]
    np.testing.assert_allclose(actual, [0.0] * steps + [30.0] * steps, atol=1e-7)
    frame = block.export_trajectory(values)
    assert frame.iloc[0]["temperature_start_c"] == pytest.approx(0.0)
    assert frame.iloc[steps]["temperature_start_c"] == pytest.approx(30.0)


def test_actual_supply_shortage_cannot_pass_comfort_using_unserved_variable():
    model = make_model(outdoor=[0.0] * 3)
    spec(model).update(min_temperature_c=20.0, max_temperature_c=20.0)
    block = add_building_thermal_constraints(model)
    for t in range(3):
        source = model.problem.add_var(f"actual_supply[{t}]", ub=10.0)
        # A legacy unmet-reference-load variable cannot warm the RC state.
        unserved = model.problem.add_var(f"unserved[{t}]", ub=20.0)
        model.problem.add_constraint({source: 1.0, unserved: 1.0}, lb=20.0, ub=20.0)
        model.problem.add_constraint({source: 1.0, block.node_heat["district", "low", t]: -1.0}, lb=0.0, ub=0.0)
    assert model.problem.solve(time_limit=10).status == 2


def test_full_delivery_restores_feasibility_and_node_maps_conserve_buildings():
    model = make_model(outdoor=[0.0, 30.0])
    buildings = [dict(spec(model), name="home", min_temperature_c=20.0, max_temperature_c=20.0),
                 dict(spec(model), name="office", min_temperature_c=20.0, max_temperature_c=20.0, heat_loss_kw_per_c=2.0),
                 dict(spec(model), name="clinic", node="other", heat_grade="high", min_temperature_c=20.0, max_temperature_c=20.0)]
    model.config["building_thermal"]["buildings"] = buildings
    block = add_building_thermal_constraints(model)
    for (node, grade, t), index in block.node_heat.items():
        source = model.problem.add_var(f"supply[{node},{grade},{t}]", ub=60.0)
        model.problem.add_constraint({source: 1.0, index: -1.0}, lb=0.0, ub=0.0)
    for (node, t), index in block.node_cooling.items():
        source = model.problem.add_var(f"cold_supply[{node},{t}]", ub=30.0)
        model.problem.add_constraint({source: 1.0, index: -1.0}, lb=0.0, ub=0.0)
    values = solve(model)
    assert values[block.node_heat["district", "low", 0]] == pytest.approx(60.0)
    assert values[block.node_cooling["district", 1]] == pytest.approx(30.0)
    assert values[block.node_heat["other", "high", 0]] == pytest.approx(20.0)
    assert block.node_heat["other", "high", 0] == block.heat["clinic", 0]
    assert block.covered_node_grades == {("district", "low"), ("other", "high")}
    assert block.covered_cooling_nodes == {"district", "other"}
    assert "building_thermal_state" not in model.variables
    for index in block.node_heat.values():
        assert model.problem.objective[index] == 0.0


def test_exclusive_mode_rejects_simultaneous_heat_and_cooling_even_with_zero_cost():
    model = make_model(outdoor=[20.0])
    block = add_building_thermal_constraints(model)
    set_power(model, block, "heat", [5.0])
    set_power(model, block, "cooling", [5.0])
    assert model.problem.solve(time_limit=10).status == 2


def test_relaxed_mode_is_explicit_and_exports_simultaneous_flow():
    model = make_model(outdoor=[20.0], section={"simultaneous_mode": "relaxed"})
    block = add_building_thermal_constraints(model)
    set_power(model, block, "heat", [5.0])
    set_power(model, block, "cooling", [5.0])
    values = solve(model)
    assert not block.mode
    summary = block.comfort_summary(values).iloc[0]
    assert summary["simultaneous_heat_cooling"]
    assert not summary["no_simultaneous_heat_cooling_passed"]
    assert not summary["building_physics_passed"]
    assert summary["relaxed_equations_passed"]


def test_relaxed_solution_without_simultaneous_flow_is_physically_feasible():
    model = make_model(outdoor=[0.0], section={"simultaneous_mode": "relaxed"})
    spec(model).update(min_temperature_c=20.0, max_temperature_c=20.0)
    block = add_building_thermal_constraints(model)
    values = solve(model)
    summary = block.comfort_summary(values).iloc[0]
    assert summary["no_simultaneous_heat_cooling_passed"]
    assert summary["building_physics_passed"]


def test_derived_bounds_and_fixed_comfort_eliminate_unnecessary_binaries():
    model = make_model(outdoor=[-10.0, 35.0])
    spec(model).update(min_temperature_c=20.0, max_temperature_c=20.0)
    block = add_building_thermal_constraints(model)
    assert not block.mode
    assert len(model.problem.names) == 6
    values = solve(model)
    assert values[block.heat["home", 0]] == pytest.approx(30.0)
    assert values[block.cooling["home", 1]] == pytest.approx(15.0)


@pytest.mark.parametrize("service,weather", [("heating_enabled", 0.0), ("cooling_enabled", 40.0)])
def test_disabled_heat_or_cooling_cannot_preserve_unachievable_comfort(service, weather):
    model = make_model(outdoor=[weather])
    spec(model).update(min_temperature_c=20.0, max_temperature_c=20.0)
    spec(model)[service] = False
    add_building_thermal_constraints(model)
    assert model.problem.solve(time_limit=10).status == 2


def test_reporting_uses_dt_and_period_weight_without_multiplying_state_transition():
    model = make_model(outdoor=[0.0, 0.0], dt=0.5)
    spec(model).update(min_temperature_c=20.0, max_temperature_c=20.0)
    model.ts["period_weight"] = 20.0
    block = add_building_thermal_constraints(model)
    values = solve(model)
    summary = block.comfort_summary(values).iloc[0]
    assert summary["trajectory_hours"] == 1.0
    assert summary["weighted_hours"] == 20.0
    assert summary["heat_kwh"] == pytest.approx(20.0)
    assert summary["weighted_heat_kwh"] == pytest.approx(400.0)
    assert summary["underheated_degree_hours_end_sample"] == 0.0


def test_export_detects_fabricated_comfort_when_actual_heat_is_removed():
    model = make_model(outdoor=[0.0])
    spec(model).update(min_temperature_c=20.0, max_temperature_c=20.0)
    block = add_building_thermal_constraints(model)
    values = solve(model)
    values[block.heat["home", 0]] = 0.0
    summary = block.comfort_summary(values).iloc[0]
    assert summary["underheated_degree_hours_end_sample"] == 0.0
    assert summary["max_rc_residual_c"] > 1.0
    assert not summary["building_physics_passed"]
    values[block.temperature["home", 0]] = 18.0
    summary = block.comfort_summary(values).iloc[0]
    assert summary["underheated_degree_hours_end_sample"] == 2.0
    assert summary["underheated_hours_end_sample"] == 1.0
    with pytest.raises(ValueError, match="complete finite"):
        block.export_trajectory(np.array([np.nan]))


@pytest.mark.parametrize("key,value", [
    ("area_m2", 0), ("area_m2", float("inf")), ("area_m2", True),
    ("heat_loss_kw_per_c", -1), ("thermal_capacity_kwh_per_c", 0),
    ("initial_temperature_c", float("nan")), ("min_temperature_c", 101),
    ("min_temperature_column", "missing"), ("max_temperature_column", "missing"),
    ("internal_gain_column", "missing"), ("solar_gain_column", "missing"),
    ("node", "unknown"), ("heat_grade", "unknown"), ("max_heat_kw", -1),
    ("max_cooling_kw", -1), ("boundary_mode", "year_wrap"),
    ("cooling_enabled", "false"), ("heating_enabled", 1),
    ("boundary_conditions", {"unknown": {"initial_temperature_c": 18}}),
])
def test_invalid_building_inputs_rejected_before_model_mutation(key, value):
    model = make_model()
    spec(model)[key] = value
    with pytest.raises(ValueError):
        add_building_thermal_constraints(model)
    assert not model.problem.names
    assert not model.problem.rows
    assert not model.variables


@pytest.mark.parametrize("column,values", [
    ("outdoor_temp", [0, np.nan]), ("gain", [1, -1]), ("gain", [1, np.inf]),
    ("gain", [1, "bad"]), ("low", [21, 23]), ("service", [1, 0.5]),
])
def test_invalid_timeseries_values_rejected(column, values):
    model = make_model(outdoor=[0.0, 0.0])
    spec(model).update(internal_gain_column="gain", min_temperature_column="low", max_temperature_c=22,
                       heating_service_column="service")
    model.ts["gain"], model.ts["low"], model.ts["service"] = 0.0, 18.0, 1
    model.ts[column] = values
    with pytest.raises(ValueError):
        add_building_thermal_constraints(model)


@pytest.mark.parametrize("case", ["duplicate_name", "cyclic_terminal", "both_gains", "conflicting_final", "fixed_final_outside_comfort"])
def test_ambiguous_or_inconsistent_configurations_fail(case):
    model = make_model()
    if case == "duplicate_name":
        model.config["building_thermal"]["buildings"].append(deepcopy(spec(model)))
    elif case == "cyclic_terminal":
        spec(model)["boundary_mode"] = "cyclic"
    elif case == "both_gains":
        model.ts["gain"] = 0.0
        spec(model).update(internal_gain_kw=0.0, internal_gain_column="gain")
    elif case == "conflicting_final":
        spec(model)["final_temperature_c"] = 20.0
    else:
        spec(model)["final_min_temperature_c"] = 101.0
    with pytest.raises(ValueError):
        add_building_thermal_constraints(model)


@pytest.mark.parametrize("case", ["gap", "duplicate", "naive", "missing", "interleaved", "cross_scenario", "partition_mismatch", "wrong_dt"])
def test_invalid_chronology_and_partitions_are_rejected(case):
    model = make_model(outdoor=[0.0] * 4)
    if case == "gap":
        model.ts.loc[2:, "time"] += pd.Timedelta(hours=1)
    elif case == "duplicate":
        model.ts.loc[1, "time"] = model.ts.loc[0, "time"]
    elif case == "naive":
        model.ts["time"] = model.ts["time"].dt.tz_localize(None)
    elif case == "missing":
        model.ts.loc[1, "time"] = pd.NaT
    elif case == "interleaved":
        model.ts["boundary_group"] = ["a", "b", "a", "b"]
    elif case == "cross_scenario":
        model.ts["scenario"] = ["a", "a", "b", "b"]
    elif case == "partition_mismatch":
        model.boundary_groups = {"winter": [0, 1, 2]}
    else:
        model.dt = 0
    with pytest.raises(ValueError):
        add_building_thermal_constraints(model)


def test_disabled_module_preserves_legacy_inputs_and_registration_is_not_repeatable():
    model = make_model()
    model.config["building_thermal"]["enabled"] = False
    model.ts = pd.DataFrame()
    assert add_building_thermal_constraints(model) is None
    assert not model.problem.names
    model = make_model()
    add_building_thermal_constraints(model)
    count = len(model.problem.names)
    with pytest.raises(ValueError, match="already registered"):
        add_building_thermal_constraints(model)
    assert len(model.problem.names) == count


def test_minimal_adapter_matches_heatplanningmodel_boundary_mapping():
    model = make_model()
    model.boundary_groups = {"winter": list(range(6))}
    model.time_predecessor = {t: (t - 1) % 6 for t in range(6)}
    block = add_building_thermal_constraints(model)
    assert block.temperature is model.variables["building_temperature"]
    assert block.node_heat is model.variables["building_heat_by_node"]
    assert validate_building_thermal_inputs(model.config, model.ts) is not None


def test_coefficient_small_decay_stability_and_invalid_physics():
    a, b = exact_rc_coefficients(1e-9, 10.0, 0.5)
    assert a == pytest.approx(exp(-5e-11))
    assert b == pytest.approx(0.05, abs=2e-12)
    for parameters in [(0, 10, 1), (1, 0, 1), (1, 10, -1), (np.inf, 10, 1)]:
        with pytest.raises(ValueError):
            exact_rc_coefficients(*parameters)


def test_planning_and_preflight_share_conservative_reference_without_dhw_coverage():
    model = make_model(outdoor=[0.0, 10.0])
    spec(model).update(min_temperature_c=18.0, max_temperature_c=24.0, internal_gain_kw=5.0,
                       min_temperature_column="comfort")
    model.ts["comfort"] = [18.0, 20.0]
    model.config["building_thermal"]["buildings"].append(dict(spec(model), name="office", heat_loss_kw_per_c=2.0))
    model.config["nodes"][0]["demand_columns"] = {"low": "old_heat", "high": "dhw"}
    # There is deliberately no dependency on reference load columns for RC input.
    block = add_building_thermal_constraints(model)
    reference = block.heat_reference(design_outdoor_temperature_c=-5.0)
    preflight = building_heat_reference(validate_building_thermal_inputs(model.config, model.ts),
                                       design_outdoor_temperature_c=-5.0)
    assert reference == preflight
    assert reference.node_grade_reference_kw == {("district", "low", 0): 54.0, ("district", "low", 1): 30.0}
    assert reference.node_grade_design_peak_kw == {("district", "low"): 75.0}
    assert reference.building_design_peak_kw == {"home": 25.0, "office": 50.0}
    assert block.covered_node_grades == {("district", "low")}
    assert ("district", "high") not in block.covered_node_grades
    assert block.heat_reference().design_outdoor_temperature_c == 0.0
    with pytest.raises(ValueError):
        block.heat_reference(design_outdoor_temperature_c=float("nan"))
