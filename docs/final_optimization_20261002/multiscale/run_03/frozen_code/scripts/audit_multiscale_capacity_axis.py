"""Pure-C factorial design, independent RC/energy audit, saved-vector verification.

The physical audit and exact integrated heat balance import no heatplan code.
The separately labelled matrix/vector check rebuilds existing heatplan equations
without invoking a solver. It is a feasibility check, not a fresh optimality proof.
"""
from __future__ import annotations

from copy import deepcopy
import json
from math import exp, expm1
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[1]
CASES=("C1_unlimited","C4_unlimited","C1_shared","C4_shared")
sys.path.insert(0,str(ROOT/"scripts"))
from audit_multiscale_reference import audit_case, digest, _times


def require(condition,message):
    if not bool(condition): raise ValueError(message)


def design_contract(configs,frames,protocol,reference_frame=None):
    """Check the actual inputs contain exactly the registered two-factor changes."""
    report={"passed":False,"errors":[]}
    try:
        require(protocol["schema_version"]=="multiscale_capacity_axis.v1","wrong schema")
        require(list(protocol["cases"])==list(CASES),"wrong ordered case set")
        require(set(configs)==set(CASES) and set(frames)==set(CASES),"missing/unknown case")
        require(protocol["hours_per_building"]==2880 and protocol["dt_hours"]==1 and protocol["unit_physical_weights"] is True,"wrong physical chronology")
        reference=None; input_reference=None
        expected=pd.date_range(pd.Timestamp(protocol["start_inclusive"]),pd.Timestamp(protocol["end_exclusive"]),freq="h",inclusive="left").tz_convert("UTC")
        require(len(expected)==2880,"wrong registered time window")
        for name in CASES:
            cfg=deepcopy(configs[name]); frame=frames[name]
            require(len(frame)==2880 and _times(frame).equals(expected),"incomplete2880h clock")
            require((frame.period_weight==1).all() and (frame.scenario_probability==1).all(),"nonphysical weights")
            require(frame.scenario.nunique()==1 and frame.boundary_group.nunique()==1,"multiple chronology groups")
            if reference_frame is not None:
                pd.testing.assert_frame_equal(frame[list(reference_frame)],reference_frame,check_dtype=False,rtol=0,atol=1e-12)
            if input_reference is None: input_reference=frame
            else: pd.testing.assert_frame_equal(frame,input_reference,check_dtype=False,rtol=0,atol=1e-12)
            specs=cfg["building_thermal"]["buildings"]
            require(len(specs)==2 and [b["name"] for b in specs]==["variant_A","variant_B"],"wrong buildings")
            for j,(h,c) in enumerate(((.1043,.535576),(.15645,.535576*(1 if name.startswith("C1_") else 4)))):
                b=specs[j]
                for field,wanted in (("heat_loss_kw_per_c",h),("thermal_capacity_kwh_per_c",c),("area_m2",48.),("initial_temperature_c",20.),("final_temperature_c",20.),("min_temperature_c",20.),("max_temperature_c",27.),("max_heat_kw",1000.),("max_cooling_kw",1000.)):
                    require(not isinstance(b[field],bool) and np.isfinite(b[field]) and abs(float(b[field])-wanted)<1e-12,f"undeclared building change:{field}")
                require(np.all(frame[b["internal_gain_column"]]==.2) and np.all(frame[b["solar_gain_column"]]==0.),"undeclared gains")
            for source in cfg["sources"]:
                require(source["type"]=="electric_boiler" and source["existing_capacity_kw"]==1000. and source["efficiency"]==1. and source["max_new_capacity_kw"]==0. and source["max_retire_capacity_kw"]==0.,"undeclared heat equipment")
                cold=cfg["multi_energy_services"]["cooling"]["sources"][source["name"]]
                require(cold["eer"]==1. and cold["eer_min"]==1. and cold["eer_max"]==1. and cold["capacity_equivalence"]==1.,"undeclared cooling equipment")
            derived=.1043*2.5*max(20.-float(frame.outdoor_temp.min()),0.)
            require(abs(float(protocol["capacity_kw"])-derived)<1e-10,"capacity rule altered")
            if name.endswith("shared"):
                wanted={"existing_capacity_kw":protocol["capacity_kw"],"max_new_capacity_kw":0.,"reserve_margin":0.,"firm_capacity_method":"portfolio_dispatch","firm_simultaneity":1.}
                require(cfg.get("grid_connection")==wanted,"shared resource fields altered")
            else: require("grid_connection" not in cfg,"unlimited unexpectedly constrained")
            cfg.pop("grid_connection",None)
            specs[1]["thermal_capacity_kwh_per_c"]=.535576
            if reference is None: reference=cfg
            else: require(cfg==reference,"non-C/non-grid config differs across factorial cases")
        report["passed"]=True
    except (KeyError,ValueError,TypeError,AssertionError,AttributeError) as exc:
        report["errors"].append(str(exc))
    return report


