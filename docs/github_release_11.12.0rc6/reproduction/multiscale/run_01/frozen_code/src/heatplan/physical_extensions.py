"""Optional physical resource, event-state and monthly electricity constraints.

These interfaces carry no engineering parameter defaults: their activation must
be supported by a case-specific source ledger. Short synthetic tests verify the
equations only. They are not evidence about an actual city's costs or assets.
"""
from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

import numpy as np
import pandas as pd


def _nonnegative(value, label):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label} must be a number, not a boolean")
    number = float(value)
    if not isfinite(number) or number < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return number


def add_shared_heat_resources(model):
    """Bound upstream heat drawn jointly by direct exchangers and heat pumps.

    Q_upstream = Q_direct / eta + Q_HP * (1 - 1/COP) / eta.
    eta is the explicitly declared resource-to-device transfer efficiency.
    Compressor electricity is excluded. Cooling/rejection is not allocated to
    this pool; such sources require a separate, explicitly defined resource model.
    """
    model.shared_heat_resource_rows = []
    names, assigned = set(), set()
    for pool in model.config.get("shared_heat_resources", []):
        name = str(pool["name"])
        members = list(pool["source_names"])
        if not name.strip() or name in names or not members or len(members) != len(set(members)):
            raise ValueError("shared heat resource names/members must be unique and nonempty")
        names.add(name)
        if set(members) - set(model.sources) or assigned.intersection(members):
            raise ValueError(f"resource {name}: unknown source or source assigned to multiple pools")
        assigned.update(members)
        scalar = "available_heat_kw" in pool
        column = "available_heat_column" in pool
        if scalar == column:
            raise ValueError(f"resource {name}: specify exactly one available_heat_kw/column")
        efficiency = _nonnegative(pool["transfer_efficiency"], f"resource {name} efficiency")
        if not 0 < efficiency <= 1:
            raise ValueError("resource transfer_efficiency must be in (0, 1]")
        for source_name in members:
            source = model.sources[source_name]
            if source.get("type") not in {"direct_heat", "heat_pump"}:
                raise ValueError(f"resource {name}: only direct_heat and heat_pump are supported")
            if any(key[0] == source_name for key in model.variables["cooling_output"]) or source.get("heat_rejection_enabled"):
                raise ValueError(f"resource {name}: cooling/rejection needs a separate resource balance")
        for t in model.times:
            available = _nonnegative(
                model._ts_value(t, pool["available_heat_column"]) if column else pool["available_heat_kw"],
                f"resource {name} available heat at {t}",
            )
            terms = {}
            for source_name in members:
                source = model.sources[source_name]
                for grade_name, grade in model.grades.items():
                    idx = model.variables["source_heat"].get((source_name, grade_name, t))
                    if idx is None:
                        continue
                    fraction = 1.0
                    if source.get("type") == "heat_pump":
                        cop = float(model._cop(source, grade, t))
                        if not isfinite(cop) or cop <= 1:
                            raise ValueError("a resource heat pump requires finite COP > 1")
                        fraction -= 1.0 / cop
                    model._add(terms, idx, fraction / efficiency)
            use = model.problem.add_var(f"shared_heat_resource_use[{name},{t}]", ub=available)
            model.variables["shared_heat_resource_use"][(name, t)] = use
            model.problem.add_constraint({use: 1.0, **{i: -c for i, c in terms.items()}}, lb=0.0, ub=0.0)
            model.shared_heat_resource_rows.append((name, t, available, terms, use))


def add_firm_resource_limits(model, source_dispatch, label):
    """Conservative resource limits for static firm-heat portfolio checks.

    Availability uses the minimum in the declared horizon. Extraction uses the
    largest upstream-heat fraction across declared grades/hours (highest COP),
    independently of the worst electricity fraction used by the grid module.
    This is a conservative static adequacy check, not a dynamic outage proof.
    """
    if not hasattr(model, "_firm_resource_specs"):
        model._firm_resource_specs = []
        for pool in model.config.get("shared_heat_resources", []):
            available = (min(float(model._ts_value(t, pool["available_heat_column"])) for t in model.times)
                         if "available_heat_column" in pool else float(pool["available_heat_kw"]))
            fractions = {}
            for name in pool["source_names"]:
                source = model.sources[name]
                fraction = 1.
                if source.get("type") == "heat_pump":
                    fraction = max(1. - 1. / model._cop(source, model.grades[g], t)
                                   for g in source.get("allowed_grades", model.grades) for t in model.times)
                fractions[name] = fraction / float(pool["transfer_efficiency"])
            model._firm_resource_specs.append((pool["name"], available, fractions))
    records = getattr(model, "firm_resource_checks", [])
    for name, available, fractions in model._firm_resource_specs:
        row = {source_dispatch[s]: factor for s, factor in fractions.items() if s in source_dispatch}
        model.problem.add_constraint(row, ub=available)
        records.append((label, name, available, row))
    model.firm_resource_checks = records


