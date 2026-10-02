from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from heatplan.experiments import freeze_investment_plan
from heatplan.linear import LinearMILP
from heatplan.model import HeatPlanningModel
from heatplan.solution_quality import (SolveFailure, investment_scope,
    solution_quality_from_summary, solver_stage_evidence)


def evidence(**changes):
    summary = {"solver_status": 0, "objective_mode": "total_cost", "objective_value": 100.,
        "mip_dual_bound": 99.99, "mip_gap": .0001, "solver_requested_mip_gap": .001,
        "solver_integer_variables": 1, "numerical_audit_passed": True,
        "numerical_objective_checked": True, "numerical_solution_finite": True}
    summary.update(changes)
    return summary


def tiny_case():
    config = {"planning": {"discount_rate": .05, "asset_lifetime_years": 20,
        "time_step_hours": 1, "max_unserved_rate": 0, "mip_gap": 0},
        "grades": [{"name": "low", "temperature_c": 45}],
        "nodes": [{"name": "n", "demand_columns": {"low": "heat"}}],
        "sources": [{"name": "ideal", "node": "n", "type": "electric_boiler",
            "resource_kind": "electric", "allowed_grades": ["low"], "supply_temp_c": 90,
            "efficiency": 1, "existing_capacity_kw": 10, "max_new_capacity_kw": 10,
            "capex_yuan_per_kw": 100}], "pipes": [], "storages": []}
    frame = pd.DataFrame({"time": ["2024-01-01T00:00:00+08:00"], "heat": [1.],
        "outdoor_temp": [0.], "electricity_price": [2.], "grid_emission_factor": [.4]})
    return config, frame


def test_bound_keeps_carbon_objective_units_and_does_not_bound_cash_or_capex():
    q = solution_quality_from_summary(evidence(objective_mode="emissions", objective_value=4,
        mip_dual_bound=3.8, mip_gap=.05, solver_requested_mip_gap=.1,
        objective_yuan_per_year=2_000_000, regulated_annualized_capex_yuan_per_year=500_000))
    interval = q["solver_stages"][0]["objective_interval"]
    assert interval["lower"] == 3.8 and interval["upper"] == 4
    assert interval["unit"] == "kg_CO2_solver_linear_expression"
    assert q["cash_cost_KPI_interval"]["lower"] is None
    assert q["carbon_KPI_interval"]["status"] == "not_separately_solved"
    assert not q["KPI_gap_scaling_performed"]


def test_limit_with_high_gap_has_feasible_objective_interval_but_no_optimality():
    q = solution_quality_from_summary(evidence(solver_status=1, mip_gap=.25, mip_dual_bound=75))
    assert q["quality"] == "time_or_iteration_limited_feasible_approximation"
    stage = q["solver_stages"][0]
    assert stage["objective_interval"]["lower"] == 75
    assert not stage["optimality_proven_within_solver_tolerance"]


@pytest.mark.parametrize("changes", [
    {"numerical_audit_passed": None}, {"numerical_objective_checked": None},
    {"objective_value": float("nan")}, {"objective_mode": "unknown"},
    {"solver_status": None}, {"mip_gap": -.1}, {"solver_requested_mip_gap": -1},
    {"mip_dual_bound": 101},
])
def test_missing_or_inconsistent_evidence_never_becomes_certificate(changes):
    assert solution_quality_from_summary(evidence(**changes))["quality"] == "missing_evidence"


@pytest.mark.parametrize("status", [2, 3, 4])
def test_failed_solver_status_cannot_have_certified_upper(status):
    q = solution_quality_from_summary(evidence(solver_status=status))
    assert q["quality"] == "failed_or_infeasible"
    assert q["solver_stages"][0]["objective_interval"]["upper"] is None


def test_status_zero_above_registered_gap_is_not_converged():
    q = solution_quality_from_summary(evidence(mip_gap=.137, solver_requested_mip_gap=.005))
    assert q["quality"] == "feasible_unproven_or_tolerance_unregistered"