def thermal_ledger(config,frame,trajectory):
    """Exact integral under piecewise-constant hourly forcing; no endpoint proxy.

Qheat-Qcool+gains = envelope heat loss + change in sensible inventory.
C(T-20) is model inventory; it is not a validated ADR-event storage potential.
"""
    report={"passed":False,"method":"analytic continuous-hour1R1C integrated heat balance","buildings":[],"inventory_is_event_storage_capacity":False}
    try:
        dt=float(config["planning"]["time_step_hours"])
        outdoor=pd.to_numeric(frame[config["building_thermal"]["outdoor_temperature_column"]],errors="raise").to_numpy(float)
        for b in config["building_thermal"]["buildings"]:
            rows=trajectory[trajectory.building==b["name"]].sort_values("time_index")
            require(len(rows)==len(frame) and np.array_equal(rows.time_index,np.arange(len(frame))),"incomplete thermal ledger")
            h,c=float(b["heat_loss_kw_per_c"]),float(b["thermal_capacity_kwh_per_c"])
            start=rows.temperature_start_c.to_numpy(float); end=rows.temperature_end_c.to_numpy(float)
            heat=rows.heat_kw.to_numpy(float); cool=rows.cooling_kw.to_numpy(float)
            gains=frame[b["internal_gain_column"]].to_numpy(float)+frame[b["solar_gain_column"]].to_numpy(float)
            require(np.isfinite(np.r_[outdoor,start,end,heat,cool,gains,h,c,dt]).all() and h>0 and c>0 and dt>0,"nonfinite thermal input")
            tau=c/h; fraction=-expm1(-dt/tau)
            equilibrium=outdoor+(heat-cool+gains)/h
            integrated=equilibrium*dt+(start-equilibrium)*tau*fraction
            envelope=h*(integrated-outdoor*dt)
            inventory=c*(end-start)
            balance=(heat-cool+gains)*dt-envelope-inventory
            reconstructed_end=equilibrium+(start-equilibrium)*exp(-dt/tau)
            residual=float(np.max(np.abs(balance)))
            require(residual<=1e-6 and np.max(np.abs(reconstructed_end-end))<=1e-6,"continuous-hour heat balance failed")
            report["buildings"].append({"name":b["name"],"time_constant_hours":tau,"nominal_comfort_inventory_range_kwh":c*7.,"maximum_inventory_above20_kwh":float(np.max(c*(end-20.))),"mean_temperature_continuous_c":float(integrated.sum()/(len(frame)*dt)),"integrated_temperature_above20_c_hours":float((integrated-20.*dt).sum()),"envelope_heat_loss_kwh":float(envelope.sum()),"net_delivered_heat_kwh":float((heat-cool).sum()*dt),"internal_and_solar_gain_kwh":float(gains.sum()*dt),"terminal_inventory_change_kwh":float(inventory.sum()),"max_hourly_balance_residual_kwh":residual})
        report["passed"]=True
    except (KeyError,ValueError,TypeError,AttributeError) as exc: report["reason"]=str(exc)
    return report


def vector_audit(config,frame,values,names,trajectory,dispatch,summary):
    """Separately rebuild the registered matrix; never optimize during auditing."""
    sys.path.insert(0,str(ROOT/"src"))
    from heatplan.model import HeatPlanningModel
    report={"passed":False,"method":"core matrix and export rebuild without solver","independent_physics":False,"solver_optimality_verified":False}
    try:
        model=HeatPlanningModel(config,frame); model.build()
        require(names==model.problem.names,"variable names/order differs")
        report["numerical"]=model.problem.audit_solution(values)
        require(report["numerical"]["numerical_audit_passed"],"full vector violates matrix/bounds/integrality")
        objective=float(np.dot(model.problem.objective,values))
        require(abs(objective-float(summary["objective_value"]))<=1e-5,"vector objective differs from bill")
        exports=((model._make_dispatch(values),dispatch),(model.building_thermal.export_trajectory(values),trajectory))
        for regenerated,saved in exports:
            regenerated=regenerated.copy();saved=saved.copy()
            # CSV keeps explicit timezone text while native exports use
            # DatetimeArray. Compare UTC instants strictly, not Python types.
            regenerated["time"]=_times(regenerated)
            saved["time"]=_times(saved)
            pd.testing.assert_frame_equal(regenerated.reset_index(drop=True),saved.reset_index(drop=True),check_dtype=False,rtol=0,atol=1e-6)
        report.update(passed=True,objective=objective,variable_count=len(names),row_count=len(model.problem.rows),export_identity=True)
    except (KeyError,ValueError,TypeError,AssertionError,AttributeError) as exc: report["reason"]=str(exc)
    return report


