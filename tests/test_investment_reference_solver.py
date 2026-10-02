"""Analytical toy cases for the independently enumerated investment LP.

These fixtures are artificial mathematical checks, not equipment measurements.
The prescribed constant-temperature balance fixes heat demand independently of
the optimizer.  A separate free-decay example distinguishes exact RC dynamics
from Euler dynamics.  No source CSV or frozen experiment is read or modified.
"""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import math
import sys

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/run_investment_reference.py"
    if not path.is_file():
        pytest.fail(f"Investment reference runner has not been created: {path}")
    spec = spec_from_file_location("investment_reference_math_test_runner", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def toy():
    protocol = {
        "physics": {
            "H_kw_per_K": 0.1,
            "C_kwh_per_K": 0.5,
            "initial_c": 20.0,
            "terminal_c": 20.0,
            "min_c": 20.0,
            "max_c": 20.0,
            "dt_hours": 1.0,
        },
        "design": {
            "eta_resistance": 0.97,
            "eer_cooling": 3.0,
            "max_resistance_kw": 6.0,
            "max_cooling_kw": 2.0,
            "max_grid_kw": 4.0,
            "resistance_capex_cny_per_kw": 1.0,
            "cooling_package_capex_cny": 7.0,
            "hp_package_capex_cny": 10.0,
            "resistance_fixed_om_cny_per_kw_window": 0.2,
            "hp_fixed_om_cny_per_unit_window": 0.3,
            "cooling_fixed_om_cny_per_window": 0.8,
            "repeat_window_factor": 1.0,
            "hp_unit_candidates": [0, 1],
        },
    }
    frame = pd.DataFrame({
        "time": pd.date_range("2024-01-01T00:00:00+08:00", periods=2, freq="h").astype(str),
        "outdoor_temp": [0.0, 0.0],
        "electricity_price": [1.0, 1.0],
        "grid_emission_factor": [0.5, 0.5],
        "internal_gain_kw": [0.0, 0.0],
        "solar_gain_kw": [0.0, 0.0],
        "cop_hp": [3.0, 3.0],
        "capacity_hp_per_unit_kw": [2.0, 2.0],
    })
    return frame, protocol


def independent_totals(candidate, frame, protocol):
    """Recompute the agreed capital + repeated-window objective and CO2."""
    dispatch = candidate["dispatch"]
    design, physics = protocol["design"], protocol["physics"]
    dt = physics["dt_hours"]
    electricity = dispatch["electricity_kw"].to_numpy(dtype=float)
    window_bill = float(np.dot(electricity, frame["electricity_price"]) * dt)
    window_co2 = float(np.dot(electricity, frame["grid_emission_factor"]) * dt)
    units = candidate["hp_units"]
    capacity = candidate["resistance_capacity_kw"]
    capital = (units * design["hp_package_capex_cny"]
               + capacity * design["resistance_capex_cny_per_kw"]
               + design["cooling_package_capex_cny"])
    fixed_om = (units * design["hp_fixed_om_cny_per_unit_window"]
                + capacity * design["resistance_fixed_om_cny_per_kw_window"]
                + design["cooling_fixed_om_cny_per_window"])
    total = capital + design["repeat_window_factor"] * (window_bill + fixed_om)
    return {"capital": capital, "bill": window_bill, "fixed_om": fixed_om,
            "cost": total, "co2": window_co2}


def assert_physics(candidate, frame, protocol):
    assert candidate["success"] is True
    dispatch = candidate["dispatch"]
    required = {"time", "q_resistance_kw", "q_hp_kw", "q_cooling_kw",
                "electricity_kw", "temperature_end_c"}
    assert required <= set(dispatch.columns)
    assert len(dispatch) == len(frame)
    assert pd.to_datetime(dispatch["time"]).tolist() == pd.to_datetime(frame["time"]).tolist()
    columns = sorted(required - {"time"})
    assert np.isfinite(dispatch[columns].to_numpy(dtype=float)).all()
    design, physics = protocol["design"], protocol["physics"]
    q_r = dispatch["q_resistance_kw"].to_numpy(dtype=float)
    q_hp = dispatch["q_hp_kw"].to_numpy(dtype=float)
    q_c = dispatch["q_cooling_kw"].to_numpy(dtype=float)
    assert np.all(q_r >= -1e-7)
    assert np.all(q_hp >= -1e-7)
    assert np.all(q_c >= -1e-7)
    assert np.all(q_r <= candidate["resistance_capacity_kw"] + 1e-7)
    assert np.all(q_hp <= candidate["hp_units"] * frame["capacity_hp_per_unit_kw"].to_numpy() + 1e-7)
    assert np.all(q_c <= design["max_cooling_kw"] + 1e-7)
    electricity = q_r / design["eta_resistance"] + q_hp / frame["cop_hp"].to_numpy() + q_c / design["eer_cooling"]
    np.testing.assert_allclose(dispatch["electricity_kw"], electricity, atol=1e-7, rtol=1e-8)
    assert np.all(electricity <= design["max_grid_kw"] + 1e-7)
    temperatures = dispatch["temperature_end_c"].to_numpy(dtype=float)
    previous = np.r_[physics["initial_c"], temperatures[:-1]]
    h = physics["H_kw_per_K"]
    a = math.exp(-h * physics["dt_hours"] / physics["C_kwh_per_K"])
    expected = a * previous + (1 - a) * (
        frame["outdoor_temp"].to_numpy()
        + (frame["internal_gain_kw"].to_numpy() + frame["solar_gain_kw"].to_numpy() + q_r + q_hp - q_c) / h
    )
    np.testing.assert_allclose(temperatures, expected, atol=1e-7, rtol=1e-8)
    assert np.all(temperatures >= physics["min_c"] - 1e-7)
    assert np.all(temperatures <= physics["max_c"] + 1e-7)
    assert temperatures[-1] == pytest.approx(physics["terminal_c"], abs=1e-7)
    totals = independent_totals(candidate, frame, protocol)
    ledger_fields = {"capital": "capex_cny", "bill": "window_energy_bill_cny",
                     "fixed_om": "window_fixed_om_cny", "cost": "comparison_cost_cny",
                     "co2": "window_co2_kg"}
    for independent_key, reported_key in ledger_fields.items():
        assert candidate["metrics"][reported_key] == pytest.approx(totals[independent_key], rel=1e-8, abs=1e-6)
        assert candidate[reported_key] == pytest.approx(totals[independent_key], rel=1e-8, abs=1e-6)
    assert len(candidate["solver_stages"]) == 2
    for stage in candidate["solver_stages"]:
        assert stage["success"] is True
        assert stage["status"] == 0
        assert abs(stage["primal_dual_gap"]) < 1e-6
        assert stage["constraint_max_violation"] < 1e-6


def assert_rejected(call):
    """Invalid data may raise ValueError or return an explicit failed candidate."""
    try:
        result = call()
    except ValueError:
        return
    assert isinstance(result, dict)
    assert result["success"] is False


def test_constant_temperature_identifies_minimum_resistance_capacity(runner, toy):
    frame, protocol = toy
    candidate = runner.solve_candidate(frame, protocol, hp_units=0)
    assert_physics(candidate, frame, protocol)
    assert candidate["resistance_capacity_kw"] == pytest.approx(2.0, abs=1e-7)
    np.testing.assert_allclose(candidate["dispatch"]["q_resistance_kw"], 2.0, atol=1e-7)
    np.testing.assert_allclose(candidate["dispatch"]["q_hp_kw"], 0.0, atol=1e-7)
    np.testing.assert_allclose(candidate["dispatch"]["q_cooling_kw"], 0.0, atol=1e-7)
    totals = independent_totals(candidate, frame, protocol)
    assert totals["bill"] == pytest.approx(4 / 0.97)
    assert totals["fixed_om"] == pytest.approx(1.2)
    assert totals["cost"] == pytest.approx(9 + 4 / 0.97 + 1.2)
    assert totals["co2"] == pytest.approx(2 / 0.97)


def test_exact_rc_free_decay_is_distinct_from_euler(runner, toy):
    frame, protocol = toy
    protocol["physics"]["min_c"] = 12.0
    protocol["physics"]["max_c"] = 27.0
    protocol["physics"]["terminal_c"] = 20 * math.exp(-0.4)
    candidate = runner.solve_candidate(frame, protocol, hp_units=0, fixed_resistance_kw=0.0)
    assert_physics(candidate, frame, protocol)
    np.testing.assert_allclose(candidate["dispatch"]["temperature_end_c"],
                               20 * np.exp(-0.2 * np.arange(1, 3)), atol=1e-7)
    np.testing.assert_allclose(candidate["dispatch"]["electricity_kw"], 0.0, atol=1e-7)


def test_cooling_balance_and_eer_are_physically_accounted(runner, toy):
    frame, protocol = toy
    frame["outdoor_temp"] = 30.0
    candidate = runner.solve_candidate(frame, protocol, hp_units=0)
    assert_physics(candidate, frame, protocol)
    np.testing.assert_allclose(candidate["dispatch"]["q_cooling_kw"], 1.0, atol=1e-7)
    np.testing.assert_allclose(candidate["dispatch"]["electricity_kw"], 1 / 3, atol=1e-7)
    assert independent_totals(candidate, frame, protocol)["co2"] == pytest.approx(1 / 3, abs=1e-7)


def test_finite_cooling_capacity_cannot_be_bypassed(runner, toy):
    frame, protocol = toy
    frame["outdoor_temp"] = 50.0  # Requires 3kW; the allowed cooler supplies 2kW.
    candidate = runner.solve_candidate(frame, protocol, hp_units=0)
    assert candidate["success"] is False


def test_grid_power_limit_distinguishes_resistance_from_cop_three_hp(runner, toy):
    frame, protocol = toy
    protocol["design"]["max_grid_kw"] = 1.0
    resistance = runner.solve_candidate(frame, protocol, hp_units=0)
    assert resistance["success"] is False  # 2/0.97 kW exceeds 1kW.
    hp = runner.solve_candidate(frame, protocol, hp_units=1)
    assert_physics(hp, frame, protocol)  # 2/3 kW fits the same connection.


@pytest.mark.parametrize("repeat,expected_units", [(1.0, 0), (4.0, 1)])
def test_integer_package_selection_crosses_hand_computed_threshold(runner, toy, repeat, expected_units):
    frame, protocol = toy
    protocol["design"]["repeat_window_factor"] = repeat
    candidate = runner.optimize(frame, protocol)
    assert_physics(candidate, frame, protocol)
    assert candidate["hp_units"] == expected_units
    assert candidate["hp_units"] in [0, 1]
    resistance_cost = 9 + repeat * (4 / 0.97 + 1.2)
    hp_cost = 17 + repeat * (4 / 3 + 1.1)
    assert independent_totals(candidate, frame, protocol)["cost"] == pytest.approx(min(resistance_cost, hp_cost), abs=1e-6)
    assert candidate["resistance_capacity_kw"] == pytest.approx(2.0 if expected_units == 0 else 0.0, abs=1e-7)


def test_fixed_om_changes_integer_package_choice(runner, toy):
    frame, protocol = toy
    # Without the selected resistance asset's fixed OM, resistance is cheaper.
    # With it included once in the repeated-window ledger, one HP is cheaper.
    protocol["design"]["resistance_fixed_om_cny_per_kw_window"] = 3.0
    candidate = runner.optimize(frame, protocol)
    assert_physics(candidate, frame, protocol)
    assert candidate["hp_units"] == 1
    assert candidate["resistance_capacity_kw"] == pytest.approx(0.0, abs=1e-7)
    resistance_cost = 9 + 4 / 0.97 + 6 + 0.8
    hp_cost = 17 + 4 / 3 + 0.3 + 0.8
    assert hp_cost < resistance_cost
    assert independent_totals(candidate, frame, protocol)["cost"] == pytest.approx(hp_cost, abs=1e-6)


def test_carbon_endpoint_selects_hp_despite_higher_cost_at_single_window(runner, toy):
    frame, protocol = toy
    candidate = runner.optimize(frame, protocol, mode="carbon")
    assert_physics(candidate, frame, protocol)
    assert candidate["hp_units"] == 1
    assert independent_totals(candidate, frame, protocol)["co2"] == pytest.approx(2 / 3, abs=2e-7)
    assert independent_totals(candidate, frame, protocol)["cost"] == pytest.approx(17 + 4 / 3 + 1.1, abs=1e-6)


def test_epsilon_is_strict_and_applies_to_one_window_not_repeated_cost_windows(runner, toy):
    frame, protocol = toy
    protocol["design"]["repeat_window_factor"] = 4.0
    minimum_co2 = 2 / 3
    feasible = runner.solve_candidate(frame, protocol, hp_units=1, epsilon=minimum_co2 + 1e-8)
    assert_physics(feasible, frame, protocol)
    assert independent_totals(feasible, frame, protocol)["co2"] <= minimum_co2 + 1e-7
    infeasible = runner.solve_candidate(frame, protocol, hp_units=1, epsilon=minimum_co2 - 1e-3)
    assert infeasible["success"] is False


def test_fixed_investment_cannot_rebuy_capacity_to_hide_shortage(runner, toy):
    frame, protocol = toy
    candidate = runner.optimize(frame, protocol, fixed={"hp_units": 0, "resistance_capacity_kw": 1.9})
    assert candidate["success"] is False


def test_fixed_investment_rejects_fractional_packages_instead_of_truncating(runner, toy):
    frame, protocol = toy
    assert_rejected(lambda: runner.optimize(
        frame, protocol, fixed={"hp_units": 1.5, "resistance_capacity_kw": 0.0}
    ))


def test_secondary_failure_of_cheaper_candidate_blocks_complete_enumeration(runner, toy, monkeypatch):
    frame, protocol = toy
    # Both reference candidates are actually solved before the failure-path
    # injection.  Candidate zero is the cheaper package design for this window.
    reference_zero = runner.solve_candidate(frame, protocol, hp_units=0)
    reference_one = runner.solve_candidate(frame, protocol, hp_units=1)
    assert_physics(reference_zero, frame, protocol)
    assert_physics(reference_one, frame, protocol)
    zero_cost = independent_totals(reference_zero, frame, protocol)["cost"]
    one_cost = independent_totals(reference_one, frame, protocol)["cost"]
    assert zero_cost < one_cost

    real_stage = runner.stage
    actual_calls = []

    def fail_only_candidate_zero_secondary(lp, mode, epsilon):
        # This is failure-handling coverage, not a replacement solver: every
        # stage first executes real SciPy/HiGHS on the actual generated LP.
        result, record = real_stage(lp, mode, epsilon)
        actual_calls.append({"mode": mode, "underlying_success": bool(result.success)})
        if len(actual_calls) == 2:
            assert mode == "carbon"
            assert result.success
            expected_zero_constant = (
                protocol["design"]["cooling_package_capex_cny"]
                + protocol["design"]["repeat_window_factor"]
                * protocol["design"]["cooling_fixed_om_cny_per_window"]
            )
            assert lp[-1]["cost_constant"] == pytest.approx(expected_zero_constant)
            result = deepcopy(result)
            result.success = False
            result.status = 2
            result.x = None
            result.fun = None
            result.message = "Test-only injected failure of candidate zero secondary stage"
            record = dict(record)
            record.update(success=False, status=2, message=result.message,
                          failure_injection="test_only_candidate_zero_secondary",
                          underlying_real_solver_status=0)
            for field in ("primal_objective", "dual_objective", "primal_dual_gap",
                          "constraint_max_violation", "objective_constant"):
                record.pop(field, None)
        return result, record

    monkeypatch.setattr(runner, "stage", fail_only_candidate_zero_secondary)
    result = runner.optimize(frame, protocol, mode="cost")
    assert result["success"] is False
    assert result["integer_enumeration_complete"] is False
    assert len(actual_calls) >= 2
    assert all(call["underlying_success"] for call in actual_calls)

    zero_records = [candidate for candidate in result["enumeration"]
                    if candidate["hp_units"] == 0]
    assert len(zero_records) == 1
    zero = zero_records[0]
    assert zero["success"] is False
    assert zero["mode"] == "cost"
    assert zero["solver_stages"][0]["success"] is True
    assert zero["solver_stages"][0]["status"] == 0
    assert zero["solver_stages"][1]["success"] is False
    assert zero["solver_stages"][1]["status"] == 2
    assert zero["primary_lower_bound"] == pytest.approx(zero_cost, rel=1e-8, abs=1e-6)
    primary = zero["primary_metrics"]
    assert primary["hp_units"] == 0
    assert primary["resistance_capacity_kw"] == pytest.approx(2.0, abs=1e-7)
    assert primary["comparison_cost_cny"] == pytest.approx(zero_cost, rel=1e-8, abs=1e-6)
    assert primary["window_co2_kg"] == pytest.approx(2 / 0.97, abs=1e-7)


def test_fixed_replay_retains_unused_capacity_and_its_cost(runner, toy):
    frame, protocol = toy
    candidate = runner.optimize(frame, protocol, fixed={"hp_units": 0, "resistance_capacity_kw": 3.0})
    assert_physics(candidate, frame, protocol)
    assert candidate["hp_units"] == 0
    assert candidate["resistance_capacity_kw"] == pytest.approx(3.0, abs=1e-7)
    expected_cost = 10 + 4 / 0.97 + 1.4
    assert independent_totals(candidate, frame, protocol)["cost"] == pytest.approx(expected_cost, abs=1e-6)


def test_capacity_derating_is_bound_to_integer_units_and_fixed_backup(runner, toy):
    frame, protocol = toy
    frame["capacity_hp_per_unit_kw"] = 1.5
    feasible = runner.solve_candidate(frame, protocol, hp_units=1, fixed_resistance_kw=0.5)
    assert_physics(feasible, frame, protocol)
    np.testing.assert_allclose(feasible["dispatch"]["q_hp_kw"], 1.5, atol=1e-7)
    np.testing.assert_allclose(feasible["dispatch"]["q_resistance_kw"], 0.5, atol=1e-7)
    infeasible = runner.solve_candidate(frame, protocol, hp_units=1, fixed_resistance_kw=0.4)
    assert infeasible["success"] is False


@pytest.mark.parametrize("column,value", [
    ("electricity_price", -1.0),
    ("electricity_price", float("nan")),
    ("grid_emission_factor", float("nan")),
    ("outdoor_temp", float("nan")),
    ("cop_hp", float("nan")),
    ("cop_hp", 0.0),
    ("capacity_hp_per_unit_kw", -1.0),
])
def test_invalid_frame_is_rejected(runner, toy, column, value):
    frame, protocol = toy
    frame.loc[0, column] = value
    assert_rejected(lambda: runner.solve_candidate(frame, protocol, hp_units=0))


@pytest.mark.parametrize("section,key,value", [
    ("physics", "H_kw_per_K", float("nan")),
    ("physics", "H_kw_per_K", 0.0),
    ("physics", "C_kwh_per_K", float("nan")),
    ("physics", "C_kwh_per_K", 0.0),
    ("physics", "dt_hours", float("nan")),
    ("design", "eta_resistance", float("nan")),
    ("design", "eta_resistance", 0.0),
    ("design", "eer_cooling", float("nan")),
    ("design", "hp_package_capex_cny", float("nan")),
    ("design", "resistance_capex_cny_per_kw", -1.0),
    ("design", "max_grid_kw", float("nan")),
    ("design", "repeat_window_factor", -1.0),
])
def test_invalid_design_is_rejected(runner, toy, section, key, value):
    frame, protocol = toy
    protocol[section][key] = value
    assert_rejected(lambda: runner.solve_candidate(frame, protocol, hp_units=0))


@pytest.mark.parametrize("change", ["duplicate", "gapped", "reordered", "naive"])
def test_invalid_clock_cannot_be_used_as_adjacent_rc_intervals(runner, toy, change):
    frame, protocol = toy
    if change == "duplicate":
        frame.loc[1, "time"] = frame.loc[0, "time"]
    elif change == "gapped":
        frame.loc[1, "time"] = "2024-01-01T02:00:00+08:00"
    elif change == "reordered":
        frame["time"] = frame["time"].iloc[::-1].to_numpy()
    else:
        frame["time"] = ["2024-01-01T00:00:00", "2024-01-01T01:00:00"]
    assert_rejected(lambda: runner.solve_candidate(frame, protocol, hp_units=0))


def test_inputs_remain_unchanged_by_optimization(runner, toy):
    frame, protocol = toy
    original_frame = frame.copy(deep=True)
    original_protocol = deepcopy(protocol)
    candidate = runner.optimize(frame, protocol)
    assert candidate["success"] is True
    pd.testing.assert_frame_equal(frame, original_frame)
    assert protocol == original_protocol
