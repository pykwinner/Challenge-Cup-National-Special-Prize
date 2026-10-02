"""Analytical boundaries for LP feasibility and explicit statistical fallback."""
import numpy as np
import pytest

from heatplan.representative_adoption import (
    WeightAdoptionPolicy, assess_weight_candidate, diagnose_exact_weight_feasibility,
)


def gate(candidate=(2.0, 2.0), **kwargs):
    params = dict(baseline_w1=0.1, candidate_w1=0.09, peak_retention_ratio=1.0,
                  ramp_retention_ratio=1.0, original_observed_peak_retention_ratio=1.0)
    params.update(kwargs)
    return assess_weight_candidate([[1.0, 3.0]], [8.0], [3.0, 1.0], candidate, [1.0, 1.0], **params)


def test_exact_lp_feasible_witness_is_recomputed_in_original_units():
    answer = diagnose_exact_weight_feasibility([[1, 3]], [8], [3, 1], [1, 1])
    assert answer["status"] == "feasible"
    assert answer["witness_weights"] == pytest.approx([2, 2])
    assert answer["energy_residual_original_units"] == pytest.approx([0])
    assert answer["conservation_residual_days"] < 1e-8


def test_exact_lp_infeasible_when_extreme_floor_limits_energy_range():
    # Without the second day's floor, target 4 would be possible at [4, 0].
    answer = diagnose_exact_weight_feasibility([[1, 3]], [4], [3, 1], [1, 1])
    assert answer["status"] == "infeasible"
    assert answer["witness_weights"] is None


def test_total_floor_infeasibility_is_explicit_and_not_a_solver_success():
    answer = diagnose_exact_weight_feasibility([[1, 3]], [8], [3, 1], [3, 2])
    assert answer["reason"] == "lower_bounds_exceed_total_weight"


def test_joint_matching_can_fail_even_when_each_row_is_individually_possible():
    # Both rows demand the same value although row 2 is twice row 1.
    answer = diagnose_exact_weight_feasibility([[1, 3], [2, 6]], [8, 8], [3, 1], [1, 1])
    assert answer["status"] == "infeasible"


def test_rank_deficient_consistent_system_and_zero_target_are_feasible():
    answer = diagnose_exact_weight_feasibility([[1, 3], [2, 6], [0, 0]], [8, 16, 0], [3, 1], [1, 1])
    assert answer["status"] == "feasible"
    assert answer["zero_target_rows"] == [2]


@pytest.mark.parametrize("matrix,target,weights,lower", [
    ([[np.nan, 3]], [8], [3, 1], [1, 1]),
    ([[1, 3]], [8], [-1, 5], [1, 1]),
    ([[1, 3]], [8, 8], [3, 1], [1, 1]),
    ([[1, 3]], [8], [3, 1], [-1, 1]),
])
def test_invalid_lp_inputs_are_rejected(matrix, target, weights, lower):
    with pytest.raises(ValueError):
        diagnose_exact_weight_feasibility(matrix, target, weights, lower)


def test_candidate_adoption_preserves_original_array_and_states_scope():
    original = np.array([3.0, 1.0])
    decision = assess_weight_candidate([[1, 3]], [8], original, [2, 2], [1, 1],
        baseline_w1=.1, candidate_w1=.09, peak_retention_ratio=1,
        ramp_retention_ratio=1, original_observed_peak_retention_ratio=1)
    assert decision["statistical_candidate_adopted"]
    assert np.array_equal(original, [3, 1])
    assert not decision["engineering_operation_validated"]


def test_lp_feasibility_does_not_force_soft_candidate_adoption():
    assert diagnose_exact_weight_feasibility([[1, 3]], [8], [3, 1], [1, 1])["status"] == "feasible"
    decision = gate(candidate=(2.1, 1.9))
    assert not decision["statistical_candidate_adopted"]
    assert decision["selected_weights"] == [3, 1]
    assert "energy_error_exceeds_research_threshold" in decision["candidate_rejection_reasons"]


def test_distribution_tradeoff_is_rejected_even_for_exact_energy():
    decision = gate(candidate_w1=.11)
    assert "weighted_W1_worse_than_uncalibrated_extreme" in decision["candidate_rejection_reasons"]
    assert not decision["statistical_candidate_adopted"]


def test_excluded_original_peak_invalidates_candidate_and_fallback():
    decision = gate(original_observed_peak_retention_ratio=.99)
    assert not decision["selected_passes_research_policy"]
    assert "original_observed_peak_retention_failed" in decision["candidate_rejection_reasons"]
    assert "original_observed_peak_retention_failed" in decision["fallback_rejection_reasons"]


@pytest.mark.parametrize("candidate,reason", [
    (None, "candidate_weights_missing_malformed_or_nonfinite"),
    ((np.nan, 2), "candidate_weights_missing_malformed_or_nonfinite"),
    ((1, 2), "weight_conservation_failed"),
    ((.5, 3.5), "minimum_day_or_extreme_weight_failed"),
])
def test_failed_and_malformed_weights_fall_back_explicitly(candidate, reason):
    decision = gate(candidate)
    assert reason in decision["candidate_rejection_reasons"]
    assert decision["selected_weights"] == [3, 1]


def test_successful_solver_and_valid_weights_cannot_override_modified_data():
    decision = gate(selection_unchanged=False)
    assert "candidate_changed_selected_days_or_observations" in decision["candidate_rejection_reasons"]


def test_zero_energy_is_reported_as_undefined_not_zero_relative_error():
    decision = assess_weight_candidate([[0, 0]], [0], [2, 2], [2, 2], [1, 1],
        baseline_w1=None, candidate_w1=None, peak_retention_ratio=None,
        ramp_retention_ratio=None, original_observed_peak_retention_ratio=None)
    assert not decision["selected_passes_research_policy"]
    assert decision["candidate_diagnostics"]["maximum_absolute_relative_energy_error"] is None
    assert "relative_energy_gate_undefined_for_zero_target" in decision["candidate_rejection_reasons"]


def test_predeclared_threshold_accepts_boundary_and_rejects_above_it():
    # energy = 8 - 2 delta; a delta .004 gives exactly 0.1% relative error.
    assert gate((2.004, 1.996))["statistical_candidate_adopted"]
    assert not gate((2.00401, 1.99599))["statistical_candidate_adopted"]


def test_zero_energy_scalar_numerators_are_not_reinterpreted_using_scale_one():
    decision = assess_weight_candidate([[1, -1]], [0], [2, 2], [3, 1], [0, 0],
        baseline_w1=.1, candidate_w1=.1, peak_retention_ratio=1,
        ramp_retention_ratio=1, original_observed_peak_retention_ratio=1)
    assert decision["candidate_diagnostics"]["energy_residual_original_units"] == [2]
    assert not decision["statistical_candidate_adopted"]