def verify_run(run):
    run=Path(run).resolve(); report={"passed":False,"scope":"four-case pure-C/shared-grid factorial reference","solver_optimality_verified":False,"field_validation":False,"cases":[],"errors":[],"old_frozen_evidence_unchanged":False}
    def read(path): return json.loads(path.read_text(encoding="utf-8"))
    def local(rel):
        path=(run/rel).resolve(); require(path.is_relative_to(run),"artifact escapes run"); return path
    try:
        protocol=read(run/"protocol.json")
        require(digest(run/"protocol.json")==read(run/"protocol_lock.json")["protocol.json"],"protocol lock mismatch")
        for original,snapshot in protocol["source_snapshots"].items():
            require(digest(local(snapshot["path"]))==snapshot["sha256"],f"source snapshot mismatch:{original}")
            require(digest(ROOT/original)==snapshot["sha256"],f"current code/input differs:{original}")
        for original,expected in protocol["reference_inputs"].items():
            require(protocol["source_snapshots"][original]["sha256"]==expected,"reference identity differs")
        for rel,expected in protocol["old_frozen_files"].items():
            require(digest(ROOT/rel)==expected,f"old frozen evidence changed:{rel}")
        report["old_frozen_evidence_unchanged"]=True
        for rel,expected in read(run/"manifest.json")["files"].items(): require(digest(local(rel))==expected,f"output hash mismatch:{rel}")
        require(set(protocol["case_files"])==set(CASES),"frozen input case set differs")
        configs,frames={},{}
        for name,entries in protocol["case_files"].items():
            for rel,expected in entries.items(): require(digest(local(rel))==expected,"input hash mismatch")
            configs[name]=yaml.safe_load((run/"input_cases"/name/"config.yaml").read_text(encoding="utf-8"));frames[name]=pd.read_csv(run/"input_cases"/name/"input.csv")
        series=next(rel for rel in protocol["reference_inputs"] if rel.endswith("hourly_tou.csv"))
        reference_frame=pd.read_csv(local(protocol["source_snapshots"][series]["path"]))
        sys.path.insert(0,str(ROOT/"src"))
        from heatplan.time_registry import describe_time_axis
        axis=describe_time_axis(reference_frame,kind="continuous",dt_hours=1.,expected_start=protocol["start_inclusive"],expected_end=protocol["end_exclusive"])
        require(axis==protocol["time_axis"],"registered time identity differs")
        report["time_axis"]=axis
        report["design_contract"]=design_contract(configs,frames,protocol,reference_frame)
        require(report["design_contract"]["passed"],"factorial design contract failed")
        comparison=read(run/"comparison.json")
        require([r["name"] for r in comparison]==list(CASES),"comparison case set/order altered")
        require({f.name for f in (run/"results").iterdir() if f.is_dir()}==set(CASES),"result case set differs")
        for record in comparison:
            name=record["name"];path=run/"results"/name
            checked={"name":name,"passed":False,"status":record["status"]};report["cases"].append(checked)
            try:
                require(not (path/"failure.json").exists(),"retained attempt failure")
                require(record["status"] in ("passed","feasible_unproven"),"attempt not successful")
                for filename in ("config.yaml","input.csv"):
                    require(digest(path/filename)==digest(run/"input_cases"/name/filename),"result input copy mismatch")
                tr=pd.read_csv(path/"building_trajectory.csv");ds=pd.read_csv(path/"dispatch.csv");summary=read(path/"summary.json")
                checked["independent"]=audit_case(configs[name],frames[name],tr,ds,summary)
                checked["thermal_ledger"]=thermal_ledger(configs[name],frames[name],tr)
                checked["saved_vector"]=vector_audit(configs[name],frames[name],np.load(path/"solution_values.npy",allow_pickle=False),read(path/"variable_names.json"),tr,ds,summary)
                require(checked["independent"]["passed"] and checked["thermal_ledger"]["passed"] and checked["saved_vector"]["passed"],"physical/thermal/vector audit failed")
                for key,value in checked["independent"]["metrics"].items():
                    expected=record["metrics"][key]
                    require(abs(value-expected)<=1e-5 if isinstance(value,(int,float)) and not isinstance(value,bool) else value==expected,"comparison metric mismatch")
                investments=pd.read_csv(path/"investments.csv")
                require(investments.empty or np.abs(investments.value.to_numpy(float)).max()<=1e-6,"nonzero investment")
                checked["passed"]=True
            except (OSError,ValueError,KeyError,TypeError,AttributeError,AssertionError) as exc: checked["reason"]=str(exc)
        report["passed"]=len(report["cases"])==4 and all(r["passed"] for r in report["cases"])
    except (OSError,KeyError,ValueError,TypeError,AttributeError,AssertionError) as exc: report["errors"].append(str(exc))
    return report
