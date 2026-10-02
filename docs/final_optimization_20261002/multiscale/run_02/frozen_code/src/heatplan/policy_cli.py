from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pandas as pd
import yaml

from .experiments import carry_forward_investments
from .io import load_inputs, validate_inputs
from .model import HeatPlanningModel
from .reporting import export_results
from .scenarios import build_correlated_northern_scenario_bundle


def _apply_stage_environment(frame: pd.DataFrame, demand_columns: list[str], stage: dict) -> pd.DataFrame:
    result = frame.copy()
    result[demand_columns] *= float(stage.get("demand_multiplier", 1.0))
    if "outdoor_temp" in result:
        result["outdoor_temp"] += float(stage.get("temperature_offset_c", 0.0))
    if "waste_heat_availability" in result:
        result["waste_heat_availability"] *= float(stage.get("waste_heat_multiplier", 1.0))
        result["waste_heat_availability"] = result["waste_heat_availability"].clip(0.0, 1.0)
    if "electricity_price" in result:
        result["electricity_price"] *= float(stage.get("electricity_price_multiplier", 1.0))
    if "grid_emission_factor" in result:
        result["grid_emission_factor"] *= float(stage.get("grid_emission_multiplier", 1.0))
    if "renewable_power_available_kw" in result:
        result["renewable_power_available_kw"] *= float(stage.get("renewable_availability_multiplier", 1.0))
    if "chp_minimum_electricity_kw" in result:
        result["chp_minimum_electricity_kw"] *= float(stage.get("chp_minimum_electricity_multiplier", 1.0))
    return result


def _add_stage_build_limits(config: dict, stage: dict) -> None:
    limits = stage.get("additional_build_limits", {})
    source_map = {item["name"]: item for item in config["sources"]}
    for name, value in limits.get("sources_kw", {}).items():
        source_map[name]["max_new_capacity_kw"] = float(source_map[name].get("max_new_capacity_kw", 0.0)) + float(value)
    storage_map = {item["name"]: item for item in config["storages"]}
    for name, value in limits.get("storages_kwh", {}).items():
        storage_map[name]["max_new_capacity_kwh"] = float(
            storage_map[name].get("max_new_capacity_kwh", 0.0)
        ) + float(value)
    if "grid_connection_kw" in limits:
        grid = config["grid_connection"]
        grid["max_new_capacity_kw"] = float(grid.get("max_new_capacity_kw", 0.0)) + float(
            limits["grid_connection_kw"]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Policy-constrained rolling pathway for northern district heating")
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeseries", required=True)
    parser.add_argument("--scenario-specs", required=True)
    parser.add_argument("--policy-pathway", required=True)
    parser.add_argument("--output", default="outputs/v6_policy_pathway")
    parser.add_argument("--time-limit", type=float, default=180.0)
    args = parser.parse_args()

    base_config, base_frame = load_inputs(args.config, args.timeseries)
    scenario_spec = yaml.safe_load(Path(args.scenario_specs).read_text(encoding="utf-8"))
    pathway = yaml.safe_load(Path(args.policy_pathway).read_text(encoding="utf-8"))
    demand_columns = [
        column for node in base_config["nodes"] for column in node.get("demand_columns", {}).values()
    ]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    current_config = copy.deepcopy(base_config)
    legacy_annualized_private_capex = 0.0
    stage_rows: list[dict] = []
    investment_rows: list[pd.DataFrame] = []
    for stage in pathway["stages"]:
        name = str(stage["name"])
        stage_config = copy.deepcopy(current_config)
        _add_stage_build_limits(stage_config, stage)
        stage_config["policy"] = copy.deepcopy(stage.get("policy", {}))
        stage_config["policy"]["legacy_annualized_capex_yuan"] = legacy_annualized_private_capex
        stage_config["planning"]["risk"] = copy.deepcopy(
            stage.get("risk", scenario_spec.get("risk", {"cvar_weight": 0.0}))
        )
        stage_base = _apply_stage_environment(base_frame, demand_columns, stage)
        bundle = build_correlated_northern_scenario_bundle(
            stage_base,
            demand_columns,
            list(scenario_spec["scenarios"]),
            period_weight=float(scenario_spec.get("period_weight", 180.0)),
        )
        validate_inputs(stage_config, bundle)
        model = HeatPlanningModel(stage_config, bundle)
        artifacts = model.solve(time_limit=args.time_limit)
        stage_dir = output / name
        export_results(stage_dir, artifacts, stage_config, model.ts)
        bundle.to_csv(stage_dir / "scenario_bundle.csv", index=False)

        flat = {k: v for k, v in artifacts.summary.items() if not isinstance(v, dict)}
        stage_rows.append({"stage": name, **flat})
        investments = artifacts.investments.copy()
        investments.insert(0, "stage", name)
        investment_rows.append(investments)
        legacy_annualized_private_capex += float(
            artifacts.summary.get("components_yuan_per_year", {}).get("annualized_capex", 0.0)
        )
        current_config = carry_forward_investments(stage_config, artifacts)

    summary = pd.DataFrame(stage_rows)
    summary.to_csv(output / "policy_pathway_summary.csv", index=False)
    pd.concat(investment_rows, ignore_index=True).to_csv(output / "policy_pathway_investments.csv", index=False)
    (output / "policy_pathway.json").write_text(
        json.dumps(stage_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
