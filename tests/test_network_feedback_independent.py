"""Independent physical, optimization and certificate counterexamples.

Every fixture is an analytical mechanism case, not measured city/network data.
The reference physics deliberately does not call the production mode calculator.
"""
from copy import deepcopy
from itertools import product
import math

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import brentq

import heatplan.network_feedback as network


def case():
    branch = {
        "name": "customer_a", "from": "plant", "to": "a",
        "supply_temperature_c": 80., "ground_temperature_c": 10.,
        "one_way_length_m": 600., "inner_diameter_m": .1,
        "roughness_m": .0001, "heat_loss_w_per_mk_per_leg": .25,
        "pump_efficiency": .72, "pair_local_loss_coefficient": 5.,
        "maximum_velocity_m_per_s": 3.,
        "maximum_pressure_drop_pa_per_m": 300.,
        "maximum_pair_pressure_drop_pa": 1e6,
        "maximum_pump_power_kw": 10.,
        "maximum_instantaneous_heat_input_kw": 500.,
        "maximum_customer_return_c": 60., "maximum_plant_return_c": 60.,
        "minimum_far_supply_c": 45., "target_customer_delta_t_c": 25.,
        "delta_t_soft_band_c": 2., "delta_t_penalty_yuan_per_c_on_hour": 2.,
        "operating_modes": [
            {"name": "low", "mass_flow_kg_per_s": .6, "customer_return_temperature_c": 45.},
            {"name": "medium", "mass_flow_kg_per_s": 1.1, "customer_return_temperature_c": 45.},
            {"name": "high", "mass_flow_kg_per_s": 1.8, "customer_return_temperature_c": 45.},
        ],
    }
    config = {
        "planning": {"discount_rate": .05, "asset_lifetime_years": 20,
            "time_step_hours": 1., "representative_day_weight": 1.,
            "max_unserved_rate": 0., "max_scenario_unserved_rate": 0.,
            "firm_capacity_reserve_margin": 0., "carbon_price_yuan_per_kg": 0.},
        "grades": [{"name": "service", "temperature_c": 45.}],
        "nodes": [{"name": "plant", "demand_columns": {}},
                  {"name": "a", "demand_columns": {"service": "demand_a"}}],
        "sources": [{"name": "heater", "node": "plant", "type": "electric_boiler",
            "allowed_grades": ["service"], "supply_temp_c": 80.,
            "existing_capacity_kw": 500., "max_new_capacity_kw": 0.,
            "efficiency": .96, "variable_om_yuan_per_kwh_heat": .003}],
        "pipes": [], "storages": [],
        "network_feedback": {"mode": "independent_star_steady_duty",
            "independent_branch_pumps": True,
            "steady_on_state_duty_approximation_acknowledged": True,
            "maximum_simultaneous_electricity_kw": 1000.,
            "water": {"density_kg_per_m3": 985., "cp_kj_per_kgk": 4.18,
                      "viscosity_pa_s": .00055}, "branches": [branch]},
    }
    frame = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=3, freq="h", tz="Asia/Shanghai").astype(str),
        "demand_a": [25., 50., 75.], "electricity_price": [.3, 1.2, .6],
        "grid_emission_factor": [.5, .5, .5]})
    return config, frame


def reference_mode(branch, mode, water):
    m, cp, rho = mode["mass_flow_kg_per_s"], water["cp_kj_per_kgk"], water["density_kg_per_m3"]
    ts, tr, tg = branch["supply_temperature_c"], mode["customer_return_temperature_c"], branch["ground_temperature_c"]
    d, length = branch["inner_diameter_m"], branch["one_way_length_m"]
    exponent = branch["heat_loss_w_per_mk_per_leg"] * length / (1000 * m * cp)
    far = tg + (ts - tg) * math.exp(-exponent)
    returned = tg + (tr - tg) * math.exp(-exponent)
    velocity = 4 * m / (rho * math.pi * d * d)
    re = 4 * m / (math.pi * d * water["viscosity_pa_s"])
    if re < 2300:
        friction = 64 / re
    else:
        assert re >= 4000
        residual = lambda f: 1 / math.sqrt(f) + 2 * math.log10(
            branch["roughness_m"] / (3.7 * d) + 2.51 / (re * math.sqrt(f)))
        friction = brentq(residual, .005, .2, xtol=1e-14)
    dp_per_m = friction * rho * velocity * velocity / (2 * d)
    pair_dp = 2 * length * dp_per_m + branch["pair_local_loss_coefficient"] * rho * velocity * velocity / 2
    pump = pair_dp * (m / rho) / (1000 * branch["pump_efficiency"])
    return {"source": m * cp * (ts - returned), "delivered": m * cp * (far - tr),
            "far": far, "return": returned, "pump": pump, "dp": dp_per_m,
            "pair_dp": pair_dp, "velocity": velocity, "friction": friction, "re": re,
            "loss_supply": m * cp * (ts - far), "loss_return": m * cp * (tr - returned)}


