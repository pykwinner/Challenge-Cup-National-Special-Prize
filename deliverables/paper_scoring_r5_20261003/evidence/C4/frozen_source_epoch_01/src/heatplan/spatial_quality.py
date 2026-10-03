"""Pre-build hierarchy, candidate-route and heat-quality checks.

All network flows remain static kW heat transfers. Coordinates are explicit
Cartesian metres, not a hydraulic model or evidence of underground assets.
Call ``preprocess_spatial_quality`` before constructing HeatPlanningModel and
pass its returned ``config`` to the model: inspecting the report alone does
not enforce candidate exclusions or distance-dependent investment costs.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import hypot, isclose, isfinite
from typing import Any, Mapping, Sequence

import numpy as np


class SpatialQualityError(ValueError):
    """Invalid ownership, unsupported conversion or incomplete spatial input."""


def _number(value: Any, label: str, *, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SpatialQualityError(f"{label} must be a finite number") from exc
    if isinstance(value, bool) or not isfinite(number):
        raise SpatialQualityError(f"{label} must be a finite number")
    if minimum is not None and number < minimum:
        raise SpatialQualityError(f"{label} must be >= {minimum}")
    return number


def _named(rows: Sequence[Mapping[str, Any]], label: str, key: str = "name") -> dict:
    result = {}
    for row in rows:
        name = row.get(key)
        if not isinstance(name, str) or not name.strip():
            raise SpatialQualityError(f"{label} requires a nonempty {key}")
        if name in result:
            raise SpatialQualityError(f"duplicate {label}: {name}")
        result[name] = deepcopy(dict(row))
    return result


@dataclass
class Hierarchy:
    """Original building records and additive summaries; never an equivalent RC."""

    buildings: dict[str, dict]
    node_to_district: dict[str, str]
    district_to_system: dict[str, str]
    grades: tuple[str, ...]
    node_grade: dict[tuple[str, str], dict]
    district_grade: dict[tuple[str, str], dict]
    system_grade: dict[tuple[str, str], dict]


@dataclass
class PowerAggregation:
    """Instantaneous/interval-average kW, separately by grade at each level.

    Levels are alternate views of the same delivered heat, not quantities to
    add together. No time weights or network/source losses are applied here.
    """

    node_grade_kw: dict[tuple[str, str], np.ndarray]
    district_grade_kw: dict[tuple[str, str], np.ndarray]
    system_grade_kw: dict[tuple[str, str], np.ndarray]


@dataclass
class SpatialQualityResult:
    config: dict[str, Any]
    hierarchy: Hierarchy
    candidate_report: list[dict[str, Any]]
    source_grade_report: list[dict[str, Any]]


def _summary_add(groups: dict, key: tuple[str, str], name: str, building: dict) -> None:
    summary = groups.setdefault(key, {
        "building_names": [], "area_m2": 0.0,
        "heat_loss_kw_per_c": 0.0, "thermal_capacity_kwh_per_c": 0.0,
        "members": {},
    })
    summary["building_names"].append(name)
    for field in ("area_m2", "heat_loss_kw_per_c", "thermal_capacity_kwh_per_c"):
        summary[field] += float(building[field])
    # Retain heterogeneous parameters and comfort/occupancy metadata verbatim.
    # Summed H and C must not replace the individual temperature states.
    summary["members"][name] = deepcopy(building)


def build_hierarchy(
    config: Mapping[str, Any], spatial: Mapping[str, Any],
    buildings: Sequence[Mapping[str, Any]] | None = None,
) -> Hierarchy:
    """Validate building -> node -> district -> system, with no inferred owners.

    C1/C2 fields ``name,node,heat_grade,area_m2,heat_loss_kw_per_c,
    thermal_capacity_kwh_per_c`` are consumed directly. Optional building
    ``district_id``/``system_id`` must agree with the explicit node mapping.
    ``building_id`` is allowed as metadata and must be unique when supplied.
    Extra building fields survive unchanged, including differing H/C and gains.
    """
    nodes = _named(config.get("nodes", []), "config node")
    grades = _named(config.get("grades", []), "grade")
    systems = _named(spatial.get("systems", []), "system")
    districts = _named(spatial.get("districts", []), "district")
    assignments = _named(spatial.get("nodes", []), "node assignment")
    if not systems or not districts:
        raise SpatialQualityError("at least one explicit system and district is required")
    if set(assignments) != set(nodes):
        raise SpatialQualityError(
            f"node assignments must exactly cover config nodes; "
            f"missing={sorted(set(nodes) - set(assignments))}, "
            f"unknown={sorted(set(assignments) - set(nodes))}"
        )
    district_to_system = {}
    for name, row in districts.items():
        if row.get("system") not in systems:
            raise SpatialQualityError(f"district {name} has unknown system {row.get('system')}")
        district_to_system[name] = row["system"]
    node_to_district = {}
    for name, row in assignments.items():
        if row.get("district") not in districts:
            raise SpatialQualityError(f"node {name} has unknown district {row.get('district')}")
        node_to_district[name] = row["district"]
    if buildings is None:
        buildings = config.get("building_thermal", {}).get("buildings", [])
    building_map = _named(buildings, "building")
    ids = [b["building_id"] for b in building_map.values() if "building_id" in b]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(ids) != len(set(ids)):
        raise SpatialQualityError("building_id must be nonempty and unique when supplied")
    node_grade, district_grade, system_grade = {}, {}, {}
    for name, building in building_map.items():
        node, grade = building.get("node"), building.get("heat_grade")
        if node not in nodes:
            raise SpatialQualityError(f"building {name} has unknown node {node}")
        if grade not in grades:
            raise SpatialQualityError(f"building {name} has unknown heat_grade {grade}")
        district = node_to_district[node]
        system = district_to_system[district]
        for field, expected in (("district_id", district), ("system_id", system)):
            if field in building and building[field] != expected:
                raise SpatialQualityError(f"building {name} {field} contradicts node ownership")
        for field in ("area_m2", "heat_loss_kw_per_c", "thermal_capacity_kwh_per_c"):
            if _number(building.get(field), f"building {name}.{field}", minimum=0) <= 0:
                raise SpatialQualityError(f"building {name}.{field} must be positive")
        _summary_add(node_grade, (node, grade), name, building)
        _summary_add(district_grade, (district, grade), name, building)
        _summary_add(system_grade, (system, grade), name, building)
    return Hierarchy(building_map, node_to_district, district_to_system,
                     tuple(grades), node_grade, district_grade, system_grade)


def aggregate_delivered_power(
    hierarchy: Hierarchy, building_power_kw: Mapping[str, Sequence[float]], *,
    fixed_service_power_kw: Mapping[tuple[str, str], Sequence[float]] | None = None,
) -> PowerAggregation:
    """Sum actual delivered building power once per layer and retain heat grade.

    Every registered building must have one equal-length finite nonnegative
    series. Optional fixed services (e.g. DHW) are separately metered node-grade
    deliveries, never the old reference building load or a parent-layer total.
    This is an accounting API, not a replacement for C2's separate RC states.
    """
    if set(building_power_kw) != set(hierarchy.buildings):
        raise SpatialQualityError("power series must exactly cover registered buildings")
    node_power: dict[tuple[str, str], np.ndarray] = {}
    size = None

    def add(key: tuple[str, str], values: Sequence[float]) -> None:
        nonlocal size
        array = np.asarray(values, dtype=float)
        if array.ndim != 1 or array.size == 0 or not np.isfinite(array).all() or (array < 0).any():
            raise SpatialQualityError("delivered power must be nonempty finite nonnegative 1D kW")
        if size is not None and len(array) != size:
            raise SpatialQualityError("delivered power series lengths differ")
        size = len(array)
        node_power[key] = node_power.get(key, np.zeros(size)) + array

    for name, values in building_power_kw.items():
        building = hierarchy.buildings[name]
        add((building["node"], building["heat_grade"]), values)
    for key, values in (fixed_service_power_kw or {}).items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise SpatialQualityError("fixed service key must be (node, heat_grade)")
        if key[0] not in hierarchy.node_to_district or key[1] not in hierarchy.grades:
            raise SpatialQualityError(f"unknown fixed service node/grade: {key}")
        add(key, values)
    district_power, system_power = {}, {}
    for (node, grade), values in node_power.items():
        key = hierarchy.node_to_district[node], grade
        district_power[key] = district_power.get(key, np.zeros_like(values)) + values
    for (district, grade), values in district_power.items():
        key = hierarchy.district_to_system[district], grade
        system_power[key] = system_power.get(key, np.zeros_like(values)) + values
    return PowerAggregation(node_power, district_power, system_power)


def heat_pump_conversion(
    source: Mapping[str, Any], supply_temperature_c: float, *,
    source_temperature_c: float | None = None, relative_humidity: float | None = None,
) -> dict[str, float]:
    """Existing clipped Carnot COP, including existing defrost/humidity factors.

    Per kWh of output, electricity=1/COP and resource heat=1-1/COP. This checks
    a heat-pump source, not a serial booster consuming another node's heat: the
    latter requires new model balances and is deliberately unsupported here.
    """
    if source.get("type") != "heat_pump":
        raise SpatialQualityError("temperature lifting requires type=heat_pump")
    supply = _number(supply_temperature_c, "supply_temperature_c", minimum=-273.14)
    if source_temperature_c is None:
        if source.get("source_temp_column"):
            raise SpatialQualityError("source_temp_column requires the current source temperature")
        source_temperature_c = source.get("source_temp_c", 10.0)
    ambient = _number(source_temperature_c, "source_temperature_c", minimum=-273.14)
    efficiency = _number(source.get("carnot_efficiency", 0.45), "carnot_efficiency")
    minimum = _number(source.get("cop_min", 1.2), "cop_min")
    maximum = _number(source.get("cop_max", 6.0), "cop_max")
    if not 0 < efficiency <= 1 or not 1 < minimum <= maximum:
        raise SpatialQualityError("heat pump requires 0 < carnot_efficiency <= 1 and 1 < cop_min <= cop_max")
    raw = efficiency * (supply + 273.15) / max(supply - ambient, 4.0)
    band = source.get("defrost_temperature_band_c")
    in_band = False
    if band is not None:
        if len(band) != 2:
            raise SpatialQualityError("defrost_temperature_band_c requires two ordered temperatures")
        lower = _number(band[0], "defrost lower temperature")
        upper = _number(band[1], "defrost upper temperature")
        if lower > upper:
            raise SpatialQualityError("defrost temperature band is reversed")
        in_band = lower <= ambient <= upper
    multiplier = _number(source.get("defrost_cop_multiplier", 1.0), "defrost_cop_multiplier")
    if not 0 < multiplier <= 1:
        raise SpatialQualityError("defrost_cop_multiplier must be in (0,1]")
    if in_band:
        raw *= multiplier
    if source.get("relative_humidity_column"):
        humidity = _number(relative_humidity, "relative humidity", minimum=0)
        threshold = _number(source.get("frost_humidity_threshold", 0.75), "frost_humidity_threshold")
        loss = _number(source.get("humidity_defrost_loss_per_unit", 0.30), "humidity_defrost_loss_per_unit", minimum=0)
        floor = _number(source.get("humidity_defrost_min_multiplier", 0.72), "humidity_defrost_min_multiplier")
        if humidity > 1 or not 0 <= threshold <= 1 or not 0 < floor <= 1:
            raise SpatialQualityError("humidity and threshold must be in [0,1], multiplier in (0,1]")
        if in_band and humidity > threshold:
            raw *= max(floor, 1.0 - loss * (humidity - threshold))
    cop = min(max(raw, minimum), maximum)
    return {"cop": cop, "electricity_kwh_per_kwh_heat": 1 / cop,
            "resource_heat_kwh_per_kwh_heat": 1 - 1 / cop}


def validate_conversion_dispatch(
    heat_output_kw: float, electricity_input_kw: float, cop: float, *,
    resource_input_kw: float | None = None, absolute_tolerance_kw: float = 1e-6,
) -> dict[str, float]:
    """Audit one heat-pump dispatch's electric and resource-heat balances."""
    heat = _number(heat_output_kw, "heat_output_kw", minimum=0)
    power = _number(electricity_input_kw, "electricity_input_kw", minimum=0)
    cop = _number(cop, "cop")
    tolerance = _number(absolute_tolerance_kw, "absolute_tolerance_kw", minimum=0)
    if cop <= 1:
        raise SpatialQualityError("heat-pump COP must exceed one")
    if not isclose(power, heat / cop, rel_tol=0, abs_tol=tolerance):
        raise SpatialQualityError("heat-pump electricity balance failed; lifting is not free")
    resource = heat - power if resource_input_kw is None else _number(resource_input_kw, "resource_input_kw", minimum=0)
    if not isclose(resource + power, heat, rel_tol=0, abs_tol=tolerance):
        raise SpatialQualityError("heat-pump resource heat balance failed")
    return {"heat_output_kw": heat, "electricity_input_kw": power, "resource_input_kw": resource}


