from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from heatplan.chronology import annual_replay_acceptance_audit, representative_period_coverage_audit, seasonal_storage_postcheck
from heatplan.heating_season import prepare_heating_season_timeseries, run_fixed_investment_heating_season_replay
from heatplan.linear import LinearMILP


def config():
    return {
        "planning": {"discount_rate": 0.05, "asset_lifetime_years": 20, "time_step_hours": 1,
                     "representative_day_weight": 1, "max_unserved_rate": 0, "max_scenario_unserved_rate": 0},
        "grades": [{"name": "low", "temperature_c": 45}],
        "nodes": [{"name": "n", "demand_columns": {"low": "heat"}}],
        "sources": [{"name": "source", "node": "n", "type": "direct_heat", "resource_kind": "fossil",
                     "supply_temp_c": 90, "allowed_grades": ["low"], "existing_capacity_kw": 10,
                     "max_new_capacity_kw": 0, "variable_cost_yuan_per_kwh_heat": 1}],
        "pipes": [], "storages": [],
    }


def frame():
    return pd.DataFrame({"time": range(4), "timestamp": pd.date_range("2024-12-31T22:00:00+08:00", periods=4, freq="h"),
                         "heat": [1., 2., 1., 2.], "electricity_price": 0.6, "grid_emission_factor": 0.5})


def scope():
    return {"profile": "cross_year_smoke", "start_inclusive": "2024-12-31T22:00:00+08:00",
            "end_exclusive": "2025-01-01T02:00:00+08:00", "expected_hours": 4}


def test_numerical_audit_recomputes_feasibility_and_original_objective():
    p = LinearMILP()
    x = p.add_var("x", ub=2, integer=True, obj=3)
    p.add_constraint({x: 1}, lb=1)
    solved = p.solve()
    p.objective[x] = 999  # emulate restored cost after an emissions solve
    audit = p.audit_solution(solved.x)
    assert audit["numerical_audit_passed"]
    assert audit["numerical_objective_recompute_error"] == pytest.approx(0)
    invalid = p.audit_solution(np.array([0.5]))
    assert not invalid["numerical_audit_passed"]
    assert invalid["numerical_constraint_max_violation"] == pytest.approx(.5)
    assert invalid["numerical_integrality_max_violation"] == pytest.approx(.5)
    assert not p.audit_solution(np.array([np.nan]))["numerical_audit_passed"]
    assert p.audit_solution(np.array([3.]))["numerical_bound_max_violation"] == pytest.approx(1)


def test_large_opposite_bound_does_not_hide_negative_capacity_or_flow():
    p = LinearMILP()
    x = p.add_var("capacity", lb=0., ub=1e12)
    p.add_constraint({x: 1.}, lb=0., ub=1e12)
    audit = p.audit_solution(np.array([-1.]))
    assert not audit["numerical_audit_passed"]
    assert audit["numerical_constraint_max_scaled_violation"] > 1.
    assert audit["numerical_bound_max_scaled_violation"] > 1.


def storage_fixture():
    c = config()
    c["storages"] = [{"name": "tank", "existing_capacity_kwh": 2., "max_new_capacity_kwh": 0.,
                      "initial_soc_fraction": 0., "final_soc_min_fraction": 0., "boundary_mode": "fixed",
                      "charge_efficiency": 1., "discharge_efficiency": 1., "self_loss_per_hour": 0.,
                      "power_to_energy_ratio_per_hour": 1.}]
    records = []
    for category, values in {"storage_soc": [1., 1., 0.], "storage_charge": [1., 0., 0.], "storage_discharge": [0., 0., 1.]}.items():
        for t, value in enumerate(values):
            records.append({"asset": "tank", "category": category, "time_index": t, "value": value, "boundary_group": "season"})
    expected = pd.DataFrame({"boundary_group": ["season"] * 3})
    return c, pd.DataFrame(records), expected


