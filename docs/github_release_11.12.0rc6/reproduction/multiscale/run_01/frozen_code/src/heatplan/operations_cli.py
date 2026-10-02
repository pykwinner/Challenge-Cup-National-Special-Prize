from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from .btes import btes_go_no_go, simulate_btes_long_term
from .feasibility import apply_feasibility_exclusions, evaluate_feasibility, load_feasibility
from .hydraulics import hydraulic_thermal_postcheck
from .io import load_inputs
from .model import HeatPlanningModel
from .mpc import rolling_horizon_dispatch
from .validation import monte_carlo_out_of_sample_replay


def main() -> None:
    parser = argparse.ArgumentParser(description="Engineering checks, MPC and out-of-sample validation")
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeseries", required=True)
    parser.add_argument("--feasibility")
    parser.add_argument("--output", default="outputs/v7_operations")
    parser.add_argument("--mpc-horizon", type=int, default=12)
    parser.add_argument("--mpc-steps", type=int, default=6)
    parser.add_argument("--oos-samples", type=int, default=20)
    parser.add_argument("--btes-spec")
    parser.add_argument("--time-limit", type=float, default=60.0)
    args = parser.parse_args()

    config, frame = load_inputs(args.config, args.timeseries)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if args.feasibility:
        result = evaluate_feasibility(load_feasibility(args.feasibility))
        result.report.to_csv(output / "feasibility_report.csv", index=False)
        config = apply_feasibility_exclusions(config, result)
    nominal = HeatPlanningModel(config, frame).solve(time_limit=args.time_limit)
    hydraulic_thermal_postcheck(config, nominal.investments, nominal.dispatch).to_csv(
        output / "hydraulic_postcheck.csv", index=False
    )
    control_frame = frame.iloc[: min(len(frame), args.mpc_steps + args.mpc_horizon - 1)].copy()
    rolling_horizon_dispatch(
        config, control_frame, horizon_hours=args.mpc_horizon,
        control_step_hours=1, time_limit_per_horizon=args.time_limit,
    ).to_csv(output / "mpc_dispatch.csv", index=False)
    monte_carlo_out_of_sample_replay(
        config, frame, samples=args.oos_samples, time_limit=args.time_limit
    ).to_csv(output / "out_of_sample_replay.csv", index=False)
    if args.btes_spec:
        specification = yaml.safe_load(Path(args.btes_spec).read_text(encoding="utf-8"))
        btes = simulate_btes_long_term(specification)
        btes.to_csv(output / "btes_long_term.csv", index=False)
        (output / "btes_go_no_go.json").write_text(
            json.dumps(btes_go_no_go(btes, specification), ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
