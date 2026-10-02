import importlib.util
from math import exp
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1]/"scripts/run_representative_season_confirmation.py"
SPEC = importlib.util.spec_from_file_location("representative_season_confirmation",PATH)
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


def case(n=3):
    config={"planning":{"time_step_hours":1},"sources":[{"name":"ideal","type":"electric_boiler","efficiency":1,"max_new_capacity_kw":0,"existing_capacity_kw":1000}],
        "pipes":[],"storages":[],"multi_energy_services":{"cooling":{"sources":{"ideal":{"eer":1}}}},
        "building_thermal":{"boundary_mode":"fixed","simultaneous_mode":"exclusive","buildings":[{
            "min_temperature_c":20,"max_temperature_c":27,"initial_temperature_c":20,"final_temperature_c":20,
            "heat_loss_kw_per_c":.1,"thermal_capacity_kwh_per_c":1,
            "heating_enabled":True,"cooling_enabled":True,"heating_service_column":"heating_service",
            "internal_gain_column":"internal_gain_kw","solar_gain_column":"solar_gain_kw",
            "max_heat_kw":1000,"max_cooling_kw":1000}]}}
    frame=pd.DataFrame({"time":pd.date_range("2024-01-01",periods=n,freq="h",tz="Asia/Shanghai"),
        "outdoor_temp":0.,"internal_gain_kw":0.,"solar_gain_kw":0.,"electricity_price":2.,
        "grid_emission_factor":.4,"tariff_bucket":"peak","heating_service":1})
    return config,frame


def test_preheat_feedback_allows_passive_drift_instead_of_cooling_to_20_each_hour():
    config,frame=case()
    frame.loc[0,"tariff_bucket"]="valley"
    t=core.rollout(config,frame,4)
    assert t.loc[0,"T_end"] == pytest.approx(24)
    assert t.loc[1,"T_start"] == pytest.approx(24)
    assert t.loc[1,"T_end"] == pytest.approx(exp(-.1)*24)
    assert t.loc[1,"heat_kw"] == 0 and t.loc[1,"cool_kw"] == 0
    assert t.loc[2,"T_end"] == pytest.approx(20)


def test_days_inherit_their_own_full_season_state_not_daily_initial20():
    config,frame=case(49)
    frame.loc[23,"tariff_bucket"]="valley"
    t=core.rollout(config,frame,7)
    assert t.loc[23,"T_end"] == pytest.approx(27)
    assert t.loc[24,"T_start"] == pytest.approx(27)
    assert t.loc[24,"T_end"] > 20
    assert t.loc[24,"original_day_index"] == 1
    assert t.loc[48,"terminal_override"]


def test_terminal_restoration_cooling_is_in_electricity_bill_and_co2():
    config,frame=case(2)
    frame["outdoor_temp"]=30
    t=core.rollout(config,frame,0)
    a,b=exp(-.1),(1-exp(-.1))/.1
    passive0=a*20+(1-a)*30
    passive1=a*passive0+(1-a)*30
    expected_cooling=(passive1-20)/b
    assert t.loc[0,"cool_kw"] == 0
    assert t.loc[1,"cool_kw"] == pytest.approx(expected_cooling)
    assert t.loc[1,"energy_kwh"] == pytest.approx(expected_cooling)
    assert t.bill_yuan.sum() == pytest.approx(expected_cooling*2)
    assert t.carbon_kg.sum() == pytest.approx(expected_cooling*.4)
    assert t.iloc[-1].T_end == pytest.approx(20)


def test_same_initial_terminal_single_hour_includes_actual_heating():
    config,frame=case(1)
    t=core.rollout(config,frame,7)
    assert t.loc[0,"heat_kw"] == pytest.approx(2)
    assert t.loc[0,"energy_kwh"] == pytest.approx(2)
    assert t.loc[0,"T_start"] == 20 and t.loc[0,"T_end"] == 20