def test_independent_storage_audit_detects_interior_error_that_preserves_endpoints():
    c, dispatch, expected = storage_fixture()
    valid = seasonal_storage_postcheck(c, dispatch, expected_timeseries=expected).iloc[0]
    assert valid["state_trajectory_pass"]
    dispatch.loc[(dispatch.category == "storage_soc") & (dispatch.time_index == 1), "value"] = 2.
    invalid = seasonal_storage_postcheck(c, dispatch, expected_timeseries=expected).iloc[0]
    assert invalid["boundary_condition_pass"]  # original boundary-only check misses this
    assert not invalid["state_trajectory_pass"]
    assert invalid["maximum_soc_transition_residual_kwh"] == pytest.approx(1.)


@pytest.mark.parametrize("failure", ["missing_row", "duplicate_row", "missing_group", "out_of_bounds"])
def test_storage_evidence_and_bounds(failure):
    c, dispatch, expected = storage_fixture()
    if failure == "missing_row": dispatch = dispatch.drop(index=4)
    elif failure == "duplicate_row": dispatch = pd.concat([dispatch, dispatch.iloc[[0]]], ignore_index=True)
    elif failure == "missing_group": expected = pd.concat([expected, pd.DataFrame({"boundary_group": ["missing"]})], ignore_index=True)
    else: dispatch.loc[(dispatch.category == "storage_soc") & (dispatch.time_index == 1), "value"] = 3.
    audit = seasonal_storage_postcheck(c, dispatch, expected_timeseries=expected)
    assert not audit.state_trajectory_pass.all()


def certificate_arguments():
    numerical = {"solver_status": 0, "numerical_audit_passed": True, "numerical_objective_checked": True}
    return dict(config=config(), chronology_audit={"chronology_valid": True, "is_full_year_horizon": True, "is_calendar_year_aligned": True},
                representative_coverage_audit=pd.DataFrame({"coverage_status": ["pass"]}),
                investment_freeze_audit=pd.DataFrame({"freeze_ok": [True]}),
                fixed_investment_readiness_audit={"readiness_pass": True}, planning_summary=numerical.copy(),
                replay_summary={**numerical, "unserved_rate": 0., "worst_scenario_unserved_rate": 0.}, storage_boundary_audit=pd.DataFrame())


def test_certificate_rejects_missing_numerical_and_storage_evidence():
    args = certificate_arguments()
    assert annual_replay_acceptance_audit(**args)["certification_pass"]
    args["planning_summary"].pop("numerical_audit_passed")
    args["config"]["storages"] = [{"name": "missing"}]
    failed = annual_replay_acceptance_audit(**args)
    assert "planning_numerical_feasibility" in failed["failed_gates"]
    assert "storage_evidence_complete" in failed["failed_gates"]


def test_certificate_supports_boolean_unit_commitment_flag():
    args = certificate_arguments()
    args["config"]["sources"][0]["unit_commitment"] = True
    args["replay_summary"].update(unit_commitment_max_transition_residual=0., unit_commitment_max_indicator_fractionality=0.)
    assert annual_replay_acceptance_audit(**args)["certification_pass"]
    args["replay_summary"]["unit_commitment_max_transition_residual"] = 1.
    assert "unit_commitment_transition_audit" in annual_replay_acceptance_audit(**args)["failed_gates"]


@pytest.mark.parametrize("status", ["fail", "unknown", None])
def test_certificate_accepts_only_explicit_coverage_pass(status):
    args = certificate_arguments()
    args["representative_coverage_audit"] = pd.DataFrame({"coverage_status": [status]})
    assert "representative_period_coverage" in annual_replay_acceptance_audit(**args)["failed_gates"]


def test_cooling_coverage_detects_missing_peak_and_energy():
    c = config()
    c["multi_energy_services"] = {"cooling": {"enabled": True, "demand_columns": {"n": "cool"}}}
    rep, full = frame(), frame()
    rep["cool"], full["cool"] = [1., 1., 1., 1.], [1., 5., 1., 5.]
    row = representative_period_coverage_audit(c, rep, full).set_index("column").loc["cool"]
    assert row["service"] == "cooling"
    assert row["coverage_status"] == "review"
    assert "demand_peak_under_captured" in row["findings"]


