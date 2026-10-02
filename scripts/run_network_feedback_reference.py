"""Freeze/run a public-weather, explicitly designed star-network mechanism case."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from heatplan.network_feedback import solve_with_network_feedback, FeedbackFailure
from run_evidence_case_experiments import save, sha, code_identity

WEATHER = Path("data/derived/three_city_common_reference_20261002/urumqi_hourly.csv")
PRICE = Path("data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv")
PHYSICS = Path("docs/implementation_20261002_followup/parallel_stage_02/data/common_physical_reference.json")


def reference_config():
    branches = []
    for name, length in (("near", 30.), ("far", 150.)):
        branches.append({"name": name, "from": "plant", "to": name,
            "one_way_length_m": length, "inner_diameter_m": .015, "roughness_m": .00005,
            "heat_loss_w_per_mk_per_leg": .3, "supply_temperature_c": 65.,
            "ground_temperature_c": 5., "pump_efficiency": .72,
            "pair_local_loss_coefficient": 2., "minimum_far_supply_c": 55.,
            "maximum_customer_return_c": 45., "maximum_plant_return_c": 45.,
            "maximum_velocity_m_per_s": 3., "maximum_pressure_drop_pa_per_m": 300.,
            "maximum_pair_pressure_drop_pa": 120000., "maximum_pump_power_kw": .25,
            "maximum_instantaneous_heat_input_kw": 30.,
            "target_customer_delta_t_c": 20., "delta_t_soft_band_c": 1.,
            "delta_t_penalty_yuan_per_c_on_hour": .2,
            "operating_modes": [{"name": f"m{mass:g}_r40", "mass_flow_kg_per_s": mass,
                "customer_return_temperature_c": 40.} for mass in (.03, .06, .08, .12, .18)]})
    return {"planning": {"discount_rate": .05, "asset_lifetime_years": 20,
        "time_step_hours": 1, "representative_day_weight": 1, "max_unserved_rate": 0,
        "max_scenario_unserved_rate": 0, "firm_capacity_reserve_margin": 0,
        "carbon_price_yuan_per_kg": 0., "exergy_penalty_yuan_per_kwh": 0.},
        "grades": [{"name": "customer_min55", "temperature_c": 55.}],
        "nodes": [{"name": "plant", "demand_columns": {}}] + [{"name": n, "demand_columns": {"customer_min55": f"{n}_heat_kw"}} for n in ("near", "far")],
        "sources": [{"name": "ideal_fixed_resistance_reference", "node": "plant", "type": "electric_boiler",
            "supply_temp_c": 65., "existing_capacity_kw": 40., "max_new_capacity_kw": 0.,
            "max_retire_capacity_kw": 0., "efficiency": 1., "allowed_grades": ["customer_min55"]}],
        "pipes": [], "storages": [],
        "network_feedback": {"mode": "independent_star_steady_duty", "independent_branch_pumps": True,
            "steady_on_state_duty_approximation_acknowledged": True,
            "maximum_simultaneous_electricity_kw": 50.,
            "water": {"density_kg_per_m3": 985., "cp_kj_per_kgk": 4.18, "viscosity_pa_s": .00055},
            "branches": branches}}


def identities():
    result = {p.as_posix(): sha(ROOT / p) for p in (WEATHER, PRICE, PHYSICS)}
    result.update({k.replace("\\", "/"): v for k, v in code_identity().items()})
    for p in (Path(__file__), ROOT / "src/heatplan/network_feedback.py"):
        result[p.relative_to(ROOT).as_posix()] = sha(p)
    return result


def freeze(out):
    if out.exists():
        raise ValueError("preserve existing output")
    weather, prices = pd.read_csv(ROOT / WEATHER), pd.read_csv(ROOT / PRICE)
    if not pd.DatetimeIndex(pd.to_datetime(weather.time, utc=True)).equals(pd.DatetimeIndex(pd.to_datetime(prices.time, utc=True))):
        raise ValueError("weather/common methodological price calendar differs")
    frame = weather.iloc[:72][["time", "outdoor_temp"]].copy()
    frame["electricity_price"] = prices.electricity_price.iloc[:72].to_numpy()
    frame["grid_emission_factor"] = prices.grid_emission_factor.iloc[:72].to_numpy()
    # An explicit steady public-prototype design demand, not metered heat and
    # not the 1R1C indoor-temperature trajectory of the other experiments.
    physical = json.loads((ROOT / PHYSICS).read_text(encoding="utf-8"))
    reference = (physical["heat_loss_kw_per_c"] * (20 - frame.outdoor_temp)
                 - physical["internal_gain_kw"]).clip(lower=0)
    frame["near_heat_kw"], frame["far_heat_kw"] = .5 * reference, reference
    protocol = {"frozen_utc": datetime.now(timezone.utc).isoformat(), "input_code_sha256": identities(),
        "hours": 72, "window": [str(frame.time.iloc[0]), str(frame.time.iloc[-1])],
        "cases": [{"name": "soft_off", "soft_penalty_yuan_per_c_on_hour": 0.},
                  {"name": "soft_on", "soft_penalty_yuan_per_c_on_hour": .2}],
        "time_limit_per_iteration_seconds": 60., "mip_gap": 0.,
        "design_case": True, "real_project_validation": False,
        "scope": "same-service public-weather mechanism test of finite-mode star thermal-hydraulic feedback",
        "sources": {"weather": "existing NASA POWER UTC-to-Beijing audited Urumqi input",
                    "prototype_H_internal_gain": "existing public BESTEST600-derived heat-loss reference",
                    "prices_and_grid_factor": "existing conditional Hubei methodological reference, not local Urumqi account",
                    "all_geometry_modes_limits_water_properties_penalty_source_rating": "explicit designed numerical mechanism parameters; NOT measured/approved Bayi assets, NOT national-standard defaults"},
        "physical_method_references": [
            {"url": "https://build.openmodelica.org/Documentation/Buildings.Fluid.FixedResistances.BaseClasses.PlugFlowHeatLoss.html", "use": "steady exponential heat attenuation concept only; no adoption of transport/inertia model"},
            {"url": "https://doc.modelica.org/Modelica%203.2.3/Resources/helpWSM/Modelica/Modelica.Fluid.UsersGuide.ComponentDefinition.WallFriction.html", "use": "Darcy friction and Colebrook turbulent law"},
            {"doi": "10.1016/j.enconman.2017.08.072", "use": "related transient model identified through official Buildings documentation; its experimental validation is not our validation"}],
        "cut_validity": "Each excluded steady on-state violates an explicit fixed hard limit independent of dispatch. No arbitrary topology/capacity cuts.",
        "approximation": "hourly mean heat under steady on-state duty; exclude cold starts/transport lag/RC pulse comfort and common-pump balancing",
        "cost_scope": "72-hour source+pump reference electricity plus separately disclosed design delta-T penalty; no pipe investment, annual financial or local bill claim",
        "negative_results": "preserve every rejected iteration and any terminal solver failure; do not increase caps to obtain pass"}
    out.mkdir(parents=True)
    (out / "config.yaml").write_text(yaml.safe_dump(reference_config(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    frame.to_csv(out / "hourly.csv", index=False)
    save(out / "protocol.json", protocol)
    save(out / "protocol_lock.json", {p: sha(out / p) for p in ("protocol.json", "config.yaml", "hourly.csv")})
    print(json.dumps({"frozen": True, "out": str(out)}))


def verify(out):
    for p, digest in json.loads((out / "protocol_lock.json").read_text(encoding="utf-8")).items():
        if sha(out / p) != digest:
            raise ValueError(f"frozen input changed: {p}")
    protocol = json.loads((out / "protocol.json").read_text(encoding="utf-8"))
    if identities() != protocol["input_code_sha256"]:
        raise ValueError("source/code identities changed")
    return protocol


def run(out):
    protocol = verify(out)
    results_dir = out / "results"
    results_dir.mkdir(exist_ok=False)
    config = yaml.safe_load((out / "config.yaml").read_text(encoding="utf-8"))
    frame = pd.read_csv(out / "hourly.csv")
    summary, failures = [], []
    for case in protocol["cases"]:
        current = deepcopy(config)
        folder = results_dir / case["name"]
        folder.mkdir()
        for branch in current["network_feedback"]["branches"]:
            branch["delta_t_penalty_yuan_per_c_on_hour"] = case["soft_penalty_yuan_per_c_on_hour"]
        (folder / "config.yaml").write_text(yaml.safe_dump(current, sort_keys=False), encoding="utf-8")
        def checkpoint(record, solution, dispatch):
            iteration = folder / f"iteration_{record['iteration']:02d}"
            iteration.mkdir()
            save(iteration / "record.json", record)
            if solution is not None:
                save(iteration / "summary.json", solution.summary)
                np.save(iteration / "solution_values.npy", solution.values, allow_pickle=False)
                solution.dispatch.to_csv(iteration / "core_dispatch.csv", index=False)
                dispatch.to_csv(iteration / "mode_dispatch.csv", index=False)
        try:
            result = solve_with_network_feedback(current, frame, time_limit=60, mip_gap=0, on_iteration=checkpoint)
            save(folder / "history.json", result.history)
            save(folder / "catalog.json", result.catalog)
            save(folder / "audit.json", result.audit)
            save(folder / "summary.json", result.solution.summary)
            np.save(folder / "solution_values.npy", result.solution.values, allow_pickle=False)
            save(folder / "variable_names.json", result.model.problem.names)
            result.solution.dispatch.to_csv(folder / "core_dispatch.csv", index=False)
            result.mode_dispatch.to_csv(folder / "mode_dispatch.csv", index=False)
            bill = result.mode_dispatch.groupby("time_index").apply(lambda x: x.source_heat_kw.sum() + x.pump_kw.sum(), include_groups=False).to_numpy() @ frame.electricity_price.to_numpy()
            summary.append({"case": case["name"], "status": "accepted", "iterations": len(result.history),
                "reference_electricity_bill_yuan": float(bill), "objective_with_design_penalty_yuan": result.solution.summary["objective_yuan_per_year"], **result.audit})
        except Exception as error:
            failure = {"case": case["name"], "type": type(error).__name__, "reason": str(error), "history": getattr(error, "history", None)}
            save(folder / "failure.json", failure)
            failures.append(failure)
        save(out / "comparison.json", summary)
        save(out / "failures.json", failures)
    verify(out)
    save(out / "manifest.json", {"all_cases_passed": len(summary) == len(protocol["cases"]) and not failures,
        "cases": summary, "failures": failures, "field_validation": False,
        "files": {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*")) if p.is_file()}})
    print(json.dumps({"accepted": len(summary), "failures": failures}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "verify"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    {"freeze": freeze, "run": run, "verify": verify}[args.action](args.out.resolve())
