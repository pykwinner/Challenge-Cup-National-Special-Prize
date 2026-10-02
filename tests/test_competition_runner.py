from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from heatplan.competition_runner import (
    corrected_replay_cost, file_hash, fingerprint, independent_rc_audit, load_case,
    main, normalize_clock, representative_days, solver_lock, technology_config,
    verify_manifest, write_json,
    scenario_evidence_flags, cost_reporting_fields, compare_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def case():
    return load_case(ROOT)


def test_true_c1_spatial_and_annual_inputs(case):
    assert len(case["annual"]) == 8784
    assert case["annual_scope_audit"]["is_explicit_service_year_aligned"]
    assert case["config"] == case["spatial"].config
    assert case["config"]["pipes"][0]["diameter_options"][0]["capex_total_yuan"] != case["raw_config"]["pipes"][0]["diameter_options"][0]["capex_total_yuan"]
    assert case["config"]["policy"] == case["raw_config"]["policy"]


def test_representatives_are_real_contiguous_days_with_complete_weight(case):
    representative, assignment = representative_days(case["annual"], case["config"])
    assert representative.equals(case["representative"])
    assert representative.period_weight.sum() == 8784
    assert len(assignment) == 366
    assert isinstance(representative.time.dtype, pd.DatetimeTZDtype)
    for group, rows in representative.groupby("boundary_group"):
        assert len(rows) == 24
        assert (rows.time.diff().iloc[1:] == pd.Timedelta(hours=1)).all()
        assert rows.period_weight.nunique() == 1
    assert representative.outdoor_temp.min() == case["annual"].outdoor_temp.min()
    assert representative.outdoor_temp.max() == case["annual"].outdoor_temp.max()
    for column in case["annual"]:
        if column.startswith(("internal_gain_", "solar_gain_", "min_temperature_", "max_temperature_")):
            assert representative[column].min() == case["annual"][column].min()
            assert representative[column].max() == case["annual"][column].max()


def test_reject_incomplete_days_and_naive_clock(case):
    with pytest.raises(ValueError, match="incomplete"):
        representative_days(case["annual"].iloc[1:])
    frame = case["annual"].iloc[:24].copy()
    frame["time"] = frame.time.dt.tz_localize(None)
    with pytest.raises(ValueError, match="aware"):
        normalize_clock(frame)


def test_baseline_changes_technology_only(case):
    config, changes = technology_config(case["config"], "ashp_backup")
    assert changes
    for key in ("building_thermal", "policy", "planning", "pipes", "grid_connection"):
        assert config[key] == case["config"][key]
    allowed = {"district_a_air_hp", "distributed_air_hp_b", "district_a_emergency_eb"}
    assert all(source["max_new_capacity_kw"] == 0 for source in config["sources"] if source["name"] not in allowed)
    assert all(storage["max_new_capacity_kwh"] == 0 for storage in config["storages"])
    baseline = load_case(ROOT, "ashp_backup")
    assert baseline["service_contract_sha256"] == case["service_contract_sha256"]
    assert baseline["spatial_sha256"] == case["spatial_sha256"]


@pytest.fixture
def analytic_rc():
    config = {"planning": {"time_step_hours": 1}, "building_thermal": {"enabled": True, "buildings": [
        {"name": "room", "heat_loss_kw_per_c": 1., "thermal_capacity_kwh_per_c": 10.,
         "initial_temperature_c": 20., "final_temperature_c": 20.,
         "min_temperature_c": 19., "max_temperature_c": 21., "internal_gain_kw": 0.}]}}
    frame = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC"),
                          "boundary_group": "window", "outdoor_temp": 10.})
    trajectory = pd.DataFrame({"building": "room", "time_index": [0, 1], "time": frame.time,
                              "boundary_group": "window", "temperature_end_c": 20.,
                              "heat_kw": 10., "cooling_kw": 0., "rc_residual_c": 0.})
    return config, frame, trajectory


def test_rc_analytic_solution(analytic_rc):
    assert independent_rc_audit(*analytic_rc)["passed"]


@pytest.mark.parametrize("field,value", [("heat_kw", 0.), ("temperature_end_c", 22.), ("cooling_kw", 10.)])
def test_rc_independent_audit_rejects_corrupt_physics(analytic_rc, field, value):
    config, frame, trajectory = analytic_rc
    trajectory.loc[0, field] = value
    # Precomputed/exported zero residual must not hide changed physical data.
    assert not independent_rc_audit(config, frame, trajectory)["passed"]


