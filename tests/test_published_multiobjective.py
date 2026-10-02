"""Evidence checks for the published LP replication, including failure paths."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("published_validation", ROOT / "scripts/validate_published_multiobjective.py")
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


@pytest.fixture
def inputs():
    return json.loads((validation.DATA / "published_input.json").read_text(encoding="utf-8"))


@pytest.fixture
def protocol():
    return json.loads((validation.DATA / "predeclared_plan.json").read_text(encoding="utf-8"))["numerical_protocol"]


@pytest.mark.parametrize("cap,first,second", [
    (180, (20, 20), (4, 40)),
    (200, (20, 25), (8, 40)),
    (220, (20, 30), (12, 40)),
])
def test_independent_exact_boundary_geometry(inputs, cap, first, second):
    model = inputs["model"]
    assert tuple(validation.exact_lexicographic(model, cap, ["f1", "f2"])["x"]) == first
    assert tuple(validation.exact_lexicographic(model, cap, ["f2", "f1"])["x"]) == second


def test_exact_oracle_does_not_call_solver(monkeypatch, inputs):
    def reject(*args, **kwargs):
        raise AssertionError("Oracle cannot use optimization solver")
    monkeypatch.setattr(validation.LinearMILP, "solve", reject)
    result = validation.exact_lexicographic(inputs["model"], 200, ["f1", "f2"], 172)
    assert tuple(result["x"]) == (14, validation.Fraction(65, 2))
    assert result["objectives"] == {"f1": 14, "f2": 172}


def test_independent_geometry_detects_infeasible_threshold(inputs):
    assert not validation.exact_optimum(inputs["model"], 180, "f1", 184)["feasible"]
    assert validation.exact_lexicographic(inputs["model"], 180, ["f1", "f2"], 184) is None


@pytest.mark.parametrize("order,target", [(["f1", "f2"], [20, 160]), (["f2", "f1"], [8, 184])])
def test_actual_core_solver_reproduces_endpoints(tmp_path, inputs, protocol, order, target):
    result = validation.solve_lexicographic(tmp_path / "case", inputs["model"], 200, order, protocol)
    assert [result["objectives"]["f1"], result["objectives"]["f2"]] == pytest.approx(target)
    assert result["stage_2"]["actual_primary_fix"][1] == result["stage_1"]["objectives"][order[0]]
    assert result["stage_2"]["matrix_audit"]["numerical_audit_passed"]
    assert len(result["stage_2"]["all_matrix_details"]["all_rows"]) == 2
    assert not result["calls_HeatPlanningModel_solve_lexicographic"]


def test_corrupted_vector_is_rejected_and_preserved(tmp_path, monkeypatch, inputs, protocol):
    original = validation.LinearMILP.solve
    def corrupt(lp, **kwargs):
        result = original(lp, **kwargs)
        result.x[0] = 21.0
        return result
    monkeypatch.setattr(validation.LinearMILP, "solve", corrupt)
    with pytest.raises(RuntimeError, match="Unexpected solver/audit failure"):
        validation.solve_stage(tmp_path / "bad", inputs["model"], 200, "f1", protocol)
    record = json.loads((tmp_path / "bad/result.json").read_text(encoding="utf-8"))
    assert record["solution_vector"][0] == 21.0
    assert not record["matrix_audit"]["numerical_audit_passed"]
    assert not record["protocol_checks_passed"]


def test_published_targets_are_not_optimization_inputs(inputs):
    first = validation.build_lp(inputs["model"], 200, "f1")
    inputs["validation_reference"]["table_2"]["f1_then_f2"] = [-1000, -2000]
    second = validation.build_lp(inputs["model"], 200, "f1")
    assert validation.clean(validation.model_record(first)) == validation.clean(validation.model_record(second))


def test_lp_unexposed_bounds_are_not_fabricated(tmp_path, inputs, protocol):
    result = validation.solve_stage(tmp_path / "lp", inputs["model"], 200, "f1", protocol)
    for field in ["mip_gap", "mip_dual_bound", "mip_node_count"]:
        if field in result["solver_fields_not_exposed"]:
            assert result["solver_fields"][field] is None
    assert result["independent_exact_oracle"]["optimal_bound_maximization"] == 20
    assert "not a solver-exposed bound" in result["independent_exact_oracle"]["bound_origin"]


def test_expected_infeasible_has_no_matrix_audit_claim(tmp_path, inputs, protocol):
    result = validation.solve_stage(tmp_path / "negative", inputs["model"], 180, "f1", protocol,
                                    epsilon=184, expected_infeasible=True)
    assert result["solver_fields"]["status"] == 2
    assert result["solution_vector"] is None
    assert result["matrix_audit"] is None
    assert result["protocol_checks_passed"]


def test_failure_never_writes_complete_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "OUTPUT", tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("injected stage failure")
    monkeypatch.setattr(validation, "solve_lexicographic", fail)
    with pytest.raises(RuntimeError, match="injected stage failure"):
        validation.run_experiment("failure_case")
    assert (tmp_path / "failure_case/failure.json").is_file()
    assert not (tmp_path / "failure_case/manifest.json").exists()
    failure = json.loads((tmp_path / "failure_case/failure.json").read_text(encoding="utf-8"))
    assert not failure["complete"]
    assert not failure["empirical_validation"]


def test_run_directory_cannot_overwrite_history(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "OUTPUT", tmp_path)
    (tmp_path / "existing").mkdir()
    sentinel = tmp_path / "existing/sentinel.json"
    sentinel.write_text("preserved", encoding="utf-8")
    with pytest.raises(FileExistsError):
        validation.run_experiment("existing")
    assert sentinel.read_text(encoding="utf-8") == "preserved"


def test_nondominated_counts_do_not_assume_five():
    assert validation.nondominated_count([[20, 160], [20, 160], [20, 150], [8, 184]]) == {
        "actual_point_count": 4, "unique_point_count": 3, "nondominated_unique_point_count": 2}


def test_source_provenance_distinguishes_implicit_domain(inputs):
    domain = [entry for entry in inputs["numeric_field_provenance"] if entry["field"].startswith("lower_bounds")]
    assert len(domain) == 2
    assert all(entry["provenance_class"] == "graphical_domain_interpretation" for entry in domain)
    assert not inputs["empirical_validation"]
    assert not inputs["parameter_provenance_approved_for_heating_scenario"]


def test_manifest_detects_artifact_tampering(tmp_path):
    path = tmp_path / "summary.json"
    path.write_text("original", encoding="utf-8")
    validation.write_json(tmp_path / "manifest.json", {
        "complete": True, "artifact_sha256": {"summary.json": validation.sha256(path)}, **validation.FLAGS})
    assert validation.verify_run(tmp_path)["verified"]
    path.write_text("modified", encoding="utf-8")
    assert not validation.verify_run(tmp_path)["verified"]