def test_two_leg_conservation_and_colebrook_match_independent_root():
    config, _ = case()
    spec = config["network_feedback"]
    branch, water = spec["branches"][0], spec["water"]
    for mode in branch["operating_modes"]:
        expected = reference_mode(branch, mode, water)
        actual = network.mode_physics(branch, mode, water)
        assert actual["source_heat_kw"] == pytest.approx(expected["source"], rel=1e-12)
        assert actual["delivered_heat_kw"] == pytest.approx(expected["delivered"], rel=1e-12)
        assert actual["heat_loss_kw"] == pytest.approx(expected["loss_supply"] + expected["loss_return"], rel=1e-12)
        assert actual["pump_kw"] == pytest.approx(expected["pump"], rel=1e-10)
        assert actual["darcy_friction_factor"] == pytest.approx(expected["friction"], rel=1e-10)


def test_zero_ua_laminar_limit_has_exact_unity_efficiency():
    config, _ = case()
    spec = config["network_feedback"]
    branch = deepcopy(spec["branches"][0])
    branch["heat_loss_w_per_mk_per_leg"] = 0.
    mode = {"mass_flow_kg_per_s": .01, "customer_return_temperature_c": 45.}
    actual = network.mode_physics(branch, mode, spec["water"])
    assert actual["reynolds"] < 2300
    assert actual["delivery_efficiency"] == 1.
    assert actual["heat_loss_kw"] == 0.
    assert actual["far_supply_c"] == 80.
    assert actual["plant_return_c"] == 45.
    assert actual["darcy_friction_factor"] == pytest.approx(64 / actual["reynolds"])


def test_feedback_matches_exhaustive_finite_mode_objective():
    config, frame = case()
    spec = config["network_feedback"]
    a = spec["branches"][0]
    b = deepcopy(a)
    b.update(name="customer_b", to="b", one_way_length_m=950., target_customer_delta_t_c=30.)
    spec["branches"].append(b)
    config["nodes"].append({"name": "b", "demand_columns": {"service": "demand_b"}})
    config["sources"][0]["existing_capacity_kw"] = 400.
    frame["demand_b"] = [30., 35., 40.]
    records = [[reference_mode(branch, mode, spec["water"]) for mode in branch["operating_modes"]]
               for branch in (a, b)]
    expected_total, expected_choices = 0., []
    for hour, row in frame.iterrows():
        candidates = []
        for indices in product(range(3), repeat=2):
            selected = [records[k][index] for k, index in enumerate(indices)]
            duties = [row[column] / rec["delivered"]
                      for column, rec in zip(("demand_a", "demand_b"), selected)]
            if max(duties) > 1 or sum(rec["source"] for rec in selected) > 400.:
                continue
            instantaneous_electric = sum(rec["source"] / .96 + rec["pump"] for rec in selected)
            if instantaneous_electric > spec["maximum_simultaneous_electricity_kw"]:
                continue
            energy_cost = sum(duty * (rec["source"] * .003 + (rec["source"] / .96 + rec["pump"]) * row.electricity_price)
                              for duty, rec in zip(duties, selected))
            soft_cost = sum(duty * branch["delta_t_penalty_yuan_per_c_on_hour"] * max(
                abs(rec["far"] - mode["customer_return_temperature_c"] - branch["target_customer_delta_t_c"])
                - branch["delta_t_soft_band_c"], 0.)
                for duty, rec, branch, index in zip(duties, selected, (a, b), indices)
                for mode in [branch["operating_modes"][index]])
            candidates.append((energy_cost + soft_cost, indices))
        assert candidates
        best = min(candidates)
        expected_total += best[0]
        expected_choices.append(best[1])
    result = network.solve_with_network_feedback(config, frame, time_limit=10.)
    assert result.audit["passed"]
    assert result.solution.summary["solver_status"] == 0
    assert result.solution.summary["objective_yuan_per_year"] == pytest.approx(expected_total, rel=1e-9)
    assert result.audit["soft_penalty_yuan"] > 0
    assert result.audit["field_validation"] is False
    assert result.audit["rc_comfort_certified"] is False


