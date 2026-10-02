"""Small physical and accounting regressions for cyclic states and net costs."""
from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from heatplan.io import validate_inputs
from heatplan.model import HeatPlanningModel
from heatplan.risk_scan_cli import empirical_weighted_cvar


def _config() -> dict:
    return {
        "planning": {
            "discount_rate": 0.05,
            "asset_lifetime_years": 20,
            "time_step_hours": 1.0,
            "representative_day_weight": 1.0,
            "max_unserved_rate": 0.0,
            "max_scenario_unserved_rate": 0.0,
        },
        "grades": [{"name": "low", "temperature_c": 45.0}],
        "nodes": [{"name": "n", "demand_columns": {"low": "demand"}}],
        "sources": [{
            "name": "source", "node": "n", "type": "boiler",
            "allowed_grades": ["low"], "supply_temp_c": 60.0,
            "existing_capacity_kw": 2.0, "max_new_capacity_kw": 0.0,
            "variable_cost_yuan_per_kwh_heat": 1.0,
            "emission_kg_per_kwh_heat": 0.0,
        }],
        "pipes": [],
        "storages": [],
    }


def _frame(demands: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "time": range(len(demands)), "demand": demands,
        "electricity_price": 1.0, "grid_emission_factor": 1.0,
    })


def _solve(config: dict, frame: pd.DataFrame):
    validate_inputs(config, frame)
    model = HeatPlanningModel(config, frame)
    artifact = model.solve(mip_gap=0.0, time_limit=5.0)
    assert artifact.summary["solver_status"] == 0
    return model, artifact


def _value(model, artifact, category, key):
    return float(artifact.values[model.variables[category][key]])


@pytest.mark.parametrize("costs,probabilities,alpha,expected_cvar", [
    ([-3.8, -7.6], [0.5, 0.5], 0.5, -3.8),
    ([3.0, 9.0], [0.5, 0.5], 0.5, 9.0),
    ([-4.0, 8.0], [0.75, 0.25], 0.5, 2.0),
    ([-4.0, 8.0], [0.75, 0.25], 0.8, 8.0),
])
def test_net_cost_cvar_matches_tail_cost_and_is_translation_invariant(
    costs, probabilities, alpha, expected_cvar
):
    for offset in (0.0, -20.0):
        config = _config()
        config["planning"]["risk"] = {"cvar_alpha": alpha, "cvar_weight": 0.2}
        config["sources"][0].update({
            "type": "chp", "variable_cost_yuan_per_kwh_heat": 20.0,
            "electricity_to_heat_ratio_min": 1.0,
            "electricity_to_heat_ratio_max": 1.0,
        })
        shifted_costs = [cost + offset for cost in costs]
        frame = _frame([1.0, 1.0])
        frame["scenario"] = ["a", "b"]
        frame["scenario_probability"] = probabilities
        frame["electricity_price"] = [20.0 - cost for cost in shifted_costs]
        model, artifact = _solve(config, frame)
        realized = [
            artifact.summary["scenario_results"][scenario]["operating_risk_cost_yuan"]
            for scenario in ("a", "b")
        ]
        assert realized == pytest.approx(shifted_costs)
        premium = artifact.summary["components_yuan_per_year"]["cvar_risk_premium"]
        empirical = empirical_weighted_cvar(zip(realized, probabilities), alpha)
        assert premium / 0.2 == pytest.approx(expected_cvar + offset)
        assert premium / 0.2 == pytest.approx(empirical["cvar_operating_risk_cost_yuan"])
        assert artifact.summary["objective_yuan_per_year"] == pytest.approx(
            sum(p * c for p, c in zip(probabilities, shifted_costs)) + premium
        )
        assert model.problem.lower[model.variables["scenario_cost_var"]["system"]] == -np.inf


@pytest.mark.parametrize("groups", [["a"], ["a", "a"], ["a", "b"], ["a", "a", "b"]])
@pytest.mark.parametrize("loss", [0.0, 0.1])
def test_cyclic_storage_cannot_create_heat_in_single_or_multiple_row_groups(groups, loss):
    config = _config()
    config["storages"] = [{
        "name": "store", "node": "n", "grade": "low",
        "existing_capacity_kwh": 1.0, "max_new_capacity_kwh": 0.0,
        "power_to_energy_ratio_per_hour": 1.0,
        "charge_efficiency": 0.9, "discharge_efficiency": 0.8,
        "self_loss_per_hour": loss, "boundary_mode": "cyclic",
        "strict_charge_discharge_mode": True,
    }]
    frame = _frame([1.0] * len(groups))
    frame["boundary_group"] = groups
    model, artifact = _solve(config, frame)
    assert artifact.summary["objective_yuan_per_year"] == pytest.approx(len(groups))
    for t in model.times:
        state = _value(model, artifact, "storage_soc", ("store", t))
        previous = _value(model, artifact, "storage_soc", ("store", model.time_predecessor[t]))
        charge = _value(model, artifact, "storage_charge", ("store", t))
        discharge = _value(model, artifact, "storage_discharge", ("store", t))
        assert state - (1.0 - loss) * previous - 0.9 * charge + discharge / 0.8 == pytest.approx(0.0)
        assert _value(model, artifact, "source_heat", ("source", "low", t)) == pytest.approx(1.0)
        assert charge * discharge == pytest.approx(0.0)


