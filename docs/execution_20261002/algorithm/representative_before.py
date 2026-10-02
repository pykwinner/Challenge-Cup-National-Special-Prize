from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .model import HeatPlanningModel
from .scenarios import (
    RepresentativeWeightCalibration,
    calibrate_representative_day_weights,
    extreme_aware_typical_days,
)


@dataclass
class MultiYearRepresentativeArtifacts:
    """Auditable representative periods derived from several weather years."""

    candidate_timeseries: pd.DataFrame
    calibration: RepresentativeWeightCalibration
    combined_chronology: pd.DataFrame
    service_demand_columns: list[str]
    effective_demand_anchor_columns: list[str]
    source_risk_anchor_columns: list[str]
    years: list[str]


@dataclass
class MultiYearStochasticBundleArtifacts:
    """Two-stage scenario bundle with shared investment and yearly dispatch."""

    scenario_bundle: pd.DataFrame
    year_artifacts: dict[str, MultiYearRepresentativeArtifacts]
    scenario_summary: pd.DataFrame
    scenario_probabilities: dict[str, float]
    years: list[str]


def adapt_config_to_weather_year_scenarios(
    config: dict[str, Any], scenario_bundle: pd.DataFrame
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Expand a base-only policy filter to audited weather-year scenarios."""

    if "scenario" not in scenario_bundle.columns:
        raise ValueError("气象年情景束必须包含 scenario 列")
    scenarios = list(
        dict.fromkeys(scenario_bundle["scenario"].astype(str).tolist())
    )
    if not scenarios or any(
        not scenario.startswith("weather_year_") for scenario in scenarios
    ):
        raise ValueError("仅允许对 weather_year_* 情景执行基准政策映射")
    adapted = deepcopy(config)
    policy = adapted.setdefault("policy", {})
    configured = policy.get("transition_target_scenarios")
    audit: dict[str, Any] = {
        "weather_year_scenarios": scenarios,
        "configured_transition_target_scenarios": configured,
        "effective_transition_target_scenarios": configured,
        "mapping_applied": False,
        "mapping_rule": "none",
    }
    if configured is None:
        audit["mapping_rule"] = "no_explicit_filter_all_weather_years"
        return adapted, audit
    targets = list(dict.fromkeys(map(str, configured)))
    if set(targets) == {"base"}:
        policy["transition_target_scenarios"] = scenarios
        audit.update(
            {
                "effective_transition_target_scenarios": scenarios,
                "mapping_applied": True,
                "mapping_rule": "base_expanded_to_all_weather_years",
                "rationale": (
                    "each weather-year scenario is an alternative realization "
                    "of the original base operating boundary"
                ),
            }
        )
    elif set(targets).issubset(scenarios):
        audit["mapping_rule"] = "configured_weather_year_targets_already_valid"
    else:
        raise ValueError(
            "transition_target_scenarios 无法安全映射到气象年情景: "
            f"{targets}"
        )
    return adapted, audit


def service_demand_columns(config: dict[str, Any]) -> list[str]:
    columns = [
        str(column)
        for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    cooling = config.get("multi_energy_services", {}).get("cooling", {})
    columns.extend(
        str(value) for value in cooling.get("demand_columns", {}).values()
    )
    return list(dict.fromkeys(columns))


def representative_feature_columns(
    chronology: pd.DataFrame, service_columns: list[str]
) -> list[str]:
    columns = ["outdoor_temp", *service_columns]
    for optional in (
        "relative_humidity",
        "solar_heat_availability",
        "waste_heat_availability",
        "electricity_price",
        "resident_heating_electricity_price",
        "grid_emission_factor",
    ):
        if optional in chronology.columns:
            columns.append(optional)
    return list(dict.fromkeys(columns))


def add_model_derived_anchor_columns(
    config: dict[str, Any], chronology: pd.DataFrame
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Add effective-load and worst-COP scores used only for day selection."""

    frame = chronology.copy()
    probe = HeatPlanningModel(config, chronology)
    effective_columns: list[str] = []
    for node_name, node in probe.nodes.items():
        for grade_name, column in node.get("demand_columns", {}).items():
            proxy = f"__effective_demand__{node_name}__{grade_name}"
            frame[proxy] = [
                probe._demand_value(node, str(column), t) for t in probe.times
            ]
            effective_columns.append(proxy)
    score_columns: list[str] = []
    for source_name, source in probe.sources.items():
        if source.get("type") != "heat_pump":
            continue
        allowed_grades = source.get("allowed_grades", list(probe.grades))
        proxy = f"__worst_electric_ratio__{source_name}"
        frame[proxy] = [
            max(
                1.0 / probe._cop(source, probe.grades[grade_name], t)
                for grade_name in allowed_grades
            )
            for t in probe.times
        ]
        score_columns.append(proxy)
    return frame, effective_columns, score_columns


def build_multiyear_representative_days(
    config: dict[str, Any],
    annual_timeseries: Mapping[str, pd.DataFrame],
    *,
    n_clusters: int = 6,
    random_state: int = 42,
    regularization: float = 1e-6,
) -> MultiYearRepresentativeArtifacts:
    """Compress several complete years into one average-year planning set.

    Clustering and extreme-day anchoring operate on all supplied years. Day
    occurrence weights are divided by the number of years, so their sum is the
    average calendar length (365 for ordinary years and 366 for leap years).
    Demand-energy targets are likewise the arithmetic mean across years.
    """

    if not annual_timeseries:
        raise ValueError("多气象年代表日生成至少需要一个完整年度")
    blocks: list[pd.DataFrame] = []
    years = [str(year) for year in annual_timeseries]
    for year, annual in annual_timeseries.items():
        if len(annual) == 0 or len(annual) % 24:
            raise ValueError(f"年度 {year} 的行数必须是非零的24整数倍")
        block = annual.copy().reset_index(drop=True)
        block["source_weather_year"] = str(year)
        block["source_hour_index"] = range(len(block))
        # Preserve the original timestamp while guaranteeing uniqueness even
        # when callers use integer hour labels in more than one year.
        block["time"] = (
            str(year) + "::" + block["time"].astype(str)
        )
        blocks.append(block)
    combined = pd.concat(blocks, ignore_index=True)
    service_columns = service_demand_columns(config)
    anchor_frame, effective_columns, score_columns = (
        add_model_derived_anchor_columns(config, combined)
    )
    candidate = extreme_aware_typical_days(
        anchor_frame,
        feature_columns=representative_feature_columns(
            combined, service_columns
        ),
        demand_columns=[*service_columns, *effective_columns],
        n_clusters=n_clusters,
        random_state=random_state,
        anchor_score_columns=score_columns,
    )
    candidate = candidate.drop(
        columns=[*effective_columns, *score_columns], errors="ignore"
    )
    year_count = float(len(years))
    candidate["period_weight"] = (
        candidate["period_weight"].astype(float) / year_count
    )
    candidate["boundary_group"] = candidate["representative_day"].astype(str)

    # The calibration function accepts an unweighted chronology target. Scaling
    # its matched demand columns by 1/N produces the exact multi-year mean.
    average_energy_target = combined.copy()
    average_energy_target[service_columns] = (
        average_energy_target[service_columns].astype(float) / year_count
    )
    calibration = calibrate_representative_day_weights(
        candidate,
        average_energy_target,
        service_columns,
        time_step_hours=float(
            config["planning"].get("time_step_hours", 1.0)
        ),
        minimum_extreme_weight=1.0 / year_count,
        regularization=regularization,
    )
    return MultiYearRepresentativeArtifacts(
        candidate_timeseries=candidate,
        calibration=calibration,
        combined_chronology=combined,
        service_demand_columns=service_columns,
        effective_demand_anchor_columns=effective_columns,
        source_risk_anchor_columns=score_columns,
        years=years,
    )


def build_multiyear_stochastic_representative_bundle(
    config: dict[str, Any],
    annual_timeseries: Mapping[str, pd.DataFrame],
    *,
    scenario_probabilities: Mapping[str, float] | None = None,
    n_clusters_per_year: int = 4,
    random_state: int = 42,
    regularization: float = 1e-6,
) -> MultiYearStochasticBundleArtifacts:
    """Compress each weather year separately and preserve it as a scenario.

    Unlike :func:`build_multiyear_representative_days`, this function does not
    average all years into one chronology.  Investment variables remain shared
    by :class:`HeatPlanningModel`, while dispatch, unit commitment, storage and
    demand response close independently inside every weather-year scenario.
    Per-year occurrence weights reproduce that year's annual service energy;
    scenario probabilities then form the expected annual objective and CVaR.
    """

    if not annual_timeseries:
        raise ValueError("多气象年随机规划至少需要一个完整年度")
    annual_by_year = {
        str(year): frame for year, frame in annual_timeseries.items()
    }
    if len(annual_by_year) != len(annual_timeseries):
        raise ValueError("气象年标签转为字符串后必须唯一")
    years = list(annual_by_year)
    if scenario_probabilities is None:
        probability_by_year = {
            year: 1.0 / len(years) for year in years
        }
    else:
        supplied_probabilities = {
            str(year): value
            for year, value in scenario_probabilities.items()
        }
        missing = set(years) - set(supplied_probabilities)
        extra = set(supplied_probabilities) - set(years)
        if missing or extra:
            raise ValueError(
                f"气象年概率标签不匹配: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        probability_by_year = {
            year: float(supplied_probabilities[year]) for year in years
        }
    probabilities = np.asarray(list(probability_by_year.values()), dtype=float)
    if np.any(~np.isfinite(probabilities)) or np.any(probabilities <= 0.0):
        raise ValueError("气象年场景概率必须为正的有限数值")
    if not np.isclose(float(probabilities.sum()), 1.0, atol=1e-8):
        raise ValueError(
            f"气象年场景概率之和必须为1，当前为 {probabilities.sum():.8f}"
        )

    blocks: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    year_artifacts: dict[str, MultiYearRepresentativeArtifacts] = {}
    scenario_probability_map: dict[str, float] = {}
    for year in years:
        artifacts = build_multiyear_representative_days(
            config,
            {year: annual_by_year[year]},
            n_clusters=n_clusters_per_year,
            random_state=random_state,
            regularization=regularization,
        )
        year_artifacts[year] = artifacts
        scenario = f"weather_year_{year}"
        probability = probability_by_year[year]
        scenario_probability_map[scenario] = probability
        block = artifacts.calibration.timeseries.copy()
        block["representative_day"] = (
            scenario + "::" + block["representative_day"].astype(str)
        )
        block["boundary_group"] = (
            scenario + "::" + block["boundary_group"].astype(str)
        )
        block["scenario"] = scenario
        block["scenario_probability"] = probability
        blocks.append(block)

        day_rows = block.groupby("representative_day", sort=False).first()
        diagnostics = artifacts.calibration.diagnostics
        summaries.append(
            {
                "weather_year": year,
                "scenario": scenario,
                "scenario_probability": probability,
                "chronology_rows": int(len(annual_by_year[year])),
                "representative_days": int(len(day_rows)),
                "representative_rows": int(len(block)),
                "occurrence_weight_days": float(
                    day_rows["period_weight"].astype(float).sum()
                ),
                "extreme_days": int(
                    day_rows["is_extreme_day"].astype(bool).sum()
                ),
                "maximum_absolute_energy_bias": float(
                    diagnostics["calibrated_relative_bias"].abs().max()
                ),
            }
        )
    bundle = pd.concat(blocks, ignore_index=True)
    if bundle["time"].astype(str).duplicated().any():
        raise RuntimeError("逐年代表情景束的 time 标识必须全局唯一")
    return MultiYearStochasticBundleArtifacts(
        scenario_bundle=bundle,
        year_artifacts=year_artifacts,
        scenario_summary=pd.DataFrame(summaries),
        scenario_probabilities=scenario_probability_map,
        years=years,
    )