def test_low_flow_temperature_failure_is_cut_without_forbidding_valid_modes():
    config, frame = case()
    branch = config["network_feedback"]["branches"][0]
    branch["delta_t_penalty_yuan_per_c_on_hour"] = 0.
    branch["minimum_far_supply_c"] = 75.
    branch["operating_modes"] = [
        {"name": "cheap_invalid", "mass_flow_kg_per_s": .2, "customer_return_temperature_c": 45.},
        {"name": "valid", "mass_flow_kg_per_s": 1.1, "customer_return_temperature_c": 45.},
    ]
    frame = frame.iloc[:1].copy()
    frame.demand_a = 5.
    frame.electricity_price = 0.
    config["sources"][0]["variable_om_yuan_per_kwh_heat"] = 0.
    # Prefer the low-flow candidate through its declared delta-T soft target.
    branch["target_customer_delta_t_c"] = reference_mode(branch, branch["operating_modes"][0], config["network_feedback"]["water"])["far"] - 45.
    branch["delta_t_soft_band_c"] = 0.
    branch["delta_t_penalty_yuan_per_c_on_hour"] = 10.
    result = network.solve_with_network_feedback(config, frame, time_limit=10.)
    assert len(result.history) == 2
    assert result.history[0]["added_mode_cuts"] == ["feedback_mode_0"]
    assert result.audit["passed"]
    active = result.mode_dispatch[result.mode_dispatch.duty_fraction > 1e-6]
    assert active["mode"].tolist() == ["valid"]
    assert active.instantaneous_far_supply_c.min() >= 75.


def test_low_duty_cannot_hide_instantaneous_hydraulic_overload():
    config, frame = case()
    branch = config["network_feedback"]["branches"][0]
    branch["operating_modes"] = [branch["operating_modes"][-1]]
    branch["maximum_velocity_m_per_s"] = .05
    frame = frame.iloc[:1].copy()
    frame.demand_a = .1
    with pytest.raises(network.FeedbackFailure) as caught:
        network.solve_with_network_feedback(config, frame, time_limit=10.)
    assert caught.value.history[0]["added_mode_cuts"] == ["feedback_mode_0"]
    assert caught.value.history[-1]["status"] == "failed"


def test_average_feasible_two_branch_pulses_fail_simultaneous_source_limit():
    config, frame = case()
    spec = config["network_feedback"]
    branch = spec["branches"][0]
    branch["heat_loss_w_per_mk_per_leg"] = 0.
    branch["operating_modes"] = [branch["operating_modes"][0]]
    rec = reference_mode(branch, branch["operating_modes"][0], spec["water"])
    b = deepcopy(branch)
    b.update(name="customer_b", to="b")
    spec["branches"].append(b)
    config["nodes"].append({"name": "b", "demand_columns": {"service": "demand_b"}})
    config["sources"][0]["existing_capacity_kw"] = rec["source"] / .6
    frame = frame.iloc[:1].copy()
    frame["demand_a"] = frame["demand_b"] = .8 * rec["delivered"]
    assert 1.6 * rec["source"] < config["sources"][0]["existing_capacity_kw"]
    with pytest.raises(network.FeedbackFailure):
        network.solve_with_network_feedback(config, frame, time_limit=10.)


