from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pandas as pd
import yaml

from .io import load_inputs, validate_inputs
from .experiments import frozen_plan_scenario_replay
from .model import HeatPlanningModel
from .reporting import export_results
from .scenarios import build_correlated_northern_scenario_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Shared-investment northern scenario and CVaR planning")
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeseries", required=True)
    parser.add_argument("--scenario-specs", required=True)
    parser.add_argument("--output", default="outputs/v5_scenario")
    parser.add_argument("--time-limit", type=float, default=120.0)
    args = parser.parse_args()

    config, base = load_inputs(args.config, args.timeseries)
    specs = yaml.safe_load(Path(args.scenario_specs).read_text(encoding="utf-8"))
    demand_columns = [column for node in config["nodes"] for column in node.get("demand_columns", {}).values()]
    bundle = build_correlated_northern_scenario_bundle(
        base,
        demand_columns,
        list(specs["scenarios"]),
        period_weight=float(specs.get("period_weight", config["planning"].get("representative_day_weight", 1.0))),
    )
    validate_inputs(config, bundle)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    bundle.to_csv(output / "scenario_bundle.csv", index=False)
    frozen_plan_scenario_replay(config, base, bundle).to_csv(
        output / "v4_frozen_plan_replay.csv", index=False
    )

    neutral_config = copy.deepcopy(config)
    neutral_config["planning"]["risk"] = {"cvar_weight": 0.0}
    neutral = HeatPlanningModel(neutral_config, bundle).solve(time_limit=args.time_limit)

    risk_config = copy.deepcopy(config)
    risk_config["planning"]["risk"] = dict(specs.get("risk", {}))
    risk_model = HeatPlanningModel(risk_config, bundle)
    risk = risk_model.solve(time_limit=args.time_limit)
    export_results(output, risk, risk_config, risk_model.ts)

    comparison = pd.DataFrame(
        [
            {"model": "expected_cost", **{k: v for k, v in neutral.summary.items() if not isinstance(v, dict)}},
            {"model": "expected_cost_plus_cvar", **{k: v for k, v in risk.summary.items() if not isinstance(v, dict)}},
        ]
    )
    comparison.to_csv(output / "risk_comparison.csv", index=False)
    (output / "scenario_results.json").write_text(
        json.dumps(risk.summary["scenario_results"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    neutral.investments.to_csv(output / "investments_expected_cost.csv", index=False)
    risk.investments.to_csv(output / "investments_expected_cost_plus_cvar.csv", index=False)
    print(json.dumps(risk.summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
