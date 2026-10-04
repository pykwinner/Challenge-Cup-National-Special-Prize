# 诊断Mw=b是否可行，不是scenarios.py中的软能量拟合SLSQP。
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
    # 矩阵列对应代表日，目标为声明能量口径；权重单位为天。
    matrix, target, initial, lower, total = _inputs(design, targets, original_weights, lower_bounds, total_weight)
    tolerance = float(numerical_tolerance)
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("numerical_tolerance must be positive and finite")
    # 这项诊断不修改来源数据，也不证明工程运行可行。
    report = {"method": "standard_linear_feasibility_HiGHS",
              "equations": "M w = b; sum(w) = W; w >= lower",
              "total_weight_days": total, "lower_bound_sum_days": float(lower.sum()),
              "zero_target_rows": np.flatnonzero(target == 0).tolist(),
              "source_data_modified": False, "engineering_operation_validated": False}
    # 所有下界之和超过总天数即可判不可行，不需优化求解。
    if lower.sum() > total + tolerance:
        return {**report, "status": "infeasible", "reason": "lower_bounds_exceed_total_weight", "witness_weights": None}
    # 先按能量量级缩放，并用w=lower+z转成非负变量。
    energy_scale = np.maximum(np.abs(target), 1.0)
    # Shift w = lower + z, z >= 0, and scale the conservation equation too.
    scale = np.concatenate((energy_scale, [max(total, 1.0)]))
    equalities = np.vstack((matrix, np.ones(len(initial)))) / scale[:, None]
    rhs = np.concatenate((target - matrix @ lower, [total - lower.sum()])) / scale
    # 零目标线性规划只寻找可行见证，能量与总天数均为等式。
    result = linprog(np.zeros(len(initial)), A_eq=equalities, b_eq=rhs,
                     bounds=[(0.0, None)] * len(initial), method="highs")
    report.update(solver_status=int(result.status), solver_message=str(result.message))
    # 仅明确的LP不可行状态作为不可行结论。
    if result.status == 2:
        return {**report, "status": "infeasible", "reason": "LP_infeasible", "witness_weights": None}
    # 求解或数值失败单独登记，不冒称数学上已经证伪。
    if not result.success or result.x is None:
        return {**report, "status": "solver_failure", "reason": "LP_did_not_return_a_feasible_witness", "witness_weights": None}
    # 恢复原单位权重并重新计算能量、天数与下界残差。
    witness = lower + np.asarray(result.x, dtype=float)
    energy_residual = matrix @ witness - target
    conservation = abs(float(witness.sum() - total))
    bound_violation = float(np.maximum(lower - witness, 0).max())
    normalized_residual = float(np.max(np.abs(energy_residual) / energy_scale))
    # 见证必须通过独立有限性和残差检查；通过也不自动代表已采用。
    verified = (np.isfinite(witness).all() and normalized_residual <= tolerance
                and conservation <= tolerance * max(total, 1.0) and bound_violation <= tolerance)
    report.update(status="feasible" if verified else "solver_failure",
                  reason="verified_LP_witness" if verified else "LP_witness_failed_independent_residual_check",
                  witness_weights=witness.tolist(), energy_residual_original_units=energy_residual.tolist(),
                  maximum_normalized_energy_residual=normalized_residual,
                  conservation_residual_days=conservation, maximum_lower_bound_violation=bound_violation)
    return report
