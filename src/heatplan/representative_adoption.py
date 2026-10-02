"""Auditable feasibility diagnostics and explicit statistical weight adoption.

The LP diagnoses exact matching on fixed observed day profiles. It does not
replace the existing soft calibration or certify chronological engineering
operation. Neither operation mutates observations or source files.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy.optimize import linprog


@dataclass(frozen=True)
class WeightAdoptionPolicy:
    """Research rules, not competition requirements or physical guarantees."""

    maximum_absolute_relative_energy_error: float = 0.001
    maximum_w1_degradation: float = 0.0
    minimum_peak_retention: float = 1.0
    minimum_ramp_retention: float = 1.0
    minimum_original_observed_peak_retention: float = 1.0
    numerical_tolerance: float = 1e-8
    metric_tolerance: float = 1e-12


def _inputs(design, targets, weights, lower_bounds, total_weight=None):
    matrix = np.asarray(design, dtype=float)
    target = np.asarray(targets, dtype=float)
    initial = np.asarray(weights, dtype=float)
    lower = np.asarray(lower_bounds, dtype=float)
    if matrix.ndim != 2 or not matrix.shape[0] or not matrix.shape[1]:
        raise ValueError("design must be a nonempty two-dimensional matrix")
    if target.shape != (matrix.shape[0],) or initial.shape != (matrix.shape[1],) or lower.shape != initial.shape:
        raise ValueError("design, targets, weights and lower bounds have incompatible shapes")
    if any(not np.isfinite(v).all() for v in (matrix, target, initial, lower)):
        raise ValueError("energy matrix, targets, weights and bounds must be finite")
    if (initial < 0).any() or (lower < 0).any():
        raise ValueError("weights and lower bounds must be nonnegative")
    total = float(initial.sum() if total_weight is None else total_weight)
    if not np.isfinite(total) or total < 0:
        raise ValueError("total_weight must be finite and nonnegative")
    return matrix, target, initial, lower, total


def diagnose_exact_weight_feasibility(
    design, targets, original_weights, lower_bounds, *, total_weight=None,
    numerical_tolerance: float = 1e-8,
) -> dict[str, Any]:
    """Diagnose M w = b, sum(w) = W, w >= lower using a standard LP.

    Feasible weights are a witness only. An infeasible result is explicit;
    numerical/solver failures are never reported as a proof of infeasibility.
    Rows are scaled to protect the LP from energy-unit magnitude differences.
    The witness is independently recomputed in original units afterwards.
    """
    matrix, target, initial, lower, total = _inputs(design, targets, original_weights, lower_bounds, total_weight)
    tolerance = float(numerical_tolerance)
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("numerical_tolerance must be positive and finite")
    report = {"method": "standard_linear_feasibility_HiGHS",
              "equations": "M w = b; sum(w) = W; w >= lower",
              "total_weight_days": total, "lower_bound_sum_days": float(lower.sum()),
              "zero_target_rows": np.flatnonzero(target == 0).tolist(),
              "source_data_modified": False, "engineering_operation_validated": False}
    if lower.sum() > total + tolerance:
        return {**report, "status": "infeasible", "reason": "lower_bounds_exceed_total_weight", "witness_weights": None}
    energy_scale = np.maximum(np.abs(target), 1.0)
    # Shift w = lower + z, z >= 0, and scale the conservation equation too.
    scale = np.concatenate((energy_scale, [max(total, 1.0)]))
    equalities = np.vstack((matrix, np.ones(len(initial)))) / scale[:, None]
    rhs = np.concatenate((target - matrix @ lower, [total - lower.sum()])) / scale
    result = linprog(np.zeros(len(initial)), A_eq=equalities, b_eq=rhs,
                     bounds=[(0.0, None)] * len(initial), method="highs")
    report.update(solver_status=int(result.status), solver_message=str(result.message))
    if result.status == 2:
        return {**report, "status": "infeasible", "reason": "LP_infeasible", "witness_weights": None}
    if not result.success or result.x is None:
        return {**report, "status": "solver_failure", "reason": "LP_did_not_return_a_feasible_witness", "witness_weights": None}
    witness = lower + np.asarray(result.x, dtype=float)
    energy_residual = matrix @ witness - target
    conservation = abs(float(witness.sum() - total))
    bound_violation = float(np.maximum(lower - witness, 0).max())
    normalized_residual = float(np.max(np.abs(energy_residual) / energy_scale))
    verified = (np.isfinite(witness).all() and normalized_residual <= tolerance
                and conservation <= tolerance * max(total, 1.0) and bound_violation <= tolerance)
    report.update(status="feasible" if verified else "solver_failure",
                  reason="verified_LP_witness" if verified else "LP_witness_failed_independent_residual_check",
                  witness_weights=witness.tolist(), energy_residual_original_units=energy_residual.tolist(),
                  maximum_normalized_energy_residual=normalized_residual,
                  conservation_residual_days=conservation, maximum_lower_bound_violation=bound_violation)
    return report


def assess_weight_candidate(
    design, targets, original_weights, candidate_weights, lower_bounds, *,
    baseline_w1, candidate_w1, peak_retention_ratio, ramp_retention_ratio,
    original_observed_peak_retention_ratio, selection_unchanged=True,
    candidate_solver_success=True, candidate_method: str = "extreme_calibrated",
    policy: WeightAdoptionPolicy | None = None,
) -> dict[str, Any]:
    """Choose candidate or original weights and preserve every failed gate.

    W1 values must use the same retained observations and normalization.
    Ratios assess selected original events, never concatenated artificial
    transitions. A fallback is separately assessed and can remain unqualified.
    All-zero target energy is explicitly non-evaluable for relative adoption.
    """
    matrix, target, initial, lower, total = _inputs(design, targets, original_weights, lower_bounds)
    if not isinstance(candidate_method, str) or not candidate_method.strip():
        raise ValueError("candidate_method must be an explicit nonempty method identifier")
    candidate_method = candidate_method.strip()
    rules = policy or WeightAdoptionPolicy()
    params = asdict(rules)
    if any(not np.isfinite(v) or v < 0 for v in params.values()) or rules.numerical_tolerance <= 0:
        raise ValueError("policy thresholds must be finite and nonnegative")
    proposed = np.asarray(candidate_weights, dtype=float) if candidate_weights is not None else None

    def examine(weights, distance, candidate):
        reasons, details = [], {}
        if candidate and not candidate_solver_success:
            reasons.append("candidate_solver_failed")
        if candidate and not selection_unchanged:
            reasons.append("candidate_changed_selected_days_or_observations")
        if weights is None or weights.shape != initial.shape or not np.isfinite(weights).all():
            return reasons + ["candidate_weights_missing_malformed_or_nonfinite"], details
        conservation = abs(float(weights.sum() - total))
        bound = float(np.maximum(lower - weights, 0).max())
        details.update(conservation_residual_days=conservation, maximum_lower_bound_violation=bound,
                       energy_residual_original_units=(matrix @ weights - target).tolist())
        if conservation > rules.numerical_tolerance * max(total, 1.0):
            reasons.append("weight_conservation_failed")
        if bound > rules.numerical_tolerance:
            reasons.append("minimum_day_or_extreme_weight_failed")
        zero = np.flatnonzero(target == 0).tolist()
        details["zero_energy_target_rows"] = zero
        if zero:
            reasons.append("relative_energy_gate_undefined_for_zero_target")
        nonzero = target != 0
        bias = float(np.max(np.abs((matrix @ weights - target)[nonzero] / target[nonzero]))) if nonzero.any() else None
        details["maximum_absolute_relative_energy_error"] = bias
        if bias is not None and bias > rules.maximum_absolute_relative_energy_error + rules.metric_tolerance:
            reasons.append("energy_error_exceeds_research_threshold")
        if distance is None or baseline_w1 is None or not np.isfinite([distance, baseline_w1]).all():
            reasons.append("distribution_gate_undefined")
        elif float(distance) > float(baseline_w1) + rules.maximum_w1_degradation + rules.metric_tolerance:
            reasons.append("weighted_W1_worse_than_uncalibrated_extreme")
        for name, value, minimum in (
            ("retained_peak", peak_retention_ratio, rules.minimum_peak_retention),
            ("retained_true_adjacent_ramp", ramp_retention_ratio, rules.minimum_ramp_retention),
            ("original_observed_peak", original_observed_peak_retention_ratio, rules.minimum_original_observed_peak_retention),
        ):
            if value is None or not np.isfinite(value):
                reasons.append(name + "_gate_undefined")
            elif value + rules.metric_tolerance < minimum:
                reasons.append(name + "_retention_failed")
        return reasons, details

    candidate_reasons, candidate_details = examine(proposed, candidate_w1, True)
    fallback_reasons, fallback_details = examine(initial, baseline_w1, False)
    adopted = not candidate_reasons
    return {"statistical_candidate_adopted": adopted,
            "candidate_method": candidate_method,
            "selected_method": candidate_method if adopted else "extreme_aware_fallback",
            "selected_weights": proposed.tolist() if adopted else initial.tolist(),
            "candidate_rejection_reasons": candidate_reasons,
            "fallback_passes_research_policy": not fallback_reasons,
            "fallback_rejection_reasons": fallback_reasons,
            "selected_passes_research_policy": adopted or not fallback_reasons,
            "candidate_diagnostics": candidate_details, "fallback_diagnostics": fallback_details,
            "policy": params, "policy_origin": "predeclared_research_adoption_rule_not_official_requirement",
            "engineering_operation_validated": False,
            "caveat": "Statistical gate only. Costs, capacities, comfort, storage and fixed-investment chronological operation are not validated."}
