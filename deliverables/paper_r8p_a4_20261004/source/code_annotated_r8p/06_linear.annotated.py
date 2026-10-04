    # 审计独立重算矩阵行；它不证明输入来自现场或未装配的工程约束成立。
    def audit_solution(
        self, values: np.ndarray, *, absolute_tolerance: float = 1e-6,
        relative_tolerance: float = 1e-8,
    ) -> dict:
        """Recompute every row, bound and integer restriction from the full solution.

        Scaled violations divide by atol + rtol * row magnitude; <= 1 passes.
        The objective snapshot belongs to the last solve, including emissions
        objectives that a caller restores after the solver returns.
        """
        # 拒绝无效容差；不可通过任意放大容差掩盖物理超限。
        if not np.isfinite(absolute_tolerance) or not np.isfinite(relative_tolerance) or absolute_tolerance <= 0 or relative_tolerance < 0:
            raise ValueError("audit tolerances must be positive/nonnegative")
        x = np.asarray(values, dtype=float)
        report = {
            "numerical_audit_passed": False,
            "numerical_audit_tolerance": {
                "absolute": absolute_tolerance, "relative": relative_tolerance,
                "scaled_violation_limit": 1.0,
            },
            # 首先检查向量维数和有限性，缺失或非有限解立即不通过。
            "numerical_solution_finite": bool(x.shape == (len(self.names),) and np.isfinite(x).all()),
        }
        if not report["numerical_solution_finite"]:
            return report
        compile_seconds, cache_hit = self.last_matrix_compile_seconds, self.last_matrix_cache_hit
        matrix = self.constraint_matrix()
        self.last_matrix_compile_seconds, self.last_matrix_cache_hit = compile_seconds, cache_hit
        # activity为Ax；热平衡与储热递推等式也包含在这些行内。
        activity = np.asarray(matrix @ x)
        magnitude = np.asarray(abs(matrix) @ np.abs(x))
        row_lo, row_hi = np.asarray(self.row_lower), np.asarray(self.row_upper)
        # 同时检查行下界与上界，等式并非仅看某一方向的残差。
        row_violation = np.maximum(0.0, np.maximum(row_lo - activity, activity - row_hi))
        row_scaled = np.zeros_like(activity)
        for bound, violation in ((row_lo, np.maximum(0.0, row_lo - activity)), (row_hi, np.maximum(0.0, activity - row_hi))):
            scale = np.maximum(np.maximum(1.0, magnitude), np.where(np.isfinite(bound), np.abs(bound), 0.0))
            row_scaled = np.maximum(row_scaled, violation / (absolute_tolerance + relative_tolerance * scale))
        # 变量上下界单独复核，防止行约束满足而容量或非负界被违反。
        lower, upper = np.asarray(self.lower), np.asarray(self.upper)
        bound_violation = np.maximum(0.0, np.maximum(lower - x, x - upper))
        bound_scaled = np.zeros_like(x)
        for bound, violation in ((lower, np.maximum(0.0, lower - x)), (upper, np.maximum(0.0, x - upper))):
            scale = np.maximum(np.maximum(1.0, np.abs(x)), np.where(np.isfinite(bound), np.abs(bound), 0.0))
            bound_scaled = np.maximum(bound_scaled, violation / (absolute_tolerance + relative_tolerance * scale))
        # 整数变量与最近整数比较，不将近似浮点数直接当作合法离散决策。
        integer_values = x[np.asarray(self.integrality) != 0]
        integer_violation = float(np.max(np.abs(integer_values - np.rint(integer_values)), initial=0.0))
        objective_error = None
        objective_pass = True
        # 按本次实际目标复算，适用于费用与排放目标切换。
        if self._last_solve_objective is not None and self._last_solve_fun is not None:
            recomputed = float(self._last_solve_objective @ x)
            objective_error = abs(recomputed - self._last_solve_fun)
            objective_scale = max(1.0, float(np.abs(self._last_solve_objective * x).sum()), abs(self._last_solve_fun))
            objective_pass = bool(objective_error <= absolute_tolerance + relative_tolerance * objective_scale)
        report.update({
            "numerical_constraint_max_violation": float(np.max(row_violation, initial=0.0)),
            "numerical_constraint_max_scaled_violation": float(np.max(row_scaled, initial=0.0)),
            "numerical_constraint_worst_row": int(np.argmax(row_scaled)) if len(row_scaled) else None,
            "numerical_bound_max_violation": float(np.max(bound_violation, initial=0.0)),
            "numerical_bound_max_scaled_violation": float(np.max(bound_scaled, initial=0.0)),
            "numerical_bound_worst_variable": self.names[int(np.argmax(bound_scaled))] if len(x) else None,
            "numerical_integrality_max_violation": integer_violation,
            "numerical_objective_recompute_error": objective_error,
            "numerical_objective_checked": objective_error is not None,
            # 所有数值门槛共同决定通过，scaled violation不超过1才满足对应容差。
            "numerical_audit_passed": bool(
                np.isfinite(activity).all() and np.max(row_scaled, initial=0.0) <= 1.0
                and np.max(bound_scaled, initial=0.0) <= 1.0
                and integer_violation <= absolute_tolerance and objective_pass
            ),
        })
        return report
