    def solve(self, mip_gap: float = 0.005, time_limit: float | None = 120.0):
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
        options: dict[str, float | bool] = {"mip_rel_gap": mip_gap, "disp": False}
        if time_limit is not None:
            options["time_limit"] = float(time_limit)
        self._last_solve_objective = np.asarray(self.objective, dtype=float).copy()
        self._last_solve_fun = None
        result = milp(
            c=self._last_solve_objective,
            integrality=self._compiled_integrality,
            bounds=self._compiled_bounds,
            constraints=self._compiled_constraints,
            options=options,
        )
        if result.fun is not None:
            self._last_solve_fun = float(result.fun)
        return result
