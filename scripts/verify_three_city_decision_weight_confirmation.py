"""Independent saved-output audit for the frozen development weight experiment.

This verifier never imports its producer, calls an optimizer, or substitutes a
different controller. Physical states are reconstructed by full convolution.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
METRICS = ("energy_kwh", "bill_yuan", "carbon_kg")
HARD_METHODS = {"hard_energy_minimum_W1", "hard_energy_minimum_L1", "price_paired_L1", "hard_W1_candidate_L1_cap"}


def fs(path):
    path = str(Path(path).resolve())
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        path = "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return Path(path)


def read(path):
    return json.loads(fs(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(fs(path).read_bytes()).hexdigest()


def csv(path):
    return pd.read_csv(fs(path))


class Checks:
    def __init__(self):
        self.errors = []
        self.count = 0

    def check(self, condition, label):
        self.count += 1
        if not bool(condition):
            self.errors.append(label)

    def same(self, actual, expected, label):
        if isinstance(expected, dict):
            self.check(isinstance(actual, dict), label + ":dict")
            if isinstance(actual, dict):
                for key, value in expected.items():
                    self.check(key in actual, label + ":missing:" + key)
                    if key in actual:
                        self.same(actual[key], value, label + ":" + key)
        elif isinstance(expected, (list, tuple)):
            self.check(isinstance(actual, (list, tuple)) and len(actual) == len(expected), label + ":length")
            if isinstance(actual, (list, tuple)) and len(actual) == len(expected):
                for i, value in enumerate(expected):
                    self.same(actual[i], value, f"{label}:{i}")
        elif expected is None or isinstance(expected, (str, bool, int, set)):
            self.check(actual == expected, label)
        else:
            try:
                self.check(np.isfinite(actual) and np.isclose(actual, expected, rtol=1e-10, atol=1e-7), label)
            except (TypeError, ValueError):
                self.check(False, label + ":number")

    def result(self):
        return {"passed": not self.errors, "errors": self.errors, "check_count": self.count}


def audit_trajectory(config, frame, trajectory, policy):
    """Rebuild RC states, frozen thermostat action and hourly accounting."""
    checks = Checks()
    try:
        bld = config["building_thermal"]["buildings"][0]
        source = config["sources"][0]
        checks.check(len(config["building_thermal"]["buildings"]) == len(config["sources"]) == 1, "single_zone_source")
        checks.check(not config["pipes"] and not config["storages"], "no_network_storage")
        checks.check(config["building_thermal"]["boundary_mode"] == "fixed" and config["building_thermal"]["simultaneous_mode"] == "exclusive", "fixed_exclusive_boundary")
        checks.check(bld["min_temperature_c"] == bld["initial_temperature_c"] == bld["final_temperature_c"] == 20 and bld["max_temperature_c"] == 27, "registered_temperature_contract")
        checks.check(bld["heating_enabled"] and bld["cooling_enabled"] and (frame[bld["heating_service_column"]] == 1).all(), "full_service")
        checks.check(source["efficiency"] == 1 and source["max_new_capacity_kw"] == 0 and config["planning"]["time_step_hours"] == 1 and config["multi_energy_services"]["cooling"]["sources"][source["name"]]["eer"] == 1, "ideal_hourly_conversion")
        stamps = pd.DatetimeIndex(pd.to_datetime(frame.time))
        actual_clock = pd.DatetimeIndex(pd.to_datetime(trajectory.time))
        checks.check(stamps.tz is not None and stamps[0].utcoffset() is not None, "aware_clock")
        checks.check(stamps.is_unique and stamps.is_monotonic_increasing and np.all(np.diff(stamps.asi8) == 3600 * 10**9), "consecutive_hour_clock")
        checks.check(actual_clock.equals(stamps), "trajectory_clock")
        checks.check(len(frame) == len(trajectory) and len(frame) > 0, "trajectory_length")
        if len(frame) != len(trajectory) or not len(frame):
            return checks.result()
        n = len(frame)
        heating, cooling = trajectory.heat_kw.to_numpy(float), trajectory.cool_kw.to_numpy(float)
        start, end = trajectory.T_start.to_numpy(float), trajectory.T_end.to_numpy(float)
        outdoor, internal, solar = (frame[k].to_numpy(float) for k in ("outdoor_temp", bld["internal_gain_column"], bld["solar_gain_column"]))
        price, factor = frame.electricity_price.to_numpy(float), frame.grid_emission_factor.to_numpy(float)
        checks.check(np.isfinite(np.r_[heating, cooling, start, end, outdoor, internal, solar, price, factor]).all(), "finite_values")
        checks.check((price >= 0).all() and (factor >= 0).all() and frame.tariff_bucket.isin(["valley", "flat", "peak", "sharp"]).all(), "economic_inputs")
        h, c = float(bld["heat_loss_kw_per_c"]), float(bld["thermal_capacity_kwh_per_c"])
        checks.check(h > 0 and c > 0, "positive_H_C")
        a, b = np.exp(-h/c), -np.expm1(-h/c)/h
        forcing = (1-a)*outdoor + b*(heating-cooling+internal+solar)
        rebuilt = 20*a**np.arange(1, n+1) + np.convolve(forcing, a**np.arange(n), mode="full")[:n]
        checks.check(np.max(abs(rebuilt-end)) <= 1e-6, "continuous_RC_convolution")
        checks.check(np.max(abs(start-np.r_[20., rebuilt[:-1]])) <= 1e-6, "state_continuity")
        checks.check(end.min() >= 20-1e-6 and end.max() <= 27+1e-6 and abs(end[-1]-20) <= 1e-6, "comfort_terminal")
        checks.check(heating.min() >= -1e-7 and cooling.min() >= -1e-7, "nonnegative_powers")
        checks.check(not ((heating > 1e-7) & (cooling > 1e-7)).any(), "exclusive_heat_cool")
        energy = heating+cooling
        checks.check(heating.max() <= bld["max_heat_kw"]+1e-7 and cooling.max() <= bld["max_cooling_kw"]+1e-7 and energy.max() <= source["existing_capacity_kw"]+1e-7, "capacity")
        passive = a*start + (1-a)*outdoor + b*(internal+solar)
        target = np.where(frame.tariff_bucket.eq("valley").to_numpy() & np.isin(stamps.hour, policy["local_hours"]), 20+policy["delta_c"], 20.).astype(float)
        target[-1] = 20.
        signed = np.where(passive < target, (target-passive)/b, np.where(passive > 27, (27-passive)/b, 0.))
        signed[-1] = (20-passive[-1])/b
        checks.check(np.allclose(heating, np.maximum(signed, 0), rtol=1e-10, atol=1e-7) and np.allclose(cooling, np.maximum(-signed, 0), rtol=1e-10, atol=1e-7), "frozen_controller")
        checks.check(np.allclose(trajectory.passive_temperature_c, passive, rtol=0, atol=1e-7), "passive_temperature")
        checks.check(np.allclose(trajectory.thermostat_target_c, target, rtol=0, atol=1e-8), "thermostat_clock_window")
        checks.check(np.array_equal(trajectory.terminal_override.to_numpy(), np.arange(n) == n-1), "terminal_override")
        checks.check(np.array_equal(trajectory.time_index, np.arange(n)) and np.array_equal(trajectory.original_day_index, np.arange(n)//24), "hour_day_identity")
        checks.check((trajectory.policy_id == policy["policy_id"]).all(), "policy_identity")
        ledger = {"energy_kwh": energy, "bill_yuan": energy*price, "carbon_kg": energy*factor}
        for key, values in ledger.items():
            checks.check(np.allclose(trajectory[key], values, rtol=1e-10, atol=1e-7), "hourly_ledger:"+key)
        daily = pd.DataFrame(ledger).groupby(np.arange(n)//24).sum()
        return {**checks.result(), "totals": {k: float(v.sum()) for k, v in ledger.items()},
                "daily": daily, "maximum_temperature_residual_c": float(np.max(abs(rebuilt-end)))}
    except Exception as error:
        checks.check(False, f"trajectory_exception:{type(error).__name__}:{error}")
        return checks.result()


def cap_pass(value, cap, protocol):
    return bool(value <= cap + protocol["cap_absolute_tolerance"] + protocol["cap_relative_tolerance"]*max(abs(value), abs(cap), 1))


def decision_rows(rows, multiplier, baseline, factor, protocol):
    """Independent selection audit; actual data only enter actual gates/oracle."""
    energy_cap, carbon_cap = baseline*multiplier, baseline*multiplier*factor
    candidates = []
    for row in rows:
        item = {**row, "budget_multiplier": multiplier, "energy_cap_kwh": energy_cap, "carbon_cap_kg": carbon_cap}
        item["estimated_feasible"] = all(cap_pass(row["estimated_"+metric], cap, protocol) for metric, cap in [("energy_kwh", energy_cap), ("carbon_kg", carbon_cap)])
        item["actual_feasible"] = bool(row["service_passed"] and all(cap_pass(row["actual_"+metric], cap, protocol) for metric, cap in [("energy_kwh", energy_cap), ("carbon_kg", carbon_cap)]))
        candidates.append(item)
    def select(prefix):
        feasible = [r for r in candidates if r[prefix+"_feasible"]]
        if not feasible:
            return None
        bill = min(r[prefix+"_bill_yuan"] for r in feasible)
        return min([r for r in feasible if r[prefix+"_bill_yuan"] <= bill+protocol["bill_tie_absolute_tolerance"]], key=lambda r: r["policy_id"])
    selected, oracle = select("estimated"), select("actual")
    qualified = selected is not None and selected["actual_feasible"] and oracle is not None
    regret = selected["actual_bill_yuan"]-oracle["actual_bill_yuan"] if qualified else None
    first = rows[0]
    record = {"city": first["city"], "method_id": first["method_id"], "budget_multiplier": multiplier,
        "energy_cap_kwh": energy_cap, "carbon_cap_kg": carbon_cap,
        "selected_policy_id": None if selected is None else selected["policy_id"], "oracle_policy_id": None if oracle is None else oracle["policy_id"],
        "selection_status": "qualified" if qualified else "no_estimated_feasible_policy" if selected is None else "selected_actual_infeasible" if not selected["actual_feasible"] else "no_actual_feasible_oracle",
        "qualified": qualified, "same_oracle_choice": bool(qualified and selected["policy_id"] == oracle["policy_id"]),
        "regret_yuan": regret, "relative_regret": regret/oracle["actual_bill_yuan"] if qualified and oracle["actual_bill_yuan"] else None,
        "actual_energy_excess_kwh": max(selected["actual_energy_kwh"]-energy_cap, 0.) if selected else None,
        "actual_carbon_excess_kg": max(selected["actual_carbon_kg"]-carbon_cap, 0.) if selected else None,
        "false_feasible_policies": sum(r["estimated_feasible"] and not r["actual_feasible"] for r in candidates),
        "false_infeasible_policies": sum(not r["estimated_feasible"] and r["actual_feasible"] for r in candidates)}
    return candidates, record


def rank_rows(rows, protocol):
    actual = sorted(rows, key=lambda r: (r["actual_bill_yuan"], r["policy_id"]))
    predicted = sorted(rows, key=lambda r: (r["estimated_bill_yuan"], r["policy_id"]))
    rank_a, rank_p = {r["policy_id"]: i for i, r in enumerate(actual)}, {r["policy_id"]: i for i, r in enumerate(predicted)}
    pairs = [(l["actual_bill_yuan"]-r["actual_bill_yuan"], l["estimated_bill_yuan"]-r["estimated_bill_yuan"]) for i, l in enumerate(rows) for r in rows[i+1:]]
    tolerance = protocol["bill_tie_absolute_tolerance"]
    strict = [(a, p) for a, p in pairs if abs(a) > tolerance]
    return {"actual_order": [r["policy_id"] for r in actual], "estimated_order": [r["policy_id"] for r in predicted],
            "strict_actual_pairs": len(strict), "strict_inversions": sum(abs(p)>tolerance and a*p<0 for a, p in strict),
            "false_estimated_ties": sum(abs(p)<=tolerance for a, p in strict),
            "mean_absolute_rank_error": float(np.mean([abs(rank_a[p]-rank_p[p]) for p in rank_a]))}


def normalized_w1(observed, profiles, weights):
    """CDF integral with independently aggregated probability masses."""
    observed, selected = np.ravel(observed), np.ravel(profiles)
    support = np.unique(np.r_[observed, selected])
    full_mass = np.bincount(np.searchsorted(support, observed), minlength=len(support))/len(observed)
    selected_mass = np.bincount(np.searchsorted(support, selected), weights=np.repeat(weights, profiles.shape[1]), minlength=len(support))/(profiles.shape[1]*sum(weights))
    return float(np.diff(support) @ abs(np.cumsum(full_mass-selected_mass)[:-1])/observed.mean())


def moment_record(daily, selected, weights, bound, tolerance, unit):
    target, estimate = float(daily.sum()), float(daily[selected] @ weights)
    allowance = tolerance*max(abs(target), len(daily)*float(abs(daily[selected]).max()), 1.)
    residual = estimate-target
    violation = max(abs(residual)-bound, 0.)
    return {"unit": unit, "target": target, "estimate": estimate, "signed_residual": residual,
            "relative_residual": residual/abs(target) if target else None, "absolute_error_bound": bound,
            "bound_violation": violation, "numerical_acceptance_allowance": allowance,
            "passes_with_declared_numerical_tolerance": violation <= allowance}


def audit_calibration(frame, selected, result, method, protocol, candidate=None):
    checks = Checks()
    if result["status"] != "ok":
        checks.check(result.get("weights") is None, "failed_calibration_has_no_weights")
        if method == "hard_W1_candidate_L1_cap" and candidate is not None and candidate["status"] != "ok":
            checks.check(result["status"] == "skipped_candidate_failure", "dependency_failure_explicit")
        return checks.result()
    try:
        weights = np.asarray(result["weights"], dtype=float)
        initial, lower = selected.period_weight.to_numpy(float), selected.lower_bound.to_numpy(float)
        indices = selected.original_day_index.to_numpy(int)
        q = frame[protocol["reference_column"]].to_numpy(float).reshape(-1, 24)
        p = frame.electricity_price.to_numpy(float).reshape(-1, 24)
        hours = pd.DatetimeIndex(pd.to_datetime(frame.time)).hour.to_numpy().reshape(-1, 24)
        days, tolerance = len(q), protocol["calibration"]["numerical_tolerance"]
        checks.check(weights.shape == initial.shape and np.isfinite(weights).all(), "weight_shape_finiteness")
        checks.check(abs(weights.sum()-days) <= tolerance*max(days, 1) and np.maximum(lower-weights, 0).max() <= tolerance, "day_floor_constraints")
        if method == "frequency":
            checks.check(np.array_equal(weights, initial), "frequency_unchanged")
        displacement = float(abs(weights-initial).sum())
        checks.same(result["weight_L1_displacement_days"], displacement, "displacement")
        checks.same(result["normalized_reference_W1"], normalized_w1(q, q[indices], weights), "W1")
        energy = moment_record(q.sum(axis=1), indices, weights, 0., tolerance, "kWh_th")
        moments = {"price_product": moment_record((q*p).sum(axis=1), indices, weights,
            protocol["calibration"]["price_product_relative_band"]*abs(float((q*p).sum())), tolerance, "currency*kWh_th/kWh_e")}
        for name, registered_hours in protocol["calibration"]["windows"].items():
            daily = (q*np.isin(hours, registered_hours)).sum(axis=1)
            moments["window_energy:"+name] = moment_record(daily, indices, weights,
                protocol["calibration"]["window_energy_relative_band"]*abs(float(daily.sum())), tolerance, "kWh_th")
        expected_diag = {"reference_energy": energy, "moments": moments,
            "day_conservation_signed_residual": float(weights.sum()-days), "day_numerical_acceptance_allowance": tolerance*max(days, 1),
            "maximum_lower_bound_violation_days": float(np.maximum(lower-weights, 0).max()), "lower_bound_numerical_acceptance_allowance_days": tolerance,
            "objective_weight_L1_over_days": displacement/days,
            "passes": bool(abs(weights.sum()-days)<=tolerance*max(days, 1) and np.maximum(lower-weights, 0).max()<=tolerance and energy["passes_with_declared_numerical_tolerance"] and all(r["passes_with_declared_numerical_tolerance"] for r in moments.values()))}
        checks.same(result["paired_diagnostics"], expected_diag, "paired_diagnostics")
        if method in HARD_METHODS:
            checks.check(energy["passes_with_declared_numerical_tolerance"], "hard_reference_energy")
        if method == "price_paired_L1":
            checks.check(expected_diag["passes"], "candidate_joint_bands")
        if method == "hard_energy_minimum_L1":
            daily = (q[indices]*p[indices]).sum(axis=1)
            fixed, remainder = float(lower @ daily), days-float(lower.sum())
            endpoints = [fixed+remainder*float(daily.min()), fixed+remainder*float(daily.max())]
            target = float((q*p).sum())
            checks.same(result["vacuous_price_band"], max(abs(value-target) for value in endpoints), "vacuous_price_band")
            checks.same(result["vacuous_price_envelope"], {"minimum": endpoints[0], "maximum": endpoints[1], "full_target": target, "unit": "currency*kWh_th/kWh_e"}, "vacuous_price_envelope")
        if method == "hard_W1_candidate_L1_cap":
            checks.check(candidate is not None and candidate["status"] == "ok", "cap_control_candidate_dependency")
            candidate_displacement = float(abs(np.asarray(candidate["weights"])-initial).sum())
            checks.same(result["candidate_displacement_days"], candidate_displacement, "candidate_displacement")
            checks.same(result["l1_cap_days"], candidate_displacement+tolerance*days, "declared_l1_cap")
            checks.check(displacement <= result["l1_cap_days"]+tolerance*days, "l1_cap_constraint")
        if "solver_status" in result:
            checks.check(result["solver_status"] == 0, "successful_solver_status")
        if "solver_objective" in result:
            checks.same(result["solver_objective"], displacement/days, "L1_objective")
    except Exception as error:
        checks.check(False, f"calibration_exception:{type(error).__name__}:{error}")
    return checks.result()


def indexed(records, keys, checks, label):
    result = {}
    for row in records:
        identity = tuple(row[k] for k in keys)
        checks.check(identity not in result, label+":duplicate:"+str(identity))
        result[identity] = row
    return result


def audit(out, *, check_hashes=True):
    out = fs(out)
    checks = Checks()
    trajectory_reports, calibration_reports = {}, {}
    try:
        protocol, manifest = read(out/"protocol.json"), read(out/"manifest.json")
        if check_hashes:
            for name, digest in read(out/"protocol_lock.json").items():
                checks.same(sha(out/name), digest, "frozen_hash:"+name)
            for section in ("source_sha256", "code_sha256"):
                for name, digest in protocol[section].items():
                    checks.same(sha(ROOT/name), digest, section+":"+name)
            for name, version in protocol["versions"].items():
                checks.same(importlib.metadata.version(name), version, "version:"+name)
            for name, digest in manifest["files"].items():
                checks.same(sha(out/name), digest, "artifact_hash:"+name)
            checks.same(set(manifest["files"]), {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name != "manifest.json"}, "manifest_population")
        checks.same(manifest["protocol_sha256"], sha(out/"protocol.json"), "manifest_protocol")
        checks.check(protocol["outcome_use"]["three_cities_already_seen_development"] and not protocol["outcome_use"]["fresh_holdout"] and not protocol["outcome_use"]["adaptive_tuning"], "development_scope")
        checks.check(protocol["hours_per_city"] == 2880 and protocol["days_per_city"] == 120 and len(protocol["cities"]) == 3 and len(protocol["methods"]) == 6 and len(protocol["policies"]) == 9 and protocol["budget_multipliers"] == [1., 1.02, 1.05, 1.1], "frozen_population")
        config = read(out/"inputs/config.json")
        truth = indexed(read(out/"truth.json"), ("city", "policy_id"), checks, "truth")
        predictions = indexed(read(out/"predictions.json"), ("city", "method_id", "policy_id"), checks, "predictions")
        candidates = indexed(read(out/"candidates.json"), ("city", "method_id", "policy_id", "budget_multiplier"), checks, "candidates")
        selections = indexed(read(out/"selections.json"), ("city", "method_id", "budget_multiplier"), checks, "selections")
        rankings = indexed(read(out/"rankings.json"), ("city", "method_id"), checks, "rankings")
        expected_predictions, expected_candidates, expected_selections, expected_rankings = {}, {}, {}, {}
        actual_truth, calibrations, expected_failures = {}, {}, []
        completion = read(out/"calibration_complete.json")
        checks.check(completion["policy_truth_generated"] is False, "calibration_precedes_truth_receipt")
        start = read(out/"run_started.json")
        checks.check(datetime.fromisoformat(protocol["frozen_utc"]) <= datetime.fromisoformat(start["utc"]) <= datetime.fromisoformat(completion["utc"]) <= datetime.fromisoformat(manifest["completed_utc"]), "receipt_time_order")
        for city in protocol["cities"]:
            frame, selected = csv(out/f"inputs/{city}_hourly.csv"), csv(out/f"inputs/{city}_selected_days.csv")
            checks.check(len(frame) == 2880 and len(frame)//24 == 120, city+":chronology_size")
            indices = selected.original_day_index.to_numpy(int)
            checks.check(len(set(indices)) == len(indices) and (indices>=0).all() and (indices<120).all(), city+":selected_actual_days")
            checks.check(np.array_equal(selected.lower_bound, np.where(selected.is_extreme_day, 1., 1e-6)), city+":common_floors")
            checks.check(abs(selected.period_weight.sum()-120) <= 1e-8*120, city+":original_weight_days")
            checks.same(selected.fillna("").to_dict("records"), protocol["selected_actual_days"][city], city+":frozen_selected_record")
            representative = csv(out/f"inputs/{city}_representative_hours.csv")
            for i, selected_row in selected.iterrows():
                block = representative[representative.representative_day == selected_row.representative_day]
                original = frame.iloc[int(selected_row.original_day_index)*24:(int(selected_row.original_day_index)+1)*24]
                checks.check(len(block) == 24 and np.array_equal(block.original_time.to_numpy(), original.time.to_numpy()) and np.array_equal(block.time.to_numpy(), original.time.to_numpy()), city+":selected_hour_clock:"+str(i))
                for column in [protocol["reference_column"], "outdoor_temp", "electricity_price"]:
                    checks.check(np.array_equal(block[column].to_numpy(), original[column].to_numpy()), city+":selected_raw_profile:"+column+":"+str(i))
            for method in protocol["methods"]:
                calibration = read(out/f"calibration/{city}_{method}.json")
                calibrations[(city, method)] = calibration
                checks.same({k: calibration[k] for k in ("city", "method_id")}, {"city": city, "method_id": method}, city+":calibration_identity:"+method)
                checks.same(sha(out/f"calibration/{city}_{method}.json"), completion["weight_sha256"][f"{city}_{method}.json"], city+":weight_completion_hash:"+method)
                resources = calibration["resources"]
                checks.check(np.isfinite(resources["wall_seconds"]) and resources["wall_seconds"] >= 0, city+":nonnegative_calibration_time:"+method)
                if method != "frequency":
                    checks.same(resources, {"limit_seconds": protocol["resources"]["per_method_city_wall_seconds"], "limit_RSS_bytes": protocol["resources"]["per_worker_RSS_limit_bytes"], "numerical_threads": 1, "startup_and_verification_included": True}, city+":resource_contract:"+method)
                    affinity = calibration.get("worker_cpu_affinity", {})
                    if affinity.get("supported"):
                        checks.check(len(affinity["effective_logical_CPUs"]) == 1, city+":single_cpu:"+method)
                if method == "hard_W1_candidate_L1_cap":
                    dependency_time = calibrations[(city, "price_paired_L1")]["resources"]["wall_seconds"]
                    checks.same(resources, {"dependency_candidate_wall_seconds": dependency_time, "own_plus_dependency_wall_seconds": resources["wall_seconds"]+dependency_time}, city+":dependency_time")
                report = audit_calibration(frame, selected, calibration, method, protocol, calibrations.get((city, "price_paired_L1")))
                calibration_reports[city+":"+method] = report
                checks.check(report["passed"], city+":calibration:"+method+":"+";".join(report["errors"]))
            policy_rows = []
            for policy in protocol["policies"]:
                identity = (city, policy["policy_id"])
                recorded = truth[identity]
                if recorded["status"] != "ok":
                    checks.check(recorded["status"] == "policy_failure" and bool(recorded.get("reason")), city+":explicit_policy_failure")
                    expected_failures.append(recorded)
                    continue
                trajectory = csv(out/f"trajectories/{city}_policy_{policy['policy_id']}.csv")
                report = audit_trajectory(config, frame, trajectory, policy)
                daily = report.pop("daily", None)
                trajectory_reports[city+":"+str(policy["policy_id"])] = report
                checks.check(report["passed"], city+":trajectory:"+str(policy["policy_id"])+":"+";".join(report["errors"]))
                if daily is None:
                    continue
                recorded_daily = csv(out/f"daily/{city}_policy_{policy['policy_id']}.csv")
                checks.check(np.array_equal(recorded_daily.original_day_index, np.arange(120)), city+":daily_identity")
                for metric in METRICS:
                    checks.check(np.allclose(recorded_daily[metric], daily[metric], rtol=1e-10, atol=1e-7), city+":daily_ledger:"+metric)
                row = {"city": city, "policy_id": policy["policy_id"], "policy_name": policy["name"], "status": "ok", "service_passed": report["passed"], **{"actual_"+k: report["totals"][k] for k in METRICS}}
                actual_truth[identity] = row
                checks.same(recorded, row, city+":truth:"+str(policy["policy_id"]))
                policy_rows.append((row, daily))
            if len(policy_rows) != len(protocol["policies"]):
                for method in protocol["methods"]:
                    for multiplier in protocol["budget_multipliers"]:
                        expected_selections[(city, method, multiplier)] = {"city": city, "method_id": method, "budget_multiplier": multiplier, "selection_status": "policy_library_failed", "qualified": False, "same_oracle_choice": False, "selected_policy_id": None, "oracle_policy_id": None, "regret_yuan": None, "relative_regret": None}
                continue
            baseline = actual_truth[(city, 0)]["actual_energy_kwh"]
            factor = float(frame.grid_emission_factor.iloc[0])
            checks.check(frame.grid_emission_factor.nunique() == 1, city+":fixed_carbon_factor")
            for method in protocol["methods"]:
                calibration, rows = calibrations[(city, method)], []
                if calibration["status"] != "ok":
                    expected_failures.append({"city": city, "method_id": method, "status": calibration["status"], "reason": calibration.get("reason")})
                    for multiplier in protocol["budget_multipliers"]:
                        record = {"city": city, "method_id": method, "budget_multiplier": multiplier, "selection_status": "calibration_failed", "qualified": False, "same_oracle_choice": False, "selected_policy_id": None, "oracle_policy_id": None, "regret_yuan": None, "relative_regret": None}
                        expected_selections[(city, method, multiplier)] = record
                    continue
                for actual, daily in policy_rows:
                    row = {**actual, "method_id": method}
                    for metric in METRICS:
                        estimate = float(np.asarray(calibration["weights"]) @ daily.loc[indices, metric].to_numpy())
                        error = estimate-actual["actual_"+metric]
                        row.update({"estimated_"+metric: estimate, "signed_error_"+metric: error, "absolute_percentage_error_"+metric: abs(error)/abs(actual["actual_"+metric]) if actual["actual_"+metric] else None})
                    rows.append(row)
                    expected_predictions[(city, method, row["policy_id"])] = row
                expected_rankings[(city, method)] = {"city": city, "method_id": method, **rank_rows(rows, protocol)}
                for multiplier in protocol["budget_multipliers"]:
                    budget_rows, selection = decision_rows(rows, multiplier, baseline, factor, protocol)
                    expected_selections[(city, method, multiplier)] = selection
                    if selection["qualified"]:
                        checks.check(selection["regret_yuan"] >= -protocol["bill_tie_absolute_tolerance"] and np.isfinite(selection["regret_yuan"]), city+":nonnegative_finite_regret")
                    for row in budget_rows:
                        expected_candidates[(city, method, row["policy_id"], multiplier)] = row
        checks.same(set(truth), {(c, p["policy_id"]) for c in protocol["cities"] for p in protocol["policies"]}, "truth_population")
        checks.same(read(out/"failures.json"), expected_failures, "failure_population")
        for actual, expected, label in [(predictions, expected_predictions, "predictions"), (candidates, expected_candidates, "candidates"), (selections, expected_selections, "selections"), (rankings, expected_rankings, "rankings")]:
            checks.same(set(actual), set(expected), label+":population")
            for identity, row in expected.items():
                if identity in actual:
                    checks.same(actual[identity], row, label+":"+str(identity))
        summary = []
        for method in protocol["methods"]:
            rows = [r for r in expected_predictions.values() if r["method_id"] == method]
            choices = [r for r in expected_selections.values() if r["method_id"] == method]
            qualified = [r for r in choices if r["qualified"]]
            regrets = [r["relative_regret"] for r in qualified if r["relative_regret"] is not None]
            energy, bill = ([r["absolute_percentage_error_"+metric] for r in rows] for metric in ("energy_kwh", "bill_yuan"))
            summary.append({"method_id": method, "successful_calibrations": sum(calibrations[(c, method)]["status"] == "ok" for c in protocol["cities"]), "prediction_rows": len(rows), "qualified_choices": len(qualified), "selection_rows": len(choices),
                "no_choice": sum(r["selection_status"] == "no_estimated_feasible_policy" for r in choices), "actual_infeasible_choices": sum(r["selection_status"] == "selected_actual_infeasible" for r in choices), "same_oracle_choices": sum(r["same_oracle_choice"] for r in choices),
                "worst_relative_regret": max(regrets) if regrets else None, "mean_relative_regret": float(np.mean(regrets)) if regrets else None,
                "energy_MAPE": float(np.mean(energy)) if energy else None, "energy_max_APE": max(energy) if energy else None, "bill_MAPE": float(np.mean(bill)) if bill else None, "bill_max_APE": max(bill) if bill else None,
                "strict_rank_inversions": sum(r["strict_inversions"] for r in expected_rankings.values() if r["method_id"] == method), "calibration_wall_seconds": sum(calibrations[(c, method)]["resources"]["wall_seconds"] for c in protocol["cities"]), "calibration_wall_with_dependency_seconds": sum(calibrations[(c, method)]["resources"].get("own_plus_dependency_wall_seconds", calibrations[(c, method)]["resources"]["wall_seconds"]) for c in protocol["cities"])})
        recommendation = min(summary, key=lambda r: (-r["qualified_choices"], *(r[k] if r[k] is not None else float("inf") for k in ["worst_relative_regret", "mean_relative_regret", "energy_max_APE", "bill_MAPE"]), protocol["methods"].index(r["method_id"]))) ["method_id"]
        checks.same(read(out/"summary.json"), {"methods": summary, "development_recommendation": recommendation, "scope": protocol["experiment_identity"], "positive_external_or_investment_claim": False}, "summary")
        checks.same(manifest["development_recommendation"], recommendation, "manifest_recommendation")
        runtime = read(out/"runtime.json")
        successful = len(actual_truth)
        checks.same(runtime, {"full_policy_count": successful, "full_simulated_hours": successful*2880, "shared_full_truth_cost_charged_to_every_method": True, "additional_selection_lookups_not_simulation_speedup": True, "numerical_threads": 1, "sequential_calibration_workers": True}, "runtime_population")
        checks.check(np.isfinite(runtime["elapsed_wall_seconds"]) and runtime["elapsed_wall_seconds"] >= runtime["shared_policy_simulation_and_scoring_wall_seconds"] >= 0, "timing_consistency")
    except Exception as error:
        checks.check(False, f"audit_exception:{type(error).__name__}:{error}")
    return {**checks.result(), "hashes_checked": check_hashes, "audit_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "independent saved-output development diagnostic; no optimizer/global optimum/new holdout claim",
            "trajectory_reports": trajectory_reports, "calibration_reports": calibration_reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report_path = fs(args.report)
    if report_path.exists() or report_path.is_relative_to(fs(args.out)):
        parser.error("report must be a new file outside the frozen run")
    report = audit(args.out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "check_count": report["check_count"], "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
