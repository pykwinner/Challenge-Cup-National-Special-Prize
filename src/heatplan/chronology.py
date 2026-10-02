from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd
import yaml


@dataclass
class AnnualReplayArtifacts:
    """Planning solution plus fixed-investment chronological replay."""

    planning: Any
    replay: Any
    fixed_config: dict[str, Any]
    chronological_timeseries: pd.DataFrame
    investment_freeze_audit: pd.DataFrame
    kpi_comparison: pd.DataFrame
    chronology_audit: dict[str, Any]
    representative_coverage_audit: pd.DataFrame
    representative_recommendations: pd.DataFrame
    scenario_scope_audit: dict[str, Any]
    fixed_investment_readiness_audit: dict[str, Any]
    planning_model_profile: dict[str, Any]
    replay_model_profile: dict[str, Any]


@dataclass
class AnnualReplayPreflight:
    """Validated chronological inputs and representative-period diagnostics."""

    chronological_timeseries: pd.DataFrame
    chronology_audit: dict[str, Any]
    representative_coverage_audit: pd.DataFrame
    representative_recommendations: pd.DataFrame
    scenario_scope_audit: dict[str, Any]


@dataclass
class FixedInvestmentPlanArtifacts:
    """One representative-period plan reusable across many chronologies."""

    planning: Any
    fixed_config: dict[str, Any]
    investment_freeze_audit: pd.DataFrame
    planning_model_profile: dict[str, Any]


_FIXED_PLAN_CHECKPOINT_SCHEMA = "heatplan.fixed_investment_checkpoint.v1"
_FIXED_PLAN_CHECKPOINT_FILES = {
    "planning_summary": "representative_planning/summary.json",
    "planning_investments": "representative_planning/investments.csv",
    "planning_dispatch": "representative_planning/dispatch.csv",
    "fixed_config": "fixed_investment_config.yaml",
    "investment_freeze_audit": "investment_freeze_audit.csv",
    "planning_model_profile": "planning_model_profile.json",
}


def _canonical_config_fingerprint(config: dict[str, Any]) -> str:
    payload = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _timeseries_fingerprint(timeseries: pd.DataFrame) -> str:
    """Return a deterministic fingerprint for a loaded representative set."""

    digest = sha256()
    digest.update(
        json.dumps(
            [str(column) for column in timeseries.columns],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(
        pd.util.hash_pandas_object(timeseries, index=True)
        .to_numpy(dtype=np.uint64, copy=False)
        .tobytes()
    )
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checkpoint_structures_equivalent(left: Any, right: Any) -> bool:
    """Compare checkpoint configurations while tolerating CSV float roundoff."""

    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _checkpoint_structures_equivalent(left[key], right[key])
            for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _checkpoint_structures_equivalent(a, b)
            for a, b in zip(left, right)
        )
    if (
        isinstance(left, (int, float, np.integer, np.floating))
        and not isinstance(left, (bool, np.bool_))
        and isinstance(right, (int, float, np.integer, np.floating))
        and not isinstance(right, (bool, np.bool_))
    ):
        return bool(np.isclose(float(left), float(right), rtol=1e-10, atol=1e-8))
    return bool(left == right)


_FIXED_PLAN_RC_CHECKPOINT_SCHEMA = "heatplan.fixed_investment_checkpoint.v2"
_FIXED_PLAN_RC_CHECKPOINT_FILES = {
    "planning_building_trajectory": "representative_planning/building_trajectory.csv",
    "planning_building_comfort": "representative_planning/building_comfort.csv",
    "planning_building_independent_audit": "representative_planning/building_independent_audit.json",
    "planning_building_input_identity": "representative_planning/building_input_identity.json",
}


def _checkpoint_digest_valid(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def _checkpoint_require_finite(value: Any, label: str) -> None:
    """Never replace non-finite model inputs or physical evidence with null."""
    if isinstance(value, dict):
        for key, item in value.items():
            _checkpoint_require_finite(item, f"{label}.{key}")
    elif isinstance(value, (list, tuple, np.ndarray)):
        for index, item in enumerate(value):
            _checkpoint_require_finite(item, f"{label}[{index}]")
    elif isinstance(value, (float, np.floating)) and not np.isfinite(value):
        raise ValueError(f"检查点存在非有限数: {label}")


def _checkpoint_json_text(value: Any, *, allow_lp_gap: bool = False) -> str:
    def clean(item: Any, path: tuple = ()) -> Any:
        if isinstance(item, dict):
            return {key: clean(val, path + (key,)) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(val, path + (index,)) for index, val in enumerate(item)]
        if isinstance(item, np.generic):
            item = item.item()
        if isinstance(item, float) and not np.isfinite(item):
            if allow_lp_gap and path == ("mip_gap",) and np.isnan(item):
                return None
            raise ValueError(f"检查点JSON存在非有限数: {path}")
        return item
    return json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False)


def _checkpoint_read_strict_json(path: Path) -> dict:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"检查点JSON包含非标准常量: {value}")
    result = json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError(f"检查点JSON必须是对象: {path}")
    return result


def _checkpoint_check_comfort(comfort: pd.DataFrame, report: dict) -> None:
    required = {"building", "boundary_group", "boundary_mode", "heat_kwh", "cooling_kwh",
                "trajectory_hours", "initial_to_final_inventory_change_kwh", "building_physics_passed",
                "no_simultaneous_heat_cooling_passed", "max_rc_residual_c", "terminal_violation_c"}
    if not isinstance(comfort, pd.DataFrame) or not comfort.columns.is_unique or required - set(comfort):
        raise ValueError("检查点缺少完整楼宇舒适表")
    expected = {(g["building"], g["boundary_group"]): g for g in report["building_groups"]}
    observed = list(zip(comfort.building.astype(str), comfort.boundary_group.astype(str)))
    if len(observed) != len(expected) or len(set(observed)) != len(observed) or set(observed) != set(expected):
        raise ValueError("检查点楼宇舒适表分组不完整或重复")
    for row, key in zip(comfort.to_dict("records"), observed):
        group = expected[key]
        if row["boundary_mode"] != group["mode"]:
            raise ValueError("检查点楼宇舒适边界模式不一致")
        for flag in ("building_physics_passed", "no_simultaneous_heat_cooling_passed"):
            value = row[flag]
            valid = value is True or isinstance(value, np.bool_) and bool(value)
            if not valid:
                raise ValueError("检查点楼宇舒适通过标志无效")
        for field, target in (("heat_kwh", "heat_kwh"), ("cooling_kwh", "cooling_kwh"),
                              ("trajectory_hours", "trajectory_hours"),
                              ("initial_to_final_inventory_change_kwh", "inventory_change_kwh")):
            actual = float(row[field])
            _checkpoint_require_finite(actual, field)
            if abs(actual - group[target]) > 1e-5:
                raise ValueError(f"检查点楼宇舒适表与独立重算不一致: {field}")
        for field in ("max_rc_residual_c", "terminal_violation_c"):
            actual = float(row[field])
            _checkpoint_require_finite(actual, field)
            if not 0 <= actual <= 1e-6:
                raise ValueError(f"检查点楼宇舒适残差无效: {field}")


