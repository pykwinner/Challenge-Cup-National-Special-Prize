from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Callable

import pandas as pd
import yaml

from heatplan.io import load_inputs, validate_inputs
from heatplan.model import HeatPlanningModel
from heatplan.reporting import export_results
from heatplan.scenarios import build_correlated_northern_scenario_bundle


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = DATA / "public_scenario_inputs"
OUTPUT = ROOT / "outputs" / "reality_calibration"


def comparison_row(name: str, summary: dict) -> dict:
    return {
        "variant": name,
        "objective_yuan_per_year": summary["objective_yuan_per_year"],
        "regulated_lcoh_yuan_per_kwh": summary[
            "regulated_levelized_heat_cost_yuan_per_kwh"
        ],
        "joint_service_lcoe_yuan_per_kwh": summary[
            "joint_service_levelized_cost_yuan_per_kwh"
        ],
        "heating_cash_operating_cost_yuan_per_year": summary[
            "heating_cash_operating_cost_yuan_per_year"
        ],
        "clean_heat_share": summary["clean_heat_share"],
        "non_fossil_heat_share": summary.get("non_fossil_heat_share"),
        "unserved_rate": summary["unserved_rate"],
        "cooling_unserved_rate": summary["cooling_unserved_rate"],
        "emissions_kg_per_year": summary["emissions_kg_per_year"],
        "cooling_emissions_kg_per_year": summary["cooling_emissions_kg_per_year"],
        "biomass_fuel_kg_per_year": summary["biomass_fuel_kg_per_year"],
        "grid_connection_capacity_kw": summary["grid_connection_capacity_kw"],
        "ground_balance_ratio": summary["ground_extraction_to_rejection_ratio"],
        "direct_fossil_emissions_kg_per_year": summary.get(
            "direct_fossil_emissions_kg_per_year"
        ),
        "direct_fossil_emission_intensity_kg_per_m2": summary.get(
            "direct_fossil_emission_intensity_kg_per_m2"
        ),
    }


def scenario_bundle(config: dict, base: pd.DataFrame, specs_path: Path) -> pd.DataFrame:
    specs = yaml.safe_load(specs_path.read_text(encoding="utf-8"))
    demand_columns = [
        column
        for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    bundle = build_correlated_northern_scenario_bundle(
        base,
        demand_columns,
        list(specs["scenarios"]),
        period_weight=float(specs.get("period_weight", 1.0)),
    )
    config["planning"]["risk"] = dict(specs.get("risk", {}))
    validate_inputs(config, bundle)
    return bundle


def solve_variant(
    name: str,
    config_path: Path,
    timeseries_path: Path,
    specs_path: Path | None = None,
    mutate: Callable[[dict], None] | None = None,
) -> dict:
    config, frame = load_inputs(config_path, timeseries_path)
    config = copy.deepcopy(config)
    if mutate:
        mutate(config)
    if specs_path:
        frame = scenario_bundle(config, frame, specs_path)
    validate_inputs(config, frame)
    model = HeatPlanningModel(config, frame)
    artifacts = model.solve(time_limit=120)
    directory = OUTPUT / name
    export_results(directory, artifacts, config, model.ts)
    summary = artifacts.summary
    return comparison_row(name, summary)


def set_pellet_price(price: float) -> Callable[[dict], None]:
    def mutate(config: dict) -> None:
        chain = config["biomass_supply_chains"][0]
        chain["procurement_cost_yuan_per_kg"] = price
        chain["handling_cost_yuan_per_kg"] = 0.0
        chain["transport_cost_yuan_per_tonne_km"] = 0.0

    return mutate


def main() -> None:
    parser = argparse.ArgumentParser(description="三类场景现实证据校准回放")
    parser.add_argument(
        "--compile-existing",
        action="store_true",
        help="不重新求解，只汇总已落盘的九组结果",
    )
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    if args.compile_existing:
        names = [
            "scenario2_legacy_assumptions",
            "scenario2_evidence_resident_tariff",
            "scenario2_evidence_general_meter_proxy",
            "scenario2_pellet_stress_1_20",
            "scenario2_biomass_policy_20pct",
            "scenario1_public_baseline",
            "scenario1_reliability_overlay",
            "scenario3_heat_only",
            "scenario3_joint_heating_cooling",
        ]
        for name in names:
            path = OUTPUT / name / "summary.json"
            if not path.exists():
                raise FileNotFoundError(f"缺少已求解结果: {path}")
            rows.append(comparison_row(
                name, json.loads(path.read_text(encoding="utf-8"))
            ))
        comparison = pd.DataFrame(rows)
        comparison.to_csv(
            OUTPUT / "reality_calibration_comparison.csv",
            index=False,
            encoding="utf-8-sig",
        )
        (OUTPUT / "reality_calibration_summary.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(comparison.to_string(index=False))
        return

    scenario2_timeseries = PUBLIC / "scenario2_guyuan_representative_days_2023.csv"
    scenario2_specs = DATA / "scenario2_scenario_specs.yaml"
    evidence_config = PUBLIC / "scenario2_guyuan_evidence_public.yaml"
    rows.append(solve_variant(
        "scenario2_legacy_assumptions",
        DATA / "scenario2_distributed_clean.yaml",
        scenario2_timeseries,
        scenario2_specs,
    ))
    rows.append(solve_variant(
        "scenario2_evidence_resident_tariff",
        evidence_config,
        scenario2_timeseries,
        scenario2_specs,
    ))

    def general_meter(config: dict) -> None:
        config["planning"]["electricity_price_column"] = "electricity_price"

    rows.append(solve_variant(
        "scenario2_evidence_general_meter_proxy",
        evidence_config,
        scenario2_timeseries,
        scenario2_specs,
        general_meter,
    ))
    rows.append(solve_variant(
        "scenario2_pellet_stress_1_20",
        evidence_config,
        scenario2_timeseries,
        scenario2_specs,
        set_pellet_price(1.20),
    ))

    def biomass_policy_20(config: dict) -> None:
        config["policy"]["minimum_biomass_heat_share"] = 0.20

    rows.append(solve_variant(
        "scenario2_biomass_policy_20pct",
        evidence_config,
        scenario2_timeseries,
        scenario2_specs,
        biomass_policy_20,
    ))

    scenario1_timeseries = PUBLIC / "scenario1_urumqi_representative_days_2023.csv"
    scenario1_specs = DATA / "scenario1_scenario_specs.yaml"
    rows.append(solve_variant(
        "scenario1_public_baseline",
        PUBLIC / "scenario1_urumqi_deterministic_public.yaml",
        scenario1_timeseries,
    ))
    rows.append(solve_variant(
        "scenario1_reliability_overlay",
        PUBLIC / "scenario1_reliability_deterministic_public.yaml",
        scenario1_timeseries,
    ))

    scenario3_timeseries = PUBLIC / "scenario3_wuhan_representative_days_2023.csv"
    rows.append(solve_variant(
        "scenario3_heat_only",
        PUBLIC / "scenario3_wuhan_public.yaml",
        scenario3_timeseries,
    ))
    rows.append(solve_variant(
        "scenario3_joint_heating_cooling",
        PUBLIC / "scenario3_wuhan_joint_public.yaml",
        scenario3_timeseries,
    ))

    comparison = pd.DataFrame(rows)
    comparison.to_csv(
        OUTPUT / "reality_calibration_comparison.csv", index=False, encoding="utf-8-sig"
    )
    (OUTPUT / "reality_calibration_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(comparison.to_string(index=False))


if __name__ == "__main__":
    main()
