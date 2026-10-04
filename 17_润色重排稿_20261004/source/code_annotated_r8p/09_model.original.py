    def solve_lexicographic(
        self, primary: str = "emissions", *, absolute_tolerance: float = 1e-7,
        relative_tolerance: float = 1e-8, mip_gap: float | None = None,
        time_limit: float | None = None,
    ) -> SolveArtifacts:
        """Two-stage endpoint with an explicit primary-objective allowance.

        Each stage uses the configured solve budget. A time-limited first
        stage only certifies a secondary solution within its incumbent bound,
        not a globally proven lexicographic optimum. The returned summary
        retains both stages' termination evidence. This instance keeps the
        primary bound, so use a fresh model for each independent endpoint.
        """
        if primary not in {"total_cost", "emissions"}:
            raise ValueError("primary must be total_cost or emissions")
        if not np.isfinite(absolute_tolerance) or not np.isfinite(relative_tolerance) or absolute_tolerance < 0 or relative_tolerance < 0:
            raise ValueError("lexicographic tolerances must be finite and nonnegative")
        first = self.solve(mip_gap=mip_gap, time_limit=time_limit, objective_mode=primary)
        if primary == "emissions":
            coefficients, constant = self.total_service_emissions_expression()
            value = float(first.summary["total_service_emissions_kg_per_year"])
            secondary = "total_cost"
        else:
            coefficients = {i:c for i,c in enumerate(self.problem.objective) if c}
            constant = 0.0
            value = self._dot(coefficients, first.values)
            secondary = "emissions"
        allowance = absolute_tolerance + relative_tolerance * abs(value)
        self.problem.add_constraint(coefficients, ub=value+allowance-constant)
        failed_second = None
        try:
            second = self.solve(mip_gap=mip_gap, time_limit=time_limit, objective_mode=secondary)
            endpoint_summary = second.summary
        except SolveFailure as error:
            failed_second = error
            endpoint_summary = error.summary
        endpoint_summary.update({
            "endpoint_method": "two_stage_lexicographic_with_reported_tolerance",
            "primary_objective": primary, "primary_incumbent_value": value,
            "primary_objective_allowance": allowance,
            "primary_solver_status": first.summary["solver_status"],
            "primary_mip_gap": first.summary.get("mip_gap"),
            "primary_numerical_audit_passed": first.summary["numerical_audit_passed"],
            "primary_solver_message": first.summary["solver_message"],
            "primary_solver_objective_value": first.summary["objective_value"],
            "primary_mip_dual_bound": first.summary.get("mip_dual_bound"),
            "primary_solver_requested_mip_gap": first.summary["solver_requested_mip_gap"],
            "primary_solver_time_limit_seconds": first.summary["solver_time_limit_seconds"],
            "primary_solver_elapsed_seconds": first.summary["solver_elapsed_seconds"],
            "endpoint_solver_elapsed_seconds": first.summary["solver_elapsed_seconds"]+endpoint_summary["solver_elapsed_seconds"],
        })
        stages = [deepcopy(first.summary["solver_stages"][0]), deepcopy(endpoint_summary["solver_stages"][0])]
        stages[0]["stage_id"], stages[1]["stage_id"] = "primary", "secondary"
        endpoint_summary["solver_stages"] = stages
        endpoint_summary["solution_quality"] = solution_quality_from_summary(
            endpoint_summary, scope=first.summary["solution_quality"]["scope"])
        if failed_second is not None:
            failed_second.solution_quality = endpoint_summary["solution_quality"]
            raise failed_second
        return second
