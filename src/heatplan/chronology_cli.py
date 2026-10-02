from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .chronology import (
    expand_typical_day_to_chronological_year,
    prepare_full_year_timeseries,
    seasonal_storage_postcheck,
)
from .io import load_inputs, validate_inputs
from .experiments import freeze_investment_plan
from .model import HeatPlanningModel
from .reporting import export_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous chronology and seasonal-storage runner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeseries", required=True)
    parser.add_argument("--output", default="outputs/v7_chronology")
    parser.add_argument("--timestamp-column", default="timestamp")
    parser.add_argument("--expand-demo-days", type=int, default=0)
    parser.add_argument("--solve", action="store_true")
    parser.add_argument("--time-limit", type=float, default=600.0)
    args = parser.parse_args()

    config, frame = load_inputs(args.config, args.timeseries)
    demand_columns = [column for node in config["nodes"] for column in node.get("demand_columns", {}).values()]
    if args.expand_demo_days > 0:
        chronological = expand_typical_day_to_chronological_year(
            frame, demand_columns, days=args.expand_demo_days
        )
    else:
        chronological = prepare_full_year_timeseries(frame, args.timestamp_column)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    chronological.to_csv(output / "chronological_timeseries.csv", index=False)
    if args.solve:
        validate_inputs(config, chronological)
        artifacts = HeatPlanningModel(config, chronological).solve(time_limit=args.time_limit)
        export_results(output, artifacts, config, chronological)
        fixed = freeze_investment_plan(config, artifacts, relax_reliability=False)
        seasonal_storage_postcheck(fixed, artifacts.dispatch, expected_timeseries=chronological).to_csv(
            output / "seasonal_storage_postcheck.csv", index=False
        )


if __name__ == "__main__":
    main()