def test_service_period_cross_year_and_timezone_conversion():
    selected, audit = prepare_heating_season_timeseries(config(), frame(), scope())
    assert len(selected) == 4 and selected.period_weight.eq(1).all()
    assert audit["exact_scope_match"] and audit["horizon_hours"] == 4
    naive = frame()
    naive["timestamp"] = naive.timestamp.dt.tz_localize(None)
    with pytest.raises(ValueError, match="input_timezone"):
        prepare_heating_season_timeseries(config(), naive, scope())
    converted, _ = prepare_heating_season_timeseries(config(), naive, scope(), input_timezone="Asia/Shanghai")
    assert converted.timestamp.equals(selected.timestamp)
    with pytest.raises(ValueError, match="exactly match"):
        prepare_heating_season_timeseries(config(), naive, scope(), input_timezone="UTC")


@pytest.mark.parametrize("kind", ["missing", "duplicate", "shifted", "wrong_hours", "mixed_timezone"])
def test_invalid_service_period_is_rejected_before_solving(kind):
    data, definition = frame(), scope()
    if kind == "missing": data = data.iloc[:-1]
    elif kind == "duplicate": data = pd.concat([data, data.iloc[[0]]], ignore_index=True)
    elif kind == "shifted": data["timestamp"] += pd.Timedelta(hours=1)
    elif kind == "wrong_hours": definition["expected_hours"] = 5
    else:
        data["timestamp"] = data.timestamp.astype(object)
        data.loc[0, "timestamp"] = pd.Timestamp("2024-12-31 22:00")
    with pytest.raises(ValueError):
        prepare_heating_season_timeseries(config(), data, definition)


def test_long_period_requires_exact_integer_step_boundary():
    definition = {"start_inclusive": "2024-10-10T00:00:00+08:00", "end_exclusive": "2025-04-11T00:01:48+08:00", "expected_hours": 4392}
    with pytest.raises(ValueError, match="integral number"):
        prepare_heating_season_timeseries(config(), frame(), definition)


def test_service_period_preserves_explicit_behavior_scenario_mapping():
    c = config()
    c["nodes"][0]["user_behavior"] = {"scenario_multipliers": {"stress": 1.5}}
    with pytest.raises(ValueError, match="operating_scenario"):
        prepare_heating_season_timeseries(c, frame(), scope())
    selected, audit = prepare_heating_season_timeseries(c, frame(), {**scope(), "operating_scenario": "stress"})
    assert selected.scenario.eq("stress").all()
    assert audit["operating_scenario"] == "stress"
    source = frame()
    source["scenario"] = "stress"
    selected, _ = prepare_heating_season_timeseries(c, source, scope())
    assert selected.scenario.eq("stress").all()


def test_leap_day_period_has_exact_hours():
    data = frame().iloc[[0]].copy()
    data = pd.concat([data] * 24, ignore_index=True)
    data["timestamp"] = pd.date_range("2024-02-29T00:00:00+08:00", periods=24, freq="h")
    definition = {"start_inclusive": "2024-02-29T00:00:00+08:00", "end_exclusive": "2024-03-01T00:00:00+08:00", "expected_hours": 24}
    selected, audit = prepare_heating_season_timeseries(config(), data, definition)
    assert len(selected) == 24 and audit["horizon_hours"] == 24


def test_small_season_replay_is_certified_only_as_a_period():
    result = run_fixed_investment_heating_season_replay(config(), frame().iloc[:2], frame(), scope(), planning_time_limit=5, replay_time_limit=5)
    assert result.certificate["certification_pass"]
    assert not result.certificate["annual_replay_certified"]
    assert result.period_summary["demand_kwh_over_period"] == pytest.approx(6.)
    assert result.period_summary["heating_cash_operating_cost_yuan_over_period"] == pytest.approx(6.)
    assert result.result.kpi_comparison.empty
    assert result.result.investment_freeze_audit.freeze_ok.all()
    assert result.assumptions["annual_supplement_required"]


