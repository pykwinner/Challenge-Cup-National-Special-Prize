from copy import deepcopy

import pandas as pd
import pytest

from heatplan.experiments import nondominated_cost_emission_points
from heatplan.model import HeatPlanningModel


def simple_case():
    c = {
        "planning":{"discount_rate":.05,"asset_lifetime_years":20,"time_step_hours":1,"representative_day_weight":1,"max_unserved_rate":0,"mip_gap":0},
        "grades":[{"name":"low","temperature_c":45}],
        "nodes":[{"name":"n","demand_columns":{"low":"heat"}}],
        "sources":[{"name":"hp","node":"n","type":"heat_pump","resource_kind":"renewable","allowed_grades":["low"],"existing_capacity_kw":100,"max_new_capacity_kw":0,"cop_min":2,"cop_max":2,"source_temp_c":10,"carnot_efficiency":.4}],
        "pipes":[],"storages":[],
        "multi_energy_services":{"cooling":{"enabled":True,"demand_columns":{"n":"cool"},"sources":{"hp":{"eer":2,"eer_min":2,"eer_max":2}},"max_unserved_rate":0}},
    }
    f = pd.DataFrame({"time":[0],"heat":[0.],"cool":[100.],"outdoor_temp":[30.],"electricity_price":[.5],"grid_emission_factor":[1.]})
    return c,f


def test_pure_cooling_carbon_is_constrained_by_system_cap():
    c,f = simple_case()
    uncapped = HeatPlanningModel(c,f).solve()
    assert uncapped.summary["total_service_emissions_kg_per_year"] == pytest.approx(50)
    c["planning"]["emissions_cap_kg_per_year"] = 10
    with pytest.raises(RuntimeError,match="未得到可行解"):
        HeatPlanningModel(c,f).solve()
    c["planning"]["emissions_cap_kg_per_year"] = 50
    accepted = HeatPlanningModel(c,f).solve()
    assert accepted.summary["emissions_cap_pass"]


def test_emission_primary_is_not_reversed_by_large_monetary_scale():
    c,f = simple_case()
    c.pop("multi_energy_services")
    f["heat"] = 1.
    c["sources"] = [
        {"name":"clean","node":"n","type":"direct_heat","resource_kind":"renewable","allowed_grades":["low"],"supply_temp_c":90,"existing_capacity_kw":1,"max_new_capacity_kw":0,"variable_cost_yuan_per_kwh_heat":2_000_000.,"emission_kg_per_kwh_heat":0.},
        {"name":"dirty","node":"n","type":"direct_heat","resource_kind":"fossil","allowed_grades":["low"],"supply_temp_c":90,"existing_capacity_kw":1,"max_new_capacity_kw":0,"variable_cost_yuan_per_kwh_heat":0.,"emission_kg_per_kwh_heat":1.},
    ]
    result = HeatPlanningModel(c,f).solve(objective_mode="emissions")
    assert result.summary["total_service_emissions_kg_per_year"] == pytest.approx(0,abs=1e-9)
    assert result.summary["objective_yuan_per_year"] > 1_900_000


def test_lexicographic_carbon_tie_chooses_less_expensive_source():
    c,f = simple_case()
    c.pop("multi_energy_services")
    f["heat"] = 1.
    c["sources"] = [
        {"name":name,"node":"n","type":"direct_heat","resource_kind":"renewable","allowed_grades":["low"],"supply_temp_c":90,"existing_capacity_kw":1,"max_new_capacity_kw":0,"variable_cost_yuan_per_kwh_heat":price,"emission_kg_per_kwh_heat":.1}
        for name,price in [("cheap",1.),("costly",4.)]
    ]
    result = HeatPlanningModel(c,f).solve_lexicographic()
    assert result.summary["total_service_emissions_kg_per_year"] == pytest.approx(.1)
    assert result.summary["objective_yuan_per_year"] == pytest.approx(1.)
    assert result.summary["primary_solver_status"] == 0
    assert result.summary["numerical_audit_passed"]


def test_frontier_removes_duplicates_and_dominated_points_without_losing_tradeoffs():
    f=pd.DataFrame({"objective_yuan_per_year":[1,1+1e-8,2,3,4],"total_service_emissions_kg_per_year":[4,4,3,4,1],"id":["a","duplicate","b","dominated","c"]})
    result=nondominated_cost_emission_points(f)
    assert set(result.id)=={"a","b","c"}
    with pytest.raises(ValueError,match="finite"):
        nondominated_cost_emission_points(pd.DataFrame({"objective_yuan_per_year":[float("nan")],"total_service_emissions_kg_per_year":[1.]}))