def test_rc_audit_rejects_missing_or_duplicate_identity(analytic_rc):
    config, frame, trajectory = analytic_rc
    with pytest.raises(ValueError, match="exactly cover"):
        independent_rc_audit(config, frame, trajectory.iloc[:1])
    trajectory.loc[1, "time_index"] = 0
    with pytest.raises(ValueError, match="duplicate/missing"):
        independent_rc_audit(config, frame, trajectory)


def test_rc_initial_and_terminal_are_from_raw_config(analytic_rc):
    config, frame, trajectory = analytic_rc
    config["building_thermal"]["buildings"][0]["initial_temperature_c"] = 19.
    trajectory["temperature_start_c"] = 20.
    assert not independent_rc_audit(config, frame, trajectory)["passed"]


def test_capital_included_once_and_period_not_annual():
    planning = {"components_yuan_per_year": {"annualized_capex": 100.}}
    replay = {"objective_yuan_per_year": 160., "components_yuan_per_year": {
        "annualized_capex": 100., "operating_cost": 40., "cooling_operating_cost": 10., "exergy_cost": 10.}}
    cost = corrected_replay_cost(planning, replay, annual=True)
    assert cost["full_service_year_total_objective_yuan"] == 160.
    assert cost["full_service_year_total_cash_cost_yuan"] == 150.
    replay["components_yuan_per_year"]["annualized_capex"] = 0.
    with pytest.raises(ValueError, match="capital differs"):
        corrected_replay_cost(planning, replay, annual=True)
    period = corrected_replay_cost(planning, replay, annual=False)
    assert period["full_service_year_total_objective_yuan"] is None


def test_manifest_rejects_incomplete_and_modified_files(tmp_path):
    write_json(tmp_path / "input.json", {"x": 1})
    write_json(tmp_path / "manifest.json", {"complete": False, "files": {}})
    with pytest.raises(ValueError, match="incomplete"):
        verify_manifest(tmp_path)
    write_json(tmp_path / "manifest.json", {"complete": True, "files": {"input.json": file_hash(tmp_path / "input.json")}})
    assert verify_manifest(tmp_path)["complete"]
    write_json(tmp_path / "input.json", {"x": 2})
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_manifest(tmp_path)


def test_solver_queue_blocks_concurrent_runner(tmp_path):
    with solver_lock(tmp_path):
        with pytest.raises(FileExistsError):
            with solver_lock(tmp_path):
                pass
    with solver_lock(tmp_path):
        pass


def test_no_heavy_run_without_explicit_release():
    with pytest.raises(SystemExit) as error:
        main(["annual", "--run-id", "must_not_run"])
    assert error.value.code == 2


def test_no_short_solve_over_60_seconds():
    with pytest.raises(SystemExit) as error:
        main(["smoke", "--run-id", "must_not_run", "--time-limit", "61"])
    assert error.value.code == 2


def test_fingerprint_canonical_order_and_json_nonfinite(tmp_path):
    assert fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})
    write_json(tmp_path / "values.json", {"gap": np.nan, "count": np.int64(2)})
    assert json.loads((tmp_path / "values.json").read_text()) == {"gap": None, "count": 2}


def _script(path, name):
    import importlib.util
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_prototype_evidence_flags_are_explicit_and_cannot_mutate_default(case):
    assert case["evidence_purpose"] == "mathematical_scenario_validation"
    assert case["parameter_provenance_approved"] is False
    assert case["empirical_validation"] is False
    flags = scenario_evidence_flags()
    flags["empirical_validation"] = True
    assert scenario_evidence_flags()["empirical_validation"] is False


def test_carbon_fee_display_does_not_change_objective():
    summary = {"objective_yuan_per_year": 160., "components_yuan_per_year": {
        "annualized_capex": 100., "operating_cost": 40., "carbon_cost": 10.,
        "cooling_carbon_cost": 5., "cooling_operating_cost": 5.}}
    before = deepcopy(summary)
    fields = cost_reporting_fields(summary)
    assert fields["assumed_carbon_price_cost_yuan"] == 15.
    assert fields["objective_excluding_carbon_price_yuan"] == 145.
    assert summary == before


