from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import yaml

from .chronology import (
    AnnualReplayReadinessError,
    annual_replay_acceptance_audit,
    annual_replay_preflight,
    chronology_quality_audit,
    chronology_required_columns,
    estimate_annual_model_structure,
    run_fixed_investment_annual_replay,
    seasonal_storage_postcheck,
)
from .io import load_config
from .reporting import export_results


def _plot_comparison(
    path: Path,
    comparison: pd.DataFrame,
    *,
    full_year: bool,
) -> None:
    usable = comparison[
        comparison["relative_delta"].notna()
        & comparison["metric"].isin(
            {
                "demand_kwh_per_year",
                "effective_heat_demand_kwh_per_year",
                "emissions_kg_per_year",
                "electricity_kwh_per_year",
                "heating_cash_operating_cost_yuan_per_year",
            }
        )
    ].copy()
    if usable.empty:
        return
    usable["difference_percent"] = 100.0 * usable["relative_delta"]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    colors = ["#2a9d8f" if value <= 0 else "#e76f51" for value in usable["difference_percent"]]
    ax.barh(usable["metric"], usable["difference_percent"], color=colors)
    ax.axvline(0.0, color="#264653", linewidth=1.0)
    ax.set_xlabel("chronology difference from representative-period plan (%)")
    ax.set_title(
        "Fixed-investment annual replay gap"
        if full_year
        else "Short-horizon replay smoke gap (not an annual comparison)"
    )
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def _plot_representative_coverage(
    path: Path,
    audit: pd.DataFrame,
) -> None:
    demand = audit[audit["series_kind"] == "demand"].copy()
    if demand.empty:
        return
    demand["peak_capture_percent"] = 100.0 * demand["peak_capture_ratio"]
    demand["energy_capture_percent"] = 100.0 * (
        1.0 + demand["total_relative_bias"]
    )
    positions = range(len(demand))
    fig, ax = plt.subplots(figsize=(9.5, max(4.2, 0.62 * len(demand) + 1.8)))
    ax.barh(
        [position - 0.18 for position in positions],
        demand["peak_capture_percent"],
        height=0.34,
        label="peak capture",
        color="#2E74B5",
    )
    ax.barh(
        [position + 0.18 for position in positions],
        demand["energy_capture_percent"],
        height=0.34,
        label="weighted energy ratio",
        color="#1E8E6E",
    )
    ax.axvline(100.0, color="#D97706", linewidth=1.4, linestyle="--")
    ax.set_yticks(list(positions), demand["column"])
    ax.set_xlabel("representative / chronology (%)")
    ax.set_title("Representative-period coverage preflight")
    ax.legend(frameon=False, ncol=2, loc="lower right")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plan with representative periods, freeze all investments, and "
            "replay a continuous annual chronology"
        )
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--representative-timeseries", required=True)
    parser.add_argument("--annual-timeseries", required=True)
    parser.add_argument("--output", default="outputs/v11_annual_replay")
    parser.add_argument("--timestamp-column", default="timestamp")
    parser.add_argument("--planning-time-limit", type=float, default=180.0)
    parser.add_argument("--replay-time-limit", type=float, default=1800.0)
    parser.add_argument("--planning-mip-gap", type=float)
    parser.add_argument("--replay-mip-gap", type=float)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="audit chronology and representative-period coverage without solving",
    )
    parser.add_argument(
        "--allow-short-horizon",
        action="store_true",
        help="allow a non-annual chronology for explicit software smoke tests",
    )
    parser.add_argument(
        "--estimate-model-size",
        action="store_true",
        help="build two short pilots and project full-horizon model structure",
    )
    parser.add_argument(
        "--pilot-hours",
        type=int,
        default=168,
        help="largest prefix used by the model-size pilot (default: 168)",
    )
    args = parser.parse_args()
    if args.pilot_hours < 2:
        parser.error("--pilot-hours must be at least 2")

    config = load_config(args.config)
    representative = pd.read_csv(args.representative_timeseries)
    annual = pd.read_csv(args.annual_timeseries)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    demand_columns = [
        str(column)
        for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    raw_quality = chronology_quality_audit(
        annual,
        timestamp_column=args.timestamp_column,
        expected_step_hours=float(
            config["planning"].get("time_step_hours", 1.0)
        ),
        required_columns=chronology_required_columns(
            config, args.timestamp_column
        ),
        nonnegative_columns=demand_columns,
    )
    (output / "chronology_quality_audit.json").write_text(
        json.dumps(raw_quality, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not raw_quality["chronology_valid"]:
        parser.error(
            "chronology preflight failed: "
            + ", ".join(raw_quality["issues"])
        )
    preflight = annual_replay_preflight(
        config,
        representative,
        annual,
        timestamp_column=args.timestamp_column,
        require_full_year=False,
    )
    preflight.representative_coverage_audit.to_csv(
        output / "representative_coverage_audit.csv", index=False
    )
    preflight.representative_recommendations.to_csv(
        output / "representative_coverage_recommendations.csv", index=False
    )
    (output / "scenario_scope_audit.json").write_text(
        json.dumps(
            preflight.scenario_scope_audit,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    review = preflight.representative_coverage_audit[
        preflight.representative_coverage_audit["coverage_status"] == "review"
    ]
    preflight_summary = {
        "chronology_valid": bool(raw_quality["chronology_valid"]),
        "is_full_year_horizon": bool(raw_quality["is_full_year_horizon"]),
        "is_calendar_year_aligned": bool(
            raw_quality["is_calendar_year_aligned"]
        ),
        "rows": int(raw_quality["rows"]),
        "horizon_hours": float(raw_quality["horizon_hours"]),
        "representative_series_review_count": int(len(review)),
        "representative_series_for_review": review["column"].tolist(),
        "representative_coverage_requires_review": bool(len(review)),
        "scenario_scope_mapping_applied": bool(
            preflight.scenario_scope_audit["mapping_applied"]
        ),
        "chronology_ready_for_formal_replay": bool(
            raw_quality["chronology_valid"]
            and raw_quality["is_full_year_horizon"]
        ),
        "preflight_status": (
            "ready_with_representative_warnings"
            if len(review)
            else "ready"
        )
        if raw_quality["is_full_year_horizon"]
        else "short_horizon_only",
    }
    size_estimate = None
    if args.estimate_model_size:
        size_estimate = estimate_annual_model_structure(
            config,
            preflight.chronological_timeseries,
            pilot_hours=args.pilot_hours,
        )
        (output / "annual_replay_model_size_estimate.json").write_text(
            json.dumps(size_estimate, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        projected = size_estimate["projected_full_horizon"]
        preflight_summary["projected_variables"] = projected["variables"]
        preflight_summary["projected_integer_variables"] = projected[
            "integer_variables"
        ]
        preflight_summary["projected_constraints"] = projected["constraints"]
        preflight_summary["projected_matrix_nonzeros"] = projected[
            "matrix_nonzeros"
        ]
        preflight_summary["projected_build_memory_proxy_mb"] = projected[
            "conservative_python_build_memory_proxy_mb"
        ]
        preflight_summary["milp_solve_risk_tier"] = size_estimate[
            "milp_solve_risk_tier"
        ]
    (output / "annual_replay_preflight_summary.json").write_text(
        json.dumps(preflight_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _plot_representative_coverage(
        output / "representative_coverage_audit.png",
        preflight.representative_coverage_audit,
    )
    if not args.allow_short_horizon and not raw_quality["is_full_year_horizon"]:
        parser.error(
            "formal replay requires a continuous 8760/8784-hour horizon; "
            "use --allow-short-horizon only for software smoke tests"
        )
    if args.preflight_only:
        print(json.dumps(preflight_summary, ensure_ascii=False, indent=2))
        return

    try:
        artifacts = run_fixed_investment_annual_replay(
            config,
            representative,
            annual,
            timestamp_column=args.timestamp_column,
            planning_time_limit=args.planning_time_limit,
            replay_time_limit=args.replay_time_limit,
            planning_mip_gap=args.planning_mip_gap,
            replay_mip_gap=args.replay_mip_gap,
            require_full_year=not args.allow_short_horizon,
        )
    except AnnualReplayReadinessError as exc:
        planning_dir = output / "representative_planning"
        export_results(planning_dir, exc.planning, config, representative)
        exc.investment_freeze_audit.to_csv(
            output / "investment_freeze_audit.csv", index=False
        )
        (output / "fixed_investment_readiness_audit.json").write_text(
            json.dumps(exc.readiness_audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output / "fixed_investment_config.yaml").write_text(
            yaml.safe_dump(
                exc.fixed_config, allow_unicode=True, sort_keys=False
            ),
            encoding="utf-8",
        )
        failure = {
            "status": "fixed_investment_readiness_failed",
            "message": str(exc),
            "planning_solver_status": exc.planning.summary["solver_status"],
            "planning_unserved_rate": exc.planning.summary["unserved_rate"],
            "firm_heat_capacity_gap_kw": exc.readiness_audit[
                "firm_heat_capacity_gap_kw"
            ],
            "firm_electric_capacity_gap_kw": exc.readiness_audit["grid"][
                "firm_electric_capacity_gap_kw"
            ],
        }
        (output / "annual_replay_failure_summary.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        parser.error(
            f"{exc}; details: {output / 'fixed_investment_readiness_audit.json'}"
        )

    planning_dir = output / "representative_planning"
    replay_dir = output / "fixed_investment_annual_replay"
    export_results(
        planning_dir,
        artifacts.planning,
        config,
        representative,
    )
    export_results(
        replay_dir,
        artifacts.replay,
        artifacts.fixed_config,
        artifacts.chronological_timeseries,
    )
    artifacts.chronological_timeseries.to_csv(
        output / "chronological_timeseries.csv", index=False
    )
    artifacts.investment_freeze_audit.to_csv(
        output / "investment_freeze_audit.csv", index=False
    )
    artifacts.kpi_comparison.to_csv(
        output / "annual_replay_kpi_comparison.csv", index=False
    )
    execution_profile = {
        "planning": artifacts.planning_model_profile,
        "fixed_investment_replay": artifacts.replay_model_profile,
        "pilot_estimate": size_estimate,
    }
    (output / "model_execution_profile.json").write_text(
        json.dumps(execution_profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "fixed_investment_readiness_audit.json").write_text(
        json.dumps(
            artifacts.fixed_investment_readiness_audit,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    storage_boundary_audit = seasonal_storage_postcheck(
        artifacts.fixed_config, artifacts.replay.dispatch,
        expected_timeseries=artifacts.chronological_timeseries,
    )
    storage_boundary_audit.to_csv(
        output / "seasonal_storage_postcheck.csv", index=False
    )
    (output / "fixed_investment_config.yaml").write_text(
        yaml.safe_dump(
            artifacts.fixed_config,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    chronology_hours = float(artifacts.chronology_audit["horizon_hours"])
    is_full_year = bool(artifacts.chronology_audit["is_full_year_horizon"])
    combined_summary = {
        "all_investments_frozen": bool(
            artifacts.investment_freeze_audit["freeze_ok"].all()
        ),
        "chronological_rows": len(artifacts.chronological_timeseries),
        "chronology_hours": chronology_hours,
        "is_full_year_horizon": is_full_year,
        "is_calendar_year_aligned": bool(
            artifacts.chronology_audit["is_calendar_year_aligned"]
        ),
        "representative_series_review_count": int(len(review)),
        "representative_coverage_requires_review": bool(len(review)),
        "preflight_status": preflight_summary["preflight_status"],
        "comparison_scope": (
            "full_year_chronology"
            if is_full_year
            else "short_horizon_software_smoke_only"
        ),
        "planning_solver_status": artifacts.planning.summary["solver_status"],
        "replay_solver_status": artifacts.replay.summary["solver_status"],
        "planning_unserved_rate": artifacts.planning.summary["unserved_rate"],
        "replay_unserved_rate": artifacts.replay.summary["unserved_rate"],
        "planning_build_seconds": artifacts.planning_model_profile[
            "build_seconds"
        ],
        "planning_solve_seconds": artifacts.planning_model_profile[
            "solve_seconds"
        ],
        "replay_build_seconds": artifacts.replay_model_profile[
            "build_seconds"
        ],
        "replay_solve_seconds": artifacts.replay_model_profile[
            "solve_seconds"
        ],
        "replay_variables": artifacts.replay_model_profile["variables"],
        "replay_integer_variables": artifacts.replay_model_profile[
            "integer_variables"
        ],
        "replay_constraints": artifacts.replay_model_profile["constraints"],
        "replay_matrix_nonzeros": artifacts.replay_model_profile[
            "matrix_nonzeros"
        ],
        "firm_heat_capacity_gap_kw": artifacts.fixed_investment_readiness_audit[
            "firm_heat_capacity_gap_kw"
        ],
        "firm_electric_capacity_gap_kw": artifacts.fixed_investment_readiness_audit[
            "grid"
        ]["firm_electric_capacity_gap_kw"],
    }
    acceptance = annual_replay_acceptance_audit(
        artifacts.fixed_config,
        chronology_audit=artifacts.chronology_audit,
        representative_coverage_audit=artifacts.representative_coverage_audit,
        investment_freeze_audit=artifacts.investment_freeze_audit,
        fixed_investment_readiness_audit=(
            artifacts.fixed_investment_readiness_audit
        ),
        planning_summary=artifacts.planning.summary,
        replay_summary=artifacts.replay.summary,
        storage_boundary_audit=storage_boundary_audit,
    )
    (output / "annual_replay_acceptance_certificate.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    combined_summary["annual_replay_certified"] = bool(
        acceptance["certification_pass"]
    )
    combined_summary["acceptance_failed_gates"] = acceptance["failed_gates"]
    (output / "annual_replay_summary.json").write_text(
        json.dumps(combined_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _plot_comparison(
        output / "annual_replay_gap.png",
        artifacts.kpi_comparison,
        full_year=is_full_year,
    )


if __name__ == "__main__":
    main()
