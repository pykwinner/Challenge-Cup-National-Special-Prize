# 此接口不是三城聚合网络的通用连续非线性水力优化器。
def solve_with_network_feedback(config, timeseries, *, mip_gap=0., time_limit=60.,
                                max_rounds=None, on_iteration: Callable | None = None):
    # 模式目录和适用配置在构模入口先验证，禁用模式必须来自该有限集合。
    model, catalog, flags = build_feedback_model(config, timeseries)
    # 默认轮数有限；不会承诺任意网络的全局收敛。
    if max_rounds is None:
        max_rounds = len(catalog) + 1
    # 拒绝无效轮数、时间预算和间隙。
    if isinstance(max_rounds, bool) or int(max_rounds) != max_rounds or max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer")
    if not math.isfinite(time_limit) or time_limit <= 0 or not math.isfinite(mip_gap) or not 0 <= mip_gap < 1:
        raise ValueError("invalid solve budget/gap")
    # 逐轮历史及已排除模式单独记录，便于辨识失败发生在哪一步。
    history, eliminated = [], set()
    # 每轮在已有割约束下求解，然后接受独立模式校核。
    for iteration in range(int(max_rounds)):
        try:
            # 费用最小可行候选仍需通过温度、水力及服务范围检查。
            solution = model.solve(time_limit=time_limit, mip_gap=mip_gap)
            dispatch, audit = audit_feedback_solution(config, model, solution, catalog)
        # 求解或审计异常保存至history，再抛出FeedbackFailure。
        except Exception as error:
            record = {"iteration": iteration, "status": "failed", "type": type(error).__name__, "reason": str(error),
                      "solver_evidence": getattr(error, "summary", None), "previous_cuts": sorted(eliminated)}
            history.append(record)
            if on_iteration:
                on_iteration(record, None, None)
            raise FeedbackFailure(str(error), history) from error
        # invalid_modes来自独立硬校核，不由软惩罚值直接替代。
        invalid = set(audit["invalid_modes"])
        record = {"iteration": iteration, "status": "accepted" if audit["passed"] else "rejected",
            "objective_yuan": solution.summary["objective_yuan_per_year"],
            "solver_status": solution.summary["solver_status"], "mip_gap": solution.summary.get("mip_gap"),
            "solution_quality": solution.summary["solution_quality"], "audit": audit,
            "added_mode_cuts": sorted(invalid - eliminated), "previous_cuts": sorted(eliminated)}
        history.append(record)
        if on_iteration:
            on_iteration(record, solution, dispatch)
        # 全部校核通过才返回；最优性范围限于有限目录和登记容差。
        if audit["passed"]:
            solution.summary["network_feedback"] = {"iterations": iteration + 1, "eliminated_modes": sorted(eliminated),
                "audit": audit, "optimality_domain": "finite catalog MILP within recorded solver tolerances; not continuous nonlinear network optimum",
                "soft_penalty_is_not_cash_cost": True}
            return FeedbackArtifacts(model, solution, dispatch, history, audit, catalog)
        # 没有可新增禁用模式时明确失败，避免重复无效求解。
        if not invalid - eliminated:
            raise FeedbackFailure("non-engineering audit failure; no valid mode cut available", history)
        # 对每个时段将不合格模式的选择变量置零，再重新求解。
        for virtual in sorted(invalid - eliminated):
            for t in model.times:
                model.problem.add_constraint({flags[virtual, t]: 1.}, ub=0.)
        eliminated.update(invalid)
    # 轮数耗尽仍未验收则失败，不把最后一轮写成合格方案。
    raise FeedbackFailure("feedback iteration limit reached without engineering acceptance", history)
