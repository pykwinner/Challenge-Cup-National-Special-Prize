"""Independently audit a declared investment-reference plan, without its runner.

This checks arithmetic, chronology and the declared ideal RC/design constraints.
It does not establish FT8 performance, a real tariff account, or field validity.
No solver or heatplan module is imported, and the CLI writes only to stdout.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
import hashlib
import json
from math import exp, expm1, isclose, isfinite
from pathlib import Path
from typing import Any


INPUT_COLUMNS = (
    "time", "outdoor_temp", "electricity_price", "grid_emission_factor",
    "internal_gain_kw", "solar_gain_kw", "cop_hp", "capacity_hp_per_unit_kw",
)
DISPATCH_COLUMNS = (
    "time", "q_resistance_kw", "q_hp_kw", "q_cooling_kw",
    "electricity_kw", "temperature_end_c",
)
PHYSICS_KEYS = (
    "H_kw_per_K", "C_kwh_per_K", "initial_c", "terminal_c",
    "min_c", "max_c", "dt_hours",
)
DESIGN_KEYS = (
    "eta_resistance", "eer_cooling", "max_resistance_kw", "max_cooling_kw",
    "max_grid_kw", "resistance_capex_cny_per_kw", "cooling_package_capex_cny",
    "hp_package_capex_cny", "resistance_fixed_om_cny_per_kw_window",
    "hp_fixed_om_cny_per_unit_window", "cooling_fixed_om_cny_per_window",
    "repeat_window_factor",
)
ACCOUNT_KEYS = (
    "capex_cny", "window_energy_bill_cny", "window_fixed_om_cny",
    "window_co2_kg", "electricity_kwh", "comparison_cost_cny",
)
POWER_TOL = 1e-6
TEMPERATURE_TOL = 1e-6


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name}: missing/bool is not a numeric observation")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{name}: blank must not be replaced with zero")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}: invalid numeric value") from error
    if not isfinite(result):
        raise ValueError(f"{name}: non-finite value")
    return result


def _integer(value: Any, name: str) -> int:
    result = _number(value, name)
    if result < 0 or not result.is_integer():
        raise ValueError(f"{name}: expected a non-negative integer")
    return int(result)


def _time(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: missing timestamp")
    try:
        result = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError(f"{name}: invalid ISO timestamp") from error
    if result.tzinfo is None or result.utcoffset() != timedelta(hours=8):
        raise ValueError(f"{name}: explicitly declare Beijing time (+08:00)")
    return result


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _read_json(path: Path, expected_type=dict):
    def bad_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")
    result = json.loads(path.read_text(encoding="utf-8-sig"),
                        object_pairs_hook=_unique_pairs, parse_constant=bad_constant)
    if not isinstance(result, expected_type):
        raise ValueError(f"{path.name}: expected {expected_type.__name__}")
    return result


def _read_csv(path: Path, columns: tuple[str, ...]) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not set(columns).issubset(header):
            raise ValueError(f"{path.name}: missing or duplicate required columns")
        rows = []
        for index, row in enumerate(reader):
            if None in row or any(row[column] is None for column in columns):
                raise ValueError(f"{path.name}: irregular CSV width at row {index + 2}")
            parsed = {"time": _time(row["time"], f"{path.name}[{index}].time")}
            parsed.update({column: _number(row[column], f"{path.name}[{index}].{column}")
                           for column in columns if column != "time"})
            rows.append(parsed)
    if not rows:
        raise ValueError(f"{path.name}: empty chronology")
    return rows


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close(left: float, right: float) -> bool:
    return isclose(left, right, rel_tol=1e-8, abs_tol=1e-6)


def _bound_scope(protocol, summary, investment, design, candidates, enumeration, totals, check):
    """Check the actual registered feasible set, not a superset of investments.

    Reported LP dual values are solver evidence, not independently derived dual
    certificates. Every successful primary enters the lower-bound calculation,
    including one whose secondary subsequently failed.
    """
    scope = protocol["bound_scope"]
    reported_scope = summary["bound_scope"]
    if not isinstance(scope, dict) or not isinstance(reported_scope, dict):
        raise ValueError("bound_scope must be an explicit object in protocol and summary")
    kind = scope["kind"]
    if kind not in ("free_registered_candidates", "baseline_no_HP", "fixed_investment"):
        raise ValueError("unknown investment bound scope")
    scope_candidates = [_integer(value, "bound_scope.hp_unit_candidates")
                        for value in scope["hp_unit_candidates"]]
    reported_candidates = [_integer(value, "summary.bound_scope.hp_unit_candidates")
                           for value in reported_scope["hp_unit_candidates"]]
    fixed = scope["fixed_resistance_kw"]
    reported_fixed = reported_scope["fixed_resistance_kw"]
    fixed = None if fixed is None else _number(fixed, "bound_scope.fixed_resistance_kw")
    reported_fixed = None if reported_fixed is None else _number(reported_fixed, "summary.bound_scope.fixed_resistance_kw")
    check("bound_scope_matches_protocol", reported_scope["kind"] == kind and reported_candidates == scope_candidates
          and ((fixed is None and reported_fixed is None) or
               (fixed is not None and reported_fixed is not None and _close(fixed, reported_fixed))),
          "summary bound scope differs from the effective protocol")
    check("scope_candidates_match_design", scope_candidates == candidates,
          "actual scope candidates differ from effective design candidates")
    if kind == "baseline_no_HP":
        check("baseline_scope", scope_candidates == [0] and fixed is None and "fixed_investment" not in protocol,
              "no-HP baseline must declare exactly candidate [0] with free resistance sizing")
    elif kind == "free_registered_candidates":
        check("free_scope", fixed is None and "fixed_investment" not in protocol,
              "free registered scope must not conceal fixed investment bounds")
    else:
        frozen = protocol["fixed_investment"]
        frozen_hp = _integer(frozen["hp_units"], "fixed_investment.hp_units")
        frozen_r = _number(frozen["resistance_capacity_kw"], "fixed_investment.resistance_capacity_kw")
        frozen_c = _number(frozen["cooling_capacity_kw"], "fixed_investment.cooling_capacity_kw")
        check("fixed_scope", scope_candidates == [frozen_hp] and fixed is not None and _close(fixed, frozen_r),
              "fixed scope must declare its single HP candidate and actual resistance bound")
        check("fixed_investment_preserved", investment["hp_units"] == frozen_hp and
              _close(investment["resistance_capacity_kw"], frozen_r) and
              _close(investment["cooling_capacity_kw"], frozen_c),
              "exported HP/resistance/cooling investment changed from the fixed protocol")
    if not isinstance(enumeration, list) or not enumeration:
        raise ValueError("enumeration.json must contain the actual candidate records")
    unit_ids = [_integer(row["hp_units"], "enumeration.hp_units") for row in enumeration]
    check("enumeration_exact_scope", len(unit_ids) == len(scope_candidates) and
          len(set(unit_ids)) == len(unit_ids) and set(unit_ids) == set(scope_candidates),
          "enumeration must contain every actual scope candidate exactly once, without omitted or extra rows")
    mode = summary["mode"]
    if mode not in ("cost", "carbon"):
        raise ValueError("summary primary mode must be cost or carbon")
    check("selected_primary_mode", summary["solver_stages"][0]["mode"] == mode,
          "selected primary stage mode disagrees with summary")
    complete, primary_resolved = True, True
    lower_bounds, records = [], []
    selected_rows = []
    for position, row in enumerate(enumeration):
        units = unit_ids[position]
        stages = row["solver_stages"]
        if not isinstance(stages, list) or not stages:
            raise ValueError("each enumeration record needs its primary solver stage")
        primary = stages[0]
        status = primary["status"]
        check(f"enum_{position}_primary_mode", primary["mode"] == mode,
              f"enumeration candidate {units} has a different primary objective")
        primary_zero = not isinstance(status, bool) and status == 0 and primary["success"] is True
        primary_infeasible = not isinstance(status, bool) and status == 2 and primary["success"] is False and len(stages) == 1 and row["success"] is False
        candidate_complete = primary_infeasible
        lower = None
        if primary_zero:
            primal = _number(primary["primal_objective"], "enumeration.primary.primal_objective")
            dual = _number(primary["dual_objective"], "enumeration.primary.dual_objective")
            constant = _number(primary["objective_constant"], "enumeration.primary.objective_constant")
            expected_constant = (units * design["hp_package_capex_cny"] + design["cooling_package_capex_cny"]
                + design["repeat_window_factor"] * (units * design["hp_fixed_om_cny_per_unit_window"]
                   + design["cooling_fixed_om_cny_per_window"])) if mode == "cost" else 0.
            check(f"enum_{position}_primary_constant", _close(constant, expected_constant),
                  f"enumeration candidate {units} uses the wrong primary objective constant")
            gap = _number(primary["primal_dual_gap"], "enumeration.primary.primal_dual_gap")
            violation = _number(primary["constraint_max_violation"], "enumeration.primary.constraint_max_violation")
            tolerance = 1e-6 + 1e-8 * max(1., abs(primal), abs(dual))
            check(f"enum_{position}_primary_certificate", 0 <= violation <= POWER_TOL and
                  abs(gap-(primal-dual)) <= tolerance and -tolerance <= primal-dual <= tolerance,
                  f"enumeration candidate {units} has an inconsistent primary solver record")
            # Include this even when secondary failed. Omitting it can raise the
            # claimed global lower bound above a cheaper primary candidate.
            lower = dual + constant
            lower_bounds.append(lower)
            if len(stages) == 2:
                secondary = stages[1]
                candidate_complete = secondary["status"] == 0 and not isinstance(secondary["status"], bool) and secondary["success"] is True and row["success"] is True and secondary["mode"] == ("carbon" if mode == "cost" else "cost")
        elif not primary_infeasible:
            primary_resolved = False
        complete &= candidate_complete
        check(f"enum_{position}_complete", candidate_complete,
              f"enumeration candidate {units} is unresolved: only primary-only status2 or primary0+secondary0 is complete")
        if units == investment["hp_units"]:
            selected_rows.append(row)
        records.append({"hp_units": units, "primary_status": status,
                        "complete": candidate_complete, "primary_lower_bound": lower})
    check("integer_enumeration_complete", complete and summary["integer_enumeration_complete"] is True,
          "integer enumeration is incomplete or its completion flag is missing/false")
    check("selected_enumeration_record", len(selected_rows) == 1 and selected_rows[0]["success"] is True
          and selected_rows[0]["solver_stages"] == summary["solver_stages"],
          "selected solver stages do not match the selected actual-scope enumeration row")
    lower_bound = min(lower_bounds) if primary_resolved and lower_bounds else None
    claimed = summary["global_primary_lower_bound"]
    claimed = None if claimed is None else _number(claimed, "global_primary_lower_bound")
    check("global_primary_lower_bound", (lower_bound is None and claimed is None) or
          (lower_bound is not None and claimed is not None and _close(lower_bound, claimed)),
          "global lower bound must use every primary0 dual+constant in the actual scope")
    final_primary = totals["comparison_cost_cny"] if mode == "cost" else totals["window_co2_kg"]
    primary_gap = None if lower_bound is None else max(0., final_primary-lower_bound)
    claimed_gap = summary["global_primary_gap"]
    claimed_gap = None if claimed_gap is None else _number(claimed_gap, "global_primary_gap")
    check("global_primary_gap", (primary_gap is None and claimed_gap is None) or
          (primary_gap is not None and claimed_gap is not None and _close(primary_gap, claimed_gap)),
          "global primary gap does not match the final dispatch and actual-scope lower bound")
    check("global_bound_below_selected", lower_bound is not None and lower_bound <= final_primary + 1e-6 + abs(final_primary)*1e-8,
          "actual-scope lower bound exceeds the selected feasible objective")
    return {"bound_scope": {"kind": kind, "hp_unit_candidates": scope_candidates, "fixed_resistance_kw": fixed},
            "enumeration_records": records, "all_primary_statuses_resolved": primary_resolved,
            "integer_enumeration_records_complete": complete,
            "global_primary_lower_bound": lower_bound, "global_primary_gap": primary_gap}


def audit_plan(plan_dir, input_csv, protocol_path) -> dict:
    """Return a JSON-safe report; malformed/missing observations fail closed."""
    plan_dir, input_csv, protocol_path = map(Path, (plan_dir, input_csv, protocol_path))
    report = {
        "audit_schema_version": 1, "passed": False, "plan_dir": str(plan_dir),
        "scope": "declared RC/design feasibility and arithmetic; not real equipment or account certification",
        "field_or_device_validation": False, "real_source_closure_asserted": False,
        "errors": [], "checks": {}, "recomputed": {}, "input_sha256": {},
    }
    errors, checks = report["errors"], report["checks"]

    def check(name: str, passed: bool, detail: str):
        checks[name] = bool(passed)
        if not passed:
            errors.append(detail)

    try:
        paths = {"protocol": protocol_path, "input": input_csv,
                 "dispatch": plan_dir / "dispatch.csv",
                 "investments": plan_dir / "investments.json",
                 "enumeration": plan_dir / "enumeration.json",
                 "summary": plan_dir / "summary.json"}
        for name, path in paths.items():
            report["input_sha256"][name] = _sha(path)
        protocol = _read_json(protocol_path)
        investment = _read_json(paths["investments"])
        summary = _read_json(paths["summary"])
        enumeration = _read_json(paths["enumeration"], list)
        inputs = _read_csv(input_csv, INPUT_COLUMNS)
        dispatch = _read_csv(paths["dispatch"], DISPATCH_COLUMNS)
        physics = {key: _number(protocol["physics"][key], f"physics.{key}")
                   for key in PHYSICS_KEYS}
        design = {key: _number(protocol["design"][key], f"design.{key}")
                  for key in DESIGN_KEYS}
        candidates = protocol["design"]["hp_unit_candidates"]
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("hp_unit_candidates: non-empty list required")
        candidates = [_integer(value, "hp_unit_candidates") for value in candidates]
        if len(candidates) != len(set(candidates)):
            raise ValueError("hp_unit_candidates: repeated candidates")
        hp_units = _integer(investment["hp_units"], "hp_units")
        resistance_capacity = _number(investment["resistance_capacity_kw"], "resistance_capacity_kw")
        cooling_capacity = _number(investment["cooling_capacity_kw"], "cooling_capacity_kw")
        h, c, dt = physics["H_kw_per_K"], physics["C_kwh_per_K"], physics["dt_hours"]
        if h <= 0 or c <= 0 or dt <= 0:
            raise ValueError("H, C and dt must be strictly positive")
        if not 0 < design["eta_resistance"] <= 1 or design["eer_cooling"] <= 0:
            raise ValueError("eta_resistance must be in (0,1]; eer_cooling must be positive")
        if design["repeat_window_factor"] <= 0:
            raise ValueError("repeat_window_factor must be positive")
        if any(value < 0 for key, value in design.items()
               if key not in ("eta_resistance", "eer_cooling", "repeat_window_factor")):
            raise ValueError("capacity bounds and declared cost parameters must be non-negative")
        if design["max_cooling_kw"] <= 0 or design["max_grid_kw"] <= 0:
            raise ValueError("fixed cooling service and grid bounds must be positive")
        if physics["min_c"] > physics["max_c"]:
            raise ValueError("comfort band is reversed")
        if any(not physics["min_c"] <= physics[key] <= physics["max_c"]
               for key in ("initial_c", "terminal_c")):
            raise ValueError("initial and terminal temperatures must be in the comfort band")
        if any(row["cop_hp"] <= 0 or row["capacity_hp_per_unit_kw"] < 0
               or row["electricity_price"] < 0 or row["grid_emission_factor"] < 0
               or row["internal_gain_kw"] < 0 or row["solar_gain_kw"] < 0 for row in inputs):
            raise ValueError("input COP must be positive; capacity, price, carbon and gains must be non-negative")
        check("declared_parameters", True, "invalid declared parameters")
        check("integer_candidate", hp_units in candidates, "hp_units not in the registered candidate list")
        check("investment_capacity", -POWER_TOL <= resistance_capacity <= design["max_resistance_kw"] + POWER_TOL,
              "resistance investment outside the finite capacity bounds")
        check("fixed_cooling_investment", abs(cooling_capacity - design["max_cooling_kw"]) <= POWER_TOL,
              "the common fixed cooling package capacity was changed")

        start = _time(protocol["window_start_inclusive"], "window_start_inclusive")
        end = _time(protocol["window_end_exclusive"], "window_end_exclusive")
        periods = (end - start).total_seconds() / (3600 * dt)
        if periods <= 0 or not isclose(periods, round(periods), rel_tol=0, abs_tol=1e-9):
            raise ValueError("registered window must be a positive integral number of time steps")
        expected = [start + timedelta(hours=dt * index) for index in range(round(periods))]
        check("input_registered_chronology", [row["time"] for row in inputs] == expected,
              "input does not cover the registered complete, unique, ordered chronology")
        check("dispatch_registered_chronology", [row["time"] for row in dispatch] == expected,
              "dispatch does not cover the registered complete, unique, ordered chronology")
        if len(inputs) != len(dispatch):
            raise ValueError("dispatch and input row counts differ")

        a, b = exp(-h * dt / c), -expm1(-h * dt / c) / h
        residuals, electrical_residuals = [], []
        min_power, violations, simultaneous, discomfort = 0., [], [], []
        previous = physics["initial_c"]
        totals = {"electricity_kwh": 0., "window_energy_bill_cny": 0., "window_co2_kg": 0.,
                  "heat_resistance_kwh": 0., "heat_hp_kwh": 0., "cooling_kwh": 0.}
        expected_electricity = []
        for index, (source, row) in enumerate(zip(inputs, dispatch)):
            qr, qhp, qc = (row[key] for key in ("q_resistance_kw", "q_hp_kw", "q_cooling_kw"))
            min_power = min(min_power, qr, qhp, qc, row["electricity_kw"])
            p = qr / design["eta_resistance"] + qhp / source["cop_hp"] + qc / design["eer_cooling"]
            expected_electricity.append(p)
            electrical_residuals.append(abs(row["electricity_kw"] - p))
            if qr > resistance_capacity + POWER_TOL or qhp > hp_units * source["capacity_hp_per_unit_kw"] + POWER_TOL or qc > cooling_capacity + POWER_TOL:
                violations.append(index)
            if qr + qhp > POWER_TOL and qc > POWER_TOL:
                simultaneous.append(index)
            temperature = a * previous + (1-a) * source["outdoor_temp"] + b * (
                qr + qhp - qc + source["internal_gain_kw"] + source["solar_gain_kw"])
            residuals.append(abs(row["temperature_end_c"] - temperature))
            if not physics["min_c"] - TEMPERATURE_TOL <= row["temperature_end_c"] <= physics["max_c"] + TEMPERATURE_TOL:
                discomfort.append(index)
            previous = row["temperature_end_c"]
            totals["electricity_kwh"] += p * dt
            totals["window_energy_bill_cny"] += p * dt * source["electricity_price"]
            totals["window_co2_kg"] += p * dt * source["grid_emission_factor"]
            totals["heat_resistance_kwh"] += qr * dt
            totals["heat_hp_kwh"] += qhp * dt
            totals["cooling_kwh"] += qc * dt
        check("nonnegative_power", min_power >= -POWER_TOL, "negative thermal/electric dispatch")
        check("hourly_capacity", not violations, f"hourly capacity violations at rows {violations[:8]}")
        check("heat_cooling_exclusive", not simultaneous, f"simultaneous heating and cooling at rows {simultaneous[:8]}")
        check("rc_dynamics", max(residuals) <= TEMPERATURE_TOL, "independent RC recurrence failed")
        check("comfort", not discomfort, f"comfort violations at rows {discomfort[:8]}")
        check("terminal", abs(previous - physics["terminal_c"]) <= TEMPERATURE_TOL, "terminal temperature not preserved")
        check("electricity_balance", max(electrical_residuals) <= POWER_TOL, "electricity does not match thermal service inputs")
        check("grid_capacity", max(expected_electricity) <= design["max_grid_kw"] + POWER_TOL and
              max(row["electricity_kw"] for row in dispatch) <= design["max_grid_kw"] + POWER_TOL,
              "finite grid capacity exceeded")

        # Cooling is a fixed, installed, common service in every plan.
        totals["capex_cny"] = hp_units * design["hp_package_capex_cny"] + resistance_capacity * design["resistance_capex_cny_per_kw"] + design["cooling_package_capex_cny"]
        totals["window_fixed_om_cny"] = hp_units * design["hp_fixed_om_cny_per_unit_window"] + resistance_capacity * design["resistance_fixed_om_cny_per_kw_window"] + design["cooling_fixed_om_cny_per_window"]
        totals["comparison_cost_cny"] = totals["capex_cny"] + design["repeat_window_factor"] * (totals["window_energy_bill_cny"] + totals["window_fixed_om_cny"])
        cost_constant = hp_units * design["hp_package_capex_cny"] + design["cooling_package_capex_cny"] + design["repeat_window_factor"] * (hp_units * design["hp_fixed_om_cny_per_unit_window"] + design["cooling_fixed_om_cny_per_window"])
        variable_objectives = {"cost": totals["comparison_cost_cny"] - cost_constant,
                               "carbon": totals["window_co2_kg"]}
        for key in ACCOUNT_KEYS:
            reported = _number(summary[key], f"summary.{key}")
            check(f"ledger_{key}", _close(reported, totals[key]), f"independent {key} disagrees with summary")
        report["bound_evidence"] = _bound_scope(protocol, summary, investment, design, candidates, enumeration, totals, check)
        requested = summary["requested_epsilon_co2_kg"]
        epsilon = None if requested is None else _number(requested, "requested_epsilon_co2_kg")
        if epsilon is not None and epsilon < 0:
            raise ValueError("carbon epsilon must be non-negative")
        check("carbon_epsilon", epsilon is None or totals["window_co2_kg"] <= epsilon + 1e-6,
              "recomputed window carbon exceeds requested epsilon")
        stages = summary["solver_stages"]
        if not isinstance(stages, list) or not stages:
            raise ValueError("solver_stages must be a non-empty list")
        stage_records = []
        for index, stage in enumerate(stages):
            if not isinstance(stage, dict):
                raise ValueError("each solver stage must be an object")
            mode = stage["mode"]
            if mode not in ("cost", "carbon"):
                raise ValueError("solver stage mode must be cost or carbon")
            violation = _number(stage["constraint_max_violation"], "constraint_max_violation")
            seconds = _number(stage["seconds"], "seconds")
            primal = _number(stage["primal_objective"], "primal_objective")
            dual = _number(stage["dual_objective"], "dual_objective")
            gap = _number(stage["primal_dual_gap"], "primal_dual_gap")
            constant = _number(stage["objective_constant"], "objective_constant")
            check(f"solver_stage_{index}_constant", _close(constant, cost_constant if mode == "cost" else 0.),
                  f"solver stage {index} objective constant does not match the independent ledger")
            stage_epsilon = stage["epsilon_co2_kg"]
            if stage_epsilon is not None:
                stage_epsilon = _number(stage_epsilon, "epsilon_co2_kg")
                if stage_epsilon < 0:
                    raise ValueError("stage epsilon must be non-negative")
            status = stage["status"]
            successful = stage["success"] is True and not isinstance(status, bool) and status == 0
            tolerance = 1e-6 + 1e-8 * max(1., abs(primal), abs(dual))
            valid = successful and 0 <= violation <= POWER_TOL and seconds >= 0 and abs(gap - (primal-dual)) <= tolerance and -tolerance <= primal-dual <= tolerance
            check(f"solver_stage_{index}", valid, f"solver stage {index} ({mode}) failed status/claimed primal-dual checks")
            check(f"solver_stage_{index}_epsilon", stage_epsilon is None or totals["window_co2_kg"] <= stage_epsilon + 1e-6,
                  f"final dispatch does not satisfy solver stage {index} carbon cap")
            if index == len(stages) - 1:
                check("final_stage_objective", _close(primal, variable_objectives[mode]),
                      "final solver objective disagrees with the independently recomputed dispatch")
            elif index == 0:
                lex_tolerance = 1e-7 + abs(primal) * 1e-9
                numerical_tolerance = 1e-6 + abs(primal) * 1e-8
                check("primary_lexicographic_bound", variable_objectives[mode] <= primal + lex_tolerance + numerical_tolerance,
                      "final dispatch exceeds the declared primary lexicographic allowance")
            stage_records.append({"mode": mode, "success": successful, "epsilon_co2_kg": stage_epsilon,
                                  "primal_minus_dual": primal-dual, "reported_gap": gap})
        totals.update({"rows": len(inputs), "window_hours": len(inputs) * dt,
                       "hp_units": hp_units, "resistance_capacity_kw": resistance_capacity,
                       "cooling_capacity_kw": cooling_capacity,
                       "max_rc_residual_c": max(residuals),
                       "max_electricity_balance_residual_kw": max(electrical_residuals),
                       "peak_electricity_kw": max(expected_electricity),
                       "requested_epsilon_co2_kg": epsilon,
                       "lp_cost_constant_cny": cost_constant,
                       "lp_variable_cost_objective": variable_objectives["cost"]})
        report["recomputed"] = totals
        report["solver_stage_records"] = stage_records
        report["solver_optimality_scope"] = "checks actual-scope enumeration coverage/statuses and recomputes its lower bound from reported primary duals; final objective is independently recomputed, but LP dual vectors/optimality are not independently certified"
        report["passed"] = not errors and all(checks.values())
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        errors.append(f"invalid or incomplete audit input: {type(error).__name__}: {error}")
        report["passed"] = False
    return report


def _find_bundle(plan: Path, root: Path) -> tuple[Path, Path]:
    effective = plan / "effective_protocol.json"
    for directory in (plan, *plan.parents):
        if (directory / "input.csv").is_file() and (directory / "protocol.json").is_file():
            return directory / "input.csv", effective if effective.is_file() else directory / "protocol.json"
        if directory == root:
            break
    for directory in (root / "input_bundle", root / "bundle", root):
        if (directory / "input.csv").is_file() and (directory / "protocol.json").is_file():
            return directory / "input.csv", effective if effective.is_file() else directory / "protocol.json"
    raise ValueError(f"no adjacent input.csv/protocol.json found for {plan}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="experiment root; discover summary.json plans")
    parser.add_argument("--plan-dir", type=Path, help="audit just this plan")
    parser.add_argument("--input-csv", type=Path)
    parser.add_argument("--protocol", type=Path)
    args = parser.parse_args(argv)
    if args.root is None and args.plan_dir is None:
        parser.error("provide --root or --plan-dir")
    root = (args.root or args.plan_dir).resolve()
    plans = [args.plan_dir.resolve()] if args.plan_dir else sorted(path.parent for path in root.rglob("summary.json") if (path.parent / "dispatch.csv").is_file())
    reports = []
    for plan in plans:
        try:
            local_input, local_protocol = _find_bundle(plan, root) if args.input_csv is None or args.protocol is None else (args.input_csv, args.protocol)
            reports.append(audit_plan(plan, args.input_csv or local_input, args.protocol or local_protocol))
        except ValueError as error:
            reports.append({"passed": False, "plan_dir": str(plan), "errors": [str(error)]})
    result = {"passed": bool(reports) and all(report["passed"] for report in reports),
              "plans": reports, "errors": [] if reports else ["no plans found"]}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
