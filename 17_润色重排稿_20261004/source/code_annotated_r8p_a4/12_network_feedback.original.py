def solve_with_network_feedback(config, timeseries, *, mip_gap=0., time_limit=60.,
                                max_rounds=None, on_iteration: Callable | None = None):
    model, catalog, flags = build_feedback_model(config, timeseries)
    if max_rounds is None:
        max_rounds = len(catalog) + 1
    if isinstance(max_rounds, bool) or int(max_rounds) != max_rounds or max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer")
    if not math.isfinite(time_limit) or time_limit <= 0 or not math.isfinite(mip_gap) or not 0 <= mip_gap < 1:
        raise ValueError("invalid solve budget/gap")
    history, eliminated = [], set()
    for iteration in range(int(max_rounds)):
        try:
            solution = model.solve(time_limit=time_limit, mip_gap=mip_gap)
            dispatch, audit = audit_feedback_solution(config, model, solution, catalog)
        except Exception as error:
            record = {"iteration": iteration, "status": "failed", "type": type(error).__name__, "reason": str(error),
                      "solver_evidence": getattr(error, "summary", None), "previous_cuts": sorted(eliminated)}
            history.append(record)
            if on_iteration:
                on_iteration(record, None, None)
            raise FeedbackFailure(str(error), history) from error
        invalid = set(audit["invalid_modes"])
        record = {"iteration": iteration, "status": "accepted" if audit["passed"] else "rejected",
            "objective_yuan": solution.summary["objective_yuan_per_year"],
            "solver_status": solution.summary["solver_status"], "mip_gap": solution.summary.get("mip_gap"),
            "solution_quality": solution.summary["solution_quality"], "audit": audit,
            "added_mode_cuts": sorted(invalid - eliminated), "previous_cuts": sorted(eliminated)}
        history.append(record)
        if on_iteration:
            on_iteration(record, solution, dispatch)
        if audit["passed"]:
            solution.summary["network_feedback"] = {"iterations": iteration + 1, "eliminated_modes": sorted(eliminated),
                "audit": audit, "optimality_domain": "finite catalog MILP within recorded solver tolerances; not continuous nonlinear network optimum",
                "soft_penalty_is_not_cash_cost": True}
            return FeedbackArtifacts(model, solution, dispatch, history, audit, catalog)
        if not invalid - eliminated:
            raise FeedbackFailure("non-engineering audit failure; no valid mode cut available", history)
        for virtual in sorted(invalid - eliminated):
            for t in model.times:
                model.problem.add_constraint({flags[virtual, t]: 1.}, ub=0.)
        eliminated.update(invalid)
    raise FeedbackFailure("feedback iteration limit reached without engineering acceptance", history)
