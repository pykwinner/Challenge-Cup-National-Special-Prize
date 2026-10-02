"""Synthetic analytical examples; no city data, policy trajectories or tuning."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.stats import wasserstein_distance

import heatplan.decision_weight_calibration as candidate


def paired_example():
    # Three synthetic 24h days: every day has the same reference energy and
    # marginal distribution, but the reference load/price pairing differs.
    a, b = np.tile([1.0, 3.0], 12), np.tile([3.0, 1.0], 12)
    return dict(reference_power_kw=np.array([a, b, b]),
                electricity_price_per_kwh=np.tile([1.0, 4.0], (3, 12)),
                selected_day_indices=np.array([0, 1]),
                original_weights=np.array([2.0, 1.0]), lower_bounds=np.array([1.0, 1.0]),
                day_ids=["synthetic-A", "synthetic-B", "synthetic-C"], reference_kind="thermal",
                moment_absolute_tolerances={"price_product": 0.0})


def solve(**changes):
    arguments = paired_example()
    arguments.update(changes)
    return candidate.calibrate_price_operation_weights(**arguments)


def test_zero_W1_and_exact_energy_can_hide_wrong_load_price_pairing():
    case = paired_example()
    power, price = case["reference_power_kw"], case["electricity_price_per_kwh"]
    selected, frequency = power[:2], np.array([2.0, 1.0])
    assert power.sum() == selected.sum(axis=1) @ frequency == 144.0
    assert wasserstein_distance(power.ravel(), selected.ravel(),
                                v_weights=np.repeat(frequency, 24)) == 0
    # Sorting the load and prices separately also cannot identify this reversal.
    assert np.array_equal(np.sort(power[0]), np.sort(power[1]))
    assert np.array_equal(np.sort(price[0]), np.sort(price[1]))
    actual_pair_product = float((power * price).sum())
    assert actual_pair_product == 324.0
    assert (selected * price[:2]).sum(axis=1) @ frequency == 396.0
    answer = candidate.calibrate_price_operation_weights(**case)
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([1.0, 2.0])
    assert (selected * price[:2]).sum(axis=1) @ answer["weights"] == pytest.approx(324.0)
    assert answer["diagnostics"]["reference_energy"]["signed_residual"] == pytest.approx(0)
    assert answer["price_product_is_actual_policy_bill"] is False
    assert answer["diagnostics"]["moments"]["price_product"]["unit"] == "currency*kWh_th/kWh_e"


def test_native_unit_band_minimizes_change_rather_than_forcing_zero_price_error():
    answer = solve(moment_absolute_tolerances={"price_product": 36.0})
    assert answer["status"] == "ok"
    # B(w)=252+72*w_A. The allowed upper bound is 360, so nearest to w_A=2
    # is w_A=1.5. L1 displacement is 1 day / 3 days, not 36 currency units.
    assert answer["weights"] == pytest.approx([1.5, 1.5])
    row = answer["diagnostics"]["moments"]["price_product"]
    assert row["signed_residual"] == pytest.approx(36.0)
    assert row["absolute_error_bound"] == 36.0
    assert answer["diagnostics"]["objective_weight_L1_over_days"] == pytest.approx(1 / 3)
    assert answer["solver_objective"] == pytest.approx(1 / 3)
    assert answer["objective_unit"] == "dimensionless"


def test_feasible_original_weights_are_preserved_even_with_duplicate_profiles():
    answer = solve(original_weights=[1.0, 2.0])
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([1.0, 2.0])
    assert answer["solver_objective"] == 0
    assert answer["multiple_optima_possible"] is True


def test_extreme_floor_can_conflict_with_price_pairing_without_being_relaxed():
    answer = solve(lower_bounds=[1.5, 1.0])
    assert answer["status"] == "infeasible"
    assert answer["weights"] is None
    assert answer["lower_bounds"] == [1.5, 1.0]
    assert "price_product" in answer["excluded_moments"]


def test_floor_sum_exceeds_calendar_is_explicit_infeasible():
    answer = solve(lower_bounds=[2.0, 2.0])
    assert answer["status"] == "infeasible"
    assert answer["reason"] == "lower_bounds_exceed_calendar_days"
    assert answer["weights"] is None
    assert answer["baseline_diagnostics"]["maximum_lower_bound_violation_days"] == 1


def test_reference_energy_envelope_can_be_infeasible_on_fixed_actual_days():
    answer = solve(reference_power_kw=np.repeat([[1.0], [3.0], [0.0], [0.0]], 24, axis=1),
                   electricity_price_per_kwh=np.ones((4, 24)), original_weights=[3.0, 1.0],
                   day_ids=["A", "B", "C", "D"])
    assert answer["status"] == "infeasible"
    assert answer["reason"] == "individual_moment_envelope_excludes_target"
    assert answer["weights"] is None
    interval = answer["individual_moment_envelopes"]["reference_energy"]
    assert interval["target"] == 96
    assert interval["attainable_lower"] == 144
    assert answer["input_identity"]["selected_day_indices"] == [0, 1]


def test_individually_feasible_moments_may_be_jointly_infeasible():
    # Days and reference energy force [1.5,1.5]. Price product then is 252,
    # while the complete synthetic calendar target is 288. Both row-wise
    # envelopes contain their targets; neither proves joint feasibility.
    answer = solve(reference_power_kw=np.repeat([[1.0], [3.0], [2.0]], 24, axis=1),
                   electricity_price_per_kwh=np.repeat([[1.0], [2.0], [2.5]], 24, axis=1))
    assert answer["status"] == "infeasible"
    assert answer["reason"] == "joint_moment_constraints_infeasible"
    assert answer["solver_status"] == 2
    assert answer["weights"] is None
    assert all(not row["excludes_target_band"] for row in answer["individual_moment_envelopes"].values())
    assert answer["individual_envelopes_are_only_necessary"] is True


def test_operation_window_expresses_timing_when_flat_prices_cannot():
    case = paired_example()
    mask = np.tile([1, 0], (3, 12))
    case.update(electricity_price_per_kwh=np.ones((3, 24)),
                operation_windows={"alternating_preheat_slots": mask},
                moment_absolute_tolerances={"price_product": 0, "window_energy:alternating_preheat_slots": 0})
    answer = candidate.calibrate_price_operation_weights(**case)
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([1, 2])
    row = answer["diagnostics"]["moments"]["window_energy:alternating_preheat_slots"]
    assert row["target"] == 84
    assert row["estimate"] == pytest.approx(84)
    assert row["unit"] == "kWh_th"
    hours = answer["diagnostics"]["operation_window_active_hours"]["alternating_preheat_slots"]
    assert hours == {"full_calendar": 36, "per_selected_day": [12, 12]}
    assert answer["engineering_operation_validated"] is False


def test_window_masks_are_calendar_specific_and_overlaps_are_explicit():
    case = paired_example()
    power = np.ones((3, 24))
    mask = np.zeros((3, 24))
    mask[0, :3], mask[1, :6], mask[2, :9] = 1, 1, 1
    case.update(reference_power_kw=power, electricity_price_per_kwh=np.ones((3, 24)),
                lower_bounds=[0, 0], operation_windows={"preheat": mask, "overlap": mask},
                moment_absolute_tolerances={"price_product": 0, "window_energy:preheat": 0, "window_energy:overlap": 0})
    answer = candidate.calibrate_price_operation_weights(**case)
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([0, 3])
    assert answer["diagnostics"]["moments"]["window_energy:preheat"]["target"] == 18
    assert answer["diagnostics"]["operation_window_active_hours"]["preheat"]["per_selected_day"] == [3, 6]


def test_independent_diagnostic_has_no_solver_dependency(monkeypatch):
    def forbidden_solver(*args, **kwargs):
        raise AssertionError("diagnostics must not call linprog")

    monkeypatch.setattr(candidate, "linprog", forbidden_solver)
    case = paired_example()
    diagnostic = candidate.diagnose_decision_weights(**case, weights=[2, 1])
    assert diagnostic["passes"] is False
    assert diagnostic["reference_energy"]["signed_residual"] == 0
    assert diagnostic["moments"]["price_product"]["signed_residual"] == 72
    assert diagnostic["moments"]["price_product"]["bound_violation"] == 72
    assert candidate.diagnose_decision_weights(**case, weights=[1, 2])["passes"] is True


def test_day_and_profile_identity_order_and_readonly_inputs_are_unchanged():
    case = paired_example()
    case.update(selected_day_indices=np.array([1, 0]), original_weights=np.array([1.0, 2.0]))
    mask = np.tile([1, 0], (3, 12)).astype(float)
    case.update(operation_windows={"preheat": mask},
                moment_absolute_tolerances={"price_product": 0, "window_energy:preheat": 0})
    arrays = [case[key] for key in ("reference_power_kw", "electricity_price_per_kwh", "selected_day_indices",
                                  "original_weights", "lower_bounds")] + [mask]
    snapshots = [x.copy() for x in arrays]
    for array in arrays:
        array.setflags(write=False)
    answer = candidate.calibrate_price_operation_weights(**case)
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([2, 1])
    identity = answer["input_identity"]
    assert identity["selected_day_indices"] == [1, 0]
    assert identity["selected_day_ids"] == ["synthetic-B", "synthetic-A"]
    diagnostic = candidate.diagnose_decision_weights(**case, weights=answer["weights"])
    assert diagnostic["input_identity"] == identity
    assert all(np.array_equal(a, before) for a, before in zip(arrays, snapshots))
    changed = paired_example()
    changed["electricity_price_per_kwh"][0] = changed["electricity_price_per_kwh"][0, ::-1]
    changed_diagnostic = candidate.diagnose_decision_weights(**changed, weights=[1, 2])
    assert changed_diagnostic["input_identity"]["paired_prices_sha256"] != identity["paired_prices_sha256"]


def test_price_and_policy_electricity_error_cannot_be_simultaneously_minimized():
    case = paired_example()
    answer = candidate.calibrate_price_operation_weights(**case)
    new, frequency = np.array(answer["weights"]), np.array([2.0, 1.0])
    # Independent synthetic policy power, never used in weight fitting:
    # full-calendar daily energy [1,3,1], bills [4,3,3]. With sum(w)=3,
    # zero electricity error requires [2,1]; zero bill error requires [1,2].
    electric = np.zeros((3, 24))
    electric[0, 1], electric[1, 0] = 1, 3
    electric[2, 0], electric[2, 1] = 1 / 3, 2 / 3
    daily_energy = electric.sum(axis=1)
    daily_bill = (electric * case["electricity_price_per_kwh"]).sum(axis=1)
    assert daily_energy == pytest.approx([1, 3, 1])
    assert daily_bill == pytest.approx([4, 3, 3])
    assert daily_energy.sum() == 5
    assert daily_bill.sum() == 10
    assert daily_energy[:2] @ frequency == 5
    assert daily_energy[:2] @ new == pytest.approx(7)
    assert daily_bill[:2] @ frequency == 11
    assert daily_bill[:2] @ new == pytest.approx(10)
    # A truly electrical reference would label its static price product money,
    # yet neither reference type implies actual-policy energy/service accuracy.
    diagnostic = candidate.diagnose_decision_weights(**{**case, "reference_kind": "electric"}, weights=new)
    assert diagnostic["moments"]["price_product"]["unit"] == "currency"
    assert answer["out_of_sample_validated"] is False


def test_zero_reference_and_zero_target_relative_error_are_explicit():
    answer = solve(reference_power_kw=np.zeros((3, 24)))
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([2, 1])
    assert answer["diagnostics"]["reference_energy"]["relative_residual"] is None
    assert answer["diagnostics"]["moments"]["price_product"]["relative_residual"] is None
    assert answer["solver_objective"] == 0


def test_signed_prices_and_zero_price_product_are_supported():
    prices = np.tile([1.0, -1.0], (3, 12))
    answer = solve(reference_power_kw=np.ones((3, 24)), electricity_price_per_kwh=prices)
    assert answer["status"] == "ok"
    assert answer["diagnostics"]["moments"]["price_product"]["target"] == 0
    assert answer["diagnostics"]["moments"]["price_product"]["relative_residual"] is None


def test_non_hourly_complete_days_have_correct_native_energy_units():
    # Two samples per day, each 12h. No implicit hourly sum is allowed.
    answer = solve(reference_power_kw=np.array([[1, 3], [3, 1], [3, 1]]),
                   electricity_price_per_kwh=np.array([[1, 4], [1, 4], [1, 4]]), time_step_hours=12)
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([1, 2])
    assert answer["diagnostics"]["reference_energy"]["target"] == 144
    assert answer["diagnostics"]["moments"]["price_product"]["target"] == 324


@pytest.mark.parametrize("changes", [
    {"reference_power_kw": np.ones(72)},
    {"electricity_price_per_kwh": np.ones(24)},
    {"reference_power_kw": np.empty((0, 24)), "electricity_price_per_kwh": np.empty((0, 24))},
    {"reference_power_kw": np.full((3, 24), np.nan)},
    {"reference_power_kw": np.full((3, 24), -1.0)},
    {"electricity_price_per_kwh": np.full((3, 24), np.inf)},
    {"selected_day_indices": [0, 0]},
    {"selected_day_indices": [0.0, 1.0]},
    {"selected_day_indices": [False, True]},
    {"selected_day_indices": [-1, 1]},
    {"selected_day_indices": [0, 3]},
    {"selected_day_indices": []},
    {"original_weights": [1.0, 1.0]},
    {"lower_bounds": [-1, 1]},
    {"lower_bounds": [0, np.inf]},
    {"day_ids": ["A", "A", "C"]},
    {"day_ids": ["A", "B", ""]},
    {"day_ids": "ABC"},
    {"day_ids": None},
    {"reference_kind": "unknown"},
    {"time_step_hours": 0.5},
    {"numerical_tolerance": 0},
    {"numerical_tolerance": np.nan},
    {"moment_absolute_tolerances": {}},
    {"moment_absolute_tolerances": {"price_product": -1}},
    {"moment_absolute_tolerances": {"price_product": np.inf}},
    {"moment_absolute_tolerances": {"price_product": 0, "undeclared": 0}},
    {"operation_windows": {"unbounded_window": np.ones((3, 24))}},
    {"operation_windows": {"bad": np.full((3, 24), 0.5)},
     "moment_absolute_tolerances": {"price_product": 0, "window_energy:bad": 0}},
    {"operation_windows": {"bad": np.ones((2, 24))},
     "moment_absolute_tolerances": {"price_product": 0, "window_energy:bad": 0}},
    {"solver_time_limit_seconds": 0},
])
def test_invalid_input_is_rejected_before_any_solver(monkeypatch, changes):
    def forbidden_solver(*args, **kwargs):
        raise AssertionError("invalid inputs reached solver")

    monkeypatch.setattr(candidate, "linprog", forbidden_solver)
    with pytest.raises(ValueError):
        solve(**changes)


def test_finite_inputs_that_overflow_products_are_rejected_before_solver(monkeypatch):
    def forbidden_solver(*args, **kwargs):
        raise AssertionError("overflow reached solver")

    monkeypatch.setattr(candidate, "linprog", forbidden_solver)
    with pytest.raises(ValueError, match="overflowed"):
        solve(reference_power_kw=np.full((3, 24), 1e308))


def test_finite_calendar_totals_with_overflowing_row_scale_are_rejected(monkeypatch):
    def forbidden_solver(*args, **kwargs):
        raise AssertionError("overflowing row scale reached solver")

    monkeypatch.setattr(candidate, "linprog", forbidden_solver)
    power = np.zeros((3, 24))
    power[0, 0] = 8e307
    with pytest.raises(ValueError, match="scale overflowed"):
        solve(reference_power_kw=power, electricity_price_per_kwh=np.zeros((3, 24)))


def test_extreme_finite_diagnostic_weights_do_not_emit_nonfinite_json():
    case = paired_example()
    with pytest.raises(ValueError, match="diagnostic arithmetic overflowed"):
        candidate.diagnose_decision_weights(**case, weights=[1e308, 1e308])


@pytest.mark.parametrize("status", [1, 2, 3, 4])
def test_unsuccessful_solver_never_returns_or_relabels_baseline_weights(monkeypatch, status):
    monkeypatch.setattr(candidate, "linprog", lambda *args, **kwargs:
                        SimpleNamespace(status=status, success=False, x=np.array([1, 2, 1, 1]),
                                        nit=0, message="injected failure", fun=2 / 3))
    answer = solve()
    assert answer["weights"] is None
    assert answer["method"] == candidate.METHOD
    assert answer["status"] == ("infeasible" if status == 2 else "solver_failure")
    assert answer["baseline_diagnostics"]["moments"]["price_product"]["signed_residual"] == 72


def test_solver_exception_is_explicit_failure(monkeypatch):
    def failing_solver(*args, **kwargs):
        raise RuntimeError("injected solver failure")

    monkeypatch.setattr(candidate, "linprog", failing_solver)
    answer = solve()
    assert answer["status"] == "solver_failure"
    assert answer["reason"] == "solver_exception"
    assert answer["weights"] is None


@pytest.mark.parametrize("weights,fun,reason", [
    ([2, 1, 0, 0], 0, "LP_failed_residual_check"),
    ([1, 2, 1, 1], 0, "LP_failed_residual_check"),
    ([1, np.nan, 1, 1], 2 / 3, "nonfinite_solver_result"),
    ([1e308, 1e308, 0, 0], 0, "LP_failed_residual_check"),
])
def test_apparently_successful_solver_is_audited_before_weights_are_accepted(monkeypatch, weights, fun, reason):
    monkeypatch.setattr(candidate, "linprog", lambda *args, **kwargs:
                        SimpleNamespace(status=0, success=True, x=np.array(weights),
                                        nit=0, message="injected success", fun=fun))
    answer = solve()
    assert answer["status"] == "solver_failure"
    assert answer["weights"] is None
    assert answer["reason"] == reason


def test_diagnostic_detects_violated_days_and_floors_without_clipping():
    case = paired_example()
    report = candidate.diagnose_decision_weights(**case, weights=[0, 2])
    assert report["passes"] is False
    assert report["day_conservation_signed_residual"] == -1
    assert report["maximum_lower_bound_violation_days"] == 1
    assert report["moments"]["price_product"]["estimate"] == 168
