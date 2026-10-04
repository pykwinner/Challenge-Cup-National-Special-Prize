    # 本方法不构建热物理方程，只求解上层已装配的MILP。
    def solve(self, mip_gap: float = 0.005, time_limit: float | None = 120.0):
        # 矩阵和边界可缓存；约束修改后由模型维护缓存失效。
        matrix = self.constraint_matrix()
        if self._compiled_constraints is None:
            self._compiled_constraints = LinearConstraint(
                matrix,
                np.asarray(self.row_lower),
                np.asarray(self.row_upper),
            )
        if self._compiled_bounds is None:
            self._compiled_bounds = Bounds(
                np.asarray(self.lower), np.asarray(self.upper)
            )
        if self._compiled_integrality is None:
            self._compiled_integrality = np.asarray(self.integrality)
        # 相对间隙控制停止条件，达到时间上限也可能仅返回可行候选。
        options: dict[str, float | bool] = {"mip_rel_gap": mip_gap, "disp": False}
        if time_limit is not None:
            options["time_limit"] = float(time_limit)
        # 记录本次实际目标，便于上层改回目标后仍能独立复算。
        self._last_solve_objective = np.asarray(self.objective, dtype=float).copy()
        self._last_solve_fun = None
        # 整数类型、边界与矩阵同时传入；不能只检查返回目标值。
        result = milp(
            c=self._last_solve_objective,
            integrality=self._compiled_integrality,
            bounds=self._compiled_bounds,
            constraints=self._compiled_constraints,
            options=options,
        )
        # 有目标值时保留它；无可行解的异常报告在HeatPlanningModel.solve中。
        if result.fun is not None:
            self._last_solve_fun = float(result.fun)
        return result
