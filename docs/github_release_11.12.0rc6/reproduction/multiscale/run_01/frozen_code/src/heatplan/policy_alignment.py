from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import yaml


SHARE_POLICY_FIELDS = (
    "minimum_clean_heat_share",
    "minimum_waste_heat_share",
    "minimum_biomass_heat_share",
    "minimum_non_fossil_heat_share",
    "maximum_fossil_heat_share",
    "minimum_retrofit_heat_share",
    "minimum_renewable_electricity_absorption_rate",
    "minimum_heat_metering_coverage_fraction",
)

RELATIVE_TARGET_PAIRS = (
    (
        "minimum_emission_intensity_reduction_fraction",
        "baseline_emission_intensity_kg_per_kwh",
    ),
    (
        "minimum_direct_fossil_emission_reduction_fraction",
        "baseline_direct_fossil_emission_intensity_kg_per_m2",
    ),
)

ASSESSMENT_MODES = {
    "contextual_reference",
    "conditional_proxy",
    "project_kpi",
    "qualitative_mapping",
    "implementation_gap",
}

COMPARISONS = {"minimum", "maximum", "none"}

SOURCE_VERIFICATION_STATUSES = {
    "verified_official",
    "verified_official_republication",
    "pending_verification",
}

POLICY_ROLES = {
    "strategic_context",
    "conditional_constraint",
    "model_mechanism",
    "mandatory_inventory_screen",
    "local_project_kpi",
}

ENFORCEMENT_READINESS = {
    "reference_only",
    "requires_local_baseline",
    "requires_local_policy_target",
    "requires_asset_inventory",
    "ready_for_project_enforcement",
    "qualitative",
}

SCENARIO_IDS = {
    "scenario1_existing_network",
    "scenario2_distributed_clean",
    "scenario3_high_quality_newbuild",
}


def _finite_float(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须为数值") from exc
    if not np.isfinite(parsed):
        raise ValueError(f"{field} 必须为有限数值")
    return parsed


def _iso_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} 必须为 ISO 日期 YYYY-MM-DD") from exc
    return text


def _is_official_government_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "gov.cn" or host.endswith(".gov.cn")


