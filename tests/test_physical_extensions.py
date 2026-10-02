"""Analytical equation checks only; numerical fixtures are not city data."""
from copy import deepcopy

import pandas as pd
import pytest

from heatplan.experiments import freeze_investment_plan
from heatplan.model import HeatPlanningModel


def config():
    return {
        "planning": {"discount_rate": .05, "asset_lifetime_years": 20,
                     "time_step_hours": 1, "max_unserved_rate": 0,
                     "max_scenario_unserved_rate": 0, "firm_capacity_reserve_margin": 0},
        "grades": [{"name": "low", "temperature_c": 45}],
        "nodes": [{"name": "n", "demand_columns": {"low": "demand"}}],
        "sources": [{"name": "boiler", "node": "n", "type": "electric_boiler",
                     "allowed_grades": ["low"], "existing_capacity_kw": 100,
                     "max_new_capacity_kw": 0, "efficiency": 1}],
        "pipes": [], "storages": [],
    }


def frame(demands):
    return pd.DataFrame({"time": pd.date_range("2024-01-01", periods=len(demands), freq="h").astype(str),
                         "demand": demands, "electricity_price": 1., "grid_emission_factor": .5})


def solve(c, ts):
    model = HeatPlanningModel(c, ts)
    result = model.solve(time_limit=10, mip_gap=0)
    assert result.summary["numerical_audit_passed"]
    return model, result


def test_same_resource_is_not_allocated_twice_and_compressor_heat_is_excluded():
    c, ts = config(), frame([60])
    c["grades"].append({"name": "high", "temperature_c": 75})
    c["nodes"][0]["demand_columns"]["high"] = "high_demand"
    ts["high_demand"] = 60.
    c["sources"] = [
        {"name": "direct", "node": "n", "type": "direct_heat", "supply_temp_c": 50,
         "allowed_grades": ["low"], "existing_capacity_kw": 60},
        {"name": "pump", "node": "n", "type": "heat_pump", "cop_min": 3, "cop_max": 3,
         "allowed_grades": ["high"], "existing_capacity_kw": 60},
    ]
    c["shared_heat_resources"] = [{"name": "industrial", "source_names": ["direct", "pump"],
                                   "available_heat_column": "available", "transfer_efficiency": .8}]
    ts["available"] = 125.
    _, a = solve(c, ts)
    use = a.dispatch.query("category == 'shared_heat_resource_use'")["value"].iloc[0]
    assert use == pytest.approx((60 + 60 * (1 - 1 / 3)) / .8)
    assert a.summary["electricity_kwh_per_year"] == pytest.approx(20)
    assert a.summary["shared_heat_resource_max_overdraw_kw"] <= 1e-8
    ts["available"] = 124.
    with pytest.raises(RuntimeError, match="可行解"):
        solve(c, ts)
    c.pop("shared_heat_resources")
    solve(c, ts)  # The legacy independent device caps miss that shortfall.


def shared_firm_case():
    c = config()
    c["sources"] = [{"name": n, "node": "n", "type": "direct_heat",
                     "existing_capacity_kw": 60, "supply_temp_c": 50,
                     "allowed_grades": ["low"]} for n in ["base", "second"]]
    c["shared_heat_resources"] = [{"name": "pool", "source_names": ["base", "second"],
                                   "available_heat_kw": 75, "transfer_efficiency": 1}]
    return c


def test_shared_pool_can_require_new_independent_firm_backup():
    c, ts = shared_firm_case(), frame([50])
    c["planning"]["firm_capacity_reserve_margin"] = 1
    c["sources"].append({"name": "backup", "node": "n", "type": "boiler",
                          "allowed_grades": ["low"], "existing_capacity_kw": 0,
                          "max_new_capacity_kw": 100, "capex_yuan_per_kw": 1,
                          "variable_cost_yuan_per_kwh_heat": 1})
    _, a = solve(c, ts)
    assert a.investments.query("category == 'source_capacity' and asset == 'backup'")["value"].iloc[0] == pytest.approx(25)
    assert a.summary["firm_shared_resource_checks"][0]["required_upstream_heat_kw"] <= 75 + 1e-8
    c["planning"]["n_minus_1_source_names"] = ["backup"]
    with pytest.raises(RuntimeError): solve(c, ts)


