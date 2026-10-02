"""Finite-mode thermo-hydraulic feedback for independently pumped star branches.

This deliberately bounded model couples both pipe legs, pump electricity and
temperature quality to the existing MILP without changing its frozen sources.
Modes are steady on-states; their hourly duty fractions deliver average heat.
No pipe transport/startup dynamics, shared-pump pressure balancing or general
meshed network claim is made. RC comfort under pulsed delivery is not certified.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Callable

import numpy as np
import pandas as pd

from .model import HeatPlanningModel, SolveArtifacts


def _number(obj, key, *, minimum=None, strict=False):
    if key not in obj or isinstance(obj[key], bool):
        raise ValueError(f"explicit numeric {key} required")
    value = float(obj[key])
    if not math.isfinite(value) or (minimum is not None and
            (value <= minimum if strict else value < minimum)):
        raise ValueError(f"invalid {key}")
    return value


def _friction(reynolds, relative_roughness):
    """Laminar law or Colebrook root; transition is explicitly unsupported."""
    if reynolds < 2300:
        return 64.0 / reynolds
    if reynolds < 4000:
        raise ValueError("transition Reynolds number 2300..4000 unsupported")
    lower, upper = 1., 30.
    def residual(x):
        return x + 2 * math.log10(relative_roughness / 3.7 + 2.51 * x / reynolds)
    if residual(lower) >= 0 or residual(upper) <= 0:
        raise ValueError("Colebrook root outside supported bracket")
    for _ in range(80):
        midpoint = (lower + upper) / 2
        if residual(midpoint) > 0:
            upper = midpoint
        else:
            lower = midpoint
    return ((lower + upper) / 2) ** -2


def mode_physics(branch, mode, fluid):
    """Instantaneous two-leg enthalpy balance, constant-property water model."""
    rho = _number(fluid, "density_kg_per_m3", minimum=0, strict=True)
    cp = _number(fluid, "cp_kj_per_kgk", minimum=0, strict=True)
    mu = _number(fluid, "viscosity_pa_s", minimum=0, strict=True)
    m = _number(mode, "mass_flow_kg_per_s", minimum=0, strict=True)
    ts = _number(branch, "supply_temperature_c")
    tr = _number(mode, "customer_return_temperature_c")
    tg = _number(branch, "ground_temperature_c")
    length = _number(branch, "one_way_length_m", minimum=0, strict=True)
    diameter = _number(branch, "inner_diameter_m", minimum=0, strict=True)
    rough = _number(branch, "roughness_m", minimum=0)
    u = _number(branch, "heat_loss_w_per_mk_per_leg", minimum=0)
    pump_eta = _number(branch, "pump_efficiency", minimum=0, strict=True)
    local_k = _number(branch, "pair_local_loss_coefficient", minimum=0)
    if not -273.15 < tg < tr < ts or pump_eta > 1 or rough / diameter >= .05:
        raise ValueError("unsupported temperature ordering, roughness or pump efficiency")
    capacity_rate = m * cp
    attenuation = math.exp(-u * length / 1000 / capacity_rate)
    far_supply = tg + (ts - tg) * attenuation
    plant_return = tg + (tr - tg) * attenuation
    q_in = capacity_rate * (ts - plant_return)
    q_out = capacity_rate * (far_supply - tr)
    if not 0 < q_out <= q_in or not math.isfinite(q_in):
        raise ValueError("mode must deliver positive heat with 0 < Qout <= Qin")
    velocity = m / rho / (math.pi * diameter**2 / 4)
    reynolds = rho * velocity * diameter / mu
    friction = _friction(reynolds, rough / diameter)
    dp_per_m = friction * rho * velocity**2 / (2 * diameter)
    pair_dp = 2 * length * dp_per_m + local_k * rho * velocity**2 / 2
    pump_kw = pair_dp * m / rho / pump_eta / 1000
    result = {"mass_flow_kg_per_s": m, "source_heat_kw": q_in,
        "delivered_heat_kw": q_out, "heat_loss_kw": q_in - q_out,
        "delivery_efficiency": q_out / q_in, "pump_kw": pump_kw,
        "far_supply_c": far_supply, "customer_return_c": tr,
        "plant_return_c": plant_return, "customer_delta_t_c": far_supply - tr,
        "velocity_m_per_s": velocity, "reynolds": reynolds,
        "darcy_friction_factor": friction, "pressure_drop_pa_per_m": dp_per_m,
        "pair_pressure_drop_pa": pair_dp}
    if not all(math.isfinite(v) for v in result.values()):
        raise ValueError("nonfinite mode physics")
    return result


def mode_violations(branch, physics, tolerance=1e-7):
    """Hard on-state engineering limits; never apply them to duty averages."""
    upper = {
        "velocity_m_per_s": "maximum_velocity_m_per_s",
        "pressure_drop_pa_per_m": "maximum_pressure_drop_pa_per_m",
        "pair_pressure_drop_pa": "maximum_pair_pressure_drop_pa",
        "pump_kw": "maximum_pump_power_kw",
        "source_heat_kw": "maximum_instantaneous_heat_input_kw",
        "customer_return_c": "maximum_customer_return_c",
        "plant_return_c": "maximum_plant_return_c",
    }
    reasons = [key for key, limit in upper.items()
               if physics[key] > _number(branch, limit) + tolerance]
    if physics["far_supply_c"] < _number(branch, "minimum_far_supply_c") - tolerance:
        reasons.append("far_supply_c")
    return reasons


@dataclass
class FeedbackArtifacts:
    model: HeatPlanningModel
    solution: SolveArtifacts
    mode_dispatch: pd.DataFrame
    history: list[dict]
    audit: dict
    catalog: list[dict]


class FeedbackFailure(RuntimeError):
    def __init__(self, reason, history):
        super().__init__(reason)
        self.history = history


def build_feedback_model(config, timeseries):
    """Build average-energy MILP and conservative simultaneous on-state limits.

    The supported physical domain is intentionally checked before mutation.
    There is one fixed-capacity, temperature-independent source and no RC,
    storage, cooling, demand shifting, policy or shared-pump interpretation.
    All physical branches have independent circulators and fixed geometries.
    """
    raw = deepcopy(config)
    allowed_sections = {"planning", "grades", "nodes", "sources", "pipes", "storages",
                        "demand_response", "network_feedback"}
    if set(raw) - allowed_sections:
        raise ValueError("unsupported feedback config sections: " + str(sorted(set(raw) - allowed_sections)))
    spec = raw.get("network_feedback")
    if not isinstance(spec, dict) or spec.get("mode") != "independent_star_steady_duty":
        raise ValueError("explicit independent_star_steady_duty network_feedback required")
    for flag in ("independent_branch_pumps", "steady_on_state_duty_approximation_acknowledged"):
        if spec.get(flag) is not True:
            raise ValueError(f"{flag} must be explicitly acknowledged")
    if raw.get("pipes") or raw.get("storages") or raw.get("policy") or raw.get("risk"):
        raise ValueError("feedback domain rejects pre-existing pipes/storage/policy/risk")
    if raw.get("building_thermal", {}).get("enabled") or raw.get("multi_energy_services"):
        raise ValueError("pulsed pipe delivery does not certify RC or cooling services")
    if raw.get("demand_response", {}).get("max_shift_fraction", 0) != 0:
        raise ValueError("demand shifting unsupported")
    if len(raw.get("sources", [])) != 1 or len(raw.get("grades", [])) != 1:
        raise ValueError("one fixed source and one heat grade required")
    source = raw["sources"][0]
    grade = raw["grades"][0]
    source_keys = {"name", "node", "type", "supply_temp_c", "existing_capacity_kw",
                  "max_new_capacity_kw", "max_retire_capacity_kw", "efficiency", "allowed_grades",
                  "variable_cost_yuan_per_kwh_heat", "variable_om_yuan_per_kwh_heat",
                  "emission_kg_per_kwh_heat", "direct_emission_kg_per_kwh_heat", "resource_kind",
                  "availability", "firm_capacity_credit", "firm_capacity_availability"}
    if set(source) - source_keys:
        raise ValueError("unsupported feedback source fields")
    if source.get("availability", 1.) != 1.:
        raise ValueError("source availability must equal one in the fixed on-state domain")
    if set(grade) != {"name", "temperature_c"}:
        raise ValueError("plain single temperature grade required")
    _number(grade, "temperature_c", minimum=-273.15, strict=True)
    if source.get("allowed_grades") != [grade["name"]]:
        raise ValueError("source must explicitly serve the single grade")
    for node in raw["nodes"]:
        if set(node) != {"name", "demand_columns"}:
            raise ValueError("node extensions unsupported in fixed branch service audit")
    if set(raw.get("demand_response", {})) - {"max_shift_fraction"}:
        raise ValueError("demand-response extensions unsupported")
    if source.get("type") not in {"electric_boiler", "boiler", "direct_heat"}:
        raise ValueError("temperature-dependent heat-pump/CHP source unsupported")
    for key in ("max_new_capacity_kw", "max_retire_capacity_kw"):
        if float(source.get(key, 0)) != 0:
            raise ValueError("fixed source capacity required")
    if any(key in source for key in ("availability_column", "availability_derating", "temperature_derating", "fuel_capacity_column")):
        raise ValueError("time-dependent source availability unsupported")
    planning = raw["planning"]
    planning_keys = {"discount_rate", "asset_lifetime_years", "time_step_hours",
        "representative_day_weight", "max_unserved_rate", "max_scenario_unserved_rate",
        "unserved_heat_penalty_yuan_per_kwh", "firm_capacity_reserve_margin", "carbon_price_yuan_per_kg",
        "exergy_penalty_yuan_per_kwh", "ambient_reference_c"}
    if set(planning) - planning_keys:
        raise ValueError("unsupported feedback planning fields")
    for key in planning:
        _number(planning, key)
    if _number(planning, "time_step_hours", minimum=0, strict=True) != 1:
        raise ValueError("feedback reference requires one-hour chronology")
    if planning.get("max_unserved_rate") != 0 or planning.get("max_scenario_unserved_rate") != 0:
        raise ValueError("hard full service required")
    _number(planning, "discount_rate", minimum=0, strict=True)
    lifetime = _number(planning, "asset_lifetime_years", minimum=0, strict=True)
    if lifetime != int(lifetime):
        raise ValueError("asset lifetime must be integer")
    if timeseries.empty:
        raise ValueError("nonempty hourly chronology required")
    stamps = pd.DatetimeIndex(pd.to_datetime(timeseries["time"], errors="raise"))
    if stamps.tz is None or stamps.has_duplicates or (len(stamps) > 1 and
            not np.all(np.diff(stamps.tz_convert("UTC").asi8) == 3600 * 10**9)):
        raise ValueError("explicit timezone and contiguous one-hour chronology required")
    for field in ("electricity_price", "grid_emission_factor"):
        if not np.isfinite(timeseries[field].to_numpy(float)).all():
            raise ValueError(f"finite {field} required")
    if planning.get("n_minus_1_pipe_names") or planning.get("n_minus_1_source_names") or planning.get("enforce_n_minus_one"):
        raise ValueError("virtual modes must not be interpreted as N-1 physical pipes")
    if raw.get("electricity_system") or raw.get("electricity_tariff") or raw.get("microgrid"):
        raise ValueError("use explicit instantaneous reference connection limit for this domain")
    plant_capacity = _number(source, "existing_capacity_kw", minimum=0, strict=True)
    source_temp = _number(source, "supply_temp_c")
    if source.get("type") == "electric_boiler":
        efficiency = _number(source, "efficiency", minimum=0, strict=True)
        if efficiency > 1:
            raise ValueError("electric resistance efficiency must be <= 1")
        source_electric_ratio = 1 / efficiency
    else:
        source_electric_ratio = 0.
    connection = _number(spec, "maximum_simultaneous_electricity_kw", minimum=0, strict=True)
    fluid = spec["water"]
    if set(fluid) != {"density_kg_per_m3", "cp_kj_per_kgk", "viscosity_pa_s"}:
        raise ValueError("only explicit constant water properties supported")
    branches = spec.get("branches", [])
    if not branches or len({str(b["name"]) for b in branches}) != len(branches):
        raise ValueError("nonempty uniquely named branches required")
    node_names = {node["name"] for node in raw["nodes"]}
    if len(node_names) != len(raw["nodes"]):
        raise ValueError("unique node identities required")
    destinations = [b["to"] for b in branches]
    if len(set(destinations)) != len(destinations):
        raise ValueError("one independent branch per demand node required")
    if set(destinations) | {source["node"]} != node_names:
        raise ValueError("star must cover all demand nodes")
    plant = next(node for node in raw["nodes"] if node["name"] == source["node"])
    if plant.get("demand_columns"):
        raise ValueError("source plant must have no local heat demand")
    for node in raw["nodes"]:
        if node["name"] == source["node"]:
            continue
        if set(node["demand_columns"]) != {grade["name"]}:
            raise ValueError("exactly one explicit demand column per customer required")
        demand = timeseries[node["demand_columns"][grade["name"]]].to_numpy(float)
        if not np.isfinite(demand).all() or np.any(demand < 0):
            raise ValueError("finite nonnegative customer heat demand required")
    catalog, pipes = [], []
    for b in branches:
        branch_keys = {"name", "from", "to", "one_way_length_m", "inner_diameter_m", "roughness_m",
            "heat_loss_w_per_mk_per_leg", "supply_temperature_c", "ground_temperature_c", "pump_efficiency",
            "pair_local_loss_coefficient", "minimum_far_supply_c", "maximum_customer_return_c",
            "maximum_plant_return_c", "maximum_velocity_m_per_s", "maximum_pressure_drop_pa_per_m",
            "maximum_pair_pressure_drop_pa", "maximum_pump_power_kw", "maximum_instantaneous_heat_input_kw",
            "target_customer_delta_t_c", "delta_t_soft_band_c", "delta_t_penalty_yuan_per_c_on_hour", "operating_modes"}
        if set(b) != branch_keys:
            raise ValueError("unsupported or missing explicit branch contract fields")
        if b.get("from") != source["node"] or b["to"] == source["node"]:
            raise ValueError("only direct plant-to-customer star branches supported")
        if _number(b, "supply_temperature_c") != source_temp:
            raise ValueError("branch supply and source supply temperature must match")
        if _number(b, "minimum_far_supply_c") < float(grade["temperature_c"]):
            raise ValueError("far supply minimum must meet the core service grade")
        target = _number(b, "target_customer_delta_t_c", minimum=0, strict=True)
        soft_band = _number(b, "delta_t_soft_band_c", minimum=0)
        penalty = _number(b, "delta_t_penalty_yuan_per_c_on_hour", minimum=0)
        modes = b.get("operating_modes", [])
        if not modes or len({str(m["name"]) for m in modes}) != len(modes):
            raise ValueError("nonempty unique branch operating modes required")
        for number, mode in enumerate(modes):
            if set(mode) != {"name", "mass_flow_kg_per_s", "customer_return_temperature_c"}:
                raise ValueError("only fixed mass-flow/return-temperature modes supported")
            physics = mode_physics(b, mode, fluid)
            # Validate hard limit identity now, without rejecting a physically
            # defined but engineering-invalid mode before the feedback step.
            violations = mode_violations(b, physics)
            virtual = f"feedback_mode_{len(catalog)}"
            deviation = max(abs(physics["customer_delta_t_c"] - target) - soft_band, 0.)
            catalog.append({"branch": b["name"], "mode": mode["name"], "virtual_pipe": virtual,
                "branch_index": branches.index(b), "mode_index": number, **physics,
                "delta_t_excess_c": deviation, "soft_penalty_yuan_per_on_hour": penalty * deviation,
                "engineering_violations": violations})
            pipes.append({"name": virtual, "from": b["from"], "to": b["to"],
                "existing_capacity_kw": physics["source_heat_kw"], "diameter_options": [],
                "delivery_efficiency": physics["delivery_efficiency"],
                "pumping_electricity_kwh_per_kwh_heat": physics["pump_kw"] / physics["source_heat_kw"]})
    raw["pipes"] = pipes
    model = HeatPlanningModel(raw, timeseries)
    if len(model.scenarios) != 1 or any(w != 1 for w in model._period_weight_by_time):
        raise ValueError("continuous single-scenario, unweighted reference required")
    model.build()
    flags = {}
    for t in model.times:
        heat_instant, electric_instant = {}, {}
        for b in branches:
            switches = {}
            for entry in catalog:
                if entry["branch"] != b["name"]:
                    continue
                key = (entry["virtual_pipe"], grade["name"], t)
                q = model.variables["pipe_flow_forward"][key]
                z = model.problem.add_var(f"network_on[{entry['virtual_pipe']},{t}]", ub=1, integer=True)
                flags[(entry["virtual_pipe"], t)] = z
                model.variables["network_mode_on"][(entry["virtual_pipe"], t)] = z
                model.problem.add_constraint({q: 1., z: -entry["source_heat_kw"]}, ub=0.)
                switches[z] = 1.
                heat_instant[z] = entry["source_heat_kw"]
                electric_instant[z] = source_electric_ratio * entry["source_heat_kw"] + entry["pump_kw"]
                model._record_time_component("network_delta_t_soft_penalty", q,
                    entry["soft_penalty_yuan_per_on_hour"] / entry["source_heat_kw"], t)
            model.problem.add_constraint(switches, ub=1.)
        # All chosen branch on-states can operate simultaneously. This is
        # conservative but removes the otherwise unproved intra-hour packing.
        model.problem.add_constraint(heat_instant, ub=plant_capacity)
        model.problem.add_constraint(electric_instant, ub=connection)
    return model, catalog, flags


def audit_feedback_solution(config, model, solution, catalog, tolerance=1e-6):
    """Recompute all used on-state physics and hourly enthalpy/pump ledgers."""
    if isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("positive finite audit tolerance required")
    spec = config["network_feedback"]
    expected_catalog = []
    for bi, branch in enumerate(spec["branches"]):
        for mi, mode in enumerate(branch["operating_modes"]):
            expected_catalog.append({"virtual_pipe": f"feedback_mode_{len(expected_catalog)}",
                "branch_index": bi, "mode_index": mi, "branch": branch["name"], "mode": mode["name"]})
    identity_fields = ("virtual_pipe", "branch_index", "mode_index", "branch", "mode")
    if len(catalog) != len(expected_catalog) or any(
            any(actual.get(key) != expected[key] for key in identity_fields)
            for actual, expected in zip(catalog, expected_catalog)):
        raise ValueError("catalog physical branch/mode identity mismatch")
    numerical = model.problem.audit_solution(solution.values)
    if not numerical["numerical_solution_finite"]:
        raise ValueError("full solution vector shape/finiteness invalid")
    export_checks = []
    # Verify the exported variables themselves, not the producer's stored
    # boolean. Missing/duplicate rows and forged powers invalidate the audit.
    for category in ("source_heat", "pipe_flow_forward"):
        records = solution.dispatch[solution.dispatch.category == category]
        expected = model.variables[category]
        export_checks.append(len(records) == len(expected))
        observed = set()
        for row in records.itertuples():
            key = (row.asset, row.grade, int(row.time_index))
            export_checks.append(key in expected and key not in observed)
            observed.add(key)
            if key in expected:
                export_checks.append(math.isfinite(float(row.value)) and abs(float(row.value) - solution.values[expected[key]]) <= tolerance)
                expected_node = (model.sources[row.asset]["node"] if category == "source_heat"
                                 else model.pipes[row.asset]["from"])
                export_checks.append(row.node == expected_node and row.unit == "kW"
                    and row.time_index == key[-1] and str(row.time) == str(model._ts_value(key[-1], "time"))
                    and str(row.scenario) == model._scenario(key[-1])
                    and str(row.boundary_group) == model.time_boundary_group[key[-1]])
    entries = {entry["virtual_pipe"]: entry for entry in catalog}
    exported = solution.dispatch.query("category == 'pipe_flow_forward'")
    rows, rejected = [], set()
    simultaneous_heat = np.zeros(len(model.times))
    simultaneous_electric = np.zeros(len(model.times))
    mode_switches = []
    source = config["sources"][0]
    electricity_ratio = 1 / float(source["efficiency"]) if source["type"] == "electric_boiler" else 0.
    for row in exported.itertuples():
        entry = entries[row.asset]
        branch = spec["branches"][entry["branch_index"]]
        physics = mode_physics(branch, branch["operating_modes"][entry["mode_index"]], spec["water"])
        excess = max(abs(physics["customer_delta_t_c"] - _number(branch, "target_customer_delta_t_c"))
                     - _number(branch, "delta_t_soft_band_c"), 0.)
        penalty_rate = excess * _number(branch, "delta_t_penalty_yuan_per_c_on_hour")
        duty = float(row.value) / physics["source_heat_kw"]
        switch = float(solution.values[model.variables["network_mode_on"][(row.asset, int(row.time_index))]])
        mode_switches.append(duty <= switch + tolerance)
        simultaneous_heat[int(row.time_index)] += switch * physics["source_heat_kw"]
        simultaneous_electric[int(row.time_index)] += switch * (electricity_ratio * physics["source_heat_kw"] + physics["pump_kw"])
        reasons = mode_violations(branch, physics) if duty > tolerance else []
        if reasons:
            rejected.add(row.asset)
        rows.append({"time_index": int(row.time_index), "time": row.time,
            "branch": entry["branch"], "mode": entry["mode"], "virtual_pipe": row.asset,
            "duty_fraction": duty, "source_heat_kw": float(row.value),
            "delivered_heat_kw": duty * physics["delivered_heat_kw"],
            "heat_loss_kw": duty * physics["heat_loss_kw"], "pump_kw": duty * physics["pump_kw"],
            "instantaneous_far_supply_c": physics["far_supply_c"],
            "instantaneous_customer_return_c": physics["customer_return_c"],
            "instantaneous_plant_return_c": physics["plant_return_c"],
            "instantaneous_velocity_m_per_s": physics["velocity_m_per_s"],
            "instantaneous_pressure_drop_pa_per_m": physics["pressure_drop_pa_per_m"],
            "delta_t_excess_c_on_hours": duty * excess * model.dt,
            "soft_penalty_yuan": duty * penalty_rate * model.dt,
            "engineering_passed": not reasons, "reasons": ";".join(reasons)})
    frame = pd.DataFrame(rows)
    duty_group = frame.groupby(["branch", "time_index"])
    branch_checks = {}
    for branch in spec["branches"]:
        node = next(n for n in config["nodes"] if n["name"] == branch["to"])
        expected = model.ts[node["demand_columns"][config["grades"][0]["name"]]].to_numpy(float)
        delivered = frame[frame.branch == branch["name"]].groupby("time_index").delivered_heat_kw.sum().reindex(model.times).to_numpy()
        branch_checks[branch["name"]] = float(np.max(np.abs(delivered - expected))) <= tolerance
    heat_rows = solution.dispatch.query("category == 'source_heat'").groupby("time_index").value.sum().reindex(model.times).to_numpy()
    from_pipes = frame.groupby("time_index").source_heat_kw.sum().reindex(model.times).to_numpy()
    source = config["sources"][0]
    source_electric = heat_rows / float(source["efficiency"]) if source["type"] == "electric_boiler" else np.zeros(len(model.times))
    pumps = frame.groupby("time_index").pump_kw.sum().reindex(model.times).to_numpy()
    electric_kwh = float((source_electric + pumps).sum() * model.dt)
    soft_total = float(frame.soft_penalty_yuan.sum())
    soft_core = model._dot(model.component_coefficients["network_delta_t_soft_penalty"], solution.values)
    objective_rebuilt = float(np.asarray(model.problem.objective) @ solution.values)
    checks = {"engineering_modes": not rejected, "duty_bounds": bool(frame.duty_fraction.min() >= -tolerance and duty_group.duty_fraction.sum().max() <= 1 + tolerance),
        "model_contract_identity": all(model.config.get(key) == value for key, value in config.items() if key != "pipes"),
        "one_mode_per_branch_hour": bool(duty_group.duty_fraction.apply(lambda x: (x > tolerance).sum()).max() <= 1),
        "source_heat_ledger": bool(np.max(np.abs(heat_rows - from_pipes)) <= tolerance),
        "branch_service_ledgers": all(branch_checks.values()),
        "electricity_including_pumps": abs(electric_kwh - solution.summary["electricity_kwh_per_year"]) <= tolerance * max(1., electric_kwh),
        "solver_numerical": bool(numerical["numerical_audit_passed"]),
        "export_matches_values": all(export_checks),
        "duty_linked_to_on_mode": all(mode_switches),
        "simultaneous_source_capacity": bool(simultaneous_heat.max() <= float(source["existing_capacity_kw"]) + tolerance),
        "simultaneous_electric_connection": bool(simultaneous_electric.max() <= float(spec["maximum_simultaneous_electricity_kw"]) + tolerance),
        "soft_penalty_ledger": abs(soft_total - soft_core) <= tolerance * max(1., soft_total),
        "objective_summary_matches_values": abs(objective_rebuilt - float(solution.summary["objective_yuan_per_year"])) <= tolerance * max(1., abs(objective_rebuilt)),
        "carbon_cap": bool(solution.summary["emissions_cap_pass"])}
    active = frame.duty_fraction > tolerance
    return frame, {"passed": all(checks.values()), "checks": checks, "branch_service": branch_checks,
        "invalid_modes": sorted(rejected), "source_heat_kwh": float(from_pipes.sum() * model.dt),
        "delivered_heat_kwh": float(frame.delivered_heat_kw.sum() * model.dt),
        "heat_loss_kwh": float(frame.heat_loss_kw.sum() * model.dt),
        "pump_kwh": float(pumps.sum() * model.dt), "electricity_kwh": electric_kwh,
        "delta_t_excess_degree_on_hours": float(frame.delta_t_excess_c_on_hours.sum()),
        "soft_penalty_yuan": float(frame.soft_penalty_yuan.sum()),
        "minimum_active_far_supply_c": float(frame.loc[active, "instantaneous_far_supply_c"].min()) if active.any() else None,
        "maximum_active_pressure_drop_pa_per_m": float(frame.loc[active, "instantaneous_pressure_drop_pa_per_m"].max()) if active.any() else None,
        "field_validation": False, "rc_comfort_certified": False,
        "scope": "finite steady on-modes, independent star pumps, average hourly heat, conservative simultaneous source/connection capacities"}


def solve_with_network_feedback(config, timeseries, *, mip_gap=0., time_limit=60.,
                                max_rounds=None, on_iteration: Callable | None = None):
    model, catalog, flags = build_feedback_model(config, timeseries)
    if max_rounds is None:
        max_rounds = len(catalog) + 1
    if isinstance(max_rounds, bool) or int(max_rounds) != max_rounds or max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer")
    if not math.isfinite(time_limit) or time_limit <= 0 or not math.isfinite(mip_gap) or not 0 <= mip_gap < 1:
        raise ValueError("invalid solve budget/gap")
    history, eliminated = [], set()
    for iteration in range(int(max_rounds)):
        try:
            solution = model.solve(time_limit=time_limit, mip_gap=mip_gap)
            dispatch, audit = audit_feedback_solution(config, model, solution, catalog)
        except Exception as error:
            record = {"iteration": iteration, "status": "failed", "type": type(error).__name__, "reason": str(error),
                      "solver_evidence": getattr(error, "summary", None), "previous_cuts": sorted(eliminated)}
            history.append(record)
            if on_iteration:
                on_iteration(record, None, None)
            raise FeedbackFailure(str(error), history) from error
        invalid = set(audit["invalid_modes"])
        record = {"iteration": iteration, "status": "accepted" if audit["passed"] else "rejected",
            "objective_yuan": solution.summary["objective_yuan_per_year"],
            "solver_status": solution.summary["solver_status"], "mip_gap": solution.summary.get("mip_gap"),
            "solution_quality": solution.summary["solution_quality"], "audit": audit,
            "added_mode_cuts": sorted(invalid - eliminated), "previous_cuts": sorted(eliminated)}
        history.append(record)
        if on_iteration:
            on_iteration(record, solution, dispatch)
        if audit["passed"]:
            solution.summary["network_feedback"] = {"iterations": iteration + 1, "eliminated_modes": sorted(eliminated),
                "audit": audit, "optimality_domain": "finite catalog MILP within recorded solver tolerances; not continuous nonlinear network optimum",
                "soft_penalty_is_not_cash_cost": True}
            return FeedbackArtifacts(model, solution, dispatch, history, audit, catalog)
        if not invalid - eliminated:
            raise FeedbackFailure("non-engineering audit failure; no valid mode cut available", history)
        for virtual in sorted(invalid - eliminated):
            for t in model.times:
                model.problem.add_constraint({flags[virtual, t]: 1.}, ub=0.)
        eliminated.update(invalid)
    raise FeedbackFailure("feedback iteration limit reached without engineering acceptance", history)
