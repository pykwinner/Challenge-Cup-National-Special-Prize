"""Fixed actual-day LP with explicit price/operation moment error bounds.

This is a standard bounded-moment LP, not a dispatch model or a new theorem.
It preserves the reference energy, not the electricity of arbitrary policies.
All arrays describe one admitted, complete, equally sampled calendar window.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix, eye, hstack, vstack


METHOD = "standard_LP_bounded_price_operation_moments_minimum_weight_L1"


def _problem(reference_power_kw, electricity_price_per_kwh, selected_day_indices,
             original_weights, lower_bounds, *, day_ids, reference_kind,
             moment_absolute_tolerances, operation_windows, time_step_hours,
             numerical_tolerance):
    power = np.asarray(reference_power_kw, dtype=float)
    price = np.asarray(electricity_price_per_kwh, dtype=float)
    if power.ndim != 2 or not all(power.shape) or price.shape != power.shape:
        raise ValueError("power and prices must have the same nonempty day-by-sample shape")
    if not np.isfinite(power).all() or (power < 0).any() or not np.isfinite(price).all():
        raise ValueError("reference power must be finite/nonnegative and prices finite")
    days, samples = power.shape
    dt, tol = float(time_step_hours), float(numerical_tolerance)
    if not np.isfinite(dt) or dt <= 0 or not np.isclose(samples * dt, 24, rtol=0, atol=1e-9):
        raise ValueError("each row must describe a complete 24h equally sampled day")
    if not np.isfinite(tol) or not 1e-10 <= tol <= 1e-4:
        raise ValueError("numerical_tolerance must be finite and between 1e-10 and 1e-4")
    if reference_kind not in ("thermal", "electric"):
        raise ValueError("reference_kind must explicitly be thermal or electric")
    if day_ids is None or isinstance(day_ids, (str, bytes)):
        raise ValueError("day_ids must be a sequence of unique nonempty strings")
    ids = list(day_ids)
    if (len(ids) != days or any(not isinstance(x, str) or not x.strip() for x in ids)
            or len(set(ids)) != days):
        raise ValueError("one unique nonempty day_id is required per chronology row")
    selected = np.asarray(selected_day_indices)
    if (selected.ndim != 1 or not selected.size or selected.dtype.kind not in "iu"
            or (selected < 0).any() or (selected >= days).any()
            or len(np.unique(selected)) != len(selected)):
        raise ValueError("selected_day_indices must be unique integer indices of actual source days")
    selected = selected.astype(np.intp)
    initial, lower = np.asarray(original_weights, dtype=float), np.asarray(lower_bounds, dtype=float)
    if (initial.shape != selected.shape or lower.shape != selected.shape
            or any(not np.isfinite(x).all() or (x < 0).any() for x in (initial, lower))):
        raise ValueError("weights and lower bounds must match selected days and be finite/nonnegative")
    if not np.isclose(initial.sum(), days, rtol=0, atol=tol * max(days, 1)):
        raise ValueError("original weights must represent the complete source day count")
    if operation_windows is None:
        operation_windows = {}
    if not isinstance(operation_windows, Mapping):
        raise ValueError("operation_windows must map predeclared names to full calendar binary masks")
    windows = {}
    for name, values in operation_windows.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("operation window names must be nonempty strings")
        mask = np.asarray(values, dtype=float)
        if mask.shape != power.shape or not np.isfinite(mask).all() or not np.isin(mask, [0, 1]).all():
            raise ValueError("operation window masks must be binary and match the full calendar")
        windows[name] = mask
    energy_unit = "kWh_th" if reference_kind == "thermal" else "kWh_e"
    units = {"price_product": "currency*kWh_th/kWh_e" if reference_kind == "thermal" else "currency"}
    units.update({"window_energy:" + name: energy_unit for name in windows})
    if not isinstance(moment_absolute_tolerances, Mapping) or set(moment_absolute_tolerances) != set(units):
        raise ValueError("explicit absolute tolerances are required for every and only declared moment")
    tolerances = {name: float(moment_absolute_tolerances[name]) for name in units}
    if any(not np.isfinite(x) or x < 0 for x in tolerances.values()):
        raise ValueError("moment tolerances must be finite/nonnegative in the declared native units")
    with np.errstate(over="ignore", invalid="ignore"):
        energy = power.sum(axis=1) * dt
        moments = {"price_product": (power * price).sum(axis=1) * dt}
        moments.update({"window_energy:" + name: (power * mask).sum(axis=1) * dt
                        for name, mask in windows.items()})
    if any(not np.isfinite(x).all() or not np.isfinite(x.sum()) for x in [energy, *moments.values()]):
        raise ValueError("daily contributions or calendar totals overflowed; no solver called")
    return dict(power=power, price=price, selected=selected, initial=initial, lower=lower,
                ids=ids, days=days, dt=dt, tol=tol, windows=windows, energy=energy,
                moments=moments, tolerances=tolerances, units=units, energy_unit=energy_unit)


def _identity(problem):
    """Fingerprint the numeric inputs and row identity, never edit or reorder them."""
    selected = problem["selected"]

    def digest(values):
        array = np.ascontiguousarray(values, dtype="<f8")
        return hashlib.sha256(str(array.shape).encode("ascii") + array.tobytes()).hexdigest()

    return {"selected_day_indices": selected.tolist(),
            "selected_day_ids": [problem["ids"][i] for i in selected],
            "calendar_day_ids_sha256": hashlib.sha256(json.dumps(problem["ids"], ensure_ascii=False).encode("utf-8")).hexdigest(),
            "reference_power_sha256": digest(problem["power"]),
            "paired_prices_sha256": digest(problem["price"]),
            "selected_reference_power_sha256": digest(problem["power"][selected]),
            "selected_paired_prices_sha256": digest(problem["price"][selected]),
            "operation_window_sha256": {name: digest(mask) for name, mask in problem["windows"].items()}}


def _scale(daily, selected, days):
    scale = max(abs(float(daily.sum())), float(np.max(np.abs(daily[selected]))) * days, 1.0)
    if not np.isfinite(scale):
        raise ValueError("moment scale overflowed; no solver called")
    return scale


def _diagnostics(problem, weights):
    """Recalculate from raw power/price products, independently of LP/epigraph rows."""
    power, price, indices = problem["power"], problem["price"], problem["selected"]
    dt, tol, days = problem["dt"], problem["tol"], problem["days"]
    weights = np.asarray(weights, dtype=float)
    if weights.shape != indices.shape or not np.isfinite(weights).all():
        raise ValueError("diagnostic weights must be finite and match selected days")

    def record(full_values, unit, error_bound):
        # Use the original calendar arrays, not the solver's coefficient matrix.
        daily = np.sum(full_values, axis=1) * dt
        target = float(np.sum(full_values) * dt)
        estimated = float(sum(float(w) * float(np.sum(full_values[i]) * dt)
                              for i, w in zip(indices, weights)))
        residual = estimated - target
        allowance = tol * _scale(daily, indices, days)
        if not np.isfinite([target, estimated, residual, allowance]).all():
            raise ValueError("diagnostic arithmetic overflowed for supplied weights")
        violation = max(abs(residual) - error_bound, 0.0)
        return {"unit": unit, "target": target, "estimate": estimated,
                "signed_residual": residual,
                "relative_residual": residual / abs(target) if target else None,
                "absolute_error_bound": error_bound, "bound_violation": violation,
                "numerical_acceptance_allowance": allowance,
                "passes_with_declared_numerical_tolerance": violation <= allowance}

    energy = record(power, problem["energy_unit"], 0.0)
    moments = {"price_product": record(power * price, problem["units"]["price_product"],
                                       problem["tolerances"]["price_product"])}
    for name, mask in problem["windows"].items():
        key = "window_energy:" + name
        moments[key] = record(power * mask, problem["units"][key], problem["tolerances"][key])
    day_residual = float(weights.sum() - days)
    lower_violation = float(np.maximum(problem["lower"] - weights, 0).max())
    passed = (abs(day_residual) <= tol * max(days, 1) and lower_violation <= tol
              and all(row["passes_with_declared_numerical_tolerance"] for row in [energy, *moments.values()]))
    return {"passes": bool(passed), "reference_energy": energy, "moments": moments,
            "day_conservation_signed_residual": day_residual,
            "day_numerical_acceptance_allowance": tol * max(days, 1),
            "maximum_lower_bound_violation_days": lower_violation,
            "lower_bound_numerical_acceptance_allowance_days": tol,
            "objective_weight_L1_over_days": float(np.abs(weights - problem["initial"]).sum() / days),
            "objective_unit": "dimensionless (sum absolute weight displacement in days / calendar days)",
            "operation_window_active_hours": {
                name: {"full_calendar": float(mask.sum() * dt),
                       "per_selected_day": (mask[indices].sum(axis=1) * dt).tolist()}
                for name, mask in problem["windows"].items()}}


def diagnose_decision_weights(reference_power_kw, electricity_price_per_kwh,
                              selected_day_indices, original_weights, lower_bounds,
                              weights, *, day_ids, reference_kind,
                              moment_absolute_tolerances, operation_windows=None,
                              time_step_hours=1.0, numerical_tolerance=1e-8):
    """Audit supplied weights without running a solver or changing any source.

    Prices are currency/kWh_e. Thermal-reference price products have units
    currency*kWh_th/kWh_e: they are proxies, never policy electricity bills.
    Calendar timestamps/provenance must already have been admitted by the caller.
    """
    problem = _problem(reference_power_kw, electricity_price_per_kwh, selected_day_indices,
                       original_weights, lower_bounds, day_ids=day_ids, reference_kind=reference_kind,
                       moment_absolute_tolerances=moment_absolute_tolerances,
                       operation_windows=operation_windows, time_step_hours=time_step_hours,
                       numerical_tolerance=numerical_tolerance)
    return {"input_identity": _identity(problem), **_diagnostics(problem, weights)}


def calibrate_price_operation_weights(reference_power_kw, electricity_price_per_kwh,
                                      selected_day_indices, original_weights, lower_bounds,
                                      *, day_ids, reference_kind, moment_absolute_tolerances,
                                      operation_windows=None, time_step_hours=1.0,
                                      numerical_tolerance=1e-8, solver_time_limit_seconds=30.0):
    """Minimize sum_j |w_j-w0_j|/D with hard days/reference energy and moment bands.

    Every representative is selected by index from the supplied source calendar;
    there is no replacement, synthetic-day construction, implicit soft matching,
    or default substantive error bound. All bounds must be frozen by the caller.
    Return weights=None on infeasibility/failure; baseline diagnostics are retained.
    Masks express reference load at predeclared operating times, not RC dynamics.
    """
    problem = _problem(reference_power_kw, electricity_price_per_kwh, selected_day_indices,
                       original_weights, lower_bounds, day_ids=day_ids, reference_kind=reference_kind,
                       moment_absolute_tolerances=moment_absolute_tolerances,
                       operation_windows=operation_windows, time_step_hours=time_step_hours,
                       numerical_tolerance=numerical_tolerance)
    limit = float(solver_time_limit_seconds)
    if not np.isfinite(limit) or limit <= 0:
        raise ValueError("solver_time_limit_seconds must be positive and finite")
    indices, initial, lower = problem["selected"], problem["initial"], problem["lower"]
    days, tol, m = problem["days"], problem["tol"], len(indices)
    report = {"method": METHOD, "status": None, "weights": None,
              "input_identity": _identity(problem), "original_weights": initial.tolist(),
              "lower_bounds": lower.tolist(), "total_weight_days": days,
              "reference_kind": reference_kind, "time_step_hours": problem["dt"],
              "source_data_modified": False, "selected_profiles_modified": False,
              "price_product_is_actual_policy_bill": False,
              "engineering_operation_validated": False, "out_of_sample_validated": False,
              "moment_absolute_tolerances": problem["tolerances"],
              "numerical_tolerance": tol, "solver_time_limit_seconds": limit,
              "baseline_diagnostics": _diagnostics(problem, initial),
              "objective": "minimum sum absolute weight displacement / calendar day count",
              "objective_unit": "dimensionless", "multiple_optima_possible": True,
              "linear_program_size": {"variables": 2 * m, "equalities": 2,
                                      "inequalities": 2 * m + 2 * len(problem["moments"])} }
    if lower.sum() > days:
        return {**report, "status": "infeasible", "reason": "lower_bounds_exceed_calendar_days"}
    # Necessary row-wise attainable intervals under days/floors. These are NOT
    # a joint feasibility certificate or an irreducible infeasible subsystem.
    remainder = days - float(lower.sum())
    rows = {"reference_energy": problem["energy"], **problem["moments"]}
    envelopes, excluded = {}, []
    for name, daily in rows.items():
        coefficients, target = daily[indices], float(daily.sum())
        attainable_lower = float(coefficients @ lower + remainder * coefficients.min())
        attainable_upper = float(coefficients @ lower + remainder * coefficients.max())
        error = 0.0 if name == "reference_energy" else problem["tolerances"][name]
        allowance = tol * _scale(daily, indices, days)
        impossible = target + error < attainable_lower - allowance or target - error > attainable_upper + allowance
        envelopes[name] = {"attainable_lower": attainable_lower, "attainable_upper": attainable_upper,
                           "target": target, "absolute_error_bound": error,
                           "excludes_target_band": bool(impossible)}
        if impossible:
            excluded.append(name)
    report.update(individual_moment_envelopes=envelopes, individual_envelopes_are_only_necessary=True)
    if excluded:
        return {**report, "status": "infeasible", "reason": "individual_moment_envelope_excludes_target",
                "excluded_moments": excluded}
    energy = problem["energy"]
    energy_scale = _scale(energy, indices, days)
    equality = hstack((csr_matrix(np.vstack((np.ones(m) / days, energy[indices] / energy_scale))),
                       csr_matrix((2, m))), format="csr")
    rhs = np.array([1.0, energy.sum() / energy_scale])
    matrix = csr_matrix(np.vstack([daily[indices] / _scale(daily, indices, days)
                                  for daily in problem["moments"].values()]))
    target = np.array([daily.sum() / _scale(daily, indices, days) for daily in problem["moments"].values()])
    error = np.array([problem["tolerances"][name] / _scale(daily, indices, days)
                      for name, daily in problem["moments"].items()])
    zeros, identity = csr_matrix((matrix.shape[0], m)), eye(m, format="csr")
    inequality = vstack((hstack((matrix, zeros)), hstack((-matrix, zeros)),
                         hstack((identity, -identity)), hstack((-identity, -identity))), format="csr")
    upper = np.concatenate((target + error, -target + error, initial, -initial))
    objective = np.concatenate((np.zeros(m), np.ones(m) / days))
    try:
        result = linprog(objective, A_ub=inequality, b_ub=upper, A_eq=equality, b_eq=rhs,
                         bounds=[(float(value), None) for value in lower] + [(0, None)] * m,
                         method="highs", options={"time_limit": limit, "primal_feasibility_tolerance": tol,
                                                  "dual_feasibility_tolerance": tol})
    except (ValueError, RuntimeError, MemoryError) as error:
        return {**report, "status": "solver_failure", "reason": "solver_exception", "solver_message": str(error)}
    report.update(solver_status=int(result.status), solver_message=str(result.message),
                  solver_iterations=int(result.nit))
    if result.status == 2:
        return {**report, "status": "infeasible", "reason": "joint_moment_constraints_infeasible"}
    if not result.success or result.x is None:
        return {**report, "status": "solver_failure", "reason": "LP_did_not_return_optimum"}
    weights = np.asarray(result.x[:m], dtype=float)
    if not np.isfinite(weights).all() or not np.isfinite(result.fun):
        return {**report, "status": "solver_failure", "reason": "nonfinite_solver_result"}
    try:
        diagnostics = _diagnostics(problem, weights)
    except ValueError as error:
        return {**report, "status": "solver_failure", "reason": "LP_failed_residual_check",
                "diagnostic_message": str(error)}
    objective_residual = abs(diagnostics["objective_weight_L1_over_days"] - float(result.fun))
    verified = diagnostics["passes"] and objective_residual <= tol * max(abs(float(result.fun)), 1.0)
    return {**report, "status": "ok" if verified else "solver_failure",
            "reason": "independently_recomputed_constraints_and_objective" if verified else "LP_failed_residual_check",
            "weights": weights.tolist() if verified else None, "diagnostics": diagnostics,
            "solver_objective": float(result.fun), "solver_objective_residual": objective_residual}