def test_lp_null_gap_stays_null_and_no_dual_bound_is_invented():
    q = solution_quality_from_summary(evidence(solver_integer_variables=0, mip_gap=None, mip_dual_bound=None))
    assert q["quality"] == "solver_proven_optimal_within_registered_tolerance"
    stage = q["solver_stages"][0]
    assert stage["mip_gap"] is None
    assert stage["objective_interval"]["lower"] is None
    assert stage["objective_interval"]["upper"] == 100


def test_missing_mip_gap_is_not_lp_without_integer_count_evidence():
    q = solution_quality_from_summary(evidence(solver_integer_variables=None, mip_gap=None))
    assert q["quality"] == "feasible_unproven_or_tolerance_unregistered"


def test_legacy_lex_primary_kpi_anchor_is_not_solver_fun_or_certificate():
    q = solution_quality_from_summary(evidence(primary_objective="emissions", primary_incumbent_value=4,
        primary_objective_allowance=1e-7, primary_solver_status=0, primary_mip_gap=0,
        primary_numerical_audit_passed=True))
    assert q["quality"] == "missing_evidence"
    assert q["primary_incumbent_anchor"] == 4
    assert q["solver_stages"][0]["objective_value"] is None
    assert q["lexicographic_claim"] == "incumbent_anchored_secondary_not_global_lexicographic"


def test_failed_service_audit_overrides_solver_optimal():
    q = solution_quality_from_summary(evidence(emissions_cap_pass=False))
    assert q["quality"] == "failed_or_infeasible"
    assert not q["feasible_incumbent_audited"]


@pytest.mark.parametrize("failure", [{"emissions_cap_pass":False},{"failure_reason":"physical audit failed"}])
def test_overall_failed_endpoint_never_carries_successful_lex_claim(failure):
    summary=evidence(solver_stages=[evidence(),evidence()],primary_objective="total_cost",**failure)
    q=solution_quality_from_summary(summary)
    assert q["quality"] == "failed_or_infeasible"
    assert q["lexicographic_claim"] == "failed_endpoint"


def test_malformed_stage_and_scope_provenance_become_missing_evidence():
    q=solution_quality_from_summary({"solver_stages":["not a stage"]},scope={"label":"joint"})
    assert q["quality"] == "missing_evidence"
    assert q["scope"]["investment_scope"] == "unknown_investment_scope"


def test_fixed_scope_and_quality_are_independent():
    q = solution_quality_from_summary(evidence(), scope="fixed_investment_dispatch")
    assert q["quality"] == "solver_proven_optimal_within_registered_tolerance"
    assert q["fixed_or_existing_assets_feasible_operation"]
    assert q["optimality_scope"] == "fixed_investment_dispatch"


def test_scope_detects_free_fixed_and_mixed_bounds_not_selected_additions():
    variables = {"source_capacity": {"s": 0}}
    assert investment_scope({}, variables, [0], [10])["investment_scope"] == "joint_investment_dispatch"
    assert investment_scope({}, variables, [0], [0])["investment_scope"] == "existing_assets_dispatch"
    marker = {"fixed_investment_accounting": {"scope": "annual_selected_plan_charges"}}
    assert investment_scope(marker, variables, [0], [0])["investment_scope"] == "fixed_investment_dispatch"
    assert investment_scope(marker, variables, [0], [10])["investment_scope"] == "mixed_fixed_and_free_investment_dispatch"


def test_actual_model_exports_registered_budget_and_scope_with_zero_new_capacity_selected():
    config, frame = tiny_case()
    result = HeatPlanningModel(config, frame).solve(time_limit=7, mip_gap=.0001)
    assert result.investments.value.sum() == pytest.approx(0)
    assert result.summary["solution_scope"] == "joint_investment_dispatch"
    assert result.summary["solver_time_limit_seconds"] == 7
    assert result.summary["solver_requested_mip_gap"] == .0001
    assert result.summary["solution_quality"]["quality"] == "solver_proven_optimal_within_registered_tolerance"
    fixed = freeze_investment_plan(config, result)
    replay = HeatPlanningModel(fixed, frame).solve()
    assert replay.summary["solution_scope"] == "fixed_investment_dispatch"
    assert replay.summary["solution_quality"]["fixed_or_existing_assets_feasible_operation"]