def test_season_requires_explicit_positive_storage_initial_state_and_handles_zero_capacity():
    c = config()
    c["storages"] = [{"name": "tank", "node": "n", "grade": "low", "existing_capacity_kwh": 1., "max_new_capacity_kwh": 0.}]
    with pytest.raises(ValueError, match="initial_soc_fraction"):
        run_fixed_investment_heating_season_replay(c, frame(), frame(), scope(), planning_time_limit=5, replay_time_limit=5)
    c["storages"][0].update(initial_soc_fraction=0., final_soc_min_fraction=0., charge_efficiency=1., discharge_efficiency=1.)
    result = run_fixed_investment_heating_season_replay(c, frame(), frame(), scope(), planning_time_limit=5, replay_time_limit=5)
    assert result.certificate["certification_pass"]
    assert result.storage_audit.state_trajectory_pass.all()


def test_season_does_not_silently_assume_cyclic_uc_state():
    c = config()
    c["sources"][0]["unit_commitment"] = True
    with pytest.raises(ValueError, match="cyclic-state assumptions"):
        run_fixed_investment_heating_season_replay(c, frame(), frame(), scope(), planning_time_limit=5, replay_time_limit=5)
    result = run_fixed_investment_heating_season_replay(c, frame(), frame(), scope(), planning_time_limit=5, replay_time_limit=5, allow_cyclic_state_assumptions=True)
    assert result.certificate["certification_pass"]
    assert result.assumptions["cyclic_state_assumptions"] == ["unit_commitment"]


def test_season_defers_annual_cost_constraint_without_modifying_original_config():
    c = config()
    c["policy"] = {"maximum_levelized_heat_cost_yuan_per_kwh": 2., "legacy_annualized_capex_yuan": 1.}
    original = deepcopy(c)
    result = run_fixed_investment_heating_season_replay(c, frame(), frame(), scope(), planning_time_limit=5, replay_time_limit=5)
    assert c == original
    assert "policy.maximum_levelized_heat_cost_yuan_per_kwh" in result.assumptions["deferred_annual_constraints"]
    assert "maximum_levelized_heat_cost_yuan_per_kwh" not in result.result.fixed_config["policy"]


def test_season_cli_exports_scope_specific_artifacts(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config()), encoding="utf-8")
    (tmp_path / "scope.json").write_text(json.dumps(scope()), encoding="utf-8")
    frame().to_csv(tmp_path / "input.csv", index=False)
    output = tmp_path / "output"
    command = [sys.executable, "-m", "heatplan.heating_season_cli", "--config", str(tmp_path / "config.yaml"),
               "--scope-file", str(tmp_path / "scope.json"), "--representative-timeseries", str(tmp_path / "input.csv"),
               "--chronological-timeseries", str(tmp_path / "input.csv"), "--output", str(output),
               "--planning-time-limit", "5", "--replay-time-limit", "5"]
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    run = subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
    assert run.returncode == 0, run.stderr
    certificate = json.loads((output / "heating_season_acceptance_certificate.json").read_text(encoding="utf-8"))
    assert certificate["certification_pass"] and not certificate["annual_replay_certified"]
    assert "period_dispatch.csv" in certificate["artifact_sha256"]
    assert certificate["run_identity_sha256"]
    # A later failed invocation cannot leave the previous passing certificate.
    frame().iloc[:-1].to_csv(tmp_path / "input.csv", index=False)
    run = subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
    assert run.returncode != 0
    assert not json.loads((output / "heating_season_acceptance_certificate.json").read_text(encoding="utf-8"))["certification_pass"]
    # Even a missing input file must invalidate an old successful result first.
    (output / "heating_season_acceptance_certificate.json").write_text('{"certification_pass":true}', encoding="utf-8")
    (tmp_path / "input.csv").unlink()
    run = subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
    assert run.returncode != 0
    assert not json.loads((output / "heating_season_acceptance_certificate.json").read_text(encoding="utf-8"))["certification_pass"]