def add_resource_firm_portfolios(model, source_firm, requirement):
    if not model.config.get("shared_heat_resources"):
        return
    cases = [("base", None)] + [(f"source_outage:{name}", str(name))
                                for name in model.config["planning"].get("n_minus_1_source_names", [])]
    for label, outage in cases:
        dispatch, heat = {}, {}
        for name, (existing, cap_idx, retire_idx, credit) in source_firm.items():
            idx = model.problem.add_var(f"resource_firm_dispatch[{label},{name}]", ub=0. if outage == name else np.inf)
            model.variables["resource_firm_dispatch"][(label, name)] = idx
            dispatch[name], heat[idx] = idx, 1.
            if outage == name:
                continue
            row = {idx: 1.}
            if cap_idx is not None: row[cap_idx] = -credit
            if retire_idx is not None: row[retire_idx] = credit
            model.problem.add_constraint(row, ub=existing)
        for name, idx in model.variables["storage_firm_capacity"].items():
            heat[idx] = float(model.storages[name].get("firm_capacity_credit", 1.))
        model.problem.add_constraint(heat, lb=requirement)
        add_firm_resource_limits(model, dispatch, f"system:{label}")


class EventStateBoundary:
    """Explicit event boundaries for fuel inventory and electrical storage.

    Legacy cyclic operation is retained when no fixed boundary is requested.
    Fixed mode requires initial stock and at least one terminal bound. Values
    may be scalar or a mapping covering exactly the model's boundary groups.
    """

    def __init__(self, model, specification, *, label, mode_key, initial_key,
                 minimum_key, maximum_key, existing_capacity, capacity_index):
        self.model, self.label = model, label
        self.mode = str(specification.get(mode_key, "cyclic"))
        self.initial, self.minimum, self.maximum = {}, {}, {}
        self.starts = {times[0]: group for group, times in model.boundary_groups.items()}
        if self.mode not in {"cyclic", "fixed"}:
            raise ValueError(f"{label}: boundary mode must be cyclic or fixed")
        keys = (initial_key, minimum_key, maximum_key)
        if self.mode == "cyclic":
            if any(key in specification for key in keys):
                raise ValueError(f"{label}: fixed state values require fixed boundary mode")
            return
        if initial_key not in specification or not any(key in specification for key in keys[1:]):
            raise ValueError(f"{label}: fixed mode requires initial state and terminal bound")
        if any(abs(w - 1.0) > 1e-12 for w in model._period_weight_by_time):
            raise ValueError(f"{label}: fixed event states require unweighted chronology")
        groups = set(model.boundary_groups)
        if "time" not in model.ts or pd.api.types.is_numeric_dtype(model.ts["time"]):
            raise ValueError(f"{label}: fixed event states require actual datetime timestamps")
        stamps = pd.to_datetime(model.ts["time"], errors="raise")
        if stamps.isna().any():
            raise ValueError(f"{label}: missing event timestamps")

        def expand(key):
            if key not in specification:
                return {group: None for group in groups}
            value = specification[key]
            if isinstance(value, Mapping):
                if set(value) != groups:
                    raise ValueError(f"{label}: {key} must cover exactly the boundary groups")
                return {group: _nonnegative(value[group], key) for group in groups}
            return {group: _nonnegative(value, key) for group in groups}

        self.initial, self.minimum, self.maximum = map(expand, keys)
        for group, times in model.boundary_groups.items():
            if len({model._scenario(t) for t in times}) != 1:
                raise ValueError(f"{label}: a fixed event cannot cross scenarios")
            steps = stamps.iloc[times].diff().iloc[1:].dt.total_seconds().to_numpy() / 3600.
            if times != list(range(times[0], times[-1] + 1)) or not np.allclose(steps, model.dt, rtol=0, atol=1e-9):
                raise ValueError(f"{label}: fixed event timestamps must be contiguous at time_step_hours")
            lo, hi = self.minimum[group], self.maximum[group]
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{label}: terminal minimum exceeds maximum")
            initial = self.initial[group]
            if capacity_index is None:
                if initial > existing_capacity + 1e-9:
                    raise ValueError(f"{label}: initial state exceeds installed capacity")
            else:
                model.problem.add_constraint({capacity_index: 1.0}, lb=initial - existing_capacity)

    def add_predecessor(self, row, t, indices, retention):
        if not isfinite(retention) or not 0 <= retention <= 1:
            raise ValueError(f"{self.label}: retention must be in [0, 1]")
        if self.mode == "fixed" and t in self.starts:
            return retention * self.initial[self.starts[t]]
        self.model._add(row, indices[self.model.time_predecessor[t]], -retention)
        return 0.0

    def finish(self, indices):
        if self.mode != "fixed":
            return
        records = getattr(self.model, "event_state_boundaries", [])
        for group, times in self.model.boundary_groups.items():
            lo, hi = self.minimum[group], self.maximum[group]
            idx = indices[times[-1]]
            self.model.problem.add_constraint({idx: 1.0}, lb=-np.inf if lo is None else lo,
                                              ub=np.inf if hi is None else hi)
            records.append({"asset": self.label, "boundary_group": group,
                            "initial_state": self.initial[group], "terminal_min": lo,
                            "terminal_max": hi, "terminal_index": idx})
        self.model.event_state_boundaries = records


