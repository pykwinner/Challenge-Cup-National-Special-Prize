from copy import deepcopy

import pandas as pd
import pytest

from heatplan.model import HeatPlanningModel


def case(outdoor=0.,cooling=False):
    c={"planning":{"discount_rate":.05,"asset_lifetime_years":20,"time_step_hours":1,"representative_day_weight":1,"max_unserved_rate":0,"mip_gap":0,"design_outdoor_temperature_c":-5},
       "grades":[{"name":"low","temperature_c":45}],
       "nodes":[{"name":"n","demand_columns":{"low":"old_heat"},"thermal_inertia":{"low":{"thermal_capacity_kwh_per_c":999,"max_temperature_deviation_c":1}}}],
       "sources":[{"name":"hp","node":"n","type":"heat_pump","resource_kind":"renewable","allowed_grades":["low"],"existing_capacity_kw":100,"max_new_capacity_kw":0,"cop_min":2,"cop_max":2,"source_temp_c":10,"carnot_efficiency":.4}],
       "pipes":[],"storages":[],"demand_response":{"max_shift_fraction":.5},
       "building_thermal":{"enabled":True,"buildings":[{"name":"b","node":"n","heat_grade":"low","area_m2":100,"heat_loss_kw_per_c":1,"thermal_capacity_kwh_per_c":3,"initial_temperature_c":20,"final_temperature_c":20,"min_temperature_c":20,"max_temperature_c":24,"max_cooling_kw":100 if cooling else 0}]}}
    f=pd.DataFrame({"time":pd.date_range("2023-01-01",periods=4,freq="h",tz="UTC").astype(str),"old_heat":999.,"old_cool":777.,"outdoor_temp":outdoor,"electricity_price":.5,"grid_emission_factor":1.})
    f["timestamp"]=f.time
    if cooling:
        c["multi_energy_services"]={"cooling":{"enabled":True,"demand_columns":{"n":"old_cool"},"sources":{"hp":{"eer":2,"eer_min":2,"eer_max":2}},"max_unserved_rate":0}}
    return c,f


def test_actual_delivered_heat_replaces_old_template_dr_and_state():
    c,f=case()
    m=HeatPlanningModel(c,f)
    a=m.solve()
    assert not m.variables["unserved"] and not m.variables["demand_up"] and not m.variables["building_thermal_state"]
    assert a.summary["building_comfort_state_passed"]
    assert a.summary["demand_kwh_per_year"] == pytest.approx(80)
    assert a.building_trajectory.heat_kw.sum() == pytest.approx(80)
    assert m.design_peak_demand() == pytest.approx(25)
    assert a.summary["building_reference_heat_kwh_per_year"] == pytest.approx(80)
    assert a.dispatch.query("category=='building_temperature'").value.min() == pytest.approx(20)


def test_no_supply_cannot_be_hidden_by_unserved_or_fictitious_temperature():
    c,f=case()
    c["sources"][0]["existing_capacity_kw"]=0
    with pytest.raises(RuntimeError,match="未得到可行解"):
        HeatPlanningModel(c,f).solve()


def test_delivered_cooling_replaces_proxy_and_has_actual_electricity_carbon():
    c,f=case(outdoor=30.,cooling=True)
    c["building_thermal"]["buildings"][0]["max_temperature_c"]=20
    a=HeatPlanningModel(c,f).solve()
    assert a.summary["building_comfort_state_passed"]
    assert a.summary["cooling_demand_kwh_per_year"] == pytest.approx(40)
    assert a.summary["cooling_electricity_kwh_per_year"] == pytest.approx(20)
    assert a.summary["total_service_emissions_kg_per_year"] == pytest.approx(20)
    assert a.summary["demand_kwh_per_year"] == pytest.approx(0)


def test_fixed_other_grade_service_is_retained_beside_rc_building():
    c,f=case()
    c["grades"].append({"name":"high","temperature_c":70})
    c["nodes"][0]["demand_columns"]["high"]="dhw"
    c["sources"][0]["allowed_grades"].append("high")
    f["dhw"]=2.
    a=HeatPlanningModel(c,f).solve()
    assert a.summary["demand_kwh_per_year"] == pytest.approx(88)
    assert a.summary["unserved_kwh_per_year"] == pytest.approx(0)


def test_policy_cost_cap_uses_variable_delivered_heat():
    c,f=case()
    c["policy"]={"maximum_levelized_heat_cost_yuan_per_kwh":.3}
    assert HeatPlanningModel(c,f).solve().summary["building_comfort_state_passed"]
    c["policy"]["maximum_levelized_heat_cost_yuan_per_kwh"]=.2
    with pytest.raises(RuntimeError,match="未得到可行解"):
        HeatPlanningModel(c,f).solve()


def test_rc_fixed_metering_savings_are_rejected():
    c,f=case()
    c["nodes"][0]["heat_metering"]={"saving_fraction_at_full_coverage":.2}
    with pytest.raises(ValueError,match="metering"):
        HeatPlanningModel(c,f)


def test_frozen_annual_plan_preserves_selected_capital_in_cost_and_policy():
    from heatplan.experiments import freeze_investment_plan
    c,f=case()
    c["sources"][0].update(existing_capacity_kw=0,max_new_capacity_kw=100,capex_yuan_per_kw=1000)
    a=HeatPlanningModel(c,f).solve()
    fixed=freeze_investment_plan(c,a,relax_reliability=False)
    replay=HeatPlanningModel(fixed,f).solve()
    assert a.summary["components_yuan_per_year"]["annualized_capex"] > 1000
    assert replay.summary["objective_yuan_per_year"] == pytest.approx(a.summary["objective_yuan_per_year"])
    assert replay.summary["regulated_annualized_capex_yuan_per_year"] == pytest.approx(a.summary["regulated_annualized_capex_yuan_per_year"])
    fixed["policy"]={"maximum_levelized_heat_cost_yuan_per_kwh":1.}
    with pytest.raises(RuntimeError,match="未得到可行解"):
        HeatPlanningModel(fixed,f).solve()


def test_rc_metering_coverage_is_reference_weighted_without_fictitious_saving():
    c,f=case()
    c["nodes"][0]["heat_metering"]={"existing_coverage_fraction":1.,"saving_fraction_at_full_coverage":0.}
    c["policy"]={"minimum_heat_metering_coverage_fraction":.5}
    a=HeatPlanningModel(c,f).solve()
    assert a.summary["heat_metering_coverage_fraction"] == pytest.approx(1.)
    assert a.summary["demand_kwh_per_year"] == pytest.approx(80.)


def test_n_minus_one_is_enforced_even_at_zero_reserve_margin():
    c,f=case()
    c["planning"].update(firm_capacity_reserve_margin=0,n_minus_1_source_names=["hp"])
    with pytest.raises(RuntimeError,match="未得到可行解"):
        HeatPlanningModel(c,f).solve()


def test_rc_coverage_detects_hot_extreme_and_marks_legacy_loads_as_references():
    from heatplan.chronology import representative_period_coverage_audit
    c,f=case(cooling=True)
    f["outdoor_temp"]=[0.,10.,20.,30.]
    audit=representative_period_coverage_audit(c,f.iloc[:2],f)
    assert "hot_extreme_under_captured" in audit.set_index("column").loc["outdoor_temp","findings"]
    assert audit.set_index("column").loc["old_heat","series_kind"] == "legacy_reference"
    assert "rc_steady_cooling_reference_b_kw" in set(audit.column)
