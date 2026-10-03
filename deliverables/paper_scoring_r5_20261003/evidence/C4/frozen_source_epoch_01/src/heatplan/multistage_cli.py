from __future__ import annotations

import argparse
import json
from pathlib import Path

from .multistage import solve_joint_multistage


def main() -> None:
    parser = argparse.ArgumentParser(description="Joint multi-stage northern district-heating planner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeseries", required=True)
    parser.add_argument("--scenario-specs", required=True)
    parser.add_argument("--policy-pathway", required=True)
    parser.add_argument("--feasibility")
    parser.add_argument("--output", default="outputs/v7_joint_multistage")
    parser.add_argument("--time-limit", type=float, default=300.0)
    args = parser.parse_args()

    artifacts = solve_joint_multistage(
        args.config, args.timeseries, args.scenario_specs, args.policy_pathway,
        feasibility_path=args.feasibility, time_limit=args.time_limit,
    )
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    artifacts.stage_summary.to_csv(output / "stage_summary.csv", index=False)
    (output / "joint_summary.json").write_text(
        json.dumps(artifacts.summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    artifacts.build_schedule.to_csv(output / "build_schedule.csv", index=False)
    artifacts.cumulative_investments.to_csv(output / "cumulative_investments.csv", index=False)
    artifacts.dispatch.to_csv(output / "dispatch.csv", index=False)
    artifacts.hydraulic_postcheck.to_csv(output / "hydraulic_postcheck.csv", index=False)
    if artifacts.feasibility_report is not None:
        artifacts.feasibility_report.to_csv(output / "feasibility_report.csv", index=False)
    if artifacts.finance is not None:
        artifacts.finance.cashflow.to_csv(output / "finance_cashflow.csv", index=False)
        (output / "finance_metrics.json").write_text(
            json.dumps(artifacts.finance.metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (output / "solver.json").write_text(json.dumps({
        "status": int(artifacts.result.status),
        "message": str(artifacts.result.message),
        "joint_present_value_yuan": float(artifacts.result.fun),
        "mip_gap": float(getattr(artifacts.result, "mip_gap", float("nan"))),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(artifacts.stage_summary.to_string(index=False))


if __name__ == "__main__":
    main()
