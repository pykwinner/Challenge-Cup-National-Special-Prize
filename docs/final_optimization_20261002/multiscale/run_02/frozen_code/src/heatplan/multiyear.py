from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .chronology import (
    FixedInvestmentPlanArtifacts,
    adapt_config_to_chronology_scenarios,
    annual_replay_preflight,
    fixed_investment_readiness_audit,
    prepare_fixed_investment_plan,
)
from .io import validate_inputs


@dataclass
class MultiYearReadinessArtifacts:
    fixed_plan: FixedInvestmentPlanArtifacts
    year_audit: pd.DataFrame
    coverage_audits: dict[str, pd.DataFrame]
    readiness_audits: dict[str, dict[str, Any]]
    scenario_scope_audits: dict[str, dict[str, Any]]


def _normalise_risk(values: pd.Series, *, higher_is_worse: bool) -> pd.Series:
    numeric = values.astype(float)
    span = float(numeric.max() - numeric.min())
    if span <= 1e-12:
        return pd.Series(np.zeros(len(numeric)), index=numeric.index)
    if higher_is_worse:
        return (numeric - float(numeric.min())) / span
    return (float(numeric.max()) - numeric) / span


def _optional_float(value: Any) -> float:
    """Preserve a non-applicable audit result as NaN in tabular exports."""

    return np.nan if value is None else float(value)


def audit_fixed_plan_across_years(
    config: dict[str, Any],
    representative_timeseries: pd.DataFrame,
    annual_timeseries: Mapping[str, pd.DataFrame],
    *,
    timestamp_column: str = "timestamp",
    planning_time_limit: float = 180.0,
    planning_mip_gap: float | None = None,
) -> MultiYearReadinessArtifacts:
    """Freeze one plan and screen it against multiple complete weather years."""

    if not annual_timeseries:
        raise ValueError("跨年度审计至少需要一个连续年度")
    fixed_plan = prepare_fixed_investment_plan(
        config,
        representative_timeseries,
        planning_time_limit=planning_time_limit,
        planning_mip_gap=planning_mip_gap,
    )
    return audit_prepared_fixed_plan_across_years(
        config,
        representative_timeseries,
        annual_timeseries,
        fixed_plan,
        timestamp_column=timestamp_column,
    )


