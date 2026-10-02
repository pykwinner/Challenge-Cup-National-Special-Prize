from __future__ import annotations

import argparse
from copy import deepcopy
import gc
from hashlib import sha256
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable
from uuid import uuid4

import pandas as pd
import yaml

from .chronology import (
    load_fixed_investment_plan_checkpoint,
    prepare_fixed_investment_plan,
    save_fixed_investment_plan_checkpoint,
)
from .io import load_config
from .multiyear import audit_prepared_fixed_plan_across_years
from .provenance import algorithm_provenance, atomic_write_json, canonical_sha256, file_sha256


_RISK_SCAN_ALGORITHM_REVISION = (
    "experiment_and_case_content_identity_v5"
)
_EXPERIMENT_FILE = "risk_scan_experiment.json"
_EXPERIMENT_SCHEMA = "heatplan.risk_scan_experiment.v1"
_CASE_SCHEMA = "heatplan.risk_case_completion.v2"
_CASE_REQUIRED_FILES = {
    "fixed_plan_checkpoint_manifest.json", "fixed_investment_config.yaml",
    "investment_freeze_audit.csv", "planning_model_profile.json",
    "representative_planning/summary.json", "representative_planning/investments.csv",
    "representative_planning/dispatch.csv", "risk_planning_config.yaml",
    "risk_case_summary.json", "multiyear_readiness_audit.csv",
}


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _weight_slug(weight: float) -> str:
    # repr is round-trip safe; rounding to eight decimals aliases 1e-9 with 0.
    text = "0" if float(weight) == 0.0 else repr(float(weight))
    if "e" not in text:
        text = text.rstrip("0").rstrip(".")
    return "weight_" + (text or "0").replace("-", "m").replace(".", "p")


def empirical_weighted_cvar(
    scenario_costs: Iterable[tuple[float, float]], alpha: float
) -> dict[str, float]:
    """Evaluate the same discrete CVaR epigraph used by the planning MILP."""

    if not 0.0 < alpha < 1.0:
        raise ValueError("cvar_alpha 必须位于 (0,1)")
    pairs = [(float(cost), float(probability)) for cost, probability in scenario_costs]
    if not pairs:
        raise ValueError("CVaR统计至少需要一个场景成本")
    probability_sum = sum(probability for _, probability in pairs)
    if abs(probability_sum - 1.0) > 1e-8:
        raise ValueError("CVaR场景概率之和必须为1")
    candidates = sorted({0.0, *(cost for cost, _ in pairs)})
    evaluations = [
        (
            eta
            + sum(
                probability * max(cost - eta, 0.0)
                for cost, probability in pairs
            )
            / (1.0 - alpha),
            eta,
        )
        for eta in candidates
    ]
    cvar, var = min(evaluations, key=lambda item: (item[0], item[1]))
    expected = sum(cost * probability for cost, probability in pairs)
    return {
        "expected_operating_risk_cost_yuan": expected,
        "value_at_risk_yuan": var,
        "cvar_operating_risk_cost_yuan": cvar,
        "worst_operating_risk_cost_yuan": max(cost for cost, _ in pairs),
        "best_operating_risk_cost_yuan": min(cost for cost, _ in pairs),
        "tail_cost_above_expected_yuan": cvar - expected,
    }


def _case_fingerprint(
    *,
    config: dict[str, Any],
    representative_path: Path,
    annual_paths: dict[str, Path],
    alpha: float,
    weight: float,
    time_limit: float,
    mip_gap: float | None,
    timestamp_column: str = "time",
) -> str:
    return canonical_sha256({
            "config": config,
            "representative_sha256": file_sha256(representative_path),
            "annual_sha256": {year: file_sha256(path) for year, path in sorted(annual_paths.items())},
            "algorithm": algorithm_provenance(),
            "settings": {
                "cvar_alpha": alpha,
                "cvar_weight": weight,
                "time_limit": time_limit,
                "mip_gap": mip_gap,
                "algorithm_revision": _RISK_SCAN_ALGORITHM_REVISION,
                "timestamp_column": timestamp_column,
            },
    })


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _investment_signature(investments: pd.DataFrame) -> str:
    columns = ["category", "asset", "option", "unit", "value"]
    normalized = investments[columns].copy()
    normalized["value"] = normalized["value"].astype(float).round(8)
    normalized = normalized.sort_values(columns[:-1]).reset_index(drop=True)
    return sha256(
        normalized.to_csv(index=False, lineterminator="\n").encode("utf-8")
    ).hexdigest()


