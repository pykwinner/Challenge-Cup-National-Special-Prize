"""Frozen, finite development experiment: actual-day weights and policy decisions.

No investment or simulation speedup. All policies carry states over the same
2880 hours; calibration workers never read policy outcomes. Run freeze first.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

# Set before importing numerical libraries, also propagated to each worker.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import numpy as np
import pandas as pd
import psutil
from scipy.optimize import linprog
from scipy.sparse import csr_matrix, eye, hstack, vstack
from scipy.stats import wasserstein_distance
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from heatplan.scenarios import extreme_aware_typical_days, calibrate_representative_day_weights
from heatplan.hard_weight_calibration import minimum_w1_energy_weights
from heatplan.decision_weight_calibration import calibrate_price_operation_weights, diagnose_decision_weights
from reproduce_admitted_reference import inspect_reference, check_chronology, filesystem_path

OWNED = ROOT / "docs/implementation_20261002_followup/parallel_stage_03/algorithm"
DATA_MANIFEST = "docs/implementation_20261002_followup/parallel_stage_02/data/manifest.json"
DATA_PROTOCOL = "docs/implementation_20261002_followup/parallel_stage_02/data/protocol.json"
REFERENCE = "heat_reference_no_gain_kw"
CITIES = ["urumqi", "guyuan", "wuhan"]
METHODS = ["frequency", "soft_energy", "hard_energy_minimum_W1", "hard_energy_minimum_L1",
           "price_paired_L1", "hard_W1_candidate_L1_cap"]
POLICIES = [{"policy_id": 0, "name": "zero", "delta_c": 0, "local_hours": []}]
for _window, _hours in [("all_valley", [0, 1, 2, 3, 4, 5, 12, 13]),
                        ("morning_end", [5]), ("midday_end", [13]), ("both_ends", [5, 13])]:
    for _delta in (1, 3):
        POLICIES.append({"policy_id": len(POLICIES), "name": f"{_window}_{_delta}",
                         "delta_c": _delta, "local_hours": _hours})
BUDGETS = [1., 1.02, 1.05, 1.10]
NUM_TOL, CAP_ATOL, CAP_RTOL, COST_TIE = 1e-8, 1e-7, 1e-9, 1e-8
WORKER_SECONDS, WORKER_RSS_BYTES = 60., 1024 ** 3
CODE = ["scripts/run_three_city_decision_weight_confirmation.py",
        "scripts/verify_three_city_decision_weight_confirmation.py",
        "scripts/report_three_city_decision_weight_confirmation.py",
        "tests/test_three_city_decision_weight_confirmation.py",
        "tests/test_verify_three_city_decision_weight_confirmation.py",
        "src/heatplan/scenarios.py", "src/heatplan/hard_weight_calibration.py",
        "src/heatplan/decision_weight_calibration.py", "scripts/reproduce_admitted_reference.py"]


def require(condition, reason):
    if not bool(condition):
        raise ValueError(reason)


def read(path):
    return json.loads(filesystem_path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(filesystem_path(path).read_bytes()).hexdigest()


def plain(value):
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


def save(path, value):
    filesystem_path(path).write_text(json.dumps(plain(value), ensure_ascii=False, indent=2,
                                              allow_nan=False) + "\n", encoding="utf-8")


def checked_output(path):
    name = str(path)
    if os.name == "nt" and name.startswith("\\\\?\\UNC\\"):
        path = "\\\\"+name[8:]
    elif os.name == "nt" and name.startswith("\\\\?\\"):
        path = name[4:]
    path = Path(path).resolve()
    require(path.is_relative_to(OWNED.resolve()) and path != OWNED.resolve(), "output must be a new subdirectory of owned stage03 algorithm")
    return filesystem_path(path)


def read_csv(path):
    return pd.read_csv(filesystem_path(path))


def freeze(out):
    freeze_started = time.perf_counter()
    require(not out.exists(), "freeze output exists; evidence must not be overwritten")
    admission, config, prices, source_report = inspect_reference(ROOT)
    data_manifest, data_protocol = read(ROOT / DATA_MANIFEST), read(ROOT / DATA_PROTOCOL)
    names = [f"data/derived/three_city_common_reference_20261002/{city}_hourly.csv" for city in CITIES]
    names += [DATA_PROTOCOL,
              "docs/implementation_20261002_followup/parallel_stage_02/data/common_physical_reference.json",
              "docs/implementation_20261002_followup/parallel_stage_02/data/field_provenance.csv"]
    sources = {name: data_manifest["artifact_sha256"][name] for name in names}
    sources[DATA_MANIFEST] = sha(ROOT / DATA_MANIFEST)
    sources["docs/implementation_20261002/data/admission_tou.json"] = sha(ROOT / "docs/implementation_20261002/data/admission_tou.json")
    sources.update({r["path"]: r["expected_sha256"] for r in source_report["checks"] if r["required_this_run"]})
    for name, digest in sources.items():
        require(sha(ROOT / name) == digest, f"source hash differs: {name}")
    physical = data_protocol["physical_reference"]
    require(config["building_thermal"] == physical["building_thermal"], "physical building differs across data/config")
    for key, value in physical["ideal_source"].items():
        require(config["sources"][0][key] == value, f"physical source differs: {key}")
    require(config["multi_energy_services"]["cooling"]["sources"][config["sources"][0]["name"]]["eer"] == 1, "ideal cooling required")
    require(prices.grid_emission_factor.nunique() == 1, "redundant energy/carbon caps need a constant factor")
    scope = {k: data_protocol["time"][k] for k in ("start_inclusive", "end_exclusive", "hours")}
    frames, representatives, selecteds = {}, {}, {}
    for city, name in zip(CITIES, names[:3]):
        frame = read_csv(ROOT / name)
        check_chronology(frame, config, scope)
        require(not {"electricity_price", "grid_emission_factor"}.intersection(frame), "climate data already contain economic fields")
        left = pd.DatetimeIndex(pd.to_datetime(frame.time, utc=True))
        right = pd.DatetimeIndex(pd.to_datetime(prices.time, utc=True))
        require(left.equals(right), "common price and climate clocks differ")
        for column in ["electricity_price", "grid_emission_factor", "tariff_month", "tariff_bucket", "tariff_source_id", "tariff_pdf_page"]:
            frame[column] = prices[column].to_numpy()
        require(np.isfinite(frame[[REFERENCE, "electricity_price"]]).all().all() and (frame[REFERENCE] >= 0).all(), "invalid exogenous calibration inputs")
        with threadpool_limits(limits=1):
            representative = extreme_aware_typical_days(frame, [REFERENCE, "outdoor_temp", "electricity_price"], [REFERENCE], 6, 42)
        representative["time"] = representative.original_time
        selected = representative.groupby("representative_day", sort=False).first().reset_index()
        selected["lower_bound"] = np.where(selected.is_extreme_day, 1., 1e-6)
        selected = selected[["representative_day", "original_day_index", "original_time", "period_weight", "is_extreme_day", "extreme_reasons", "lower_bound"]]
        frames[city], representatives[city], selecteds[city] = frame, representative, selected
    versions = {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "scipy", "scikit-learn", "psutil", "threadpoolctl"]}
    code = {name: sha(ROOT / name) for name in CODE}
    out.mkdir(parents=True)
    (out / "inputs").mkdir()
    save(out / "inputs/config.json", config)
    save(out / "source_checks.json", source_report)
    for city in CITIES:
        frames[city].to_csv(out / f"inputs/{city}_hourly.csv", index=False)
        representatives[city].to_csv(out / f"inputs/{city}_representative_hours.csv", index=False)
        selecteds[city].to_csv(out / f"inputs/{city}_selected_days.csv", index=False)
    save(out / "protocol.json", {
        "schema_version": 1, "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "experiment_identity": "seen-development_three-city_finite-policy_weight_confirmation_v1",
        "cities": CITIES, "methods": METHODS, "policies": POLICIES, "budget_multipliers": BUDGETS,
        "source_sha256": sources, "code_sha256": code, "versions": versions,
        "test_and_render_versions": {name: importlib.metadata.version(name) for name in ["pytest", "matplotlib", "PyYAML"]},
        "python_version": sys.version, "platform": platform.platform(), "hours_per_city": 2880, "days_per_city": 120,
        "reference_column": REFERENCE, "reference_kind": "thermal",
        "calibration_information": "exogenous no-gain reference heat, paired common hourly price, local05/13 binary masks only; no actual policy bills/electricity/oracle",
        "selected_actual_days": {city: selecteds[city].to_dict("records") for city in CITIES},
        "selection": {"initial_cluster_count": 6, "seed": 42, "features": [REFERENCE, "outdoor_temp", "electricity_price"],
                      "extreme_floor_days": 1., "ordinary_floor_days": 1e-6, "all_methods_same_actual_hours": True},
        "calibration": {"price_product_relative_band": 0.005, "window_energy_relative_band": 0.01,
            "windows": {"morning_end": [5], "midday_end": [13]},
            "relative_band_conversion": "fraction times absolute full exogenous moment; zero target has zero band",
            "minimum_L1_control": "same day/floor/reference-energy equalities; price band is max absolute deviation from full target at two attainable price-moment envelope endpoints under day/floor simplex; no windows; save endpoints and band in original units",
            "candidate_L1_cap_control": "hard energy minimum W1; raw sum abs(w-w_frequency) <= successful price candidate displacement + 1e-8*120; no price/windows; skip explicitly if candidate failed",
            "soft_energy": "existing SLSQP soft reference-energy error plus 1e-4 regularization; ftol1e-12/maxiter2000; not W1",
            "numerical_tolerance": NUM_TOL, "LP_time_limit_seconds": 30.,
            "legacy_hard_W1_internal_time_limit": None,
            "tie_policy": "retain first native HiGHS optimal vertex with frozen inputs, row/day order, SciPy version; save exact returned weights; multiple optimal weights possible; no outcome-driven tie resolution"},
        "policy_operation": "20..27C exclusive ideal heat/cool, 20C initial/final, 1000kW shared capacity, 1h exact RC; target20+delta only registered local hour AND valley bucket; natural drift, continuous states, last hour restore20 and bill any cooling",
        "economic_scope": "same admitted Hubei conditional TOU method price applied to every climate; not three local bills or eligibility; fixed grid factor carbon; no equipment investment/full finance",
        "budget_baseline": "actual full-season zero-policy energy times each fixed multiplier, carbon multiplied by fixed factor; shared across methods; oracle labels created after weights frozen",
        "cap_absolute_tolerance": CAP_ATOL, "cap_relative_tolerance": CAP_RTOL, "bill_tie_absolute_tolerance": COST_TIE,
        "choice_rule": "minimum estimated bill among estimated energy/carbon feasible, ties within1e-8 then lowest policy_id; independently evaluate selected actual service/caps",
        "oracle_rule": "minimum actual bill within same nine-policy library satisfying actual service/caps, same tie; not unconstrained optimized dispatch",
        "rank_rule": "ordinal sorted bill then policy_id; strict pair inversions use1e-8; also count forecast false ties",
        "regret_rule": "(selected actual bill - same-library oracle actual bill) only when selected actual feasible and service passes; relative denominator oracle bill; otherwise null, report cap excess and failures",
        "development_recommendation_rule": "max qualified choices/12, then min worst qualified relative regret, mean qualified relative regret, max all-policy energy APE, mean all-policy bill APE, fixed methods order; not external validation",
        "resources": {"sequential_workers": True, "numerical_threads": 1, "per_method_city_wall_seconds": WORKER_SECONDS,
                      "shared_input_check_selection_snapshot_wall_seconds": time.perf_counter()-freeze_started,
                      "per_worker_RSS_limit_bytes": WORKER_RSS_BYTES, "monitor_interval_seconds": 0.05,
                      "worker_cpu_affinity": "restrict to first allowed logical CPU when supported; failure recorded, not silently claimed",
                      "timing_includes_process_startup_and_verification": True, "memory_monitor_is_sampled_not_hard_OS_limit": True,
                      "dependency_cost": "W1 L1-cap method additionally requires successful price candidate; record own plus candidate wall separately; shared truth and freeze cost remain common"},
        "outcome_use": {"three_cities_already_seen_development": True, "fresh_holdout": False,
                        "adaptive_tuning": False, "simulation_speedup_claimed": False, "investment_improvement_claimed": False,
                        "all_full_simulation_cost_included": True, "caps_redundant": True,
                        "failure_fallback_or_constraint_relaxation": False}})
    files = [p for p in out.rglob("*") if p.is_file()]
    save(out / "protocol_lock.json", {p.relative_to(out).as_posix(): sha(p) for p in files})
    return {"frozen": True, "protocol_sha256": sha(out / "protocol.json"),
            "representative_day_count": {city: len(selecteds[city]) for city in CITIES}}


def verify_frozen(out, *, check_manifest=False):
    for name, digest in read(out / "protocol_lock.json").items():
        require(sha(out / name) == digest, f"frozen file changed: {name}")
    protocol = read(out / "protocol.json")
    for section in ("source_sha256", "code_sha256"):
        for name, digest in protocol[section].items():
            require(sha(ROOT / name) == digest, f"{section} changed: {name}")
    for name, version in protocol["versions"].items():
        require(importlib.metadata.version(name) == version, f"runtime version changed: {name}")
    require(protocol["methods"] == METHODS and protocol["policies"] == POLICIES and protocol["budget_multipliers"] == BUDGETS, "executable protocol mismatch")
    if check_manifest:
        for name, digest in read(out / "manifest.json")["files"].items():
            require(sha(out / name) == digest, f"run artifact changed: {name}")
    return protocol


def calibration_arrays(out, city):
    frame = read_csv(out / f"inputs/{city}_hourly.csv")
    selected = read_csv(out / f"inputs/{city}_selected_days.csv")
    q, p = frame[REFERENCE].to_numpy().reshape(-1, 24), frame.electricity_price.to_numpy().reshape(-1, 24)
    local_hours = pd.DatetimeIndex(pd.to_datetime(frame.time)).hour.to_numpy().reshape(-1, 24)
    windows = {"morning_end": (local_hours == 5).astype(float), "midday_end": (local_hours == 13).astype(float)}
    tolerances = {"price_product": 0.005 * abs(float((q*p).sum())),
                  **{f"window_energy:{name}": 0.01*abs(float((q*mask).sum())) for name, mask in windows.items()}}
    kwargs = dict(day_ids=[str(x)[:10] for x in frame.time.iloc[::24]], reference_kind="thermal",
                  moment_absolute_tolerances=tolerances, operation_windows=windows, numerical_tolerance=NUM_TOL)
    return frame, selected, q, p, windows, kwargs


def minimum_w1_with_l1_cap(observed, profiles, original, lower, l1_cap):
    """Standard CDF W1 LP with hard energy, days, floors and displacement cap."""
    values, profiles, original, lower = [np.asarray(x, dtype=float) for x in (observed, profiles, original, lower)]
    require(values.ndim == 1 and values.size > 0 and profiles.ndim == 2 and all(profiles.shape), "invalid W1 input shapes")
    m, hours = profiles.shape
    require(original.shape == lower.shape == (m,) and abs(len(values)-float(original.sum())*hours) <= NUM_TOL, "complete day/weight shape required")
    require(all(np.isfinite(x).all() and (x >= 0).all() for x in (values, profiles, original, lower)), "W1 inputs must be nonnegative finite")
    require(np.isfinite(l1_cap) and l1_cap >= 0 and values.mean() > 0, "positive reference and finite nonnegative cap required")
    days, target, mean = float(original.sum()), float(values.sum()), float(values.mean())
    support = np.unique(np.concatenate([values, profiles.ravel()]))
    left, widths = support[:-1], np.diff(support)
    n = len(left)
    cdf = np.column_stack([np.searchsorted(np.sort(row), left, side="right") for row in profiles]) / (hours * days)
    true_cdf = np.searchsorted(np.sort(values), left, side="right") / len(values)
    matrix = csr_matrix(cdf)
    z_nm, z_mn = csr_matrix((n, m)), csr_matrix((m, n))
    inequalities = vstack([
        hstack([matrix, -eye(n), z_nm]), hstack([-matrix, -eye(n), z_nm]),
        hstack([eye(m), z_mn, -eye(m)]), hstack([-eye(m), z_mn, -eye(m)]),
        csr_matrix(np.r_[np.zeros(m+n), np.ones(m)][None, :])], format="csr")
    upper = np.r_[true_cdf, -true_cdf, original, -original, l1_cap]
    equality = hstack([csr_matrix(np.vstack([profiles.sum(axis=1)/max(target, 1), np.ones(m)/max(days, 1)])),
                       csr_matrix((2, n+m))], format="csr")
    result = linprog(np.r_[np.zeros(m), widths/mean, np.zeros(m)], A_ub=inequalities, b_ub=upper,
                     A_eq=equality, b_eq=[target/max(target, 1), days/max(days, 1)],
                     bounds=[(float(x), None) for x in lower] + [(0, None)]*(n+m), method="highs",
                     options={"time_limit": 30.})
    report = {"status": "infeasible" if result.status == 2 else "solver_failure", "weights": None,
              "solver_status": int(result.status), "solver_message": str(result.message), "l1_cap_days": float(l1_cap)}
    if not result.success or result.x is None:
        return report
    weights = result.x[:m]
    w1 = wasserstein_distance(values, profiles.ravel(), v_weights=np.repeat(weights, hours)) / mean
    residuals = {"energy": float(weights @ profiles.sum(axis=1) - target), "days": float(weights.sum()-days),
                 "floor_violation": float(np.maximum(lower-weights, 0).max()),
                 "l1_cap_violation": max(float(abs(weights-original).sum()-l1_cap), 0.),
                 "objective_residual": abs(float(result.fun)-float(w1))}
    passed = (abs(residuals["energy"]) <= NUM_TOL*max(target, 1) and abs(residuals["days"]) <= NUM_TOL*max(days, 1)
              and residuals["floor_violation"] <= NUM_TOL and residuals["l1_cap_violation"] <= NUM_TOL*max(days, 1)
              and residuals["objective_residual"] <= NUM_TOL*max(w1, 1))
    return {**report, "status": "ok" if passed else "solver_failure", "weights": weights.tolist() if passed else None,
            "normalized_W1": float(w1), "native_residuals": residuals}


def vacuous_price_band(q, p, indices, lower):
    daily = (q[indices]*p[indices]).sum(axis=1)
    target = float((q*p).sum())
    remainder = len(q)-float(lower.sum())
    require(remainder >= 0, "floor/day simplex infeasible")
    fixed = float(lower @ daily)
    endpoints = [fixed+remainder*float(daily.min()), fixed+remainder*float(daily.max())]
    return max(abs(value-target) for value in endpoints), {"minimum": endpoints[0], "maximum": endpoints[1], "full_target": target,
        "unit": "currency*kWh_th/kWh_e", "scope": "attainable under only day conservation/floors; hard energy narrows this set"}


def worker(out, city, method):
    verify_frozen(out)
    affinity = {"supported": False}
    try:
        process = psutil.Process()
        allowed = process.cpu_affinity()
        process.cpu_affinity([allowed[0]])
        affinity = {"supported": True, "effective_logical_CPUs": process.cpu_affinity()}
    except (psutil.Error, AttributeError, IndexError, NotImplementedError) as error:
        affinity["reason"] = str(error)
    frame, selected, q, p, windows, kwargs = calibration_arrays(out, city)
    indices = selected.original_day_index.to_numpy(dtype=int)
    original, lower = selected.period_weight.to_numpy(), selected.lower_bound.to_numpy()
    with threadpool_limits(limits=1):
        if method == "soft_energy":
            soft = calibrate_representative_day_weights(read_csv(out / f"inputs/{city}_representative_hours.csv"), frame, [REFERENCE],
                                                       minimum_day_weight=1e-6, minimum_extreme_weight=1., regularization=1e-4)
            result = {"status": "ok", "weights": soft.weights.calibrated_weight.tolist(), "soft_summary": soft.summary}
        elif method == "hard_energy_minimum_W1":
            result = minimum_w1_energy_weights(q.ravel(), q[indices], original, lower)
        elif method == "hard_energy_minimum_L1":
            vacuous, envelope = vacuous_price_band(q, p, indices, lower)
            result = calibrate_price_operation_weights(q, p, indices, original, lower,
                **{**kwargs, "operation_windows": None, "moment_absolute_tolerances": {"price_product": vacuous}}, solver_time_limit_seconds=30.)
            result["vacuous_price_band"] = vacuous
            result["vacuous_price_envelope"] = envelope
        elif method == "price_paired_L1":
            result = calibrate_price_operation_weights(q, p, indices, original, lower, **kwargs, solver_time_limit_seconds=30.)
        elif method == "hard_W1_candidate_L1_cap":
            candidate = read(out / f"calibration/{city}_price_paired_L1.json")
            if candidate["status"] != "ok":
                result = {"status": "skipped_candidate_failure", "weights": None, "reason": candidate["status"]}
            else:
                displacement = float(abs(np.asarray(candidate["weights"])-original).sum())
                result = minimum_w1_with_l1_cap(q.ravel(), q[indices], original, lower, displacement + NUM_TOL*len(q))
                result["candidate_displacement_days"] = displacement
        else:
            raise ValueError("worker method is not frozen")
    if result["status"] == "ok":
        weights = np.asarray(result["weights"])
        # Candidate moment diagnostics are deliberately reported for every method,
        # but only the price method is required to satisfy those bands.
        result["paired_diagnostics"] = diagnose_decision_weights(q, p, indices, original, lower, weights, **kwargs)
        result["normalized_reference_W1"] = float(wasserstein_distance(q.ravel(), q[indices].ravel(), v_weights=np.repeat(weights, 24))/q.mean())
        result["weight_L1_displacement_days"] = float(abs(weights-original).sum())
    return {"city": city, "method_id": method, "worker_cpu_affinity": affinity, **result}


def limited_worker(out, city, method):
    target = out / f"calibration/{city}_{method}.json"
    command = [sys.executable, str(Path(__file__).resolve()), "worker", "--out", str(out), "--city", city, "--method", method]
    start, peak, termination = time.perf_counter(), 0, None
    with (out / f"calibration/{city}_{method}.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy())
        process = psutil.Process(child.pid)
        while child.poll() is None:
            try:
                peak = max(peak, process.memory_info().rss + sum(p.memory_info().rss for p in process.children(recursive=True)))
            except psutil.Error:
                pass
            if time.perf_counter()-start > WORKER_SECONDS or peak > WORKER_RSS_BYTES:
                termination = "wall_time_limit" if time.perf_counter()-start > WORKER_SECONDS else "sampled_RSS_limit"
                child.kill()
                child.wait()
                break
            time.sleep(0.05)
        return_code = child.wait()
    if termination or return_code != 0 or not target.is_file():
        result = {"city": city, "method_id": method, "status": "resource_limit" if termination else "worker_failure",
                  "weights": None, "reason": termination or f"worker_exit_{return_code}; see preserved log"}
    else:
        result = read(target)
    result["resources"] = {"wall_seconds": time.perf_counter()-start, "sampled_peak_RSS_bytes": peak,
                           "return_code": return_code, "termination": termination,
                           "limit_seconds": WORKER_SECONDS, "limit_RSS_bytes": WORKER_RSS_BYTES,
                           "numerical_threads": 1, "startup_and_verification_included": True}
    if method == "hard_W1_candidate_L1_cap":
        dependency = read(out / f"calibration/{city}_price_paired_L1.json")
        result["resources"]["dependency_candidate_wall_seconds"] = dependency["resources"]["wall_seconds"]
        result["resources"]["own_plus_dependency_wall_seconds"] = result["resources"]["wall_seconds"] + dependency["resources"]["wall_seconds"]
    save(target, result)
    return result


def rollout(config, frame, policy):
    require(policy in POLICIES and len(frame) > 0, "policy must be frozen and chronology nonempty")
    stamps = pd.DatetimeIndex(pd.to_datetime(frame.time))
    require(stamps.tz is not None and stamps.is_unique and stamps.is_monotonic_increasing and
            (len(stamps) == 1 or np.all(np.diff(stamps.asi8) == 3600*10**9)), "aware consecutive 1h clock required")
    bld = config["building_thermal"]["buildings"][0]
    require(len(config["building_thermal"]["buildings"]) == 1 and len(config["sources"]) == 1 and not config["pipes"] and not config["storages"], "fixed single zone required")
    require(config["building_thermal"]["boundary_mode"] == "fixed" and config["building_thermal"]["simultaneous_mode"] == "exclusive" and
            bld["min_temperature_c"] == bld["initial_temperature_c"] == bld["final_temperature_c"] == 20 and bld["max_temperature_c"] == 27,
            "registered comfort/exclusive/state boundary required")
    require(bld["heating_enabled"] and bld["cooling_enabled"] and (frame[bld["heating_service_column"]] == 1).all(), "full service required")
    source = config["sources"][0]
    require(source["efficiency"] == 1 and source["max_new_capacity_kw"] == 0 and
            config["multi_energy_services"]["cooling"]["sources"][source["name"]]["eer"] == 1 and config["planning"]["time_step_hours"] == 1, "ideal no-investment hourly scope required")
    h, c = bld["heat_loss_kw_per_c"], bld["thermal_capacity_kwh_per_c"]
    require(h > 0 and c > 0, "positive H/C required")
    a, b, temp, rows = np.exp(-h/c), -np.expm1(-h/c)/h, 20., []
    for t, row in enumerate(frame.to_dict("records")):
        drivers = [row[key] for key in ["outdoor_temp", bld["internal_gain_column"], bld["solar_gain_column"], "electricity_price", "grid_emission_factor"]]
        require(np.isfinite(drivers).all() and min(drivers[-2:]) >= 0 and row["tariff_bucket"] in {"valley", "flat", "peak", "sharp"}, "invalid physical/economic drivers")
        outdoor, internal, solar, price, factor = drivers
        passive = a*temp + (1-a)*outdoor + b*(internal+solar)
        terminal = t == len(frame)-1
        target = 20. + policy["delta_c"] if not terminal and row["tariff_bucket"] == "valley" and stamps[t].hour in policy["local_hours"] else 20.
        signed = (20-passive)/b if terminal else (target-passive)/b if passive < target else (27-passive)/b if passive > 27 else 0.
        heat, cool = max(signed, 0.), max(-signed, 0.)
        end, energy = passive+b*(heat-cool), heat+cool
        require(20-1e-8 <= end <= 27+1e-8 and heat <= bld["max_heat_kw"]+1e-8 and cool <= bld["max_cooling_kw"]+1e-8 and energy <= source["existing_capacity_kw"]+1e-8, "service/capacity failure")
        rows.append({"time": row["time"], "time_index": t, "original_day_index": t//24, "policy_id": policy["policy_id"],
                     "T_start": temp, "T_end": end, "heat_kw": heat, "cool_kw": cool, "passive_temperature_c": passive,
                     "thermostat_target_c": target, "terminal_override": terminal, "energy_kwh": energy,
                     "bill_yuan": energy*price, "carbon_kg": energy*factor})
        temp = end
    return pd.DataFrame(rows)


def cap_pass(value, cap):
    return bool(value <= cap + CAP_ATOL + CAP_RTOL*max(abs(value), abs(cap), 1))


def choice(rows, estimated):
    prefix = "estimated" if estimated else "actual"
    feasible = [r for r in rows if r[prefix+"_feasible"]]
    if not feasible:
        return None
    lowest = min(r[prefix+"_bill_yuan"] for r in feasible)
    return min((r for r in feasible if r[prefix+"_bill_yuan"] <= lowest+COST_TIE), key=lambda r: r["policy_id"])


def rank_metrics(rows):
    actual_order = sorted(rows, key=lambda r: (r["actual_bill_yuan"], r["policy_id"]))
    predicted_order = sorted(rows, key=lambda r: (r["estimated_bill_yuan"], r["policy_id"]))
    actual_ranks = {r["policy_id"]: i+1 for i, r in enumerate(actual_order)}
    predicted_ranks = {r["policy_id"]: i+1 for i, r in enumerate(predicted_order)}
    strict_pairs, inversions, false_ties = 0, 0, 0
    for i, left in enumerate(rows):
        for right in rows[i+1:]:
            truth, pred = left["actual_bill_yuan"]-right["actual_bill_yuan"], left["estimated_bill_yuan"]-right["estimated_bill_yuan"]
            if abs(truth) > COST_TIE:
                strict_pairs += 1
                inversions += abs(pred) > COST_TIE and truth*pred < 0
                false_ties += abs(pred) <= COST_TIE
    return {"actual_order": [r["policy_id"] for r in actual_order], "estimated_order": [r["policy_id"] for r in predicted_order],
            "strict_actual_pairs": strict_pairs, "strict_inversions": int(inversions), "false_estimated_ties": int(false_ties),
            "mean_absolute_rank_error": float(np.mean([abs(actual_ranks[p]-predicted_ranks[p]) for p in actual_ranks]))}


def evaluate_budget(rows, city, method, multiplier, baseline_energy, factor):
    energy_cap, carbon_cap = baseline_energy*multiplier, baseline_energy*multiplier*factor
    candidates = [{**row, "budget_multiplier": multiplier, "energy_cap_kwh": energy_cap, "carbon_cap_kg": carbon_cap,
        "estimated_feasible": cap_pass(row["estimated_energy_kwh"], energy_cap) and cap_pass(row["estimated_carbon_kg"], carbon_cap),
        "actual_feasible": bool(row["service_passed"] and cap_pass(row["actual_energy_kwh"], energy_cap) and cap_pass(row["actual_carbon_kg"], carbon_cap))} for row in rows]
    chosen, oracle = choice(candidates, True), choice(candidates, False)
    qualified = chosen is not None and chosen["actual_feasible"] and oracle is not None
    regret = chosen["actual_bill_yuan"]-oracle["actual_bill_yuan"] if qualified else None
    if regret is not None:
        require(regret >= -COST_TIE and np.isfinite(regret), "negative/nonfinite regret indicates audit error")
    record = {"city": city, "method_id": method, "budget_multiplier": multiplier, "energy_cap_kwh": energy_cap, "carbon_cap_kg": carbon_cap,
        "selected_policy_id": chosen["policy_id"] if chosen else None, "oracle_policy_id": oracle["policy_id"] if oracle else None,
        "selection_status": "qualified" if qualified else "no_estimated_feasible_policy" if chosen is None else "selected_actual_infeasible" if not chosen["actual_feasible"] else "no_actual_feasible_oracle",
        "qualified": qualified, "same_oracle_choice": bool(qualified and chosen["policy_id"] == oracle["policy_id"]),
        "regret_yuan": regret, "relative_regret": regret/oracle["actual_bill_yuan"] if qualified and oracle["actual_bill_yuan"] else None,
        "actual_energy_excess_kwh": max(chosen["actual_energy_kwh"]-energy_cap, 0.) if chosen else None,
        "actual_carbon_excess_kg": max(chosen["actual_carbon_kg"]-carbon_cap, 0.) if chosen else None,
        "false_feasible_policies": sum(r["estimated_feasible"] and not r["actual_feasible"] for r in candidates),
        "false_infeasible_policies": sum(not r["estimated_feasible"] and r["actual_feasible"] for r in candidates)}
    return candidates, record


def run(out):
    protocol = verify_frozen(out)
    require(not (out / "run_started.json").exists(), "run already started; preserve success or failure")
    save(out / "run_started.json", {"utc": datetime.now(timezone.utc).isoformat(), "protocol_sha256": sha(out / "protocol.json")})
    (out / "calibration").mkdir()
    (out / "trajectories").mkdir()
    (out / "daily").mkdir()
    started = time.perf_counter()
    calibrations = {}
    # Complete all exogenous calibrations before any new policy truth is computed.
    for city in CITIES:
        frequency_started = time.perf_counter()
        _, selected, q, p, _, kwargs = calibration_arrays(out, city)
        indices, original = selected.original_day_index.to_numpy(dtype=int), selected.period_weight.to_numpy()
        frequency = {"city": city, "method_id": "frequency", "status": "ok", "weights": original.tolist(),
            "paired_diagnostics": diagnose_decision_weights(q, p, indices, original, selected.lower_bound.to_numpy(), original, **kwargs),
            "normalized_reference_W1": float(wasserstein_distance(q.ravel(), q[indices].ravel(), v_weights=np.repeat(original, 24))/q.mean()),
            "weight_L1_displacement_days": 0., "resources": {"wall_seconds": time.perf_counter()-frequency_started, "sampled_peak_RSS_bytes": None, "no_solver": True}}
        save(out / f"calibration/{city}_frequency.json", frequency)
        calibrations[(city, "frequency")] = frequency
        for method in METHODS[1:]:
            calibrations[(city, method)] = limited_worker(out, city, method)
        print(json.dumps({"city": city, "calibration_status": {m: calibrations[(city, m)]["status"] for m in METHODS}}), flush=True)
    save(out / "calibration_complete.json", {"utc": datetime.now(timezone.utc).isoformat(), "policy_truth_generated": False,
         "weight_sha256": {p.name: sha(p) for p in (out / "calibration").glob("*.json")}})
    config = read(out / "inputs/config.json")
    predictions, candidates, selections, rankings, truth, failures = [], [], [], [], [], []
    simulation_start = time.perf_counter()
    simulated_hours = 0
    for city in CITIES:
        frame = read_csv(out / f"inputs/{city}_hourly.csv")
        selected = read_csv(out / f"inputs/{city}_selected_days.csv")
        indices = selected.original_day_index.to_numpy(dtype=int)
        factor = float(frame.grid_emission_factor.iloc[0])
        policy_truth = []
        for policy in POLICIES:
            try:
                trajectory = rollout(config, frame, policy)
                trajectory.to_csv(out / f"trajectories/{city}_policy_{policy['policy_id']}.csv", index=False)
                simulated_hours += len(trajectory)
                daily = trajectory.groupby("original_day_index", sort=True)[["energy_kwh", "bill_yuan", "carbon_kg"]].sum()
                daily.to_csv(out / f"daily/{city}_policy_{policy['policy_id']}.csv")
                row = {"city": city, "policy_id": policy["policy_id"], "policy_name": policy["name"], "status": "ok", "service_passed": True,
                       **{f"actual_{k}": float(daily[k].sum()) for k in ["energy_kwh", "bill_yuan", "carbon_kg"]}}
                policy_truth.append((row, daily))
                truth.append(row)
            except Exception as error:
                failure = {"city": city, "policy_id": policy["policy_id"], "status": "policy_failure", "reason": str(error), "traceback": traceback.format_exc()}
                truth.append(failure)
                failures.append(failure)
        if len(policy_truth) != len(POLICIES):
            for method in METHODS:
                for multiplier in BUDGETS:
                    selections.append({"city": city, "method_id": method, "budget_multiplier": multiplier,
                        "selection_status": "policy_library_failed", "qualified": False, "same_oracle_choice": False,
                        "selected_policy_id": None, "oracle_policy_id": None, "regret_yuan": None, "relative_regret": None})
            continue
        baseline = policy_truth[0][0]["actual_energy_kwh"]
        for method in METHODS:
            calibration = calibrations[(city, method)]
            if calibration["status"] != "ok":
                failures.append({"city": city, "method_id": method, "status": calibration["status"], "reason": calibration.get("reason")})
                for multiplier in BUDGETS:
                    selections.append({"city": city, "method_id": method, "budget_multiplier": multiplier,
                        "selection_status": "calibration_failed", "qualified": False, "same_oracle_choice": False,
                        "selected_policy_id": None, "oracle_policy_id": None, "regret_yuan": None, "relative_regret": None})
                continue
            weights, rows = np.asarray(calibration["weights"]), []
            for actual, daily in policy_truth:
                row = {**actual, "method_id": method}
                for metric in ["energy_kwh", "bill_yuan", "carbon_kg"]:
                    estimate = float(weights @ daily.loc[indices, metric].to_numpy())
                    row[f"estimated_{metric}"] = estimate
                    row[f"signed_error_{metric}"] = estimate-actual[f"actual_{metric}"]
                    row[f"absolute_percentage_error_{metric}"] = abs(estimate-actual[f"actual_{metric}"])/abs(actual[f"actual_{metric}"]) if actual[f"actual_{metric}"] else None
                rows.append(row)
            predictions.extend(rows)
            rankings.append({"city": city, "method_id": method, **rank_metrics(rows)})
            for multiplier in BUDGETS:
                budget_rows, record = evaluate_budget(rows, city, method, multiplier, baseline, factor)
                candidates.extend(budget_rows)
                selections.append(record)
        print(json.dumps({"city": city, "full_policies": len(policy_truth), "simulated_hours": len(policy_truth)*len(frame)}), flush=True)
    shared_wall = time.perf_counter()-simulation_start
    for name, value in [("truth.json", truth), ("predictions.json", predictions), ("candidates.json", candidates),
                        ("selections.json", selections), ("rankings.json", rankings), ("failures.json", failures)]:
        save(out / name, value)
    summary = []
    for method in METHODS:
        rows, choices = [r for r in predictions if r["method_id"] == method], [r for r in selections if r["method_id"] == method]
        qualified = [r for r in choices if r["qualified"]]
        regrets = [r["relative_regret"] for r in qualified if r["relative_regret"] is not None]
        energy = [r["absolute_percentage_error_energy_kwh"] for r in rows]
        bill = [r["absolute_percentage_error_bill_yuan"] for r in rows]
        summary.append({"method_id": method, "successful_calibrations": sum(calibrations[(c, method)]["status"] == "ok" for c in CITIES),
            "prediction_rows": len(rows), "qualified_choices": len(qualified), "selection_rows": len(choices),
            "no_choice": sum(r["selection_status"] == "no_estimated_feasible_policy" for r in choices),
            "actual_infeasible_choices": sum(r["selection_status"] == "selected_actual_infeasible" for r in choices),
            "same_oracle_choices": sum(r["same_oracle_choice"] for r in choices),
            "worst_relative_regret": max(regrets) if regrets else None, "mean_relative_regret": float(np.mean(regrets)) if regrets else None,
            "energy_MAPE": float(np.mean(energy)) if energy else None, "energy_max_APE": max(energy) if energy else None,
            "bill_MAPE": float(np.mean(bill)) if bill else None, "bill_max_APE": max(bill) if bill else None,
            "strict_rank_inversions": sum(r["strict_inversions"] for r in rankings if r["method_id"] == method),
            "calibration_wall_seconds": sum(calibrations[(c, method)]["resources"]["wall_seconds"] for c in CITIES),
            "calibration_wall_with_dependency_seconds": sum(calibrations[(c, method)]["resources"].get("own_plus_dependency_wall_seconds", calibrations[(c, method)]["resources"]["wall_seconds"]) for c in CITIES)})
    def recommendation_key(r):
        return (-r["qualified_choices"], *(r[k] if r[k] is not None else float("inf") for k in
               ["worst_relative_regret", "mean_relative_regret", "energy_max_APE", "bill_MAPE"]), METHODS.index(r["method_id"]))
    recommendation = min(summary, key=recommendation_key)["method_id"]
    save(out / "summary.json", {"methods": summary, "development_recommendation": recommendation,
         "scope": protocol["experiment_identity"], "positive_external_or_investment_claim": False})
    save(out / "runtime.json", {"elapsed_wall_seconds": time.perf_counter()-started, "shared_policy_simulation_and_scoring_wall_seconds": shared_wall,
        "shared_input_check_selection_snapshot_wall_seconds": protocol["resources"]["shared_input_check_selection_snapshot_wall_seconds"],
        "full_policy_count": len([r for r in truth if r["status"] == "ok"]), "full_simulated_hours": simulated_hours,
        "shared_full_truth_cost_charged_to_every_method": True, "additional_selection_lookups_not_simulation_speedup": True,
        "numerical_threads": 1, "sequential_calibration_workers": True})
    files = {p.relative_to(out).as_posix(): sha(p) for p in out.rglob("*") if p.is_file()}
    save(out / "manifest.json", {"completed_utc": datetime.now(timezone.utc).isoformat(), "files": files,
        "protocol_sha256": sha(out / "protocol.json"), "calibration_failures_preserved": True, "new_input_tuning": False,
        "independent_audit_required": True, "development_recommendation": recommendation})
    return {"completed": True, "recommendation": recommendation, "methods": summary, "failures": len(failures)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run", "worker", "check"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--city", choices=CITIES)
    parser.add_argument("--method", choices=METHODS[1:])
    args = parser.parse_args()
    out = checked_output(args.out)
    try:
        if args.action == "freeze":
            result = freeze(out)
        elif args.action == "run":
            result = run(out)
        elif args.action == "check":
            verify_frozen(out, check_manifest=True)
            result = {"passed": True}
        else:
            require(args.city and args.method, "worker requires city and method")
            result = worker(out, args.city, args.method)
            save(out / f"calibration/{args.city}_{args.method}.json", result)
            result = {"city": args.city, "method": args.method, "status": result["status"]}
        print(json.dumps(plain(result), ensure_ascii=False, allow_nan=False), flush=True)
    except Exception as error:
        if args.action == "worker":
            traceback.print_exc()
        elif out.is_dir():
            path = out / f"{args.action}_failure.json"
            if not path.exists():
                save(path, {"error": str(error), "traceback": traceback.format_exc(), "utc": datetime.now(timezone.utc).isoformat()})
        print(json.dumps({"failed": True, "error": str(error)}, ensure_ascii=False), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
