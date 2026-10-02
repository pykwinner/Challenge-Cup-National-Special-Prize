"""Causal design guards and nontrivial physical/vector tampering controls."""
from copy import deepcopy
from io import StringIO
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
sys.path.insert(0,str(ROOT/"src"))
from run_multiscale_capacity_axis import make_case
from run_multiscale_reference import input_reference, START, END
from audit_multiscale_capacity_axis import CASES, design_contract, thermal_ledger, vector_audit
from audit_multiscale_reference import audit_case
from heatplan.model import HeatPlanningModel


@pytest.fixture(scope="module")
def factorial():
    reference,frame=input_reference()
    cap=.1043*2.5*(20.-frame.outdoor_temp.min())
    configs,frames={},{}
    for name in CASES: configs[name],frames[name]=make_case(reference,frame,name,cap)
    protocol={"schema_version":"multiscale_capacity_axis.v1","cases":list(CASES),"hours_per_building":2880,"dt_hours":1,"unit_physical_weights":True,"start_inclusive":START,"end_exclusive":END,"capacity_kw":cap}
    return configs,frames,protocol,frame


def test_exact_pure_c_factorial(factorial):
    configs,frames,protocol,reference=factorial
    assert design_contract(configs,frames,protocol,reference)["passed"]
    low=deepcopy(configs["C1_shared"]);high=deepcopy(configs["C4_shared"])
    high["building_thermal"]["buildings"][1]["thermal_capacity_kwh_per_c"]/=4
    assert low==high


@pytest.mark.parametrize("mutation",["H","comfort","final","grid","equipment","cooler","price","weather","weight","time","gains","C_nonfinite","missing_case"])
def test_reject_confounding_factorial_changes(factorial,mutation):
    configs,frames,protocol,reference=deepcopy(factorial)
    b=configs["C4_shared"]["building_thermal"]["buildings"][1]
    if mutation=="H": b["heat_loss_kw_per_c"]*=1.1
    elif mutation=="comfort": b["max_temperature_c"]=28.
    elif mutation=="final": b["final_temperature_c"]=21.
    elif mutation=="grid": configs["C4_shared"]["grid_connection"]["existing_capacity_kw"]+=.1
    elif mutation=="equipment":
        for cfg in configs.values(): cfg["sources"][0]["efficiency"] = .9
    elif mutation=="cooler":
        for cfg in configs.values(): cfg["multi_energy_services"]["cooling"]["sources"]["ideal_B"]["eer"]=2.
    elif mutation in ("price","weather"):
        column="electricity_price" if mutation=="price" else "outdoor_temp"
        for frame in frames.values(): frame.loc[0,column]+=.1
    elif mutation=="weight": frames["C4_shared"].loc[0,"period_weight"]=2
    elif mutation=="time": frames["C4_shared"].loc[1,"time"]=frames["C4_shared"].loc[0,"time"]
    elif mutation=="gains": frames["C4_shared"].loc[0,b["internal_gain_column"]]=.3
    elif mutation=="C_nonfinite": b["thermal_capacity_kwh_per_c"]=np.nan
    elif mutation=="missing_case": configs.pop("C1_unlimited")
    assert not design_contract(configs,frames,protocol,reference)["passed"]


@pytest.fixture(scope="module")
def solved(factorial):
    configs,frames,_,_=factorial
    cfg=deepcopy(configs["C4_shared"]); frame=frames["C4_shared"].iloc[:24].copy().reset_index(drop=True)
    model=HeatPlanningModel(cfg,frame);result=model.solve(mip_gap=0.,time_limit=30.)
    assert result.summary["numerical_audit_passed"]
    return cfg,frame,model,result


def test_solved_independent_and_vector_audit(solved):
    cfg,frame,model,result=solved
    assert audit_case(cfg,frame,result.building_trajectory,result.dispatch,result.summary)["passed"]
    assert thermal_ledger(cfg,frame,result.building_trajectory)["passed"]
    assert vector_audit(cfg,frame,result.values,model.problem.names,result.building_trajectory,result.dispatch,result.summary)["passed"]