def _risk_case_summary(
    *,
    profile: str,
    weight: float,
    alpha: float,
    source: str,
    fixed_plan: Any,
    readiness: pd.DataFrame,
    execution_seconds: float,
    case_output: Path,
) -> dict[str, Any]:
    summary = fixed_plan.planning.summary
    components = summary.get("components_yuan_per_year", {})
    premium = float(components.get("cvar_risk_premium", 0.0))
    scenario_results = summary.get("scenario_results", {})
    risk_statistics = empirical_weighted_cvar(
        [
            (
                float(item["operating_risk_cost_yuan"]),
                float(item["probability"]),
            )
            for item in scenario_results.values()
        ],
        alpha,
    )
    implied_cvar = premium / weight if weight > 0.0 else None
    solver_gap = summary.get("mip_gap")
    if solver_gap is not None and not math.isfinite(float(solver_gap)):
        solver_gap = None  # LP solves may not provide a MIP gap.
    return {
        "profile": profile,
        "cvar_alpha": alpha,
        "cvar_weight": weight,
        "case_slug": _weight_slug(weight),
        "case_output": str(case_output),
        "result_source": source,
        "solver_status": int(summary["solver_status"]),
        "solver_mip_gap": solver_gap,
        "objective_yuan_per_year": float(summary["objective_yuan_per_year"]),
        "expected_cost_before_cvar_yuan_per_year": float(
            summary["objective_yuan_per_year"] - premium
        ),
        "cvar_risk_premium_yuan_per_year": premium,
        "implied_cvar_operating_risk_cost_yuan": implied_cvar,
        "cvar_consistency_absolute_error_yuan": (
            abs(
                float(implied_cvar)
                - risk_statistics["cvar_operating_risk_cost_yuan"]
            )
            if implied_cvar is not None
            else None
        ),
        **risk_statistics,
        "emissions_kg_per_year": float(summary["emissions_kg_per_year"]),
        "regulated_levelized_heat_cost_yuan_per_kwh": float(
            summary["regulated_levelized_heat_cost_yuan_per_kwh"]
        ),
        "worst_scenario_unserved_rate": float(
            summary["worst_scenario_unserved_rate"]
        ),
        "worst_scenario_cooling_unserved_rate": float(
            summary.get("worst_scenario_cooling_unserved_rate", 0.0)
        ),
        "readiness_pass_years": int(readiness["readiness_pass"].astype(bool).sum()),
        "readiness_years": int(len(readiness)),
        "minimum_heat_capacity_gap_kw": float(
            readiness["firm_heat_capacity_gap_kw"].min()
        ),
        "minimum_electric_capacity_gap_kw": float(
            readiness["firm_electric_capacity_gap_kw"].min()
        ),
        "planning_variables": int(fixed_plan.planning_model_profile["variables"]),
        "planning_integer_variables": int(
            fixed_plan.planning_model_profile["integer_variables"]
        ),
        "planning_constraints": int(
            fixed_plan.planning_model_profile["constraints"]
        ),
        "planning_build_seconds": float(
            fixed_plan.planning_model_profile["build_seconds"]
        ),
        "planning_solve_seconds": float(
            fixed_plan.planning_model_profile["solve_seconds"]
        ),
        "case_execution_seconds": execution_seconds,
        "investment_signature_sha256": _investment_signature(
            fixed_plan.planning.investments
        ),
    }


