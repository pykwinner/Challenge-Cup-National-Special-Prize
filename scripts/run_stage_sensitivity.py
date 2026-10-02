from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pandas as pd
import yaml

from heatplan.io import load_inputs, validate_inputs
from heatplan.model import HeatPlanningModel
from heatplan.scenarios import build_correlated_northern_scenario_bundle


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "stage_analysis"


def scenario3_bundle(
    *, legacy_behavior: bool = False, enable_building_flex: bool = True
) -> tuple[dict, pd.DataFrame]:
    config, frame = load_inputs(
        ROOT / "data" / "public_scenario_inputs" / "scenario3_wuhan_public.yaml",
        ROOT / "data" / "public_scenario_inputs" / "scenario3_wuhan_representative_days_2023.csv",
    )
    specs = yaml.safe_load(
        (ROOT / "data" / "scenario3_scenario_specs.yaml").read_text(encoding="utf-8")
    )
    if legacy_behavior:
        for scenario in specs["scenarios"]:
            if scenario["name"] == "evening_rebound":
                scenario["demand_multiplier"] = 1.08
            elif scenario["name"] == "high_comfort_preference":
                scenario["demand_multiplier"] = 1.05
    demand_columns = [
        column
        for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    frame = build_correlated_northern_scenario_bundle(
        frame,
        demand_columns,
        specs["scenarios"],
        period_weight=float(specs["period_weight"]),
    )
    config = copy.deepcopy(config)
    config["planning"]["risk"] = dict(specs.get("risk", {}))
    if not enable_building_flex:
        config["planning"]["firm_building_flexibility"] = {"enabled": False}
    if legacy_behavior:
        for node in config["nodes"]:
            node.get("user_behavior", {}).pop("scenario_multiplier_weight_column", None)
    validate_inputs(config, frame)
    return config, frame


def solve_rate(base_config: dict, frame: pd.DataFrame, rate: float, time_limit: float) -> dict:
    config = copy.deepcopy(base_config)
    subsidized_categories = (
        "source_capacity",
        "storage_capacity",
        "grid_connection_capacity",
        "pipe_choice",
    )
    config.setdefault("policy", {})["capex_subsidy_fraction"] = {
        category: rate for category in subsidized_categories
    }
    artifacts = HeatPlanningModel(config, frame).solve(time_limit=time_limit)
    summary = artifacts.summary
    capacities = artifacts.investments[
        artifacts.investments["category"] == "source_capacity"
    ].set_index("asset")["value"].to_dict()
    components = summary["components_yuan_per_year"]
    return {
        "subsidy_fraction": rate,
        "solver_status": summary["solver_status"],
        "mip_gap": summary["mip_gap"],
        "levelized_heat_cost_yuan_per_kwh": summary[
            "regulated_levelized_heat_cost_yuan_per_kwh"
        ],
        "objective_yuan_per_year": summary["objective_yuan_per_year"],
        "net_annualized_capex_yuan": components.get("annualized_capex", 0.0),
        "operating_cost_yuan": components.get("operating_cost", 0.0),
        "carbon_cost_yuan": components.get("carbon_cost", 0.0),
        "risk_premium_yuan": components.get("cvar_risk_premium", 0.0),
        "public_subsidy_yuan_per_year": summary["public_subsidy_yuan_per_year"],
        "demand_kwh": summary["demand_kwh_per_year"],
        "emission_intensity_kg_per_kwh": summary["emission_intensity_kg_per_kwh"],
        "clean_heat_share": summary["clean_heat_share"],
        "worst_unserved_rate": summary["worst_scenario_unserved_rate"],
        "occupied_temperature_deviation_degree_hours": summary[
            "occupied_temperature_deviation_degree_hours"
        ],
        "grid_connection_capacity_kw": summary["grid_connection_capacity_kw"],
        "building_flex_firm_capacity_kw": summary[
            "building_flex_firm_capacity_kw"
        ],
        "district_a_ground_hp_kw": capacities.get("district_a_ground_hp", 0.0),
        "district_b_ground_hp_kw": capacities.get("district_b_ground_hp", 0.0),
        "air_source_hp_kw": (
            capacities.get("district_a_air_hp", 0.0)
            + capacities.get("distributed_air_hp_b", 0.0)
        ),
        "electric_backup_kw": capacities.get("district_a_emergency_eb", 0.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage affordability sensitivity for Wuhan")
    parser.add_argument("--rates", default="0,0.15,0.25,0.3,0.35,0.4")
    parser.add_argument("--time-limit", type=float, default=90.0)
    args = parser.parse_args()
    rates = [float(value) for value in args.rates.split(",")]
    if any(not 0.0 <= rate <= 1.0 for rate in rates):
        raise ValueError("subsidy rates must lie in [0,1]")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    config, frame = scenario3_bundle()
    rows = [solve_rate(config, frame, rate, args.time_limit) for rate in rates]
    result = pd.DataFrame(rows).sort_values("subsidy_fraction")
    result.to_csv(OUTPUT / "scenario3_affordability_sensitivity.csv", index=False)

    fixed_config, fixed_frame = scenario3_bundle(enable_building_flex=False)
    legacy_config, legacy_frame = scenario3_bundle(
        legacy_behavior=True, enable_building_flex=False
    )
    fixed = solve_rate(fixed_config, fixed_frame, 0.0, args.time_limit)
    legacy = solve_rate(legacy_config, legacy_frame, 0.0, args.time_limit)
    current = result.iloc[0].to_dict()
    improvement = pd.DataFrame([
        {"stage": "原始：行为冲击双计、无建筑柔性容量信用", **legacy},
        {"stage": "修正：行为冲击仅计一次且按占用加权", **fixed},
        {"stage": "优化：再计入建筑热惯性可靠削峰", **current},
    ])
    improvement.to_csv(OUTPUT / "scenario3_model_improvement.csv", index=False)

    planning = config["planning"]
    rate = float(planning["discount_rate"])
    years = int(planning["asset_lifetime_years"])
    crf = rate * (1.0 + rate) ** years / ((1.0 + rate) ** years - 1.0)
    sources = {str(source["name"]): source for source in config["sources"]}
    ground_capex = crf * (
        float(current["district_a_ground_hp_kw"])
        * float(sources["district_a_ground_hp"]["capex_yuan_per_kw"])
        + float(current["district_b_ground_hp_kw"])
        * float(sources["district_b_ground_hp"]["capex_yuan_per_kw"])
    )
    grid_capex = (
        crf
        * float(current["grid_connection_capacity_kw"])
        * float(config["grid_connection"]["capex_yuan_per_kw"])
    )
    other_capex = max(
        0.0,
        float(current["net_annualized_capex_yuan"]) - ground_capex - grid_capex,
    )
    allocation_cases = [
        ("供热独立承担全部投资", 1.00, 1.00),
        ("公共配电仅4%计入供热", 0.04, 1.00),
        ("配电共享 + 热泵80%计入供热", 0.04, 0.80),
        ("配电共享 + 热泵65%计入供热", 0.04, 0.65),
        ("配电共享 + 热泵50%计入供热", 0.04, 0.50),
    ]
    allocation_rows = []
    for label, grid_fraction, heat_pump_fraction in allocation_cases:
        regulated_capex = (
            other_capex
            + grid_fraction * grid_capex
            + heat_pump_fraction * ground_capex
        )
        regulated_cost = (
            regulated_capex
            + float(current["operating_cost_yuan"])
            + float(current["carbon_cost_yuan"])
        )
        allocation_rows.append({
            "case": label,
            "grid_heating_allocation_fraction": grid_fraction,
            "ground_hp_heating_allocation_fraction": heat_pump_fraction,
            "regulated_annualized_capex_yuan": regulated_capex,
            "shared_non_heating_capex_yuan": (
                float(current["net_annualized_capex_yuan"]) - regulated_capex
            ),
            "levelized_heat_cost_yuan_per_kwh": (
                regulated_cost / float(current["demand_kwh"])
            ),
            "total_system_objective_yuan_per_year": float(
                current["objective_yuan_per_year"]
            ),
            "worst_unserved_rate": float(current["worst_unserved_rate"]),
        })
    pd.DataFrame(allocation_rows).to_csv(
        OUTPUT / "scenario3_multi_energy_allocation.csv", index=False
    )

    target = 0.40
    lower = result[result["levelized_heat_cost_yuan_per_kwh"] > target].tail(1)
    upper = result[result["levelized_heat_cost_yuan_per_kwh"] <= target].head(1)
    threshold = None
    if not lower.empty and not upper.empty:
        x0 = float(lower.iloc[0]["subsidy_fraction"])
        y0 = float(lower.iloc[0]["levelized_heat_cost_yuan_per_kwh"])
        x1 = float(upper.iloc[0]["subsidy_fraction"])
        y1 = float(upper.iloc[0]["levelized_heat_cost_yuan_per_kwh"])
        threshold = x0 + (target - y0) * (x1 - x0) / (y1 - y0)

    baseline = result.iloc[0]
    variable_unit_cost = (
        float(baseline["operating_cost_yuan"])
        + float(baseline["carbon_cost_yuan"])
    ) / float(baseline["demand_kwh"])
    required_demand = float(baseline["net_annualized_capex_yuan"]) / max(
        target - variable_unit_cost, 1e-9
    )
    metadata = {
        "target_levelized_heat_cost_yuan_per_kwh": target,
        "interpolated_uniform_capex_subsidy_threshold": threshold,
        "baseline_variable_cost_yuan_per_kwh": variable_unit_cost,
        "same_peak_required_heat_sales_kwh_for_target": required_demand,
        "same_peak_heat_sales_increase_fraction": (
            required_demand / float(baseline["demand_kwh"]) - 1.0
        ),
        "interpretation": (
            "Subsidy is a regulated-tariff lever, not a reduction in total social cost. "
            "The heat-sales estimate assumes unchanged peak capacity and unit variable cost."
        ),
    }
    (OUTPUT / "scenario3_affordability_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(result.to_string(index=False))
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