def validate_policy_config(config: dict[str, Any]) -> None:
    """Validate policy fields that affect optimization semantics.

    National macro targets are deliberately not hard-coded here.  A project
    may enforce them only after supplying an audited project baseline and an
    explicit policy field in its own configuration.
    """

    policy = config.get("policy", {})
    if not isinstance(policy, dict):
        raise ValueError("policy 必须为映射")

    for field in SHARE_POLICY_FIELDS:
        if field not in policy:
            continue
        value = _finite_float(policy[field], field)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{field} 必须位于 [0,1]")

    for reduction_field, baseline_field in RELATIVE_TARGET_PAIRS:
        reduction_present = reduction_field in policy
        baseline_present = baseline_field in policy
        if reduction_present != baseline_present:
            raise ValueError(
                f"{reduction_field} 与 {baseline_field} 必须同时提供"
            )
        if not reduction_present:
            continue
        reduction = _finite_float(policy[reduction_field], reduction_field)
        baseline = _finite_float(policy[baseline_field], baseline_field)
        if not 0.0 <= reduction <= 1.0:
            raise ValueError(f"{reduction_field} 必须位于 [0,1]")
        if baseline <= 0.0:
            raise ValueError(f"{baseline_field} 必须为正")

    nonnegative_fields = (
        "emission_intensity_cap_kg_per_kwh",
        "maximum_direct_fossil_emission_intensity_kg_per_m2",
        "served_floor_area_m2",
        "maximum_average_household_heat_bill_yuan_per_year",
    )
    for field in nonnegative_fields:
        if field in policy and _finite_float(policy[field], field) < 0.0:
            raise ValueError(f"{field} 不得为负")

    for source in config.get("sources", []):
        existing_capacity = _finite_float(
            source.get("existing_capacity_kw", 0.0),
            f"sources.{source.get('name', '?')}.existing_capacity_kw",
        )
        max_retire_capacity = _finite_float(
            source.get("max_retire_capacity_kw", 0.0),
            f"sources.{source.get('name', '?')}.max_retire_capacity_kw",
        )
        minimum_retire_capacity = _finite_float(
            source.get("minimum_retire_capacity_kw", 0.0),
            f"sources.{source.get('name', '?')}.minimum_retire_capacity_kw",
        )
        if minimum_retire_capacity < 0.0:
            raise ValueError("minimum_retire_capacity_kw 不得为负")
        if minimum_retire_capacity > min(max_retire_capacity, existing_capacity) + 1e-9:
            raise ValueError(
                f"热源 {source.get('name', '?')} 的最低退役容量超过可退役容量"
            )
        if "boiler_capacity_t_per_hour" in source:
            boiler_capacity = _finite_float(
                source["boiler_capacity_t_per_hour"],
                f"sources.{source.get('name', '?')}.boiler_capacity_t_per_hour",
            )
            if boiler_capacity <= 0.0:
                raise ValueError("boiler_capacity_t_per_hour 必须为正")
        if "non_fossil_heat_fraction" in source:
            value = _finite_float(
                source["non_fossil_heat_fraction"],
                f"sources.{source.get('name', '?')}.non_fossil_heat_fraction",
            )
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"热源 {source.get('name', '?')} 的 non_fossil_heat_fraction 必须位于 [0,1]"
                )
        if "direct_emission_kg_per_kwh_heat" in source:
            value = _finite_float(
                source["direct_emission_kg_per_kwh_heat"],
                f"sources.{source.get('name', '?')}.direct_emission_kg_per_kwh_heat",
            )
            if value < 0.0:
                raise ValueError(
                    f"热源 {source.get('name', '?')} 的直接排放因子不得为负"
                )

    direct_target_fields = {
        "maximum_direct_fossil_emission_intensity_kg_per_m2",
        "minimum_direct_fossil_emission_reduction_fraction",
    }
    if direct_target_fields.intersection(policy):
        area = _finite_float(policy.get("served_floor_area_m2"), "served_floor_area_m2")
        if area <= 0.0:
            raise ValueError("直接碳排强度目标需要正的 served_floor_area_m2")

    if bool(policy.get("enforce_small_coal_boiler_phaseout", False)):
        threshold = _finite_float(
            policy.get("small_coal_boiler_phaseout_max_t_per_hour", 10.0),
            "small_coal_boiler_phaseout_max_t_per_hour",
        )
        if threshold <= 0.0:
            raise ValueError("small_coal_boiler_phaseout_max_t_per_hour 必须为正")
        for source in config.get("sources", []):
            if (
                source.get("type") != "boiler"
                or source.get("resource_kind") != "fossil"
                or float(source.get("existing_capacity_kw", 0.0)) <= 0.0
            ):
                continue
            name = source.get("name", "?")
            if "boiler_capacity_t_per_hour" not in source:
                raise ValueError(
                    f"启用小燃煤锅炉淘汰时，热源 {name} 必须提供 boiler_capacity_t_per_hour"
                )
            boiler_capacity = float(source["boiler_capacity_t_per_hour"])
            if boiler_capacity <= threshold + 1e-9:
                existing = float(source.get("existing_capacity_kw", 0.0))
                max_retire = float(source.get("max_retire_capacity_kw", 0.0))
                if max_retire + 1e-9 < existing:
                    raise ValueError(
                        f"小燃煤锅炉 {name} 的 max_retire_capacity_kw 必须覆盖全部存量容量"
                    )


def load_policy_profile(path: str | Path) -> dict[str, Any]:
    profile = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    validate_policy_profile(profile)
    return profile