def test_actual_carbon_solve_interval_is_not_its_cash_objective():
    config, frame = tiny_case()
    result = HeatPlanningModel(config, frame).solve(objective_mode="emissions")
    stage = result.summary["solver_stages"][0]
    assert stage["objective_value"] == pytest.approx(.4)
    assert result.summary["objective_yuan_per_year"] >= 2
    assert stage["objective_interval"]["unit"] == "kg_CO2_solver_linear_expression"


def test_actual_two_stage_run_preserves_both_modes_budgets_and_scoped_quality():
    config, frame = tiny_case()
    result = HeatPlanningModel(config, frame).solve_lexicographic(primary="emissions", time_limit=9, mip_gap=.001)
    stages = result.summary["solver_stages"]
    assert [s["objective_mode"] for s in stages] == ["emissions", "total_cost"]
    assert [s["solver_time_limit_seconds"] for s in stages] == [9, 9]
    assert result.summary["primary_solver_objective_value"] == pytest.approx(.4)
    assert result.summary["solution_quality"]["lexicographic_claim"] == "two_stage_tolerance_limited_endpoint"
    assert not result.summary["solution_quality"]["strict_global_lexicographic_optimum_claimed"]


def test_actual_lex_first_stage_limit_never_upgrades_after_second_optimal(monkeypatch):
    original, calls = LinearMILP.solve, []
    def limited_first(self, **kwargs):
        result = original(self, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            result.status, result.message = 1, "Time limit reached (controlled feasible fixture)"
            result.mip_gap, result.mip_dual_bound = .2, float(result.fun)*.8
        return result
    monkeypatch.setattr(LinearMILP, "solve", limited_first)
    config, frame = tiny_case()
    result = HeatPlanningModel(config, frame).solve_lexicographic(primary="emissions")
    q = result.summary["solution_quality"]
    assert q["quality"] == "time_or_iteration_limited_feasible_approximation"
    assert q["solver_stages"][1]["optimality_proven_within_solver_tolerance"]
    assert q["lexicographic_claim"] == "incumbent_anchored_secondary_not_global_lexicographic"


def test_infeasible_model_exception_carries_exportable_failure_summary():
    config, frame = tiny_case()
    config["sources"][0].update(existing_capacity_kw=0, max_new_capacity_kw=0)
    with pytest.raises(SolveFailure, match="未得到可行解") as caught:
        HeatPlanningModel(config, frame).solve()
    assert isinstance(caught.value, RuntimeError)
    assert caught.value.summary["solution_quality"]["quality"] == "failed_or_infeasible"
    assert caught.value.summary["solver_stages"][0]["objective_interval"]["upper"] is None


def test_second_stage_failure_preserves_first_stage_evidence(monkeypatch):
    from scipy.optimize import OptimizeResult
    original, calls = LinearMILP.solve, []
    def fail_second(self, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            return OptimizeResult(status=2, message="controlled second-stage infeasible fixture", x=None, fun=None)
        return original(self, **kwargs)
    monkeypatch.setattr(LinearMILP, "solve", fail_second)
    config, frame = tiny_case()
    with pytest.raises(SolveFailure) as caught:
        HeatPlanningModel(config, frame).solve_lexicographic()
    stages = caught.value.summary["solver_stages"]
    assert len(stages) == 2 and stages[0]["solver_status"] == 0 and stages[1]["solver_status"] == 2
    assert stages[0]["objective_value"] == pytest.approx(.4)
    assert caught.value.solution_quality["quality"] == "failed_or_infeasible"


def test_classifier_does_not_mutate_historical_summary():
    summary = evidence(mip_gap=float("nan"))
    before = deepcopy(summary)
    q = solution_quality_from_summary(summary)
    assert summary.keys() == before.keys()
    assert str(summary) == str(before)
    assert q["solver_stages"][0]["mip_gap"] is None


def load_auditor():
    path = Path(__file__).resolve().parents[1] / "scripts/audit_solution_quality.py"
    spec = importlib.util.spec_from_file_location("quality_auditor_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_historical_sidecar_requires_matching_manifest_for_tolerance_and_preserves_inputs(tmp_path):
    auditor = load_auditor()
    root = tmp_path / "historical"
    run = root / "point"
    run.mkdir(parents=True)
    summary = evidence(mip_gap=.013, solver_requested_mip_gap=None)
    del summary["solver_requested_mip_gap"]
    (run/"summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (root/"protocol.json").write_text(json.dumps({"mip_gap_target":.02,"time_limit_per_stage_seconds":120}),encoding="utf-8")
    (root/"manifest.json").write_text(json.dumps({"files":{"point/summary.json":auditor.sha(run/"summary.json")}}),encoding="utf-8")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    out = tmp_path / "sidecar"
    auditor.audit([root],out)
    record = json.loads((out/"summary_properties.json").read_text(encoding="utf-8"))[0]
    assert record["properties"]["quality"] == "solver_proven_optimal_within_registered_tolerance"
    assert record["properties"]["scope"]["investment_scope"] == "unknown_investment_scope"
    assert record["registered_tolerance_source"] == str(root/"protocol.json")
    assert all(path.read_bytes() == content for path, content in before.items())
    with pytest.raises(ValueError,match="output exists"):
        auditor.audit([root],out)


def test_mismatched_manifest_cannot_register_tolerance(tmp_path):
    auditor = load_auditor()
    root = tmp_path / "historical"
    root.mkdir()
    summary = evidence(mip_gap=.013)
    del summary["solver_requested_mip_gap"]
    (root/"summary.json").write_text(json.dumps(summary),encoding="utf-8")
    (root/"protocol.json").write_text(json.dumps({"mip_gap_target":.02}),encoding="utf-8")
    (root/"manifest.json").write_text(json.dumps({"files":{"summary.json":"wrong"}}),encoding="utf-8")
    out=tmp_path/"sidecar"
    auditor.audit([root],out)
    record=json.loads((out/"summary_properties.json").read_text(encoding="utf-8"))[0]
    assert record["registered_tolerance_source"] is None
    assert record["properties"]["quality"] == "feasible_unproven_or_tolerance_unregistered"


def test_recorded_failure_not_counted_twice_or_upgraded_by_solver_optimal(tmp_path):
    auditor=load_auditor()
    root=tmp_path/"history"
    root.mkdir()
    (root/"summary.json").write_text(json.dumps(evidence()),encoding="utf-8")
    (root/"failure.json").write_text(json.dumps({"reason":"independent physical audit failed"}),encoding="utf-8")
    auditor.audit([root],tmp_path/"sidecar")
    records=json.loads((tmp_path/"sidecar/summary_properties.json").read_text(encoding="utf-8"))
    assert len(records)==1 and records[0]["properties"]["quality"] == "failed_or_infeasible"


def test_historical_config_scope_retains_fixed_replay_without_joint_promotion():
    auditor=load_auditor()
    config,_=tiny_case()
    config["fixed_investment_accounting"]={"scope":"annual_selected_plan_charges"}
    config["sources"][0]["max_new_capacity_kw"]=0
    assert auditor.infer_scope(config)["investment_scope"] == "fixed_investment_dispatch"
    config["sources"][0]["max_new_capacity_kw"]=10
    assert auditor.infer_scope(config)["investment_scope"] == "mixed_fixed_and_free_investment_dispatch"
