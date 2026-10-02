from __future__ import annotations

import argparse
import json

from .model import HeatPlanningModel
from .reporting import export_results


def main() -> None:
    parser = argparse.ArgumentParser(description="CP-202611 source-network-load-storage MILP baseline")
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeseries", required=True)
    parser.add_argument("--output", default="outputs/baseline")
    parser.add_argument("--mip-gap", type=float)
    parser.add_argument("--time-limit", type=float)
    args = parser.parse_args()

    model = HeatPlanningModel.from_files(args.config, args.timeseries)
    artifacts = model.solve(mip_gap=args.mip_gap, time_limit=args.time_limit)
    export_results(args.output, artifacts, model.config, model.ts)
    print(json.dumps(artifacts.summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