def validate_policy_profile(profile: dict[str, Any]) -> None:
    if not isinstance(profile, dict):
        raise ValueError("政策画像必须为映射")
    metadata = profile.get("profile")
    if not isinstance(metadata, dict):
        raise ValueError("政策画像缺少 profile 元数据")
    for field in ("id", "name", "period"):
        if not str(metadata.get(field, "")).strip():
            raise ValueError(f"政策画像 profile.{field} 不得为空")

    sources = profile.get("sources", [])
    targets = profile.get("targets", [])
    if not isinstance(sources, list) or not sources:
        raise ValueError("政策画像必须至少包含一个来源")
    if not isinstance(targets, list) or not targets:
        raise ValueError("政策画像必须至少包含一个目标")

    source_ids: set[str] = set()
    for source in sources:
        source_id = str(source.get("id", "")).strip()
        if not source_id or source_id in source_ids:
            raise ValueError("政策来源 id 必须非空且唯一")
        source_ids.add(source_id)
        url = str(source.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"政策来源 {source_id} 的 URL 无效")
        verification_status = str(
            source.get("verification_status", "")
        ).strip()
        if verification_status and verification_status not in SOURCE_VERIFICATION_STATUSES:
            raise ValueError(f"政策来源 {source_id} 的 verification_status 无效")
        if (
            verification_status == "verified_official"
            and not _is_official_government_url(url)
        ):
            raise ValueError(
                f"政策来源 {source_id} 标记为 verified_official 时必须使用政府官网 URL"
            )
        for date_field in ("issued_date", "retrieved_date"):
            if date_field in source:
                _iso_date(source[date_field], f"{source_id}.{date_field}")

    target_codes: set[str] = set()
    for target in targets:
        code = str(target.get("code", "")).strip()
        if not code or code in target_codes:
            raise ValueError("政策目标 code 必须非空且唯一")
        target_codes.add(code)
        source_id = str(target.get("source_id", "")).strip()
        if source_id not in source_ids:
            raise ValueError(f"政策目标 {code} 引用了未知来源 {source_id}")
        mode = str(target.get("assessment_mode", "")).strip()
        if mode not in ASSESSMENT_MODES:
            raise ValueError(f"政策目标 {code} 的 assessment_mode 无效")
        comparison = str(target.get("comparison", "none")).strip()
        if comparison not in COMPARISONS:
            raise ValueError(f"政策目标 {code} 的 comparison 无效")
        if comparison != "none":
            _finite_float(target.get("target_value"), f"{code}.target_value")
        policy_role = str(target.get("policy_role", "")).strip()
        if policy_role and policy_role not in POLICY_ROLES:
            raise ValueError(f"政策目标 {code} 的 policy_role 无效")
        readiness = str(target.get("enforcement_readiness", "")).strip()
        if readiness and readiness not in ENFORCEMENT_READINESS:
            raise ValueError(f"政策目标 {code} 的 enforcement_readiness 无效")
        applicable_scenarios = target.get("applicable_scenarios")
        if applicable_scenarios is not None:
            if not isinstance(applicable_scenarios, list) or not applicable_scenarios:
                raise ValueError(f"政策目标 {code} 的 applicable_scenarios 必须为非空列表")
            unknown = set(map(str, applicable_scenarios)) - SCENARIO_IDS
            if unknown:
                raise ValueError(
                    f"政策目标 {code} 含未知场景: {sorted(unknown)}"
                )


def evaluate_policy_alignment(
    summary: dict[str, Any],
    profile: dict[str, Any],
    scenario_id: str | None = None,
    as_of_date: str | None = None,
) -> pd.DataFrame:
    """Create an auditable policy-to-model alignment table.

    Only ``project_kpi`` targets receive a compliance verdict.  National
    macro targets remain contextual references unless the project owner has
    supplied a local, denominator-consistent target.
    """

    validate_policy_profile(profile)
    if scenario_id is not None and scenario_id not in SCENARIO_IDS:
        raise ValueError(f"未知供热场景: {scenario_id}")
    if as_of_date is not None:
        _iso_date(as_of_date, "as_of_date")

    sources = {str(source["id"]): source for source in profile["sources"]}
    rows: list[dict[str, Any]] = []
    for target in profile["targets"]:
        source = sources[str(target["source_id"])]
        metric_key = target.get("model_metric")
        raw_actual = summary.get(metric_key) if metric_key else None
        actual = (
            float(raw_actual)
            if isinstance(raw_actual, (int, float)) and np.isfinite(raw_actual)
            else None
        )
        target_value = target.get("target_value")
        comparison = str(target.get("comparison", "none"))
        mode = str(target["assessment_mode"])
        readiness = str(target.get("enforcement_readiness", "")).strip()
        applicable_scenarios = list(target.get("applicable_scenarios", []))
        source_verification = str(
            source.get("verification_status", "unspecified")
        )
        compliant: bool | None = None
        gap: float | None = None

        if scenario_id is not None and applicable_scenarios and scenario_id not in applicable_scenarios:
            status = "not_applicable_to_scenario"
        elif source_verification == "pending_verification":
            status = "source_verification_required"
        elif (
            mode == "project_kpi"
            and readiness
            and readiness != "ready_for_project_enforcement"
        ):
            status = {
                "requires_local_baseline": "needs_audited_local_baseline",
                "requires_local_policy_target": "needs_local_policy_target",
                "requires_asset_inventory": "needs_local_asset_inventory",
            }.get(readiness, "not_ready_for_project_enforcement")
        elif mode == "project_kpi" and comparison != "none":
            if actual is None:
                status = "missing_model_metric"
            else:
                target_number = float(target_value)
                if comparison == "minimum":
                    gap = actual - target_number
                    compliant = gap >= -1e-9
                else:
                    gap = target_number - actual
                    compliant = gap >= -1e-9
                status = "compliant" if compliant else "not_compliant"
        elif mode == "conditional_proxy":
            status = {
                "requires_asset_inventory": "needs_local_asset_inventory",
                "requires_local_policy_target": "needs_local_policy_target",
            }.get(readiness, "needs_audited_local_baseline")
        elif mode == "contextual_reference":
            status = "national_reference_only"
        elif mode == "qualitative_mapping":
            status = "mapped_to_model"
        else:
            status = "not_yet_modeled"

        rows.append(
            {
                "profile_id": profile["profile"]["id"],
                "scenario_id": scenario_id,
                "as_of_date": as_of_date,
                "target_code": target["code"],
                "target_name": target["name"],
                "policy_scope": target.get("policy_scope", ""),
                "policy_role": target.get("policy_role", ""),
                "enforcement_readiness": readiness,
                "applicable_scenarios": ";".join(applicable_scenarios),
                "assessment_mode": mode,
                "comparison": comparison,
                "target_value": target_value,
                "target_unit": target.get("unit", ""),
                "model_metric": metric_key,
                "actual_value": actual,
                "gap_to_target": gap,
                "compliant": compliant,
                "status": status,
                "source_id": target["source_id"],
                "source_title": source.get("title", ""),
                "source_authority": source.get("authority", ""),
                "source_document_number": source.get("document_number", ""),
                "source_issued_date": str(source.get("issued_date", "")),
                "source_verification_status": source_verification,
                "source_url": source["url"],
                "model_mapping": target.get("model_mapping", ""),
                "evidence_required": ";".join(
                    map(str, target.get("evidence_required", []))
                ),
                "caveat": target.get("caveat", ""),
            }
        )
    return pd.DataFrame(rows)


