"""定位武汉冻结方案在全年回放中的不可行约束（逐项松弛，一次只放开一项）。

做法：以冻结投资配置为基准，对若干候选约束组各建一个副本，只放开其中一组，
重新求解全年 MILP。哪一组放开后可行，哪一组就是阻塞项。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
HERE = Path(__file__).resolve().parent

from heatplan.chronology import prepare_fixed_investment_plan  # noqa: E402
from heatplan.io import load_config  # noqa: E402
from heatplan.model import HeatPlanningModel  # noqa: E402
from heatplan.solution_quality import SolveFailure  # noqa: E402

REP = HERE / "scenario3_wuhan_representative_days_2023_defrost.csv"
ANN = ROOT / "data" / "public_scenario_inputs" / "scenario3_wuhan_full_year_2023.csv"
CFG = ROOT / "data" / "public_scenario_inputs" / "scenario3_wuhan_joint_public.yaml"


def relax_grid(config: dict) -> None:
    grid = config.setdefault("grid_connection", {})
    grid["existing_capacity_kw"] = float(grid.get("existing_capacity_kw", 0.0)) * 3.0
    grid["max_new_capacity_kw"] = 0.0
    grid["firm_capacity_method"] = "all_installed_electric"
    grid["firm_simultaneity"] = 0.0


def relax_ground(config: dict) -> None:
    for section in ("multi_energy_services", "sources"):
        block = config.get(section)
        if isinstance(block, dict):
            for value in block.values():
                if isinstance(value, dict):
                    value.pop("ground_thermal_balance", None)
    for source in config.get("sources", []):
        source.pop("ground_thermal_balance", None)


def relax_policy(config: dict) -> None:
    policy = config.setdefault("policy", {})
    for key in list(policy):
        if "share" in key or "minimum_clean" in key:
            policy[key] = 0.0


def relax_carbon(config: dict) -> None:
    planning = config.setdefault("planning", {})
    planning.pop("emission_cap_kg", None)
    planning.pop("carbon_cap_kg", None)
    planning["exergy_penalty_yuan_per_kwh"] = 0.0


VARIANTS = {
    "baseline": lambda c: None,
    "relax_grid_capacity": relax_grid,
    "relax_ground_balance": relax_ground,
    "relax_policy_shares": relax_policy,
}


def main() -> None:
    rep = pd.read_csv(REP)
    ann = pd.read_csv(ANN)
    plan = prepare_fixed_investment_plan(load_config(CFG), rep)
    (HERE / "fixed_investment_config.yaml").write_text(
        json.dumps(plan.fixed_config, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    results = []
    for name, mutate in VARIANTS.items():
        config = copy.deepcopy(plan.fixed_config)
        mutate(config)
        record = {"variant": name}
        try:
            model = HeatPlanningModel(config, ann)
            model.build()
            summary = model.solve(mip_gap=None, time_limit=900)
            record["status"] = "feasible"
            record["objective"] = summary.get("objective_value")
            record["unserved_rate"] = summary.get("unserved_rate")
        except SolveFailure as exc:
            record["status"] = "infeasible"
            record["message"] = str(exc)[:200]
        except Exception as exc:  # noqa: BLE001
            record["status"] = f"error:{type(exc).__name__}"
            record["message"] = str(exc)[:200]
        print(json.dumps(record, ensure_ascii=False), flush=True)
        results.append(record)
    (HERE / "infeasibility_diagnosis.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
