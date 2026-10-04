def minimum_w1_energy_weights(observed, representative_profiles, original_weights,
                              lower_bounds, *, numerical_tolerance=1e-8):
    """Minimize normalized 1-D W1 with exact energy and day conservation.

    Each representative row is one equally sampled actual day; observations
    comprise complete equally sampled days. All values must be finite and
    nonnegative. With zero observed mean the normalized metric is undefined.
    Returns explicit success, infeasibility, or failure with no data mutation.
    """
    # 只读入观测与固定代表日；优化对象是天数权重。
    values = np.asarray(observed, dtype=float)
    profiles = np.asarray(representative_profiles, dtype=float)
    original = np.asarray(original_weights, dtype=float)
    lower = np.asarray(lower_bounds, dtype=float)
    if values.ndim != 1 or not values.size or profiles.ndim != 2 or not all(profiles.shape):
        raise ValueError("observed must be nonempty 1D and representative profiles nonempty 2D")
    # 每行是一个实际代表日；观测须由完整同长日组成。
    m, hours = profiles.shape
    if original.shape != (m,) or lower.shape != (m,) or len(values) % hours:
        raise ValueError("weight/profile dimensions or complete-day observation length are invalid")
    if any(not np.isfinite(x).all() or (x < 0).any() for x in (values, profiles, original, lower)):
        raise ValueError("observations, profiles, weights and lower bounds must be finite and nonnegative")
    tolerance = float(numerical_tolerance)
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("numerical_tolerance must be positive and finite")
    # 原权重总天数乘每日采样数须等于观测长度。
    total = float(original.sum())
    if not np.isclose(total * hours, len(values), rtol=0, atol=tolerance):
        raise ValueError("original day weight does not represent the observed complete-day count")
    # 报告明确保留未验证工程运行的身份。
    report = {"method": "standard_LP_hard_energy_minimum_1D_CDF_W1",
              "energy_matching": "hard_equality_with_independent_residual_check",
              "source_data_modified": False, "selected_profiles_modified": False,
              "engineering_operation_validated": False, "total_weight_days": total,
              "original_weights": original.tolist(), "lower_bounds": lower.tolist()}
    # 下界总和超过总天数时直接返回不可行。
    if lower.sum() > total + tolerance:
        return {**report, "status": "infeasible", "reason": "lower_bounds_exceed_total_weight", "weights": None}
    # 零均值时归一化W1无定义，不能伪造零误差。
    mean = float(values.mean())
    if mean == 0:
        return {**report, "status": "zero_energy_undefined", "reason": "normalized_W1_and_relative_energy_undefined", "weights": None}
    # 合并取值支撑点，将CDF差积分化为区间加权和。
    support = np.unique(np.concatenate((values, profiles.ravel())))
    left, widths = support[:-1], np.diff(support)
    observed_cdf = np.searchsorted(np.sort(values), left, side="right") / len(values)
    # A day weight contributes count(profile <= support) / (H*W) to the CDF.
    # 每列给出一个代表日单位天数的CDF贡献。
    cdf = np.column_stack([np.searchsorted(np.sort(p), left, side="right") for p in profiles]) / (hours * total)
    day_energy, target = profiles.sum(axis=1), float(values.sum())
    n = len(left)
    # 令权重等于下界加非负余量，显式保证逐日下界。
    shifted_target = target - float(day_energy @ lower)
    # 两行硬等式分别锁定总能量与总天数，并按量级缩放。
    equality = csr_matrix(np.vstack((day_energy / max(target, 1), np.ones(m) / max(total, 1))))
    equality = hstack((equality, csr_matrix((2, n))), format="csr")
    rhs = np.array([shifted_target/max(target, 1), (total-lower.sum())/max(total, 1)])
    if n:
        # 两组不等式让辅助量包住CDF误差的正负两侧。
        residual = observed_cdf - cdf @ lower
        cdf_sparse = csr_matrix(cdf)
        absolute = -eye(n, format="csr")
        inequalities = vstack((hstack((cdf_sparse, absolute)), hstack((-cdf_sparse, absolute))), format="csr")
        upper = np.concatenate((residual, -residual))
    else:
        inequalities, upper = None, None
    # 区间宽度除以实测均值是绝对误差辅助量的目标系数。
    objective = np.concatenate((np.zeros(m), widths / mean))
    result = linprog(objective, A_ub=inequalities, b_ub=upper,
                     A_eq=equality, b_eq=rhs,
                     bounds=[(0, None)] * (m+n), method="highs")
    report.update(solver_status=int(result.status), solver_message=str(result.message),
                  solver_iterations=int(result.nit), support_intervals=n)
    # 只有明确的LP不可行状态才登记为不可行。
    if result.status == 2:
        return {**report, "status": "infeasible", "reason": "hard_energy_or_day_bounds_infeasible", "weights": None}
    if not result.success or result.x is None:
        return {**report, "status": "solver_failure", "reason": "LP_did_not_return_optimum", "weights": None}
    # 恢复原权重后按原量纲独立重算守恒与CDF目标。
    weights = lower + np.asarray(result.x[:m])
    energy = float(day_energy @ weights - target)
    conservation = abs(float(weights.sum() - total))
    bound = float(np.maximum(lower - weights, 0).max())
    normalized_w1 = float(widths @ np.abs(cdf @ weights - observed_cdf) / mean)
    objective_residual = abs(normalized_w1 - float(result.fun))
    # 有限性和四类残差全部通过才交付权重。
    verified = (np.isfinite(weights).all() and abs(energy)/max(target, 1) <= tolerance
                and conservation <= tolerance*max(total, 1) and bound <= tolerance
                and objective_residual <= tolerance*max(normalized_w1, 1))
    return {**report, "status": "ok" if verified else "solver_failure",
            "reason": "independently_verified_hard_energy_and_CDF_objective" if verified else "LP_failed_residual_check",
            "weights": weights.tolist() if verified else None,
            "energy_residual_original_units": energy,
            "relative_energy_residual": energy/target,
            "day_conservation_residual": conservation,
            "maximum_lower_bound_violation": bound,
            "normalized_W1": normalized_w1,
            "W1_original_units": normalized_w1*mean,
            "solver_objective_residual": objective_residual,
            "caveat": "Minimum W1 on fixed selected profiles subject to these statistical constraints only; chronology and engineering operation unvalidated."}