def test_actual_csv_roundtrip_preserves_instants_and_rejects_shift(solved):
    cfg,frame,model,result=solved
    tr=pd.read_csv(StringIO(result.building_trajectory.to_csv(index=False)))
    ds=pd.read_csv(StringIO(result.dispatch.to_csv(index=False)))
    assert vector_audit(cfg,frame,result.values,model.problem.names,tr,ds,result.summary)["passed"]
    ds.loc[0,"time"]=(pd.Timestamp(ds.loc[0,"time"])+pd.Timedelta(hours=1)).isoformat()
    assert not vector_audit(cfg,frame,result.values,model.problem.names,tr,ds,result.summary)["passed"]


@pytest.mark.parametrize("mutation",["state","heat","source","summary","fractional_binary","name_order"])
def test_reject_saved_vector_or_physical_export_tampering(solved,mutation):
    cfg,frame,model,result=solved
    tr=result.building_trajectory.copy();ds=result.dispatch.copy();summary=deepcopy(result.summary)
    x=result.values.copy(); names=list(model.problem.names)
    if mutation=="state": tr.loc[0,"temperature_end_c"]+=.1
    elif mutation=="heat": tr.loc[0,"heat_kw"]+=.1
    elif mutation=="source": ds.loc[ds.category=="source_heat","value"]+=.1
    elif mutation=="summary": summary["objective_value"]+=.1
    elif mutation=="fractional_binary": x[next(i for i,v in enumerate(model.problem.integrality) if v)]=.25
    else: names[0],names[1]=names[1],names[0]
    assert not vector_audit(cfg,frame,x,names,tr,ds,summary)["passed"]
    if mutation in ("state","heat"):
        assert not thermal_ledger(cfg,frame,tr)["passed"]
    if mutation in ("state","heat","source","summary"):
        assert not audit_case(cfg,frame,tr,ds,summary)["passed"]


def test_continuous_loss_integral_not_endpoint_rectangle():
    # Heating off, Tout0C and initial25C: exact exponential cooling; the
    # endpoint rectangle cannot satisfy this integrated energy balance.
    h,c=.1,1.;end=25*np.exp(-h/c)
    cfg={"planning":{"time_step_hours":1},"building_thermal":{"outdoor_temperature_column":"outdoor_temp","buildings":[{"name":"b","heat_loss_kw_per_c":h,"thermal_capacity_kwh_per_c":c,"internal_gain_column":"g","solar_gain_column":"s"}]}}
    frame=pd.DataFrame({"outdoor_temp":[0.],"g":[0.],"s":[0.]})
    tr=pd.DataFrame({"building":["b"],"time_index":[0],"temperature_start_c":[25.],"temperature_end_c":[end],"heat_kw":[0.],"cooling_kw":[0.]})
    ledger=thermal_ledger(cfg,frame,tr)
    assert ledger["passed"]
    actual=ledger["buildings"][0]["envelope_heat_loss_kwh"]
    assert actual==pytest.approx(c*(25-end),abs=1e-12)
    assert abs(actual-h*end)>.1


def test_fixed20_flat_service_is_identical_at_both_capacitances():
    # Negative control against a universal 'larger C always saves' statement.
    frame=pd.DataFrame({"outdoor_temp":[5.]*4,"g":[.2]*4,"s":[0.]*4})
    tr=pd.DataFrame({"building":["b"]*4,"time_index":range(4),"temperature_start_c":[20.]*4,"temperature_end_c":[20.]*4,"heat_kw":[1.3]*4,"cooling_kw":[0.]*4})
    results=[]
    for c in (.5,2.):
        cfg={"planning":{"time_step_hours":1},"building_thermal":{"outdoor_temperature_column":"outdoor_temp","buildings":[{"name":"b","heat_loss_kw_per_c":.1,"thermal_capacity_kwh_per_c":c,"internal_gain_column":"g","solar_gain_column":"s"}]}}
        ledger=thermal_ledger(cfg,frame,tr);assert ledger["passed"]
        results.append(ledger["buildings"][0]["net_delivered_heat_kwh"])
    assert results==pytest.approx([5.2,5.2])
