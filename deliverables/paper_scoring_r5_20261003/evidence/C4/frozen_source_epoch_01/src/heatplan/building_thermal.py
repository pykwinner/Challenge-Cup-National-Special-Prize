"""Building sensible-heat 1R1C blocks, independent of the legacy load shifting model.

Powers are delivered interval-average kW, H is kW/K, C is kWh/K, and
temperatures are interval-end degC. Inputs are held constant within a step.
This is a physical scenario model, not an empirically calibrated building model.
See docs/execution_20261001/C2_handoff.md for integration and boundary semantics.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import exp, expm1, isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label} must be a finite number, not boolean")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{label} must be finite" + (" and positive" if positive else ""))
    return result


def exact_rc_coefficients(
    heat_loss_kw_per_c: float, thermal_capacity_kwh_per_c: float, dt_hours: float,
) -> tuple[float, float]:
    """Return a, b for T_end = a*T_start + (1-a)*Tout + b*(heat-cool+gains).

    expm1 avoids cancellation at small H*dt/C. H=0 is deliberately rejected:
    this module requires a positive envelope/ventilation heat loss coefficient.
    """
    h = _number(heat_loss_kw_per_c, "heat_loss_kw_per_c", positive=True)
    c = _number(thermal_capacity_kwh_per_c, "thermal_capacity_kwh_per_c", positive=True)
    dt = _number(dt_hours, "dt_hours", positive=True)
    decay = h * (dt / c)
    a, b = exp(-decay), -expm1(-decay) / h
    if not isfinite(b) or b <= 1e-12:
        raise ValueError("RC coefficient b is outside LinearMILP numerical resolution; rescale units")
    return a, b


@dataclass(frozen=True)
class BoundaryCondition:
    mode: str
    initial_temperature_c: float
    final_min_temperature_c: float | None
    final_max_temperature_c: float | None


@dataclass(frozen=True)
class BuildingSpec:
    name: str
    node: str
    heat_grade: str
    area_m2: float
    heat_loss_kw_per_c: float
    thermal_capacity_kwh_per_c: float
    a: float
    b: float
    minimum: np.ndarray
    maximum: np.ndarray
    internal_gain: np.ndarray
    solar_gain: np.ndarray
    heating_service: np.ndarray
    heat_upper: np.ndarray
    cooling_upper: np.ndarray
    boundary: dict[str, BoundaryCondition]


@dataclass(frozen=True)
class BuildingThermalInputs:
    buildings: tuple[BuildingSpec, ...]
    frame: pd.DataFrame
    dt_hours: float
    outdoor_temperature: np.ndarray
    boundary_groups: dict[str, tuple[int, ...]]
    time_boundary_group: tuple[str, ...]
    simultaneous_mode: str


@dataclass(frozen=True)
class BuildingHeatReference:
    """Conservative sensible-heat reference, never an additional fixed RC load.

    No gains or thermal-inventory credit. Design temperature is each building's
    highest required lower comfort bound, not the optional preheating upper bound.
    These are steady-state capacity screens; they do not certify recovery ramps.
    """
    node_grade_reference_kw: dict[tuple[str, str, int], float]
    node_grade_design_peak_kw: dict[tuple[str, str], float]
    building_design_peak_kw: dict[str, float]
    design_temperature_c: dict[str, float]
    design_outdoor_temperature_c: float


def building_heat_reference(
    inputs: BuildingThermalInputs, *, design_outdoor_temperature_c: float | None = None,
) -> BuildingHeatReference:
    """Shared planning/preflight reference: H*max(0, required_temperature-Tout).

    The default design outdoor temperature is the minimum in the supplied frame,
    so a short/representative frame is NOT a climate design-temperature source.
    Pass the same explicit design temperature to planning and replay preflight.
    Gains are conservatively zero for both hourly and peak capacity references.
    """
    outdoor = (_number(design_outdoor_temperature_c, "design_outdoor_temperature_c")
               if design_outdoor_temperature_c is not None else float(inputs.outdoor_temperature.min()))
    if outdoor <= -273.15:
        raise ValueError("design_outdoor_temperature_c must exceed absolute zero")
    hourly: dict[tuple[str, str, int], float] = defaultdict(float)
    peak: dict[tuple[str, str], float] = defaultdict(float)
    building_peak, design_temperature = {}, {}
    for building in inputs.buildings:
        design_temperature[building.name] = float(building.minimum.max())
        building_peak[building.name] = building.heat_loss_kw_per_c * max(0.0, design_temperature[building.name] - outdoor)
        peak[building.node, building.heat_grade] += building_peak[building.name]
        for t, required in enumerate(building.minimum):
            hourly[building.node, building.heat_grade, t] += building.heat_loss_kw_per_c * max(0.0, required - inputs.outdoor_temperature[t])
    return BuildingHeatReference(dict(hourly), dict(peak), building_peak, design_temperature, outdoor)


def _column(frame: pd.DataFrame, column: Any, label: str) -> np.ndarray:
    if not isinstance(column, str) or not column or column not in frame.columns:
        raise ValueError(f"{label}: missing or invalid timeseries column {column!r}")
    try:
        result = pd.to_numeric(frame[column], errors="raise").to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label}: column {column!r} must be numeric") from error
    if not np.isfinite(result).all():
        raise ValueError(f"{label}: column {column!r} contains non-finite values")
    return result


def _series(spec: Mapping[str, Any], key: str, frame: pd.DataFrame,
            default: float = 0.0, *, column_key: str | None = None) -> np.ndarray:
    column_key = column_key or key.removesuffix("_c").removesuffix("_kw") + "_column"
    if column_key in spec:
        return _column(frame, spec[column_key], column_key)
    return np.full(len(frame), _number(spec.get(key, default), key))


def _boundary(spec: Mapping[str, Any], section: Mapping[str, Any], group: str) -> BoundaryCondition:
    mode = str(spec.get("boundary_mode", section.get("boundary_mode", "fixed")))
    if mode not in {"fixed", "cyclic"}:
        raise ValueError("building boundary_mode must be fixed or cyclic")
    overrides = spec.get("boundary_conditions", {})
    if not isinstance(overrides, Mapping) or not isinstance(overrides.get(group, {}), Mapping):
        raise ValueError("building boundary_conditions must map groups to boundary settings")
    override = overrides.get(group, {})
    allowed = {"initial_temperature_c", "final_temperature_c", "final_min_temperature_c", "final_max_temperature_c"}
    if set(override) - allowed:
        raise ValueError(f"unsupported boundary_conditions fields: {set(override) - allowed}")
    merged = dict(spec)
    merged.update(override)
    initial = _number(merged["initial_temperature_c"], "initial_temperature_c")
    if initial <= -273.15:
        raise ValueError("initial_temperature_c must exceed absolute zero")
    terminal_keys = allowed - {"initial_temperature_c"}
    if mode == "cyclic":
        if terminal_keys.intersection(spec) or overrides:
            raise ValueError("cyclic boundaries cannot also specify fixed initial/final overrides")
        return BoundaryCondition(mode, initial, None, None)
    if "final_temperature_c" in merged:
        if {"final_min_temperature_c", "final_max_temperature_c"}.intersection(merged):
            raise ValueError("choose final_temperature_c or final_min/max_temperature_c, not both")
        final = _number(merged["final_temperature_c"], "final_temperature_c")
        return BoundaryCondition(mode, initial, final, final)
    # Conservatively recover initial thermal inventory unless explicitly relaxed.
    minimum = _number(merged.get("final_min_temperature_c", initial), "final_min_temperature_c")
    maximum = (_number(merged["final_max_temperature_c"], "final_max_temperature_c")
               if "final_max_temperature_c" in merged else None)
    if maximum is not None and minimum > maximum:
        raise ValueError("final_min_temperature_c exceeds final_max_temperature_c")
    return BoundaryCondition(mode, initial, minimum, maximum)


def validate_building_thermal_inputs(
    config: Mapping[str, Any], frame: pd.DataFrame, *, dt_hours: float | None = None,
    boundary_groups: Mapping[str, list[int]] | None = None,
) -> BuildingThermalInputs | None:
    """Validate and freeze input arrays before changing any MILP variables.

    Disabled/missing sections return None. Reference load columns are not used.
    The optional boundary_groups must exactly match the timeseries partition.
    """
    section = config.get("building_thermal", {})
    if not isinstance(section, Mapping):
        raise ValueError("building_thermal must be a mapping")
    if not isinstance(section.get("enabled", False), bool):
        raise ValueError("building_thermal.enabled must be boolean")
    if not section.get("enabled", False):
        return None
    raw_buildings = section.get("buildings")
    if not isinstance(raw_buildings, list) or not raw_buildings:
        raise ValueError("enabled building_thermal requires a nonempty buildings list")
    if frame.empty or not frame.columns.is_unique:
        raise ValueError("building timeseries must be nonempty with unique columns")
    dt = _number(dt_hours if dt_hours is not None else config.get("planning", {}).get("time_step_hours", 1.0),
                 "time_step_hours", positive=True)
    frame = frame.reset_index(drop=True).copy(deep=True)
    if "time" not in frame:
        raise ValueError("building timeseries requires timezone-aware time")
    try:
        time = pd.to_datetime(frame["time"], errors="raise")
        if time.isna().any() or not isinstance(time.dtype, pd.DatetimeTZDtype):
            raise ValueError("naive or missing time")
        frame["time"] = time.dt.tz_convert("UTC")
    except (TypeError, ValueError) as error:
        raise ValueError("building time must be timezone-aware; convert source clock to UTC explicitly") from error
    if "scenario" not in frame:
        frame["scenario"] = "base"
    if "boundary_group" not in frame:
        frame["boundary_group"] = frame["scenario"]
    for column in ("scenario", "boundary_group"):
        if frame[column].isna().any() or (frame[column].astype(str).str.strip() == "").any():
            raise ValueError(f"{column} cannot be missing or empty")
        frame[column] = frame[column].astype(str)
    for column, default in (("period_weight", config.get("planning", {}).get("representative_day_weight", 1.0)),
                            ("scenario_probability", 1.0)):
        if column not in frame:
            frame[column] = default
        frame[column] = _column(frame, column, column)
        if (frame[column] < 0).any():
            raise ValueError(f"{column} must be nonnegative")
    groups = {str(group): tuple(int(t) for t in rows.index)
              for group, rows in frame.groupby("boundary_group", sort=False)}
    if boundary_groups is not None and {str(g): tuple(ts) for g, ts in boundary_groups.items()} != groups:
        raise ValueError("model boundary_groups disagree with building timeseries")
    for group, times in groups.items():
        rows = frame.iloc[list(times)]
        if rows["scenario"].nunique() != 1:
            raise ValueError(f"boundary_group {group!r} spans different scenarios")
        if times != tuple(range(times[0], times[-1] + 1)):
            raise ValueError(f"boundary_group {group!r} is interleaved; use contiguous trajectories")
        steps = rows["time"].diff().iloc[1:].dt.total_seconds().to_numpy() / 3600
        if not np.allclose(steps, dt, rtol=0.0, atol=1e-9):
            raise ValueError(f"boundary_group {group!r} has gaps, duplicates or a wrong time step")
    outdoor = _column(frame, section.get("outdoor_temperature_column", "outdoor_temp"), "outdoor temperature")
    if (outdoor <= -273.15).any():
        raise ValueError("outdoor temperature must exceed absolute zero")
    simultaneous = section.get("simultaneous_mode", "exclusive")
    if simultaneous not in {"exclusive", "relaxed"}:
        raise ValueError("simultaneous_mode must be exclusive or relaxed")
    nodes = {str(node["name"]) for node in config.get("nodes", [])}
    grades = {str(grade["name"]) for grade in config.get("grades", [])}
    required = {"name", "node", "heat_grade", "area_m2", "heat_loss_kw_per_c", "thermal_capacity_kwh_per_c",
                "initial_temperature_c", "min_temperature_c", "max_temperature_c"}
    buildings: list[BuildingSpec] = []
    names: set[str] = set()
    for spec in raw_buildings:
        if not isinstance(spec, Mapping) or required - set(spec):
            raise ValueError(f"building requires fields {sorted(required)}")
        if any(not isinstance(spec[key], str) or not spec[key].strip() for key in ("name", "node", "heat_grade")):
            raise ValueError("building name/node/heat_grade must be nonempty strings")
        name, node, grade = spec["name"], spec["node"], spec["heat_grade"]
        if name in names or node not in nodes or grade not in grades:
            raise ValueError(f"building {name!r}: duplicate name or unknown node/heat_grade")
        names.add(name)
        area = _number(spec["area_m2"], f"{name}.area_m2", positive=True)
        h = _number(spec["heat_loss_kw_per_c"], f"{name}.heat_loss_kw_per_c", positive=True)
        c = _number(spec["thermal_capacity_kwh_per_c"], f"{name}.thermal_capacity_kwh_per_c", positive=True)
        if _number(spec["initial_temperature_c"], "initial_temperature_c") <= -273.15:
            raise ValueError("initial_temperature_c must exceed absolute zero")
        a, b = exact_rc_coefficients(h, c, dt)
        # Required scalar limits remain valid reference metadata even when columns override them.
        scalar_min = _number(spec["min_temperature_c"], "min_temperature_c")
        scalar_max = _number(spec["max_temperature_c"], "max_temperature_c")
        if scalar_min <= -273.15 or scalar_min > scalar_max:
            raise ValueError(f"building {name!r}: invalid scalar comfort interval")
        minimum, maximum = _series(spec, "min_temperature_c", frame), _series(spec, "max_temperature_c", frame)
        if (minimum <= -273.15).any() or (minimum > maximum).any():
            raise ValueError(f"building {name!r}: invalid hourly comfort interval")
        if "internal_gain_kw" in spec and "internal_gain_column" in spec:
            raise ValueError("choose internal_gain_kw or internal_gain_column to avoid double counting")
        internal, solar = _series(spec, "internal_gain_kw", frame), _series(spec, "solar_gain_kw", frame)
        if "solar_gain_kw" in spec:
            raise ValueError("use solar_gain_column (kW) for solar gains")
        if (internal < 0).any() or (solar < 0).any():
            raise ValueError("internal and solar gains must be nonnegative kW")
        service_column = spec.get("heating_service_column", section.get("heating_service_column"))
        service = np.ones(len(frame)) if service_column is None else _column(frame, service_column, "heating service")
        if not np.isin(service, [0.0, 1.0]).all():
            raise ValueError("heating_service_column must contain only 0/1")
        override_groups = spec.get("boundary_conditions", {})
        if not isinstance(override_groups, Mapping) or set(override_groups) - set(groups):
            raise ValueError(f"building {name!r}: unknown boundary_conditions group")
        boundaries = {group: _boundary(spec, section, group) for group in groups}
        heat_upper, cooling_upper = np.zeros(len(frame)), np.zeros(len(frame))
        for group, times in groups.items():
            condition = boundaries[group]
            if condition.mode == "fixed":
                end_min = max(minimum[times[-1]], condition.final_min_temperature_c)
                end_max = min(maximum[times[-1]], condition.final_max_temperature_c
                              if condition.final_max_temperature_c is not None else np.inf)
                if end_min > end_max:
                    raise ValueError(f"building {name!r}/{group}: final bound conflicts with comfort interval")
            for position, t in enumerate(times):
                prev = times[position - 1]
                lo, hi = minimum[prev], maximum[prev]
                if position == 0 and condition.mode == "fixed":
                    lo = hi = condition.initial_temperature_c
                gains = internal[t] + solar[t]
                # Bounds on *pure* heat/cooling from reachable interval endpoints.
                heat_upper[t] = max(0.0, (maximum[t] - lo) / b + h * (lo - outdoor[t]) - gains)
                cooling_upper[t] = max(0.0, (hi - minimum[t]) / b + h * (outdoor[t] - hi) + gains)
        for key, upper in (("max_heat_kw", heat_upper), ("max_cooling_kw", cooling_upper)):
            if key in spec:
                cap = _number(spec[key], key)
                if cap < 0:
                    raise ValueError(f"{key} must be nonnegative")
                np.minimum(upper, cap, out=upper)
            if not np.isfinite(upper).all():
                raise ValueError(f"building {name!r}: non-finite derived {key}")
        for key, upper in (("heating_enabled", heat_upper), ("cooling_enabled", cooling_upper)):
            if not isinstance(spec.get(key, True), bool):
                raise ValueError(f"{key} must be boolean")
            if not spec.get(key, True):
                upper[:] = 0.0
        buildings.append(BuildingSpec(name, node, grade, area, h, c, a, b, minimum, maximum,
                                      internal, solar, service, heat_upper, cooling_upper, boundaries))
    for building in buildings:
        for array in (building.minimum, building.maximum, building.internal_gain, building.solar_gain,
                      building.heating_service, building.heat_upper, building.cooling_upper):
            array.setflags(write=False)
    outdoor.setflags(write=False)
    return BuildingThermalInputs(tuple(buildings), frame, dt, outdoor, groups,
                                 tuple(frame["boundary_group"]), simultaneous)


@dataclass
class BuildingThermalBlock:
    inputs: BuildingThermalInputs
    temperature: dict[tuple[str, int], int]
    heat: dict[tuple[str, int], int]
    cooling: dict[tuple[str, int], int]
    mode: dict[tuple[str, int], int]
    node_heat: dict[tuple[str, str, int], int]
    node_cooling: dict[tuple[str, int], int]

    @property
    def covered_node_grades(self) -> frozenset[tuple[str, str]]:
        return frozenset((building.node, building.heat_grade) for building in self.inputs.buildings)

    @property
    def covered_cooling_nodes(self) -> frozenset[str]:
        return frozenset(building.node for building in self.inputs.buildings)

    def heat_reference(self, *, design_outdoor_temperature_c: float | None = None) -> BuildingHeatReference:
        return building_heat_reference(self.inputs, design_outdoor_temperature_c=design_outdoor_temperature_c)

    def export_trajectory(self, values: np.ndarray) -> pd.DataFrame:
        """Export actual delivered powers, temperatures, boundaries and RC residuals.

        Does not clip negative flows or residuals. Missing/non-finite solutions fail.
        Comfort deficits are endpoint samples, not continuous-time integrals.
        """
        x = np.asarray(values, dtype=float)
        indices = list(self.temperature.values()) + list(self.heat.values()) + list(self.cooling.values())
        if x.ndim != 1 or len(x) <= max(indices) or not np.isfinite(x[indices]).all():
            raise ValueError("building export requires a complete finite solution vector")
        inputs, rows = self.inputs, []
        for building in inputs.buildings:
            for group, times in inputs.boundary_groups.items():
                condition = building.boundary[group]
                for position, t in enumerate(times):
                    start = (condition.initial_temperature_c if position == 0 and condition.mode == "fixed"
                             else float(x[self.temperature[building.name, times[position - 1]]]))
                    end = float(x[self.temperature[building.name, t]])
                    heat, cooling = float(x[self.heat[building.name, t]]), float(x[self.cooling[building.name, t]])
                    outdoor = inputs.outdoor_temperature[t]
                    gains = building.internal_gain[t] + building.solar_gain[t]
                    expected = start + building.b * (building.heat_loss_kw_per_c * (outdoor - start) + heat - cooling + gains)
                    metadata = inputs.frame.iloc[t]
                    rows.append({
                        "building": building.name, "node": building.node, "heat_grade": building.heat_grade,
                        "time_index": t, "time": metadata["time"], "scenario": metadata["scenario"],
                        "boundary_group": group, "boundary_mode": condition.mode,
                        "period_weight": metadata["period_weight"], "scenario_probability": metadata["scenario_probability"],
                        "dt_hours": inputs.dt_hours, "area_m2": building.area_m2,
                        "heat_loss_kw_per_c": building.heat_loss_kw_per_c,
                        "thermal_capacity_kwh_per_c": building.thermal_capacity_kwh_per_c,
                        "initial_temperature_reference_c": condition.initial_temperature_c,
                        "final_min_temperature_c": condition.final_min_temperature_c,
                        "final_max_temperature_c": condition.final_max_temperature_c,
                        "temperature_start_c": start, "temperature_end_c": end,
                        "min_temperature_c": building.minimum[t], "max_temperature_c": building.maximum[t],
                        "outdoor_temperature_c": outdoor, "internal_gain_kw": building.internal_gain[t],
                        "solar_gain_kw": building.solar_gain[t], "heating_service": building.heating_service[t],
                        "heat_kw": heat, "cooling_kw": cooling,
                        "heat_upper_kw": building.heat_upper[t], "cooling_upper_kw": building.cooling_upper[t],
                        "heat_kwh": heat * inputs.dt_hours, "cooling_kwh": cooling * inputs.dt_hours,
                        "under_temperature_c": max(0.0, building.minimum[t] - end),
                        "over_temperature_c": max(0.0, end - building.maximum[t]),
                        "rc_residual_c": end - expected,
                    })
        return pd.DataFrame(rows)

    def comfort_summary(self, values: np.ndarray, *, temperature_tolerance_c: float = 1e-6,
                        power_tolerance_kw: float = 1e-6) -> pd.DataFrame:
        """Per-building/group KPIs; no probability aggregation or annual certification.

        Report interval-end degree-hours and heating-service denominators explicitly.
        A diagnostic vector with insufficient delivered heat fails RC consistency even
        if it contains fabricated comfortable temperatures. Network/solver audits are
        still required separately; these diagnostics are not an independent validator.
        """
        tol = _number(temperature_tolerance_c, "temperature_tolerance_c", positive=True)
        power_tol = _number(power_tolerance_kw, "power_tolerance_kw", positive=True)
        trajectory, rows = self.export_trajectory(values), []
        for (name, group), frame in trajectory.groupby(["building", "boundary_group"], sort=False):
            dt = frame["dt_hours"].to_numpy()
            service_hours = dt * frame["heating_service"].to_numpy()
            weight = frame["period_weight"].to_numpy()
            under, over = frame["under_temperature_c"].to_numpy(), frame["over_temperature_c"].to_numpy()
            first, last = frame.iloc[0], frame.iloc[-1]
            terminal_violation = 0.0
            if first["boundary_mode"] == "fixed":
                terminal_violation = max(0.0, float(last["final_min_temperature_c"] - last["temperature_end_c"]))
                if pd.notna(last["final_max_temperature_c"]):
                    terminal_violation = max(terminal_violation, float(last["temperature_end_c"] - last["final_max_temperature_c"]))
            simultaneous = bool(((frame["heat_kw"] > power_tol) & (frame["cooling_kw"] > power_tol)).any())
            power_valid = bool(((frame["heat_kw"] >= -power_tol) & (frame["cooling_kw"] >= -power_tol)
                                & (frame["heat_kw"] <= frame["heat_upper_kw"] + power_tol)
                                & (frame["cooling_kw"] <= frame["cooling_upper_kw"] + power_tol)).all())
            thermal_valid = bool(max(under.max(), over.max(), terminal_violation,
                                     frame["rc_residual_c"].abs().max()) <= tol)
            rows.append({
                "building": name, "boundary_group": group, "scenario": first["scenario"],
                "boundary_mode": first["boundary_mode"], "area_m2": first["area_m2"],
                "trajectory_hours": float(dt.sum()), "weighted_hours": float((dt * weight).sum()),
                "heating_service_hours": float(service_hours.sum()),
                "weighted_heating_service_hours": float((service_hours * weight).sum()),
                "heat_kwh": float(frame["heat_kwh"].sum()), "cooling_kwh": float(frame["cooling_kwh"].sum()),
                "weighted_heat_kwh": float((frame["heat_kwh"] * weight).sum()),
                "weighted_cooling_kwh": float((frame["cooling_kwh"] * weight).sum()),
                "underheated_hours_end_sample": float((service_hours * (under > tol)).sum()),
                "underheated_degree_hours_end_sample": float((service_hours * under).sum()),
                "weighted_underheated_degree_hours_end_sample": float((service_hours * weight * under).sum()),
                "overheated_degree_hours_end_sample": float((service_hours * over).sum()),
                "all_service_under_degree_hours_end_sample": float((dt * under).sum()),
                "all_service_over_degree_hours_end_sample": float((dt * over).sum()),
                "min_temperature_c": float(frame["temperature_end_c"].min()),
                "max_temperature_c": float(frame["temperature_end_c"].max()),
                "max_rc_residual_c": float(frame["rc_residual_c"].abs().max()),
                "terminal_violation_c": terminal_violation,
                "initial_to_final_inventory_change_kwh": float(first["thermal_capacity_kwh_per_c"]
                                                               * (last["temperature_end_c"] - first["temperature_start_c"])),
                "simultaneous_heat_cooling": simultaneous,
                "no_simultaneous_heat_cooling_passed": not simultaneous,
                "relaxed_equations_passed": bool(thermal_valid and power_valid),
                "building_physics_passed": bool(thermal_valid and power_valid and not simultaneous),
            })
        return pd.DataFrame(rows)


def add_building_thermal_constraints(model: Any) -> BuildingThermalBlock | None:
    """Register on model.problem / model.variables and return load variable maps.

    Needs config, ts, dt, problem and variables, optionally boundary_groups.
    Adds no source equipment, costs, unmet-load variables or reliability credits.
    The caller MUST consume node_heat/node_cooling in physical supply balances.
    """
    inputs = validate_building_thermal_inputs(model.config, model.ts, dt_hours=model.dt,
                                             boundary_groups=getattr(model, "boundary_groups", None))
    if inputs is None:
        return None
    categories = ("building_temperature", "building_heat", "building_cooling", "building_heat_mode",
                  "building_heat_by_node", "building_cooling_by_node")
    if any(model.variables.get(category) for category in categories):
        raise ValueError("building thermal variables already registered")
    maps = {category: {} for category in categories}
    temperature, heat, cooling, mode, node_heat, node_cooling = (maps[category] for category in categories)
    p = model.problem
    heat_members: dict[tuple[str, str, int], list[int]] = defaultdict(list)
    cooling_members: dict[tuple[str, int], list[int]] = defaultdict(list)
    for building in inputs.buildings:
        for t in range(len(inputs.frame)):
            key = building.name, t
            temperature[key] = p.add_var(f"building_temperature[{building.name},{t}]", lb=building.minimum[t], ub=building.maximum[t])
            heat[key] = p.add_var(f"building_heat[{building.name},{t}]", ub=building.heat_upper[t])
            cooling[key] = p.add_var(f"building_cooling[{building.name},{t}]", ub=building.cooling_upper[t])
            heat_members[building.node, building.heat_grade, t].append(heat[key])
            cooling_members[building.node, t].append(cooling[key])
            if inputs.simultaneous_mode == "exclusive" and building.heat_upper[t] > 0 and building.cooling_upper[t] > 0:
                mode[key] = p.add_var(f"building_heat_mode[{building.name},{t}]", ub=1.0, integer=True)
                p.add_constraint({heat[key]: 1.0, mode[key]: -building.heat_upper[t]}, ub=0.0)
                p.add_constraint({cooling[key]: 1.0, mode[key]: building.cooling_upper[t]}, ub=building.cooling_upper[t])
        for group, times in inputs.boundary_groups.items():
            condition = building.boundary[group]
            for position, t in enumerate(times):
                key = building.name, t
                row = {temperature[key]: 1.0, heat[key]: -building.b, cooling[key]: building.b}
                rhs = building.b * (building.heat_loss_kw_per_c * inputs.outdoor_temperature[t]
                                     + building.internal_gain[t] + building.solar_gain[t])
                if position == 0 and condition.mode == "fixed":
                    rhs += building.a * condition.initial_temperature_c
                else:
                    previous = temperature[building.name, times[position - 1]]
                    # Addition is essential for single-step cyclic groups (same index).
                    row[previous] = row.get(previous, 0.0) - building.a
                p.add_constraint(row, lb=rhs, ub=rhs)
            if condition.mode == "fixed":
                p.add_constraint({temperature[building.name, times[-1]]: 1.0},
                                 lb=condition.final_min_temperature_c,
                                 ub=condition.final_max_temperature_c if condition.final_max_temperature_c is not None else np.inf)
    for category, members, target in (("building_heat_by_node", heat_members, node_heat),
                                      ("building_cooling_by_node", cooling_members, node_cooling)):
        for key, indices in members.items():
            if len(indices) == 1:
                target[key] = indices[0]  # Alias avoids a redundant row/variable.
            else:
                aggregate = p.add_var(f"{category}[{','.join(map(str, key))}]", ub=sum(p.upper[i] for i in indices))
                target[key] = aggregate
                row = {index: -1.0 for index in indices}
                row[aggregate] = 1.0
                p.add_constraint(row, lb=0.0, ub=0.0)
    model.variables.update(maps)
    return BuildingThermalBlock(inputs, temperature, heat, cooling, mode, node_heat, node_cooling)