def test_library_policy_changes_actual_dispatch_and_shared_parameter_across_days():
    config,frame=case(48)
    frame.loc[[0,24],"tariff_bucket"]="valley"
    zero,warm=core.rollout(config,frame,0),core.rollout(config,frame,4)
    assert not np.allclose(zero.heat_kw,warm.heat_kw)
    assert warm.loc[0,"T_end"] == pytest.approx(24) and warm.loc[24,"T_end"] == pytest.approx(24)
    assert zero.iloc[-1].T_end == pytest.approx(warm.iloc[-1].T_end)
    assert (np.minimum(warm.heat_kw,warm.cool_kw)==0).all()


@pytest.mark.parametrize("unit",["s","ms","us","ns"])
def test_chronology_checks_do_not_assume_nanosecond_storage(unit):
    config,frame=case(3)
    frame["time"]=frame.time.dt.as_unit(unit)
    assert len(core.rollout(config,frame,0)) == 3
    frame.loc[2,"time"]=frame.loc[2,"time"]+pd.Timedelta(hours=1)
    with pytest.raises(ValueError,match="contiguous"):
        core.rollout(config,frame,0)


@pytest.mark.parametrize("fault",["duplicate","reverse","nonfinite_driver","bad_bucket","capacity","bad_delta"])
def test_invalid_policies_or_chronology_fail_closed(fault):
    config,frame=case()
    delta=0
    if fault=="duplicate":frame.loc[1,"time"]=frame.loc[0,"time"]
    elif fault=="reverse":frame=frame.iloc[::-1]
    elif fault=="nonfinite_driver":frame.loc[1,"outdoor_temp"]=np.nan
    elif fault=="bad_bucket":frame.loc[1,"tariff_bucket"]="unknown"
    elif fault=="capacity":config["sources"][0]["existing_capacity_kw"]=0
    else:delta=8
    with pytest.raises(ValueError):core.rollout(config,frame,delta)


def test_selects_by_estimated_cost_only_tie_break_has_no_actual_cost_oracle_leak():
    candidates=[{"policy_delta_c":0,"estimated_bill_yuan":10,"estimated_feasible":True,"actual_bill_yuan":100},
                {"policy_delta_c":1,"estimated_bill_yuan":10+1e-9,"estimated_feasible":True,"actual_bill_yuan":1}]
    assert core.choose_policy(candidates)["policy_delta_c"] == 0
    candidates[1]["estimated_bill_yuan"]=9
    assert core.choose_policy(candidates)["policy_delta_c"] == 1


def test_no_estimated_feasible_policy_has_no_automatic_fallback():
    assert core.choose_policy([{"policy_delta_c":0,"estimated_bill_yuan":1,"estimated_feasible":False}]) is None


def test_weights_can_change_finite_policy_selection_without_reoptimizing_dispatch():
    daily=np.array([[1.,5.],[4.,1.]])
    def select(weights):
        return core.choose_policy([{"policy_delta_c":i,"estimated_bill_yuan":float(cost@weights),"estimated_feasible":True}
                                   for i,cost in enumerate(daily)])["policy_delta_c"]
    assert select(np.array([3.,1.])) == 0
    assert select(np.array([1.,3.])) == 1


def test_actual_energy_and_carbon_cap_need_separate_boolean_from_estimated_pass():
    assert core.cap_pass(100,100)
    assert not core.cap_pass(103,102)
    assert core.cap_pass(100*.4,100*.4)
    assert not core.cap_pass(103*.4,102*.4)


def test_policy_simulation_does_not_modify_source_configuration_or_frame():
    config,frame=case()
    from copy import deepcopy
    original_config,original_frame=deepcopy(config),frame.copy(deep=True)
    core.rollout(config,frame,3)
    assert config==original_config
    pd.testing.assert_frame_equal(frame,original_frame)


@pytest.mark.parametrize("fault",["two_buildings","partial_service","cooling_disabled","nonexclusive"])
def test_controller_rejects_unregistered_multi_building_or_partial_service_scope(fault):
    config,frame=case()
    if fault=="two_buildings":config["building_thermal"]["buildings"].append(dict(config["building_thermal"]["buildings"][0]))
    elif fault=="partial_service":frame.loc[1,"heating_service"]=0
    elif fault=="cooling_disabled":config["building_thermal"]["buildings"][0]["cooling_enabled"]=False
    else:config["building_thermal"]["simultaneous_mode"]="allow"
    with pytest.raises(ValueError):core.rollout(config,frame,0)