@pytest.mark.parametrize("rows", [1, 2])
def test_fixed_storage_boundary_preserves_explicit_initial_energy(rows):
    config = _config()
    config["planning"]["time_step_hours"] = 0.5
    config["storages"] = [{
        "name": "store", "node": "n", "grade": "low",
        "existing_capacity_kwh": 1.0, "max_new_capacity_kwh": 0.0,
        "power_to_energy_ratio_per_hour": 1.0,
        "charge_efficiency": 0.9, "discharge_efficiency": 0.8,
        "self_loss_per_hour": 0.1, "boundary_mode": "fixed",
        "initial_soc_fraction": 1.0, "final_soc_min_fraction": 0.25,
    }]
    model, artifact = _solve(config, _frame([1.0] * rows))
    previous = 1.0
    for t in model.times:
        state = _value(model, artifact, "storage_soc", ("store", t))
        charge = _value(model, artifact, "storage_charge", ("store", t))
        discharge = _value(model, artifact, "storage_discharge", ("store", t))
        assert state == pytest.approx(0.9 ** 0.5 * previous + 0.9 * charge * 0.5 - discharge * 0.5 / 0.8)
        previous = state
    assert previous >= 0.25 - 1e-8
    assert artifact.summary["objective_yuan_per_year"] < rows * 0.5


@pytest.mark.parametrize("groups", [["a"], ["a", "a"], ["a", "b"]])
@pytest.mark.parametrize("retention", [1.0, 0.9])
def test_cyclic_biomass_inventory_requires_real_fuel_deliveries(groups, retention):
    config = _config()
    config["sources"][0]["variable_cost_yuan_per_kwh_heat"] = 0.0
    config["biomass_supply_chains"] = [{
        "name": "fuel", "source_names": ["source"],
        "lower_heating_value_kwh_per_kg": 1.0, "conversion_efficiency": 1.0,
        "existing_inventory_capacity_kg": 10.0,
        "delivery_capacity_kg_per_hour": 2.0,
        "procurement_cost_yuan_per_kg": 2.0,
        "inventory_retention_per_hour": retention,
    }]
    frame = _frame([1.0] * len(groups))
    frame["boundary_group"] = groups
    model, artifact = _solve(config, frame)
    assert artifact.summary["objective_yuan_per_year"] == pytest.approx(2.0 * len(groups))
    for t in model.times:
        state = _value(model, artifact, "biomass_inventory", ("fuel", t))
        previous = _value(model, artifact, "biomass_inventory", ("fuel", model.time_predecessor[t]))
        delivery = _value(model, artifact, "biomass_delivery", ("fuel", t))
        heat = _value(model, artifact, "source_heat", ("source", "low", t))
        assert state - retention * previous - delivery + heat == pytest.approx(0.0)


@pytest.mark.parametrize("groups", [["a", "b"], ["a", "a"]])
@pytest.mark.parametrize("loss", [0.0, 0.1])
def test_building_cycle_preserves_imposed_thermal_offsets(groups, loss):
    config = _config()
    config["demand_response"] = {"max_shift_fraction": 0.5, "response_window_hours": 2}
    config["nodes"][0]["thermal_inertia"] = {"low": {
        "thermal_capacity_kwh_per_c": 1.0,
        "max_temperature_deviation_c": 1.0,
        "recovery_loss_per_hour": loss,
    }}
    frame = _frame([1.0, 1.0])
    frame["boundary_group"] = groups
    validate_inputs(config, frame)
    model = HeatPlanningModel(config, frame)
    model.build()
    # These offsets have feasible heat shifts and expose an erroneous state
    # coefficient even when a zero-state optimum would hide the error.
    for t, offset in enumerate([0.25, -0.25]):
        state = model.variables["building_thermal_state"][("n", "low", t)]
        model.problem.add_constraint({state: 1.0}, lb=offset, ub=offset)
    artifact = model.solve(mip_gap=0.0, time_limit=5.0)
    assert artifact.summary["solver_status"] == 0
    for t in model.times:
        state = _value(model, artifact, "building_thermal_state", ("n", "low", t))
        previous = _value(model, artifact, "building_thermal_state", ("n", "low", model.time_predecessor[t]))
        up = _value(model, artifact, "demand_up", ("n", "low", t))
        down = _value(model, artifact, "demand_down", ("n", "low", t))
        assert state - (1.0 - loss) * previous - up + down == pytest.approx(0.0)