def audit_prepared_fixed_plan_across_years(
    config: dict[str, Any],
    representative_timeseries: pd.DataFrame,
    annual_timeseries: Mapping[str, pd.DataFrame],
    fixed_plan: FixedInvestmentPlanArtifacts,
    *,
    timestamp_column: str = "timestamp",
) -> MultiYearReadinessArtifacts:
    """Screen an already solved fixed plan without repeating its MILP solve."""

    if not annual_timeseries:
        raise ValueError("跨年度审计至少需要一个连续年度")
    if fixed_plan.investment_freeze_audit.empty or not bool(
        fixed_plan.investment_freeze_audit["freeze_ok"].all()
    ):
        raise ValueError("跨年度审计要求已通过投资冻结检查的固定方案")
    heat_columns = [
        str(column)
        for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    cooling_columns = [
        str(column)
        for column in config.get("multi_energy_services", {})
        .get("cooling", {})
        .get("demand_columns", {})
        .values()
    ]
    rows: list[dict[str, Any]] = []
    coverage_audits: dict[str, pd.DataFrame] = {}
    readiness_audits: dict[str, dict[str, Any]] = {}
    scope_audits: dict[str, dict[str, Any]] = {}
    for label, annual in annual_timeseries.items():
        preflight = annual_replay_preflight(
            config,
            representative_timeseries,
            annual,
            timestamp_column=timestamp_column,
            require_full_year=True,
        )
        chronology = preflight.chronological_timeseries
        fixed_config, scope_audit = adapt_config_to_chronology_scenarios(
            deepcopy(fixed_plan.fixed_config), chronology
        )
        validate_inputs(fixed_config, chronology)
        readiness = fixed_investment_readiness_audit(
            fixed_config, chronology
        )
        total_heat = chronology[heat_columns].sum(axis=1)
        total_cooling = (
            chronology[cooling_columns].sum(axis=1)
            if cooling_columns
            else pd.Series(np.zeros(len(chronology)), index=chronology.index)
        )
        review = preflight.representative_coverage_audit[
            preflight.representative_coverage_audit["coverage_status"]
            == "review"
        ]
        rows.append(
            {
                "year_label": str(label),
                "rows": int(len(chronology)),
                "minimum_outdoor_temp_c": float(
                    chronology["outdoor_temp"].min()
                )
                if "outdoor_temp" in chronology
                else np.nan,
                "peak_heat_demand_kw": float(total_heat.max()),
                "maximum_heat_ramp_kw_per_step": float(
                    total_heat.diff().abs().max()
                ),
                "peak_cooling_demand_kw": float(total_cooling.max()),
                "representative_review_count": int(len(review)),
                "readiness_pass": bool(readiness["readiness_pass"]),
                "heat_firm_capacity_audit_applicable": bool(
                    readiness["heat_firm_capacity_audit_applicable"]
                ),
                "firm_heat_capacity_gap_kw": _optional_float(
                    readiness["firm_heat_capacity_gap_kw"]
                ),
                "grid_firm_capacity_audit_applicable": bool(
                    readiness["grid"]["applicable"]
                ),
                "firm_electric_capacity_gap_kw": _optional_float(
                    readiness["grid"]["firm_electric_capacity_gap_kw"]
                ),
                "minimum_firm_electric_requirement_kw": float(
                    readiness["grid"]["minimum_firm_electric_requirement_kw"]
                ),
                "scenario_scope_mapping_applied": bool(
                    scope_audit["mapping_applied"]
                ),
            }
        )
        coverage_audits[str(label)] = preflight.representative_coverage_audit
        readiness_audits[str(label)] = readiness
        scope_audits[str(label)] = scope_audit
    audit = pd.DataFrame(rows)
    risk_metrics = {
        "coldest_temperature": ("minimum_outdoor_temp_c", False),
        "peak_heat_demand": ("peak_heat_demand_kw", True),
        "maximum_heat_ramp": ("maximum_heat_ramp_kw_per_step", True),
        "peak_cooling_demand": ("peak_cooling_demand_kw", True),
        "firm_electric_requirement": (
            "minimum_firm_electric_requirement_kw",
            True,
        ),
    }
    reason_by_index: dict[int, list[str]] = {index: [] for index in audit.index}
    normalised = []
    for reason, (column, higher_is_worse) in risk_metrics.items():
        if audit[column].isna().all():
            continue
        finite = audit[column].dropna().astype(float)
        if (
            len(finite) == 0
            or float(finite.abs().max()) <= 1e-12
            or float(finite.max() - finite.min()) <= 1e-12
        ):
            continue
        scores = _normalise_risk(
            audit[column], higher_is_worse=higher_is_worse
        )
        normalised.append(scores)
        worst_value = (
            float(audit[column].max())
            if higher_is_worse
            else float(audit[column].min())
        )
        for index in audit.index[
            np.isclose(audit[column].astype(float), worst_value)
        ]:
            reason_by_index[int(index)].append(reason)
    audit["risk_score"] = (
        pd.concat(normalised, axis=1).max(axis=1) if normalised else 0.0
    )
    audit["dominant_risk_reasons"] = [
        ";".join(reason_by_index[int(index)]) for index in audit.index
    ]
    audit["selected_extreme_year"] = audit["dominant_risk_reasons"] != ""
    audit["risk_rank"] = audit["risk_score"].rank(
        method="dense", ascending=False
    ).astype(int)
    return MultiYearReadinessArtifacts(
        fixed_plan=fixed_plan,
        year_audit=audit,
        coverage_audits=coverage_audits,
        readiness_audits=readiness_audits,
        scenario_scope_audits=scope_audits,
    )