def test_comparison_cannot_promote_labels_to_empirical_evidence(tmp_path):
    # Output-metadata fixture only; no engineering solve or certificate.
    paths = []
    for name in ("optimized_annual", "baseline_annual", "optimized_season", "baseline_season"):
        directory = tmp_path / name
        directory.mkdir()
        annual = name.endswith("annual")
        optimized = name.startswith("optimized")
        summary = {"runner_audit": {"fixture_integrity": True},
            "corrected_cost": {"full_service_year_total_cash_cost_yuan": 100. if annual else None,
                "full_service_year_total_objective_yuan": 100. if annual else None,
                "replay_period_operating_objective_yuan": 70., "planned_annualized_capex_yuan": 30.},
            "total_service_emissions_kg_per_year": 20., "building_heat_delivered_kwh_per_year": 10.,
            "building_cooling_delivered_kwh_per_year": 5., "solver_status": 0, "mip_gap": 0.,
            "building_mode_formulation": "exclusive"}
        write_json(directory / "summary.json", summary)
        write_json(directory / "manifest.json", {"complete": True,
            "files": {"summary.json": file_hash(directory / "summary.json")},
            "formal_scope": "annual" if annual else "season", "hours": 8784 if annual else 2928,
            "technology": "optimized" if optimized else "ashp_backup",
            "parent_investment_sha256": "opt_fixture" if optimized else "base_fixture",
            "service_contract_sha256": "same_fixture", "spatial_sha256": "same_fixture",
            "source_sha256": {}, "code_sha256": {},
            "empirical_validation": True, "parameter_provenance_approved": True})
        paths.append(directory)
    report = compare_evidence(tmp_path / "comparison", *paths)
    assert report["evidence_purpose"] == "mathematical_scenario_validation"
    assert report["empirical_validation"] is False
    assert report["parameter_provenance_approved"] is False
    assert verify_manifest(tmp_path / "comparison")["empirical_validation"] is False


def test_cyclic_ablation_changes_boundary_only(case):
    module = _script(ROOT / "outputs/competition_20261001/C4/run_cyclic_boundary_ablation.py", "cyclic_unit")
    before = deepcopy(case["config"])
    cyclic = module.make_cyclic_planning_config(case["config"])
    assert case["config"] == before
    assert "annual_scope" not in cyclic["planning"]
    for building in cyclic["building_thermal"]["buildings"]:
        assert building["boundary_mode"] == "cyclic"
        assert "final_temperature_c" not in building
        assert building["initial_temperature_c"] == 20.
    assert module.nonboundary_building_contract(before["building_thermal"]) == module.nonboundary_building_contract(cyclic["building_thermal"])


def test_frontier_calls_true_lexicographic_api_and_records_failure(tmp_path, monkeypatch):
    module = _script(ROOT / "scripts/run_competition_frontier.py", "frontier_unit")
    calls = []
    class FailBeforeSolve:
        def __init__(self, config, frame):
            self.ts = frame
        def build(self):
            pass
        def solve_lexicographic(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("unit fixture: no engineering solve")
    monkeypatch.setattr(module, "HeatPlanningModel", FailBeforeSolve)
    monkeypatch.setattr(module, "code_identity", lambda root: {"test_epoch": "same"})
    # Keep the nested unit-fixture anchor below Windows MAX_PATH. Production
    # uses the actual project root, not this already nested pytest root.
    monkeypatch.setattr(module, "OUTPUT", Path("o"))
    case = {"source_identities": {}, "service_contract_sha256": "fixture", "spatial_sha256": "fixture"}
    frame = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")})
    with pytest.raises(RuntimeError, match="no engineering solve"):
        module.endpoint(tmp_path, tmp_path / "endpoint", case, {}, frame, "emissions", 1., .005)
    assert calls == [{"primary": "emissions", "time_limit": 1., "mip_gap": .005}]
    failure = json.loads((tmp_path / "endpoint/failure.json").read_text(encoding="utf-8"))
    assert failure["empirical_validation"] is False
    assert failure["parameter_provenance_approved"] is False
    assert not (tmp_path / "endpoint/manifest.json").exists()


def test_frozen_plan_cannot_cross_code_epoch_by_label(tmp_path, monkeypatch):
    import heatplan.competition_runner as runner
    case = {"service_contract_sha256": "service", "spatial_sha256": "spatial",
            "spatial_config_sha256": "config", "technology": "optimized", "source_identities": {}}
    plan = {key: case[key] for key in ("service_contract_sha256", "spatial_sha256",
                                     "spatial_config_sha256", "technology")}
    plan.update({"source_sha256": {}, "code_sha256": {"epoch": "old"},
                 "fixed_config": {}, "fixed_config_sha256": fingerprint({})})
    write_json(tmp_path / "fixed_plan.json", plan)
    monkeypatch.setattr(runner, "verify_manifest", lambda directory: {"complete": True})
    monkeypatch.setattr(runner, "code_identity", lambda root: {"epoch": "new"})
    with pytest.raises(ValueError, match="source/code changed"):
        runner.load_plan(tmp_path, tmp_path, case)
