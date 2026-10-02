from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


@dataclass
class RepresentativeWeightCalibration:
    """Explicit, audited representative-day weight calibration result."""

    timeseries: pd.DataFrame
    weights: pd.DataFrame
    diagnostics: pd.DataFrame
    summary: dict[str, Any]


def cluster_typical_days(hourly: pd.DataFrame, feature_columns: list[str], n_clusters: int = 5, random_state: int = 42):
    """Return centroid-nearest actual days, occurrence weights and cluster labels.

    These exemplars are not pairwise-distance k-medoids. Identical profiles can
    provide fewer distinct representatives than the requested cluster budget.

    Seasonal storage users must add month anchors/state-transition constraints; ordinary
    typical-day cyclic SOC is not valid for BTES.
    """
    if not len(hourly) or len(hourly) % 24:
        raise ValueError("输入行数必须是 24 的整数倍")
    if not feature_columns:
        raise ValueError("至少需要一个聚类特征")
    if isinstance(n_clusters, bool) or not isinstance(n_clusters, (int, np.integer)) or not 1 <= n_clusters <= len(hourly) // 24:
        raise ValueError("n_clusters 必须是介于1和完整天数之间的整数")
    values = hourly[feature_columns].to_numpy(dtype=float).reshape(-1, 24 * len(feature_columns))
    if not np.isfinite(values).all():
        raise ValueError("聚类特征必须全部为有限数值；请先明确处理缺测")
    effective_clusters = min(n_clusters, len(np.unique(values, axis=0)))
    scaled = StandardScaler().fit_transform(values)
    raw_labels = KMeans(n_clusters=effective_clusters, random_state=random_state, n_init=20).fit_predict(scaled)
    # Also tolerate a numerically degenerate fit that produces fewer labels.
    _, labels = np.unique(raw_labels, return_inverse=True)
    medoids: list[int] = []
    weights: list[int] = []
    for label in range(int(labels.max()) + 1):
        members = np.where(labels == label)[0]
        center = scaled[members].mean(axis=0)
        medoid = members[np.argmin(np.linalg.norm(scaled[members] - center, axis=1))]
        medoids.append(int(medoid))
        weights.append(int(len(members)))
    return medoids, weights, labels


def _representative_hour_adjacency(hourly: pd.DataFrame) -> tuple[np.ndarray, str]:
    """Identify observed one-hour edges without bridging gaps or scenarios.

    Legacy opaque hour labels support within-block edges only. Source timestamps
    preserved by the multiyear adapter take precedence over synthetic time IDs.
    """
    column = next(c for c in ("source_time", "timestamp", "time") if c in hourly)
    selected_time_column = column
    raw = hourly[column]
    if pd.api.types.is_numeric_dtype(raw):
        parsed = pd.Series(pd.NaT, index=hourly.index, dtype="datetime64[ns]")
    else:
        parsed = pd.to_datetime(raw, format="ISO8601", errors="coerce")
    within_day = np.arange(1, len(hourly)) % 24 != 0
    group_columns = [c for c in ("scenario", "boundary_group", "source_weather_year") if c in hourly]
    same_group = np.ones(len(hourly) - 1, dtype=bool)
    for column in group_columns:
        if hourly[column].isna().any():
            raise ValueError(f"时序边界列 {column} 不允许缺失")
        group = hourly[column].astype(str).to_numpy()
        same_group &= group[1:] == group[:-1]
    if np.any(within_day & ~same_group):
        raise ValueError("一个24小时代表日块内不能跨越情景或状态边界")
    if parsed.isna().all():
        if selected_time_column != "time":
            raise ValueError(f"{selected_time_column} 必须提供可解析的真实时标")
        return within_day & same_group, "opaque_labels_within_block_only"
    if parsed.isna().any():
        raise ValueError("真实时标不能混有缺失或无法解析的值")
    delta = parsed.diff().iloc[1:].to_numpy()
    consecutive = delta == np.timedelta64(1, "h")
    if np.any(within_day & ~consecutive):
        raise ValueError("每个24小时日块必须包含连续逐小时时标，不得拼接缺测小时")
    keys = hourly[group_columns].astype(str).copy()
    keys["__observed_time"] = parsed.to_numpy()
    if keys.duplicated().any():
        raise ValueError("同一情景的真实小时不能重复")
    if np.any(same_group & (delta <= np.timedelta64(0, "h"))):
        raise ValueError("同一情景的真实时标必须递增")
    return consecutive & same_group, "observed_hourly_edges"


