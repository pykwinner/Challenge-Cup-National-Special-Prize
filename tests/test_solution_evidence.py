from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest
from scipy.optimize import Bounds, LinearConstraint, milp

from heatplan.solution_evidence import classify_result, classify_stage


def stage(**changes):
    value = dict(status=0, primal=100., dual=99., relative_gap=.01, relative_gap_target=.02,
        integer_variables=1, audit_passed=True, incumbent_present=True, objective_checked=True,
        objective=dict(name="cost", unit="CNY", expression_id="registered_cost_expression", sense="minimize", constant=5.))
    value.update(changes)
    return value


def record(**changes):
    value = dict(scope=dict(kind="full_registered_problem", verified=True), input_identity_verified=True,
                 audits=dict(passed=True), stages=[stage()])
    value.update(changes)
    return value


@pytest.mark.parametrize("kind", ["full_registered_problem", "fixed_investment_subproblem", "existing_assets_subproblem", "restricted_baseline_subproblem"])
def test_optimality_respects_registered_feasible_region(kind):
    result = classify_result(record(scope=dict(kind=kind, verified=True)))
    assert result["classification"] == ("optimal_full_registered_problem" if kind == "full_registered_problem" else "optimal_declared_subproblem")
    assert result["cash_cost_KPI_interval"] is None and result["carbon_KPI_interval"] is None
    assert result["KPI_gap_scaling_performed"] is False
    assert result["stage_certificates"][0]["ordered_objective_interval"] == dict(lower=104., upper=105., unit="CNY",
        expression_id="registered_cost_expression", constant_added_to_both_raw_bounds=5.,
        status="ordered_solver_bound_pair", covers_only_this_objective_and_stage_feasible_region=True,
        not_a_statistical_or_KPI_perturbation_interval=True)


@pytest.mark.parametrize("change", [dict(dual=None), dict(integer_variables=None), dict(relative_gap=None),
    dict(relative_gap_target=None), dict(relative_gap=-.1), dict(relative_gap_target=-.1),
    dict(relative_gap_target=1), dict(dual=100.0000000001), dict(relative_gap=0), dict(status=True)])
def test_missing_or_contradictory_evidence_cannot_certify_mip(change):
    assert classify_stage(stage(**change))["certificate_within_solver_tolerance"] is False


def test_limit_with_audited_incumbent_keeps_lower_bound_but_never_becomes_optimal():
    result = classify_stage(stage(status=1, dual=100, relative_gap=0, relative_gap_target=0))
    assert result["quality"] == "time_or_iteration_limited_feasible"
    assert result["ordered_objective_interval"]["lower"] == 105


@pytest.mark.parametrize("status", [1, 2, 3, 4])
def test_no_incumbent_is_not_proven_infeasible_except_status_two(status):
    result = classify_stage(stage(status=status, primal=None, incumbent_present=False, audit_passed=False))
    assert result["quality"] == "no_feasible_incumbent"
    assert result["infeasibility_proven_for_stage"] is (status == 2)
    assert result["ordered_objective_interval"]["upper"] is None


def lex_record():
    primary = stage(role="primary", primal=100, dual=100, relative_gap=0)
    secondary = stage(role="secondary", primal=2, dual=2, relative_gap=0,
        objective=dict(name="carbon", unit="kg_CO2", expression_id="registered_carbon", sense="minimize", constant=0))
    return record(stages=[primary, secondary], lexicographic=dict(primary_anchor=105,
        freeze_allowance=.001, returned_primary=105.0005, freeze_contract_verified=True,
        secondary_feasible_region="registered model plus incumbent freeze"))


def test_two_stage_primary_bound_and_secondary_certificate_remain_separate():
    result = classify_result(lex_record())
    assert result["lexicographic"]["claim"] == "two_stage_tolerance_limited_endpoint"
    assert result["lexicographic"]["primary_stage_certificate"]["ordered_objective_interval"]["unit"] == "CNY"
    assert result["lexicographic"]["secondary_stage_certificate"]["ordered_objective_interval"]["unit"] == "kg_CO2"
    assert result["lexicographic"]["strict_global_lexicographic_optimum_claimed"] is False