def _grid_import_row(model, t):
    row = model._electric_load_row(t)
    for category, coefficient in (("microgrid_battery_charge", 1.0),
                                  ("microgrid_battery_discharge", -1.0),
                                  ("microgrid_renewable_use", -1.0),
                                  ("microgrid_backup_generation", -1.0),
                                  ("renewable_electricity_use", -1.0)):
        idx = model.variables[category].get(t)
        if idx is not None:
            model._add(row, idx, coefficient)
    return row


def add_monthly_electricity_tariff(model):
    """Add monthly maximum-import charges or a fixed kVA contract charge.

    The modeled meter excludes unrelated building electricity and exported CHP.
    Complete calendar months are required unless the caller explicitly declares
    a diagnostic modeled-hours-only scope. No period-weight scaling of peaks.
    """
    tariff = model.config.get("electricity_tariff")
    model.monthly_tariff_records = []
    if not tariff:
        return
    if tariff.get("meter_scope") != "modeled_energy_system_import":
        raise ValueError("electricity_tariff must declare meter_scope=modeled_energy_system_import")
    method = tariff.get("method")
    if method not in {"monthly_demand", "monthly_capacity"}:
        raise ValueError("electricity_tariff method must be monthly_demand or monthly_capacity")
    if model.variables["cooling_output"] and "heating_cost_allocation_fraction" not in tariff:
        raise ValueError("joint/cooling service billing requires explicit heating_cost_allocation_fraction")
    heating_share = _nonnegative(tariff.get("heating_cost_allocation_fraction", 1.), "heating billing allocation")
    if heating_share > 1:
        raise ValueError("heating_cost_allocation_fraction must be in [0, 1]")
    if any(abs(w - 1.0) > 1e-12 for w in model._period_weight_by_time):
        raise ValueError("monthly billing requires unweighted chronology, not representative-day weights")
    coverage = tariff.get("coverage_policy", "require_complete_months")
    if coverage not in {"require_complete_months", "modeled_hours_only"}:
        raise ValueError("unknown monthly billing coverage policy")
    column = str(tariff.get("timestamp_column", "time"))
    if column not in model.ts or pd.api.types.is_numeric_dtype(model.ts[column]):
        raise ValueError("monthly billing requires actual datetime timestamps")
    stamps = pd.to_datetime(model.ts[column], errors="raise")
    if stamps.isna().any():
        raise ValueError("monthly billing timestamps cannot be missing")
    rate_key = "rate_yuan_per_kw_month" if method == "monthly_demand" else "rate_yuan_per_kva_month"
    rate = tariff[rate_key]
    month_keys = set(stamps.dt.strftime("%Y-%m"))
    if isinstance(rate, Mapping) and set(rate) != month_keys:
        raise ValueError("monthly rate mapping must exactly cover modeled months")
    capacity = power_factor = None
    if method == "monthly_capacity":
        capacity = _nonnegative(tariff["contract_capacity_kva"], "contract kVA")
        power_factor = _nonnegative(tariff["power_factor"], "power factor")
        if not 0 < power_factor <= 1:
            raise ValueError("power_factor must be in (0, 1]")
    for scenario, times in model.scenario_times.items():
        series = stamps.iloc[times]
        if series.duplicated().any() or not series.is_monotonic_increasing:
            raise ValueError("monthly billing times must be unique and ordered per scenario")
        for month in dict.fromkeys(series.dt.strftime("%Y-%m")):
            selected = [t for t in times if stamps.iloc[t].strftime("%Y-%m") == month]
            if coverage == "require_complete_months":
                start = pd.Timestamp(f"{month}-01", tz=stamps.dt.tz)
                end = start + pd.offsets.MonthBegin(1)
                expected = pd.date_range(start, end, freq=pd.Timedelta(hours=model.dt), inclusive="left")
                if not pd.DatetimeIndex(stamps.iloc[selected]).equals(expected):
                    raise ValueError(f"{scenario}/{month}: incomplete billing month")
            unit_rate = _nonnegative(rate[month] if isinstance(rate, Mapping) else rate, "monthly rate")
            key = (scenario, month)
            if method == "monthly_demand":
                billing = model.problem.add_var(f"monthly_billing_demand[{scenario},{month}]")
                model.variables["monthly_billing_demand"][key] = billing
            else:
                billing = model.problem.add_var(f"monthly_billing_capacity[{scenario},{month}]", lb=capacity, ub=capacity)
                model.variables["monthly_billing_capacity"][key] = billing
            imports = {}
            for t in selected:
                row = _grid_import_row(model, t)
                imports[t] = dict(row)
                model.problem.add_constraint(dict(row), lb=0.0)
                if method == "monthly_demand":
                    model._add(row, billing, -1.0)
                    model.problem.add_constraint(row, ub=0.0)
                else:
                    model.problem.add_constraint(row, ub=capacity * power_factor)
            expected_rate = unit_rate * model.scenario_probabilities[scenario]
            model._record_component("heating_electricity_basic_cost", billing, heating_share * expected_rate)
            model._record_component("shared_non_heating_electricity_basic_cost", billing, (1. - heating_share) * expected_rate)
            model._add(model.scenario_cost_coefficients[scenario], billing, unit_rate)
            model.monthly_tariff_records.append({"scenario": scenario, "month": month, "method": method,
                                                 "coverage_policy": coverage, "unit_rate": unit_rate,
                                                 "heating_cost_allocation_fraction": heating_share,
                                                 "billing_index": billing, "imports": imports})