def validate_source_grades(config: Mapping[str, Any], timeseries: Any = None) -> list[dict]:
    """Reject impossible direct output; validate every requested HP grade/time.

    ``timeseries`` may be a DataFrame or a mapping of equal-length sequences.
    Dynamic resource-temperature and humidity columns must actually be present.
    Direct supply temperature is constant because that is what the core uses.
    """
    grades = _named(config.get("grades", []), "grade")
    nodes = _named(config.get("nodes", []), "node")
    sources = _named(config.get("sources", []), "source")
    temperatures = {g: _number(row.get("temperature_c"), f"grade {g} temperature_c", minimum=-273.14)
                    for g, row in grades.items()}
    report = []
    for name, source in sources.items():
        if source.get("node") not in nodes:
            raise SpatialQualityError(f"source {name} has unknown node")
        allowed = source.get("allowed_grades", list(grades))
        if not isinstance(allowed, (list, tuple)) or len(allowed) != len(set(allowed)) or set(allowed) - set(grades):
            raise SpatialQualityError(f"source {name} has duplicate/unknown allowed_grades")
        if source.get("supply_temp_column"):
            raise SpatialQualityError("dynamic direct supply temperature is unsupported by the core")
        if source.get("type") != "heat_pump":
            supply = _number(source.get("supply_temp_c"), f"source {name}.supply_temp_c", minimum=-273.14)
            for grade in allowed:
                if supply < temperatures[grade]:
                    raise SpatialQualityError(f"source {name}: low-temperature direct supply cannot serve {grade} ({supply} < {temperatures[grade]} C)")
                report.append({"source": name, "grade": grade, "mode": "direct",
                               "supply_temp_c": supply, "required_temp_c": temperatures[grade]})
            continue
        columns = {}
        for field in ("source_temp_column", "relative_humidity_column"):
            column = source.get(field)
            if column:
                if timeseries is None or column not in timeseries:
                    raise SpatialQualityError(f"source {name} requires timeseries column {column}")
                values = np.asarray(timeseries[column], dtype=float)
                if values.ndim != 1 or not values.size or not np.isfinite(values).all():
                    raise SpatialQualityError(f"source {name} column {column} must be finite nonempty 1D")
                columns[field] = values
        if len({len(values) for values in columns.values()}) > 1:
            raise SpatialQualityError(f"source {name} timeseries column lengths differ")
        n = len(next(iter(columns.values()))) if columns else 1
        for grade in allowed:
            conversions = [heat_pump_conversion(
                source, temperatures[grade],
                source_temperature_c=float(columns["source_temp_column"][t]) if "source_temp_column" in columns else None,
                relative_humidity=float(columns["relative_humidity_column"][t]) if "relative_humidity_column" in columns else None,
            ) for t in range(n)]
            cops = [c["cop"] for c in conversions]
            report.append({"source": name, "grade": grade, "mode": "heat_pump",
                           "checked_steps": n, "cop_min": min(cops), "cop_max": max(cops),
                           "electricity_kwh_per_kwh_heat_min": 1 / max(cops),
                           "electricity_kwh_per_kwh_heat_max": 1 / min(cops)})
    return report


