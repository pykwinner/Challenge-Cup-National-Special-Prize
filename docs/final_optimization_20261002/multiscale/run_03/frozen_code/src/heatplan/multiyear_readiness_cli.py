from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .chronology import save_fixed_investment_plan_checkpoint
from .io import load_config
from .multiyear import audit_fixed_plan_across_years
from .reporting import export_results


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def run_multiyear_manifest(
    manifest_path: str | Path,
    output_root: str | Path,
    *,
    planning_time_limit: float = 180.0,
    planning_mip_gap: float | None = None,
) -> pd.DataFrame:
    manifest_path = Path(manifest_path).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    profiles = payload.get("profiles", [])
    if not profiles:
        raise ValueError("跨年度清单至少需要一个profile")
    summary_rows: list[dict[str, Any]] = []
    for profile in profiles:
        name = str(profile["name"])
        output = output_root / name
        output.mkdir(parents=True, exist_ok=True)
        config_path = _resolve(manifest_path.parent, str(profile["config"]))
        representative_path = _resolve(
            manifest_path.parent, str(profile["representative_timeseries"])
        )
        config = load_config(config_path)
        representative = pd.read_csv(representative_path)
        annual_frames = {
            str(item["year"]): pd.read_csv(
                _resolve(manifest_path.parent, str(item["path"]))
            )
            for item in profile.get("annual_timeseries", [])
        }
        artifacts = audit_fixed_plan_across_years(
            config,
            representative,
            annual_frames,
            timestamp_column="time",
            planning_time_limit=planning_time_limit,
            planning_mip_gap=planning_mip_gap,
        )
        export_results(
            output / "representative_planning",
            artifacts.fixed_plan.planning,
            config,
            representative,
        )
        save_fixed_investment_plan_checkpoint(
            output,
            artifacts.fixed_plan,
            source_config=config,
            representative_timeseries=representative,
        )
        artifacts.year_audit.to_csv(
            output / "multiyear_readiness_audit.csv",
            index=False,
            encoding="utf-8-sig",
        )
        for label, audit in artifacts.coverage_audits.items():
            year_output = output / label
            year_output.mkdir(exist_ok=True)
            audit.to_csv(
                year_output / "representative_coverage_audit.csv",
                index=False,
                encoding="utf-8-sig",
            )
            (year_output / "fixed_investment_readiness_audit.json").write_text(
                json.dumps(
                    artifacts.readiness_audits[label],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (year_output / "scenario_scope_audit.json").write_text(
                json.dumps(
                    artifacts.scenario_scope_audits[label],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        failed = artifacts.year_audit[
            ~artifacts.year_audit["readiness_pass"]
        ]
        selected = artifacts.year_audit[
            artifacts.year_audit["selected_extreme_year"]
        ]
        summary_rows.append(
            {
                "profile": name,
                "years": int(len(artifacts.year_audit)),
                "readiness_pass_years": int(
                    artifacts.year_audit["readiness_pass"].sum()
                ),
                "failed_years": ";".join(failed["year_label"]),
                "selected_extreme_years": ";".join(
                    selected["year_label"]
                ),
                "minimum_heat_capacity_gap_kw": float(
                    artifacts.year_audit["firm_heat_capacity_gap_kw"].min()
                ),
                "minimum_electric_capacity_gap_kw": float(
                    artifacts.year_audit[
                        "firm_electric_capacity_gap_kw"
                    ].min()
                ),
                "representative_planning_solver_status": int(
                    artifacts.fixed_plan.planning.summary["solver_status"]
                ),
                "representative_model_variables": int(
                    artifacts.fixed_plan.planning_model_profile["variables"]
                ),
                "representative_model_integer_variables": int(
                    artifacts.fixed_plan.planning_model_profile[
                        "integer_variables"
                    ]
                ),
                "representative_model_constraints": int(
                    artifacts.fixed_plan.planning_model_profile["constraints"]
                ),
                "representative_model_build_seconds": float(
                    artifacts.fixed_plan.planning_model_profile[
                        "build_seconds"
                    ]
                ),
                "representative_model_solve_seconds": float(
                    artifacts.fixed_plan.planning_model_profile[
                        "solve_seconds"
                    ]
                ),
            }
        )
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(
        output_root / "multiyear_readiness_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (output_root / "multiyear_readiness_summary.json").write_text(
        json.dumps(summary_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="同一固定投资方案的三场景跨年度可靠容量审计"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--planning-time-limit", type=float, default=180.0)
    parser.add_argument("--planning-mip-gap", type=float)
    args = parser.parse_args()
    result = run_multiyear_manifest(
        args.manifest,
        args.output,
        planning_time_limit=args.planning_time_limit,
        planning_mip_gap=args.planning_mip_gap,
    )
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
