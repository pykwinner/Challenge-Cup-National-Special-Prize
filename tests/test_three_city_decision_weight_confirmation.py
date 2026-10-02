"""Analytic controls and decision safeguards, without looking at city outcomes."""
import copy
import importlib.util
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import wasserstein_distance
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("decision_confirmation", ROOT / "scripts/run_three_city_decision_weight_confirmation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def constant_example():
    q = np.repeat([[1.], [3.], [3.]], 24, axis=1)
    return q.ravel(), q[:2], np.array([2., 1.]), np.zeros(2)


def test_l1_cap_lp_exact_two_day_solution_matches_distribution():
    values, profiles, original, lower = constant_example()
    answer = module.minimum_w1_with_l1_cap(values, profiles, original, lower, 2.)
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([1., 2.])
    assert answer["normalized_W1"] == pytest.approx(0., abs=1e-10)
    assert abs(np.array(answer["weights"])-original).sum() == pytest.approx(2.)


@pytest.mark.parametrize("cap", [0., 1., 1.999])
def test_l1_cap_cannot_hide_hard_energy_infeasibility(cap):
    answer = module.minimum_w1_with_l1_cap(*constant_example(), cap)
    assert answer["status"] == "infeasible"
    assert answer["weights"] is None


def test_l1_control_relaxes_to_original_w1_optimum_with_large_cap():
    profiles = np.array([np.tile([0., 4.], 12), np.full(24, 1.), np.full(24, 3.)])
    observed = np.r_[profiles[0], profiles[1], profiles[2], np.full(24, 2.)]
    original, lower = np.array([1., 2., 1.]), np.array([0.1, 0.1, 0.1])
    baseline = module.minimum_w1_energy_weights(observed, profiles, original, lower)
    control = module.minimum_w1_with_l1_cap(observed, profiles, original, lower, 8.)
    assert baseline["status"] == control["status"] == "ok"
    assert control["normalized_W1"] == pytest.approx(baseline["normalized_W1"], abs=1e-9)
    assert control["normalized_W1"] == pytest.approx(wasserstein_distance(observed, profiles.ravel(),
           v_weights=np.repeat(control["weights"], 24))/observed.mean())


@pytest.mark.parametrize("change", ["negative_values", "negative_cap", "nan_cap", "weight_days"])
def test_w1_control_rejects_invalid_scope(change):
    values, profiles, original, lower = constant_example()
    cap = 2.
    if change == "negative_values":
        values[0] = -1
    elif change == "negative_cap":
        cap = -1.
    elif change == "nan_cap":
        cap = float("nan")
    else:
        original[0] = 1.
    with pytest.raises(ValueError):
        module.minimum_w1_with_l1_cap(values, profiles, original, lower, cap)


def test_vacuous_price_band_covers_entire_floor_simplex_and_records_units():
    q = np.repeat([[1.], [2.], [3.]], 24, axis=1)
    p = np.repeat([[1.], [2.], [0.5]], 24, axis=1)
    idx, floor = np.array([0, 1]), np.array([0.5, 0.25])
    band, envelope = module.vacuous_price_band(q, p, idx, floor)
    assert envelope["minimum"] == pytest.approx(90.)
    assert envelope["maximum"] == pytest.approx(252.)
    assert envelope["full_target"] == pytest.approx(156.)
    assert band == pytest.approx(96.)
    for extra_first in np.linspace(0., 2.25, 11):
        weights = floor + [extra_first, 2.25-extra_first]
        assert abs(weights @ (q[idx]*p[idx]).sum(axis=1)-envelope["full_target"]) <= band+1e-12


def thermostat_inputs(hours=4):
    config = yaml.safe_load((ROOT / "configs/evidence_case_20261002/wuhan_bestest600_rc_tou.yaml").read_text(encoding="utf-8"))
    frame = pd.DataFrame({"time": pd.date_range("2024-11-16T04:00:00+08:00", periods=hours, freq="h").astype(str),
                          "outdoor_temp": 20., "internal_gain_kw": 0., "solar_gain_kw": 0.,
                          "electricity_price": 1., "grid_emission_factor": 0.4, "tariff_bucket": "valley", "heating_service": 1})
    return config, frame


def test_hour_mask_and_continuous_state_and_terminal_cooling_are_accounted():
    config, frame = thermostat_inputs()
    result = module.rollout(config, frame, module.POLICIES[4])  # morning 3C
    assert result.thermostat_target_c.tolist() == [20., 23., 20., 20.]
    assert result.T_start.iloc[2] == pytest.approx(23.)
    assert result.T_end.iloc[2] > 20.
    assert result.cool_kw.iloc[-1] > 0
    assert result.T_end.iloc[-1] == pytest.approx(20.)
    assert np.allclose(result.energy_kwh, result.heat_kw+result.cool_kw)
    assert result.bill_yuan.sum() == pytest.approx(result.energy_kwh.sum())
    assert result.carbon_kg.sum() == pytest.approx(0.4*result.energy_kwh.sum())


def test_hour_mask_does_not_preheat_in_non_valley_bucket():
    config, frame = thermostat_inputs()
    frame.loc[1, "tariff_bucket"] = "peak"
    result = module.rollout(config, frame, module.POLICIES[4])
    assert (result.thermostat_target_c == 20).all()
    assert result.energy_kwh.sum() == pytest.approx(0.)


@pytest.mark.parametrize("invalid", ["naive", "missing_hour", "service_gap", "investment", "capacity"])
def test_controller_rejects_boundary_or_scope_violation(invalid):
    config, frame = thermostat_inputs()
    if invalid == "naive":
        frame.time = pd.date_range("2024-11-16T04:00:00", periods=4, freq="h").astype(str)
    elif invalid == "missing_hour":
        frame = frame.drop(index=1)
    elif invalid == "service_gap":
        frame.loc[1, "heating_service"] = 0
    elif invalid == "investment":
        config["sources"][0]["max_new_capacity_kw"] = 1
    else:
        config["sources"][0]["existing_capacity_kw"] = 0.1
    with pytest.raises(ValueError):
        module.rollout(config, frame, module.POLICIES[4])


def policy_row(policy, estimate_energy, actual_energy, estimate_bill, actual_bill, service=True):
    return {"city": "synthetic", "method_id": "synthetic", "policy_id": policy, "service_passed": service,
            "estimated_energy_kwh": estimate_energy, "actual_energy_kwh": actual_energy,
            "estimated_carbon_kg": estimate_energy*0.4, "actual_carbon_kg": actual_energy*0.4,
            "estimated_bill_yuan": estimate_bill, "actual_bill_yuan": actual_bill}


def test_forecast_choice_actual_cap_failure_has_no_regret():
    rows = [policy_row(0, 99, 100, 10, 10), policy_row(1, 99, 102, 8, 7)]
    candidates, selection = module.evaluate_budget(rows, "synthetic", "method", 1., 100., 0.4)
    assert selection["selected_policy_id"] == 1
    assert selection["oracle_policy_id"] == 0
    assert selection["selection_status"] == "selected_actual_infeasible"
    assert selection["regret_yuan"] is selection["relative_regret"] is None
    assert selection["actual_energy_excess_kwh"] == pytest.approx(2.)
    assert selection["false_feasible_policies"] == 1
    assert candidates[1]["estimated_feasible"] and not candidates[1]["actual_feasible"]


def test_no_forecast_feasible_policy_keeps_actual_oracle_and_null_choice():
    rows = [policy_row(0, 101, 100, 10, 10)]
    _, selection = module.evaluate_budget(rows, "synthetic", "method", 1., 100., 0.4)
    assert selection["selected_policy_id"] is None
    assert selection["oracle_policy_id"] == 0
    assert selection["selection_status"] == "no_estimated_feasible_policy"
    assert selection["false_infeasible_policies"] == 1


def test_service_failure_excludes_oracle_and_gates_selected_regret():
    rows = [policy_row(0, 90, 90, 9, 9, False), policy_row(1, 95, 95, 10, 10)]
    _, selection = module.evaluate_budget(rows, "synthetic", "method", 1., 100., 0.4)
    assert selection["selected_policy_id"] == 0
    assert selection["oracle_policy_id"] == 1
    assert not selection["qualified"] and selection["regret_yuan"] is None


def test_bill_tie_rule_is_fixed_id_and_qualified_regret_uses_actual_bill():
    rows = [policy_row(1, 90, 90, 8., 11.), policy_row(0, 90, 90, 8.+5e-9, 10.)]
    _, selection = module.evaluate_budget(rows, "synthetic", "method", 1., 100., 0.4)
    assert selection["selected_policy_id"] == selection["oracle_policy_id"] == 0
    assert selection["regret_yuan"] == 0.
    rows[1]["estimated_bill_yuan"] += 1e-5
    _, selection = module.evaluate_budget(rows, "synthetic", "method", 1., 100., 0.4)
    assert selection["regret_yuan"] == 1.
    assert selection["relative_regret"] == pytest.approx(0.1)


def test_all_policy_rank_metrics_detect_inversions_and_false_ties():
    rows = [policy_row(0, 90, 90, 3, 1), policy_row(1, 90, 90, 2, 2), policy_row(2, 90, 90, 2, 3)]
    rank = module.rank_metrics(rows)
    assert rank["actual_order"] == [0, 1, 2]
    assert rank["estimated_order"] == [1, 2, 0]
    assert rank["strict_inversions"] == 2
    assert rank["false_estimated_ties"] == 1
    assert rank["strict_actual_pairs"] == 3


def test_protocol_lock_prevents_post_freeze_mutation(tmp_path):
    protocol = {"source_sha256": {}, "code_sha256": {}, "versions": {},
                "methods": module.METHODS, "policies": module.POLICIES, "budget_multipliers": module.BUDGETS}
    module.save(tmp_path / "protocol.json", protocol)
    module.save(tmp_path / "protocol_lock.json", {"protocol.json": module.sha(tmp_path / "protocol.json")})
    module.verify_frozen(tmp_path)
    protocol["policies"] = copy.deepcopy(protocol["policies"])
    protocol["policies"][1]["delta_c"] = 2
    module.save(tmp_path / "protocol.json", protocol)
    with pytest.raises(ValueError, match="frozen file changed"):
        module.verify_frozen(tmp_path)


def test_output_cannot_write_outside_owned_stage03():
    with pytest.raises(ValueError, match="owned stage03"):
        module.checked_output(module.OWNED.parent / "outside_algorithm")


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path subprocess handoff")
def test_extended_output_remains_inside_owned_scope():
    expected = module.checked_output(module.OWNED / "synthetic_no_write")
    assert module.checked_output(str(expected)) == expected


def test_candidate_failure_skips_dependent_control_without_solving(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "verify_frozen", lambda out: {})
    q = np.ones((3, 24))
    selected = pd.DataFrame({"original_day_index": [0, 1], "period_weight": [2., 1.], "lower_bound": [0., 0.]})
    monkeypatch.setattr(module, "calibration_arrays", lambda out, city: (None, selected, q, q, {}, {}))
    def forbidden(*args, **kwargs):
        pytest.fail("dependent LP must not run after candidate failed")
    monkeypatch.setattr(module, "minimum_w1_with_l1_cap", forbidden)
    (tmp_path / "calibration").mkdir()
    module.save(tmp_path / "calibration/wuhan_price_paired_L1.json", {"status": "infeasible", "weights": None})
    answer = module.worker(tmp_path, "wuhan", "hard_W1_candidate_L1_cap")
    assert answer["status"] == "skipped_candidate_failure" and answer["weights"] is None