def preprocess_spatial_quality(
    config: Mapping[str, Any], spatial: Mapping[str, Any], *,
    buildings: Sequence[Mapping[str, Any]] | None = None, timeseries: Any = None,
) -> SpatialQualityResult:
    """Return a deep-copied config with inadmissible candidate pipes removed.

    Spatial schema v1 requires systems, districts, node coordinates in metres,
    and one candidate rule per config pipe. Route length = Euclidean distance
    * route_factor (>=1). Rules enforce max_route_length_m, connection_allowed,
    and optional cross-system permission. Cost mode ``per_m`` replaces each
    option's capex by fixed_yuan + route_length_m * yuan_per_m; ``preserve``
    retains its existing engineering estimate. Existing assets and required
    N-1 pipes are never silently removed: conflicts raise errors.

    Call once on the raw configuration after inheritance/overrides and before
    model construction. Grade-changing pipes and per-pipe grade restrictions
    unsupported by the current core are rejected instead of silently ignored.
    """
    if spatial.get("schema_version") != 1:
        raise SpatialQualityError("spatial schema_version must be 1")
    if spatial.get("coordinate_system") != "local_cartesian_m":
        raise SpatialQualityError("coordinate_system must explicitly be local_cartesian_m")
    hierarchy = build_hierarchy(config, spatial, buildings)
    source_report = validate_source_grades(config, timeseries)
    coordinates = {}
    for node in spatial["nodes"]:
        coordinates[node["name"]] = (_number(node.get("x_m"), "node x_m"), _number(node.get("y_m"), "node y_m"))
    pipes = _named(config.get("pipes", []), "pipe")
    rules = _named(spatial.get("candidate_rules", []), "candidate rule", key="pipe")
    if set(rules) != set(pipes):
        raise SpatialQualityError("candidate_rules must exactly cover config pipes")
    processed = deepcopy(dict(config))
    accepted, reports = [], []
    for name, pipe in pipes.items():
        rule = rules[name]
        start, end = pipe.get("from"), pipe.get("to")
        if start not in coordinates or end not in coordinates or start == end:
            raise SpatialQualityError(f"pipe {name} has unknown or identical endpoints")
        if "from_grade" in pipe or "to_grade" in pipe:
            raise SpatialQualityError(f"pipe {name}: grade-changing pipe fields are unsupported; a pipe cannot lift temperature")
        if "allowed_grades" in pipe and set(pipe["allowed_grades"]) != set(hierarchy.grades):
            raise SpatialQualityError(f"pipe {name}: per-pipe grade restrictions are unsupported by the core")
        factor = _number(rule.get("route_factor"), f"pipe {name} route_factor", minimum=1)
        limit = _number(rule.get("max_route_length_m"), f"pipe {name} max_route_length_m", minimum=0)
        permitted = rule.get("connection_allowed")
        cross_system = rule.get("allow_cross_system", False)
        if not isinstance(permitted, bool) or not isinstance(cross_system, bool):
            raise SpatialQualityError(f"pipe {name} connection permissions must be explicit booleans")
        a, b = coordinates[start], coordinates[end]
        direct = hypot(a[0] - b[0], a[1] - b[1])
        route = direct * factor
        if not isfinite(route):
            raise SpatialQualityError(f"pipe {name} route length overflow")
        systems = [hierarchy.district_to_system[hierarchy.node_to_district[n]] for n in (start, end)]
        reasons = []
        if not permitted:
            reasons.append("connection_not_permitted")
        if systems[0] != systems[1] and not cross_system:
            reasons.append("cross_system_not_permitted")
        if route > limit:
            reasons.append("exceeds_max_route_length")
        existing = _number(pipe.get("existing_capacity_kw", 0), f"pipe {name} existing_capacity_kw", minimum=0)
        if reasons and (existing > 0 or name in config.get("planning", {}).get("n_minus_1_pipe_names", [])):
            raise SpatialQualityError(f"pipe {name}: rule conflicts with installed asset or required N-1 pipe: {reasons}")
        mode = rule.get("cost_mode", "preserve")
        if mode not in {"preserve", "per_m"}:
            raise SpatialQualityError(f"pipe {name}: cost_mode must be preserve or per_m")
        options = _named(pipe.get("diameter_options", []), f"pipe {name} option")
        option_costs = {}
        if mode == "per_m":
            rates = _named(rule.get("option_costs", []), f"pipe {name} cost option")
            if set(rates) != set(options):
                raise SpatialQualityError(f"pipe {name}: option_costs must exactly cover diameter_options")
            for option_name, option in options.items():
                rate = rates[option_name]
                variable = _number(rate.get("yuan_per_m"), f"{name}/{option_name} yuan_per_m", minimum=0)
                fixed = _number(rate.get("fixed_yuan", 0), f"{name}/{option_name} fixed_yuan", minimum=0)
                option["capex_total_yuan"] = fixed + route * variable
        for option_name, option in options.items():
            option_costs[option_name] = _number(option.get("capex_total_yuan"), f"{name}/{option_name} capex_total_yuan", minimum=0)
        pipe["length_m"] = route
        pipe["diameter_options"] = list(options.values())
        if not reasons:
            accepted.append(pipe)
        reports.append({"pipe": name, "from": start, "to": end, "accepted": not reasons,
                        "reasons": reasons, "euclidean_distance_m": direct, "route_length_m": route,
                        "max_route_length_m": limit, "cost_mode": mode, "option_capex_yuan": option_costs})
    processed["pipes"] = accepted
    return SpatialQualityResult(processed, hierarchy, reports, source_report)