def extension_summary(model, values):
    result = {}
    if getattr(model, "shared_heat_resource_rows", []):
        result["shared_heat_resource_max_overdraw_kw"] = max(
            max(0.0, sum(c * values[i] for i, c in terms.items()) - cap)
            for _, _, cap, terms, _ in model.shared_heat_resource_rows)
    if getattr(model, "event_state_boundaries", []):
        result["event_state_boundaries"] = [
            {**{k: v for k, v in record.items() if k != "terminal_index"},
             "terminal_state": float(values[record["terminal_index"]])}
            for record in model.event_state_boundaries]
    if getattr(model, "firm_resource_checks", []):
        result["firm_shared_resource_checks"] = [
            {"case": label, "resource": name, "available_heat_kw": cap,
             "required_upstream_heat_kw": float(sum(c * values[i] for i, c in terms.items())),
             "scope": "conservative_static_adequacy_in_declared_horizon"}
            for label, name, cap, terms in model.firm_resource_checks]
    if getattr(model, "monthly_tariff_records", []):
        result["monthly_electricity_billing"] = []
        for record in model.monthly_tariff_records:
            peak = max(sum(c * values[i] for i, c in row.items()) for row in record["imports"].values())
            billed = float(values[record["billing_index"]])
            result["monthly_electricity_billing"].append({
                **{k: v for k, v in record.items() if k not in {"imports", "billing_index"}},
                "maximum_modeled_import_kw": float(peak), "billed_quantity": billed,
                "modeled_basic_charge_yuan": billed * record["unit_rate"],
                "epigraph_slack_kw": billed - float(peak) if record["method"] == "monthly_demand" else None,
            })
    return result
