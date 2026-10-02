"""Independent audit of exported interval-end building RC trajectories.

No building_thermal, model, IO or solver helpers are imported. The caller supplies
the trusted effective config and the COMPLETE intended frame, not a frame rebuilt
from the result. Freeze ``building_audit_identity`` before solving and keep it
outside the result directory if coordinated input/result changes must be detected.
This audit does not certify equipment, network supply, investment, solver gap,
preheating provenance or annual coverage beyond that trusted input frame.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime
from hashlib import sha256
import json
from math import exp, expm1, isfinite
from typing import Any

import numpy as np
import pandas as pd


_TEXT = {"building", "node", "heat_grade", "time", "scenario", "boundary_group", "boundary_mode"}
_NULLABLE = {"final_min_temperature_c", "final_max_temperature_c"}
_NUMERIC = {
    "time_index", "period_weight", "scenario_probability", "dt_hours", "area_m2",
    "heat_loss_kw_per_c", "thermal_capacity_kwh_per_c", "initial_temperature_reference_c",
    "temperature_start_c", "temperature_end_c", "min_temperature_c", "max_temperature_c",
    "outdoor_temperature_c", "internal_gain_kw", "solar_gain_kw", "heating_service",
    "heat_kw", "cooling_kw", "heat_upper_kw", "cooling_upper_kw", "heat_kwh", "cooling_kwh",
    "under_temperature_c", "over_temperature_c", "rc_residual_c",
}
_REQUIRED = _TEXT | _NUMERIC | _NULLABLE


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label}: boolean is not a number")
    result = float(value)
    if not isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{label}: requires a finite" + (" positive" if positive else "") + " number")
    return result


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, np.ndarray)):
        return [_json_value(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not isfinite(value):
        # Keep distinct non-finite identities; validation rejects them separately.
        return {"nonfinite": str(value)}
    return value


def building_audit_identity(config: Mapping[str, Any], frame: pd.DataFrame) -> dict[str, str]:
    """SHA256 of full effective config and ordered frame (index labels excluded).

    Intended as an externally retained pre-solve anchor, not a signature. Hash the
    same in-memory input representation on replay; CSV type/clock conversions can
    intentionally change this identity. All frame columns are included.
    """
    def digest(value: Any) -> str:
        payload = json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return sha256(payload).hexdigest()
    return {"config_sha256": digest(config), "frame_sha256": digest({
        "columns": list(frame.columns), "rows": frame.to_numpy(dtype=object).tolist()})}


class _Checks:
    def __init__(self) -> None:
        self.checks: dict[str, dict[str, Any]] = {}

    def record(self, code: str, passed: bool, detail: str = "") -> None:
        entry = self.checks.setdefault(code, {"passed": True, "evaluations": 0,
                                               "failures": 0, "examples": []})
        entry["evaluations"] += 1
        if not passed:
            entry["passed"] = False
            entry["failures"] += 1
            if len(entry["examples"]) < 8:
                entry["examples"].append(str(detail))

    def passed(self) -> bool:
        return bool(self.checks) and all(c["passed"] for c in self.checks.values())


def _clock(values: pd.Series, label: str) -> pd.Series:
    result = []
    for value in values:
        if not isinstance(value, (str, pd.Timestamp)) and not hasattr(value, "tzinfo"):
            raise ValueError(f"{label}: time must be explicitly timezone-aware")
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError(f"{label}: missing/naive time")
        result.append(stamp.tz_convert("UTC"))
    return pd.Series(result, dtype="datetime64[ns, UTC]")


def _column(frame: pd.DataFrame, column: str) -> np.ndarray:
    if not isinstance(column, str) or column not in frame:
        raise ValueError(f"missing input column {column!r}")
    return np.array([_number(v, column) for v in frame[column]], dtype=float)


def _prepare(config: Mapping[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    """Interpret the published input contract independently of the model builder."""
    if not isinstance(frame, pd.DataFrame) or frame.empty or not frame.columns.is_unique:
        raise ValueError("frame must be nonempty with unique columns")
    section = config.get("building_thermal", {})
    if not isinstance(section, Mapping) or section.get("enabled") is not True:
        raise ValueError("enabled building_thermal is required for a building audit")
    specs = section.get("buildings")
    if not isinstance(specs, list) or not specs:
        raise ValueError("buildings must be a nonempty list")
    if section.get("simultaneous_mode", "exclusive") not in {"exclusive", "relaxed"}:
        raise ValueError("invalid simultaneous_mode")
    planning = config.get("planning", {})
    dt = _number(planning.get("time_step_hours", 1), "time_step_hours", positive=True)
    f = frame.reset_index(drop=True).copy(deep=True)
    f["time"] = _clock(f["time"], "frame")
    if "scenario" not in f:
        f["scenario"] = "base"
    if "boundary_group" not in f:
        f["boundary_group"] = f["scenario"]
    for key in ("scenario", "boundary_group"):
        if f[key].isna().any() or f[key].astype(str).str.strip().eq("").any():
            raise ValueError(f"empty {key}")
        f[key] = f[key].astype(str)
    for key, default in (("period_weight", planning.get("representative_day_weight", 1)),
                         ("scenario_probability", 1)):
        if key not in f:
            f[key] = default
        f[key] = _column(f, key)
        if (f[key] < 0).any():
            raise ValueError(f"negative {key}")
    groups = {str(g): list(rows.index) for g, rows in f.groupby("boundary_group", sort=False)}
    for group, indices in groups.items():
        rows = f.iloc[indices]
        if indices != list(range(indices[0], indices[-1] + 1)) or rows.scenario.nunique() != 1:
            raise ValueError(f"{group}: interleaved group or multiple scenarios")
        steps = rows.time.diff().iloc[1:].dt.total_seconds().to_numpy() / 3600
        if not np.allclose(steps, dt, rtol=0, atol=1e-9):
            raise ValueError(f"{group}: duplicate/gapped/reversed time or wrong dt")
    outdoor = _column(f, section.get("outdoor_temperature_column", "outdoor_temp"))
    if (outdoor <= -273.15).any():
        raise ValueError("outdoor temperature is at/below absolute zero")
    nodes = [str(n["name"]) for n in config.get("nodes", [])]
    grades = [str(g["name"]) for g in config.get("grades", [])]
    if len(set(nodes)) != len(nodes) or len(set(grades)) != len(grades):
        raise ValueError("duplicate node/grade declarations")
    buildings: dict[str, dict[str, Any]] = {}
    for spec in specs:
        name, node, grade = (spec[key] for key in ("name", "node", "heat_grade"))
        if any(not isinstance(v, str) or not v.strip() for v in (name, node, grade)):
            raise ValueError("building name/node/heat_grade must be nonempty strings")
        if name in buildings or node not in nodes or grade not in grades:
            raise ValueError(f"{name}: duplicate building or unknown node/grade")
        scalar = {k: _number(spec[k], f"{name}.{k}", positive=True) for k in
                  ("area_m2", "heat_loss_kw_per_c", "thermal_capacity_kwh_per_c")}
        h, c = scalar["heat_loss_kw_per_c"], scalar["thermal_capacity_kwh_per_c"]
        a, b = exp(-dt * h / c), -expm1(-dt * h / c) / h
        if not isfinite(b) or b <= 1e-12:
            raise ValueError(f"{name}: RC coefficient below supported numerical resolution")
        initial = _number(spec["initial_temperature_c"], "initial_temperature_c")
        lo = _number(spec["min_temperature_c"], "min_temperature_c")
        hi = _number(spec["max_temperature_c"], "max_temperature_c")
        if initial <= -273.15 or lo <= -273.15 or lo > hi:
            raise ValueError(f"{name}: invalid initial/scalar comfort temperature")
        def series(column_key: str, default: float) -> np.ndarray:
            return _column(f, spec[column_key]) if column_key in spec else np.full(len(f), default)
        lower, upper = series("min_temperature_column", lo), series("max_temperature_column", hi)
        if (lower <= -273.15).any() or (lower > upper).any():
            raise ValueError(f"{name}: invalid hourly comfort interval")
        if "internal_gain_kw" in spec and "internal_gain_column" in spec:
            raise ValueError("ambiguous internal gains")
        if "solar_gain_kw" in spec:
            raise ValueError("solar gains require solar_gain_column")
        internal = series("internal_gain_column", _number(spec.get("internal_gain_kw", 0), "internal_gain_kw"))
        solar = series("solar_gain_column", 0)
        if (internal < 0).any() or (solar < 0).any():
            raise ValueError("negative internal/solar gains")
        service_col = spec.get("heating_service_column", section.get("heating_service_column"))
        service = np.ones(len(f)) if service_col is None else _column(f, service_col)
        if not np.isin(service, [0, 1]).all():
            raise ValueError("heating service indicator must be 0/1")
        mode = spec.get("boundary_mode", section.get("boundary_mode", "fixed"))
        overrides = spec.get("boundary_conditions", {})
        terminal_keys = {"final_temperature_c", "final_min_temperature_c", "final_max_temperature_c"}
        if mode not in {"fixed", "cyclic"} or not isinstance(overrides, Mapping) or set(overrides) - set(groups):
            raise ValueError(f"{name}: invalid boundary mode/overrides")
        if mode == "cyclic" and (overrides or terminal_keys.intersection(spec)):
            raise ValueError(f"{name}: cyclic cannot use fixed terminal/initial overrides")
        conditions, heat_cap, cool_cap = {}, np.zeros(len(f)), np.zeros(len(f))
        for group, indices in groups.items():
            override = overrides.get(group, {})
            if not isinstance(override, Mapping) or set(override) - (terminal_keys | {"initial_temperature_c"}):
                raise ValueError(f"{name}/{group}: invalid boundary override fields")
            merged = dict(spec, **override)
            start = _number(merged["initial_temperature_c"], "initial_temperature_c")
            if start <= -273.15:
                raise ValueError("initial temperature at/below absolute zero")
            final_lo = final_hi = None
            if mode == "fixed":
                if "final_temperature_c" in merged:
                    if {"final_min_temperature_c", "final_max_temperature_c"}.intersection(merged):
                        raise ValueError("conflicting terminal equality/interval")
                    final_lo = final_hi = _number(merged["final_temperature_c"], "final_temperature_c")
                else:
                    final_lo = _number(merged.get("final_min_temperature_c", start), "final_min_temperature_c")
                    if "final_max_temperature_c" in merged:
                        final_hi = _number(merged["final_max_temperature_c"], "final_max_temperature_c")
                end_lo = max(lower[indices[-1]], final_lo)
                end_hi = min(upper[indices[-1]], final_hi if final_hi is not None else float("inf"))
                if end_lo > end_hi:
                    raise ValueError(f"{name}/{group}: terminal bounds incompatible with comfort")
            conditions[group] = {"mode": mode, "initial": start, "final_lo": final_lo, "final_hi": final_hi}
            for pos, t in enumerate(indices):
                prev_lo, prev_hi = lower[indices[pos - 1]], upper[indices[pos - 1]]
                if pos == 0 and mode == "fixed":
                    prev_lo = prev_hi = start
                # Invert the temperature CHANGE; avoid subtracting almost equal
                # absolute temperatures when the heat capacity is large.
                gains = internal[t] + solar[t]
                maintain_lo = h * (prev_lo - outdoor[t]) - gains
                maintain_hi = h * (prev_hi - outdoor[t]) - gains
                heat_cap[t] = max(0., (upper[t] - prev_lo) / b + maintain_lo)
                cool_cap[t] = max(0., (prev_hi - lower[t]) / b - maintain_hi)
        for key, enabled, cap in (("max_heat_kw", "heating_enabled", heat_cap),
                                  ("max_cooling_kw", "cooling_enabled", cool_cap)):
            if key in spec:
                ceiling = _number(spec[key], key)
                if ceiling < 0:
                    raise ValueError(f"negative {key}")
                np.minimum(cap, ceiling, out=cap)
            if not isinstance(spec.get(enabled, True), bool):
                raise ValueError(f"{enabled} must be boolean")
            if not spec.get(enabled, True):
                cap[:] = 0.
            if not np.isfinite(cap).all():
                raise ValueError("nonfinite derived power bound")
        buildings[name] = dict(scalar, name=name, node=node, heat_grade=grade, a=a, b=b,
                               lower=lower, upper=upper, internal=internal, solar=solar,
                               service=service, conditions=conditions, heat_cap=heat_cap, cool_cap=cool_cap)
    return {"frame": f, "dt": dt, "groups": groups, "outdoor": outdoor, "buildings": buildings}


def audit_building_trajectory(
    config: Mapping[str, Any], frame: pd.DataFrame, trajectory: pd.DataFrame,
    dispatch: pd.DataFrame | None = None, summary: Mapping[str, Any] | None = None, *,
    temperature_tolerance_c: float = 1e-6, power_tolerance_kw: float = 1e-6,
    energy_tolerance_kwh: float = 1e-5, metadata_tolerance: float = 1e-8,
    energy_relative_tolerance: float = 1e-9, require_continuous_fixed: bool = False,
    expected_input_identity: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a strict JSON-serializable report; malformed data fails closed.

    ``passed`` includes all requested checks; ``building_physics_passed`` covers
    trusted-input/trajectory checks before optional dispatch/summary comparison.
    Rows may be reordered, but every (configured building, frame position) must
    occur exactly once and its UTC clock/scenario/group must match. Independent
    representative groups are supported. ``require_continuous_fixed`` requires
    fixed state, unit period weights and one group per scenario; it does not
    infer a complete year from a filename or from the number of unit tests.
    Summary accepts explicit building_*_delivered_kwh_per_year; current model
    total-demand fields can be compared exactly only when all services are RC.
    For HeatPlanningModel exports, pass model.ts (including its normalized
    scenario probabilities) and freeze identity on that SAME effective frame.
    Raw direct-C2 inputs default to probability=1; the core's multi-scenario
    defaults differ. No inference from exported metadata resolves that choice.
    """
    checks = _Checks()
    report: dict[str, Any] = {
        "schema_version": "building_audit.v1", "passed": False, "building_physics_passed": False,
        "checks": checks.checks, "metrics": {}, "input_identity": None,
        "input_identity_anchored": expected_input_identity is not None,
        "dispatch_checked": dispatch is not None, "summary_checked": summary is not None,
        "dispatch_check_scope": "building_rows_and_optional_node_aliases_only",
        "summary_check_scope": "actual_RC_delivery_and_present_building_diagnostics_only",
        "scope": "supplied_trusted_frame_only", "annual_certification": False,
        "limitations": ["No equipment/network source balance, investment or solver-optimality certification.",
                        "Comfort is sampled at interval ends; initial preheat provenance is external.",
                        "Keep original full frame/config and pre-solve identity outside mutable results."],
    }
    try:
        tol_t = _number(temperature_tolerance_c, "temperature_tolerance_c", positive=True)
        tol_p = _number(power_tolerance_kw, "power_tolerance_kw", positive=True)
        tol_e = _number(energy_tolerance_kwh, "energy_tolerance_kwh", positive=True)
        tol_m = _number(metadata_tolerance, "metadata_tolerance", positive=True)
        rtol_e = _number(energy_relative_tolerance, "energy_relative_tolerance")
        if rtol_e < 0 or not isinstance(require_continuous_fixed, bool):
            raise ValueError("invalid relative tolerance/continuous flag")
        report["tolerances"] = {"temperature_c": tol_t, "power_kw": tol_p, "energy_kwh": tol_e,
                                "metadata_absolute": tol_m, "energy_relative": rtol_e}
        inputs = _prepare(config, frame)
        report["input_identity"] = building_audit_identity(config, frame)
        if expected_input_identity is not None:
            checks.record("input_identity", dict(expected_input_identity) == report["input_identity"],
                          "effective config or ordered input frame differs from frozen identity")
        checks.record("input_valid", True)
        f, dt, groups, buildings = (inputs[k] for k in ("frame", "dt", "groups", "buildings"))
        report["expected_rows"] = len(f) * len(buildings)
        report["observed_rows"] = len(trajectory)
        report["frame_rows"] = len(f)
        report["boundary_groups"] = [{"boundary_group": g, "scenario": f.scenario.iloc[ix[0]],
                                       "rows": len(ix), "start_utc": f.time.iloc[ix[0]].isoformat(),
                                       "end_exclusive_utc": (f.time.iloc[ix[-1]] + pd.Timedelta(hours=dt)).isoformat()}
                                      for g, ix in groups.items()]
        if require_continuous_fixed:
            continuous = (all(b["conditions"][g]["mode"] == "fixed" for b in buildings.values() for g in groups)
                          and f.groupby("scenario").boundary_group.nunique().eq(1).all()
                          and np.all(f.period_weight.to_numpy() == 1))
            checks.record("continuous_fixed", continuous, "continuous replay requires fixed state, one group/scenario, weight=1")
        if not isinstance(trajectory, pd.DataFrame) or not trajectory.columns.is_unique:
            raise ValueError("trajectory must have unique columns")
        missing = _REQUIRED - set(trajectory.columns)
        if missing:
            raise ValueError(f"missing trajectory columns: {sorted(missing)}")
        tr = trajectory.reset_index(drop=True).copy(deep=True)
        for col in _TEXT - {"time"}:
            if tr[col].isna().any() or not tr[col].map(lambda v: isinstance(v, str) and bool(v.strip())).all():
                raise ValueError(f"invalid trajectory text column {col}")
        tr["time"] = _clock(tr["time"], "trajectory")
        for col in _NUMERIC:
            tr[col] = [_number(v, f"trajectory.{col}") for v in tr[col]]
        for col in _NULLABLE:
            tr[col] = [None if pd.isna(v) else _number(v, col) for v in tr[col]]
        indices = tr.time_index.to_numpy()
        if not np.equal(indices, np.floor(indices)).all() or (indices < 0).any() or (indices >= len(f)).any():
            raise ValueError("time_index must be an exact integer frame position")
        tr["time_index"] = tr.time_index.astype(int)
        expected = {(name, t) for name in buildings for t in range(len(f))}
        actual = list(zip(tr.building, tr.time_index))
        complete = len(actual) == len(expected) and len(set(actual)) == len(actual) and set(actual) == expected
        checks.record("trajectory_complete", complete, "missing/duplicate/unknown (building,time_index) rows")
        if not complete:
            return report
        tr = tr.set_index(["building", "time_index"], drop=False)
        node_heat: dict[tuple[str, str, int], float] = defaultdict(float)
        node_cool: dict[tuple[str, int], float] = defaultdict(float)
        detailed: list[dict[str, Any]] = []
        residual_max = under_max = over_max = terminal_max = 0.
        heat_total = cool_total = weighted_heat = weighted_cool = under_dh = over_dh = 0.
        simultaneous_count = 0
        for name, building in buildings.items():
            h, c = building["heat_loss_kw_per_c"], building["thermal_capacity_kwh_per_c"]
            for group, times in groups.items():
                condition = building["conditions"][group]
                first_start = (condition["initial"] if condition["mode"] == "fixed"
                               else float(tr.loc[(name, times[-1]), "temperature_end_c"]))
                previous = first_start
                group_heat = group_cool = 0.
                for t in times:
                    row = tr.loc[(name, t)]
                    label = f"{name}/{group}/t={t}"
                    checks.record("time_and_assignment", all((
                        row.time == f.time.iloc[t], row.scenario == f.scenario.iloc[t],
                        row.boundary_group == group, row.node == building["node"],
                        row.heat_grade == building["heat_grade"], row.boundary_mode == condition["mode"])), label)
                    metadata = {
                        "area_m2": building["area_m2"], "heat_loss_kw_per_c": h,
                        "thermal_capacity_kwh_per_c": c, "dt_hours": dt,
                        "period_weight": f.period_weight.iloc[t], "scenario_probability": f.scenario_probability.iloc[t],
                        "initial_temperature_reference_c": condition["initial"],
                        "min_temperature_c": building["lower"][t], "max_temperature_c": building["upper"][t],
                        "outdoor_temperature_c": inputs["outdoor"][t], "internal_gain_kw": building["internal"][t],
                        "solar_gain_kw": building["solar"][t], "heating_service": building["service"][t],
                    }
                    for key, wanted in metadata.items():
                        checks.record("input_metadata", abs(float(row[key]) - wanted) <= tol_m, f"{label}: {key}")
                    for key, wanted in (("final_min_temperature_c", condition["final_lo"]),
                                        ("final_max_temperature_c", condition["final_hi"])):
                        checks.record("boundary_metadata", (pd.isna(row[key]) if wanted is None else
                                      pd.notna(row[key]) and abs(float(row[key]) - wanted) <= tol_m), f"{label}: {key}")
                    for key, wanted in (("heat_upper_kw", building["heat_cap"][t]), ("cooling_upper_kw", building["cool_cap"][t])):
                        checks.record("power_bound_metadata", abs(float(row[key]) - wanted) <= tol_p, f"{label}: {key}")
                    checks.record("state_continuity", abs(row.temperature_start_c - previous) <= tol_t, label)
                    heat, cool, end = (float(row[k]) for k in ("heat_kw", "cooling_kw", "temperature_end_c"))
                    # Rebuild the analytic ODE solution using trusted H/C, gains,
                    # dt and predecessor; never use reported start or residual.
                    net = heat - cool + building["internal"][t] + building["solar"][t]
                    expected_end = building["a"] * previous + building["b"] * (h * inputs["outdoor"][t] + net)
                    residual = end - expected_end
                    under, over = max(0., building["lower"][t] - end), max(0., end - building["upper"][t])
                    simultaneous = heat > tol_p and cool > tol_p
                    checks.record("exact_rc", isfinite(residual) and abs(residual) <= tol_t, f"{label}: residual={residual:.9g} C")
                    checks.record("comfort_bounds", max(under, over) <= tol_t, label)
                    checks.record("delivered_power_bounds", -tol_p <= heat <= building["heat_cap"][t] + tol_p
                                  and -tol_p <= cool <= building["cool_cap"][t] + tol_p, label)
                    checks.record("no_simultaneous_heat_cooling", not simultaneous, label)
                    for key, wanted, tolerance in (("rc_residual_c", residual, tol_t), ("under_temperature_c", under, tol_t),
                                                   ("over_temperature_c", over, tol_t), ("heat_kwh", heat * dt, tol_e),
                                                   ("cooling_kwh", cool * dt, tol_e)):
                        checks.record("reported_diagnostics", abs(row[key] - wanted) <= tolerance, f"{label}: {key}")
                    residual_max, under_max, over_max = max(residual_max, abs(residual)), max(under_max, under), max(over_max, over)
                    simultaneous_count += int(simultaneous)
                    under_dh += under * dt
                    over_dh += over * dt
                    group_heat += heat * dt
                    group_cool += cool * dt
                    op_weight = dt * f.period_weight.iloc[t] * f.scenario_probability.iloc[t]
                    weighted_heat += heat * op_weight
                    weighted_cool += cool * op_weight
                    node_heat[building["node"], building["heat_grade"], t] += heat
                    node_cool[building["node"], t] += cool
                    previous = end
                terminal = 0.
                if condition["mode"] == "fixed":
                    terminal = max(0., condition["final_lo"] - previous)
                    if condition["final_hi"] is not None:
                        terminal = max(terminal, previous - condition["final_hi"])
                checks.record("terminal_state", terminal <= tol_t, f"{name}/{group}: {terminal:.9g} C")
                terminal_max = max(terminal_max, terminal)
                detailed.append({"building": name, "boundary_group": group, "mode": condition["mode"],
                                 "initial_start_c": first_start, "final_end_c": previous,
                                 "inventory_change_kwh": c * (previous - first_start),
                                 "trajectory_hours": len(times) * dt, "heat_kwh": group_heat, "cooling_kwh": group_cool})
                heat_total += group_heat
                cool_total += group_cool
        report["metrics"] = {"max_rc_residual_c": residual_max, "max_under_temperature_c": under_max,
                             "max_over_temperature_c": over_max, "max_terminal_violation_c": terminal_max,
                             "under_degree_hours_end_sample": under_dh, "over_degree_hours_end_sample": over_dh,
                             "simultaneous_rows": simultaneous_count, "heat_kwh": heat_total, "cooling_kwh": cool_total,
                             "expected_weighted_heat_kwh": weighted_heat, "expected_weighted_cooling_kwh": weighted_cool}
        report["building_groups"] = detailed
        report["node_service_energy"] = _node_energy(node_heat, node_cool, f, dt)
        checks.record("finite_recalculation", _all_finite(report["metrics"]) and _all_finite(detailed)
                      and _all_finite(report["node_service_energy"]), "nonfinite arithmetic in recomputed metrics")
        report["building_physics_passed"] = checks.passed()
        if dispatch is not None:
            _audit_dispatch(checks, dispatch, tr, f, buildings, node_heat, node_cool, tol_t, tol_p)
        if summary is not None:
            _audit_summary(checks, report, config, summary, buildings, tol_e, rtol_e, tol_t)
        report["passed"] = checks.passed()
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as error:
        checks.record("invalid_data", False, f"{type(error).__name__}: {error}")
    # Sanitize any failed arithmetic too: allow_nan=False must always work.
    return _json_value(report)