def test_pipe_contingency_reserve_cannot_borrow_duplicate_resource():
    c, ts = shared_firm_case(), frame([50])
    c["nodes"].append({"name": "plant", "demand_columns": {}})
    for source in c["sources"]: source["node"] = "plant"
    c["pipes"] = [{"name": name, "from": "plant", "to": "n", "existing_capacity_kw": 150,
                    "delivery_efficiency": 1, "diameter_options": []} for name in ("a", "b")]
    c["planning"].update(n_minus_1_pipe_names=["a"], n_minus_1_pipe_reserve_margin=1)
    with pytest.raises(RuntimeError): solve(c, ts)
    c["planning"]["n_minus_1_pipe_reserve_margin"] = 0
    _, a = solve(c, ts)
    assert any(x["case"] == "pipe_outage:a" for x in a.summary["firm_shared_resource_checks"])


@pytest.mark.parametrize("failure", ["duplicate_member", "two_pools", "ambiguous_limit", "nan", "efficiency"])
def test_resource_pool_bad_identity_or_units_fail(failure):
    c, ts = config(), frame([1])
    c["sources"][0]["type"] = "direct_heat"
    pool = {"name": "waste", "source_names": ["boiler"], "available_heat_kw": 2, "transfer_efficiency": 1}
    c["shared_heat_resources"] = [pool]
    if failure == "duplicate_member": pool["source_names"].append("boiler")
    if failure == "two_pools": c["shared_heat_resources"].append({**pool, "name": "other"})
    if failure == "ambiguous_limit": pool["available_heat_column"] = "demand"
    if failure == "nan": pool["available_heat_kw"] = float("nan")
    if failure == "efficiency": pool["transfer_efficiency"] = 0
    with pytest.raises(ValueError): solve(c, ts)


def biomass(c):
    c["sources"][0].update(type="boiler", variable_cost_yuan_per_kwh_heat=0.)
    c["biomass_supply_chains"] = [{"name": "fuel", "source_names": ["boiler"],
        "lower_heating_value_kwh_per_kg": 1, "conversion_efficiency": 1,
        "existing_inventory_capacity_kg": 2, "delivery_capacity_kg_per_hour": 1,
        "delivery_availability_column": "delivery", "procurement_cost_yuan_per_kg": 2,
        "boundary_mode": "fixed", "initial_inventory_kg": 1,
        "terminal_inventory_min_kg": 0, "terminal_inventory_max_kg": 0}]
    return c["biomass_supply_chains"][0]


def test_cold_event_consumes_only_explicit_initial_fuel_and_available_deliveries():
    c, ts = config(), frame([1, 1])
    chain = biomass(c)
    ts["delivery"] = [0, 1]
    _, a = solve(c, ts)
    assert a.dispatch.query("category == 'biomass_delivery'")["value"].sum() == pytest.approx(1)
    assert a.summary["event_state_boundaries"][0]["terminal_state"] == pytest.approx(0)
    chain["initial_inventory_kg"] = 0
    with pytest.raises(RuntimeError): solve(c, ts)
    chain["initial_inventory_kg"] = 1
    chain["terminal_inventory_min_kg"] = chain["terminal_inventory_max_kg"] = 1
    with pytest.raises(RuntimeError): solve(c, ts)


def test_fixed_fuel_single_step_retention_and_terminal_are_independent():
    c, ts = config(), frame([.5])
    chain = biomass(c)
    chain["inventory_retention_per_hour"] = .9
    chain["terminal_inventory_min_kg"] = chain["terminal_inventory_max_kg"] = .4
    ts["delivery"] = 0
    _, a = solve(c, ts)
    assert a.summary["event_state_boundaries"][0]["terminal_state"] == pytest.approx(.4)


