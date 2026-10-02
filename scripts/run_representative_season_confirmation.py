"""Offline day-weight -> finite thermostat-policy selection on one admitted season.

Every library policy is simulated on the complete chronology BEFORE scoring its
real daily slices. This is neither a simulation speedup nor out-of-sample work.
No investment decisions or reoptimized full-season dispatch are involved.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from math import exp
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from heatplan.scenarios import extreme_aware_typical_days, calibrate_representative_day_weights
from heatplan.hard_weight_calibration import minimum_w1_energy_weights
from run_evidence_case_experiments import (load_inputs, chronology_check, verify_admission,
    code_identity, sha, save)

CONFIG = ROOT / "configs/evidence_case_20261002/wuhan_bestest600_rc_tou.yaml"
SERIES = ROOT / "data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv"
ADMISSION = ROOT / "docs/implementation_20261002/data/admission_tou.json"
REFERENCE = "heat_reference_no_gain_kw"
POLICIES, BUDGETS = list(range(8)), [1., 1.02, 1.05, 1.10]
CAP_ATOL, CAP_RTOL, COST_TIE_ATOL = 1e-7, 1e-9, 1e-8


def require(ok, reason):
    if not bool(ok):
        raise ValueError(reason)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def identities():
    code = {name.replace("\\","/"):digest for name,digest in code_identity().items()}
    for path in [Path(__file__), ROOT/"src/heatplan/scenarios.py", ROOT/"src/heatplan/hard_weight_calibration.py"]:
        code[path.relative_to(ROOT).as_posix()] = sha(path)
    return code


def inputs():
    config, frame = load_inputs(CONFIG, SERIES)
    admission = verify_admission(ADMISSION, CONFIG, SERIES, rc=True)
    require(admission.get("conditional_operating_tariff_reference_admitted") is True,"conditional TOU source admission required")
    chronology_check(frame, config, "2024-11-16T00:00:00+08:00", "2025-03-16T00:00:00+08:00")
    require(len(frame) == 2880 and np.allclose(frame.period_weight, 1) and
            np.allclose(frame.scenario_probability, 1), "one full 2880h season with unit weights required")
    require(frame.grid_emission_factor.nunique() == 1, "this protocol registers redundant energy/carbon caps")
    require(np.isfinite(frame[REFERENCE]).all() and (frame[REFERENCE]>=0).all() and frame[REFERENCE].sum()>0,
            "nonnegative positive-energy reference proxy required")
    return config, frame, admission


def rollout(config, frame, delta):
    """State-feedback thermostat; carry state across all days and restore final T."""
    require(isinstance(delta, (int, np.integer)) and delta in POLICIES, "policy delta must be frozen integer 0..7")
    require(len(frame) > 0, "empty policy chronology")
    stamps = pd.DatetimeIndex(pd.to_datetime(frame.time, errors="raise", utc=True))
    require(stamps.is_monotonic_increasing and not stamps.has_duplicates and
            (len(stamps) == 1 or np.all(stamps[1:]-stamps[:-1] == pd.Timedelta(hours=1))), "policy chronology must be contiguous 1h")
    require(len(config["building_thermal"]["buildings"])==1,"exactly one building required")
    building, source = config["building_thermal"]["buildings"][0], config["sources"][0]
    require(config["building_thermal"]["boundary_mode"] == "fixed" and
            len(config["sources"]) == 1 and not config["pipes"] and not config["storages"], "fixed one-zone ideal-source scope required")
    service=building.get("heating_service_column")
    require(config["building_thermal"].get("simultaneous_mode")=="exclusive" and
            building.get("heating_enabled") is True and building.get("cooling_enabled") is True and
            service in frame and (frame[service]==1).all(),"exclusive enabled heat/cool with full-hour service required")
    require(building["min_temperature_c"] == 20 and building["max_temperature_c"] == 27 and
            building["initial_temperature_c"] == 20 and building["final_temperature_c"] == 20,
            "registered 20..27C comfort and 20C initial/final state required")
    cooling = config["multi_energy_services"]["cooling"]["sources"][source["name"]]
    require(source.get("type")=="electric_boiler" and source["efficiency"] == 1 and cooling["eer"] == 1 and source["max_new_capacity_kw"] == 0,
            "no investment and ideal heat/cool efficiency required")
    h, c = building["heat_loss_kw_per_c"], building["thermal_capacity_kwh_per_c"]
    require(h > 0 and c > 0 and config["planning"]["time_step_hours"] == 1, "positive RC and 1h step required")
    a, b = exp(-h/c), (1-exp(-h/c))/h
    temperature, rows = 20., []
    for t, row in enumerate(frame.to_dict("records")):
        require(row["tariff_bucket"] in {"valley","flat","peak","sharp"}, "unrecognized TOU bucket")
        outdoor, internal, solar, price, factor = [float(row[key]) for key in
            ["outdoor_temp", building["internal_gain_column"], building["solar_gain_column"], "electricity_price", "grid_emission_factor"]]
        require(np.isfinite([outdoor, internal, solar, price, factor]).all() and min(price,factor) >= 0, "invalid policy driver")
        passive = a*temperature + (1-a)*outdoor + b*(internal+solar)
        terminal = t == len(frame)-1
        target = 20. if terminal else 20.+delta if row["tariff_bucket"] == "valley" else 20.
        # Allow natural heat drift. Only actual overheating or the final fixed
        # boundary causes cooling, rather than resetting every non-valley hour.
        signed = (20-passive)/b if terminal else (target-passive)/b if passive < target else (27-passive)/b if passive > 27 else 0.
        heat, cool = max(signed,0.), max(-signed,0.)
        end = passive + b*(heat-cool)
        require(20-1e-8 <= end <= 27+1e-8 and heat <= building["max_heat_kw"]+1e-8 and
                cool <= building["max_cooling_kw"]+1e-8 and heat+cool <= source["existing_capacity_kw"]+1e-8,
                "policy exceeds comfort/source delivery capacity")
        energy = heat+cool
        rows.append({"time":row["time"], "time_index":t, "original_day_index":t//24,
            "policy_delta_c":int(delta), "T_start":temperature, "T_end":end, "heat_kw":heat, "cool_kw":cool,
            "passive_temperature_c":passive, "thermostat_target_c":target, "terminal_override":terminal,
            "energy_kwh":energy, "bill_yuan":energy*price, "carbon_kg":energy*factor,
            "tariff_bucket":row["tariff_bucket"], "electricity_price":price, "grid_emission_factor":factor})
        temperature = end
    return pd.DataFrame(rows)


def cap_pass(value, cap):
    return bool(value <= cap + CAP_ATOL + CAP_RTOL*max(abs(value),abs(cap),1))


def choose_policy(candidates):
    feasible = [r for r in candidates if r["estimated_feasible"]]
    if not feasible:
        return None
    best = min(r["estimated_bill_yuan"] for r in feasible)
    return min((r for r in feasible if r["estimated_bill_yuan"] <= best+COST_TIE_ATOL), key=lambda r:r["policy_delta_c"])


def freeze(out):
    require(not out.exists(), "freeze output exists; do not overwrite evidence")
    config, frame, admission = inputs()
    representative = extreme_aware_typical_days(frame, [REFERENCE,"outdoor_temp","electricity_price"], [REFERENCE],6,42)
    selected = representative.groupby("representative_day",sort=False).first().reset_index()
    selected["lower_bound"] = np.where(selected.is_extreme_day,1.,1e-6)
    selected = selected[["representative_day","original_day_index","original_time","period_weight","is_extreme_day","extreme_reasons","lower_bound"]]
    out.mkdir(parents=True)
    selected.to_csv(out/"selected_days.csv",index=False)
    representative["time"] = representative.original_time
    representative.to_csv(out/"selected_actual_hours.csv",index=False)
    sources = {name.replace("\\","/"):digest for name,digest in admission["input_sha256"].items()}
    sources[ADMISSION.relative_to(ROOT).as_posix()] = sha(ADMISSION)
    save(out/"protocol.json", {"frozen_utc":datetime.now(timezone.utc).isoformat(), "source_sha256":sources,
        "code_sha256":identities(), "policies_delta_c":POLICIES, "budget_multipliers":BUDGETS,
        "methods":["frequency","soft_energy","hard_energy_minimum_W1"], "seed":42,"initial_cluster_count":6,
        "calibration_column":REFERENCE, "calibration_scope":"model-derived no-gain reference heat only; not actual policy electric energy or metered observations",
        "regularization":1e-4,"extreme_weight_floor":1.,"ordinary_weight_floor":1e-6,
        "policy":"valley target20+delta; other hours floor20; natural drift<=27; final hour restore20 with heat/cooling accounted",
        "selection":"lowest estimated bill among estimated energy/carbon-feasible library policies; absolute cost tie1e-8 then smaller delta",
        "cap_absolute_tolerance":CAP_ATOL,"cap_relative_tolerance":CAP_RTOL,
        "budget_baseline":"same complete-season zero-preheat actual electricity/CO2 for all methods and four caps",
        "oracle":"same-season full-chronology enumeration of this eight-policy library only",
        "same_full_season_used_for_calibration_and_scoring":True,"continuous_library_trajectories_built_before_daily_scoring":True,
        "energy_and_carbon_caps_redundant_under_fixed_factor":True,"full_trajectory_simulation_not_saved":True,
        "prediction_or_out_of_sample_claimed":False,"investment_improvement_claimed":False,
        "source_input_modified":False,"adaptive_grid_or_budget_adjustment_allowed":False})
    save(out/"protocol_lock.json", {name:sha(out/name) for name in ["protocol.json","selected_days.csv","selected_actual_hours.csv"]})
    print(json.dumps({"frozen":True,"selected_days":len(selected),"protocol_sha256":sha(out/"protocol.json")}))


def verify(out):
    for name,digest in read(out/"protocol_lock.json").items():
        require(sha(out/name)==digest,f"frozen output changed: {name}")
    protocol=read(out/"protocol.json")
    for name,digest in protocol["source_sha256"].items():
        require(sha(ROOT/name)==digest,f"source changed: {name}")
    for name,digest in protocol["code_sha256"].items():
        require(sha(ROOT/name)==digest,f"code changed: {name}")
    if (out/"manifest.json").exists():
        for name,digest in read(out/"manifest.json")["files"].items():
            require(sha(out/name)==digest,f"run output changed: {name}")
    return protocol


def run(out):
    protocol=verify(out)
    require(not (out/"policies").exists(), "run exists; preserve failure and choose a new output")
    config,frame,_=inputs()
    representative=pd.read_csv(out/"selected_actual_hours.csv")
    selected=pd.read_csv(out/"selected_days.csv")
    days=selected.original_day_index.to_numpy(dtype=int)
    original=selected.period_weight.to_numpy(dtype=float)
    profiles=frame[REFERENCE].to_numpy().reshape(-1,24)[days]
    lower=selected.lower_bound.to_numpy(dtype=float)
    methods={"frequency":{"status":"ok","weights":original.tolist()}}
    started=perf_counter(); failures=[]
    for method in ["soft_energy","hard_energy_minimum_W1"]:
        try:
            if method=="soft_energy":
                result=calibrate_representative_day_weights(representative,frame,[REFERENCE],regularization=1e-4)
                methods[method]={**result.summary,"status":"ok" if result.summary["solver_success"] else "solver_failure",
                                 "weights":result.weights.calibrated_weight.tolist() if result.summary["solver_success"] else None}
            else:
                methods[method]=minimum_w1_energy_weights(frame[REFERENCE].to_numpy(),profiles,original,lower)
        except Exception as error:
            methods[method]={"status":"failure","weights":None,"reason":str(error)}
    save(out/"weight_reports_initial.json",methods)
    (out/"policies").mkdir(); daily=[]; totals=[]
    for delta in POLICIES:
        destination=out/"policies"/f"delta_{delta}"; destination.mkdir()
        trajectory=rollout(config,frame,delta)
        trajectory.to_csv(destination/"trajectory.csv",index=False)
        d=trajectory.groupby("original_day_index",sort=True)[["energy_kwh","bill_yuan","carbon_kg"]].sum().reset_index()
        d["policy_delta_c"]=delta; daily.append(d)
        totals.append({"policy_delta_c":delta,**{column:float(trajectory[column].sum()) for column in ["energy_kwh","bill_yuan","carbon_kg"]},
                       "peak_import_kw":float(trajectory.energy_kwh.max())})
    daily=pd.concat(daily,ignore_index=True); actual={r["policy_delta_c"]:r for r in totals}; baseline=actual[0]
    daily.to_csv(out/"daily_totals.csv",index=False); pd.DataFrame(totals).to_csv(out/"policy_season_totals.csv",index=False)
    weight_rows=[]; candidate_rows=[]; selections=[]
    for method,report in methods.items():
        weights=np.array(report["weights"]) if report.get("status")=="ok" else None
        if weights is not None:
            require(np.isfinite(weights).all() and np.all(weights>=lower-1e-8) and abs(weights.sum()-120)<=1e-6,"invalid weights")
            report["relative_reference_energy_error"]=(float(profiles.sum(axis=1)@weights)-float(frame[REFERENCE].sum()))/float(frame[REFERENCE].sum())
            report["normalized_reference_W1"]=float(wasserstein_distance(frame[REFERENCE],profiles.ravel(),v_weights=np.repeat(weights,24))/frame[REFERENCE].mean())
            weight_rows.extend({"method":method,"original_day_index":int(day),"weight_days":float(w)} for day,w in zip(days,weights))
        else:
            failures.append({"method":method,"reason":report.get("reason",report.get("solver_message",report["status"]))})
        for multiplier in BUDGETS:
            energy_cap,carbon_cap=baseline["energy_kwh"]*multiplier,baseline["carbon_kg"]*multiplier
            oracle_candidates=[r for r in totals if cap_pass(r["energy_kwh"],energy_cap) and cap_pass(r["carbon_kg"],carbon_cap)]
            oracle=choose_policy([{**r,"estimated_feasible":True,"estimated_bill_yuan":r["bill_yuan"]} for r in oracle_candidates])
            candidates=[]
            for delta in POLICIES:
                r={"method":method,"budget_multiplier":multiplier,"policy_delta_c":delta,"energy_cap_kwh":energy_cap,"carbon_cap_kg":carbon_cap,
                   **{f"actual_{key}":value for key,value in actual[delta].items() if key!="policy_delta_c"}}
                values=daily[daily.policy_delta_c==delta].set_index("original_day_index").loc[days]
                estimates={key:float(values[key].to_numpy()@weights) if weights is not None else None for key in ["energy_kwh","bill_yuan","carbon_kg"]}
                r.update({f"estimated_{key}":value for key,value in estimates.items()},
                    estimated_feasible=weights is not None and cap_pass(estimates["energy_kwh"],energy_cap) and cap_pass(estimates["carbon_kg"],carbon_cap),
                    actual_feasible=cap_pass(r["actual_energy_kwh"],energy_cap) and cap_pass(r["actual_carbon_kg"],carbon_cap))
                candidates.append(r)
            chosen=choose_policy(candidates); candidate_rows.extend(candidates)
            selected_record={"method":method,"budget_multiplier":multiplier,"selection_status":"selected" if chosen else "no_estimated_feasible_candidate",
                "selected_policy_delta_c":chosen["policy_delta_c"] if chosen else None,"oracle_policy_delta_c":oracle["policy_delta_c"],"oracle_bill_yuan":oracle["bill_yuan"],
                "actual_qualified_regret_yuan":chosen["actual_bill_yuan"]-oracle["bill_yuan"] if chosen and chosen["actual_feasible"] else None,
                "actual_energy_cap_excess_kwh":max(0,chosen["actual_energy_kwh"]-energy_cap) if chosen else None,
                "actual_carbon_cap_excess_kg":max(0,chosen["actual_carbon_kg"]-carbon_cap) if chosen else None,
                "selected_policy_actual_feasible":chosen["actual_feasible"] if chosen else False}
            selections.append(selected_record)
    save(out/"weight_reports.json",methods); pd.DataFrame(weight_rows).to_csv(out/"selected_day_weights.csv",index=False)
    pd.DataFrame(candidate_rows).to_csv(out/"policy_candidates.csv",index=False); save(out/"selections.json",selections)
    pd.DataFrame(selections).to_csv(out/"selections.csv",index=False); save(out/"failures.json",failures)
    verify(out)
    save(out/"manifest.json",{"all_weight_methods_available":not failures,"elapsed_seconds":perf_counter()-started,
        "code_and_sources_stable":True,"policy_library_count":8,"budget_count":4,"method_budget_records":12,
        "full_simulation_hours":8*2880,"solver_or_investment_optimization_performed":False,
        "files":{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob("*")) if p.is_file()}})
    print(json.dumps({"completed":True,"weight_failures":len(failures),"selections":selections},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["freeze","run","verify"])
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(); out=args.out.resolve()
    if args.action=="freeze": freeze(out)
    elif args.action=="run":
        try:
            run(out)
        except Exception as error:
            if out.exists() and not (out/"run_failure.json").exists():
                save(out/"run_failure.json",{"type":type(error).__name__,"reason":str(error),
                     "failed_utc":datetime.now(timezone.utc).isoformat(),"partial_outputs_preserved":True})
            raise
    else: verify(out); print(json.dumps({"hash_verification_passed":True}))


if __name__=="__main__":
    main()
