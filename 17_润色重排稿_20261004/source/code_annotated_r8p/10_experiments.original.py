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
    base_config, frame = load_inputs(config_path, timeseries_path)
    base_config = copy.deepcopy(base_config)
    base_config["planning"]["carbon_price_yuan_per_kg"] = 0.0
    base_config["planning"].pop("emissions_cap_kg_per_year", None)

    cost_optimum = HeatPlanningModel(copy.deepcopy(base_config), frame).solve_lexicographic(primary="total_cost").summary
    emission_optimum = HeatPlanningModel(copy.deepcopy(base_config), frame).solve_lexicographic(primary="emissions").summary
    emission_column = "total_service_emissions_kg_per_year"
    min_emissions = float(emission_optimum[emission_column])
    cost_optimum_emissions = float(cost_optimum[emission_column])
    if min_emissions > cost_optimum_emissions + 1e-6 + 1e-8*abs(cost_optimum_emissions):
        raise RuntimeError("Carbon endpoint exceeds cost endpoint; improve solve budgets before defining an epsilon range")
    caps = np.linspace(min_emissions, cost_optimum_emissions, points)

    rows = []
    for cap in caps:
        config = copy.deepcopy(base_config)
        # A tiny numerical allowance avoids rejecting the emissions-optimal endpoint.
        effective_cap = float(cap) + 1e-7 + abs(float(cap))*1e-8
        config["planning"]["emissions_cap_kg_per_year"] = effective_cap
        summary = HeatPlanningModel(config, frame).solve(objective_mode="total_cost").summary
        if not summary["numerical_audit_passed"] or not summary["emissions_cap_pass"]:
            raise RuntimeError("A sampled frontier incumbent failed independent numerical or carbon-cap checks")
        rows.append({"requested_emissions_cap_kg_per_year": float(cap), **summary})
    sampled = pd.json_normalize(rows)
    result = nondominated_cost_emission_points(sampled)
    result.attrs["all_sampled_points"] = sampled.to_dict(orient="records")
    result.attrs["cost_endpoint"] = cost_optimum
    result.attrs["carbon_endpoint"] = emission_optimum
    result["frontier_scope"] = "nondominated_subset_of_sampled_feasible_solutions"
    result["sampled_point_count"] = len(rows)
    result["removed_duplicate_or_dominated_count"] = len(rows)-len(result)
    result["marginal_abatement_cost_yuan_per_kg"] = np.nan
    for i in range(len(result) - 1):
        delta_cost = result.loc[i, "objective_yuan_per_year"] - result.loc[i + 1, "objective_yuan_per_year"]
        delta_emission = result.loc[i + 1, emission_column] - result.loc[i, emission_column]
        if delta_emission > 1e-6 + 1e-8*abs(result.loc[i + 1, emission_column]):
            result.loc[i, "marginal_abatement_cost_yuan_per_kg"] = delta_cost / delta_emission

    cost_span = result["objective_yuan_per_year"].max() - result["objective_yuan_per_year"].min()
    emission_span = result[emission_column].max() - result[emission_column].min()
    normalized_cost = (result["objective_yuan_per_year"] - result["objective_yuan_per_year"].min()) / max(cost_span, 1e-9)
    normalized_emission = (result[emission_column] - result[emission_column].min()) / max(emission_span, 1e-9)
    result["distance_to_ideal"] = np.sqrt(normalized_cost**2 + normalized_emission**2)
    result["recommended_compromise"] = False
    result.loc[result["distance_to_ideal"].idxmin(), "recommended_compromise"] = True
    result["recommendation_method"] = "closest_normalized_ideal_point"
    return result
