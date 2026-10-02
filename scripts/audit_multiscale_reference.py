"""Independent verifier for the declared ideal-source multibuilding reference.

No heatplan model, building, solver or accounting code is imported. Temperatures
are rebuilt by the full-history analytic 1R1C convolution, using parameters and
drivers from config/input rather than the exported RC residual or coefficients.
This verifies a numerical reference experiment, not calibrated engineering,
local tariffs, solver optimality, or performance claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from math import exp, expm1, isfinite
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
POWER_TOL = 1e-6
TEMP_TOL = 1e-6
ACCOUNT_TOL = 1e-5


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any, label: str, positive: bool = False) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label}: boolean is not a number")
    x = float(value)
    if not isfinite(x) or (positive and x <= 0):
        raise ValueError(f"{label}: invalid finite value")
    return x


def _numeric(frame: pd.DataFrame, column: str) -> np.ndarray:
    if column not in frame:
        raise ValueError(f"missing column: {column}")
    if any(isinstance(v, (bool, np.bool_)) for v in frame[column]):
        raise ValueError(f"{column}: boolean values")
    x = pd.to_numeric(frame[column], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(x).all():
        raise ValueError(f"{column}: nonfinite values")
    return x


def _driver(spec: Mapping, frame: pd.DataFrame, key: str, default: float = 0.) -> np.ndarray:
    column_key = key.removesuffix("_kw").removesuffix("_c") + "_column"
    if column_key in spec:
        return _numeric(frame, str(spec[column_key]))
    return np.full(len(frame), _number(spec.get(key, default), key))


def _times(frame: pd.DataFrame) -> pd.DatetimeIndex:
    if "time" not in frame:
        raise ValueError("missing time")
    values = pd.to_datetime(frame.time, errors="raise")
    if not isinstance(values.dtype, pd.DatetimeTZDtype) or values.isna().any():
        raise ValueError("timestamps must be timezone-aware and nonmissing")
    return pd.DatetimeIndex(values).tz_convert("UTC")


def _maxabs(values: np.ndarray) -> float:
    return float(np.max(np.abs(values), initial=0.))


class _Checks:
    def __init__(self):
        self.checks: dict[str, dict] = {}

    def add(self, code: str, passed: bool, detail: str = "") -> None:
        entry = self.checks.setdefault(code, {"passed": True, "details": []})
        entry["passed"] = entry["passed"] and bool(passed)
        if not passed:
            entry["details"].append(detail)

    def near(self, code: str, actual: np.ndarray | float, expected: np.ndarray | float,
             tolerance: float, label: str) -> None:
        residual = _maxabs(np.asarray(actual) - np.asarray(expected))
        self.add(code, residual <= tolerance, f"{label}: maximum residual {residual:.12g}")


def audit_case(config: Mapping[str, Any], frame: pd.DataFrame,
               trajectory: pd.DataFrame | None, dispatch: pd.DataFrame | None,
               summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a fail-closed, JSON-serializable independent physical/accounting audit.

    Supports the explicitly declared full-chronology, fixed-boundary buildings,
    electric boilers and constant-EER cooling, with no pipes, storage, other
    electric generators, investment, shedding, or legacy load shifting.
    """
    checks = _Checks()
    report: dict[str, Any] = {
        "passed": False, "scope": "independent ideal-source multibuilding chronology audit",
        "method": "closed-form RC full-history convolution; independent source electricity ledger",
        "empirical_validation": False, "engineering_economics_validation": False,
        "solver_optimality_verified": False, "metrics": {}, "buildings": [], "node_balances": [],
        "tolerances": {"power_kw": POWER_TOL, "temperature_c": TEMP_TOL,
                       "accounting_absolute": ACCOUNT_TOL}, "checks": checks.checks,
    }
    try:
        if not isinstance(config, Mapping) or not isinstance(summary, Mapping):
            raise ValueError("config and summary must be mappings; summary required")
        if frame.empty or not frame.columns.is_unique:
            raise ValueError("nonempty input with unique columns required")
        if trajectory is None or trajectory.empty:
            raise ValueError("building trajectory is missing or empty")
        if dispatch is None or dispatch.empty:
            raise ValueError("dispatch is missing or empty")
        for exported in (trajectory, dispatch):
            if not exported.columns.is_unique:
                raise ValueError("duplicate export columns")
        frame = frame.reset_index(drop=True)
        tr, ds = trajectory.copy(), dispatch.copy()
        n = len(frame)
        planning = config.get("planning", {})
        dt = _number(planning.get("time_step_hours", 1.), "time_step_hours", True)
        stamps = _times(frame)
        if len(stamps) > 1 and not np.allclose(np.diff(stamps.asi8) / 3.6e12, dt, rtol=0, atol=1e-9):
            raise ValueError("input chronology has gaps, duplicates, or wrong timestep")
        scenarios = frame.get("scenario", pd.Series(["base"] * n)).astype(str).to_numpy()
        groups = frame.get("boundary_group", pd.Series(scenarios)).astype(str).to_numpy()
        if len(set(scenarios)) != 1 or len(set(groups)) != 1:
            raise ValueError("reference must be a single continuous scenario/boundary group")
        for column, default in (("period_weight", planning.get("representative_day_weight", 1.)),
                                ("scenario_probability", 1.)):
            values = _numeric(frame, column) if column in frame else np.full(n, _number(default, column))
            if not np.all(values == 1.):
                raise ValueError("reference chronology requires unit period/scenario weights")
        section = config.get("building_thermal", {})
        if section.get("enabled") is not True:
            raise ValueError("enabled building_thermal required")
        specs = section.get("buildings", [])
        names = [str(b["name"]) for b in specs]
        nodes = {str(v["name"]) for v in config.get("nodes", [])}
        grades = {str(v["name"]) for v in config.get("grades", [])}
        if not names or len(names) != len(set(names)):
            raise ValueError("unique nonempty building declarations required")
        if config.get("pipes") or config.get("storages") or config.get("microgrid_islanding"):
            raise ValueError("network, storage and generation outside reference scope")
        if _number(config.get("demand_response", {}).get("max_shift_fraction", 0.), "max_shift_fraction") != 0:
            raise ValueError("legacy load shifting outside reference scope")
        for key in ("carbon_price_yuan_per_kg", "exergy_penalty_yuan_per_kwh"):
            if _number(planning.get(key, 0.), key) != 0:
                raise ValueError(f"nonzero {key} outside energy-charge-only scope")
        required_tr = {"building", "node", "heat_grade", "time_index", "time", "temperature_start_c",
                       "temperature_end_c", "heat_kw", "cooling_kw"}
        required_ds = {"category", "asset", "node", "grade", "time_index", "time", "value", "unit"}
        if required_tr - set(tr) or required_ds - set(ds):
            raise ValueError(f"missing exported columns: {sorted((required_tr-set(tr)) | (required_ds-set(ds)))}")
        for exported in (tr, ds):
            indices = _numeric(exported, "time_index")
            if not np.array_equal(indices, indices.astype(int)) or (indices < 0).any() or (indices >= n).any():
                raise ValueError("export has noninteger or out-of-range time_index")
            exported["time_index"] = indices.astype(int)
            checks.add("time_alignment", bool(np.array_equal(_times(exported).asi8, stamps.asi8[indices.astype(int)])),
                       "exported clock disagrees with input at time_index")
            for field, expected in (("scenario", scenarios), ("boundary_group", groups)):
                if field in exported:
                    checks.add("time_alignment", np.array_equal(exported[field].astype(str).to_numpy(), expected[indices.astype(int)]),
                               f"exported {field} disagrees with input")
        for field in ("temperature_start_c", "temperature_end_c", "heat_kw", "cooling_kw"):
            _numeric(tr, field)
        _numeric(ds, "value")
        expected_pairs = {(name, t) for name in names for t in range(n)}
        actual_pairs = list(zip(tr.building.astype(str), tr.time_index))
        checks.add("trajectory_complete", len(actual_pairs) == len(expected_pairs) and set(actual_pairs) == expected_pairs,
                   "trajectory must contain every declared building/hour exactly once")
        if not checks.checks["trajectory_complete"]["passed"]:
            raise ValueError("incomplete or duplicated trajectory")
        ds_pairs = list(zip(ds.category.astype(str), ds.asset.astype(str), ds.grade.astype(str), ds.time_index))
        if len(ds_pairs) != len(set(ds_pairs)):
            raise ValueError("duplicate dispatch category/asset/grade/hour")
        outdoor = _numeric(frame, str(section.get("outdoor_temperature_column", "outdoor_temp")))
        if (outdoor <= -273.15).any():
            raise ValueError("outdoor temperature below absolute zero")
        heat_nodes: dict[tuple[str, str], np.ndarray] = {}
        cool_nodes: dict[str, np.ndarray] = {}
        heat_total, cool_total = np.zeros(n), np.zeros(n)
        max_rc = comfort = terminal = 0.

        def values(category: str, asset: str, node: str, grade: str, unit: str,
                   required: bool = True) -> np.ndarray:
            rows = ds[(ds.category == category) & (ds.asset == asset) & (ds.grade == grade)].sort_values("time_index")
            if rows.empty and not required:
                return np.zeros(n)
            if len(rows) != n or not np.array_equal(rows.time_index.to_numpy(), np.arange(n)):
                raise ValueError(f"{category}/{asset}/{grade}: incomplete dispatch")
            checks.add("dispatch_assignment", bool((rows.node.astype(str) == node).all() and (rows.unit.astype(str) == unit).all()),
                       f"{category}/{asset}/{grade}: forged node or unit")
            return _numeric(rows, "value")

        for spec in specs:
            name, node, grade = str(spec["name"]), str(spec["node"]), str(spec["heat_grade"])
            if node not in nodes or grade not in grades:
                raise ValueError(f"{name}: undeclared node/grade")
            rows = tr[tr.building == name].sort_values("time_index")
            checks.add("trajectory_assignment", bool((rows.node == node).all() and (rows.heat_grade == grade).all()),
                       f"{name}: exported node/grade differs from config")
            h = _number(spec["heat_loss_kw_per_c"], "H", True)
            c = _number(spec["thermal_capacity_kwh_per_c"], "C", True)
            area = _number(spec["area_m2"], "area_m2", True)
            initial = _number(spec["initial_temperature_c"], "initial_temperature_c")
            if spec.get("boundary_mode", section.get("boundary_mode", "fixed")) != "fixed" or spec.get("boundary_conditions"):
                raise ValueError("only a single explicitly fixed boundary is admitted")
            if "final_temperature_c" not in spec:
                raise ValueError("explicit final_temperature_c required")
            final = _number(spec["final_temperature_c"], "final_temperature_c")
            lo, hi = _driver(spec, frame, "min_temperature_c"), _driver(spec, frame, "max_temperature_c")
            gain, solar = _driver(spec, frame, "internal_gain_kw"), _driver(spec, frame, "solar_gain_kw")
            if (lo > hi).any():
                raise ValueError("comfort lower bound exceeds upper bound")
            heat, cool = _numeric(rows, "heat_kw"), _numeric(rows, "cooling_kw")
            observed, start = _numeric(rows, "temperature_end_c"), _numeric(rows, "temperature_start_c")
            a, b = exp(-h * dt / c), -expm1(-h * dt / c) / h
            # Full-history Green's-function convolution: does not feed an
            # exported temperature back into the next reconstructed step.
            kernel = np.power(a, np.arange(n))
            forcing = (1. - a) * outdoor + b * (heat - cool + gain + solar)
            rebuilt = initial * np.power(a, np.arange(1, n + 1)) + np.convolve(forcing, kernel)[:n]
            rebuilt_start = np.r_[initial, rebuilt[:-1]]
            residual = _maxabs(rebuilt - observed)
            max_rc = max(max_rc, residual)
            checks.near("rc_convolution", observed, rebuilt, TEMP_TOL, name)
            checks.near("initial_and_continuity", start, rebuilt_start, TEMP_TOL, name)
            checks.near("terminal_inventory", observed[-1], final, TEMP_TOL, name)
            terminal = max(terminal, abs(float(observed[-1]) - final))
            under, over = np.maximum(lo - observed, 0.), np.maximum(observed - hi, 0.)
            checks.add("comfort", max(float(under.max()), float(over.max())) <= TEMP_TOL, f"{name}: outside comfort")
            checks.add("comfort", lo[0] - TEMP_TOL <= initial <= hi[0] + TEMP_TOL, f"{name}: initial outside comfort")
            comfort += float((under + over).sum() * dt)
            checks.add("nonnegative_service", min(float(heat.min()), float(cool.min())) >= -POWER_TOL, f"{name}: negative service")
            if section.get("simultaneous_mode", "exclusive") == "exclusive":
                checks.add("exclusive_service", float(np.minimum(heat, cool).max()) <= POWER_TOL, f"{name}: simultaneous heat/cooling")
            for field, power in (("max_heat_kw", heat), ("max_cooling_kw", cool)):
                if field in spec:
                    checks.add("building_capacity", float(power.max()) <= _number(spec[field], field) + POWER_TOL, f"{name}: {field} exceeded")
            if not spec.get("heating_enabled", True):
                checks.near("building_capacity", heat, 0., POWER_TOL, f"{name}: disabled heating")
            if not spec.get("cooling_enabled", True):
                checks.near("building_capacity", cool, 0., POWER_TOL, f"{name}: disabled cooling")
            service_column = spec.get("heating_service_column", section.get("heating_service_column"))
            if service_column:
                service = _numeric(frame, str(service_column))
                if not np.isin(service, [0., 1.]).all():
                    raise ValueError("heating service indicator must be binary")
                checks.near("building_capacity", heat * (1. - service), 0., POWER_TOL, f"{name}: off-service heating")
            metadata = {"area_m2": area, "heat_loss_kw_per_c": h, "thermal_capacity_kwh_per_c": c,
                        "initial_temperature_reference_c": initial, "dt_hours": dt,
                        "outdoor_temperature_c": outdoor, "internal_gain_kw": gain, "solar_gain_kw": solar,
                        "min_temperature_c": lo, "max_temperature_c": hi, "heat_kwh": heat * dt,
                        "cooling_kwh": cool * dt}
            for field, expected in metadata.items():
                if field in rows:
                    checks.near("trajectory_input_metadata", _numeric(rows, field), expected, TEMP_TOL, f"{name}/{field}")
            for category, expected, out_grade, unit in (("building_heat", heat, grade, "kW"),
                    ("building_cooling", cool, "cooling", "kW"), ("building_temperature", observed, grade, "degC")):
                checks.near("building_dispatch_identity", values(category, name, node, out_grade, unit), expected, POWER_TOL, f"{name}/{category}")
            heat_nodes.setdefault((node, grade), np.zeros(n))[:] += heat
            cool_nodes.setdefault(node, np.zeros(n))[:] += cool
            heat_total += heat
            cool_total += cool
            report["buildings"].append({"name": name, "node": node, "heat_loss_kw_per_c": h,
                "thermal_capacity_kwh_per_c": c, "area_m2": area, "rc_max_residual_c": residual,
                "initial_start_c": float(start[0]), "final_end_c": float(observed[-1]),
                "min_temperature_c": float(observed.min()), "max_temperature_c": float(observed.max()),
                "outside_comfort_degree_hours": float((under + over).sum() * dt),
                "heat_kwh": float(heat.sum() * dt), "cooling_kwh": float(cool.sum() * dt),
                "inventory_change_kwh": c * (float(observed[-1]) - initial)})

        cooling = config.get("multi_energy_services", {}).get("cooling", {})
        if cooling.get("enabled") is not True:
            raise ValueError("enabled explicit cooling service required")
        supplied_heat = {key: np.zeros(n) for key in heat_nodes}
        supplied_cool = {key: np.zeros(n) for key in cool_nodes}
        electric_heat, electric_cool = np.zeros(n), np.zeros(n)
        sources = config.get("sources", [])
        source_names = [str(s["name"]) for s in sources]
        if not sources or len(source_names) != len(set(source_names)):
            raise ValueError("unique nonempty sources required")
        capacity_excess = 0.
        allowed_keys = {(str(row.category), str(row.asset), str(row.grade)) for row in ds.itertuples()}
        expected_keys = set()
        for spec in specs:
            expected_keys |= {("building_heat", spec["name"], spec["heat_grade"]),
                              ("building_cooling", spec["name"], "cooling"),
                              ("building_temperature", spec["name"], spec["heat_grade"])}
        for source in sources:
            name, node = str(source["name"]), str(source["node"])
            if source.get("type") != "electric_boiler" or node not in nodes:
                raise ValueError("only electric_boiler at a declared node admitted")
            for key in ("max_new_capacity_kw", "max_retire_capacity_kw", "minimum_retire_capacity_kw",
                        "variable_om_yuan_per_kwh_heat", "fixed_om_yuan_per_kw_year"):
                if _number(source.get(key, 0.), key) != 0.:
                    raise ValueError(f"{name}: nonzero {key} outside no-investment reference")
            eta = _number(source["efficiency"], "efficiency", True)
            allowed = source.get("allowed_grades", list(grades))
            source_heat = np.zeros(n)
            for grade in allowed:
                if (node, grade) not in supplied_heat:
                    raise ValueError(f"{name}: source has no declared building service for {node}/{grade}")
                power = values("source_heat", name, node, str(grade), "kW")
                supplied_heat[node, grade] += power
                source_heat += power
                expected_keys.add(("source_heat", name, str(grade)))
            cs = cooling.get("sources", {}).get(name)
            if not isinstance(cs, Mapping) or "eer" not in cs:
                raise ValueError(f"{name}: explicit cooling EER mapping required")
            eer = _number(cs["eer"], "eer", True)
            if _number(cs.get("eer_loss_per_c_above_reference", 0.), "eer_loss") != 0.:
                raise ValueError("only constant declared EER admitted")
            if not _number(cs.get("eer_min", 1.5), "eer_min", True) <= eer <= _number(cs.get("eer_max", 8.), "eer_max", True):
                raise ValueError("declared EER must be inside explicitly configured clipping limits")
            for key in ("variable_om_yuan_per_kwh_cooling", "direct_emission_kg_per_kwh_cooling"):
                if _number(cs.get(key, 0.), key) != 0.:
                    raise ValueError(f"nonzero {key} outside reference scope")
            source_cool = values("cooling_output", name, node, "cooling", "kW")
            if node not in supplied_cool:
                raise ValueError("cooling source without a building")
            supplied_cool[node] += source_cool
            expected_keys.add(("cooling_output", name, "cooling"))
            capacity = _number(source["existing_capacity_kw"], "existing_capacity_kw", True)
            availability = _number(source.get("availability", 1.), "availability")
            if "availability_column" in source:
                availability = _numeric(frame, str(source["availability_column"]))
            excess = source_heat + source_cool * _number(cs.get("capacity_equivalence", 1.), "capacity_equivalence", True) - capacity * availability
            capacity_excess = max(capacity_excess, float(np.max(excess, initial=0.)))
            checks.add("source_capacity", float(excess.max()) <= POWER_TOL, f"{name}: shared heating/cooling capacity exceeded")
            checks.add("nonnegative_service", min(float(source_heat.min()), float(source_cool.min())) >= -POWER_TOL, f"{name}: negative source service")
            electric_heat += source_heat / eta
            electric_cool += source_cool / eer
        for key, expected in heat_nodes.items():
            checks.near("node_heat_balance", supplied_heat[key], expected, POWER_TOL, str(key))
            report["node_balances"].append({"node": key[0], "grade": key[1], "service": "heat",
                                            "max_residual_kw": _maxabs(supplied_heat[key] - expected)})
        for node, expected in cool_nodes.items():
            checks.near("node_cooling_balance", supplied_cool[node], expected, POWER_TOL, node)
            report["node_balances"].append({"node": node, "service": "cooling",
                                            "max_residual_kw": _maxabs(supplied_cool[node] - expected)})
        # All nonreference streams must be declared zero and correctly assigned.
        zero_categories = {"demand_up", "demand_down", "unserved", "cooling_unserved", "renewable_electricity_use"}
        for category, asset, grade in allowed_keys - expected_keys:
            extra = ds[(ds.category == category) & (ds.asset == asset) & (ds.grade == grade)]
            checks.add("no_extra_service", category in zero_categories and _maxabs(_numeric(extra, "value")) <= POWER_TOL,
                       f"unexpected nonzero or unsupported dispatch {category}/{asset}/{grade}")
            if category != "renewable_electricity_use":
                checks.add("dispatch_assignment", bool(set(extra.node.astype(str)) <= nodes), f"unknown node for {category}/{asset}")
        electric = electric_heat + electric_cool
        price = _numeric(frame, str(planning.get("electricity_price_column", "electricity_price")))
        factor = _numeric(frame, "grid_emission_factor")
        bill, co2 = float(np.dot(electric, price) * dt), float(np.dot(electric, factor) * dt)
        heat_energy, cool_energy = float(heat_total.sum() * dt), float(cool_total.sum() * dt)
        eh_energy, ec_energy = float(electric_heat.sum() * dt), float(electric_cool.sum() * dt)
        grid = config.get("grid_connection", {})
        # Some configurations locate the same public planning block under grid.
        if not grid:
            grid = config.get("grid", {})
        cap = None
        if grid and grid.get("enabled", True):
            if _number(grid.get("max_new_capacity_kw", 0.), "grid.max_new_capacity_kw") != 0.:
                raise ValueError("grid investment outside reference scope")
            if grid.get("firm_capacity_method") != "portfolio_dispatch":
                raise ValueError("shared grid must use declared portfolio_dispatch method")
            cap = _number(grid["existing_capacity_kw"], "grid existing capacity", True)
            checks.add("shared_grid_limit", float(electric.max()) <= cap + POWER_TOL, "reconstructed shared import exceeds cap")
        peak = float(electric.max())
        expected_summary = {
            "objective_yuan_per_year": bill, "objective_value": bill,
            "electricity_kwh_per_year": eh_energy + ec_energy,
            "grid_electricity_kwh_per_year": eh_energy + ec_energy,
            "total_service_emissions_kg_per_year": co2,
            "emissions_kg_per_year": float(np.dot(electric_heat, factor) * dt),
            "cooling_emissions_kg_per_year": float(np.dot(electric_cool, factor) * dt),
            "cooling_electricity_kwh_per_year": ec_energy, "demand_kwh_per_year": heat_energy,
            "cooling_demand_kwh_per_year": cool_energy,
            "building_heat_delivered_kwh_per_year": heat_energy, "building_cooling_delivered_kwh_per_year": cool_energy,
            "unserved_kwh_per_year": 0., "cooling_unserved_kwh_per_year": 0.,
            "legacy_annualized_capex_yuan_per_year": 0., "regulated_annualized_capex_yuan_per_year": 0.,
            "reference_energy_charge_yuan": bill, "reference_average_factor_co2_kg": co2,
            "maximum_ideal_source_electric_import_kw": peak, "grid_peak_kw": peak,
        }
        for required in ("objective_yuan_per_year", "electricity_kwh_per_year", "total_service_emissions_kg_per_year"):
            if required not in summary:
                raise ValueError(f"missing summary accounting key {required}")
        for field, expected in expected_summary.items():
            if field in summary:
                checks.near("summary_accounting", _number(summary[field], field), expected, ACCOUNT_TOL, field)
        if cap is not None:
            if "grid_connection_capacity_kw" in summary:
                checks.near("summary_accounting", _number(summary["grid_connection_capacity_kw"], "grid capacity"), cap, POWER_TOL, "grid capacity")
            if "firm_grid_capacity_method" in summary:
                checks.add("summary_accounting", summary["firm_grid_capacity_method"] == "portfolio_dispatch", "summary grid method differs")
        if "components_yuan_per_year" in summary:
            components = summary["components_yuan_per_year"]
            if not isinstance(components, Mapping):
                raise ValueError("summary components must be mapping")
            expected_components = {"operating_cost": float(np.dot(electric_heat, price) * dt),
                                   "cooling_operating_cost": float(np.dot(electric_cool, price) * dt)}
            for field, actual in components.items():
                checks.near("summary_accounting", _number(actual, field), expected_components.get(field, 0.), ACCOUNT_TOL, f"component/{field}")
            checks.near("summary_accounting", sum(_number(v, k) for k, v in components.items()), bill, ACCOUNT_TOL, "sum of cost components")
        for field in ("numerical_audit_passed", "building_independent_audit_passed"):
            if field in summary:
                checks.add("exported_audit_status", summary[field] is True, f"{field} is not true")
        report["metrics"] = {"hours": n, "dt_hours": dt, "building_count": len(names),
            "heat_kwh": heat_energy, "cooling_kwh": cool_energy, "electricity_kwh": eh_energy + ec_energy,
            "heating_electricity_kwh": eh_energy, "cooling_electricity_kwh": ec_energy,
            "reference_energy_charge_yuan": bill, "reference_average_factor_co2_kg": co2,
            "grid_peak_kw": peak, "maximum_ideal_source_electric_import_kw": peak, "grid_capacity_kw": cap,
            "grid_max_excess_kw": max(0., peak - cap) if cap is not None else 0.,
            "grid_binding_hours": int(np.count_nonzero(np.abs(electric - cap) <= POWER_TOL)) if cap is not None else 0,
            "grid_exceedance_hours": int(np.count_nonzero(electric > cap + POWER_TOL)) if cap is not None else 0,
            "max_rc_residual_c": max_rc, "outside_comfort_degree_hours": comfort,
            "max_terminal_violation_c": terminal, "source_capacity_max_excess_kw": capacity_excess,
            "no_investment_verified": True}
        checks.add("valid_data", True)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as error:
        checks.add("valid_data", False, str(error))
    report["passed"] = bool(checks.checks) and all(check["passed"] for check in checks.checks.values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    report = verify_run(args.run)
    target = args.run / "independent_verification.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cases_checked": len(report["cases"]), "output": str(target)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


def verify_run(run: Path) -> dict[str, Any]:
    """Reopen frozen inputs and outputs, recompute every attempted case, retain failures.

    Archived sources, inputs, protocol lock and each manifest hash are checked.
    Current working-tree source differences are reported separately because the
    archived run remains inspectable after unrelated future code edits.
    """
    run = Path(run).resolve()
    checks = _Checks()
    report = {"passed": False, "verification_completed": False,
              "scope": "frozen 2880h two-building shared-grid reference and all counterfactuals",
              "empirical_validation": False, "engineering_economics_validation": False,
              "solver_optimality_verified": False, "cases": [], "checks": checks.checks,
              "current_source_differences": [], "verifier_sha256": digest(Path(__file__)),
              "run": str(run)}

    def read(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def local(relative: str) -> Path:
        path = (run / relative).resolve()
        if not path.is_relative_to(run):
            raise ValueError(f"artifact escapes run directory: {relative}")
        return path

    def check_file(relative: str, expected: str, code: str):
        path = local(relative)
        checks.add(code, path.is_file() and digest(path) == expected, f"hash mismatch/missing: {relative}")

    try:
        protocol = read(run / "protocol.json")
        lock = read(run / "protocol_lock.json")
        checks.add("protocol_lock", digest(run / "protocol.json") == lock["protocol.json"], "protocol digest differs from lock")
        registered_cases = protocol["cases"]
        expected_cases = {"independent_A", "independent_B", "joint_unlimited", "coupled_flexible",
                          "coupled_A_fixed20", "coupled_B_fixed20", "coupled_all_fixed20"}
        checks.add("protocol_scope", protocol.get("schema_version") == "multiscale_reference.v1" and
                   set(registered_cases) == expected_cases and len(registered_cases) == len(expected_cases) and
                   protocol.get("hours_per_building") == 2880 and protocol.get("dt_hours") == 1 and
                   protocol.get("unit_physical_weights") is True, "unexpected protocol case set/chronology/schema")
        if not checks.checks["protocol_scope"]["passed"]:
            raise ValueError("protocol scope is not the registered complete multiscale reference")
        snapshots = protocol["source_snapshots"]
        if not isinstance(snapshots, Mapping) or not snapshots:
            raise ValueError("nonempty source snapshots required")
        for original, snapshot in snapshots.items():
            check_file(snapshot["path"], snapshot["sha256"], "source_snapshot_hashes")
            current = (ROOT / original).resolve()
            if not current.is_relative_to(ROOT):
                raise ValueError("original source reference escapes workspace")
            if not current.is_file() or digest(current) != snapshot["sha256"]:
                report["current_source_differences"].append(original)
        originals = protocol.get("reference_inputs", protocol.get("input_sha256", {}))
        if not originals:
            raise ValueError("reference input identities required")
        for original, expected in originals.items():
            snapshot = snapshots.get(original)
            checks.add("reference_input_hashes", bool(snapshot and snapshot["sha256"] == expected),
                       f"reference identity disagrees with snapshot: {original}")
        manifest = read(run / "manifest.json")
        if not manifest.get("files"):
            raise ValueError("nonempty output manifest required")
        for relative, expected in manifest["files"].items():
            check_file(relative, expected, "output_manifest_hashes")
        case_files = protocol["case_files"]
        checks.add("frozen_case_set", set(case_files) == expected_cases, "frozen case inputs incomplete")
        for name, files in case_files.items():
            for relative, expected in files.items():
                check_file(relative, expected, "frozen_input_hashes")
        comparison = read(run / "comparison.json")
        if not isinstance(comparison, list):
            raise ValueError("comparison must be list of case records")
        comparison_names = [record["name"] for record in comparison]
        checks.add("comparison_case_set", len(comparison_names) == len(expected_cases) and set(comparison_names) == expected_cases,
                   "comparison missing/duplicate/unknown cases")
        by_name = {record["name"]: record for record in comparison}
        actual_directories = {path.name for path in (run / "results").iterdir() if path.is_dir()}
        checks.add("result_case_set", actual_directories == expected_cases, "results missing/unknown case directory")
        shared_capacity = _number(protocol["shared_resource"]["capacity_kw"], "registered grid capacity", True)
        for name in registered_cases:
            path = run / "results" / name
            record: dict = {"name": name, "passed": False, "status": "failed"}
            report["cases"].append(record)
            try:
                for filename in ("config.yaml", "input.csv"):
                    frozen = run / "input_cases" / name / filename
                    result_copy = path / filename
                    if not result_copy.is_file() or digest(result_copy) != digest(frozen):
                        raise ValueError(f"result copy differs from frozen input: {name}/{filename}")
                config = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
                frame = pd.read_csv(path / "input.csv")
                times = _times(frame)
                start = pd.Timestamp(protocol["start_inclusive"]).tz_convert("UTC")
                end = pd.Timestamp(protocol["end_exclusive"]).tz_convert("UTC")
                expected_clock = pd.date_range(start, end, freq="h", inclusive="left")
                if len(frame) != 2880 or not times.equals(expected_clock):
                    raise ValueError("case input does not contain the complete frozen 2880h clock")
                if name.startswith("coupled_"):
                    if abs(_number(config["grid_connection"]["existing_capacity_kw"], "capacity") - shared_capacity) > POWER_TOL:
                        raise ValueError("case shared capacity differs from predeclared protocol")
                elif config.get("grid_connection"):
                    raise ValueError("independent/unlimited counterfactual unexpectedly has shared grid")
                # Independently check the capacity's pre-solve physical rule.
                all_h = .1043 * 2.5
                derived_cap = all_h * max(0., 20. - float(_numeric(frame, "outdoor_temp").min()))
                if abs(derived_cap - shared_capacity) > POWER_TOL:
                    raise ValueError("registered shared capacity does not follow declared full-window H/Tout rule")
                spec_by_name = {spec["name"]: spec for spec in config["building_thermal"]["buildings"]}
                members = ("A",) if name == "independent_A" else ("B",) if name == "independent_B" else ("A", "B")
                if set(spec_by_name) != {f"variant_{member}" for member in members}:
                    raise ValueError("case has unexpected building variants")
                for member in members:
                    spec = spec_by_name[f"variant_{member}"]
                    wanted_h, wanted_c = .1043 * (1. if member == "A" else 1.5), .535576 * (1. if member == "A" else 4.)
                    wanted_max = 20. if name == "coupled_all_fixed20" or name == f"coupled_{member}_fixed20" else 27.
                    for key, wanted in (("heat_loss_kw_per_c", wanted_h), ("thermal_capacity_kwh_per_c", wanted_c),
                                        ("area_m2", 48.), ("initial_temperature_c", 20.), ("final_temperature_c", 20.),
                                        ("min_temperature_c", 20.), ("max_temperature_c", wanted_max)):
                        if abs(_number(spec[key], key) - wanted) > 1e-10:
                            raise ValueError(f"{name}/{member}: undeclared design/control change {key}")
                    if _maxabs(_driver(spec, frame, "internal_gain_kw") - .2) > 1e-10 or _maxabs(_driver(spec, frame, "solar_gain_kw")) > 1e-10:
                        raise ValueError("case gains differ from declared common reference")
                failure_path = path / "failure.json"
                failure = read(failure_path) if failure_path.exists() else None
                if failure is not None:
                    record.update(status=failure.get("status", "failed"), failure=failure)
                needed = ("building_trajectory.csv", "dispatch.csv", "summary.json", "numerical_audit.json")
                if not all((path / filename).is_file() for filename in needed):
                    if failure is None:
                        raise ValueError("missing solution artifact without retained failure record")
                    record["reason"] = "retained failed attempt has no complete solution; never counted as successful"
                    checks.add("attempted_case_success", False, f"{name}: retained {record['status']}")
                    continue
                summary = read(path / "summary.json")
                numerical = read(path / "numerical_audit.json")
                expected_numerical = {k: v for k, v in summary.items() if k.startswith("numerical_")}
                if not expected_numerical or numerical != expected_numerical or numerical.get("numerical_audit_passed") is not True:
                    raise ValueError("numerical audit absent, failed, or disagrees with summary")
                if (path / "investments.csv").is_file():
                    investments = pd.read_csv(path / "investments.csv")
                    if not investments.empty and _maxabs(_numeric(investments, "value")) > POWER_TOL:
                        raise ValueError("nonzero investment/retirement in fixed-assets reference")
                audited = audit_case(config, frame, pd.read_csv(path / "building_trajectory.csv"),
                                     pd.read_csv(path / "dispatch.csv"), summary)
                record["audit"] = audited
                original = by_name.get(name, {})
                if original.get("audit") and audited["passed"]:
                    for key, actual in audited["metrics"].items():
                        saved = original["audit"].get("metrics", {}).get(key)
                        if isinstance(actual, (float, int)) and not isinstance(actual, bool):
                            checks.near("comparison_recomputed_metrics", _number(saved, key), actual, ACCOUNT_TOL, f"{name}/{key}")
                        else:
                            checks.add("comparison_recomputed_metrics", saved == actual, f"{name}/{key} differs")
                if original.get("status") not in {"passed", "feasible_unproven", "failed", "infeasible"}:
                    raise ValueError("comparison does not record a final attempted case status")
                if failure is not None:
                    record["reason"] = "retained failure prevents successful certification even if incumbent artifacts exist"
                elif audited["passed"] and original.get("status") in {"passed", "feasible_unproven"}:
                    record.update(passed=True, status=original["status"])
                else:
                    record["reason"] = "independent audit rejected case or comparison marks failed attempt"
                checks.add("attempted_case_success", record["passed"], f"{name}: {record['status']}")
            except (OSError, KeyError, ValueError, TypeError, AttributeError) as error:
                record["reason"] = str(error)
                checks.add("attempted_case_success", False, f"{name}: {error}")
        report["verification_completed"] = True
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        checks.add("valid_run_artifacts", False, str(error))
    report["passed"] = report["verification_completed"] and all(v["passed"] for v in checks.checks.values())
    return report


if __name__ == "__main__":
    raise SystemExit(main())