@pytest.mark.parametrize("failure", ["missing_initial", "missing_terminal", "weighted", "oversized", "cyclic_values", "group_keys"])
def test_event_state_must_have_explicit_and_compatible_boundaries(failure):
    c, ts = config(), frame([1])
    chain = biomass(c)
    ts["delivery"] = 1
    if failure == "missing_initial": chain.pop("initial_inventory_kg")
    if failure == "missing_terminal":
        chain.pop("terminal_inventory_min_kg"); chain.pop("terminal_inventory_max_kg")
    if failure == "weighted": ts["period_weight"] = 2
    if failure == "oversized": chain["initial_inventory_kg"] = 3
    if failure == "cyclic_values": chain["boundary_mode"] = "cyclic"
    if failure == "group_keys": chain["initial_inventory_kg"] = {"wrong": 1}
    with pytest.raises(ValueError): solve(c, ts)


def test_outage_uses_explicit_battery_soc_and_enforces_terminal_recovery():
    c, ts = config(), frame([1])
    c["sources"][0]["existing_capacity_kw"] = 1
    c["grid_connection"] = {"existing_capacity_kw": 1, "availability_column": "grid"}
    ts["grid"] = 0
    battery = c["microgrid_islanding"] = {
        "existing_battery_energy_kwh": 1, "existing_battery_power_kw": 1,
        "battery_charge_efficiency": 1, "battery_discharge_efficiency": 1,
        "battery_boundary_mode": "fixed", "battery_initial_soc_kwh": 1,
        "battery_terminal_soc_min_kwh": 0, "battery_terminal_soc_max_kwh": 0,
    }
    _, a = solve(c, ts)
    assert a.summary["grid_electricity_kwh_per_year"] == pytest.approx(0)
    assert a.summary["microgrid_battery_discharge_kwh_per_year"] == pytest.approx(1)
    battery["battery_terminal_soc_min_kwh"] = battery["battery_terminal_soc_max_kwh"] = 1
    with pytest.raises(RuntimeError): solve(c, ts)


def tariff(c, method="monthly_demand"):
    spec = {"method": method, "meter_scope": "modeled_energy_system_import",
            "coverage_policy": "modeled_hours_only", "rate_yuan_per_kw_month": 42}
    c["electricity_tariff"] = spec
    return spec


def test_monthly_demand_is_peak_not_energy_and_keeps_calendar_months_separate():
    c, ts = config(), frame([2, 5, 3])
    ts["time"] = ["2024-01-31 23:00", "2024-02-01 00:00", "2024-02-01 01:00"]
    tariff(c)
    _, a = solve(c, ts)
    assert a.summary["components_yuan_per_year"]["heating_electricity_basic_cost"] == pytest.approx(42 * (2 + 5))
    assert a.summary["objective_yuan_per_year"] == pytest.approx(304)
    assert [x["maximum_modeled_import_kw"] for x in a.summary["monthly_electricity_billing"]] == pytest.approx([2, 5])


def test_basic_fee_changes_economic_dispatch_and_uses_net_local_generation():
    c, ts = config(), frame([10])
    ts["electricity_price"] = .1
    c["sources"].append({"name": "fuel", "node": "n", "type": "boiler", "existing_capacity_kw": 10,
                          "allowed_grades": ["low"], "variable_cost_yuan_per_kwh_heat": .5})
    _, a = solve(c, ts)
    assert a.summary["electricity_kwh_per_year"] == pytest.approx(10)
    tariff(c)["rate_yuan_per_kw_month"] = 1
    _, a = solve(c, ts)
    assert a.summary["electricity_kwh_per_year"] == pytest.approx(0)
    assert a.summary["objective_yuan_per_year"] == pytest.approx(5)
    c["renewable_electricity"] = {"availability_column": "green", "price_yuan_per_kwh": 0,
                                  "emission_kg_per_kwh": 0}
    ts["green"] = 10
    _, a = solve(c, ts)
    assert a.summary["electricity_kwh_per_year"] == pytest.approx(10)
    assert a.summary["components_yuan_per_year"]["heating_electricity_basic_cost"] == pytest.approx(0)