@pytest.mark.parametrize("container,key,value", [
    ("root", "microgrid_islanding", {"existing_backup_capacity_kw": 1.}),
    ("root", "renewable_electricity", {"availability_column": "sun"}),
    ("root", "shared_heat_resources", [{"name": "pool"}]),
    ("root", "biomass_supply_chains", [{"name": "fuel"}]),
    ("root", "fixed_investment_accounting", {"scope": "annual_selected_plan_charges"}),
    ("root", "grid_connection", {"existing_capacity_kw": 1000., "max_new_capacity_kw": 100.}),
    ("planning", "risk", {"cvar_alpha": .95, "cvar_weight": .1}),
    ("source", "unit_commitment", True),
    ("source", "availability", .5),
    ("node", "heat_metering", {"saving_fraction_at_full_coverage": .1}),
    ("node", "thermal_inertia", {"service": {"thermal_capacity_kwh_per_c": 10.}}),
    ("node", "intermittent_heating", {"schedule_column": "schedule"}),
    ("node", "user_behavior", {"rebound_fraction": .1}),
])
def test_unsupported_core_extensions_are_rejected_before_build(monkeypatch, container, key, value):
    config, frame = case()
    target = {"root": config, "planning": config["planning"],
              "source": config["sources"][0], "node": config["nodes"][1]}[container]
    target[key] = value
    def unexpected_core(*args, **kwargs):
        pytest.fail(f"unsupported {container}.{key} reached the frozen core")
    monkeypatch.setattr(network, "HeatPlanningModel", unexpected_core)
    with pytest.raises(ValueError):
        network.build_feedback_model(config, frame)


@pytest.mark.parametrize("times", [
    ["2024-01-01 00:00+08:00", "2024-01-01 00:00+08:00", "2024-01-01 02:00+08:00"],
    ["2024-01-01 01:00+08:00", "2024-01-01 00:00+08:00", "2024-01-01 02:00+08:00"],
    ["2024-01-01 00:00+08:00", "2024-01-01 02:00+08:00", "2024-01-01 03:00+08:00"],
])
def test_continuous_reference_rejects_duplicate_reverse_and_missing_hour(times):
    config, frame = case()
    frame.time = times
    with pytest.raises(ValueError):
        network.build_feedback_model(config, frame)


@pytest.mark.parametrize("container,key", [
    ("grade", "temperature_c"), ("planning", "time_step_hours"),
    ("source", "efficiency"), ("branch", "minimum_far_supply_c"),
    ("branch", "maximum_velocity_m_per_s"), ("water", "cp_kj_per_kgk"),
])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_contract_parameters_rejected(container, key, bad):
    config, frame = case()
    target = {"grade": config["grades"][0], "planning": config["planning"],
        "source": config["sources"][0], "branch": config["network_feedback"]["branches"][0],
        "water": config["network_feedback"]["water"]}[container]
    target[key] = bad
    with pytest.raises(ValueError):
        network.build_feedback_model(config, frame)


def test_saved_false_certificate_cannot_hide_altered_full_solution():
    config, frame = case()
    result = network.solve_with_network_feedback(config, frame, time_limit=10.)
    tampered = deepcopy(result.solution)
    source_index = result.model.variables["source_heat"]["heater", "service", 0]
    tampered.values[source_index] += 1.
    assert tampered.summary["numerical_audit_passed"] is True
    _, audit = network.audit_feedback_solution(config, result.model, tampered, result.catalog)
    assert audit["passed"] is False


def test_independent_audit_recomputes_instantaneous_limits_from_original_contract():
    config, frame = case()
    result = network.solve_with_network_feedback(config, frame, time_limit=10.)
    altered_contract = deepcopy(config)
    altered_contract["network_feedback"]["maximum_simultaneous_electricity_kw"] = .01
    _, audit = network.audit_feedback_solution(altered_contract, result.model, result.solution, result.catalog)
    assert audit["passed"] is False


def test_independent_audit_recomputes_soft_penalty_without_trusting_catalog():
    config, frame = case()
    result = network.solve_with_network_feedback(config, frame, time_limit=10.)
    forged_catalog = deepcopy(result.catalog)
    for entry in forged_catalog:
        entry["delta_t_excess_c"] = 0.
        entry["soft_penalty_yuan_per_on_hour"] = 0.
    assert result.audit["soft_penalty_yuan"] > 0
    _, audit = network.audit_feedback_solution(config, result.model, result.solution, forged_catalog)
    assert audit["passed"] is False or audit["soft_penalty_yuan"] == pytest.approx(result.audit["soft_penalty_yuan"])


