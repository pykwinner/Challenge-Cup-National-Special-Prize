# 本接口检验声明供暖季，不等同于在全年窗口联合重求投资。
def run_fixed_investment_heating_season_replay(
    config: dict[str, Any], representative_timeseries: pd.DataFrame,
    chronological_source: pd.DataFrame, scope: dict[str, Any], *,
    timestamp_column: str = "timestamp", input_timezone: str | None = None,
    planning_time_limit: float = 180.0, replay_time_limit: float = 1800.0,
    planning_mip_gap: float | None = None, replay_mip_gap: float | None = None,
    allow_cyclic_state_assumptions: bool = False,
    fixed_plan: FixedInvestmentPlanArtifacts | None = None,
) -> HeatingSeasonReplayArtifacts:
    """Plan on the supplied planning horizon and validate only the stated season."""
    # 按有时区的左闭右开窗口切取时序，检查步长和完整覆盖。
    chronology, scope_audit = prepare_heating_season_timeseries(
        config, chronological_source, scope, timestamp_column=timestamp_column, input_timezone=input_timezone,
    )
    # 不支持的状态交接假设在昂贵求解前拒绝。
    # Reject unsupported state assumptions before a potentially expensive solve.
    _season_config(config, allow_cyclic_state_assumptions)
    # 无既有固定方案时先规划并完整冻结；有方案时复用该方案。
    plan = fixed_plan if fixed_plan is not None else prepare_fixed_investment_plan(
        config,representative_timeseries,planning_time_limit=planning_time_limit,planning_mip_gap=planning_mip_gap)
    # 季节转换保存假设；年度经济与资源约束另需年度复算。
    fixed_config, assumptions = _season_config(plan.fixed_config, allow_cyclic_state_assumptions)
    plan = replace(plan, fixed_config=fixed_config)
    _, scenario_audit = adapt_config_to_chronology_scenarios(fixed_config, chronology)
    preflight = AnnualReplayPreflight(
        chronological_timeseries=chronology, chronology_audit=scope_audit,
        representative_coverage_audit=pd.DataFrame(columns=["coverage_status"]),
        representative_recommendations=pd.DataFrame(), scenario_scope_audit=scenario_audit,
    )
    # 运行模型使用已冻结配置，require_full_year=False仅标明季节范围。
    result = replay_prepared_fixed_investment_plan(
        config, representative_timeseries, chronology, plan,
        timestamp_column=timestamp_column, replay_time_limit=replay_time_limit,
        replay_mip_gap=replay_mip_gap, require_full_year=False, preflight=preflight,
    )
    # 不把年度规划总量与季节总量直接比较，也不按小时比例外推全年费用。
    # Annual planning totals and seasonal totals are intentionally not compared.
    # 清空跨年度与季节的直接总量比较，避免口径错配。
    result.kpi_comparison = pd.DataFrame()
    # 独立复核储热状态、初末边界和对应时序。
    storage = seasonal_storage_postcheck(result.fixed_config, result.replay.dispatch, expected_timeseries=chronology)
    # 共同验收检查结合冻结、可靠容量、求解与储热证据。
    common = annual_replay_acceptance_audit(
        result.fixed_config, chronology_audit=scope_audit,
        representative_coverage_audit=preflight.representative_coverage_audit,
        investment_freeze_audit=result.investment_freeze_audit,
        fixed_investment_readiness_audit=result.fixed_investment_readiness_audit,
        planning_summary=result.planning.summary, replay_summary=result.replay.summary,
        storage_boundary_audit=storage,
    )
    # 删除年度专属门槛；季节证书不能因这些门槛被删除而晋级年度证书。
    gates = [gate for gate in common["gates"] if gate["gate"] not in {"continuous_full_calendar_year", "continuous_full_service_year", "representative_period_coverage"}]
    for gate in gates:
        gate["gate"] = gate["gate"].replace("annual_", "period_")
        gate["requirement"] = gate["requirement"].replace("全年", "本服务期")
    # 另加服务窗口精确匹配门槛，防止短片段冒充完整供暖季。
    gates.insert(0, {"gate": "exact_service_period", "passed": bool(scope_audit["exact_scope_match"]),
                     "observed": scope_audit, "requirement": "exact timezone-aware half-open service window"})
    # 保留全部失败门槛及假设，不把失败结果写成通过。
    # 证书由全部门槛共同决定，保留未通过项目名称。
    failed = [gate["gate"] for gate in gates if not gate["passed"]]
    certificate = {"schema": "heatplan.heating_season_certificate.v1", "scope_type": "heating_season",
                   "certification_pass": not failed, "gate_count": len(gates),
                   "passed_gate_count": len(gates) - len(failed), "failed_gate_count": len(failed),
                   "failed_gates": failed, "gates": gates, "assumptions": assumptions,
                   "annual_replay_certified": False}
    summary = {"scope": scope_audit, "heating_season_certified": not failed,
               "annual_replay_certified": False, "assumptions": assumptions,
               "solver_status": result.replay.summary["solver_status"],
               "unserved_rate": result.replay.summary["unserved_rate"],
               "numerical_audit_passed": result.replay.summary.get("numerical_audit_passed", False)}
    # 期间指标重新命名为over_period，避免per_year标签误导。
    for key in ("demand_kwh_per_year", "effective_heat_demand_kwh_per_year", "unserved_kwh_per_year",
                "emissions_kg_per_year", "total_service_emissions_kg_per_year", "electricity_kwh_per_year",
                "heating_cash_operating_cost_yuan_per_year", "cooling_demand_kwh_per_year"):
        if key in result.replay.summary:
            summary[key.replace("_per_year", "_over_period")] = result.replay.summary[key]
    return HeatingSeasonReplayArtifacts(result, scope_audit, assumptions, storage, certificate, summary)