def _all_finite(value: Any) -> bool:
    if isinstance(value, Mapping):
        return all(_all_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_all_finite(v) for v in value)
    return not isinstance(value, (float, np.floating)) or isfinite(value)


def _node_energy(heat: dict, cool: dict, f: pd.DataFrame, dt: float) -> list[dict[str, Any]]:
    totals: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0., 0.])
    for service, powers in (("heat", heat), ("cooling", cool)):
        for key, power in powers.items():
            node, grade, t = key if service == "heat" else (key[0], "cooling", key[1])
            values = totals[node, grade, service]
            values[0] += power * dt
            values[1] += power * dt * f.period_weight.iloc[t] * f.scenario_probability.iloc[t]
    return [{"node": n, "grade": g, "service": s, "kwh": v[0], "expected_weighted_kwh": v[1]}
            for (n, g, s), v in totals.items()]


def _audit_dispatch(checks: _Checks, dispatch: pd.DataFrame, tr: pd.DataFrame, f: pd.DataFrame,
                    buildings: dict, node_heat: dict, node_cool: dict, tol_t: float, tol_p: float) -> None:
    required = {"category", "asset", "node", "grade", "time_index", "time", "scenario", "boundary_group", "value", "unit"}
    if not isinstance(dispatch, pd.DataFrame) or not dispatch.columns.is_unique or required - set(dispatch.columns):
        raise ValueError("dispatch missing required columns or has duplicate columns")
    categories = {"building_heat": ("heat_kw", "kW"), "building_cooling": ("cooling_kw", "kW"),
                  "building_temperature": ("temperature_end_c", "degC")}
    selected = dispatch[dispatch.category.isin(set(categories) | {"building_heat_by_node", "building_cooling_by_node"})].copy().reset_index(drop=True)
    selected["time"] = _clock(selected.time, "dispatch")
    for key in ("time_index", "value"):
        selected[key] = [_number(v, f"dispatch.{key}") for v in selected[key]]
    if not selected.time_index.between(0, len(f) - 1).all() or not selected.time_index.mod(1).eq(0).all():
        raise ValueError("invalid dispatch time_index")
    selected["time_index"] = selected.time_index.astype(int)
    for category, (column, unit) in categories.items():
        rows = selected[selected.category == category]
        keys = list(zip(rows.asset, rows.time_index))
        expected = set(tr.index)
        complete = len(keys) == len(expected) and len(set(keys)) == len(keys) and set(keys) == expected
        checks.record("dispatch_complete", complete, f"{category}: missing/duplicate/unknown rows")
        if not complete:
            continue
        aggregated: dict[tuple, float] = defaultdict(float)
        for row in rows.itertuples(index=False):
            t, building = row.time_index, buildings[row.asset]
            grade = "cooling" if category == "building_cooling" else building["heat_grade"]
            checks.record("dispatch_metadata", row.node == building["node"] and row.grade == grade and row.unit == unit
                          and row.time == f.time.iloc[t] and row.scenario == f.scenario.iloc[t]
                          and row.boundary_group == f.boundary_group.iloc[t], f"{category}/{row.asset}/{t}")
            checks.record("dispatch_values", abs(row.value - tr.loc[(row.asset, t), column]) <= (tol_t if unit == "degC" else tol_p),
                          f"{category}/{row.asset}/{t}")
            if unit == "kW":
                aggregated[(row.node, row.grade, t) if category == "building_heat" else (row.node, t)] += row.value
        if unit == "kW":
            wanted = node_heat if category == "building_heat" else node_cool
            checks.record("dispatch_node_aggregation", set(aggregated) == set(wanted) and
                          all(abs(aggregated[k] - v) <= tol_p for k, v in wanted.items()), category)
    # Optional explicit node exports are aliases of the same delivered service:
    # compare, never add them to building energy a second time.
    for category, wanted in (("building_heat_by_node", node_heat), ("building_cooling_by_node", node_cool)):
        rows = selected[selected.category == category]
        if rows.empty:
            continue
        keys = [(r.node, r.grade, r.time_index) if category == "building_heat_by_node" else (r.node, r.time_index)
                for r in rows.itertuples(index=False)]
        checks.record("dispatch_explicit_nodes_complete", len(keys) == len(wanted) and len(set(keys)) == len(keys)
                      and set(keys) == set(wanted), category)
        for row, key in zip(rows.itertuples(index=False), keys):
            t = row.time_index
            checks.record("dispatch_explicit_nodes", key in wanted and abs(row.value - wanted.get(key, 0)) <= tol_p
                          and row.unit == "kW" and row.time == f.time.iloc[t] and row.scenario == f.scenario.iloc[t]
                          and row.boundary_group == f.boundary_group.iloc[t]
                          and (category == "building_heat_by_node" or row.grade == "cooling"), str(key))