def _compare_investments(
    neutral: pd.DataFrame, current: pd.DataFrame, *,
    absolute_tolerance: float, relative_tolerance: float,
    fraction_tolerance: float = 1e-4, binary_tolerance: float = 1e-6,
    capacity_tolerances: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Compare selection identities independently of engineering capacities.

    The legacy absolute tolerance remains the fallback for continuous units;
    fractions and discrete selections never inherit that capacity tolerance.
    """
    capacities = {str(unit).lower(): float(value)
                  for unit, value in (capacity_tolerances or {}).items()}
    for value in (absolute_tolerance, relative_tolerance, fraction_tolerance,
                  binary_tolerance, *capacities.values()):
        if not math.isfinite(value) or value < 0:
            raise ValueError("投资比较容差必须是有限非负数")
    if binary_tolerance >= 0.5:
        raise ValueError("二元变量容差必须小于 0.5")
    keys = ["category", "asset", "option", "unit"]

    def normalized(frame: pd.DataFrame, value_name: str) -> pd.DataFrame:
        result = frame[keys + ["value"]].copy()
        for key in keys:
            result[key] = result[key].fillna("").astype(str)
        result["value"] = pd.to_numeric(result["value"], errors="raise")
        if not result["value"].map(math.isfinite).all():
            raise ValueError("投资比较包含非有限决策值")
        if result.duplicated(keys).any():
            raise ValueError("投资比较包含重复的资产/选项/单位")
        return result.rename(columns={"value": value_name})

    comparison = normalized(neutral, "neutral_value").merge(
        normalized(current, "risk_weight_value"), on=keys, how="outer",
        validate="one_to_one",
    )
    value_columns = ["neutral_value", "risk_weight_value"]
    comparison[value_columns] = comparison[value_columns].fillna(0.0)
    comparison["delta_vs_neutral"] = comparison["risk_weight_value"] - comparison["neutral_value"]
    kinds, thresholds, structural = [], [], []
    for row in comparison.itertuples(index=False):
        unit = row.unit.lower()
        option = unit == "option" or row.category.endswith("_choice")
        if unit == "binary" or option:
            kind = "option" if option else "binary"
            before, after = float(row.neutral_value), float(row.risk_weight_value)
            if any(min(abs(value), abs(value - 1.0)) > binary_tolerance
                   for value in (before, after)):
                raise ValueError(f"离散投资值不是有效 0/1: {row.category}/{row.asset}/{row.option}")
            threshold = binary_tolerance
            changed_structure = round(before) != round(after)
        elif unit == "fraction":
            kind = "fraction"
            threshold = fraction_tolerance + relative_tolerance * max(
                abs(row.neutral_value), abs(row.risk_weight_value),
            )
            changed_structure = False
        else:
            kind = "capacity" if unit in {"kw", "kwh", "kg"} else "continuous"
            threshold = capacities.get(unit, absolute_tolerance) + relative_tolerance * max(
                abs(row.neutral_value), 1.0,
            )
            changed_structure = False
        kinds.append(kind)
        thresholds.append(threshold)
        structural.append(changed_structure)
    comparison["decision_kind"] = kinds
    comparison["material_change_threshold"] = thresholds
    comparison["is_decision_variable"] = comparison["category"] != "storage_firm_capacity"
    comparison["structural_change"] = pd.Series(structural, index=comparison.index, dtype=bool)
    comparison["structural_change"] &= comparison["is_decision_variable"]
    comparison["numerical_change"] = comparison["is_decision_variable"] & (
        comparison["delta_vs_neutral"].abs() > binary_tolerance
    )
    discrete = comparison["decision_kind"].isin(["binary", "option"])
    comparison["material_change"] = comparison["structural_change"] | (
        comparison["is_decision_variable"] & ~discrete
        & (comparison["delta_vs_neutral"].abs() > comparison["material_change_threshold"])
    )
    return comparison


def _write_aggregate_outputs(
    output_root: Path,
    rows: list[dict[str, Any]],
    *,
    absolute_tolerance: float,
    relative_tolerance: float,
    fraction_tolerance: float = 1e-4,
    binary_tolerance: float = 1e-6,
    capacity_tolerances: dict[str, float] | None = None,
    scan_complete: bool | None = None,
) -> pd.DataFrame:
    atomic_write_json(output_root / "investment_comparison_settings.json", {
        "absolute_capacity_tolerance_fallback": absolute_tolerance,
        "relative_tolerance": relative_tolerance,
        "fraction_absolute_tolerance": fraction_tolerance,
        "binary_integrality_tolerance": binary_tolerance,
        "capacity_absolute_tolerances_by_unit": capacity_tolerances or {},
        "discrete_rule": "compare rounded 0/1 selections after integrality validation",
        "first_change_scope": "first material change among scanned weights; not an exact breakpoint",
    })
    if not rows:
        summary = pd.DataFrame(columns=["profile", "cvar_weight"])
        summary.to_csv(output_root / "risk_weight_scan_summary.csv", index=False)
        atomic_write_json(output_root / "risk_weight_scan_summary.json", [])
        atomic_write_json(output_root / "risk_weight_scan_acceptance_rollup.json", {
            "completed_cases": 0, "scan_complete": False, "acceptance_pass": False,
            "status": "no_completed_cases_for_current_experiment",
        })
        return summary
    summary = pd.DataFrame(rows).sort_values(["profile", "cvar_weight"])
    neutral = summary.loc[summary["cvar_weight"].astype(float) == 0.0].set_index("profile")
    summary["neutral_baseline_available"] = summary["profile"].isin(neutral.index)
    summary["neutral_baseline_status"] = summary["neutral_baseline_available"].map(
        {True: "available", False: "no_neutral_weight_in_current_experiment"}
    )
    for column in (
        "expected_cost_before_cvar_yuan_per_year",
        "cvar_operating_risk_cost_yuan",
        "worst_operating_risk_cost_yuan",
    ):
        summary[f"{column}_delta_vs_neutral"] = summary[column] - summary["profile"].map(neutral[column])
    summary.to_csv(
        output_root / "risk_weight_scan_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    # pandas emits JSON null for unavailable neutral comparisons, never NaN.
    atomic_write_json(output_root / "risk_weight_scan_summary.json",
                      json.loads(summary.to_json(orient="records", force_ascii=False, double_precision=15)))

    investment_blocks: list[pd.DataFrame] = []
    stability_rows: list[dict[str, Any]] = []
    for profile, profile_rows in summary.groupby("profile", sort=True):
        profile_rows = profile_rows.sort_values("cvar_weight")
        if not (profile_rows["cvar_weight"].astype(float) == 0.0).any():
            continue
        neutral_row = profile_rows.loc[
            profile_rows["cvar_weight"].astype(float) == 0.0
        ].iloc[0]
        neutral = pd.read_csv(
            Path(neutral_row["case_output"])
            / "representative_planning"
            / "investments.csv"
        )
        for row in profile_rows.itertuples(index=False):
            current = pd.read_csv(
                Path(row.case_output)
                / "representative_planning"
                / "investments.csv"
            )
            comparison = _compare_investments(
                neutral, current, absolute_tolerance=absolute_tolerance,
                relative_tolerance=relative_tolerance,
                fraction_tolerance=fraction_tolerance, binary_tolerance=binary_tolerance,
                capacity_tolerances=capacity_tolerances,
            )
            comparison.insert(0, "profile", profile)
            comparison.insert(1, "cvar_weight", float(row.cvar_weight))
            changed = comparison[comparison["material_change"]]
            stability_rows.append(
                {
                    "profile": profile,
                    "cvar_weight": float(row.cvar_weight),
                    "same_investment_as_neutral": bool(changed.empty),
                    "same_structure_as_neutral": not bool(comparison["structural_change"].any()),
                    "structurally_changed_asset_count": int(
                        len(comparison.loc[comparison["structural_change"], ["category", "asset"]].drop_duplicates())
                    ),
                    "numerically_changed_asset_count": int(comparison["numerical_change"].sum()),
                    "changed_fraction_count": int((changed["decision_kind"] == "fraction").sum()),
                    "changed_option_asset_count": int(len(changed.loc[
                        changed["decision_kind"] == "option", ["category", "asset"],
                    ].drop_duplicates())),
                    "materially_changed_asset_count": int(len(changed)),
                    "absolute_delta_kw": float(
                        changed.loc[changed["unit"] == "kW", "delta_vs_neutral"]
                        .abs()
                        .sum()
                    ),
                    "absolute_delta_kwh": float(
                        changed.loc[changed["unit"] == "kWh", "delta_vs_neutral"]
                        .abs()
                        .sum()
                    ),
                    "changed_binary_count": int(
                        changed.loc[changed["unit"] == "binary", "material_change"].sum()
                    ),
                    "maximum_absolute_asset_delta": float(
                        changed["delta_vs_neutral"].abs().max()
                    )
                    if not changed.empty
                    else 0.0,
                }
            )
            investment_blocks.append(comparison)
    stability = pd.DataFrame(stability_rows)
    if not stability.empty:
        first_changes = (
            stability.loc[
                ~stability["same_investment_as_neutral"]
                & (stability["cvar_weight"] > 0.0)
            ]
            .groupby("profile")["cvar_weight"]
            .min()
        )
        stability["first_material_change_weight"] = stability["profile"].map(
            first_changes
        )
        stability.to_csv(
            output_root / "investment_stability_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )
    if investment_blocks:
        pd.concat(investment_blocks, ignore_index=True).to_csv(
            output_root / "investment_delta_vs_neutral.csv",
            index=False,
            encoding="utf-8-sig",
        )
    write_risk_scan_acceptance_rollup(output_root, summary, stability, scan_complete=scan_complete)
    return summary


def write_risk_scan_acceptance_rollup(
    output_root: str | Path,
    summary: pd.DataFrame,
    stability: pd.DataFrame,
    *,
    scan_complete: bool | None = None,
) -> dict[str, Any]:
    """Write a hash-linked acceptance record for every completed scan case."""

    root = Path(output_root).resolve()
    experiment = _read_json(root / _EXPERIMENT_FILE)
    if scan_complete is None:
        scan_complete = not isinstance(experiment, dict) or experiment.get("status") == "complete"
    cases: list[dict[str, Any]] = []
    for row in summary.itertuples(index=False):
        case_root = Path(row.case_output).resolve()
        case_summary = case_root / "risk_case_summary.json"
        checkpoint_manifest = case_root / "fixed_plan_checkpoint_manifest.json"
        if not case_summary.is_file() or not checkpoint_manifest.is_file():
            raise ValueError(f"风险扫描案例文件不完整: {case_root}")
        cases.append(
            {
                "profile": str(row.profile),
                "cvar_weight": float(row.cvar_weight),
                "case_summary": str(case_summary.relative_to(root)).replace(
                    "\\", "/"
                ),
                "case_summary_sha256": sha256(
                    case_summary.read_bytes()
                ).hexdigest(),
                "checkpoint_manifest": str(
                    checkpoint_manifest.relative_to(root)
                ).replace("\\", "/"),
                "checkpoint_manifest_sha256": sha256(
                    checkpoint_manifest.read_bytes()
                ).hexdigest(),
            }
        )
    positive_errors = summary.loc[
        summary["cvar_weight"].astype(float) > 0.0,
        "cvar_consistency_absolute_error_yuan",
    ].astype(float)
    breakpoints: dict[str, float | None] = {}
    stable_profiles: list[str] = []
    if not stability.empty:
        for profile, block in stability.groupby("profile"):
            values = block["first_material_change_weight"].dropna()
            breakpoints[str(profile)] = (
                float(values.iloc[0]) if len(values) else None
            )
            if bool(block["same_investment_as_neutral"].all()):
                stable_profiles.append(str(profile))
    rollup = {
        "schema": "heatplan.cvar_weight_scan_acceptance.v1",
        "algorithm_revision": _RISK_SCAN_ALGORITHM_REVISION,
        "experiment_fingerprint": experiment.get("fingerprint") if isinstance(experiment, dict) else None,
        "scan_complete": scan_complete,
        "profiles_without_neutral_baseline": sorted(
            set(summary["profile"].astype(str)) - set(summary.loc[
                summary["cvar_weight"].astype(float) == 0.0, "profile"
            ].astype(str))
        ),
        "completed_cases": int(len(summary)),
        "profiles": sorted(summary["profile"].astype(str).unique().tolist()),
        "weights": sorted(summary["cvar_weight"].astype(float).unique().tolist()),
        "all_solver_status_zero": bool(
            (summary["solver_status"].astype(int) == 0).all()
        ),
        "readiness_pass_years": int(
            summary["readiness_pass_years"].astype(int).sum()
        ),
        "readiness_years": int(summary["readiness_years"].astype(int).sum()),
        "all_heat_unserved_zero": bool(
            (summary["worst_scenario_unserved_rate"].astype(float) <= 1e-12).all()
        ),
        "all_cooling_unserved_zero": bool(
            (
                summary["worst_scenario_cooling_unserved_rate"].astype(float)
                <= 1e-12
            ).all()
        ),
        "maximum_cvar_ledger_consistency_error_yuan": float(
            positive_errors.abs().max()
        )
        if len(positive_errors)
        else None,
        "first_material_investment_change_weight": breakpoints,
        "investment_stable_for_all_scanned_weights": sorted(stable_profiles),
        "acceptance_pass": bool(
            scan_complete and (summary["solver_status"].astype(int) == 0).all()
            and (
                summary["readiness_pass_years"].astype(int)
                == summary["readiness_years"].astype(int)
            ).all()
            and (positive_errors.abs() <= 1e-5).all()
        ),
        "cases": cases,
    }
    (root / "risk_weight_scan_acceptance_rollup.json").write_text(
        json.dumps(rollup, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return rollup


def _risk_config(config: dict[str, Any], alpha: float, weight: float) -> dict[str, Any]:
    configured = deepcopy(config)
    configured.setdefault("planning", {}).setdefault("risk", {}).update({
        "cvar_alpha": alpha, "cvar_weight": weight,
    })
    return configured


def _scan_profile_specs(manifest_path: Path) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    specs = {}
    for profile in payload.get("profiles", []):
        name = str(profile["name"])
        if name in {"", ".", "..", ".risk_history"} or Path(name).name != name or name in specs:
            raise ValueError("风险扫描 profile 必须是唯一的单个目录名")
        annual_items = profile.get("annual_timeseries", [])
        annual_paths = {str(item["year"]): _resolve(manifest_path.parent, str(item["path"]))
                        for item in annual_items}
        timestamps = {str(item.get("timestamp_column", "time")) for item in annual_items}
        if not annual_paths or len(annual_paths) != len(annual_items) or len(timestamps) != 1:
            raise ValueError(f"{name} 需要非空且年份唯一、时间戳字段一致的年度输入")
        specs[name] = {
            "config_path": _resolve(manifest_path.parent, str(profile["config"])),
            "representative_path": _resolve(manifest_path.parent, str(profile["representative_timeseries"])),
            "annual_paths": annual_paths, "timestamp_column": next(iter(timestamps)),
        }
    if not specs:
        raise ValueError("风险扫描清单至少需要一个 profile")
    return specs


def _scan_experiment_identity(
    manifest_path: Path, specs: dict[str, dict[str, Any]], *,
    alpha: float, time_limit: float, mip_gap: float | None, comparison: dict[str, Any],
) -> dict[str, Any]:
    """Weights are deliberately excluded so one experiment can be extended."""
    return {
        "schema": _EXPERIMENT_SCHEMA,
        "manifest_sha256": file_sha256(manifest_path),
        "algorithm": algorithm_provenance(),
        "comparison": comparison,
        "profile_identities": {
            name: _case_fingerprint(
                config=_risk_config(load_config(spec["config_path"]), alpha, 0.0),
                representative_path=spec["representative_path"], annual_paths=spec["annual_paths"],
                alpha=alpha, weight=0.0, time_limit=time_limit, mip_gap=mip_gap,
                timestamp_column=spec["timestamp_column"],
            ) for name, spec in sorted(specs.items())
        },
    }


def _archive_risk_outputs(root: Path, paths: Iterable[Path], label: str) -> None:
    root = root.resolve()
    existing = [path for path in paths if path.exists()]
    if not existing:
        return
    destination = root / ".risk_history" / label / uuid4().hex
    if not destination.resolve().is_relative_to(root):
        raise ValueError("风险扫描历史目录越出输出目录")
    for path in existing:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("风险扫描旧产物越出输出目录")
        target = destination / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)


def _risk_case_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): file_sha256(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.relative_to(root).as_posix() != "risk_case_run.json"}


def _trusted_risk_case(
    root: Path, fingerprint: str, algorithm_sha256: str,
    experiment_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    marker = _read_json(root / "risk_case_run.json")
    if not isinstance(marker, dict) or (
        marker.get("schema") != _CASE_SCHEMA or marker.get("completed") is not True
        or marker.get("fingerprint") != fingerprint
        or marker.get("algorithm_sha256") != algorithm_sha256
        or (experiment_fingerprint is not None
            and marker.get("experiment_fingerprint") != experiment_fingerprint)
    ):
        return None
    try:
        hashes = _risk_case_hashes(root)
    except OSError:
        return None
    if not _CASE_REQUIRED_FILES.issubset(hashes) or hashes != marker.get("outputs_sha256"):
        return None
    summary = _read_json(root / "risk_case_summary.json")
    if not isinstance(summary, dict) or summary.get("solver_status") != 0:
        return None
    return summary


def run_risk_weight_scan(
    manifest_path: str | Path,
    output_root: str | Path,
    *,
    weights: Iterable[float],
    cvar_alpha: float = 0.8,
    selected_profiles: Iterable[str] | None = None,
    time_limit: float = 900.0,
    mip_gap: float | None = 0.005,
    seed_checkpoint_root: str | Path | None = None,
    resume: bool = True,
    investment_absolute_tolerance: float = 1.0,
    investment_relative_tolerance: float = 1e-3,
    investment_fraction_tolerance: float = 1e-4,
    investment_binary_tolerance: float = 1e-6,
    investment_kw_tolerance: float | None = None,
    investment_kwh_tolerance: float | None = None,
    investment_kg_tolerance: float | None = None,
) -> pd.DataFrame:
    """Solve and compare shared-investment plans across CVaR risk weights."""

    manifest_path = Path(manifest_path).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    prior_experiment = _read_json(output_root / _EXPERIMENT_FILE)
    _archive_risk_outputs(output_root, [
        output_root / _EXPERIMENT_FILE,
        output_root / "risk_weight_scan_acceptance_rollup.json",
    ], "attempt_start")
    # Invalidate success before parsing configuration/data: even an early input
    # error must not leave this new attempt looking like the previous success.
    atomic_write_json(output_root / _EXPERIMENT_FILE, {
        "schema": _EXPERIMENT_SCHEMA, "status": "in_progress",
    })
    atomic_write_json(output_root / "risk_weight_scan_acceptance_rollup.json", {
        "status": "in_progress", "scan_complete": False, "acceptance_pass": False,
    })
    capacity_tolerances = {
        unit: value for unit, value in (
            ("kW", investment_kw_tolerance), ("kWh", investment_kwh_tolerance),
            ("kg", investment_kg_tolerance),
        ) if value is not None
    }
    # Validate before solving, even when there is no risk-neutral comparison.
    _compare_investments(
        pd.DataFrame(columns=["category", "asset", "option", "unit", "value"]),
        pd.DataFrame(columns=["category", "asset", "option", "unit", "value"]),
        absolute_tolerance=investment_absolute_tolerance,
        relative_tolerance=investment_relative_tolerance,
        fraction_tolerance=investment_fraction_tolerance,
        binary_tolerance=investment_binary_tolerance,
        capacity_tolerances=capacity_tolerances,
    )
    seed_root = (
        Path(seed_checkpoint_root).resolve()
        if seed_checkpoint_root is not None
        else None
    )
    parsed_weights = sorted({float(weight) for weight in weights})
    if not parsed_weights or parsed_weights[0] < 0.0:
        raise ValueError("CVaR权重必须是非负数")
    if not 0.0 < cvar_alpha < 1.0:
        raise ValueError("cvar_alpha 必须位于 (0,1)")
    if any(not math.isfinite(weight) for weight in parsed_weights):
        raise ValueError("CVaR权重必须是有限非负数")
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("求解时间上限必须是有限正数")
    specs = _scan_profile_specs(manifest_path)
    chosen = set(selected_profiles or specs)
    unknown = chosen - set(specs)
    if unknown:
        raise ValueError(f"清单中不存在这些 profile: {sorted(unknown)}")
    comparison = {
        "absolute_tolerance": investment_absolute_tolerance,
        "relative_tolerance": investment_relative_tolerance,
        "fraction_tolerance": investment_fraction_tolerance,
        "binary_tolerance": investment_binary_tolerance,
        "capacity_tolerances": capacity_tolerances,
    }

    def current_identity():
        return _scan_experiment_identity(
            manifest_path, specs, alpha=cvar_alpha, time_limit=time_limit,
            mip_gap=mip_gap, comparison=comparison,
        )

    identity = current_identity()
    experiment_fingerprint = canonical_sha256(identity)
    algorithm_sha256 = canonical_sha256(identity["algorithm"])
    prior = prior_experiment
    if not isinstance(prior, dict) or (
        prior.get("schema") != _EXPERIMENT_SCHEMA
        or prior.get("fingerprint") != experiment_fingerprint
    ):
        _archive_risk_outputs(output_root,
            [path for path in output_root.iterdir() if path.name != ".risk_history"], "experiments")
    else:
        # Preserve earlier reports but rebuild them from verified case files;
        # the previous aggregate JSON is never accepted as evidence by itself.
        _archive_risk_outputs(output_root,
            [path for path in output_root.iterdir() if path.is_file() and path.name != _EXPERIMENT_FILE], "reports")
    experiment = {
        "schema": _EXPERIMENT_SCHEMA, "fingerprint": experiment_fingerprint,
        "identity": identity, "status": "in_progress",
        "requested_profiles": sorted(chosen), "requested_weights": parsed_weights,
    }
    atomic_write_json(output_root / _EXPERIMENT_FILE, experiment)
    aggregate = {}

    def case_arguments(spec, config, weight):
        return dict(config=config, representative_path=spec["representative_path"],
                    annual_paths=spec["annual_paths"], alpha=cvar_alpha, weight=weight,
                    time_limit=time_limit, mip_gap=mip_gap,
                    timestamp_column=spec["timestamp_column"])

    # Retain unrequested weights only from this exact experiment, with their
    # complete current-algorithm case and checkpoint contents still intact.
    for name, spec in specs.items():
        for case_output in list((output_root / name).glob("weight_*")):
            marker = _read_json(case_output / "risk_case_run.json")
            if not isinstance(marker, dict):
                _archive_risk_outputs(output_root, [case_output], "cases")
                continue
            try:
                weight = float(marker["cvar_weight"])
                if not math.isfinite(weight) or weight < 0 or case_output.name != _weight_slug(weight):
                    _archive_risk_outputs(output_root, [case_output], "cases")
                    continue
                config = _risk_config(load_config(spec["config_path"]), cvar_alpha, weight)
                fingerprint = _case_fingerprint(**case_arguments(spec, config, weight))
                summary = _trusted_risk_case(case_output, fingerprint, algorithm_sha256, experiment_fingerprint)
            except (KeyError, TypeError, ValueError, OSError):
                _archive_risk_outputs(output_root, [case_output], "cases")
                continue
            if summary is not None and summary.get("profile") == name and summary.get("cvar_weight") == weight:
                if not resume and name in chosen and weight in parsed_weights:
                    continue
                summary = dict(summary, case_output=str(case_output))
                aggregate[(name, weight)] = summary
            else:
                _archive_risk_outputs(output_root, [case_output], "cases")
    _write_aggregate_outputs(output_root, list(aggregate.values()), **comparison)

    for name, spec in specs.items():
        if name not in chosen:
            continue
        base_config = load_config(spec["config_path"])
        representative = pd.read_csv(spec["representative_path"])
        annual_frames = {year: pd.read_csv(path) for year, path in spec["annual_paths"].items()}
        for weight in parsed_weights:
            if resume and (name, weight) in aggregate:
                aggregate[(name, weight)] = dict(aggregate[(name, weight)], result_source="resumed")
                continue
            case_started = perf_counter()
            config = _risk_config(base_config, cvar_alpha, weight)
            arguments = case_arguments(spec, config, weight)
            fingerprint = _case_fingerprint(**arguments)
            case_output = output_root / name / _weight_slug(weight)
            # Unbound, corrupt and old-algorithm checkpoints are preserved, not
            # automatically relabelled with a new algorithm fingerprint.
            _archive_risk_outputs(output_root, [case_output], "cases")
            case_output.mkdir(parents=True, exist_ok=True)
            fixed_plan = None
            source = "solved"
            if resume and seed_root is not None:
                for candidate in (seed_root / name / _weight_slug(weight), seed_root / name):
                    if _trusted_risk_case(candidate, fingerprint, algorithm_sha256) is None:
                        continue
                    try:
                        fixed_plan = load_fixed_investment_plan_checkpoint(candidate,
                            expected_config=config, expected_representative_timeseries=representative)
                    except (ValueError, OSError, KeyError):
                        fixed_plan = None
                        continue
                    source = "seed_checkpoint"
                    break
            if fixed_plan is None:
                fixed_plan = prepare_fixed_investment_plan(config, representative,
                    planning_time_limit=time_limit, planning_mip_gap=mip_gap)
            if int(fixed_plan.planning.summary["solver_status"]) != 0:
                raise RuntimeError(f"{name}/{weight:g} 未取得状态0规划解")
            save_fixed_investment_plan_checkpoint(case_output, fixed_plan,
                source_config=config, representative_timeseries=representative)
            (case_output / "risk_planning_config.yaml").write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
            readiness_artifacts = audit_prepared_fixed_plan_across_years(
                config, representative, annual_frames, fixed_plan,
                timestamp_column=spec["timestamp_column"])
            readiness_artifacts.year_audit.to_csv(case_output / "multiyear_readiness_audit.csv",
                index=False, encoding="utf-8-sig")
            case_summary = _risk_case_summary(profile=name, weight=weight, alpha=cvar_alpha,
                source=source, fixed_plan=fixed_plan, readiness=readiness_artifacts.year_audit,
                execution_seconds=perf_counter() - case_started, case_output=case_output)
            case_summary.update({"experiment_fingerprint": experiment_fingerprint,
                                 "case_fingerprint": fingerprint})
            atomic_write_json(case_output / "risk_case_summary.json", case_summary)
            try:
                unchanged = canonical_sha256(current_identity()) == experiment_fingerprint
            except (OSError, ValueError, AttributeError, yaml.YAMLError):
                unchanged = False
            if not unchanged:
                experiment["status"] = "inputs_changed_during_run"
                atomic_write_json(output_root / _EXPERIMENT_FILE, experiment)
                raise RuntimeError("风险扫描输入、配置或算法在运行期间改变；未发布本案例完成标记")
            hashes = _risk_case_hashes(case_output)
            if not _CASE_REQUIRED_FILES.issubset(hashes):
                raise RuntimeError("风险扫描缺少必要产物，未发布本案例完成标记")
            atomic_write_json(case_output / "risk_case_run.json", {
                "schema": _CASE_SCHEMA, "fingerprint": fingerprint, "completed": True,
                "experiment_fingerprint": experiment_fingerprint,
                "algorithm_sha256": algorithm_sha256, "outputs_sha256": hashes,
                "result_source": source, "cvar_alpha": cvar_alpha, "cvar_weight": weight,
                "time_limit": time_limit, "mip_gap": mip_gap,
            })
            aggregate[(name, weight)] = case_summary
            _write_aggregate_outputs(output_root, list(aggregate.values()), **comparison)
            print(f"{name}/{weight:g}: source={source} solve={case_summary['planning_solve_seconds']:.3f}s", flush=True)
            del fixed_plan, readiness_artifacts
            gc.collect()
        del annual_frames, representative
        gc.collect()
    if canonical_sha256(current_identity()) != experiment_fingerprint:
        raise RuntimeError("风险扫描输入或算法已改变，不能发布完整扫描结果")
    result = _write_aggregate_outputs(output_root, list(aggregate.values()),
                                      **comparison, scan_complete=True)
    experiment["status"] = "complete"
    experiment["completed_cases"] = len(aggregate)
    experiment["report_files_sha256"] = {
        path.name: file_sha256(path) for path in sorted(output_root.iterdir())
        if path.is_file() and path.name != _EXPERIMENT_FILE
    }
    atomic_write_json(output_root / _EXPERIMENT_FILE, experiment)
    return result



def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan CVaR weights and locate material investment breakpoints"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--weights", nargs="+", type=float, default=[0.0, 0.02, 0.05, 0.10]
    )
    parser.add_argument("--cvar-alpha", type=float, default=0.8)
    parser.add_argument("--profiles", nargs="+")
    parser.add_argument("--time-limit", type=float, default=900.0)
    parser.add_argument("--mip-gap", type=float, default=0.005)
    parser.add_argument("--seed-checkpoint-root")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--investment-absolute-tolerance", type=float, default=1.0)
    parser.add_argument("--investment-relative-tolerance", type=float, default=1e-3)
    parser.add_argument("--investment-fraction-tolerance", type=float, default=1e-4)
    parser.add_argument("--investment-binary-tolerance", type=float, default=1e-6)
    parser.add_argument("--investment-kw-tolerance", type=float)
    parser.add_argument("--investment-kwh-tolerance", type=float)
    parser.add_argument("--investment-kg-tolerance", type=float)
    args = parser.parse_args()
    result = run_risk_weight_scan(
        args.manifest,
        args.output,
        weights=args.weights,
        cvar_alpha=args.cvar_alpha,
        selected_profiles=args.profiles,
        time_limit=args.time_limit,
        mip_gap=args.mip_gap,
        seed_checkpoint_root=args.seed_checkpoint_root,
        resume=not args.no_resume,
        investment_absolute_tolerance=args.investment_absolute_tolerance,
        investment_relative_tolerance=args.investment_relative_tolerance,
        investment_fraction_tolerance=args.investment_fraction_tolerance,
        investment_binary_tolerance=args.investment_binary_tolerance,
        investment_kw_tolerance=args.investment_kw_tolerance,
        investment_kwh_tolerance=args.investment_kwh_tolerance,
        investment_kg_tolerance=args.investment_kg_tolerance,
    )
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