def budget_stress_scenarios(base: pd.DataFrame, demand_columns: list[str], gamma: int, deviation: float = 0.10):
    """Generate a small scenario set for a budgeted uncertainty baseline.

    Each scenario stresses up to gamma high-load hours. For full-scale work replace this
    enumeration with C&CG, keeping investment variables shared across scenarios.
    """
    if gamma < 0:
        raise ValueError("gamma 必须非负")
    score = base[demand_columns].sum(axis=1).to_numpy()
    candidate_hours = list(np.argsort(score)[-min(8, len(score)):])
    scenarios = [("nominal", base.copy())]
    for size in range(1, min(gamma, len(candidate_hours)) + 1):
        for chosen in combinations(candidate_hours, size):
            stressed = base.copy()
            stressed.loc[list(chosen), demand_columns] *= 1.0 + deviation
            scenarios.append(("stress_" + "_".join(map(str, chosen)), stressed))
    return scenarios


def build_correlated_northern_scenario_bundle(
    base: pd.DataFrame,
    demand_columns: list[str],
    specifications: list[dict],
    period_weight: float = 180.0,
) -> pd.DataFrame:
    """Create a shared-investment scenario bundle for northern heating planning.

    Each specification describes a physically correlated event. For example, a
    cold wave can simultaneously reduce outdoor temperature, increase heat load,
    lower industrial waste-heat availability and raise electricity prices. The
    returned blocks share investment variables in :class:`HeatPlanningModel`, but
    storage, demand response and unit commitment close within each scenario.
    """
    if not specifications:
        raise ValueError("至少需要一个场景")
    probability_sum = sum(float(spec["probability"]) for spec in specifications)
    if not np.isclose(probability_sum, 1.0, atol=1e-8):
        raise ValueError(f"场景概率之和必须为1，当前为 {probability_sum:.8f}")
    missing_demand = set(demand_columns) - set(base.columns)
    if missing_demand:
        raise ValueError(f"缺少负荷列: {sorted(missing_demand)}")

    blocks: list[pd.DataFrame] = []
    for spec in specifications:
        name = str(spec["name"])
        block = base.copy()
        if "outdoor_temp" in block:
            block["outdoor_temp"] += float(spec.get("temperature_offset_c", 0.0))
        if "waste_source_temp" in block:
            block["waste_source_temp"] += float(spec.get("waste_source_temp_offset_c", 0.0))
        block[demand_columns] *= float(spec.get("demand_multiplier", 1.0))
        if "waste_heat_availability" in block:
            block["waste_heat_availability"] *= float(spec.get("waste_heat_multiplier", 1.0))
            block["waste_heat_availability"] = block["waste_heat_availability"].clip(0.0, 1.0)
        price_columns = [
            column for column in block.columns
            if column == "electricity_price" or column.endswith("_electricity_price")
        ]
        if price_columns:
            block[price_columns] *= float(spec.get("electricity_price_multiplier", 1.0))
        if "grid_emission_factor" in block:
            block["grid_emission_factor"] *= float(spec.get("grid_emission_multiplier", 1.0))
        if "grid_availability" in block:
            block["grid_availability"] *= float(spec.get("grid_availability_multiplier", 1.0))
            block["grid_availability"] = block["grid_availability"].clip(0.0, 1.0)
        if "biomass_delivery_availability" in block:
            block["biomass_delivery_availability"] *= float(
                spec.get("biomass_delivery_availability_multiplier", 1.0)
            )
            block["biomass_delivery_availability"] = block[
                "biomass_delivery_availability"
            ].clip(0.0, 1.0)
        block["scenario"] = name
        block["scenario_probability"] = float(spec["probability"])
        # Preserve occurrence weights produced by representative-day
        # compression.  A scenario-level override remains available when it is
        # explicitly provided in the scenario specification.
        if "period_weight" in spec:
            block["period_weight"] = float(spec["period_weight"])
        elif "period_weight" not in block.columns:
            block["period_weight"] = float(period_weight)
        else:
            block["period_weight"] = block["period_weight"].astype(float)
        # Short-term storage and unit commitment must close within each
        # representative day rather than linking unrelated medoids.  Prefixing
        # the original boundary also keeps scenario states independent.
        if "boundary_group" in block.columns:
            block["boundary_group"] = name + "::" + block["boundary_group"].astype(str)
        else:
            block["boundary_group"] = name
        blocks.append(block)
    return pd.concat(blocks, ignore_index=True)


