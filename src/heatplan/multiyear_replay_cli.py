from __future__ import annotations

import argparse
import gc
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd
import yaml

from .chronology import (
    annual_replay_acceptance_audit,
    load_fixed_investment_plan_checkpoint,
    replay_prepared_fixed_investment_plan,
    save_fixed_investment_plan_checkpoint,
    seasonal_storage_postcheck,
)
from .io import load_config
from .multiyear import audit_fixed_plan_across_years
from .reporting import export_results


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def write_multiyear_replay_rollup(
    output_root: str | Path, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Write one tamper-evident acceptance rollup for completed annual replays."""

    root = Path(output_root).resolve()
    frame = pd.DataFrame(rows)
    certificate_rows: list[dict[str, Any]] = []
    for row in rows:
        certificate = (
            root
            / str(row["profile"])
            / str(row["weather_year"])
            / "annual_replay_acceptance_certificate.json"
        )
        if not certificate.is_file():
            raise ValueError(f"缺少年度验收证书: {certificate}")
        digest = sha256(certificate.read_bytes()).hexdigest()
        payload = json.loads(certificate.read_text(encoding="utf-8"))
        certificate_rows.append(
            {
                "profile": str(row["profile"]),
                "weather_year": str(row["weather_year"]),
                "path": str(certificate.relative_to(root)).replace("\\", "/"),
                "sha256": digest,
                "certification_pass": bool(payload["certification_pass"]),
                "failed_gates": payload["failed_gates"],
            }
        )
    planning_seconds_avoided = 0.0
    for profile in sorted(frame["profile"].astype(str).unique()):
        model_profile = root / profile / "planning_model_profile.json"
        if model_profile.is_file():
            payload = json.loads(model_profile.read_text(encoding="utf-8"))
            planning_seconds_avoided += float(payload.get("solve_seconds", 0.0))
    all_certified = bool(frame["certification_pass"].astype(bool).all())
    heat_zero = bool((frame["heat_unserved_rate"].astype(float) <= 1e-12).all())
    cooling_zero = bool(
        (frame["cooling_unserved_rate"].astype(float) <= 1e-12).all()
    )
    rollup = {
        "schema": "heatplan.multiyear_replay_acceptance_rollup.v1",
        "completed_replays": int(len(frame)),
        "profiles": sorted(frame["profile"].astype(str).unique().tolist()),
        "weather_years": sorted(
            frame["weather_year"].astype(str).unique().tolist()
        ),
        "chronological_hours_verified": int(
            frame["chronological_rows"].astype(int).sum()
        ),
        "all_solver_status_zero": bool(
            (frame["replay_solver_status"].astype(int) == 0).all()
        ),
        "all_certified": all_certified,
        "all_heat_unserved_zero": heat_zero,
        "all_cooling_unserved_zero": cooling_zero,
        "maximum_replay_mip_gap": float(
            frame["replay_mip_gap"].astype(float).max()
        ),
        "total_replay_solve_seconds": float(
            frame["replay_solve_seconds"].astype(float).sum()
        ),
        "planning_solve_seconds_avoided_by_checkpoint": (
            planning_seconds_avoided
        ),
        "acceptance_pass": bool(
            all_certified
            and heat_zero
            and cooling_zero
            and (frame["replay_solver_status"].astype(int) == 0).all()
        ),
        "certificates": certificate_rows,
    }
    (root / "formal_replay_acceptance_rollup.json").write_text(
        json.dumps(rollup, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return rollup


def run_selected_extreme_year_replays(
    manifest_path: str | Path,
    output_root: str | Path,
    *,
    selected_profiles: list[str] | None = None,
    planning_time_limit: float = 240.0,
    replay_time_limit: float = 1800.0,
    planning_mip_gap: float | None = 0.005,
    replay_mip_gap: float | None = 0.005,
    checkpoint_root: str | Path | None = None,
    checkpoint_case_subdir: str | None = None,
) -> pd.DataFrame:
    """Formally replay extremes, optionally reusing solved planning checkpoints."""

    manifest_path = Path(manifest_path).resolve()
    output_root = Path(output_root).resolve()
    checkpoint_root_path = (
        Path(checkpoint_root).resolve() if checkpoint_root is not None else None
    )
    output_root.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    profiles = payload.get("profiles", [])
    if not profiles:
        raise ValueError("跨年度回放清单至少需要一个 profile")
    chosen = set(selected_profiles or [])
    known = {str(profile["name"]) for profile in profiles}
    unknown = chosen - known
    if unknown:
        raise ValueError(f"清单中不存在这些 profile: {sorted(unknown)}")
    summary_path = output_root / "multiyear_replay_summary.json"
    aggregate: list[dict[str, Any]] = []
    if chosen and summary_path.exists():
        prior = json.loads(summary_path.read_text(encoding="utf-8"))
        aggregate = [
            row for row in prior if str(row.get("profile")) not in chosen
        ]
    for profile in profiles:
        name = str(profile["name"])
        if chosen and name not in chosen:
            continue
        profile_output = output_root / name
        profile_output.mkdir(parents=True, exist_ok=True)
        config_path = _resolve(manifest_path.parent, str(profile["config"]))
        representative_path = _resolve(
            manifest_path.parent, str(profile["representative_timeseries"])
        )
        config = load_config(config_path)
        representative = pd.read_csv(representative_path)
        annual_items = {
            str(item["year"]): {
                "path": _resolve(manifest_path.parent, str(item["path"])),
                "timestamp_column": str(item.get("timestamp_column", "time")),
            }
            for item in profile.get("annual_timeseries", [])
        }
        annual_frames = {
            year: pd.read_csv(item["path"])
            for year, item in annual_items.items()
        }
        checkpoint_reused = checkpoint_root_path is not None
        checkpoint_path: Path | None = None
        if checkpoint_reused:
            checkpoint_path = checkpoint_root_path / name
            if checkpoint_case_subdir:
                checkpoint_path = (checkpoint_path / checkpoint_case_subdir).resolve()
                try:
                    checkpoint_path.relative_to(checkpoint_root_path)
                except ValueError as exc:
                    raise ValueError(
                        "检查点案例子目录不得越出检查点根目录"
                    ) from exc
            fixed_plan = load_fixed_investment_plan_checkpoint(
                checkpoint_path,
                expected_config=config,
                expected_representative_timeseries=representative,
            )
            readiness_path = checkpoint_path / "multiyear_readiness_audit.csv"
            if not readiness_path.is_file():
                raise ValueError(
                    f"固定投资检查点缺少跨年度容量审计: {readiness_path}"
                )
            year_audit = pd.read_csv(readiness_path)
            if set(year_audit["year_label"].astype(str)) != set(annual_items):
                raise ValueError(
                    f"{name} 检查点的年度集合与当前清单不匹配"
                )
        else:
            readiness = audit_fixed_plan_across_years(
                config,
                representative,
                annual_frames,
                timestamp_column="time",
                planning_time_limit=planning_time_limit,
                planning_mip_gap=planning_mip_gap,
            )
            fixed_plan = readiness.fixed_plan
            year_audit = readiness.year_audit
        readiness_pass = year_audit["readiness_pass"]
        if readiness_pass.dtype != bool:
            readiness_pass = readiness_pass.astype(str).str.lower().map(
                {"true": True, "false": False}
            )
        if readiness_pass.isna().any() or not bool(readiness_pass.all()):
            failed = year_audit.loc[
                ~readiness_pass.fillna(False), "year_label"
            ].tolist()
            raise RuntimeError(
                f"{name} 固定方案未通过这些年度的容量预审: {failed}"
            )
        selected_mask = year_audit["selected_extreme_year"]
        if selected_mask.dtype != bool:
            selected_mask = (
                selected_mask.astype(str)
                .str.lower()
                .map({"true": True, "false": False})
                .fillna(False)
            )
        selected = (
            year_audit.loc[selected_mask, "year_label"].astype(str).tolist()
        )
        if not selected:
            selected = [
                str(
                    year_audit.sort_values("risk_rank").iloc[0][
                        "year_label"
                    ]
                )
            ]

        export_results(
            profile_output / "representative_planning",
            fixed_plan.planning,
            config,
            representative,
        )
        save_fixed_investment_plan_checkpoint(
            profile_output,
            fixed_plan,
            source_config=config,
            representative_timeseries=representative,
        )
        year_audit.to_csv(
            profile_output / "multiyear_readiness_audit.csv",
            index=False,
            encoding="utf-8-sig",
        )
        print(f"{name}: selected years {','.join(selected)}", flush=True)

        for year in selected:
            item = annual_items[year]
            started = perf_counter()
            artifacts = replay_prepared_fixed_investment_plan(
                config,
                representative,
                annual_frames[year],
                fixed_plan,
                timestamp_column=item["timestamp_column"],
                replay_time_limit=replay_time_limit,
                replay_mip_gap=replay_mip_gap,
                require_full_year=True,
            )
            year_output = profile_output / year
            year_output.mkdir(parents=True, exist_ok=True)
            export_results(
                year_output / "fixed_investment_annual_replay",
                artifacts.replay,
                artifacts.fixed_config,
                artifacts.chronological_timeseries,
            )
            artifacts.kpi_comparison.to_csv(
                year_output / "annual_replay_kpi_comparison.csv",
                index=False,
                encoding="utf-8-sig",
            )
            artifacts.representative_coverage_audit.to_csv(
                year_output / "representative_coverage_audit.csv",
                index=False,
                encoding="utf-8-sig",
            )
            (year_output / "chronology_quality_audit.json").write_text(
                json.dumps(
                    artifacts.chronology_audit, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            (year_output / "fixed_investment_readiness_audit.json").write_text(
                json.dumps(
                    artifacts.fixed_investment_readiness_audit,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            storage_audit = seasonal_storage_postcheck(
                artifacts.fixed_config, artifacts.replay.dispatch,
                expected_timeseries=artifacts.chronological_timeseries,
            )
            storage_audit.to_csv(
                year_output / "seasonal_storage_postcheck.csv", index=False
            )
            acceptance = annual_replay_acceptance_audit(
                artifacts.fixed_config,
                chronology_audit=artifacts.chronology_audit,
                representative_coverage_audit=(
                    artifacts.representative_coverage_audit
                ),
                investment_freeze_audit=artifacts.investment_freeze_audit,
                fixed_investment_readiness_audit=(
                    artifacts.fixed_investment_readiness_audit
                ),
                planning_summary=artifacts.planning.summary,
                replay_summary=artifacts.replay.summary,
                storage_boundary_audit=storage_audit,
            )
            (year_output / "annual_replay_acceptance_certificate.json").write_text(
                json.dumps(acceptance, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            elapsed = perf_counter() - started
            summary = {
                "profile": name,
                "weather_year": year,
                "source_annual_timeseries": str(item["path"]),
                "chronological_rows": int(
                    len(artifacts.chronological_timeseries)
                ),
                "planning_reused": True,
                "planning_checkpoint_reused": checkpoint_reused,
                "planning_checkpoint_path": (
                    str(checkpoint_path) if checkpoint_path is not None else None
                ),
                "planning_solver_status": int(
                    artifacts.planning.summary["solver_status"]
                ),
                "replay_solver_status": int(
                    artifacts.replay.summary["solver_status"]
                ),
                "replay_mip_gap": artifacts.replay.summary.get("mip_gap"),
                "replay_solve_seconds": artifacts.replay_model_profile[
                    "solve_seconds"
                ],
                "elapsed_seconds": elapsed,
                "replay_variables": artifacts.replay_model_profile["variables"],
                "replay_integer_variables": artifacts.replay_model_profile[
                    "integer_variables"
                ],
                "replay_constraints": artifacts.replay_model_profile[
                    "constraints"
                ],
                "heat_unserved_rate": artifacts.replay.summary[
                    "unserved_rate"
                ],
                "cooling_unserved_rate": artifacts.replay.summary.get(
                    "cooling_unserved_rate", 0.0
                ),
                "emissions_kg_per_year": artifacts.replay.summary[
                    "emissions_kg_per_year"
                ],
                "levelized_heat_cost_yuan_per_kwh": artifacts.replay.summary[
                    "regulated_levelized_heat_cost_yuan_per_kwh"
                ],
                "certification_pass": bool(
                    acceptance["certification_pass"]
                ),
                "failed_gates": acceptance["failed_gates"],
            }
            (year_output / "annual_replay_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            aggregate.append(summary)
            pd.DataFrame(aggregate).to_csv(
                output_root / "multiyear_replay_summary.csv",
                index=False,
                encoding="utf-8-sig",
            )
            (output_root / "multiyear_replay_summary.json").write_text(
                json.dumps(aggregate, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            write_multiyear_replay_rollup(output_root, aggregate)
            print(
                f"{name}/{year}: certified={summary['certification_pass']} "
                f"solve={summary['replay_solve_seconds']:.3f}s",
                flush=True,
            )
            del artifacts
            gc.collect()
        del annual_frames, fixed_plan, year_audit
        gc.collect()
    return pd.DataFrame(aggregate)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan once and formally replay selected multi-year extremes"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profiles", nargs="+")
    parser.add_argument("--planning-time-limit", type=float, default=240.0)
    parser.add_argument("--replay-time-limit", type=float, default=1800.0)
    parser.add_argument("--planning-mip-gap", type=float, default=0.005)
    parser.add_argument("--replay-mip-gap", type=float, default=0.005)
    parser.add_argument(
        "--checkpoint-root",
        help="Reuse one validated fixed-plan checkpoint subdirectory per profile",
    )
    parser.add_argument(
        "--checkpoint-case-subdir",
        help="Optional nested case directory such as weight_0p05",
    )
    args = parser.parse_args()
    result = run_selected_extreme_year_replays(
        args.manifest,
        args.output,
        selected_profiles=args.profiles,
        planning_time_limit=args.planning_time_limit,
        replay_time_limit=args.replay_time_limit,
        planning_mip_gap=args.planning_mip_gap,
        replay_mip_gap=args.replay_mip_gap,
        checkpoint_root=args.checkpoint_root,
        checkpoint_case_subdir=args.checkpoint_case_subdir,
    )
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
