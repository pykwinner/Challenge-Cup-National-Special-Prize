# 独立接口不自动运行于三城单次入口，也不代表历史扫描使用相同规则。
def epsilon_constraint_pareto(
    config_path: str | Path,
    timeseries_path: str | Path,
    points: int = 7,
) -> pd.DataFrame:
    """Sample a nondominated system cost/carbon frontier with explicit caps.

    The investment and operating model is unchanged across points. A hard reliability
    constraint prevents the emissions objective from creating artificial low-carbon
    solutions through heat curtailment.
    """
    if points < 2:
        raise ValueError("points 至少为 2")
    # 配置与时序只读取一次；各档复制同一基础配置，保持输入一致。
    base_config, frame = load_inputs(config_path, timeseries_path)
    base_config = copy.deepcopy(base_config)
    # 清除碳价与原碳帽；其余启用约束和偏好仍按配置保留。
    base_config["planning"]["carbon_price_yuan_per_kg"] = 0.0
    base_config["planning"].pop("emissions_cap_kg_per_year", None)

    # 两个端点分别新建模型，避免前一端点的主目标约束污染另一端点。
    cost_optimum = HeatPlanningModel(copy.deepcopy(base_config), frame).solve_lexicographic(primary="total_cost").summary
    emission_optimum = HeatPlanningModel(copy.deepcopy(base_config), frame).solve_lexicographic(primary="emissions").summary
    # 采用冷热服务及共享电力口径的登记系统总碳，单位kg CO2。
    emission_column = "total_service_emissions_kg_per_year"
    min_emissions = float(emission_optimum[emission_column])
    cost_optimum_emissions = float(cost_optimum[emission_column])
    # 端点次序超出容差则报错，不伪造可用扫描区间。
    if min_emissions > cost_optimum_emissions + 1e-6 + 1e-8*abs(cost_optimum_emissions):
        raise RuntimeError("Carbon endpoint exceeds cost endpoint; improve solve budgets before defining an epsilon range")
    caps = np.linspace(min_emissions, cost_optimum_emissions, points)

    # 逐档保存全部可行候选，再统一进行非支配筛选。
    rows = []
    # 每档均由基础配置开始；本函数没有调用投资冻结。
    for cap in caps:
        config = copy.deepcopy(base_config)
        # A tiny numerical allowance avoids rejecting the emissions-optimal endpoint.
        effective_cap = float(cap) + 1e-7 + abs(float(cap))*1e-8
        # 实际碳帽包含微小数值余量，同时保存请求值供审查。
        config["planning"]["emissions_cap_kg_per_year"] = effective_cap
        # 自由投资输入时每档重求投资和运行；已固定配置才只检验固定资产。
        summary = HeatPlanningModel(config, frame).solve(objective_mode="total_cost").summary
        # 数值或碳帽复核失败立即终止，不能把失败点加入前沿。
        if not summary["numerical_audit_passed"] or not summary["emissions_cap_pass"]:
            raise RuntimeError("A sampled frontier incumbent failed independent numerical or carbon-cap checks")
        rows.append({"requested_emissions_cap_kg_per_year": float(cap), **summary})
    sampled = pd.json_normalize(rows)
    # 非支配关系只针对已采样可行解，不是完整连续Pareto前沿。
    result = nondominated_cost_emission_points(sampled)
    # 保留筛选前的完整分母，便于追查重复及被支配样本。
    result.attrs["all_sampled_points"] = sampled.to_dict(orient="records")
    result.attrs["cost_endpoint"] = cost_optimum
    result.attrs["carbon_endpoint"] = emission_optimum
    result["frontier_scope"] = "nondominated_subset_of_sampled_feasible_solutions"
    result["sampled_point_count"] = len(rows)
    result["removed_duplicate_or_dominated_count"] = len(rows)-len(result)
    result["marginal_abatement_cost_yuan_per_kg"] = np.nan
    # 相邻点费用差除以减排量，得到元/kg的边际减排费用。
    for i in range(len(result) - 1):
        delta_cost = result.loc[i, "objective_yuan_per_year"] - result.loc[i + 1, "objective_yuan_per_year"]
        delta_emission = result.loc[i + 1, emission_column] - result.loc[i, emission_column]
        # 减排差须超过数值容差，避免用近零分母放大比值。
        if delta_emission > 1e-6 + 1e-8*abs(result.loc[i + 1, emission_column]):
            result.loc[i, "marginal_abatement_cost_yuan_per_kg"] = delta_cost / delta_emission

    # 理想点距离依赖归一化规则，折中点不是额外物理或工程验收。
    cost_span = result["objective_yuan_per_year"].max() - result["objective_yuan_per_year"].min()
    emission_span = result[emission_column].max() - result[emission_column].min()
    normalized_cost = (result["objective_yuan_per_year"] - result["objective_yuan_per_year"].min()) / max(cost_span, 1e-9)
    normalized_emission = (result[emission_column] - result[emission_column].min()) / max(emission_span, 1e-9)
    result["distance_to_ideal"] = np.sqrt(normalized_cost**2 + normalized_emission**2)
    result["recommended_compromise"] = False
    result.loc[result["distance_to_ideal"].idxmin(), "recommended_compromise"] = True
    result["recommendation_method"] = "closest_normalized_ideal_point"
    return result