def _audit_summary(checks: _Checks, report: dict, config: Mapping, summary: Mapping,
                   buildings: dict, tol: float, rtol: float, tol_t: float) -> None:
    if not isinstance(summary, Mapping):
        raise ValueError("summary must be a mapping")
    covered_heat = {(b["node"], b["heat_grade"]) for b in buildings.values()}
    declared_heat = {(str(n["name"]), str(g)) for n in config.get("nodes", []) for g in n.get("demand_columns", {})}
    covered_cool = {b["node"] for b in buildings.values()}
    declared_cool = set(config.get("multi_energy_services", {}).get("cooling", {}).get("demand_columns", {}))
    metrics = report["metrics"]
    comparisons = []
    for service, pure_rc, total_key, unserved_key in (
        ("heat", declared_heat <= covered_heat, "demand_kwh_per_year", "unserved_kwh_per_year"),
        ("cooling", declared_cool <= covered_cool, "cooling_demand_kwh_per_year", "cooling_unserved_kwh_per_year"),
    ):
        target = metrics[f"expected_weighted_{service}_kwh"]
        explicit = f"building_{service}_delivered_kwh_per_year"
        keys = [explicit] if explicit in summary else []
        if pure_rc and total_key in summary:
            keys.append(total_key)
        checks.record("summary_actual_service_available", bool(keys),
                      f"{service}: requires {explicit}, or all-RC {total_key}; reference load is not actual delivery")
        for key in keys:
            actual = _number(summary[key], key)
            checks.record("summary_actual_service_energy", abs(actual - target) <= tol + rtol * abs(target), key)
            comparisons.append({"field": key, "reported_kwh": actual, "recomputed_kwh": target})
        if pure_rc and unserved_key in summary:
            checks.record("summary_rc_no_unserved", abs(_number(summary[unserved_key], unserved_key)) <= tol, unserved_key)
    if "building_count" in summary:
        checks.record("summary_building_count", _number(summary["building_count"], "building_count") == len(buildings))
    for key, recomputed in (
        ("building_max_rc_residual_c", "max_rc_residual_c"),
        ("building_under_degree_hours_end_sample", "under_degree_hours_end_sample"),
        ("building_over_degree_hours_end_sample", "over_degree_hours_end_sample"),
    ):
        if key in summary:
            checks.record("summary_building_diagnostics", abs(_number(summary[key], key) - metrics[recomputed]) <= tol_t, key)
    for key in ("building_comfort_state_passed", "building_no_simultaneous_heat_cooling_passed"):
        if key in summary:
            expected = (report["building_physics_passed"] if key == "building_comfort_state_passed"
                        else metrics["simultaneous_rows"] == 0)
            checks.record("summary_physics_flags", isinstance(summary[key], (bool, np.bool_)) and bool(summary[key]) == expected, key)
    report["summary_comparisons"] = comparisons
