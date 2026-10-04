"""Result provenance: optimization evidence and investment scope are separate.

Certificates below refer to the supplied finite LP/MILP and solver tolerances,
not to field feasibility, all engineering costs, or a complete Pareto frontier.
Raw bounds always stay in the objective expression/units of their own stage.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

SCHEMA = "heatplan.solution_quality.v1"


class SolveFailure(RuntimeError):
    """Preserve the old exception contract while exposing exportable evidence."""
    def __init__(self, message: str, summary: dict):
        super().__init__(message)
        self.summary = summary
        self.solution_quality = summary["solution_quality"]


INVESTMENT_FAMILIES = (
    "source_capacity", "source_retirement", "storage_capacity", "biomass_inventory_capacity",
    "pipe_choice", "pipe_retrofit", "terminal_temperature_retrofit", "heat_metering_coverage",
    "grid_connection_capacity", "microgrid_renewable_capacity", "microgrid_battery_power",
    "microgrid_battery_energy", "microgrid_backup_capacity",
)
STAGE_FIELDS = (
    "solver_status", "solver_message", "objective_mode", "objective_value", "mip_dual_bound",
    "mip_gap", "mip_node_count", "solver_elapsed_seconds", "solver_requested_mip_gap",
    "solver_time_limit_seconds", "solver_integer_variables", "solver_objective_constant",
    "numerical_audit_passed", "numerical_objective_checked", "numerical_solution_finite",
)
SCOPES = {"joint_investment_dispatch", "fixed_investment_dispatch", "existing_assets_dispatch",
          "mixed_fixed_and_free_investment_dispatch", "unknown_investment_scope"}


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if isfinite(parsed) else None


def investment_scope(config: Mapping, variables: Mapping, lower, upper) -> dict:
    """Inspect actual model variable bounds; a zero selected addition is not fixed."""
    indices = {index for family in INVESTMENT_FAMILIES for index in variables.get(family, {}).values()}
    free = [index for index in indices if float(upper[index]) > float(lower[index])]
    frozen = bool(config.get("fixed_investment_accounting") or config.get("replay_service_policy"))
    scope = ("mixed_fixed_and_free_investment_dispatch" if frozen and free else
             "joint_investment_dispatch" if free else
             "fixed_investment_dispatch" if frozen else "existing_assets_dispatch")
    return {"investment_scope": scope, "free_investment_variables": len(free),
            "investment_variables_including_fixed": len(indices), "fixed_plan_marker_present": frozen,
            "scope_evidence": "actual_built_investment_variable_bounds",
            "joint_investment_decisions_present": bool(free and not frozen)}


def solver_stage_evidence(summary: Mapping, *, stage_id: str = "single") -> dict:
    record = {key: summary.get(key) for key in STAGE_FIELDS}
    # Normalize nonfinite fields to null without turning missing gaps into zero.
    for key in STAGE_FIELDS:
        if key not in {"solver_message", "objective_mode", "numerical_audit_passed",
                       "numerical_objective_checked", "numerical_solution_finite"}:
            record[key] = finite_number(record[key])
    record["stage_id"] = stage_id
    mode, status = record["objective_mode"], record["solver_status"]
    unit = {"total_cost": "yuan_model_objective", "emissions": "kg_CO2_solver_linear_expression"}.get(mode)
    value, gap, target = record["objective_value"], record["mip_gap"], record["solver_requested_mip_gap"]
    audited = record["numerical_audit_passed"] is True
    checked = record["numerical_objective_checked"] is True
    feasible = bool(audited and value is not None and record["numerical_solution_finite"] is not False and status in (0, 1))
    reasons = []
    continuous = record["solver_integer_variables"] == 0
    if record["numerical_audit_passed"] is False or record["numerical_solution_finite"] is False or status in (2, 3, 4):
        quality = "failed_or_infeasible"
        reasons.append("failed numerical audit or infeasible/unbounded/error solver termination")
    elif not feasible or not checked or unit is None:
        quality = "missing_evidence"
        reasons.append("finite audited incumbent, checked objective or known objective identity unavailable")
    elif status == 1:
        quality = "time_or_iteration_limited_feasible_approximation"
        reasons.append("solver limit termination; incumbent is feasible but global optimality is not claimed")
    elif gap is not None and gap < 0:
        quality = "missing_evidence"
        reasons.append("negative reported gap is inconsistent")
    elif target is not None and not 0 <= target < 1:
        quality = "missing_evidence"
        reasons.append("invalid registered relative gap tolerance")
    elif continuous:
        quality = "solver_proven_optimal_within_registered_tolerance"
        reasons.append("continuous LP solver optimal termination; null MIP gap remains null, solver numerical tolerances apply")
    elif gap is not None and ((target is not None and gap <= target + 1e-12) or gap == 0):
        quality = "solver_proven_optimal_within_registered_tolerance"
        reasons.append("solver optimal termination and reported relative gap within recorded target or closed reported gap")
    else:
        quality = "feasible_unproven_or_tolerance_unregistered"
        reasons.append("optimality gap/registered target insufficient or inconsistent; do not infer convergence")
    bound = record["mip_dual_bound"]
    lower_bound = bound if feasible and checked and unit is not None else None
    upper_bound = value if feasible and checked and unit is not None else None
    if lower_bound is not None and upper_bound is not None and lower_bound > upper_bound:
        lower_bound = None
        reasons.append("raw lower bound exceeds incumbent; invalid interval retained as missing lower bound")
        if quality == "solver_proven_optimal_within_registered_tolerance":
            quality = "missing_evidence"
    record.update({"quality": quality, "feasible_incumbent_audited": feasible,
        "optimality_proven_within_solver_tolerance": quality == "solver_proven_optimal_within_registered_tolerance",
        "objective_unit": unit, "objective_sense": "minimize", "reasons": reasons,
        "objective_interval": {"lower": lower_bound, "upper": upper_bound, "unit": unit,
            "objective_mode": mode, "expression": "this stage's solver linear objective, excluding separately reported constant",
            "status": "solver_bound_and_feasible_incumbent" if lower_bound is not None and upper_bound is not None else
                      "feasible_upper_only_lower_not_reported" if upper_bound is not None else "unavailable",
            "solver_tolerance_limited": True, "not_a_KPI_perturbation_interval": True}})
    return record


def _legacy_primary(summary: Mapping) -> dict:
    # primary_incumbent_value can be an affine KPI including a constant. Never
    # relabel it as the solver fun or infer its missing dual bound.
    primary = {key: summary.get("primary_" + key) for key in STAGE_FIELDS}
    primary["objective_mode"] = summary.get("primary_objective")
    primary["objective_value"] = summary.get("primary_solver_objective_value")
    primary["solver_elapsed_seconds"] = summary.get("primary_solver_elapsed_seconds")
    primary["mip_gap"] = summary.get("primary_mip_gap")
    primary["mip_dual_bound"] = summary.get("primary_mip_dual_bound")
    primary["numerical_audit_passed"] = summary.get("primary_numerical_audit_passed")
    return primary


def solution_quality_from_summary(summary: Mapping, *, scope: str | Mapping | None = None) -> dict:
    """Read-only classification; absence of evidence never becomes a certificate."""
    if scope is None:
        scope = summary.get("solution_scope", "unknown_investment_scope")
    scope_record = dict(scope) if isinstance(scope, Mapping) else {"investment_scope": scope,
        "scope_evidence": "explicit_argument_or_summary" if scope != "unknown_investment_scope" else "unavailable"}
    if scope_record.get("investment_scope") not in SCOPES:
        scope_record.update(investment_scope="unknown_investment_scope", scope_evidence="invalid or incomplete scope provenance")
    raw = summary.get("solver_stages")
    if isinstance(raw, list) and raw:
        stages = [solver_stage_evidence(stage if isinstance(stage, Mapping) else {},
                  stage_id=str(stage.get("stage_id", i)) if isinstance(stage, Mapping) else str(i)) for i, stage in enumerate(raw)]
    elif "primary_objective" in summary:
        stages = [solver_stage_evidence(_legacy_primary(summary), stage_id="primary"),
                  solver_stage_evidence(summary, stage_id="secondary")]
    else:
        stages = [solver_stage_evidence(summary)]
    qualities = [stage["quality"] for stage in stages]
    service_failed = any(summary.get(key) is False for key in
        ("emissions_cap_pass", "building_comfort_state_passed", "building_no_simultaneous_heat_cooling_passed"))
    if service_failed or summary.get("failure_reason") or "failed_or_infeasible" in qualities:
        quality = "failed_or_infeasible"
    elif "missing_evidence" in qualities:
        quality = "missing_evidence"
    elif "time_or_iteration_limited_feasible_approximation" in qualities:
        quality = "time_or_iteration_limited_feasible_approximation"
    elif all(stage["optimality_proven_within_solver_tolerance"] for stage in stages):
        quality = "solver_proven_optimal_within_registered_tolerance"
    else:
        quality = "feasible_unproven_or_tolerance_unregistered"
    feasible = bool(not service_failed and all(stage["feasible_incumbent_audited"] for stage in stages))
    lex = len(stages) > 1 or "primary_objective" in summary
    lex_claim = ("two_stage_tolerance_limited_endpoint" if lex and all(s["optimality_proven_within_solver_tolerance"] for s in stages)
                 else "incumbent_anchored_secondary_not_global_lexicographic" if lex else "not_applicable")
    if lex and quality == "failed_or_infeasible":
        lex_claim = "failed_endpoint"
    fixed = scope_record["investment_scope"] in {"fixed_investment_dispatch", "existing_assets_dispatch"}
    return {"schema_version": SCHEMA, "quality": quality, "scope": scope_record,
        "feasible_incumbent_audited": feasible,
        "fixed_or_existing_assets_feasible_operation": bool(fixed and feasible),
        "optimality_scope": scope_record["investment_scope"], "model_scope": "supplied finite LP/MILP and registered solver tolerances only",
        "solver_stages": stages, "lexicographic_claim": lex_claim,
        "strict_global_lexicographic_optimum_claimed": False,
        "primary_incumbent_anchor": finite_number(summary.get("primary_incumbent_value")),
        "primary_objective_allowance": finite_number(summary.get("primary_objective_allowance")),
        "cash_cost_KPI_interval": {"status": "not_separately_solved", "lower": None, "upper": None},
        "carbon_KPI_interval": {"status": "not_separately_solved", "lower": None, "upper": None},
        "KPI_gap_scaling_performed": False, "engineering_or_empirical_validation_claimed": False,
        "limitations": ["A solver certificate covers this stage objective and feasible region only.",
            "Fixed-investment dispatch optimality does not prove the joint investment plan optimal.",
            "Relative MIP gap cannot be multiplied by cash cost, carbon, CAPEX or another KPI to invent an interval.",
            "Two-stage incumbent allowances and solver tolerances are not a strict exact global lexicographic proof."]}