def extreme_aware_typical_days(
    hourly: pd.DataFrame,
    feature_columns: list[str],
    demand_columns: list[str],
    n_clusters: int = 5,
    random_state: int = 42,
    availability_columns: list[str] | None = None,
    anchor_score_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Select actual days and force system- and series-level extreme anchors.

    In addition to system demand peak/ramp, every non-zero demand series keeps
    its own peak and maximum-ramp day.  Minimum temperature and non-constant
    resource-availability days are also retained.  Duplicate anchors are merged
    by source-day index.  Each newly added anchor receives one occurrence day,
    removed from its ordinary cluster, so total represented days stay constant.
    Maximum ramps use genuinely adjacent hours when timestamps are available.
    Both incident days of a midnight ramp are retained; this does not itself
    introduce interday operational constraints in an independently cycled model.
    """
    if len(hourly) % 24:
        raise ValueError("输入行数必须是 24 的整数倍")
    days = len(hourly) // 24
    if days == 0:
        raise ValueError("输入至少需要一个完整日")
    if n_clusters > days:
        raise ValueError("n_clusters 不能大于完整天数")
    feature_columns = list(dict.fromkeys(str(column) for column in feature_columns))
    demand_columns = list(dict.fromkeys(str(column) for column in demand_columns))
    anchor_score_columns = list(
        dict.fromkeys(str(column) for column in (anchor_score_columns or []))
    )
    missing = sorted(
        (
            set(feature_columns)
            | set(demand_columns)
            | set(anchor_score_columns)
            | {"time"}
        )
        - set(hourly.columns)
    )
    if missing:
        raise ValueError(f"代表日生成缺少列: {missing}")
    if not demand_columns:
        raise ValueError("至少需要一个负荷列")
    if availability_columns is None:
        availability_columns = [str(c) for c in hourly if str(c).endswith("_availability")]
    numeric_columns = set(feature_columns + demand_columns + anchor_score_columns + list(availability_columns))
    if "outdoor_temp" in hourly:
        numeric_columns.add("outdoor_temp")
    for column in numeric_columns:
        if column not in hourly or not np.isfinite(hourly[column].to_numpy(dtype=float)).all():
            raise ValueError(f"代表日列 {column} 必须存在且全部为有限数值")
    adjacent, adjacency_mode = _representative_hour_adjacency(hourly)
    medoids, weights, labels = cluster_typical_days(
        hourly, feature_columns, n_clusters, random_state
    )

    anchor_reasons: dict[int, list[str]] = {}

    def add_anchor(day: int, reason: str) -> None:
        reasons = anchor_reasons.setdefault(int(day), [])
        if reason not in reasons:
            reasons.append(reason)

    def add_ramp_anchors(values: np.ndarray, reason: str) -> None:
        ramps = np.abs(np.diff(values.reshape(-1)))
        eligible = np.flatnonzero(adjacent)
        if not len(eligible):
            return
        edge = int(eligible[np.argmax(ramps[eligible])])
        if float(ramps[edge]) > 1e-12:
            add_anchor(edge // 24, reason)
            add_anchor((edge + 1) // 24, reason)

    demand_cube = hourly[demand_columns].astype(float).to_numpy().reshape(
        days, 24, len(demand_columns)
    )
    system_demand = demand_cube.sum(axis=2)
    if float(np.max(system_demand)) > 1e-12:
        add_anchor(
            int(np.argmax(system_demand.max(axis=1))),
            "maximum_system_demand",
        )
    add_ramp_anchors(system_demand, "maximum_system_ramp")
    for column_index, column in enumerate(demand_columns):
        values = demand_cube[:, :, column_index]
        if float(np.max(np.abs(values))) <= 1e-12:
            continue
        add_anchor(
            int(np.argmax(values.max(axis=1))),
            f"maximum_peak::{column}",
        )
        add_ramp_anchors(values, f"maximum_ramp::{column}")
    if "outdoor_temp" in hourly.columns:
        outdoor = (
            hourly["outdoor_temp"].astype(float).to_numpy().reshape(days, 24)
        )
        add_anchor(
            int(np.argmin(outdoor.min(axis=1))),
            "minimum_temperature",
        )
    if availability_columns is None:
        availability_columns = [
            str(column)
            for column in hourly.columns
            if str(column).endswith("_availability")
        ]
    for column in dict.fromkeys(availability_columns):
        if column not in hourly.columns:
            raise ValueError(f"资源可用率列不存在: {column}")
        daily_mean = (
            hourly[column].astype(float).to_numpy().reshape(days, 24).mean(axis=1)
        )
        if float(np.ptp(daily_mean)) > 1e-12:
            add_anchor(
                int(np.argmin(daily_mean)),
                f"minimum_availability::{column}",
            )
    for column in anchor_score_columns:
        values = hourly[column].astype(float).to_numpy().reshape(days, 24)
        daily_maximum = values.max(axis=1)
        if float(np.ptp(daily_maximum)) > 1e-12:
            add_anchor(
                int(np.argmax(daily_maximum)),
                f"maximum_score::{column}",
            )

    selected: list[tuple[int, float, str]] = [
        (day, float(weight), f"cluster_{cluster}") for cluster, (day, weight) in enumerate(zip(medoids, weights))
    ]
    selected_days = {day for day, _, _ in selected}
    mutable_weights = {day: weight for day, weight, _ in selected}
    for day in sorted(anchor_reasons):
        if day in selected_days:
            continue
        cluster = int(labels[day])
        medoid = medoids[cluster]
        mutable_weights[medoid] -= 1.0
        selected.append((day, 1.0, f"extreme_day_{day:03d}"))
        selected_days.add(day)

    blocks: list[pd.DataFrame] = []
    for order, (day, initial_weight, label) in enumerate(selected):
        weight = mutable_weights.get(day, initial_weight)
        if weight <= 0:
            continue
        block = hourly.iloc[day * 24 : (day + 1) * 24].copy()
        block["original_time"] = block["time"].astype(str)
        block["time"] = [f"rep_{order:02d}_{value}" for value in block["original_time"]]
        block["period_weight"] = weight
        block["representative_day"] = label
        block["original_day_index"] = int(day)
        reasons = anchor_reasons.get(day, [])
        block["extreme_reasons"] = ";".join(reasons)
        block["is_extreme_day"] = bool(reasons)
        blocks.append(block)
    result = pd.concat(blocks, ignore_index=True)
    result.attrs["ramp_adjacency_mode"] = adjacency_mode
    result.attrs["interday_operational_constraints_added"] = False
    if not np.isclose(result.groupby("representative_day")["period_weight"].first().sum(), days):
        raise RuntimeError("代表日权重未保持全年天数")
    return result


def calibrate_representative_day_weights(
    representative: pd.DataFrame,
    chronology: pd.DataFrame,
    match_columns: list[str],
    *,
    day_column: str = "representative_day",
    weight_column: str = "period_weight",
    extreme_column: str = "is_extreme_day",
    time_step_hours: float = 1.0,
    minimum_day_weight: float = 1e-6,
    minimum_extreme_weight: float = 1.0,
    regularization: float = 1e-4,
) -> RepresentativeWeightCalibration:
    """Fit service energy softly while conserving day weight and extreme bounds.

    This is an explicit candidate-generation operation.  It returns a copy and
    audit tables; it never writes the calibrated weights back to source files.
    Scenario probabilities are held fixed and only enter the energy accounting.
    """

    from scipy.optimize import Bounds, minimize

    match_columns = list(dict.fromkeys(str(column) for column in match_columns))
    required = {day_column, weight_column, *match_columns}
    missing_representative = sorted(required - set(representative.columns))
    missing_chronology = sorted(set(match_columns) - set(chronology.columns))
    if missing_representative:
        raise ValueError(f"代表时段权重校准缺少列: {missing_representative}")
    if missing_chronology:
        raise ValueError(f"连续时序权重校准缺少列: {missing_chronology}")
    dt = float(time_step_hours)
    if not np.isfinite(dt) or dt <= 0.0:
        raise ValueError("time_step_hours 必须为正的有限数值")
    if not np.isfinite([minimum_day_weight, minimum_extreme_weight]).all() or minimum_day_weight < 0.0 or minimum_extreme_weight < minimum_day_weight:
        raise ValueError("代表日权重下界设置无效")
    if not np.isfinite(regularization) or regularization < 0.0:
        raise ValueError("regularization 不能为负")

    day_order = list(dict.fromkeys(representative[day_column].astype(str)))
    if not day_order:
        raise ValueError("没有可校准的代表日")
    frame = representative.copy()
    if frame[day_column].isna().any():
        raise ValueError("代表日标识不允许缺失")
    if not np.isfinite(frame[weight_column].to_numpy(dtype=float)).all():
        raise ValueError("代表日原始权重必须为非负有限数值")
    frame[day_column] = frame[day_column].astype(str)
    weight_counts = frame.groupby(day_column, sort=False)[weight_column].nunique()
    if int(weight_counts.max()) != 1:
        raise ValueError("同一代表日的 period_weight 必须保持一致")
    original_weights = (
        frame.groupby(day_column, sort=False)[weight_column]
        .first()
        .reindex(day_order)
        .astype(float)
        .to_numpy()
    )
    if np.any(~np.isfinite(original_weights)) or np.any(original_weights < 0.0):
        raise ValueError("代表日原始权重必须为非负有限数值")
    target_weight_sum = float(original_weights.sum())

    if "scenario_probability" in frame.columns:
        probability = frame["scenario_probability"].astype(float).to_numpy()
    elif "scenario" in frame.columns:
        scenario_count = max(frame["scenario"].astype(str).nunique(), 1)
        probability = np.full(len(frame), 1.0 / scenario_count)
    else:
        probability = np.ones(len(frame), dtype=float)
    if np.any(~np.isfinite(probability)) or np.any(probability < 0.0):
        raise ValueError("scenario_probability 必须为非负有限数值")

    design_rows: list[np.ndarray] = []
    targets: list[float] = []
    used_columns: list[str] = []
    for column in match_columns:
        if not np.isfinite(frame[column].to_numpy(dtype=float)).all() or not np.isfinite(chronology[column].to_numpy(dtype=float)).all():
            raise ValueError(f"校准列 {column} 含缺测或非有限数值，不能静默跳过")
        row_contribution = frame[column].astype(float).to_numpy() * probability * dt
        contribution = (
            pd.Series(row_contribution, index=frame.index)
            .groupby(frame[day_column], sort=False)
            .sum()
            .reindex(day_order)
            .to_numpy(dtype=float)
        )
        target = float(chronology[column].astype(float).sum() * dt)
        if abs(target) <= 1e-12 and float(np.max(np.abs(contribution))) <= 1e-12:
            continue
        design_rows.append(contribution)
        targets.append(target)
        used_columns.append(column)
    if not design_rows:
        raise ValueError("所选校准列均没有有效能量")
    design = np.vstack(design_rows)
    target_vector = np.asarray(targets, dtype=float)
    scales = np.maximum(np.abs(target_vector), 1.0)

    extreme_by_day = (
        frame.groupby(day_column, sort=False)[extreme_column].any().reindex(day_order)
        if extreme_column in frame.columns
        else pd.Series(False, index=day_order)
    )
    lower = np.where(
        extreme_by_day.to_numpy(dtype=bool),
        float(minimum_extreme_weight),
        float(minimum_day_weight),
    )
    if float(lower.sum()) > target_weight_sum + 1e-9:
        raise ValueError("权重下界之和超过原代表天数")
    relative_scale = np.maximum(original_weights, 1.0)

    def objective(weights: np.ndarray) -> float:
        residual = (design @ weights - target_vector) / scales
        displacement = (weights - original_weights) / relative_scale
        return float(
            np.mean(residual**2)
            + float(regularization) * np.mean(displacement**2)
        )

    def gradient(weights: np.ndarray) -> np.ndarray:
        residual = (design @ weights - target_vector) / scales
        return (
            2.0 * (design / scales[:, None]).T @ residual / len(target_vector)
            + 2.0 * float(regularization) * (weights - original_weights)
            / relative_scale**2 / len(original_weights)
        )

    slack = np.maximum(original_weights - lower, 0.0)
    if not slack.sum():
        slack = np.ones_like(slack)
    initial = lower + slack / slack.sum() * max(0.0, target_weight_sum - lower.sum())

    result = minimize(
        objective,
        initial,
        method="SLSQP",
        jac=gradient,
        bounds=Bounds(lower, np.full(len(day_order), np.inf)),
        constraints={
            "type": "eq",
            "fun": lambda weights: float(weights.sum() - target_weight_sum),
            "jac": lambda weights: np.ones_like(weights),
        },
        options={"ftol": 1e-12, "maxiter": 2000, "disp": False},
    )
    if not result.success:
        raise RuntimeError(f"代表日权重校准失败: {result.message}")
    calibrated_weights = np.asarray(result.x, dtype=float)
    feasibility_tolerance = 1e-8 * max(1.0, target_weight_sum)
    conservation_residual = abs(float(calibrated_weights.sum()) - target_weight_sum)
    bound_violation = float(np.maximum(lower - calibrated_weights, 0.0).max())
    if not np.isfinite(calibrated_weights).all() or not np.isfinite(result.fun) or conservation_residual > feasibility_tolerance or bound_violation > feasibility_tolerance:
        raise RuntimeError("代表日权重校准返回了非有限值或未满足权重守恒/下界")
    weight_map = dict(zip(day_order, calibrated_weights))
    calibrated = frame.copy()
    calibrated[weight_column] = calibrated[day_column].map(weight_map).astype(float)

    original_totals = design @ original_weights
    calibrated_totals = design @ calibrated_weights
    diagnostics = pd.DataFrame(
        {
            "column": used_columns,
            "chronology_total": target_vector,
            "original_representative_total": original_totals,
            "calibrated_representative_total": calibrated_totals,
            "original_relative_bias": (original_totals - target_vector) / scales,
            "calibrated_relative_bias": (calibrated_totals - target_vector) / scales,
        }
    )
    weight_audit = pd.DataFrame(
        {
            day_column: day_order,
            "is_extreme_day": extreme_by_day.to_numpy(dtype=bool),
            "original_weight": original_weights,
            "calibrated_weight": calibrated_weights,
            "weight_change": calibrated_weights - original_weights,
        }
    )
    summary = {
        "application_mode": "explicit_function_call_only",
        "source_files_modified": False,
        "solver": "SLSQP_constrained_least_squares",
        "solver_success": bool(result.success),
        "solver_message": str(result.message),
        "energy_matching": "soft_regularized_least_squares",
        "weight_conservation_residual": conservation_residual,
        "maximum_bound_violation": bound_violation,
        "objective_value": float(result.fun),
        "matched_columns": used_columns,
        "representative_days": len(day_order),
        "original_weight_sum_days": target_weight_sum,
        "calibrated_weight_sum_days": float(calibrated_weights.sum()),
        "minimum_calibrated_weight": float(calibrated_weights.min()),
        "minimum_extreme_calibrated_weight": (
            float(calibrated_weights[extreme_by_day.to_numpy(dtype=bool)].min())
            if bool(extreme_by_day.any())
            else None
        ),
        "maximum_absolute_original_bias": float(
            np.max(np.abs(diagnostics["original_relative_bias"]))
        ),
        "maximum_absolute_calibrated_bias": float(
            np.max(np.abs(diagnostics["calibrated_relative_bias"]))
        ),
        "regularization": float(regularization),
        "caveat": (
            "candidate weights only; rerun coverage, investment stability and "
            "fixed-investment chronological replay before adoption"
        ),
    }
    return RepresentativeWeightCalibration(
        timeseries=calibrated,
        weights=weight_audit,
        diagnostics=diagnostics,
        summary=summary,
    )