@pytest.mark.parametrize("mutation", ["duplicate_zero", "drop_zero", "grade", "time", "scenario", "unit", "node"])
def test_export_identity_tampering_cannot_pass_certificate(mutation):
    config, frame = case()
    result = network.solve_with_network_feedback(config, frame, time_limit=10.)
    tampered = deepcopy(result.solution)
    pipe_rows = tampered.dispatch.category == "pipe_flow_forward"
    zero_index = tampered.dispatch.index[pipe_rows & (tampered.dispatch.value == 0)][0]
    if mutation == "duplicate_zero":
        tampered.dispatch = pd.concat([tampered.dispatch, tampered.dispatch.loc[[zero_index]]], ignore_index=True)
    elif mutation == "drop_zero":
        tampered.dispatch = tampered.dispatch.drop(index=zero_index)
    else:
        bad = {"grade": "unknown_grade", "time": "1900-01-01 00:00", "scenario": "forged",
               "unit": "Wh", "node": "unknown_node"}[mutation]
        tampered.dispatch.loc[zero_index, mutation] = bad
    _, audit = network.audit_feedback_solution(config, result.model, tampered, result.catalog)
    assert audit["passed"] is False


def test_original_source_capacity_change_invalidates_instantaneous_certificate():
    config, frame = case()
    result = network.solve_with_network_feedback(config, frame, time_limit=10.)
    altered_contract = deepcopy(config)
    altered_contract["sources"][0]["existing_capacity_kw"] = .01
    _, audit = network.audit_feedback_solution(altered_contract, result.model, result.solution, result.catalog)
    assert audit["passed"] is False


@pytest.mark.parametrize("bad", [0., -1., float("nan"), float("inf")])
def test_invalid_audit_tolerance_rejected(bad):
    config, frame = case()
    result = network.solve_with_network_feedback(config, frame, time_limit=10.)
    with pytest.raises(ValueError):
        network.audit_feedback_solution(config, result.model, result.solution, result.catalog, tolerance=bad)


def test_catalog_cannot_rebind_invalid_branch_to_identical_branch_with_lax_limits():
    config, frame = case()
    spec = config["network_feedback"]
    a = spec["branches"][0]
    a["operating_modes"] = [a["operating_modes"][1]]
    b = deepcopy(a)
    b.update(name="customer_b", to="b")
    a["maximum_velocity_m_per_s"] = .01
    spec["branches"].append(b)
    config["nodes"].append({"name": "b", "demand_columns": {"service": "demand_b"}})
    frame = frame.iloc[:1].copy()
    frame["demand_a"] = frame["demand_b"] = 10.
    model, catalog, _ = network.build_feedback_model(config, frame)
    solution = model.solve(time_limit=10., mip_gap=0.)
    _, original_audit = network.audit_feedback_solution(config, model, solution, catalog)
    assert original_audit["passed"] is False
    assert original_audit["invalid_modes"] == ["feedback_mode_0"]
    forged_catalog = deepcopy(catalog)
    forged_catalog[0]["branch_index"] = 1
    try:
        _, audit = network.audit_feedback_solution(config, model, solution, forged_catalog)
    except ValueError:
        return
    assert audit["passed"] is False


@pytest.mark.parametrize("container,key,value", [
    ("branch", "bidirectional", True),
    ("branch", "supply_temperature_column", "dynamic_supply"),
    ("mode", "customer_return_temperature_column", "dynamic_return"),
    ("water", "density_column", "dynamic_density"),
])
def test_nested_dynamic_or_topological_extensions_rejected_before_core(monkeypatch, container, key, value):
    config, frame = case()
    spec = config["network_feedback"]
    target = {"branch": spec["branches"][0], "mode": spec["branches"][0]["operating_modes"][0],
              "water": spec["water"]}[container]
    target[key] = value
    def unexpected_core(*args, **kwargs):
        pytest.fail(f"unsupported physical {container}.{key} reached the frozen core")
    monkeypatch.setattr(network, "HeatPlanningModel", unexpected_core)
    with pytest.raises(ValueError):
        network.build_feedback_model(config, frame)


def test_service_grade_below_absolute_zero_rejected():
    config, frame = case()
    config["grades"][0]["temperature_c"] = -300.
    with pytest.raises(ValueError):
        network.build_feedback_model(config, frame)