@pytest.mark.parametrize("mutation", ["first_limit", "second_failure", "freeze_violation", "anchor_mismatch", "missing_contract", "same_objective"])
def test_secondary_success_never_repairs_primary_or_freeze_failure(mutation):
    value = lex_record()
    if mutation == "first_limit":
        value["stages"][0]["status"] = 1
    elif mutation == "second_failure":
        value["stages"][1].update(status=2, primal=None, incumbent_present=False, audit_passed=False)
    elif mutation == "freeze_violation":
        value["lexicographic"]["returned_primary"] = 106
    elif mutation == "anchor_mismatch":
        value["lexicographic"]["primary_anchor"] = 99
    elif mutation == "missing_contract":
        value["lexicographic"]["freeze_contract_verified"] = False
    else:
        value["stages"][1]["objective"] = deepcopy(value["stages"][0]["objective"])
    result = classify_result(value)
    assert not result["classification"].startswith("optimal_")
    assert result["lexicographic"]["claim"] != "two_stage_tolerance_limited_endpoint"


@pytest.mark.parametrize("field", ["scope", "audit", "identity"])
def test_missing_global_prerequisites_fail_closed(field):
    value = record()
    if field == "scope":
        value["scope"]["verified"] = False
    elif field == "audit":
        value["audits"]["passed"] = False
    else:
        value["input_identity_verified"] = False
    result = classify_result(value)
    assert result["classification"] in {"insufficient_evidence", "audit_failed"}


def test_incomplete_enumeration_suppresses_global_lower_bound():
    value = record(global_primary=stage(), enumeration_complete=False)
    result = classify_result(value)
    assert result["global_primary_certificate"]["ordered_objective_interval"]["lower"] is None
    assert result["classification"] == "audited_feasible_optimality_unproven"


def test_method_policy_library_is_not_optimization_certificate():
    result = classify_result(record(scope=dict(kind="finite_policy_library", verified=True), stages=[]))
    assert result["classification"] == "verified_method_reference"
    assert result["feasible_incumbent_audited"] is False


def test_actual_analytic_mip_bound_maps_only_to_the_minimized_expression():
    # min x+y, integers >=0, 2x+y>=3 has optimum2 at(1,1) or(2,0).
    result = milp([1., 1.], integrality=[1, 1], bounds=Bounds([0., 0.], [3., 3.]),
        constraints=LinearConstraint([[2., 1.]], [3.], [float("inf")]), options={"mip_rel_gap": 0})
    value = stage(status=result.status, primal=result.fun, dual=result.mip_dual_bound,
        relative_gap=result.mip_gap, relative_gap_target=0)
    value["objective"]["constant"] = 0
    certificate = classify_stage(value)
    assert certificate["certificate_within_solver_tolerance"]
    assert certificate["ordered_objective_interval"]["lower"] == pytest.approx(2)


