"""Analytic ledger and saved-record tampering tests; no three-city inputs used."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1]/"scripts/verify_three_city_decision_weight_confirmation.py"
spec = importlib.util.spec_from_file_location("independent_decision_weight_verifier", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

METHODS = ["frequency", "soft_energy", "hard_energy_minimum_W1", "hard_energy_minimum_L1", "price_paired_L1", "hard_W1_candidate_L1_cap"]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def physics():
    building = {"min_temperature_c": 20, "max_temperature_c": 27, "initial_temperature_c": 20, "final_temperature_c": 20,
        "heating_enabled": True, "cooling_enabled": True, "heating_service_column": "heating_service",
        "internal_gain_column": "internal_gain_kw", "solar_gain_column": "solar_gain_kw", "heat_loss_kw_per_c": .1,
        "thermal_capacity_kwh_per_c": .5, "max_heat_kw": 1000, "max_cooling_kw": 1000}
    return {"building_thermal": {"buildings": [building], "boundary_mode": "fixed", "simultaneous_mode": "exclusive"},
        "sources": [{"name": "ideal", "efficiency": 1, "max_new_capacity_kw": 0, "existing_capacity_kw": 1000}],
        "pipes": [], "storages": [], "planning": {"time_step_hours": 1},
        "multi_energy_services": {"cooling": {"sources": {"ideal": {"eer": 1}}}}}


def analytic_trace(hours=48):
    times = pd.date_range("2024-11-16", periods=hours, freq="h", tz="Asia/Shanghai").astype(str)
    frame = pd.DataFrame({"time": times, "outdoor_temp": 10., "internal_gain_kw": 0., "solar_gain_kw": 0.,
        "heating_service": 1, "electricity_price": 1., "grid_emission_factor": 1., "tariff_bucket": "flat",
        "heat_reference_no_gain_kw": 1.})
    b = -np.expm1(-.2)/.1
    trace = pd.DataFrame({"time": times, "time_index": np.arange(hours), "original_day_index": np.arange(hours)//24,
        "policy_id": 0, "T_start": 20., "T_end": 20., "heat_kw": 1., "cool_kw": 0.,
        "passive_temperature_c": 20-b, "thermostat_target_c": 20., "terminal_override": np.arange(hours) == hours-1,
        "energy_kwh": 1., "bill_yuan": 1., "carbon_kg": 1.})
    return frame, trace


def test_constant_temperature_analytic_balance():
    frame, trace = analytic_trace()
    report = verifier.audit_trajectory(physics(), frame, trace, {"policy_id": 0, "delta_c": 0, "local_hours": []})
    assert report["passed"], report["errors"]
    assert report["totals"] == dict(energy_kwh=48., bill_yuan=48., carbon_kg=48.)
    assert np.array_equal(report["daily"].energy_kwh, [24, 24])


@pytest.mark.parametrize("column,change,label", [("heat_kw", .1, "continuous_RC_convolution"), ("T_start", 1., "state_continuity"),
    ("thermostat_target_c", 1., "thermostat_clock_window"), ("bill_yuan", 1., "hourly_ledger:bill_yuan"), ("energy_kwh", 1., "hourly_ledger:energy_kwh")])
def test_trajectory_tampering_is_rejected(column, change, label):
    frame, trace = analytic_trace()
    trace.loc[7, column] += change
    report = verifier.audit_trajectory(physics(), frame, trace, {"policy_id": 0, "delta_c": 0, "local_hours": []})
    assert not report["passed"] and label in report["errors"]


def test_terminal_cooling_is_billed_and_target_is_restored():
    frame, trace = analytic_trace(1)
    frame.loc[0, "outdoor_temp"] = 100
    frame.loc[0, "electricity_price"] = 4
    frame.loc[0, "tariff_bucket"] = "valley"
    trace.loc[0, "heat_kw"] = 0
    trace.loc[0, "cool_kw"] = 8
    trace.loc[0, "passive_temperature_c"] = 20+8*(-np.expm1(-.2)/.1)
    trace.loc[0, ["energy_kwh", "bill_yuan", "carbon_kg"]] = [8, 32, 8]
    policy = {"policy_id": 0, "delta_c": 3, "local_hours": [0]}
    report = verifier.audit_trajectory(physics(), frame, trace, policy)
    assert report["passed"], report["errors"]
    assert report["totals"]["bill_yuan"] == 32
    trace.loc[0, "bill_yuan"] = 0
    assert "hourly_ledger:bill_yuan" in verifier.audit_trajectory(physics(), frame, trace, policy)["errors"]


def decision_protocol():
    return {"cap_absolute_tolerance": 1e-7, "cap_relative_tolerance": 1e-9, "bill_tie_absolute_tolerance": 1e-8}


def row(pid, energy, bill, estimated_energy=None, estimated_bill=None, service=True):
    return {"city": "test", "method_id": "test", "policy_id": pid, "actual_energy_kwh": energy,
        "actual_carbon_kg": energy, "actual_bill_yuan": bill, "estimated_energy_kwh": energy if estimated_energy is None else estimated_energy,
        "estimated_carbon_kg": energy if estimated_energy is None else estimated_energy, "estimated_bill_yuan": bill if estimated_bill is None else estimated_bill,
        "service_passed": service}


def test_better_estimated_bill_cannot_qualify_actual_cap_violation():
    candidates, selection = verifier.decision_rows([row(0, 10, 10), row(1, 11, 5, 9, 4)], 1, 10, 1, decision_protocol())
    assert selection["selected_policy_id"] == 1 and selection["oracle_policy_id"] == 0
    assert selection["selection_status"] == "selected_actual_infeasible"
    assert selection["regret_yuan"] is None and selection["actual_energy_excess_kwh"] == 1
    assert selection["false_feasible_policies"] == 1
    assert candidates[1]["actual_feasible"] is False


def test_missing_estimated_and_actual_candidates_have_explicit_nulls():
    _, none = verifier.decision_rows([row(0, 11, 10)], 1, 10, 1, decision_protocol())
    assert none["selection_status"] == "no_estimated_feasible_policy" and none["selected_policy_id"] is None and none["oracle_policy_id"] is None
    _, service = verifier.decision_rows([row(0, 9, 10, service=False)], 1, 10, 1, decision_protocol())
    assert service["selection_status"] == "selected_actual_infeasible" and service["regret_yuan"] is None


def test_tie_order_and_positive_library_regret():
    _, tied = verifier.decision_rows([row(3, 8, 6, estimated_bill=5), row(1, 8, 5, estimated_bill=5+5e-9)], 1, 10, 1, decision_protocol())
    assert tied["selected_policy_id"] == 1 and tied["regret_yuan"] == 0
    _, missed = verifier.decision_rows([row(0, 8, 8, estimated_bill=1), row(1, 8, 5, estimated_bill=2)], 1, 10, 1, decision_protocol())
    assert missed["selected_policy_id"] == 0 and missed["oracle_policy_id"] == 1 and missed["regret_yuan"] == 3


def test_strict_rank_inversion_and_false_tie_are_separate():
    rows = [row(0, 1, 1, estimated_bill=3), row(1, 1, 2, estimated_bill=3), row(2, 1, 3, estimated_bill=1)]
    rank = verifier.rank_rows(rows, decision_protocol())
    assert rank["strict_actual_pairs"] == 3 and rank["strict_inversions"] == 2 and rank["false_estimated_ties"] == 1
    assert rank["estimated_order"] == [2, 0, 1]


def test_independent_weighted_cdf_integral_has_known_distance():
    assert verifier.normalized_w1(np.array([0., 2.]), np.array([[1., 1.]]), np.array([1.])) == 1


@pytest.fixture(scope="module")
def frozen_fixture(tmp_path_factory):
    """Complete synthetic population with known exact totals and zero errors."""
    out = tmp_path_factory.mktemp("independent_synthetic_run")
    cities = ["a", "b", "c"]
    policies = [{"policy_id": p, "name": "identical_"+str(p), "delta_c": 0, "local_hours": []} for p in range(9)]
    selected = [{"representative_day": "r0", "original_day_index": 0, "original_time": "2024-11-16 00:00:00+08:00", "period_weight": 120., "is_extreme_day": False, "extreme_reasons": "", "lower_bound": 1e-6}]
    protocol = {**decision_protocol(), "cities": cities, "methods": METHODS, "policies": policies, "budget_multipliers": [1., 1.02, 1.05, 1.1],
        "hours_per_city": 2880, "days_per_city": 120, "reference_column": "heat_reference_no_gain_kw", "selected_actual_days": {c: selected for c in cities},
        "outcome_use": {"three_cities_already_seen_development": True, "fresh_holdout": False, "adaptive_tuning": False},
        "calibration": {"numerical_tolerance": 1e-8, "price_product_relative_band": .005, "window_energy_relative_band": .01, "windows": {"morning_end": [5], "midday_end": [13]}},
        "resources": {"per_method_city_wall_seconds": 60., "per_worker_RSS_limit_bytes": 1024**3},
        "source_sha256": {}, "code_sha256": {}, "versions": {}, "experiment_identity": "synthetic-test-only",
        "frozen_utc": "2026-10-02T10:00:00+00:00"}
    save(out/"protocol.json", protocol)
    save(out/"inputs/config.json", physics())
    save(out/"run_started.json", {"utc": "2026-10-02T10:01:00+00:00"})
    frame, template = analytic_trace(2880)
    def moment(target, unit, bound):
        return {"unit": unit, "target": target, "estimate": target, "signed_residual": 0., "relative_residual": 0., "absolute_error_bound": bound,
            "bound_violation": 0., "numerical_acceptance_allowance": 1e-8*target, "passes_with_declared_numerical_tolerance": True}
    energy = moment(2880., "kWh_th", 0.)
    diag = {"reference_energy": energy, "moments": {"price_product": moment(2880., "currency*kWh_th/kWh_e", 14.4),
        "window_energy:morning_end": moment(120., "kWh_th", 1.2), "window_energy:midday_end": moment(120., "kWh_th", 1.2)},
        "day_conservation_signed_residual": 0., "day_numerical_acceptance_allowance": 1.2e-6, "maximum_lower_bound_violation_days": 0.,
        "lower_bound_numerical_acceptance_allowance_days": 1e-8, "objective_weight_L1_over_days": 0., "passes": True}
    truth, predictions, candidates, selections, ranks = [], [], [], [], []
    completion_hashes = {}
    for city in cities:
        frame.to_csv(out/f"inputs/{city}_hourly.csv", index=False)
        pd.DataFrame(selected).to_csv(out/f"inputs/{city}_selected_days.csv", index=False)
        representative = frame.iloc[:24].copy()
        representative["representative_day"] = "r0"
        representative["original_time"] = representative.time
        representative.to_csv(out/f"inputs/{city}_representative_hours.csv", index=False)
        for method in METHODS:
            resources = {"wall_seconds": 0., "limit_seconds": 60., "limit_RSS_bytes": 1024**3, "numerical_threads": 1, "startup_and_verification_included": True}
            calibration = {"city": city, "method_id": method, "status": "ok", "weights": [120.], "paired_diagnostics": deepcopy(diag), "normalized_reference_W1": 0., "weight_L1_displacement_days": 0., "resources": resources}
            if method == "hard_energy_minimum_L1":
                calibration.update(vacuous_price_band=0., vacuous_price_envelope={"minimum": 2880., "maximum": 2880., "full_target": 2880., "unit": "currency*kWh_th/kWh_e"})
            if method == "hard_W1_candidate_L1_cap":
                calibration.update(l1_cap_days=1.2e-6, candidate_displacement_days=0.)
                resources.update(dependency_candidate_wall_seconds=0., own_plus_dependency_wall_seconds=0.)
            target = out/f"calibration/{city}_{method}.json"
            save(target, calibration)
            completion_hashes[target.name] = verifier.sha(target)
        for policy in policies:
            trace = template.copy()
            trace["policy_id"] = policy["policy_id"]
            target = out/f"trajectories/{city}_policy_{policy['policy_id']}.csv"
            target.parent.mkdir(exist_ok=True)
            trace.to_csv(target, index=False)
            target = out/f"daily/{city}_policy_{policy['policy_id']}.csv"
            target.parent.mkdir(exist_ok=True)
            pd.DataFrame({"original_day_index": range(120), "energy_kwh": 24., "bill_yuan": 24., "carbon_kg": 24.}).to_csv(target, index=False)
            actual = {"city": city, "policy_id": policy["policy_id"], "policy_name": policy["name"], "status": "ok", "service_passed": True, "actual_energy_kwh": 2880., "actual_bill_yuan": 2880., "actual_carbon_kg": 2880.}
            truth.append(actual)
            for method in METHODS:
                forecast = {**actual, "method_id": method}
                for metric in verifier.METRICS:
                    forecast.update({"estimated_"+metric: 2880., "signed_error_"+metric: 0., "absolute_percentage_error_"+metric: 0.})
                predictions.append(forecast)
                for multiplier in protocol["budget_multipliers"]:
                    candidates.append({**forecast, "budget_multiplier": multiplier, "energy_cap_kwh": 2880.*multiplier, "carbon_cap_kg": 2880.*multiplier, "estimated_feasible": True, "actual_feasible": True})
        for method in METHODS:
            ranks.append({"city": city, "method_id": method, "actual_order": list(range(9)), "estimated_order": list(range(9)), "strict_actual_pairs": 0, "strict_inversions": 0, "false_estimated_ties": 0, "mean_absolute_rank_error": 0.})
            for multiplier in protocol["budget_multipliers"]:
                selections.append({"city": city, "method_id": method, "budget_multiplier": multiplier, "energy_cap_kwh": 2880.*multiplier, "carbon_cap_kg": 2880.*multiplier,
                    "selected_policy_id": 0, "oracle_policy_id": 0, "selection_status": "qualified", "qualified": True, "same_oracle_choice": True, "regret_yuan": 0., "relative_regret": 0., "actual_energy_excess_kwh": 0., "actual_carbon_excess_kg": 0., "false_feasible_policies": 0, "false_infeasible_policies": 0})
    save(out/"calibration_complete.json", {"utc": "2026-10-02T10:02:00+00:00", "policy_truth_generated": False, "weight_sha256": completion_hashes})
    for name, value in [("truth", truth), ("predictions", predictions), ("candidates", candidates), ("selections", selections), ("rankings", ranks), ("failures", [])]:
        save(out/(name+".json"), value)
    method_summary = [{"method_id": m, "successful_calibrations": 3, "prediction_rows": 27, "qualified_choices": 12, "selection_rows": 12, "no_choice": 0, "actual_infeasible_choices": 0, "same_oracle_choices": 12, "worst_relative_regret": 0., "mean_relative_regret": 0., "energy_MAPE": 0., "energy_max_APE": 0., "bill_MAPE": 0., "bill_max_APE": 0., "strict_rank_inversions": 0, "calibration_wall_seconds": 0., "calibration_wall_with_dependency_seconds": 0.} for m in METHODS]
    save(out/"summary.json", {"methods": method_summary, "development_recommendation": "frequency", "scope": "synthetic-test-only", "positive_external_or_investment_claim": False})
    save(out/"runtime.json", {"elapsed_wall_seconds": 1., "shared_policy_simulation_and_scoring_wall_seconds": .5, "full_policy_count": 27, "full_simulated_hours": 77760, "shared_full_truth_cost_charged_to_every_method": True, "additional_selection_lookups_not_simulation_speedup": True, "numerical_threads": 1, "sequential_calibration_workers": True})
    save(out/"protocol_lock.json", {"protocol.json": verifier.sha(out/"protocol.json")})
    save(out/"manifest.json", {"completed_utc": "2026-10-02T10:03:00+00:00", "files": {p.relative_to(out).as_posix(): verifier.sha(p) for p in out.rglob("*") if p.is_file()}, "protocol_sha256": verifier.sha(out/"protocol.json"), "development_recommendation": "frequency"})
    return out


def test_complete_synthetic_population_passes_without_producer(frozen_fixture):
    report = verifier.audit(frozen_fixture)
    assert report["passed"], report["errors"]
    assert len(report["trajectory_reports"]) == 27 and len(report["calibration_reports"]) == 18
    assert report["check_count"] > 10000


@pytest.mark.parametrize("filename,key,value,expected", [("predictions.json", "signed_error_bill_yuan", 1., "signed_error_bill_yuan"),
    ("rankings.json", "strict_inversions", 1, "strict_inversions"), ("candidates.json", "estimated_feasible", False, "estimated_feasible"),
    ("selections.json", "regret_yuan", 1., "regret_yuan")])
def test_saved_error_rank_cap_and_regret_tampering_is_rejected(frozen_fixture, filename, key, value, expected):
    path = frozen_fixture/filename
    original = path.read_bytes()
    content = json.loads(original)
    content[0][key] = value
    save(path, content)
    try:
        report = verifier.audit(frozen_fixture, check_hashes=False)
        assert not report["passed"] and any(expected in error for error in report["errors"])
    finally:
        path.write_bytes(original)


def test_hash_receipt_detects_tampered_saved_record(frozen_fixture):
    path = frozen_fixture/"runtime.json"
    original = path.read_bytes()
    value = json.loads(original)
    value["elapsed_wall_seconds"] = 2.
    save(path, value)
    try:
        report = verifier.audit(frozen_fixture)
        assert any(error == "artifact_hash:runtime.json" for error in report["errors"])
    finally:
        path.write_bytes(original)


def test_failed_calibration_must_never_retain_weights():
    result = verifier.audit_calibration(None, None, {"status": "infeasible", "weights": [1.]}, "price_paired_L1", {})
    assert not result["passed"] and "failed_calibration_has_no_weights" in result["errors"]
