"""Independent counterexamples for event chronology and heat/cold basic fees.

Fixtures are analytical mechanism tests, not real city data.
"""
import pandas as pd
import pytest

from heatplan.model import HeatPlanningModel


def configuration():
    return {"planning": {"discount_rate": .05, "asset_lifetime_years": 20,
            "time_step_hours": 1, "max_unserved_rate": 0,
            "max_scenario_unserved_rate": 0, "firm_capacity_reserve_margin": 0},
            "grades": [{"name": "low", "temperature_c": 45}],
            "nodes": [{"name": "n", "demand_columns": {"low": "demand"}}],
            "sources": [{"name": "s", "node": "n", "type": "boiler",
                         "allowed_grades": ["low"], "existing_capacity_kw": 100,
                         "max_new_capacity_kw": 0, "efficiency": 1,
                         "variable_cost_yuan_per_kwh_heat": 0}],
            "pipes": [], "storages": []}


def frame():
    return pd.DataFrame({"time": ["2024-01-01 00:00", "2024-01-02 00:00"],
                         "demand": [0, .2], "electricity_price": 1,
                         "grid_emission_factor": .5, "delivery": 0})


@pytest.mark.parametrize("times", [
    ["2024-01-01 00:00", "2024-01-02 00:00"],
    ["2024-01-01 01:00", "2024-01-01 00:00"],
    ["2024-01-01 00:00", "2024-01-01 00:00"],
])
def test_fixed_fuel_event_rejects_gap_reverse_and_duplicate_timestamps(times):
    c, ts = configuration(), frame()
    ts["time"] = times
    c["biomass_supply_chains"] = [{"name": "f", "source_names": ["s"],
        "lower_heating_value_kwh_per_kg": 1, "conversion_efficiency": 1,
        "existing_inventory_capacity_kg": 1, "delivery_capacity_kg_per_hour": 0,
        "inventory_retention_per_hour": .5, "boundary_mode": "fixed",
        "initial_inventory_kg": 1, "terminal_inventory_min_kg": 0,
        "terminal_inventory_max_kg": 1}]
    with pytest.raises(ValueError):
        HeatPlanningModel(c, ts).solve(time_limit=10, mip_gap=0)


def test_fixed_battery_event_rejects_missing_elapsed_hours():
    c, ts = configuration(), frame()
    c["sources"][0]["type"] = "electric_boiler"
    c["microgrid_islanding"] = {"existing_battery_energy_kwh": 1,
        "existing_battery_power_kw": 1, "battery_charge_efficiency": 1,
        "battery_discharge_efficiency": 1, "battery_self_loss_per_hour": .5,
        "battery_boundary_mode": "fixed", "battery_initial_soc_kwh": 1,
        "battery_terminal_soc_min_kwh": 0, "battery_terminal_soc_max_kwh": 1}
    c["grid_connection"] = {"existing_capacity_kw": 100, "availability_column": "grid"}
    ts["grid"] = 0
    with pytest.raises(ValueError):
        HeatPlanningModel(c, ts).solve(time_limit=10, mip_gap=0)


def test_cooling_only_basic_fee_is_not_automatically_heating_cash():
    c, ts = configuration(), frame().iloc[:1].copy()
    c["sources"][0].update(type="heat_pump", cop_min=3, cop_max=3)
    c["multi_energy_services"] = {"cooling": {"demand_columns": {"n": "cold"},
        "sources": {"s": {"eer": 3}}, "max_unserved_rate": 0}}
    ts["cold"] = 6
    c["electricity_tariff"] = {"method": "monthly_demand",
        "meter_scope": "modeled_energy_system_import", "coverage_policy": "modeled_hours_only",
        "rate_yuan_per_kw_month": 10, "heating_cost_allocation_fraction": 0}
    result = HeatPlanningModel(c, ts).solve(time_limit=10, mip_gap=0)
    assert result.summary["objective_yuan_per_year"] == pytest.approx(22)
    assert result.summary["heating_cash_operating_cost_yuan_per_year"] == pytest.approx(0)
    assert result.summary["components_yuan_per_year"]["shared_non_heating_electricity_basic_cost"] == pytest.approx(20)
    assert result.summary["energy_proportional_levelized_cooling_cost_yuan_per_kwh"] == pytest.approx(22/6)


def test_shared_heat_cold_meter_refuses_implicit_heating_cost_allocation():
    c, ts = configuration(), frame().iloc[:1].copy()
    c["sources"][0].update(type="heat_pump", cop_min=3, cop_max=3)
    c["multi_energy_services"] = {"cooling": {"demand_columns": {"n": "cold"},
        "sources": {"s": {"eer": 3}}, "max_unserved_rate": 0}}
    ts["cold"] = 6
    c["electricity_tariff"] = {"method": "monthly_demand",
        "meter_scope": "modeled_energy_system_import", "coverage_policy": "modeled_hours_only",
        "rate_yuan_per_kw_month": 10}
    with pytest.raises(ValueError, match="allocation"):
        HeatPlanningModel(c, ts).solve(time_limit=10, mip_gap=0)


def test_explicit_shared_basic_fee_allocation_preserves_total_and_cvar_cost():
    c, ts = configuration(), frame().iloc[:1].copy()
    c["sources"][0].update(type="heat_pump", cop_min=3, cop_max=3)
    c["multi_energy_services"] = {"cooling": {"demand_columns": {"n": "cold"},
        "sources": {"s": {"eer": 3}}, "max_unserved_rate": 0}}
    ts["demand"], ts["cold"] = 3, 6
    c["electricity_tariff"] = {"method": "monthly_demand",
        "meter_scope": "modeled_energy_system_import", "coverage_policy": "modeled_hours_only",
        "rate_yuan_per_kw_month": 10, "heating_cost_allocation_fraction": .4}
    result = HeatPlanningModel(c, ts).solve(time_limit=10, mip_gap=0)
    components = result.summary["components_yuan_per_year"]
    assert components["heating_electricity_basic_cost"] == pytest.approx(12)
    assert components["shared_non_heating_electricity_basic_cost"] == pytest.approx(18)
    assert result.summary["heating_cash_operating_cost_yuan_per_year"] == pytest.approx(13)
    assert result.summary["objective_yuan_per_year"] == pytest.approx(33)
    assert result.summary["scenario_results"]["base"]["operating_risk_cost_yuan"] == pytest.approx(33)
    assert result.summary["energy_proportional_levelized_cooling_cost_yuan_per_kwh"] == pytest.approx(20/6)


def test_shared_upstream_cap_cannot_be_counted_twice_as_firm_reserve():
    c, ts = configuration(), frame().iloc[:1].copy()
    c["planning"]["firm_capacity_reserve_margin"] = 1
    c["sources"] = [{"name": name, "node": "n", "type": "direct_heat",
        "supply_temp_c": 50, "allowed_grades": ["low"], "existing_capacity_kw": 60,
        "max_new_capacity_kw": 0} for name in ["a", "b"]]
    c["shared_heat_resources"] = [{"name": "shared", "source_names": ["a", "b"],
        "available_heat_kw": 75, "transfer_efficiency": 1}]
    ts["demand"] = 50
    # 100 kW reserve is required, but both devices together can draw only 75.
    # Reject an unsupported combination or prove the correct model infeasible.
    with pytest.raises((ValueError, RuntimeError)):
        HeatPlanningModel(c, ts).solve(time_limit=10, mip_gap=0)