def reporter():
    spec = importlib.util.spec_from_file_location("solution_evidence_report_tests", Path(__file__).resolve().parents[1]/"scripts/report_solution_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_hash_mutation_is_rejected_and_input_not_rewritten(tmp_path):
    module = reporter()
    run = tmp_path/"run"
    run.mkdir()
    (run/"summary.json").write_text("{}", encoding="utf-8")
    (run/"manifest.json").write_text(json.dumps(dict(files={"summary.json": "0"*64})), encoding="utf-8")
    reader = module.Reader(tmp_path)
    before = (run/"summary.json").read_bytes()
    _, passed, errors = reader.manifest(run/"manifest.json")
    assert not passed and "mismatch" in errors[0]
    assert (run/"summary.json").read_bytes() == before


def test_manifest_cannot_read_outside_registered_run(tmp_path):
    module = reporter()
    run = tmp_path/"run"
    run.mkdir()
    (tmp_path/"outside.txt").write_text("do not inspect", encoding="utf-8")
    (run/"manifest.json").write_text(json.dumps(dict(files={"../outside.txt": "0"*64})), encoding="utf-8")
    reader = module.Reader(tmp_path)
    _, passed, errors = reader.manifest(run/"manifest.json")
    assert not passed and "traversal" in errors[0]
    assert "outside.txt" not in reader.identities


def test_report_output_cannot_overwrite_or_enter_inspected_copy(tmp_path):
    module = reporter()
    with pytest.raises(ValueError, match="outside"):
        module.report(tmp_path, tmp_path/"new")
    with pytest.raises(ValueError, match="overwrite"):
        module.report(tmp_path, tmp_path)


def test_classifier_preserves_original_structures():
    value = lex_record()
    before = deepcopy(value)
    classify_result(value)
    assert value == before


def numerical_tolerance(verified=True):
    return dict(absolute=1e-6, relative=1e-8, verified=verified,
                basis="pre-existing manifest-frozen auditor")


def test_declared_audit_tolerance_does_not_sort_or_clip_raw_bound_pair():
    value = stage(integer_variables=0, dual=100.0000000001,
                  numerical_tolerance=numerical_tolerance())
    result = classify_stage(value)
    assert result["certificate_within_solver_tolerance"]
    assert result["exact_literal_bound_order_passed"] is False
    assert result["raw_signed_dual_minus_primal"] > 0
    assert result["bound_order_within_declared_numerical_tolerance"]
    assert result["ordered_objective_interval"]["lower"] is None
    assert result["raw_objective_bound_pair"]["lower"] > result["raw_objective_bound_pair"]["upper"]
    value["numerical_tolerance"]["verified"] = False
    assert not classify_stage(value)["certificate_within_solver_tolerance"]


def test_original_freeze_allowance_and_roundtrip_tolerance_are_separate():
    value = lex_record()
    value["lexicographic"].update(returned_primary=105.00100000001,
        numerical_tolerance=numerical_tolerance(), numerical_scale=100)
    result = classify_result(value)
    lex = result["lexicographic"]
    assert lex["exact_literal_freeze_passed"] is False
    assert lex["returned_freeze_passed"] is True
    assert lex["freeze_allowance"] == .001
    assert lex["raw_signed_freeze_residual"] > 0
    assert result["classification"] == "optimal_full_registered_problem"
    value["lexicographic"]["returned_primary"] = 105.002
    assert classify_result(value)["classification"] == "insufficient_evidence"


def test_missing_result_audit_never_promotes_lex_endpoint():
    value = lex_record()
    value["input_identity_verified"] = False
    assert classify_result(value)["lexicographic"]["claim"] == "incomplete_or_unverified_endpoint"


def test_no_incumbent_and_no_output_audit_is_not_an_audited_incumbent_failure():
    value = record(audits=dict(passed=False), stages=[stage(status=2, primal=None,
        incumbent_present=False, audit_passed=False)])
    assert classify_result(value)["classification"] == "no_feasible_incumbent"


def test_objective_affine_constant_overflow_is_not_a_certificate():
    value = stage(primal=1e308, dual=1e308)
    value["objective"]["constant"] = 1e308
    assert classify_stage(value)["quality"] == "insufficient_evidence"


def test_empty_selected_inventory_fails_closed_and_remains_read_only(tmp_path):
    module = reporter()
    root = tmp_path/"inputs"
    root.mkdir()
    result = module.report(root, tmp_path/"new_report")
    assert result["selected_input_inventory_passed"] is False
    assert result["records"] == []
    assert list(root.iterdir()) == []


def test_zero_mip_target_roundoff_is_separate_from_real_target_violation():
    value = stage(primal=5973.817591164886, dual=5973.817591164881,
        relative_gap=6.089872600851063e-16, relative_gap_target=0,
        numerical_tolerance=numerical_tolerance())
    result = classify_stage(value)
    assert result["certificate_within_solver_tolerance"]
    assert result["exact_literal_relative_gap_target_passed"] is False
    value.update(dual=5970, relative_gap=(value["primal"]-5970)/value["primal"])
    assert not classify_stage(value)["certificate_within_solver_tolerance"]


def test_malformed_scope_cannot_crash_into_an_optimality_claim():
    assert classify_result(record(scope=dict(kind=["full_registered_problem"],verified=True)))["classification"] == "insufficient_evidence"