@pytest.mark.parametrize("demands,groups,expected_starts,expected_stops", [
    ([0.0], ["a"], [0.0], [0.0]),
    ([1.0], ["a"], [0.0], [0.0]),
    ([1.0, 0.0], ["a", "b"], [0.0, 0.0], [0.0, 0.0]),
    ([0.0, 1.0], ["a", "a"], [0.0, 1.0], [1.0, 0.0]),
])
def test_unit_commitment_cycles_count_only_real_state_transitions(
    demands, groups, expected_starts, expected_stops
):
    config = _config()
    config["sources"][0].update({
        "unit_commitment": True, "minimum_output_fraction": 0.2,
        "startup_cost_yuan": 1.0, "shutdown_cost_yuan": 1.0,
    })
    frame = _frame(demands)
    frame["boundary_group"] = groups
    model, artifact = _solve(config, frame)
    for t, demand in enumerate(demands):
        assert _value(model, artifact, "source_on", ("source", t)) == pytest.approx(float(demand > 0))
        assert _value(model, artifact, "source_startup", ("source", t)) == pytest.approx(expected_starts[t])
        assert _value(model, artifact, "source_shutdown", ("source", t)) == pytest.approx(expected_stops[t])
    assert artifact.summary["objective_yuan_per_year"] == pytest.approx(
        sum(demands) + sum(expected_starts) + sum(expected_stops)
    )
    assert artifact.summary["unit_commitment_max_transition_residual"] <= 1e-8


def _green_inputs(external: bool, microgrid: bool):
    config = _config()
    config["sources"][0].update({"type": "electric_boiler", "efficiency": 1.0})
    if external:
        config["renewable_electricity"] = {
            "availability_column": "external_green", "price_yuan_per_kwh": 0.0,
            "emission_kg_per_kwh": 0.0,
        }
    if microgrid:
        config["microgrid_islanding"] = {
            "existing_renewable_capacity_kw": 1.0,
            "renewable_firm_output_factor": 1.0,
            "operational_renewable_output_factor": 1.0,
            "renewable_variable_cost_yuan_per_kwh": 0.0,
            "renewable_emission_kg_per_kwh": 0.0,
        }
    frame = _frame([1.0, 1.0])
    frame["external_green"] = 1.0
    return config, frame


def test_overlapping_green_modules_are_rejected_by_validation_and_direct_constructor():
    config, frame = _green_inputs(True, True)
    for entry_point in (validate_inputs, HeatPlanningModel):
        with pytest.raises(ValueError, match="renewable_electricity.*microgrid_islanding"):
            entry_point(config, frame)


@pytest.mark.parametrize("external,microgrid,expected_cost", [
    (False, False, 2.0), (True, False, 0.0), (False, True, 0.0),
])
def test_supported_green_modes_keep_cost_and_emissions_consistent(external, microgrid, expected_cost):
    config, frame = _green_inputs(external, microgrid)
    _, artifact = _solve(config, frame)
    assert artifact.summary["objective_yuan_per_year"] == pytest.approx(expected_cost)
    assert artifact.summary["emissions_kg_per_year"] == pytest.approx(expected_cost)
    assert artifact.summary["grid_electricity_kwh_per_year"] == pytest.approx(expected_cost)
    # Empty mappings are disabled by the model and must remain valid.
    disabled = deepcopy(config)
    disabled.setdefault("renewable_electricity", {})
    disabled.setdefault("microgrid_islanding", {})
    validate_inputs(disabled, frame)
    HeatPlanningModel(disabled, frame)


@pytest.mark.parametrize("objective_mode", ["total_cost", "emissions"])
def test_summary_contains_audit_of_the_actual_solver_objective(objective_mode):
    config = _config()
    config["sources"][0]["emission_kg_per_kwh_heat"] = 3.0
    model = HeatPlanningModel(config, _frame([1.0]))
    artifact = model.solve(mip_gap=0.0, time_limit=5.0, objective_mode=objective_mode)
    summary = artifact.summary
    for field in (
        "numerical_constraint_max_violation", "numerical_bound_max_violation",
        "numerical_integrality_max_violation", "numerical_objective_recompute_error",
    ):
        assert summary[field] <= 1e-8
    assert summary["numerical_constraint_max_scaled_violation"] <= 1.0
    assert summary["numerical_audit_passed"] is True
    assert summary["numerical_audit_tolerance"]
    assert summary["matrix_compile_cache_hit"] is False
