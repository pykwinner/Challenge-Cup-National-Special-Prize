"""Fail-closed, read-only certificates for *declared* finite minimization problems.

This is an additive evidence layer; it neither solves nor changes solver semantics.
An input adapter must establish objective identity, units, scope and audit identity.
Reported bounds are solver evidence, not independently certified dual vectors.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import Any

SCHEMA = "heatplan.solution_evidence.v1"
SCOPE_KINDS = {
    "full_registered_problem", "fixed_investment_subproblem",
    "existing_assets_subproblem", "restricted_baseline_subproblem",
    "mixed_fixed_free_subproblem", "fixed_candidate_subproblem",
    "finite_policy_library", "method_reference", "unknown",
}


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def integer(value: Any) -> int | None:
    result = number(value)
    return int(result) if result is not None and result == int(result) else None


def declared_tolerance(value: Any, scale: float) -> float | None:
    """Only explicit, identity-verified pre-existing tolerances may be applied."""
    if not isinstance(value, Mapping) or value.get("verified") is not True or not value.get("basis"):
        return None
    absolute, relative = number(value.get("absolute")), number(value.get("relative"))
    if absolute is None or relative is None or absolute < 0 or relative < 0:
        return None
    result = absolute + relative * scale
    return result if isfinite(result) else None


def classify_stage(stage: Mapping[str, Any]) -> dict:
    """Classify a normalized raw stage without using pre-existing quality labels.

    objective.{name,unit,expression_id,sense,constant} apply to BOTH raw bounds.
    No missing integer count/gap/tolerance becomes zero, and no objective change
    or KPI gap multiplication is performed. Inverted bounds remain inverted raw
    evidence and are never sorted or clipped to manufacture an interval.
    """
    stage = stage if isinstance(stage, Mapping) else {}
    objective = stage.get("objective", {})
    objective = objective if isinstance(objective, Mapping) else {}
    status = integer(stage.get("status"))
    primal, dual = number(stage.get("primal")), number(stage.get("dual"))
    constant = number(objective.get("constant"))
    identity = all(isinstance(objective.get(k), str) and objective[k].strip()
                   for k in ("name", "unit", "expression_id"))
    identity = identity and objective.get("sense") == "minimize" and constant is not None
    identity = identity and all(v is None or number(v + constant) is not None for v in (primal, dual))
    audit_passed = stage.get("audit_passed") is True
    present = stage.get("incumbent_present") is True and primal is not None
    feasible = audit_passed and present and status in (0, 1)
    checked = stage.get("objective_checked") is True
    gap, target = number(stage.get("relative_gap")), number(stage.get("relative_gap_target"))
    integers = integer(stage.get("integer_variables"))
    scale = max(1., abs(primal or 0.), abs(dual or 0.))
    tolerance = declared_tolerance(stage.get("numerical_tolerance"), scale)
    signed_bound_residual = dual - primal if dual is not None and primal is not None else None
    exact_ordered = signed_bound_residual is not None and signed_bound_residual <= 0
    numerical_ordered = exact_ordered or (tolerance is not None and signed_bound_residual is not None
                                        and signed_bound_residual <= tolerance)
    reasons = []
    if stage.get("audit_passed") is False and present:
        quality = "audit_failed"
        reasons.append("A saved incumbent failed the required numerical/physical/ledger audit.")
    elif not present and status in (1, 2, 3, 4):
        quality = "no_feasible_incumbent"
        reasons.append("No accepted incumbent; only status2 proves this declared stage infeasible.")
    elif not feasible or not checked or not identity:
        quality = "insufficient_evidence"
        reasons.append("Audited incumbent/objective identity or checked objective missing.")
    elif status == 1:
        quality = "time_or_iteration_limited_feasible"
        reasons.append("A solver limit is never promoted by a zero or missing gap.")
    elif integers is None or integers < 0:
        quality = "feasible_unproven"
        reasons.append("Missing actual integer-variable count does not imply LP.")
    elif dual is not None and not numerical_ordered:
        quality = "feasible_unproven"
        reasons.append("Raw dual exceeds primal; retain values, suppress an ordered interval/certificate.")
    elif gap is not None and gap < 0:
        quality = "feasible_unproven"
        reasons.append("Negative relative gap is not a valid MIP certificate.")
    elif integers == 0:
        quality = "solver_optimal_within_registered_tolerance"
        reasons.append("LP optimal termination; no missing dual bound or numerical tolerance is fabricated.")
    elif dual is None or gap is None or target is None or not 0 <= target < 1:
        quality = "feasible_unproven"
        reasons.append("MIP requires finite same-objective dual, gap and registered target.")
    elif primal != 0 and abs(gap * abs(primal) - (primal-dual)) > (tolerance or 0.):
        quality = "feasible_unproven"
        reasons.append("Reported relative gap contradicts its raw primal/dual objective values.")
    elif primal == 0 and dual != 0:
        quality = "feasible_unproven"
        reasons.append("Zero primal with nonzero dual cannot use a finite relative-gap certificate.")
    elif gap <= target:
        quality = "solver_optimal_within_registered_tolerance"
        reasons.append("MIP optimal termination and raw gap within the registered target.")
    else:
        quality = "feasible_unproven"
        reasons.append("Reported MIP gap exceeds its registered target.")
    ordered = feasible and checked and identity and exact_ordered
    upper = primal + constant if feasible and checked and identity else None
    lower = dual + constant if ordered else None
    interval = {"lower": lower, "upper": upper,
        "unit": objective.get("unit") if identity else None,
        "expression_id": objective.get("expression_id") if identity else None,
        "constant_added_to_both_raw_bounds": constant if identity else None,
        "status": "ordered_solver_bound_pair" if ordered else
                  "feasible_upper_only" if upper is not None else "unavailable",
        "covers_only_this_objective_and_stage_feasible_region": True,
        "not_a_statistical_or_KPI_perturbation_interval": True}
    return {"role": stage.get("role", "single"), "quality": quality,
        "status": status, "solver_message": stage.get("message"),
        "raw_primal": primal, "raw_dual": dual, "raw_relative_gap": gap,
        "registered_relative_gap_target": target, "integer_variables": integers,
        "raw_signed_dual_minus_primal": signed_bound_residual,
        "exact_literal_bound_order_passed": exact_ordered,
        "declared_numerical_tolerance": deepcopy(stage.get("numerical_tolerance")),
        "evaluated_numerical_allowance": tolerance,
        "bound_order_within_declared_numerical_tolerance": numerical_ordered if dual is not None else None,
        "raw_objective_bound_pair": {"lower": dual + constant if dual is not None and identity else None,
            "upper": primal + constant if primal is not None and identity else None,
            "ordered": exact_ordered, "unit": objective.get("unit") if identity else None},
        "objective": deepcopy(dict(objective)), "feasible_incumbent_audited": feasible,
        "certificate_within_solver_tolerance": quality == "solver_optimal_within_registered_tolerance",
        "ordered_objective_interval": interval,
        "infeasibility_proven_for_stage": status == 2 and not present,
        "independent_dual_vector_certificate": False,
        "elapsed_seconds": number(stage.get("elapsed_seconds")), "reasons": reasons}


def classify_result(record: Mapping[str, Any]) -> dict:
    """Classify a normalized result, with scope and optimality on separate axes."""
    record = record if isinstance(record, Mapping) else {}
    scope = record.get("scope", {})
    scope = scope if isinstance(scope, Mapping) else {}
    kind = scope.get("kind", "unknown")
    if kind not in SCOPE_KINDS:
        kind = "unknown"
    scope_verified = scope.get("verified") is True and kind != "unknown"
    stages = record.get("stages", [])
    stages = [classify_stage(s) for s in stages] if isinstance(stages, list) else []
    audits = record.get("audits", {})
    audits = audits if isinstance(audits, Mapping) else {}
    identity_verified = record.get("input_identity_verified") is True
    reference = kind in {"finite_policy_library", "method_reference"}
    failures = []
    if not identity_verified:
        failures.append("Input identity is missing/mismatched or changed during inspection.")
    if audits.get("passed") is not True:
        failures.append("A required result audit is missing or did not pass.")
    if not scope_verified:
        failures.append("Actual declared optimization scope has not been established.")
    lex_raw = record.get("lexicographic")
    lex = {"present": lex_raw is not None, "claim": "not_applicable",
           "strict_global_lexicographic_optimum_claimed": False,
           "primary_stage_certificate": stages[0] if stages else None,
           "secondary_stage_certificate": stages[1] if len(stages) > 1 else None}
    if lex_raw is not None:
        lex_raw = lex_raw if isinstance(lex_raw, Mapping) else {}
        anchor = number(lex_raw.get("primary_anchor"))
        allowance = number(lex_raw.get("freeze_allowance"))
        returned = number(lex_raw.get("returned_primary"))
        limit = number(anchor + allowance) if anchor is not None and allowance is not None and allowance >= 0 else None
        primary_upper = stages[0]["ordered_objective_interval"]["upper"] if stages else None
        anchor_matches = primary_upper is not None and anchor is not None and abs(anchor-primary_upper) <= 1e-9 * max(1., abs(anchor))
        residual = returned - limit if limit is not None and returned is not None else None
        freeze_exact = residual is not None and residual <= 0
        freeze_numerical = declared_tolerance(lex_raw.get("numerical_tolerance"),
            abs(number(lex_raw.get("numerical_scale")) or 0.))
        freeze_passed = freeze_exact or (freeze_numerical is not None and residual is not None and residual <= freeze_numerical)
        contract_verified = (lex_raw.get("freeze_contract_verified") is True and identity_verified
                             and scope_verified and audits.get("passed") is True)
        complete = len(stages) == 2 and all(s["feasible_incumbent_audited"] for s in stages)
        complete = complete and stages[0]["role"] == "primary" and stages[1]["role"] == "secondary"
        complete = complete and stages[0]["objective"].get("name") != stages[1]["objective"].get("name")
        lex.update(primary_anchor=anchor, freeze_allowance=allowance, freeze_limit=limit,
            returned_primary=returned, anchor_matches_primary_objective=anchor_matches,
            raw_signed_freeze_residual=residual, exact_literal_freeze_passed=freeze_exact,
            declared_numerical_tolerance=deepcopy(lex_raw.get("numerical_tolerance")),
            evaluated_numerical_allowance=freeze_numerical,
            freeze_contract_verified=contract_verified, returned_freeze_passed=freeze_passed,
            freeze_tolerance_source=lex_raw.get("freeze_tolerance_source"),
            secondary_feasible_region=lex_raw.get("secondary_feasible_region"),
            secondary_global_across_all_investment_candidates_claimed=False,
            claim="two_stage_tolerance_limited_endpoint" if complete and contract_verified and anchor_matches and freeze_passed
                  and all(s["certificate_within_solver_tolerance"] for s in stages) else
                  "incumbent_anchored_secondary_only" if complete and contract_verified and anchor_matches and freeze_passed else
                  "incomplete_or_unverified_endpoint")
        if not (complete and contract_verified and anchor_matches and freeze_passed):
            failures.append("Two-stage freeze/anchor/returned-primary evidence is incomplete or violated.")
    if reference:
        classification = "verified_method_reference" if not failures else "insufficient_evidence"
        feasible = False
    else:
        feasible = bool(stages and all(s["feasible_incumbent_audited"] for s in stages)
                        and audits.get("passed") is True and identity_verified)
        qualities = {s["quality"] for s in stages}
        if audits.get("passed") is False or "audit_failed" in qualities:
            classification = "audit_failed"
        elif not stages or not identity_verified or not scope_verified:
            classification = "insufficient_evidence"
        elif "no_feasible_incumbent" in qualities:
            classification = "no_feasible_incumbent"
        elif failures or "insufficient_evidence" in qualities:
            classification = "insufficient_evidence"
        elif "time_or_iteration_limited_feasible" in qualities:
            classification = "time_or_iteration_limited_feasible"
        elif feasible and all(s["certificate_within_solver_tolerance"] for s in stages):
            classification = "optimal_full_registered_problem" if kind == "full_registered_problem" else "optimal_declared_subproblem"
        else:
            classification = "audited_feasible_optimality_unproven"
    # Optional enumeration bounds refer only to the GLOBAL PRIMARY objective.
    global_primary = record.get("global_primary")
    global_interval = None
    if isinstance(global_primary, Mapping):
        global_interval = classify_stage({**global_primary, "role": "enumerated_global_primary"})
        complete_enum = record.get("enumeration_complete") is True
        global_interval["complete_candidate_enumeration_verified"] = complete_enum
        if not complete_enum:
            global_interval["certificate_within_solver_tolerance"] = False
            global_interval["ordered_objective_interval"]["lower"] = None
            global_interval["ordered_objective_interval"]["status"] = "incomplete_enumeration_upper_only"
            failures.append("Incomplete candidate enumeration: global lower bound suppressed.")
            if classification.startswith("optimal_"):
                classification = "audited_feasible_optimality_unproven"
        if global_interval["raw_objective_bound_pair"]["lower"] is None or not global_interval["bound_order_within_declared_numerical_tolerance"]:
            global_interval["certificate_within_solver_tolerance"] = False
            if classification.startswith("optimal_"):
                classification = "audited_feasible_optimality_unproven"
        global_interval["certificate_basis"] = global_primary.get("certificate_basis")
        allowance = number(global_primary.get("derived_optimality_allowance"))
        global_interval["derived_optimality_allowance"] = allowance
        if (not identity_verified or not scope_verified or audits.get("passed") is not True or
                not global_primary.get("certificate_basis") or allowance is None or allowance < 0 or global_interval["raw_primal"] is None or
                global_interval["raw_dual"] is None or global_interval["raw_primal"] - global_interval["raw_dual"] > allowance):
            global_interval["certificate_within_solver_tolerance"] = False
            if classification.startswith("optimal_"):
                classification = "audited_feasible_optimality_unproven"
        if lex_raw is not None and classification == "optimal_full_registered_problem":
            classification = "primary_full_problem_secondary_candidate_subproblem"
    return {"schema": SCHEMA, "classification": classification,
        "scope": {**deepcopy(dict(scope)), "kind": kind, "verified": scope_verified},
        "feasible_incumbent_audited": feasible, "stage_certificates": stages,
        "lexicographic": lex, "global_primary_certificate": global_interval,
        "input_identity_verified": identity_verified, "required_audits": deepcopy(dict(audits)),
        "unresolved_evidence": failures, "cash_cost_KPI_interval": None,
        "carbon_KPI_interval": None, "KPI_gap_scaling_performed": False,
        "complete_Pareto_frontier_claimed": False, "engineering_or_field_validation_claimed": False,
        "limits": ["All conclusions apply only to the declared finite model and solver tolerances.",
            "A fixed investment/existing-asset certificate does not prove the free investment problem optimal.",
            "Secondary bounds cover the primary-incumbent freeze region, not the unrestricted problem.",
            "Finite epsilon samples and a finite policy library do not certify a complete Pareto set.",
            "Raw objective bounds are not simultaneous cost/carbon/CAPEX uncertainty intervals."]}