def test_monthly_cost_probability_and_cvar_ledger_match():
    c, ts = config(), frame([1, 3])
    ts["scenario"] = ["a", "b"]
    ts["scenario_probability"] = [.25, .75]
    tariff(c)["rate_yuan_per_kw_month"] = 10
    c["planning"]["risk"] = {"cvar_alpha": .5, "cvar_weight": .1}
    _, a = solve(c, ts)
    assert a.summary["components_yuan_per_year"]["heating_electricity_basic_cost"] == pytest.approx(25)
    assert a.summary["scenario_results"]["a"]["operating_risk_cost_yuan"] == pytest.approx(11)
    assert a.summary["scenario_results"]["b"]["operating_risk_cost_yuan"] == pytest.approx(33)


def test_shared_meter_cooling_kpi_includes_its_allocated_basic_fee():
    c, ts = config(), frame([3])
    c["sources"][0].update(type="heat_pump", cop_min=3, cop_max=3)
    c["multi_energy_services"] = {"cooling": {"demand_columns": {"n": "cold"},
        "sources": {"boiler": {"eer": 3}}, "max_unserved_rate": 0}}
    ts["cold"] = 6
    tariff(c).update(rate_yuan_per_kw_month=10, heating_cost_allocation_fraction=.4)
    _, result = solve(c, ts)
    assert result.summary["objective_yuan_per_year"] == pytest.approx(33)
    assert result.summary["heating_cash_operating_cost_yuan_per_year"] == pytest.approx(13)
    assert result.summary["energy_proportional_levelized_cooling_cost_yuan_per_kwh"] == pytest.approx(20 / 6)


def test_capacity_contract_charges_once_and_limits_import():
    c, ts = config(), frame([2, 3])
    tariff(c, "monthly_capacity").update(rate_yuan_per_kva_month=26.3, contract_capacity_kva=4, power_factor=.8)
    _, a = solve(c, ts)
    assert a.summary["components_yuan_per_year"]["heating_electricity_basic_cost"] == pytest.approx(105.2)
    ts["demand"] = [2, 3.3]
    with pytest.raises(RuntimeError): solve(c, ts)


@pytest.mark.parametrize("failure", ["weighted", "incomplete", "numeric_time", "unordered", "negative_rate", "missing_month"])
def test_billing_ambiguities_are_rejected(failure):
    c, ts = config(), frame([1, 1])
    spec = tariff(c)
    if failure == "weighted": ts["period_weight"] = 12
    if failure == "incomplete": spec["coverage_policy"] = "require_complete_months"
    if failure == "numeric_time": ts["time"] = [0, 1]
    if failure == "unordered": ts["time"] = ts["time"].tolist()[::-1]
    if failure == "negative_rate": spec["rate_yuan_per_kw_month"] = -1
    if failure == "missing_month": spec["rate_yuan_per_kw_month"] = {"2024-02": 42}
    with pytest.raises(ValueError): solve(c, ts)


def test_full_month_and_default_frozen_service_contract():
    c, ts = config(), frame([1] * (31 * 24))
    tariff(c)["coverage_policy"] = "require_complete_months"
    _, a = solve(c, ts)
    fixed = freeze_investment_plan(c, a)
    assert fixed["planning"]["max_unserved_rate"] == 0
    assert fixed["replay_service_policy"] == "original_service_requirements"
    assert a.summary["components_yuan_per_year"]["heating_electricity_basic_cost"] == pytest.approx(42)
    relaxed = freeze_investment_plan(c, a, relax_reliability=True)
    assert relaxed["planning"]["max_unserved_rate"] == 1
    assert relaxed["replay_service_policy"] == "diagnostic_relaxed_reliability"