def save_fixed_investment_plan_checkpoint(
    checkpoint_dir: str | Path,
    fixed_plan: FixedInvestmentPlanArtifacts,
    *,
    source_config: dict[str, Any],
    representative_timeseries: pd.DataFrame,
) -> Path:
    """Write legacy v1 or RC v2 with mandatory, hashed replayable evidence."""
    from .building_audit import audit_building_trajectory, building_audit_identity
    from .model import HeatPlanningModel

    _checkpoint_require_finite(source_config, "source_config")
    _checkpoint_require_finite(fixed_plan.fixed_config, "fixed_config")
    _checkpoint_require_finite(representative_timeseries.to_numpy(dtype=object).tolist(), "representative_timeseries")
    if int(fixed_plan.planning.summary.get("solver_status", -1)) != 0:
        raise ValueError("只允许保存状态0的固定投资规划检查点")
    profile = fixed_plan.planning_model_profile
    allow_lp_gap = int(profile.get("integer_variables", -1)) == 0
    summary_text = _checkpoint_json_text(fixed_plan.planning.summary, allow_lp_gap=allow_lp_gap)
    profile_text = _checkpoint_json_text(profile)
    rc_enabled = source_config.get("building_thermal", {}).get("enabled", False) is True
    fixed_rc = fixed_plan.fixed_config.get("building_thermal", {}).get("enabled", False) is True
    if rc_enabled != fixed_rc:
        raise ValueError("规划与冻结配置的楼宇模式不一致")
    files = dict(_FIXED_PLAN_CHECKPOINT_FILES)
    rc_report = identity = None
    if rc_enabled:
        files.update(_FIXED_PLAN_RC_CHECKPOINT_FILES)
        saved_report = getattr(fixed_plan.planning, "building_independent_audit", None)
        identity = getattr(fixed_plan.planning, "building_input_identity", None)
        if not isinstance(saved_report, dict) or not isinstance(identity, dict) or saved_report.get("passed") is not True:
            raise ValueError("RC检查点缺少完整独立审计与求解前输入身份")
        input_model = HeatPlanningModel(source_config, representative_timeseries)
        current_identity = building_audit_identity(input_model.config, input_model.ts)
        if identity != current_identity or saved_report.get("input_identity") != identity:
            raise ValueError("RC检查点求解前输入身份与当前真实输入不一致")
        rc_report = audit_building_trajectory(input_model.config, input_model.ts,
            fixed_plan.planning.building_trajectory, dispatch=fixed_plan.planning.dispatch,
            summary=fixed_plan.planning.summary, expected_input_identity=identity)
        if rc_report["passed"] is not True or not _checkpoint_structures_equivalent(saved_report, rc_report):
            raise ValueError("RC检查点独立重算失败或保存报告与重算不一致")
        _checkpoint_check_comfort(fixed_plan.planning.building_comfort, rc_report)
    root = Path(checkpoint_dir).resolve()
    planning_dir = root / "representative_planning"
    planning_dir.mkdir(parents=True, exist_ok=True)
    (planning_dir / "summary.json").write_text(summary_text, encoding="utf-8")
    fixed_plan.planning.investments.to_csv(planning_dir / "investments.csv", index=False)
    fixed_plan.planning.dispatch.to_csv(planning_dir / "dispatch.csv", index=False)
    (root / "fixed_investment_config.yaml").write_text(
        yaml.safe_dump(fixed_plan.fixed_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    fixed_plan.investment_freeze_audit.to_csv(root / "investment_freeze_audit.csv", index=False)
    (root / "planning_model_profile.json").write_text(profile_text, encoding="utf-8")
    if rc_enabled:
        fixed_plan.planning.building_trajectory.to_csv(planning_dir / "building_trajectory.csv", index=False)
        fixed_plan.planning.building_comfort.to_csv(planning_dir / "building_comfort.csv", index=False)
        (planning_dir / "building_independent_audit.json").write_text(_checkpoint_json_text(rc_report), encoding="utf-8")
        (planning_dir / "building_input_identity.json").write_text(_checkpoint_json_text(identity), encoding="utf-8")
    manifest = {
        "schema": _FIXED_PLAN_RC_CHECKPOINT_SCHEMA if rc_enabled else _FIXED_PLAN_CHECKPOINT_SCHEMA,
        "building_thermal_enabled": rc_enabled,
        "source_config_sha256": _canonical_config_fingerprint(source_config),
        "representative_timeseries_sha256": _timeseries_fingerprint(representative_timeseries),
        "representative_rows": int(len(representative_timeseries)),
        "solver_status": int(fixed_plan.planning.summary["solver_status"]),
        "files": {label: {"path": relative_path, "sha256": _file_sha256(root / relative_path)}
                  for label, relative_path in files.items()},
    }
    manifest_path = root / "fixed_plan_checkpoint_manifest.json"
    manifest_path.write_text(_checkpoint_json_text(manifest), encoding="utf-8")
    return manifest_path


def load_fixed_investment_plan_checkpoint(
    checkpoint_dir: str | Path,
    *,
    expected_config: dict[str, Any] | None = None,
    expected_representative_timeseries: pd.DataFrame | None = None,
    require_manifest: bool = True,
) -> FixedInvestmentPlanArtifacts:
    """Recover RC only with real trusted inputs and a fresh C6 recomputation."""
    from .experiments import freeze_investment_plan
    from .model import HeatPlanningModel, SolveArtifacts

    root = Path(checkpoint_dir).resolve()
    manifest_path = root / "fixed_plan_checkpoint_manifest.json"
    if require_manifest and not manifest_path.is_file():
        raise ValueError(f"固定投资检查点缺少清单文件: {manifest_path}")
    has_manifest = manifest_path.is_file()
    manifest = _checkpoint_read_strict_json(manifest_path) if has_manifest else {}
    schema = manifest.get("schema")
    if has_manifest and schema not in {_FIXED_PLAN_CHECKPOINT_SCHEMA, _FIXED_PLAN_RC_CHECKPOINT_SCHEMA}:
        raise ValueError(f"不支持的固定投资检查点格式: {schema!r}")
    if has_manifest:
        if not isinstance(manifest.get("files"), dict):
            raise ValueError("检查点清单缺少必需files对象")
        for key in ("source_config_sha256", "representative_timeseries_sha256"):
            if not _checkpoint_digest_valid(manifest.get(key)):
                raise ValueError(f"检查点缺少必需输入指纹: {key}")
    files = dict(_FIXED_PLAN_CHECKPOINT_FILES)
    if schema == _FIXED_PLAN_RC_CHECKPOINT_SCHEMA:
        if manifest.get("building_thermal_enabled") is not True:
            raise ValueError("RC新版检查点必须显式标记楼宇模式")
        files.update(_FIXED_PLAN_RC_CHECKPOINT_FILES)
    resolved_files = {}
    for label, default_path in files.items():
        entry = manifest.get("files", {}).get(label)
        if has_manifest and (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                         or not entry["path"] or not _checkpoint_digest_valid(entry.get("sha256"))):
            raise ValueError(f"检查点缺少必需文件哈希条目: {label}")
        relative_path = entry["path"] if entry else default_path
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError(f"检查点文件越出根目录: {relative_path}") from error
        if not candidate.is_file():
            raise ValueError(f"固定投资检查点缺少文件: {candidate}")
        if entry and _file_sha256(candidate) != entry["sha256"].lower():
            raise ValueError(f"固定投资检查点文件校验失败: {candidate}")
        resolved_files[label] = candidate
    fixed_config = yaml.safe_load(resolved_files["fixed_config"].read_text(encoding="utf-8"))
    _checkpoint_require_finite(fixed_config, "fixed_config")
    rc_enabled = bool(fixed_config.get("building_thermal", {}).get("enabled", False))
    expected_rc = bool((expected_config or {}).get("building_thermal", {}).get("enabled", False))
    if rc_enabled or expected_rc or schema == _FIXED_PLAN_RC_CHECKPOINT_SCHEMA:
        if not rc_enabled or not expected_rc or schema != _FIXED_PLAN_RC_CHECKPOINT_SCHEMA:
            raise ValueError("RC检查点缺少新版证据或真实启用RC的配置")
        if expected_config is None or expected_representative_timeseries is None:
            raise ValueError("恢复RC检查点必须提供真实配置与代表时序")
    # Legacy v1 LP JSON may contain the historical NaN mip_gap. It remains
    # readable; new writers emit null and every RC v2 JSON is strict.
    summary = (_checkpoint_read_strict_json(resolved_files["planning_summary"]) if rc_enabled else
               json.loads(resolved_files["planning_summary"].read_text(encoding="utf-8")))
    if int(summary.get("solver_status", -1)) != 0:
        raise ValueError("固定投资检查点中的规划解不是状态0可行解")
    investments = pd.read_csv(resolved_files["planning_investments"])
    dispatch = pd.read_csv(resolved_files["planning_dispatch"], dtype={
        "category": str, "asset": str, "node": str, "grade": str, "scenario": str, "boundary_group": str})
    freeze_audit = pd.read_csv(resolved_files["investment_freeze_audit"])
    profile = _checkpoint_read_strict_json(resolved_files["planning_model_profile"])
    if not {"category", "asset", "value", "unit"}.issubset(investments):
        raise ValueError("固定投资检查点的投资表结构无效")
    if "freeze_ok" not in freeze_audit or freeze_audit.empty:
        raise ValueError("固定投资检查点缺少有效投资冻结审计")
    freeze_ok = freeze_audit.freeze_ok
    if freeze_ok.dtype != bool:
        freeze_ok = freeze_ok.astype(str).str.lower().map({"true": True, "false": False})
    if freeze_ok.isna().any() or not bool(freeze_ok.all()):
        raise ValueError("固定投资检查点未通过全部投资冻结审计")
    required_profile = {"time_steps", "variables", "integer_variables", "constraints", "build_seconds", "solve_seconds"}
    if required_profile - set(profile):
        raise ValueError(f"固定投资检查点模型画像缺字段: {sorted(required_profile - set(profile))}")
    planning = SolveArtifacts(result=None, values=np.array([], dtype=float), summary=summary,
                              investments=investments, dispatch=dispatch)
    if expected_config is not None:
        _checkpoint_require_finite(expected_config, "expected_config")
        fingerprint = manifest.get("source_config_sha256")
        if fingerprint and fingerprint != _canonical_config_fingerprint(expected_config):
            raise ValueError("固定投资检查点与当前规划配置不匹配")
        regenerated = freeze_investment_plan(expected_config, planning, relax_reliability=False)
        if not _checkpoint_structures_equivalent(regenerated, fixed_config):
            raise ValueError("固定投资检查点冻结配置无法由当前投资表重建")
        audit = investment_freeze_audit(expected_config, fixed_config, planning)
        if audit.empty or not bool(audit.freeze_ok.all()):
            raise ValueError("固定投资检查点未通过当前配置冻结语义复核")
    if expected_representative_timeseries is not None:
        _checkpoint_require_finite(expected_representative_timeseries.to_numpy(dtype=object).tolist(), "expected_timeseries")
        if int(profile["time_steps"]) != len(expected_representative_timeseries):
            raise ValueError("固定投资检查点与当前代表时序行数不匹配")
        fingerprint = manifest.get("representative_timeseries_sha256")
        if fingerprint and fingerprint != _timeseries_fingerprint(expected_representative_timeseries):
            raise ValueError("固定投资检查点与当前代表时序不匹配")
        if "scenario" in expected_representative_timeseries:
            if set(expected_representative_timeseries.scenario.astype(str)) != set(dispatch.scenario.astype(str)):
                raise ValueError("固定投资检查点与当前代表情景集合不匹配")
    if rc_enabled:
        identity = _checkpoint_read_strict_json(resolved_files["planning_building_input_identity"])
        if set(identity) != {"config_sha256", "frame_sha256"} or not all(_checkpoint_digest_valid(v) for v in identity.values()):
            raise ValueError("RC检查点求解前输入身份无效")
        stored_report = _checkpoint_read_strict_json(resolved_files["planning_building_independent_audit"])
        if stored_report.get("passed") is not True or stored_report.get("input_identity") != identity:
            raise ValueError("RC检查点完整审计报告与输入身份不一致")
        planning.building_trajectory = pd.read_csv(resolved_files["planning_building_trajectory"], dtype={
            "building": str, "node": str, "heat_grade": str, "scenario": str, "boundary_group": str, "boundary_mode": str})
        planning.building_comfort = pd.read_csv(resolved_files["planning_building_comfort"], dtype={
            "building": str, "scenario": str, "boundary_group": str, "boundary_mode": str})
        input_model = HeatPlanningModel(expected_config, expected_representative_timeseries)
        fresh_report = attach_independent_building_audit(input_model, planning, identity, continuous=False)
        if fresh_report["passed"] is not True or not _checkpoint_structures_equivalent(stored_report, fresh_report):
            raise ValueError("恢复RC检查点的独立重算失败或完整报告不一致")
        _checkpoint_check_comfort(planning.building_comfort, fresh_report)
    return FixedInvestmentPlanArtifacts(planning=planning, fixed_config=fixed_config,
                                         investment_freeze_audit=freeze_audit, planning_model_profile=profile)


class AnnualReplayReadinessError(ValueError):
    """Fixed plan fails a cheap, structured check before annual MILP build."""

    def __init__(
        self,
        message: str,
        *,
        planning: Any,
        fixed_config: dict[str, Any],
        chronological_timeseries: pd.DataFrame,
        readiness_audit: dict[str, Any],
        investment_freeze_audit: pd.DataFrame,
        planning_model_profile: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.planning = planning
        self.fixed_config = fixed_config
        self.chronological_timeseries = chronological_timeseries
        self.readiness_audit = readiness_audit
        self.investment_freeze_audit = investment_freeze_audit
        self.planning_model_profile = planning_model_profile


def chronology_required_columns(
    config: dict[str, Any],
    timestamp_column: str = "timestamp",
) -> list[str]:
    """Return time-series columns referenced by one planning configuration."""

    columns: set[str] = {
        timestamp_column,
        str(
            config["planning"].get(
                "electricity_price_column", "electricity_price"
            )
        ),
        "grid_emission_factor",
    }
    for node in config["nodes"]:
        columns.update(str(value) for value in node.get("demand_columns", {}).values())
        for value in (
            node.get("intermittent_heating", {}).get("schedule_column"),
            node.get("user_behavior", {}).get("multiplier_column"),
            node.get("user_behavior", {}).get(
                "scenario_multiplier_weight_column"
            ),
        ):
            if value:
                columns.add(str(value))
    for source in config["sources"]:
        for field in (
            "availability_column",
            "source_temp_column",
            "minimum_electricity_output_column",
            "relative_humidity_column",
        ):
            if source.get(field):
                columns.add(str(source[field]))
    if any(
        float(pipe.get("cold_loss_sensitivity_per_c", 0.0)) != 0.0
        for pipe in config["pipes"]
    ):
        columns.add("outdoor_temp")
    renewable = config.get("renewable_electricity", {})
    if renewable.get("availability_column"):
        columns.add(str(renewable["availability_column"]))
    grid = config.get("grid_connection", {})
    if grid.get("availability_column"):
        columns.add(str(grid["availability_column"]))
    microgrid = config.get("microgrid_islanding", {})
    if microgrid.get("renewable_availability_column"):
        columns.add(str(microgrid["renewable_availability_column"]))
    for chain in config.get("biomass_supply_chains", []):
        if chain.get("delivery_availability_column"):
            columns.add(str(chain["delivery_availability_column"]))
    cooling = config.get("multi_energy_services", {}).get("cooling", {})
    columns.update(str(value) for value in cooling.get("demand_columns", {}).values())
    thermal = config.get("building_thermal",{})
    if thermal.get("enabled",False):
        columns.add(str(thermal.get("outdoor_temperature_column","outdoor_temp")))
        for spec in [thermal,*thermal.get("buildings",[])]:
            for key in ("internal_gain_column","solar_gain_column","min_temperature_column","max_temperature_column","heating_service_column"):
                if spec.get(key):
                    columns.add(str(spec[key]))
    return sorted(columns)


def chronology_quality_audit(
    frame: pd.DataFrame,
    *,
    timestamp_column: str = "timestamp",
    expected_step_hours: float = 1.0,
    required_columns: list[str] | None = None,
    nonnegative_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Audit continuity, completeness and numerical quality before solving.

    A formal full-year horizon may begin on any date, but it must contain a
    continuous 8760 or 8784 hours.  Calendar-year alignment is reported
    separately because heating-year data often run from summer to summer.
    """

    expected_step = float(expected_step_hours)
    if not np.isfinite(expected_step) or expected_step <= 0.0:
        raise ValueError("expected_step_hours 必须为正的有限数值")
    required = list(dict.fromkeys(required_columns or [timestamp_column]))
    if timestamp_column not in required:
        required.insert(0, timestamp_column)
    missing_columns = sorted(set(required) - set(frame.columns))
    issues: list[str] = []
    if missing_columns:
        issues.append("missing_required_columns")

    report: dict[str, Any] = {
        "rows": int(len(frame)),
        "timestamp_column": timestamp_column,
        "expected_step_hours": expected_step,
        "required_column_count": len(required),
        "missing_required_columns": missing_columns,
        "invalid_timestamp_count": 0,
        "duplicate_timestamp_count": 0,
        "irregular_interval_count": 0,
        "missing_required_value_count": 0,
        "infinite_required_value_count": 0,
        "negative_nonnegative_value_count": 0,
        "start_timestamp": None,
        "end_timestamp": None,
        "observed_step_hours": None,
        "horizon_hours": 0.0,
        "is_full_year_horizon": False,
        "is_calendar_year_aligned": False,
        "chronology_valid": False,
        "issues": issues,
    }
    if timestamp_column not in frame.columns:
        report["invalid_timestamp_count"] = int(len(frame))
        issues.append("timestamp_column_missing")
        return report

    parsed = pd.to_datetime(frame[timestamp_column], errors="coerce")
    invalid_timestamp_count = int(parsed.isna().sum())
    report["invalid_timestamp_count"] = invalid_timestamp_count
    if invalid_timestamp_count:
        issues.append("invalid_timestamps")
    valid_timestamps = parsed.dropna().sort_values().reset_index(drop=True)
    duplicate_count = int(valid_timestamps.duplicated().sum())
    report["duplicate_timestamp_count"] = duplicate_count
    if duplicate_count:
        issues.append("duplicate_timestamps")

    differences = (
        valid_timestamps.diff().dropna().dt.total_seconds().to_numpy()
        / 3600.0
    )
    irregular_count = int(
        np.count_nonzero(~np.isclose(differences, expected_step, atol=1e-9))
    )
    report["irregular_interval_count"] = irregular_count
    if irregular_count:
        issues.append("irregular_or_missing_intervals")
    if len(differences):
        report["observed_step_hours"] = float(np.median(differences))
    elif len(valid_timestamps) == 1:
        report["observed_step_hours"] = expected_step

    if len(valid_timestamps):
        start = valid_timestamps.iloc[0]
        end = valid_timestamps.iloc[-1]
        horizon_hours = len(valid_timestamps) * expected_step
        report["start_timestamp"] = start.isoformat()
        report["end_timestamp"] = end.isoformat()
        report["horizon_hours"] = float(horizon_hours)
        report["is_full_year_horizon"] = bool(
            np.isclose(horizon_hours, 8760.0, atol=1e-6)
            or np.isclose(horizon_hours, 8784.0, atol=1e-6)
        )
        expected_start = pd.Timestamp(year=start.year, month=1, day=1)
        expected_end = pd.Timestamp(year=start.year + 1, month=1, day=1)
        if start.tzinfo is not None:
            expected_start = expected_start.tz_localize(start.tzinfo)
            expected_end = expected_end.tz_localize(start.tzinfo)
        report["is_calendar_year_aligned"] = bool(
            start == expected_start
            and end + pd.Timedelta(hours=expected_step) == expected_end
        )

    present_required = [column for column in required if column in frame.columns]
    if present_required:
        missing_value_count = int(frame[present_required].isna().sum().sum())
    else:
        missing_value_count = 0
    report["missing_required_value_count"] = missing_value_count
    if missing_value_count:
        issues.append("missing_required_values")

    numeric_required = [
        column
        for column in present_required
        if column != timestamp_column
        and pd.api.types.is_numeric_dtype(frame[column])
    ]
    infinite_count = int(
        sum(
            np.count_nonzero(~np.isfinite(frame[column].to_numpy(dtype=float)))
            for column in numeric_required
        )
    )
    report["infinite_required_value_count"] = infinite_count
    if infinite_count:
        issues.append("non_finite_required_values")

    negative_count = 0
    for column in nonnegative_columns or []:
        if column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            negative_count += int((values < -1e-12).sum())
    report["negative_nonnegative_value_count"] = negative_count
    if negative_count:
        issues.append("negative_demand_values")

    report["chronology_valid"] = bool(
        not missing_columns
        and invalid_timestamp_count == 0
        and duplicate_count == 0
        and irregular_count == 0
        and missing_value_count == 0
        and infinite_count == 0
        and negative_count == 0
        and len(frame) > 1
    )
    return report


def _representative_row_weights(
    config: dict[str, Any], frame: pd.DataFrame
) -> np.ndarray:
    scenarios = (
        frame["scenario"].astype(str)
        if "scenario" in frame.columns
        else pd.Series("base", index=frame.index)
    )
    unique_scenarios = list(dict.fromkeys(scenarios.tolist()))
    if "scenario_probability" in frame.columns:
        probabilities = frame["scenario_probability"].astype(float).to_numpy()
    else:
        probability = 1.0 / max(len(unique_scenarios), 1)
        probabilities = np.full(len(frame), probability, dtype=float)
    if "period_weight" in frame.columns:
        period_weights = frame["period_weight"].astype(float).to_numpy()
    else:
        period_weights = np.full(
            len(frame),
            float(config["planning"].get("representative_day_weight", 1.0)),
            dtype=float,
        )
    return (
        float(config["planning"].get("time_step_hours", 1.0))
        * probabilities
        * period_weights
    )


def _maximum_grouped_ramp(frame: pd.DataFrame, column: str) -> float:
    if len(frame) < 2:
        return 0.0
    group_column = (
        "boundary_group"
        if "boundary_group" in frame.columns
        else "scenario"
        if "scenario" in frame.columns
        else None
    )
    if group_column is None:
        return float(frame[column].astype(float).diff().abs().max())
    maxima = [
        block[column].astype(float).diff().abs().max()
        for _, block in frame.groupby(group_column, sort=False)
        if len(block) > 1
    ]
    finite = [float(value) for value in maxima if pd.notna(value)]
    return max(finite, default=0.0)


def representative_period_coverage_audit(
    config: dict[str, Any],
    representative: pd.DataFrame,
    chronology: pd.DataFrame,
) -> pd.DataFrame:
    """Compare raw input energy, peaks, extremes and ramps across horizons."""

    series: dict[str, str] = {}
    demand_columns: list[str] = []
    for node in config["nodes"]:
        for column in node.get("demand_columns", {}).values():
            demand_columns.append(str(column))
            series[str(column)] = "demand"
    cooling = config.get("multi_energy_services", {}).get("cooling", {})
    cooling_columns = {str(column) for column in cooling.get("demand_columns", {}).values()} if cooling.get("enabled", False) else set()
    for column in cooling_columns:
        demand_columns.append(column)
        series[column] = "demand"
    thermal = config.get("building_thermal", {})
    reference_cooling_columns = set()
    if thermal.get("enabled", False):
        representative, chronology = representative.copy(), chronology.copy()
        buildings = thermal["buildings"]
        covered = {(str(b["node"]), str(b["heat_grade"])) for b in buildings}
        for node in config["nodes"]:
            for grade, column in node.get("demand_columns", {}).items():
                if (str(node["name"]), str(grade)) in covered:
                    series[str(column)] = "legacy_reference"
        for column in cooling_columns:
            series[column] = "legacy_reference"
        outdoor_column = str(thermal.get("outdoor_temperature_column", "outdoor_temp"))
        series[outdoor_column] = "temperature"
        for building in buildings:
            for key in ("internal_gain_column", "solar_gain_column", "min_temperature_column", "max_temperature_column"):
                if building.get(key):
                    series[str(building[key])] = "building_driver"
            name = str(building["name"])
            hot, cold = f"rc_steady_heat_reference_{name}_kw", f"rc_steady_cooling_reference_{name}_kw"
            series[hot] = series[cold] = "demand"
            reference_cooling_columns.add(cold)
            for frame in (representative, chronology):
                def values(column_key, scalar_key, default):
                    return frame[building[column_key]].astype(float) if building.get(column_key) else float(building.get(scalar_key, default))
                minimum = values("min_temperature_column", "min_temperature_c", 18)
                maximum = values("max_temperature_column", "max_temperature_c", 26)
                gains = values("internal_gain_column", "internal_gain_kw", 0) + values("solar_gain_column", "solar_gain_kw", 0)
                h = float(building["heat_loss_kw_per_c"])
                frame[hot] = (h*(minimum-frame[outdoor_column])-gains).clip(lower=0)
                frame[cold] = (h*(frame[outdoor_column]-maximum)+gains).clip(lower=0)
    for source in config["sources"]:
        if source.get("source_temp_column"):
            series[str(source["source_temp_column"])] = "temperature"
        if source.get("availability_column"):
            series[str(source["availability_column"])] = "availability"
    if "outdoor_temp" in representative.columns and "outdoor_temp" in chronology.columns:
        series["outdoor_temp"] = "temperature"
    for column in (
        str(
            config["planning"].get(
                "electricity_price_column", "electricity_price"
            )
        ),
        "grid_emission_factor",
    ):
        series.setdefault(column, "market_or_emission")

    representative_weights = _representative_row_weights(config, representative)
    chronology_weight = float(
        config["planning"].get("time_step_hours", 1.0)
    )
    rows: list[dict[str, Any]] = []
    for column, kind in series.items():
        if column not in representative.columns or column not in chronology.columns:
            continue
        rep = representative[column].astype(float)
        annual = chronology[column].astype(float)
        representative_weighted_sum = float(
            np.dot(rep.to_numpy(), representative_weights)
        )
        representative_weight_sum = float(representative_weights.sum())
        rep_mean = representative_weighted_sum / max(
            representative_weight_sum, 1e-12
        )
        annual_mean = float(annual.mean())
        mean_bias = (
            (rep_mean - annual_mean) / abs(annual_mean)
            if abs(annual_mean) > 1e-12
            else np.nan
        )
        rep_total = (
            representative_weighted_sum if kind == "demand" else np.nan
        )
        annual_total = (
            float(annual.sum() * chronology_weight)
            if kind == "demand"
            else np.nan
        )
        rep_ramp = _maximum_grouped_ramp(representative, column)
        annual_ramp = _maximum_grouped_ramp(chronology, column)
        peak_capture = (
            float(rep.max()) / float(annual.max())
            if kind == "demand" and float(annual.max()) > 1e-12
            else np.nan
        )
        total_bias = (
            (rep_total - annual_total) / abs(annual_total)
            if kind == "demand" and abs(annual_total) > 1e-12
            else np.nan
        )
        ramp_capture = (
            rep_ramp / annual_ramp if annual_ramp > 1e-12 else np.nan
        )
        findings: list[str] = []
        if kind == "demand":
            if np.isfinite(peak_capture) and peak_capture < 0.99:
                findings.append("demand_peak_under_captured")
            if np.isfinite(total_bias) and abs(total_bias) > 0.10:
                findings.append("annual_energy_bias_over_10pct")
            if np.isfinite(ramp_capture) and ramp_capture < 0.90:
                findings.append("maximum_ramp_under_captured")
        elif kind == "temperature":
            if float(rep.min()) > float(annual.min()) + 0.1:
                findings.append("cold_extreme_under_captured")
            if (cooling.get("enabled", False) or thermal.get("enabled", False)) and float(rep.max()) < float(annual.max()) - 0.1:
                findings.append("hot_extreme_under_captured")
        elif kind == "building_driver":
            if float(rep.min()) > float(annual.min()) + 1e-9:
                findings.append("minimum_building_driver_under_captured")
            if float(rep.max()) < float(annual.max()) - 1e-9:
                findings.append("maximum_building_driver_under_captured")
        elif kind == "availability" and float(rep.min()) > float(annual.min()) + 1e-9:
            findings.append("minimum_availability_under_captured")
        elif kind == "market_or_emission":
            if float(rep.max()) < float(annual.max()) - 1e-9:
                findings.append("maximum_market_or_emission_under_captured")
            if float(rep.min()) > float(annual.min()) + 1e-9:
                findings.append("minimum_market_or_emission_under_captured")
        rows.append(
            {
                "series_kind": kind,
                "service": "cooling" if column in cooling_columns | reference_cooling_columns else ("heating" if kind == "demand" else "shared"),
                "column": column,
                "representative_min": float(rep.min()),
                "chronology_min": float(annual.min()),
                "minimum_gap": float(rep.min() - annual.min()),
                "representative_max": float(rep.max()),
                "chronology_max": float(annual.max()),
                "maximum_gap": float(rep.max() - annual.max()),
                "representative_weighted_total": rep_total,
                "chronology_total": annual_total,
                "total_relative_bias": total_bias,
                "representative_weighted_mean": rep_mean,
                "chronology_mean": annual_mean,
                "mean_relative_bias": mean_bias,
                "representative_max_abs_ramp": rep_ramp,
                "chronology_max_abs_ramp": annual_ramp,
                "ramp_capture_ratio": ramp_capture,
                "peak_capture_ratio": peak_capture,
                "coverage_status": "pass" if not findings else "review",
                "findings": ";".join(findings),
                "audit_scope": "RC_steady_reference_not_actual_delivered_or_dynamic_certificate" if column.startswith("rc_steady_") else ("legacy_proxy_not_RC_service" if kind == "legacy_reference" else "raw_input_before_behavior_and_schedule_modifiers"),
            }
        )
    return pd.DataFrame(rows)


def representative_coverage_recommendations(
    audit: pd.DataFrame,
) -> pd.DataFrame:
    """Translate coverage findings into non-mutating modeling suggestions.

    Recommendations are deliberately diagnostic.  The function never edits
    representative weights because an energy correction can conflict with the
    number of represented calendar days or uncertainty-scenario probabilities.
    """

    rows: list[dict[str, Any]] = []
    for record in audit.to_dict(orient="records"):
        finding_value = record.get("findings", "")
        if finding_value is None or pd.isna(finding_value):
            continue
        findings = set(filter(None, str(finding_value).split(";")))
        if not findings:
            continue
        actions: list[str] = []
        if "demand_peak_under_captured" in findings:
            actions.append("add_or_force_maximum_demand_day")
        if "maximum_ramp_under_captured" in findings:
            actions.append("add_or_force_maximum_ramp_day")
        if "cold_extreme_under_captured" in findings:
            actions.append("add_or_force_minimum_temperature_day")
        if "hot_extreme_under_captured" in findings:
            actions.append("add_or_force_maximum_temperature_day")
        if {"minimum_building_driver_under_captured", "maximum_building_driver_under_captured"} & findings:
            actions.append("add_or_force_building_driver_extreme_day")
        if "minimum_availability_under_captured" in findings:
            actions.append("add_or_force_minimum_resource_day")
        if "maximum_market_or_emission_under_captured" in findings:
            actions.append("add_high_price_or_high_emission_period")
        if "minimum_market_or_emission_under_captured" in findings:
            actions.append("add_low_price_or_low_emission_period")
        if "annual_energy_bias_over_10pct" in findings:
            actions.append("recalibrate_weights_or_add_seasonal_day")
        representative_total = record.get("representative_weighted_total")
        chronology_total = record.get("chronology_total")
        multiplier = np.nan
        if (
            record.get("series_kind") == "demand"
            and representative_total is not None
            and chronology_total is not None
            and np.isfinite(float(representative_total))
            and np.isfinite(float(chronology_total))
            and abs(float(representative_total)) > 1e-12
        ):
            multiplier = float(chronology_total) / float(representative_total)
        rows.append(
            {
                "series_kind": record.get("series_kind"),
                "column": record.get("column"),
                "findings": record.get("findings", ""),
                "suggested_energy_weight_multiplier": multiplier,
                "recommended_actions": ";".join(actions),
                "automatic_application_allowed": False,
                "caveat": (
                    "diagnostic_only; preserve total represented days, scenario "
                    "probabilities and extreme anchors when recalibrating"
                ),
            }
        )
    columns = [
        "series_kind",
        "column",
        "findings",
        "suggested_energy_weight_multiplier",
        "recommended_actions",
        "automatic_application_allowed",
        "caveat",
    ]
    return pd.DataFrame(rows, columns=columns)


def model_structure_profile(
    model: Any,
    *,
    build_seconds: float | None = None,
    solve_seconds: float | None = None,
) -> dict[str, Any]:
    """Return sparse-model structure and transparent memory proxies."""

    problem = model.problem
    variables = len(problem.names)
    integer_variables = int(sum(problem.integrality))
    constraints = len(problem.rows)
    nonzeros = int(sum(len(row) for row in problem.rows))
    # CSR matrix: float64 values + int32 column indices + int32 row pointers.
    csr_bytes = nonzeros * (8 + 4) + (constraints + 1) * 4
    # Solver vectors include objective, two variable bounds, integrality and
    # two row bounds.  This excludes solver-internal presolve/cut memory.
    vector_bytes = variables * (8 * 3 + 4) + constraints * (8 * 2)
    # Python dict/list assembly is much heavier than the final CSR matrix.  The
    # coefficient is an intentionally conservative engineering proxy, not a
    # process-RSS guarantee.
    python_build_proxy_bytes = (
        nonzeros * 72 + variables * 256 + constraints * 192
    )
    profile = {
        "time_steps": int(len(model.times)),
        "variables": int(variables),
        "continuous_variables": int(variables - integer_variables),
        "integer_variables": integer_variables,
        "constraints": int(constraints),
        "matrix_nonzeros": nonzeros,
        "matrix_density": (
            nonzeros / max(variables * constraints, 1)
        ),
        "estimated_csr_and_vector_memory_mb": (
            csr_bytes + vector_bytes
        )
        / (1024.0**2),
        "conservative_python_build_memory_proxy_mb": python_build_proxy_bytes
        / (1024.0**2),
        "memory_scope": (
            "matrix/vector and Python-build proxies only; excludes solver "
            "presolve, branch-and-bound tree, cuts and operating-system overhead"
        ),
    }
    if build_seconds is not None:
        profile["build_seconds"] = float(build_seconds)
    if solve_seconds is not None:
        profile["solve_seconds"] = float(solve_seconds)
    if hasattr(problem, "last_matrix_compile_seconds"):
        profile["matrix_compile_seconds"] = float(
            problem.last_matrix_compile_seconds
        )
        profile["matrix_compile_cache_hit"] = bool(
            problem.last_matrix_cache_hit
        )
    return profile


def adapt_config_to_chronology_scenarios(
    config: dict[str, Any],
    chronology: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map planning-only scenario filters onto one realized chronology.

    A fixed-investment replay commonly has one observed scenario named
    ``full_year`` even when planning policy constraints targeted ``base`` or a
    subset of stochastic design scenarios.  Mapping is safe only when the
    replay itself has exactly one scenario.  Multi-scenario mismatches remain
    untouched so model validation still rejects them.
    """

    adapted = deepcopy(config)
    if "scenario" in chronology.columns:
        chronology_scenarios = list(
            dict.fromkeys(chronology["scenario"].astype(str).tolist())
        )
    else:
        chronology_scenarios = ["base"]
    policy = adapted.setdefault("policy", {})
    configured = policy.get("transition_target_scenarios")
    audit: dict[str, Any] = {
        "chronology_scenarios": chronology_scenarios,
        "configured_transition_target_scenarios": (
            list(map(str, configured)) if configured is not None else None
        ),
        "effective_transition_target_scenarios": (
            list(map(str, configured)) if configured is not None else None
        ),
        "mapping_applied": False,
        "mapping_rule": "none",
        "requires_review": False,
    }
    if configured is None:
        audit["mapping_rule"] = "no_explicit_planning_scenario_filter"
        return adapted, audit

    targets = list(dict.fromkeys(map(str, configured)))
    unknown = sorted(set(targets) - set(chronology_scenarios))
    audit["unknown_configured_targets"] = unknown
    if unknown and len(chronology_scenarios) == 1:
        policy["transition_target_scenarios"] = chronology_scenarios
        audit.update(
            {
                "effective_transition_target_scenarios": chronology_scenarios,
                "mapping_applied": True,
                "mapping_rule": "single_realized_chronology_scenario",
                "rationale": (
                    "the fixed-investment replay has one realized chronology; "
                    "planning scenario filters therefore apply to that full year"
                ),
            }
        )
    elif unknown:
        audit.update(
            {
                "mapping_rule": "multi_scenario_mismatch_not_mapped",
                "requires_review": True,
            }
        )
    else:
        audit["mapping_rule"] = "configured_targets_already_valid"
    return adapted, audit


def fixed_investment_readiness_audit(
    fixed_config: dict[str, Any],
    chronology: pd.DataFrame,
) -> dict[str, Any]:
    """Check firm heat and firm electric capacity before the annual MILP build."""

    from .model import HeatPlanningModel

    model = HeatPlanningModel(fixed_config, chronology)
    planning = model.config["planning"]
    reserve_margin = float(planning.get("firm_capacity_reserve_margin", 0.0))
    peak_demand = model.design_peak_demand()
    building_flex = model._building_flex_firm_capacity()
    required_firm_heat = max(
        0.0, peak_demand * (1.0 + reserve_margin) - building_flex
    )
    source_rows: list[dict[str, Any]] = []
    dispatch_options: list[tuple[float, float, str]] = []
    for source_name, source in model.sources.items():
        minimum_availability = min(
            (model._source_availability(source, t) for t in model.times),
            default=1.0,
        )
        firm_availability = float(
            source.get("firm_capacity_availability", minimum_availability)
        )
        credit = float(source.get("firm_capacity_credit", 1.0))
        installed = float(source.get("existing_capacity_kw", 0.0))
        firm_heat = credit * firm_availability * installed
        source_type = str(source.get("type", ""))
        electric_ratio = 0.0
        if source_type == "heat_pump" and firm_heat > 0.0:
            electric_ratio = max(
                1.0 / model._cop(source, model.grades[grade_name], t)
                for grade_name in source.get(
                    "allowed_grades", list(model.grades)
                )
                for t in model.times
            )
        elif source_type == "electric_boiler" and firm_heat > 0.0:
            electric_ratio = 1.0 / float(source.get("efficiency", 0.98))
        dispatch_options.append((electric_ratio, firm_heat, source_name))
        source_rows.append(
            {
                "source": source_name,
                "installed_capacity_kw": installed,
                "minimum_chronology_availability": minimum_availability,
                "firm_capacity_credit": credit,
                "firm_capacity_availability": firm_availability,
                "firm_heat_capacity_kw": firm_heat,
                "worst_electricity_per_firm_heat": electric_ratio,
                "firm_electric_requirement_kw": firm_heat * electric_ratio,
            }
        )

    duration = float(planning.get("critical_peak_duration_hours", 0.0))
    storage_rows: list[dict[str, Any]] = []
    for storage_name, storage in model.storages.items():
        installed = float(storage.get("existing_capacity_kwh", 0.0))
        power_ratio = float(storage.get("power_to_energy_ratio_per_hour", 1.0))
        firm_power = power_ratio * installed
        if duration > 0.0:
            firm_power = min(firm_power, installed / duration)
        credited = firm_power * float(storage.get("firm_capacity_credit", 1.0))
        dispatch_options.append((0.0, credited, f"storage::{storage_name}"))
        storage_rows.append(
            {
                "storage": storage_name,
                "installed_energy_kwh": installed,
                "firm_heat_capacity_kw": credited,
            }
        )

    total_firm_heat = float(sum(option[1] for option in dispatch_options))
    heat_audit_applicable = reserve_margin > 0.0
    heat_gap = (
        total_firm_heat - required_firm_heat
        if heat_audit_applicable
        else None
    )
    grid = model.config.get("grid_connection")
    grid_audit: dict[str, Any] = {
        "applicable": bool(grid),
        "firm_capacity_method": None,
        "installed_grid_capacity_kw": None,
        "firm_local_electric_capacity_kw": 0.0,
        "minimum_firm_electric_requirement_kw": 0.0,
        "firm_electric_capacity_gap_kw": None,
        "pass": True,
    }
    if grid:
        method = str(grid.get("firm_capacity_method", "all_installed_electric"))
        if method not in {"all_installed_electric", "portfolio_dispatch"}:
            raise ValueError(
                "grid_connection.firm_capacity_method 仅支持 "
                "all_installed_electric 或 portfolio_dispatch"
            )
        simultaneity = float(grid.get("firm_simultaneity", 1.0))
        installed_grid = float(grid.get("existing_capacity_kw", 0.0))
        local = 0.0
        microgrid = model.config.get("microgrid_islanding", {})
        if microgrid:
            local += float(microgrid.get("existing_backup_capacity_kw", 0.0))
            local += float(microgrid.get("existing_battery_power_kw", 0.0))
            local += float(
                microgrid.get("renewable_firm_output_factor", 0.15)
            ) * float(microgrid.get("existing_renewable_capacity_kw", 0.0))
        if method == "portfolio_dispatch":
            remaining = required_firm_heat
            electric_requirement = 0.0
            for ratio, capacity, _ in sorted(dispatch_options):
                used = min(max(remaining, 0.0), capacity)
                electric_requirement += used * ratio
                remaining -= used
        else:
            electric_requirement = float(
                sum(ratio * capacity for ratio, capacity, _ in dispatch_options)
            )
        electric_requirement *= simultaneity
        grid_gap = installed_grid + local - electric_requirement
        grid_audit.update(
            {
                "firm_capacity_method": method,
                "firm_simultaneity": simultaneity,
                "installed_grid_capacity_kw": installed_grid,
                "firm_local_electric_capacity_kw": local,
                "minimum_firm_electric_requirement_kw": electric_requirement,
                "firm_electric_capacity_gap_kw": grid_gap,
                "pass": bool(grid_gap >= -1e-7),
            }
        )

    heat_pass = bool(
        not heat_audit_applicable
        or (heat_gap is not None and heat_gap >= -1e-7)
    )
    readiness_pass = bool(heat_pass and grid_audit["pass"])
    return {
        "readiness_pass": readiness_pass,
        "heat_firm_capacity_audit_applicable": heat_audit_applicable,
        "heat_firm_capacity_pass": heat_pass,
        "grid_firm_capacity_pass": bool(grid_audit["pass"]),
        "peak_effective_heat_demand_kw": peak_demand,
        "firm_capacity_reserve_margin": reserve_margin,
        "building_flex_firm_capacity_kw": building_flex,
        "required_firm_heat_capacity_kw": required_firm_heat,
        "available_firm_heat_capacity_kw": total_firm_heat,
        "firm_heat_capacity_gap_kw": heat_gap,
        "grid": grid_audit,
        "sources": source_rows,
        "storages": storage_rows,
        "caveat": (
            "pre-build adequacy screen; hourly dispatch, storage chronology, "
            "ground balance and policy constraints still require annual MILP replay"
        ),
    }


def estimate_annual_model_structure(
    config: dict[str, Any],
    chronology: pd.DataFrame,
    *,
    pilot_hours: int = 168,
) -> dict[str, Any]:
    """Project full-horizon model size from two prefix builds without solving."""

    from .model import HeatPlanningModel

    dt = float(config["planning"].get("time_step_hours", 1.0))
    if not np.isfinite(dt) or dt <= 0.0:
        raise ValueError("time_step_hours 必须为正的有限数值")
    if len(chronology) < 2:
        raise ValueError("模型规模估计至少需要两个连续时间步")
    build_config, scenario_scope_audit = adapt_config_to_chronology_scenarios(
        config, chronology
    )
    requested_hours = max(2.0 * dt, float(pilot_hours))
    maximum_rows = len(chronology)
    large_rows = min(maximum_rows, max(2, int(round(requested_hours / dt))))
    small_rows = max(2, large_rows // 2)
    sample_rows = sorted(set([small_rows, large_rows]))
    pilots: list[dict[str, Any]] = []
    for rows in sample_rows:
        sample = chronology.iloc[:rows].copy().reset_index(drop=True)
        sample["boundary_group"] = f"size_pilot_{rows}"
        model = HeatPlanningModel(build_config, sample)
        started = perf_counter()
        model.build()
        elapsed = perf_counter() - started
        profile = model_structure_profile(model, build_seconds=elapsed)
        pilots.append(profile)

    target_rows = len(chronology)

    def project(field: str) -> int:
        x = np.asarray([row["time_steps"] for row in pilots], dtype=float)
        y = np.asarray([row[field] for row in pilots], dtype=float)
        if len(pilots) == 1 or np.isclose(x.max(), x.min()):
            value = y[-1] * target_rows / max(x[-1], 1.0)
        else:
            slope, intercept = np.polyfit(x, y, 1)
            value = intercept + slope * target_rows
        return max(0, int(round(value)))

    projected = {
        "time_steps": int(target_rows),
        "variables": project("variables"),
        "integer_variables": project("integer_variables"),
        "constraints": project("constraints"),
        "matrix_nonzeros": project("matrix_nonzeros"),
    }
    projected["continuous_variables"] = max(
        0, projected["variables"] - projected["integer_variables"]
    )
    csr_bytes = (
        projected["matrix_nonzeros"] * 12
        + (projected["constraints"] + 1) * 4
    )
    vector_bytes = (
        projected["variables"] * 28
        + projected["constraints"] * 16
    )
    python_proxy_bytes = (
        projected["matrix_nonzeros"] * 72
        + projected["variables"] * 256
        + projected["constraints"] * 192
    )
    projected["estimated_csr_and_vector_memory_mb"] = (
        csr_bytes + vector_bytes
    ) / (1024.0**2)
    projected["conservative_python_build_memory_proxy_mb"] = (
        python_proxy_bytes / (1024.0**2)
    )
    scaled_build_times = [
        float(row["build_seconds"])
        * target_rows
        / max(int(row["time_steps"]), 1)
        for row in pilots
    ]
    build_estimate = float(np.median(scaled_build_times))
    projected["estimated_build_seconds"] = build_estimate
    projected["build_seconds_uncertainty_range"] = [
        max(0.0, 0.5 * min(scaled_build_times)),
        3.0 * max(scaled_build_times),
    ]
    integers = projected["integer_variables"]
    if integers < 5_000:
        risk = "moderate"
    elif integers < 50_000:
        risk = "high"
    else:
        risk = "very_high"
    return {
        "estimate_scope": (
            "unfrozen configuration proxy; investment variables are negligible "
            "but replay structure may differ after the selected plan is frozen"
        ),
        "pilot_hours_requested": int(pilot_hours),
        "scenario_scope_audit": scenario_scope_audit,
        "pilot_profiles": pilots,
        "projected_full_horizon": projected,
        "milp_solve_risk_tier": risk,
        "solve_time_estimate": None,
        "solve_time_caveat": (
            "MILP solve time is not extrapolated from build time or matrix size; "
            "it depends on presolve, integrality, incumbent quality and MIP gap"
        ),
        "recommended_action": (
            "run with an explicit time limit, persist incumbent and MIP gap, "
            "and compare actual model_execution_profile.json with this proxy"
        ),
    }


def explicit_annual_scope_audit(
    config: dict[str,Any], frame: pd.DataFrame, *, timestamp_column: str = "timestamp",
) -> dict[str,Any]:
    """Check a declared one-year service window without relabelling its clock.

    A service year (e.g. July-to-July) is distinct from a calendar year. Both
    must contain their exact one-year, continuous, half-open time axis.
    """
    scope = config.get("planning",{}).get("annual_scope")
    if not scope:
        return {"is_explicit_service_year_aligned":False}
    start,end = pd.Timestamp(scope["start_inclusive"]),pd.Timestamp(scope["end_exclusive"])
    if pd.isna(start) or pd.isna(end):
        raise ValueError("annual_scope requires valid boundaries")
    zone = scope.get("timezone")
    if start.tzinfo is None or end.tzinfo is None:
        if zone is None:
            raise ValueError("annual_scope naive boundaries require timezone")
        start = start.tz_localize(zone) if start.tzinfo is None else start
        end = end.tz_localize(zone) if end.tzinfo is None else end
    if zone:
        start,end=start.tz_convert(zone),end.tz_convert(zone)
    if end != start + pd.DateOffset(years=1):
        raise ValueError("annual_scope must span exactly one calendar anniversary")
    dt=float(config["planning"].get("time_step_hours",1.0))
    start,end=start.tz_convert("UTC"),end.tz_convert("UTC")
    duration=(end-start).total_seconds()/3600
    if not np.isfinite(dt) or dt<=0 or (end-start).value % pd.Timedelta(hours=dt).value:
        raise ValueError("annual_scope must contain whole time steps")
    if "expected_hours" in scope and not np.isclose(float(scope["expected_hours"]),duration,rtol=0,atol=1e-9):
        raise ValueError("annual_scope expected_hours disagrees with boundaries")
    parsed=[pd.Timestamp(x) for x in frame[timestamp_column]]
    if any(pd.isna(x) or x.tzinfo is None for x in parsed):
        raise ValueError("explicit annual scope requires timezone-aware input timestamps")
    observed=pd.DatetimeIndex(pd.to_datetime(parsed,utc=True)).sort_values()
    expected=pd.date_range(start,end,freq=pd.Timedelta(hours=dt),inclusive="left")
    valid=observed.equals(expected) and not observed.has_duplicates
    if not valid:
        raise ValueError("annual_scope timestamps do not match the exact declared service year")
    return {"is_explicit_service_year_aligned":True,"annual_scope_type":"declared_service_year","annual_scope":dict(scope),"annual_scope_hours":duration}


def annual_replay_preflight(
    config: dict[str, Any],
    representative_timeseries: pd.DataFrame,
    annual_timeseries: pd.DataFrame,
    *,
    timestamp_column: str = "timestamp",
    require_full_year: bool = True,
) -> AnnualReplayPreflight:
    """Validate annual data before any planning or replay solve is started."""

    from .io import validate_inputs

    validate_inputs(config, representative_timeseries)
    demand_columns = [
        str(column)
        for node in config["nodes"]
        for column in node.get("demand_columns", {}).values()
    ]
    quality = chronology_quality_audit(
        annual_timeseries,
        timestamp_column=timestamp_column,
        expected_step_hours=float(
            config["planning"].get("time_step_hours", 1.0)
        ),
        required_columns=chronology_required_columns(config, timestamp_column),
        nonnegative_columns=demand_columns,
    )
    if not quality["chronology_valid"]:
        raise ValueError(
            "连续时序质量审计未通过: " + ", ".join(quality["issues"])
        )
    if require_full_year or config.get("planning",{}).get("annual_scope"):
        quality.update(explicit_annual_scope_audit(config,annual_timeseries,timestamp_column=timestamp_column))
    chronology = prepare_full_year_timeseries(
        annual_timeseries,
        timestamp_column=timestamp_column,
    )
    validate_inputs(config, chronology)
    coverage = representative_period_coverage_audit(
        config,
        representative_timeseries,
        chronology,
    )
    recommendations = representative_coverage_recommendations(coverage)
    _, scenario_scope_audit = adapt_config_to_chronology_scenarios(
        config, chronology
    )
    if require_full_year and not quality["is_full_year_horizon"]:
        raise ValueError(
            "连续时序未覆盖完整8760/8784小时；短时序测试必须显式允许"
        )
    return AnnualReplayPreflight(
        chronological_timeseries=chronology,
        chronology_audit=quality,
        representative_coverage_audit=coverage,
        representative_recommendations=recommendations,
        scenario_scope_audit=scenario_scope_audit,
    )


def prepare_full_year_timeseries(
    frame: pd.DataFrame,
    timestamp_column: str = "timestamp",
    scenario: str = "full_year",
) -> pd.DataFrame:
    """Prepare a continuous chronological series for seasonal-storage studies."""
    if timestamp_column not in frame.columns:
        raise ValueError(f"全年时序必须包含 {timestamp_column} 列")
    result = frame.copy()
    timestamp = pd.to_datetime(result[timestamp_column], errors="raise")
    order = np.argsort(timestamp.to_numpy())
    result = result.iloc[order].reset_index(drop=True)
    timestamp = pd.to_datetime(result[timestamp_column])
    differences = timestamp.diff().dropna().dt.total_seconds() / 3600.0
    if not differences.empty and not np.allclose(differences, differences.iloc[0]):
        raise ValueError("全年时序步长不连续，需先补齐缺失时段")
    if timestamp.duplicated().any():
        raise ValueError("全年时序存在重复时间戳")
    result["time"] = timestamp.astype(str)
    result["scenario"] = scenario
    result["scenario_probability"] = 1.0
    result["period_weight"] = 1.0
    result["boundary_group"] = scenario
    return result


def expand_typical_day_to_chronological_year(
    typical_day: pd.DataFrame,
    demand_columns: list[str],
    start: str = "2026-01-01",
    days: int = 365,
    seasonal_temperature_amplitude_c: float = 13.0,
) -> pd.DataFrame:
    """Create a reproducible chronological demo year from one hourly profile.

    This is only a software demonstration. Real planning must use measured
    hourly data. Demand is scaled with a smooth heating-season factor and all
    storage states remain linked across the generated year.
    """
    if len(typical_day) == 0 or 24 % len(typical_day) != 0:
        raise ValueError("典型日必须包含可整除 24 小时的连续时段")
    base = typical_day.iloc[:24].copy().reset_index(drop=True)
    rows: list[pd.DataFrame] = []
    for day in range(days):
        block = base.copy()
        phase = 2.0 * np.pi * day / max(days, 1)
        heating_factor = 0.15 + 0.85 * (1.0 + np.cos(phase)) / 2.0
        block[demand_columns] *= heating_factor
        if "outdoor_temp" in block:
            block["outdoor_temp"] += seasonal_temperature_amplitude_c * (1.0 - np.cos(phase)) / 2.0
        block["timestamp"] = pd.date_range(pd.Timestamp(start) + pd.Timedelta(days=day), periods=24, freq="h")
        rows.append(block)
    return prepare_full_year_timeseries(pd.concat(rows, ignore_index=True))


def seasonal_storage_postcheck(
    config: dict[str, Any], dispatch: pd.DataFrame,
    *, expected_timeseries: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Audit complete storage trajectories under a frozen asset configuration.

    Reported SOC is the state *after* each time step.  For a cyclic group the
    state before its first step is therefore the final reported state, not the
    first reported state.  Comparing the first and final rows directly creates
    a false drift whenever the storage charges or discharges in the first hour.
    """

    rows: list[dict[str, Any]] = []
    frame = dispatch.copy()
    for column in ("category", "asset", "time_index", "value"):
        if column not in frame:
            frame[column] = pd.Series(dtype=float if column == "value" else object)
    if "boundary_group" not in frame.columns:
        if "scenario" in frame.columns:
            frame["boundary_group"] = frame["scenario"].astype(str)
        else:
            frame["boundary_group"] = "all"
    expected = frame[["time_index", "boundary_group"]].drop_duplicates().copy()
    if expected_timeseries is not None:
        expected = expected_timeseries.reset_index(drop=True).copy()
        expected["time_index"] = np.arange(len(expected))
        if "boundary_group" not in expected:
            expected["boundary_group"] = expected.get("scenario", "base")
    expected["boundary_group"] = expected["boundary_group"].astype(str)
    soc = frame[frame["category"] == "storage_soc"].copy()
    charge = frame[frame["category"] == "storage_charge"].copy()
    discharge = frame[frame["category"] == "storage_discharge"].copy()
    dt = float(config.get("planning", {}).get("time_step_hours", 1.0))
    tolerance = float(
        config.get("planning", {}).get(
            "storage_boundary_audit_tolerance_kwh", 1e-5
        )
    )
    complementarity_tolerance = float(
        config.get("planning", {}).get(
            "storage_operational_audit_tolerance_kw", 1e-7
        )
    )
    for storage in config.get("storages", []):
        name = str(storage["name"])
        asset_soc = soc[soc["asset"] == name]
        boundary_mode = str(storage.get("boundary_mode", "cyclic"))
        capacity = float(
            storage.get("existing_capacity_kwh", 0.0)
            + storage.get("max_new_capacity_kwh", 0.0)
        )
        eta_charge = float(storage.get("charge_efficiency", 0.95))
        eta_discharge = float(storage.get("discharge_efficiency", 0.95))
        loss_factor = (
            1.0 - float(storage.get("self_loss_per_hour", 0.0))
        ) ** dt
        groups = list(dict.fromkeys(expected["boundary_group"]))
        if not groups:
            groups = ["all"]
        for group in groups:
            group_soc = asset_soc[
                asset_soc["boundary_group"].astype(str) == group
            ].sort_values("time_index")
            group_charge = charge[
                (charge["asset"] == name)
                & (charge["boundary_group"].astype(str) == group)
            ].sort_values("time_index")
            group_discharge = discharge[
                (discharge["asset"] == name)
                & (discharge["boundary_group"].astype(str) == group)
            ].sort_values("time_index")
            series = group_soc["value"]
            charged = float(group_charge["value"].sum()) * dt
            discharged = float(group_discharge["value"].sum()) * dt
            charge_by_time = group_charge.groupby("time_index")["value"].sum()
            discharge_by_time = group_discharge.groupby("time_index")[
                "value"
            ].sum()
            aligned = pd.concat(
                [charge_by_time, discharge_by_time], axis=1
            ).fillna(0.0)
            aligned.columns = ["charge", "discharge"]
            simultaneous = (
                aligned[["charge", "discharge"]].min(axis=1)
                if not aligned.empty
                else pd.Series(dtype=float)
            )
            maximum_simultaneous = (
                float(simultaneous.max()) if not simultaneous.empty else 0.0
            )
            simultaneous_energy = float(simultaneous.sum()) * dt
            complementarity_pass = bool(
                maximum_simultaneous <= complementarity_tolerance
            )
            first_soc = float(series.iloc[0]) if not series.empty else np.nan
            end_soc = float(series.iloc[-1]) if not series.empty else np.nan
            first_charge = (
                float(group_charge["value"].iloc[0])
                if not group_charge.empty
                else 0.0
            )
            first_discharge = (
                float(group_discharge["value"].iloc[0])
                if not group_discharge.empty
                else 0.0
            )
            if boundary_mode == "cyclic":
                start_reference = end_soc
                final_requirement = end_soc
                final_margin = 0.0
            else:
                start_reference = float(
                    storage.get("initial_soc_fraction", 0.5)
                ) * capacity
                final_requirement = float(
                    storage.get("final_soc_min_fraction", 0.0)
                ) * capacity
                final_margin = end_soc - final_requirement
            balance_residual = (
                first_soc
                - loss_factor * start_reference
                - eta_charge * first_charge * dt
                + first_discharge * dt / eta_discharge
            )
            boundary_pass = bool(
                abs(balance_residual) <= tolerance
                and (
                    boundary_mode == "cyclic"
                    or final_margin >= -tolerance
                )
            )
            expected_indices = set(expected.loc[expected["boundary_group"] == group, "time_index"])
            complete = bool(expected_indices) and all(
                set(part["time_index"]) == expected_indices
                and not part["time_index"].duplicated().any()
                and np.isfinite(pd.to_numeric(part["value"], errors="coerce")).all()
                for part in (group_soc, group_charge, group_discharge)
            )
            maximum_transition_residual = np.inf
            maximum_soc_violation = np.inf
            maximum_power_violation = np.inf
            if complete:
                states = group_soc["value"].to_numpy(dtype=float)
                charges = group_charge["value"].to_numpy(dtype=float)
                discharges = group_discharge["value"].to_numpy(dtype=float)
                previous = np.r_[start_reference, states[:-1]]
                residuals = states - loss_factor * previous - eta_charge * charges * dt + discharges * dt / eta_discharge
                maximum_transition_residual = float(np.max(np.abs(residuals), initial=0.0))
                maximum_soc_violation = float(max(0.0, -states.min(), states.max() - capacity))
                power_limit = capacity * float(storage.get("power_to_energy_ratio_per_hour", 0.25))
                maximum_power_violation = float(max(0.0, -charges.min(), -discharges.min(), charges.max() - power_limit, discharges.max() - power_limit))
            trajectory_pass = bool(
                complete and maximum_transition_residual <= tolerance
                and maximum_soc_violation <= tolerance
                and maximum_power_violation <= tolerance
            )
            rows.append(
                {
                    "storage": name,
                    "boundary_group": group,
                    "start_soc_kwh": start_reference,
                    "first_period_end_soc_kwh": first_soc,
                    "end_soc_kwh": end_soc,
                    "soc_drift_kwh": end_soc - start_reference,
                    "soc_balance_residual_kwh": balance_residual,
                    "final_soc_requirement_kwh": final_requirement,
                    "final_soc_margin_kwh": final_margin,
                    "boundary_condition_pass": boundary_pass,
                    "trajectory_complete": bool(complete),
                    "expected_periods": len(expected_indices),
                    "observed_periods": len(group_soc),
                    "maximum_soc_transition_residual_kwh": maximum_transition_residual,
                    "maximum_soc_bound_violation_kwh": maximum_soc_violation,
                    "maximum_storage_power_bound_violation_kw": maximum_power_violation,
                    "state_trajectory_pass": trajectory_pass,
                    "minimum_soc_kwh": (
                        float(series.min()) if not series.empty else np.nan
                    ),
                    "maximum_soc_kwh": (
                        float(series.max()) if not series.empty else np.nan
                    ),
                    "equivalent_full_cycles": discharged
                    / max(capacity, 1e-9),
                    "charge_discharge_ratio": charged
                    / max(discharged, 1e-9),
                    "maximum_simultaneous_charge_discharge_kw": (
                        maximum_simultaneous
                    ),
                    "simultaneous_charge_discharge_kwh": simultaneous_energy,
                    "charge_discharge_complementarity_pass": (
                        complementarity_pass
                    ),
                    "boundary_mode": boundary_mode,
                }
            )
    return pd.DataFrame(rows)


def annual_replay_acceptance_audit(
    config: dict[str, Any],
    *,
    chronology_audit: dict[str, Any],
    representative_coverage_audit: pd.DataFrame,
    investment_freeze_audit: pd.DataFrame,
    fixed_investment_readiness_audit: dict[str, Any],
    planning_summary: dict[str, Any],
    replay_summary: dict[str, Any],
    storage_boundary_audit: pd.DataFrame,
    tolerance: float = 1e-7,
) -> dict[str, Any]:
    """Create a machine-readable certificate for a formal annual replay."""

    gates: list[dict[str, Any]] = []

    def add(
        gate: str,
        passed: bool,
        observed: Any,
        requirement: str,
    ) -> None:
        gates.append(
            {
                "gate": gate,
                "passed": bool(passed),
                "observed": observed,
                "requirement": requirement,
            }
        )

    add(
        "continuous_full_service_year" if chronology_audit.get("is_explicit_service_year_aligned") else "continuous_full_calendar_year",
        bool(
            chronology_audit.get("chronology_valid")
            and chronology_audit.get("is_full_year_horizon")
            and (chronology_audit.get("is_calendar_year_aligned") or chronology_audit.get("is_explicit_service_year_aligned"))
        ),
        {
            "chronology_valid": chronology_audit.get("chronology_valid"),
            "is_full_year_horizon": chronology_audit.get(
                "is_full_year_horizon"
            ),
            "is_calendar_year_aligned": chronology_audit.get(
                "is_calendar_year_aligned"
            ),
            "is_explicit_service_year_aligned":chronology_audit.get("is_explicit_service_year_aligned"),
            "annual_scope":chronology_audit.get("annual_scope"),
        },
        "连续完整的8760/8784小时，按公历年或经逐时核验的明确一年服务区间对齐",
    )
    coverage_status = representative_coverage_audit.get("coverage_status", pd.Series(dtype=str))
    review_count = int((coverage_status == "review").sum())
    add(
        "representative_period_coverage",
        bool(len(coverage_status)) and bool(coverage_status.eq("pass").all()),
        review_count,
        "代表时段覆盖审计无 review 项",
    )
    add(
        "representative_planning_optimal",
        int(planning_summary.get("solver_status", -1)) == 0,
        planning_summary.get("solver_status"),
        "代表时段规划求解器状态为最优（0）",
    )
    add(
        "annual_replay_optimal",
        int(replay_summary.get("solver_status", -1)) == 0,
        replay_summary.get("solver_status"),
        "全年固定投资回放求解器状态为最优（0）",
    )
    for label, summary in (("planning", planning_summary), ("replay", replay_summary)):
        add(
            f"{label}_numerical_feasibility",
            summary.get("numerical_audit_passed") is True and summary.get("numerical_objective_checked") is True,
            {key: value for key, value in summary.items() if key.startswith("numerical_")},
            "完整解的约束、变量界、整数性及实际求解目标重算均通过明确容差",
        )
        if config.get("building_thermal",{}).get("enabled",False):
            add(f"{label}_building_actual_service",summary.get("building_comfort_state_passed") is True and summary.get("building_no_simultaneous_heat_cooling_passed") is True,
                {key:value for key,value in summary.items() if key.startswith("building_")},
                "实际送达冷热量、RC状态、舒适边界及冷热互斥均通过；固定负荷失供率不能替代此项")
            add(f"{label}_independent_building_audit", summary.get("independent_building_audit_passed") is True,
                summary.get("independent_building_audit_metrics"),
                "独立模块按求解前可信输入重新核对完整楼宇轨迹、dispatch与实际服务summary，全部通过")
        if config.get("planning",{}).get("emissions_cap_kg_per_year") is not None:
            add(f"{label}_total_service_carbon_cap",summary.get("emissions_cap_pass") is True,
                {key:summary.get(key) for key in ("total_service_emissions_kg_per_year","emissions_cap_kg_per_year","emissions_cap_residual_kg")},
                "总冷热服务及共享电力排放满足同一碳帽")
    if any(bool(source.get("unit_commitment", False)) for source in config.get("sources", [])):
        add(
            "unit_commitment_transition_audit",
            float(replay_summary.get("unit_commitment_max_transition_residual", np.inf)) <= 1e-6
            and float(replay_summary.get("unit_commitment_max_indicator_fractionality", np.inf)) <= 1e-6,
            {key: replay_summary.get(key) for key in ("unit_commitment_max_transition_residual", "unit_commitment_max_indicator_fractionality")},
            "机组状态转移残差与启停指示量整数偏差不超过1e-6",
        )
    freeze_pass = bool(
        not investment_freeze_audit.empty
        and investment_freeze_audit["freeze_ok"].astype(bool).all()
    )
    add(
        "all_investments_frozen",
        freeze_pass,
        int(investment_freeze_audit["freeze_ok"].astype(bool).sum())
        if not investment_freeze_audit.empty
        else 0,
        "全部投资变量转化为固定既有容量，且无剩余扩建权限",
    )
    add(
        "prebuild_firm_capacity_readiness",
        bool(fixed_investment_readiness_audit.get("readiness_pass")),
        {
            "firm_heat_capacity_gap_kw": fixed_investment_readiness_audit.get(
                "firm_heat_capacity_gap_kw"
            ),
            "firm_electric_capacity_gap_kw": fixed_investment_readiness_audit.get(
                "grid", {}
            ).get("firm_electric_capacity_gap_kw"),
        },
        "固定热源与配电容量缺口均不小于 0",
    )
    planning = config.get("planning", {})
    maximum_unserved = float(planning.get("max_unserved_rate", 0.0))
    replay_unserved = float(replay_summary.get("unserved_rate", np.inf))
    add(
        "annual_heat_reliability",
        replay_unserved <= maximum_unserved + tolerance,
        replay_unserved,
        f"全年热失供率不高于 {maximum_unserved:g}",
    )
    maximum_scenario_unserved = float(
        planning.get("max_scenario_unserved_rate", maximum_unserved)
    )
    worst_scenario_unserved = float(
        replay_summary.get("worst_scenario_unserved_rate", np.inf)
    )
    add(
        "worst_scenario_heat_reliability",
        worst_scenario_unserved <= maximum_scenario_unserved + tolerance,
        worst_scenario_unserved,
        f"最差场景热失供率不高于 {maximum_scenario_unserved:g}",
    )
    cooling = config.get("multi_energy_services", {}).get("cooling", {})
    if bool(cooling.get("enabled", False)):
        maximum_cooling_unserved = float(
            cooling.get("max_unserved_rate", maximum_unserved)
        )
        cooling_unserved = float(
            replay_summary.get("cooling_unserved_rate", np.inf)
        )
        add(
            "annual_cooling_reliability",
            cooling_unserved <= maximum_cooling_unserved + tolerance,
            cooling_unserved,
            f"全年冷失供率不高于 {maximum_cooling_unserved:g}",
        )
    required_storage = {str(asset["name"]) for asset in config.get("storages", [])}
    audited_storage = set(storage_boundary_audit.get("storage", pd.Series(dtype=str)).astype(str))
    evidence_complete = audited_storage == required_storage
    if required_storage:
        evidence_complete = bool(
            evidence_complete and not storage_boundary_audit.empty
            and storage_boundary_audit.get("trajectory_complete", pd.Series([False])).eq(True).all()
            and not storage_boundary_audit.duplicated(["storage", "boundary_group"]).any()
        )
        if chronology_audit.get("rows") is not None and "observed_periods" in storage_boundary_audit:
            evidence_complete = evidence_complete and storage_boundary_audit.groupby("storage")["observed_periods"].sum().eq(int(chronology_audit["rows"])).all()
    add("storage_evidence_complete", bool(evidence_complete), sorted(audited_storage), "每个启用储热资产的全部时段均有完整且不重复的状态/充热/放热证据")
    storage_pass = bool(evidence_complete and (
        not required_storage or storage_boundary_audit["boundary_condition_pass"].eq(True).all()
    ))
    add(
        "storage_boundary_conditions",
        storage_pass,
        int(
            storage_boundary_audit["boundary_condition_pass"].astype(bool).sum()
        )
        if not storage_boundary_audit.empty
        else 0,
        "每个储热边界组均满足状态方程闭合及期末库存要求",
    )
    add(
        "storage_all_period_state_equations",
        bool(evidence_complete and (not required_storage or storage_boundary_audit.get("state_trajectory_pass", pd.Series([False])).eq(True).all())),
        float(storage_boundary_audit["maximum_soc_transition_residual_kwh"].max()) if "maximum_soc_transition_residual_kwh" in storage_boundary_audit else None,
        "所有小时储热状态转移、SOC界和功率界均独立重算通过",
    )
    complementarity_pass = bool(evidence_complete and (
        not required_storage or storage_boundary_audit["charge_discharge_complementarity_pass"].eq(True).all()
    ))
    add(
        "storage_charge_discharge_complementarity",
        complementarity_pass,
        float(
            storage_boundary_audit[
                "maximum_simultaneous_charge_discharge_kw"
            ].max()
        )
        if not storage_boundary_audit.empty
        else 0.0,
        "storage charge and discharge are not simultaneous beyond tolerance",
    )
    ground_balance = cooling.get("ground_source_annual_balance", {})
    extraction = float(
        replay_summary.get("ground_heat_extraction_kwh_per_year", 0.0)
    )
    rejection = float(
        replay_summary.get("ground_heat_rejection_kwh_per_year", 0.0)
    )
    if ground_balance and max(extraction, rejection) > tolerance:
        minimum_ratio = float(ground_balance.get("minimum_ratio", 0.8))
        maximum_ratio = float(ground_balance.get("maximum_ratio", 1.25))
        ratio = extraction / max(rejection, tolerance)
        add(
            "ground_thermal_annual_balance",
            minimum_ratio - tolerance <= ratio <= maximum_ratio + tolerance,
            ratio,
            f"年取热量/年排热量位于 [{minimum_ratio:g}, {maximum_ratio:g}]",
        )
    policy = config.get("policy", {})
    for key, summary_key, label in (
        ("minimum_clean_heat_share", "clean_heat_share", "清洁热占比"),
        (
            "minimum_non_fossil_heat_share",
            "non_fossil_heat_share",
            "非化石热占比",
        ),
    ):
        if key in policy:
            target = float(policy[key])
            observed = float(replay_summary.get(summary_key, -np.inf))
            add(
                key,
                observed + tolerance >= target,
                observed,
                f"{label}不低于 {target:g}",
            )
    failed = [str(gate["gate"]) for gate in gates if not gate["passed"]]
    return {
        "certification_pass": not failed,
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gates": failed,
        "gates": gates,
    }


def _planned_value(artifacts: Any, category: str, asset: str) -> float:
    selected = artifacts.investments[
        (artifacts.investments["category"] == category)
        & (artifacts.investments["asset"] == asset)
    ]
    return float(selected["value"].sum()) if not selected.empty else 0.0


def investment_freeze_audit(
    original_config: dict[str, Any],
    fixed_config: dict[str, Any],
    planning_artifacts: Any,
) -> pd.DataFrame:
    """Show that every planning investment has become fixed existing capacity."""

    rows: list[dict[str, Any]] = []

    def add(
        category: str,
        asset: str,
        planned: float,
        frozen_total: float,
        remaining: float,
        unit: str,
    ) -> None:
        rows.append(
            {
                "category": category,
                "asset": asset,
                "planned_value": planned,
                "frozen_installed_or_fixed_value": frozen_total,
                "remaining_candidate_limit": remaining,
                "unit": unit,
                "freeze_ok": abs(remaining) <= 1e-9,
            }
        )

    original_sources = {str(x["name"]): x for x in original_config["sources"]}
    for source in fixed_config["sources"]:
        name = str(source["name"])
        addition = _planned_value(planning_artifacts, "source_capacity", name)
        retirement = _planned_value(planning_artifacts, "source_retirement", name)
        expected = (
            float(original_sources[name].get("existing_capacity_kw", 0.0))
            + addition
            - retirement
        )
        add(
            "source_net_capacity",
            name,
            addition - retirement,
            float(source.get("existing_capacity_kw", 0.0)),
            float(source.get("max_new_capacity_kw", 0.0))
            + float(source.get("max_retire_capacity_kw", 0.0)),
            "kW",
        )
        rows[-1]["expected_frozen_value"] = expected
        rows[-1]["freeze_ok"] = bool(
            rows[-1]["freeze_ok"]
            and abs(rows[-1]["frozen_installed_or_fixed_value"] - expected)
            <= 1e-6
        )

    original_storages = {str(x["name"]): x for x in original_config["storages"]}
    for storage in fixed_config["storages"]:
        name = str(storage["name"])
        addition = _planned_value(planning_artifacts, "storage_capacity", name)
        expected = float(
            original_storages[name].get("existing_capacity_kwh", 0.0)
        ) + addition
        add(
            "storage_capacity",
            name,
            addition,
            float(storage.get("existing_capacity_kwh", 0.0)),
            float(storage.get("max_new_capacity_kwh", 0.0)),
            "kWh",
        )
        rows[-1]["expected_frozen_value"] = expected
        rows[-1]["freeze_ok"] = bool(
            rows[-1]["freeze_ok"]
            and abs(rows[-1]["frozen_installed_or_fixed_value"] - expected)
            <= 1e-6
        )

    original_nodes = {str(x["name"]): x for x in original_config["nodes"]}
    for node in fixed_config["nodes"]:
        metering = node.get("heat_metering")
        if metering:
            name = str(node["name"])
            addition = _planned_value(
                planning_artifacts, "heat_metering_coverage", name
            )
            add(
                "heat_metering_coverage",
                name,
                addition,
                float(metering.get("existing_coverage_fraction", 0.0)),
                float(metering.get("max_new_coverage_fraction", 0.0)),
                "fraction",
            )
            original_metering = original_nodes[name].get("heat_metering", {})
            expected = float(
                original_metering.get("existing_coverage_fraction", 0.0)
            ) + addition
            rows[-1]["expected_frozen_value"] = expected
            rows[-1]["freeze_ok"] = bool(
                rows[-1]["freeze_ok"]
                and abs(rows[-1]["frozen_installed_or_fixed_value"] - expected)
                <= 1e-6
            )
        retrofit = node.get("temperature_retrofit")
        if retrofit:
            name = str(node["name"])
            planned = _planned_value(
                planning_artifacts, "terminal_temperature_retrofit", name
            )
            fixed_value = float(retrofit.get("fixed_value", 0.0))
            add(
                "terminal_temperature_retrofit",
                name,
                planned,
                fixed_value,
                0.0,
                "binary",
            )
            rows[-1]["expected_frozen_value"] = planned
            rows[-1]["freeze_ok"] = bool(
                abs(fixed_value - planned) <= 1e-6
            )

    original_pipes = {str(x["name"]): x for x in original_config["pipes"]}
    for pipe in fixed_config["pipes"]:
        name = str(pipe["name"])
        original_pipe = original_pipes[name]
        selected = planning_artifacts.investments[
            (planning_artifacts.investments["category"] == "pipe_choice")
            & (planning_artifacts.investments["asset"] == name)
            & (planning_artifacts.investments["value"] > 0.5)
        ]
        selected_capacity = 0.0
        if not selected.empty:
            option_name = str(selected["option"].iloc[0])
            selected_capacity = float(
                next(
                    option["capacity_kw"]
                    for option in original_pipe.get("diameter_options", [])
                    if str(option["name"]) == option_name
                )
            )
        add(
            "pipe_candidate_options",
            name,
            _planned_value(planning_artifacts, "pipe_choice", name),
            float(pipe.get("existing_capacity_kw", 0.0)),
            float(len(pipe.get("diameter_options", []))),
            "option_count",
        )
        expected_capacity = float(
            original_pipe.get("existing_capacity_kw", 0.0)
        ) + selected_capacity
        rows[-1]["expected_frozen_value"] = expected_capacity
        rows[-1]["freeze_ok"] = bool(
            rows[-1]["freeze_ok"]
            and abs(
                rows[-1]["frozen_installed_or_fixed_value"]
                - expected_capacity
            )
            <= 1e-6
        )
        retrofit = pipe.get("retrofit")
        if retrofit:
            planned = _planned_value(
                planning_artifacts, "pipe_retrofit", name
            )
            fixed_value = float(retrofit.get("fixed_value", 0.0))
            add(
                "pipe_retrofit",
                name,
                planned,
                fixed_value,
                0.0,
                "binary",
            )
            rows[-1]["expected_frozen_value"] = planned
            rows[-1]["freeze_ok"] = bool(
                abs(fixed_value - planned) <= 1e-6
            )

    grid = fixed_config.get("grid_connection")
    if grid:
        planned = _planned_value(
            planning_artifacts, "grid_connection_capacity", "system"
        )
        add(
            "grid_connection_capacity",
            "system",
            planned,
            float(grid.get("existing_capacity_kw", 0.0)),
            float(grid.get("max_new_capacity_kw", 0.0)),
            "kW",
        )
        expected = float(
            original_config.get("grid_connection", {}).get(
                "existing_capacity_kw", 0.0
            )
        ) + planned
        rows[-1]["expected_frozen_value"] = expected
        rows[-1]["freeze_ok"] = bool(
            rows[-1]["freeze_ok"]
            and abs(rows[-1]["frozen_installed_or_fixed_value"] - expected)
            <= 1e-6
        )

    original_chains = {
        str(x["name"]): x
        for x in original_config.get("biomass_supply_chains", [])
    }
    for chain in fixed_config.get("biomass_supply_chains", []):
        name = str(chain["name"])
        planned = _planned_value(
            planning_artifacts, "biomass_inventory_capacity", name
        )
        add(
            "biomass_inventory_capacity",
            name,
            planned,
            float(chain.get("existing_inventory_capacity_kg", 0.0)),
            float(chain.get("max_new_inventory_capacity_kg", 0.0)),
            "kg",
        )
        expected = float(
            original_chains[name].get("existing_inventory_capacity_kg", 0.0)
        ) + planned
        rows[-1]["expected_frozen_value"] = expected
        rows[-1]["freeze_ok"] = bool(
            rows[-1]["freeze_ok"]
            and abs(rows[-1]["frozen_installed_or_fixed_value"] - expected)
            <= 1e-6
        )

    microgrid = fixed_config.get("microgrid_islanding")
    if microgrid:
        original_microgrid = original_config.get("microgrid_islanding", {})
        fields = {
            "microgrid_renewable_capacity": (
                "existing_renewable_capacity_kw",
                "max_new_renewable_capacity_kw",
                "kW",
            ),
            "microgrid_battery_power": (
                "existing_battery_power_kw",
                "max_new_battery_power_kw",
                "kW",
            ),
            "microgrid_battery_energy": (
                "existing_battery_energy_kwh",
                "max_new_battery_energy_kwh",
                "kWh",
            ),
            "microgrid_backup_capacity": (
                "existing_backup_capacity_kw",
                "max_new_backup_capacity_kw",
                "kW",
            ),
        }
        for category, (existing_key, maximum_key, unit) in fields.items():
            planned = _planned_value(planning_artifacts, category, "system")
            add(
                category,
                "system",
                planned,
                float(microgrid.get(existing_key, 0.0)),
                float(microgrid.get(maximum_key, 0.0)),
                unit,
            )
            expected = float(original_microgrid.get(existing_key, 0.0)) + planned
            rows[-1]["expected_frozen_value"] = expected
            rows[-1]["freeze_ok"] = bool(
                rows[-1]["freeze_ok"]
                and abs(rows[-1]["frozen_installed_or_fixed_value"] - expected)
                <= 1e-6
            )

    columns = [
        "category",
        "asset",
        "planned_value",
        "expected_frozen_value",
        "frozen_installed_or_fixed_value",
        "remaining_candidate_limit",
        "unit",
        "freeze_ok",
    ]
    return pd.DataFrame(rows).reindex(columns=columns)


def annual_replay_kpi_comparison(planning: Any, replay: Any) -> pd.DataFrame:
    """Return denominator-consistent planning versus chronology KPIs."""

    keys = (
        "demand_kwh_per_year",
        "effective_heat_demand_kwh_per_year",
        "metered_heat_savings_kwh_per_year",
        "heat_metering_coverage_fraction",
        "emissions_kg_per_year",
        "unserved_kwh_per_year",
        "unserved_rate",
        "clean_heat_share",
        "non_fossil_heat_share",
        "fossil_heat_share",
        "electricity_kwh_per_year",
        "heating_cash_operating_cost_yuan_per_year",
    )
    rows = []
    for key in keys:
        representative = planning.summary.get(key)
        chronological = replay.summary.get(key)
        if representative is None and chronological is None:
            continue
        delta = (
            float(chronological) - float(representative)
            if representative is not None and chronological is not None
            else np.nan
        )
        rows.append(
            {
                "metric": key,
                "representative_planning": representative,
                "fixed_investment_chronology": chronological,
                "absolute_delta": delta,
                "relative_delta": (
                    delta / abs(float(representative))
                    if representative is not None
                    and chronological is not None
                    and abs(float(representative)) > 1e-12
                    else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def run_fixed_investment_annual_replay(
    config: dict[str, Any],
    representative_timeseries: pd.DataFrame,
    annual_timeseries: pd.DataFrame,
    *,
    timestamp_column: str = "timestamp",
    planning_time_limit: float = 180.0,
    replay_time_limit: float = 1800.0,
    planning_mip_gap: float | None = None,
    replay_mip_gap: float | None = None,
    require_full_year: bool = True,
) -> AnnualReplayArtifacts:
    """Plan on representative periods, freeze assets, then replay chronology."""

    preflight = annual_replay_preflight(
        config,
        representative_timeseries,
        annual_timeseries,
        timestamp_column=timestamp_column,
        require_full_year=require_full_year,
    )
    fixed_plan = prepare_fixed_investment_plan(
        config,
        representative_timeseries,
        planning_time_limit=planning_time_limit,
        planning_mip_gap=planning_mip_gap,
    )
    return replay_prepared_fixed_investment_plan(
        config,
        representative_timeseries,
        annual_timeseries,
        fixed_plan,
        timestamp_column=timestamp_column,
        replay_time_limit=replay_time_limit,
        replay_mip_gap=replay_mip_gap,
        require_full_year=require_full_year,
        preflight=preflight,
    )


def attach_independent_building_audit(model: Any, artifacts: Any, identity: dict, *, continuous: bool) -> dict:
    """Audit exported evidence with equations independent of the model builder."""
    from .building_audit import audit_building_trajectory
    if not model.config.get("building_thermal", {}).get("enabled", False):
        return {}
    report = audit_building_trajectory(model.config, model.ts, artifacts.building_trajectory,
        dispatch=artifacts.dispatch, summary=artifacts.summary,
        expected_input_identity=identity, require_continuous_fixed=continuous)
    artifacts.summary["independent_building_audit_passed"] = report["passed"]
    artifacts.summary["independent_building_audit_metrics"] = report["metrics"]
    artifacts.building_independent_audit = report
    artifacts.building_input_identity = deepcopy(identity)
    return report


def replay_prepared_fixed_investment_plan(
    config: dict[str, Any],
    representative_timeseries: pd.DataFrame,
    annual_timeseries: pd.DataFrame,
    fixed_plan: FixedInvestmentPlanArtifacts,
    *,
    timestamp_column: str = "timestamp",
    replay_time_limit: float = 1800.0,
    replay_mip_gap: float | None = None,
    require_full_year: bool = True,
    preflight: AnnualReplayPreflight | None = None,
) -> AnnualReplayArtifacts:
    """Replay one chronology using an already solved and frozen plan.

    The explicit plan argument lets a multi-weather-year study solve investment
    variables once and reuse the exact same plan for every annual chronology.
    """

    from .io import validate_inputs
    from .model import HeatPlanningModel

    if preflight is None:
        preflight = annual_replay_preflight(
            config,
            representative_timeseries,
            annual_timeseries,
            timestamp_column=timestamp_column,
            require_full_year=require_full_year,
        )
    planning = fixed_plan.planning
    fixed_config = fixed_plan.fixed_config
    planning_profile = fixed_plan.planning_model_profile
    chronology = preflight.chronological_timeseries
    fixed_config, scenario_scope_audit = adapt_config_to_chronology_scenarios(
        fixed_config, chronology
    )
    validate_inputs(fixed_config, chronology)
    freeze_audit = fixed_plan.investment_freeze_audit
    readiness_audit = fixed_investment_readiness_audit(
        fixed_config, chronology
    )
    if not readiness_audit["readiness_pass"]:
        raise AnnualReplayReadinessError(
            "固定投资方案未通过全年构模前可靠容量审计",
            planning=planning,
            fixed_config=fixed_config,
            chronological_timeseries=chronology,
            readiness_audit=readiness_audit,
            investment_freeze_audit=freeze_audit,
            planning_model_profile=planning_profile,
        )
    replay_model = HeatPlanningModel(fixed_config, chronology)
    from .building_audit import building_audit_identity
    input_identity = building_audit_identity(fixed_config, replay_model.ts)
    replay_build_started = perf_counter()
    replay_model.build()
    replay_build_seconds = perf_counter() - replay_build_started
    replay_solve_started = perf_counter()
    replay = replay_model.solve(
        mip_gap=replay_mip_gap,
        time_limit=replay_time_limit,
    )
    replay_solve_seconds = perf_counter() - replay_solve_started
    attach_independent_building_audit(replay_model, replay, input_identity, continuous=True)
    replay_profile = model_structure_profile(
        replay_model,
        build_seconds=replay_build_seconds,
        solve_seconds=replay_solve_seconds,
    )
    comparison = annual_replay_kpi_comparison(planning, replay)
    return AnnualReplayArtifacts(
        planning=planning,
        replay=replay,
        fixed_config=fixed_config,
        chronological_timeseries=chronology,
        investment_freeze_audit=freeze_audit,
        kpi_comparison=comparison,
        chronology_audit=preflight.chronology_audit,
        representative_coverage_audit=preflight.representative_coverage_audit,
        representative_recommendations=preflight.representative_recommendations,
        scenario_scope_audit=scenario_scope_audit,
        fixed_investment_readiness_audit=readiness_audit,
        planning_model_profile=planning_profile,
        replay_model_profile=replay_profile,
    )


def prepare_fixed_investment_plan(
    config: dict[str, Any],
    representative_timeseries: pd.DataFrame,
    *,
    planning_time_limit: float = 180.0,
    planning_mip_gap: float | None = None,
) -> FixedInvestmentPlanArtifacts:
    """Solve and freeze one investment plan for reuse across annual years."""

    from .experiments import freeze_investment_plan
    from .model import HeatPlanningModel

    planning_model = HeatPlanningModel(config, representative_timeseries)
    from .building_audit import building_audit_identity
    input_identity = building_audit_identity(config, planning_model.ts)
    planning_build_started = perf_counter()
    planning_model.build()
    planning_build_seconds = perf_counter() - planning_build_started
    planning_solve_started = perf_counter()
    planning = planning_model.solve(
        mip_gap=planning_mip_gap,
        time_limit=planning_time_limit,
    )
    planning_solve_seconds = perf_counter() - planning_solve_started
    attach_independent_building_audit(planning_model, planning, input_identity, continuous=False)
    planning_profile = model_structure_profile(
        planning_model,
        build_seconds=planning_build_seconds,
        solve_seconds=planning_solve_seconds,
    )
    fixed_config = freeze_investment_plan(
        config,
        planning,
        relax_reliability=False,
    )
    freeze_audit = investment_freeze_audit(
        config, fixed_config, planning
    )
    return FixedInvestmentPlanArtifacts(
        planning=planning,
        fixed_config=fixed_config,
        investment_freeze_audit=freeze_audit,
        planning_model_profile=planning_profile,
    )
