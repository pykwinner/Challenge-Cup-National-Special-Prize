from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .chronology import annual_replay_preflight, estimate_annual_model_structure
from .io import load_config


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def run_batch_preflight(
    manifest_path: str | Path,
    output_root: str | Path,
    *,
    pilot_hours: int = 168,
    estimate_model_size: bool = True,
) -> pd.DataFrame:
    """Run strict annual preflight for every explicit manifest profile."""

    manifest_path = Path(manifest_path).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    profiles = manifest.get("profiles", [])
    if not profiles:
        raise ValueError("批量年度预检清单至少需要一个profile")
    if pilot_hours < 2:
        raise ValueError("pilot_hours至少为2")

    rows: list[dict[str, Any]] = []
    for profile in profiles:
        name = str(profile["name"])
        profile_output = output_root / name
        profile_output.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"profile": name}
        try:
            config_path = _resolve(manifest_path.parent, profile["config"])
            representative_path = _resolve(
                manifest_path.parent, profile["representative_timeseries"]
            )
            annual_path = _resolve(
                manifest_path.parent, profile["annual_timeseries"]
            )
            timestamp_column = str(profile.get("timestamp_column", "timestamp"))
            config = load_config(config_path)
            representative = pd.read_csv(representative_path)
            annual = pd.read_csv(annual_path)
            preflight = annual_replay_preflight(
                config,
                representative,
                annual,
                timestamp_column=timestamp_column,
                require_full_year=True,
            )
            audit = preflight.representative_coverage_audit
            review = audit[audit["coverage_status"] == "review"]
            audit.to_csv(
                profile_output / "representative_coverage_audit.csv",
                index=False,
                encoding="utf-8-sig",
            )
            preflight.representative_recommendations.to_csv(
                profile_output / "representative_coverage_recommendations.csv",
                index=False,
                encoding="utf-8-sig",
            )
            (profile_output / "chronology_quality_audit.json").write_text(
                json.dumps(
                    preflight.chronology_audit,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (profile_output / "scenario_scope_audit.json").write_text(
                json.dumps(
                    preflight.scenario_scope_audit,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            record.update(
                {
                    "status": (
                        "ready_with_representative_warnings"
                        if len(review)
                        else "ready"
                    ),
                    "chronology_valid": bool(
                        preflight.chronology_audit["chronology_valid"]
                    ),
                    "is_full_year_horizon": bool(
                        preflight.chronology_audit["is_full_year_horizon"]
                    ),
                    "rows": int(preflight.chronology_audit["rows"]),
                    "representative_rows": int(len(representative)),
                    "representative_days": int(
                        representative["representative_day"].nunique()
                    )
                    if "representative_day" in representative.columns
                    else None,
                    "representative_review_count": int(len(review)),
                    "representative_review_columns": ";".join(
                        review["column"].astype(str).tolist()
                    ),
                    "scenario_scope_mapping_applied": bool(
                        preflight.scenario_scope_audit["mapping_applied"]
                    ),
                    "config": str(config_path),
                    "representative_timeseries": str(representative_path),
                    "annual_timeseries": str(annual_path),
                }
            )
            if estimate_model_size:
                estimate = estimate_annual_model_structure(
                    config,
                    preflight.chronological_timeseries,
                    pilot_hours=pilot_hours,
                )
                projected = estimate["projected_full_horizon"]
                (profile_output / "annual_model_size_estimate.json").write_text(
                    json.dumps(estimate, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                record.update(
                    {
                        "projected_variables": projected["variables"],
                        "projected_integer_variables": projected[
                            "integer_variables"
                        ],
                        "projected_constraints": projected["constraints"],
                        "projected_matrix_nonzeros": projected[
                            "matrix_nonzeros"
                        ],
                        "projected_build_memory_proxy_mb": projected[
                            "conservative_python_build_memory_proxy_mb"
                        ],
                        "milp_solve_risk_tier": estimate[
                            "milp_solve_risk_tier"
                        ],
                    }
                )
            (profile_output / "preflight_summary.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # preserve all profile evidence before failing
            record.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
            (profile_output / "preflight_error.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        rows.append(record)

    result = pd.DataFrame(rows)
    result.to_csv(
        output_root / "annual_preflight_batch_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (output_root / "annual_preflight_batch_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strict batch annual-data and representative-period preflight"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pilot-hours", type=int, default=168)
    parser.add_argument(
        "--no-model-size",
        action="store_true",
        help="skip two-prefix annual model-size projection",
    )
    args = parser.parse_args()
    result = run_batch_preflight(
        args.manifest,
        args.output,
        pilot_hours=args.pilot_hours,
        estimate_model_size=not args.no_model_size,
    )
    print(result.to_string(index=False))
    if bool((result["status"] == "error").any()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