def classify_policy_status(status: str) -> str:
    if status in {"compliant", "mapped_to_model"}:
        return "model_ready"
    if status == "national_reference_only":
        return "strategic_reference"
    if status == "not_applicable_to_scenario":
        return "not_applicable"
    if status in {
        "needs_audited_local_baseline",
        "needs_local_policy_target",
        "needs_local_asset_inventory",
        "source_verification_required",
        "not_ready_for_project_enforcement",
    }:
        return "evidence_gap"
    return "implementation_or_compliance_gap"


def classify_policy_row(row: pd.Series) -> str:
    base = classify_policy_status(str(row["status"]))
    readiness = str(row.get("enforcement_readiness", ""))
    if base == "model_ready" and readiness.startswith("requires_"):
        return "model_ready_evidence_pending"
    return base


def build_policy_readiness_summary(alignment: pd.DataFrame) -> pd.DataFrame:
    """Summarize policy rows without treating national references as compliance."""

    required = {"status", "scenario_id"}
    missing = required - set(alignment.columns)
    if missing:
        raise ValueError(f"政策对齐表缺少字段: {sorted(missing)}")
    frame = alignment.copy()
    frame["readiness_group"] = frame.apply(classify_policy_row, axis=1)
    return (
        frame.groupby(["scenario_id", "readiness_group"], dropna=False)
        .size()
        .rename("target_count")
        .reset_index()
        .sort_values(["scenario_id", "readiness_group"], na_position="last")
        .reset_index(drop=True)
    )


def build_policy_action_register(alignment: pd.DataFrame) -> pd.DataFrame:
    """Return only applicable policy rows that still require evidence or code work."""

    frame = alignment.copy()
    frame["readiness_group"] = frame.apply(classify_policy_row, axis=1)
    frame = frame[
        frame["readiness_group"].isin(
            {
                "evidence_gap",
                "model_ready_evidence_pending",
                "implementation_or_compliance_gap",
            }
        )
    ]
    columns = [
        "scenario_id",
        "target_code",
        "target_name",
        "policy_role",
        "status",
        "evidence_required",
        "model_mapping",
        "source_title",
        "source_document_number",
        "source_url",
        "caveat",
    ]
    return frame.loc[:, columns].reset_index(drop=True)


def export_policy_alignment(
    summary_path: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
    scenario_id: str | None = None,
    as_of_date: str | None = None,
) -> pd.DataFrame:
    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    profile = load_policy_profile(profile_path)
    result = evaluate_policy_alignment(
        summary,
        profile,
        scenario_id=scenario_id,
        as_of_date=as_of_date,
    )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    result.to_csv(destination / "policy_alignment.csv", index=False, encoding="utf-8-sig")
    records = (
        result.astype(object)
        .where(pd.notna(result), None)
        .to_dict(orient="records")
    )
    (destination / "policy_alignment.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    readiness = build_policy_readiness_summary(result)
    readiness.to_csv(
        destination / "policy_readiness_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    actions = build_policy_action_register(result)
    actions.to_csv(
        destination / "policy_action_register.csv",
        index=False,
        encoding="utf-8-sig",
    )
    return result
