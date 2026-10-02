"""Small deterministic regressions; no annual optimization is required."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from heatplan import annual_replay_batch_cli as batch
from heatplan import provenance
from heatplan import risk_scan_cli as risk
from heatplan.io import load_config


def _inputs(root: Path) -> tuple[Path, dict]:
    (root / "parent.yaml").write_text("planning:\n  carbon_price_yuan_per_kg: 0.1\n", encoding="utf-8")
    (root / "config.yaml").write_text("extends: parent.yaml\n", encoding="utf-8")
    for name in ("representative.csv", "annual.csv"):
        (root / name).write_text("time,demand\n0,1\n", encoding="utf-8")
    manifest = root / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"profiles": [{
        "name": "case", "config": "config.yaml",
        "representative_timeseries": "representative.csv",
        "annual_timeseries": "annual.csv", "timestamp_column": "time",
    }]}), encoding="utf-8")
    return manifest, dict(
        config_path=root / "config.yaml", representative_path=root / "representative.csv",
        annual_path=root / "annual.csv", timestamp_column="time",
        planning_time_limit=180.0, replay_time_limit=1800.0,
        planning_mip_gap=None, replay_mip_gap=None, preflight_only=False,
    )


def _write_outputs(output: Path, *, preflight_only=False, estimate_model_size=False) -> None:
    for name in batch._required_outputs(preflight_only, estimate_model_size):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if path.suffix == ".json" else "value\n1\n", encoding="utf-8")
    (output / "annual_replay_preflight_summary.json").write_text(json.dumps({
        "chronology_valid": True, "is_full_year_horizon": True,
        "chronology_ready_for_formal_replay": True,
    }), encoding="utf-8")
    if not preflight_only:
        (output / "annual_replay_summary.json").write_text(json.dumps({
            "annual_replay_certified": True, "planning_solver_status": 0,
            "replay_solver_status": 0,
        }), encoding="utf-8")
        (output / "annual_replay_acceptance_certificate.json").write_text(
            json.dumps({"certification_pass": True}), encoding="utf-8",
        )


def _successful_process(command, **kwargs):
    output = Path(command[command.index("--output") + 1])
    _write_outputs(output, preflight_only="--preflight-only" in command,
                   estimate_model_size="--estimate-model-size" in command)
    return subprocess.CompletedProcess(command, 0, "ok", "")


def test_parent_and_grandparent_changes_invalidate_fingerprint(tmp_path):
    _, arguments = _inputs(tmp_path)
    initial = batch._run_fingerprint(**arguments)
    before = load_config(arguments["config_path"])
    (tmp_path / "parent.yaml").write_text("planning:\n  carbon_price_yuan_per_kg: 0.9\n", encoding="utf-8")
    assert load_config(arguments["config_path"]) != before
    assert batch._run_fingerprint(**arguments) != initial
    (tmp_path / "parent.yaml").write_text("extends: grandparent.yaml\n", encoding="utf-8")
    (tmp_path / "grandparent.yaml").write_text("planning: {carbon_price_yuan_per_kg: 0.9}\n", encoding="utf-8")
    inherited = batch._run_fingerprint(**arguments)
    (tmp_path / "grandparent.yaml").write_text("planning: {carbon_price_yuan_per_kg: 1.9}\n", encoding="utf-8")
    assert batch._run_fingerprint(**arguments) != inherited


@pytest.mark.parametrize("changed", [
    {"estimate_model_size": True}, {"wall_time_limit": 60},
    {"timestamp_column": "other"}, {"planning_mip_gap": 0.01},
])
def test_artifact_and_solver_settings_invalidate_fingerprint(tmp_path, changed):
    _, arguments = _inputs(tmp_path)
    assert batch._run_fingerprint(**arguments) != batch._run_fingerprint(**{**arguments, **changed})


def test_source_and_software_versions_are_part_of_identity(tmp_path):
    _, arguments = _inputs(tmp_path)
    with patch.object(batch, "algorithm_provenance", return_value={"source": "a", "software": "1"}):
        initial = batch._run_fingerprint(**arguments)
    for changed in ({"source": "b", "software": "1"}, {"source": "a", "software": "2"}):
        with patch.object(batch, "algorithm_provenance", return_value=changed):
            assert batch._run_fingerprint(**arguments) != initial
    actual = provenance.algorithm_provenance()
    assert {"model.py", "chronology.py", "risk_scan_cli.py", "provenance.py"}.issubset(actual["source_sha256"])
    assert {"python", "scipy", "numpy", "pandas", "PyYAML"}.issubset(actual["software_versions"])


def test_source_provenance_is_not_stale_in_same_process(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "__file__", str(tmp_path / "provenance.py"))
    source = tmp_path / "model.py"
    source.write_text("algorithm = 1", encoding="utf-8")
    initial = provenance.algorithm_provenance()
    source.write_text("algorithm = 2", encoding="utf-8")
    assert provenance.algorithm_provenance() != initial


@pytest.mark.parametrize("preflight_only", [False, True])
def test_successful_run_publishes_bound_manifest_and_can_resume(tmp_path, preflight_only):
    manifest, _ = _inputs(tmp_path)
    with patch.object(batch.subprocess, "run", side_effect=_successful_process) as run:
        first = batch.run_batch_replay(manifest, tmp_path / "outputs", preflight_only=preflight_only)
        second = batch.run_batch_replay(manifest, tmp_path / "outputs", preflight_only=preflight_only)
    assert run.call_count == 1
    assert not bool(first.loc[0, "resumed"])
    assert bool(second.loc[0, "resumed"])
    output = tmp_path / "outputs" / "case"
    marker = json.loads((output / batch._COMPLETION_FILE).read_text(encoding="utf-8"))
    configuration = json.loads((output / "batch_run_configuration.json").read_text(encoding="utf-8"))
    assert marker["fingerprint"] == configuration["fingerprint"]
    assert marker["outputs_sha256"] == batch._output_hashes(output)
    if not preflight_only:
        assert marker["certificate_sha256"] == provenance.file_sha256(output / marker["certificate_path"])


@pytest.mark.parametrize("tampered", [
    "annual_replay_acceptance_certificate.json", "fixed_investment_annual_replay/dispatch.csv",
])
def test_tampered_certificate_or_dispatch_cannot_resume(tmp_path, tampered):
    manifest, _ = _inputs(tmp_path)
    with patch.object(batch.subprocess, "run", side_effect=_successful_process) as run:
        batch.run_batch_replay(manifest, tmp_path / "outputs")
        path = tmp_path / "outputs" / "case" / tampered
        path.write_text(path.read_text(encoding="utf-8") + "\n ", encoding="utf-8")
        result = batch.run_batch_replay(manifest, tmp_path / "outputs")
    assert run.call_count == 2
    assert not bool(result.loc[0, "resumed"])


def test_legacy_success_without_bound_completion_marker_is_recomputed(tmp_path):
    manifest, _ = _inputs(tmp_path)
    with patch.object(batch.subprocess, "run", side_effect=_successful_process) as run:
        batch.run_batch_replay(manifest, tmp_path / "outputs")
        (tmp_path / "outputs" / "case" / batch._COMPLETION_FILE).unlink()
        result = batch.run_batch_replay(manifest, tmp_path / "outputs")
    assert run.call_count == 2
    assert not bool(result.loc[0, "resumed"])


def test_nonzero_process_cannot_publish_even_if_it_wrote_a_passing_certificate(tmp_path):
    manifest, _ = _inputs(tmp_path)

    def unsuccessful(command, **kwargs):
        result = _successful_process(command, **kwargs)
        result.returncode = 2
        return result

    with patch.object(batch.subprocess, "run", side_effect=unsuccessful):
        result = batch.run_batch_replay(manifest, tmp_path / "outputs")
    assert result.loc[0, "status"] == "error"
    assert not (tmp_path / "outputs" / "case" / batch._COMPLETION_FILE).exists()


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_old_success_new_failure_next_attempt_never_reuses_old_success(tmp_path, failure):
    manifest, _ = _inputs(tmp_path)
    with patch.object(batch.subprocess, "run", side_effect=_successful_process):
        assert batch.run_batch_replay(manifest, tmp_path / "outputs").loc[0, "status"] == "success"
    (tmp_path / "parent.yaml").write_text("planning: {carbon_price_yuan_per_kg: 0.9}\n", encoding="utf-8")

    def failed(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 2, "", "failed early")

    with patch.object(batch.subprocess, "run", side_effect=failed) as run:
        first = batch.run_batch_replay(manifest, tmp_path / "outputs")
        second = batch.run_batch_replay(manifest, tmp_path / "outputs")
    assert run.call_count == 2
    assert first.loc[0, "status"] != "success"
    assert second.loc[0, "status"] != "success"
    assert not bool(second.loc[0, "resumed"])
    output = tmp_path / "outputs" / "case"
    assert not (output / batch._COMPLETION_FILE).exists()
    assert not (output / "annual_replay_summary.json").exists()
    assert list((tmp_path / "outputs" / ".batch_history" / "case").rglob("annual_replay_summary.json"))


@pytest.mark.parametrize("defect", ["missing_flag", "false_flag", "missing_certificate", "false_certificate", "missing_dispatch"])
def test_uncertified_or_incomplete_results_are_never_published(tmp_path, defect):
    manifest, _ = _inputs(tmp_path)

    def incomplete(command, **kwargs):
        result = _successful_process(command, **kwargs)
        output = Path(command[command.index("--output") + 1])
        if defect in {"missing_flag", "false_flag"}:
            (output / "annual_replay_summary.json").write_text(
                json.dumps({} if defect == "missing_flag" else {"annual_replay_certified": False}), encoding="utf-8",
            )
        elif defect == "missing_certificate":
            (output / "annual_replay_acceptance_certificate.json").unlink()
        elif defect == "false_certificate":
            (output / "annual_replay_acceptance_certificate.json").write_text(
                json.dumps({"certification_pass": False}), encoding="utf-8",
            )
        else:
            (output / "fixed_investment_annual_replay" / "dispatch.csv").unlink()
        return result

    with patch.object(batch.subprocess, "run", side_effect=incomplete):
        result = batch.run_batch_replay(manifest, tmp_path / "outputs")
    assert result.loc[0, "status"] != "success"
    assert not (tmp_path / "outputs" / "case" / batch._COMPLETION_FILE).exists()


@pytest.mark.parametrize("mutation", ["changed", "deleted"])
def test_inputs_changed_during_successful_run_are_not_published(tmp_path, mutation):
    manifest, _ = _inputs(tmp_path)

    def changing(command, **kwargs):
        result = _successful_process(command, **kwargs)
        if mutation == "deleted":
            (tmp_path / "parent.yaml").unlink()
        else:
            (tmp_path / "parent.yaml").write_text("planning: {carbon_price_yuan_per_kg: 4}\n", encoding="utf-8")
        return result

    with patch.object(batch.subprocess, "run", side_effect=changing):
        result = batch.run_batch_replay(manifest, tmp_path / "outputs")
    assert result.loc[0, "status"] == "inputs_changed_during_run"
    assert not (tmp_path / "outputs" / "case" / batch._COMPLETION_FILE).exists()


def test_atomic_json_failure_preserves_previous_complete_document(tmp_path):
    target = tmp_path / "completion.json"
    provenance.atomic_write_json(target, {"complete": "old"})
    with patch.object(provenance.os, "replace", side_effect=OSError("interrupted publication")):
        with pytest.raises(OSError):
            provenance.atomic_write_json(target, {"complete": "new"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"complete": "old"}
    assert list(tmp_path.iterdir()) == [target]


def _investments(rows):
    return pd.DataFrame(rows, columns=["category", "asset", "option", "unit", "value"])


def test_binary_and_fraction_probe_is_fixed_in_actual_aggregate(tmp_path):
    rows = []
    for weight, binary, fraction in [(0.0, 0, 0), (0.1, 1, 0.25)]:
        case = tmp_path / str(weight)
        planning = case / "representative_planning"
        planning.mkdir(parents=True)
        _investments([
            ["terminal_temperature_retrofit", "node", "high_to_low", "binary", binary],
            ["heat_metering_coverage", "node", "new", "fraction", fraction],
        ]).to_csv(planning / "investments.csv", index=False)
        rows.append({
            "profile": "probe", "cvar_weight": weight, "case_output": str(case),
            "expected_cost_before_cvar_yuan_per_year": 100,
            "cvar_operating_risk_cost_yuan": 120, "worst_operating_risk_cost_yuan": 130,
        })
    with patch.object(risk, "write_risk_scan_acceptance_rollup"):
        risk._write_aggregate_outputs(tmp_path, rows, absolute_tolerance=1, relative_tolerance=.001)
    comparison = pd.read_csv(tmp_path / "investment_delta_vs_neutral.csv")
    assert comparison.loc[comparison.cvar_weight == .1, "material_change"].all()
    summary = pd.read_csv(tmp_path / "investment_stability_summary.csv").set_index("cvar_weight")
    assert not bool(summary.loc[.1, "same_investment_as_neutral"])
    assert not bool(summary.loc[.1, "same_structure_as_neutral"])
    assert summary.loc[.1, "changed_binary_count"] == 1
    assert summary.loc[.1, "changed_fraction_count"] == 1
    assert summary.loc[.1, "first_material_change_weight"] == .1


def test_option_switch_and_binary_switch_off_are_structural():
    before = _investments([
        ["pipe_choice", "pipe", "small", "binary", 1],
        ["pipe_choice", "pipe", "large", "binary", 0],
        ["retrofit", "node", "choice", "binary", 1],
    ])
    after = before.copy()
    after["value"] = [0, 1, 0]
    comparison = risk._compare_investments(before, after, absolute_tolerance=100, relative_tolerance=1)
    assert comparison["structural_change"].all()
    assert comparison["material_change"].all()


def test_fraction_noise_and_capacity_unit_tolerances():
    before = _investments([
        ["metering", "node", "new", "fraction", 0.25],
        ["source_capacity", "source", "new", "kW", 100],
        ["storage_capacity", "storage", "new", "kWh", 100],
        ["inventory", "fuel", "new", "kg", 100],
        ["storage_firm_capacity", "derived", "derived", "kW", 0],
    ])
    after = before.copy()
    after["value"] = [.25001, 105, 105, 105, 1000]
    result = risk._compare_investments(
        before, after, absolute_tolerance=1, relative_tolerance=0,
        capacity_tolerances={"kW": .1, "kWh": 10, "kg": 100},
    ).set_index("category")
    assert bool(result.loc["source_capacity", "material_change"])
    assert not result.loc[["metering", "storage_capacity", "inventory", "storage_firm_capacity"], "material_change"].any()


def test_roundoff_in_valid_binary_selection_is_not_structural_change():
    before = _investments([["retrofit", "node", "choice", "binary", 1]])
    after = before.copy()
    after["value"] = 1 - 5e-7
    result = risk._compare_investments(before, after, absolute_tolerance=1, relative_tolerance=.001)
    assert not result["material_change"].any()
    assert not result["structural_change"].any()


@pytest.mark.parametrize("value", [.2, float("nan"), float("inf")])
def test_invalid_discrete_or_nonfinite_values_fail_explicitly(value):
    before = _investments([["retrofit", "node", "choice", "binary", 0]])
    after = before.copy()
    after["value"] = value
    with pytest.raises(ValueError):
        risk._compare_investments(before, after, absolute_tolerance=1, relative_tolerance=.001)


@pytest.fixture
def risk_scan_fixture(tmp_path, monkeypatch):
    """Real four-row planning/checkpoints, with only the annual audit stubbed."""
    config = {
        "planning": {"time_step_hours": 1, "discount_rate": .05, "asset_lifetime_years": 20,
                     "max_unserved_rate": 0, "max_scenario_unserved_rate": 0},
        "grades": [{"name": "low", "temperature_c": 45}],
        "nodes": [{"name": "load", "demand_columns": {"low": "heat_demand"}}],
        "sources": [{"name": "boiler", "node": "load", "type": "boiler",
                     "allowed_grades": ["low"], "supply_temp_c": 80,
                     "existing_capacity_kw": 10, "max_new_capacity_kw": 1,
                     "capex_yuan_per_kw": 100, "variable_cost_yuan_per_kwh_heat": .1,
                     "emission_kg_per_kwh_heat": .2}],
        "pipes": [], "storages": [],
    }
    (tmp_path / "parent.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "risk_config.yaml").write_text("extends: parent.yaml\n", encoding="utf-8")
    representative = pd.DataFrame({
        "time": [0, 1, 2, 3], "heat_demand": [5, 6, 7, 8],
        "electricity_price": [.6] * 4, "grid_emission_factor": [.5] * 4,
        "scenario": ["mild", "mild", "cold", "cold"], "scenario_probability": [.5] * 4,
        "boundary_group": ["mild", "mild", "cold", "cold"], "period_weight": [1] * 4,
    })
    representative.to_csv(tmp_path / "risk_representative.csv", index=False)
    representative.iloc[:2].to_csv(tmp_path / "risk_annual.csv", index=False)
    manifest = tmp_path / "risk_manifest.yaml"
    manifest.write_text(yaml.safe_dump({"profiles": [{
        "name": "case", "config": "risk_config.yaml", "representative_timeseries": "risk_representative.csv",
        "annual_timeseries": [{"year": 2024, "path": "risk_annual.csv", "timestamp_column": "time"}],
    }]}), encoding="utf-8")
    revision = ["current"]
    monkeypatch.setattr(risk, "algorithm_provenance", lambda: {"test_revision": revision[0]})
    monkeypatch.setattr(risk, "audit_prepared_fixed_plan_across_years", lambda *a, **k: SimpleNamespace(
        year_audit=pd.DataFrame([{"readiness_pass": True, "firm_heat_capacity_gap_kw": 2,
                                 "firm_electric_capacity_gap_kw": 1}]),
    ))
    return manifest, config, revision


@pytest.mark.parametrize("mutation", ["config", "data", "alpha", "algorithm", "time_limit", "comparison"])
def test_partial_risk_scan_never_mixes_experiment_identities(tmp_path, risk_scan_fixture, mutation):
    manifest, config, revision = risk_scan_fixture
    output = tmp_path / "risk_outputs"
    with patch.object(risk, "prepare_fixed_investment_plan", wraps=risk.prepare_fixed_investment_plan) as solve:
        risk.run_risk_weight_scan(manifest, output, weights=[0, .1])
        kwargs = {}
        if mutation == "config":
            config["planning"]["carbon_price_yuan_per_kg"] = .9
            (tmp_path / "parent.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        elif mutation == "data":
            with (tmp_path / "risk_annual.csv").open("a", encoding="utf-8") as stream:
                stream.write("\n")
        elif mutation == "alpha":
            kwargs["cvar_alpha"] = .9
        elif mutation == "algorithm":
            revision[0] = "changed"
        elif mutation == "time_limit":
            kwargs["time_limit"] = 20
        else:
            kwargs["investment_fraction_tolerance"] = .002
        result = risk.run_risk_weight_scan(manifest, output, weights=[.1], **kwargs)
    assert solve.call_count == 3
    assert result["cvar_weight"].tolist() == [.1]
    assert not bool(result.iloc[0]["neutral_baseline_available"])
    saved = json.loads((output / "risk_weight_scan_summary.json").read_text(encoding="utf-8"))
    assert saved[0]["expected_cost_before_cvar_yuan_per_year_delta_vs_neutral"] is None
    assert list((output / ".risk_history" / "experiments").rglob("risk_case_run.json"))


def test_same_risk_experiment_can_extend_and_resume_without_stale_aggregate(tmp_path, risk_scan_fixture):
    manifest, _, _ = risk_scan_fixture
    output = tmp_path / "risk_outputs"
    with patch.object(risk, "prepare_fixed_investment_plan", wraps=risk.prepare_fixed_investment_plan) as solve:
        risk.run_risk_weight_scan(manifest, output, weights=[0])
        (output / "risk_weight_scan_summary.json").write_text('[{"profile":"invented","cvar_weight":999}]', encoding="utf-8")
        extended = risk.run_risk_weight_scan(manifest, output, weights=[.1])
        resumed = risk.run_risk_weight_scan(manifest, output, weights=[.1])
    assert solve.call_count == 2
    assert extended["cvar_weight"].tolist() == [0, .1]
    assert extended["neutral_baseline_available"].all()
    assert resumed.set_index("cvar_weight").loc[.1, "result_source"] == "resumed"
    rollup = json.loads((output / "risk_weight_scan_acceptance_rollup.json").read_text(encoding="utf-8"))
    assert rollup["scan_complete"] is True


@pytest.mark.parametrize("corruption", ["missing_run_marker", "changed_dispatch"])
def test_unbound_or_tampered_risk_checkpoint_is_archived_and_resolved(tmp_path, risk_scan_fixture, corruption):
    manifest, _, _ = risk_scan_fixture
    output = tmp_path / "risk_outputs"
    with patch.object(risk, "prepare_fixed_investment_plan", wraps=risk.prepare_fixed_investment_plan) as solve:
        risk.run_risk_weight_scan(manifest, output, weights=[0])
        case = output / "case" / "weight_0"
        if corruption == "missing_run_marker":
            (case / "risk_case_run.json").unlink()
        else:
            with (case / "representative_planning" / "dispatch.csv").open("a", encoding="utf-8") as stream:
                stream.write("\n")
        result = risk.run_risk_weight_scan(manifest, output, weights=[0])
    assert solve.call_count == 2
    assert result.iloc[0]["result_source"] == "solved"
    assert list((output / ".risk_history" / "cases").rglob("fixed_plan_checkpoint_manifest.json"))


@pytest.mark.parametrize("seed_kind", ["trusted", "legacy", "old_algorithm"])
def test_seed_checkpoint_requires_current_algorithm_and_bound_contents(tmp_path, risk_scan_fixture, seed_kind):
    manifest, _, revision = risk_scan_fixture
    seed = tmp_path / "seed"
    risk.run_risk_weight_scan(manifest, seed, weights=[.05])
    if seed_kind == "legacy":
        (seed / "case" / "weight_0p05" / "risk_case_run.json").unlink()
    elif seed_kind == "old_algorithm":
        revision[0] = "changed"
    with patch.object(risk, "prepare_fixed_investment_plan", wraps=risk.prepare_fixed_investment_plan) as solve:
        result = risk.run_risk_weight_scan(manifest, tmp_path / "new_outputs", weights=[.05], seed_checkpoint_root=seed)
    assert solve.call_count == (0 if seed_kind == "trusted" else 1)
    assert result.iloc[0]["result_source"] == ("seed_checkpoint" if seed_kind == "trusted" else "solved")
    assert (seed / "case" / "weight_0p05" / "fixed_plan_checkpoint_manifest.json").is_file()


def test_changed_experiment_failure_cannot_leave_old_passing_rollup(tmp_path, risk_scan_fixture):
    manifest, config, _ = risk_scan_fixture
    output = tmp_path / "risk_outputs"
    risk.run_risk_weight_scan(manifest, output, weights=[0, .1])
    config["planning"]["carbon_price_yuan_per_kg"] = .9
    (tmp_path / "parent.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    with patch.object(risk, "prepare_fixed_investment_plan", side_effect=RuntimeError("new solve failed")):
        with pytest.raises(RuntimeError):
            risk.run_risk_weight_scan(manifest, output, weights=[.1])
    assert json.loads((output / "risk_weight_scan_summary.json").read_text(encoding="utf-8")) == []
    rollup = json.loads((output / "risk_weight_scan_acceptance_rollup.json").read_text(encoding="utf-8"))
    assert rollup["acceptance_pass"] is False
    assert rollup["scan_complete"] is False


def test_same_experiment_failed_forced_rerun_excludes_old_case(tmp_path, risk_scan_fixture):
    manifest, _, _ = risk_scan_fixture
    output = tmp_path / "risk_outputs"
    risk.run_risk_weight_scan(manifest, output, weights=[0, .1])
    with patch.object(risk, "prepare_fixed_investment_plan", side_effect=RuntimeError("rerun failed")):
        with pytest.raises(RuntimeError):
            risk.run_risk_weight_scan(manifest, output, weights=[.1], resume=False)
    saved = json.loads((output / "risk_weight_scan_summary.json").read_text(encoding="utf-8"))
    assert [row["cvar_weight"] for row in saved] == [0]
    rollup = json.loads((output / "risk_weight_scan_acceptance_rollup.json").read_text(encoding="utf-8"))
    assert rollup["acceptance_pass"] is False
    assert list((output / ".risk_history" / "cases").rglob("risk_case_run.json"))


def test_risk_input_read_failure_invalidates_old_success_before_parsing(tmp_path, risk_scan_fixture):
    manifest, _, _ = risk_scan_fixture
    output = tmp_path / "risk_outputs"
    risk.run_risk_weight_scan(manifest, output, weights=[0])
    (tmp_path / "parent.yaml").unlink()
    with pytest.raises(OSError):
        risk.run_risk_weight_scan(manifest, output, weights=[0])
    rollup = json.loads((output / "risk_weight_scan_acceptance_rollup.json").read_text(encoding="utf-8"))
    assert rollup["acceptance_pass"] is False
    assert rollup["scan_complete"] is False
    assert list((output / ".risk_history" / "attempt_start").rglob("risk_scan_experiment.json"))


def test_corrupt_unrequested_neutral_cannot_be_reused_as_baseline(tmp_path, risk_scan_fixture):
    manifest, _, _ = risk_scan_fixture
    output = tmp_path / "risk_outputs"
    risk.run_risk_weight_scan(manifest, output, weights=[0, .1])
    path = output / "case" / "weight_0" / "representative_planning" / "dispatch.csv"
    path.write_text("corrupted", encoding="utf-8")
    result = risk.run_risk_weight_scan(manifest, output, weights=[.1])
    assert result["cvar_weight"].tolist() == [.1]
    assert not bool(result.iloc[0]["neutral_baseline_available"])
    assert not path.exists()
    assert list((output / ".risk_history" / "cases").rglob("dispatch.csv"))


def test_tiny_positive_weight_has_distinct_directory_and_no_false_neutral(tmp_path, risk_scan_fixture):
    manifest, _, _ = risk_scan_fixture
    assert risk._weight_slug(0) == "weight_0"
    assert risk._weight_slug(.1) == "weight_0p1"
    assert risk._weight_slug(1e-9) != risk._weight_slug(0)
    output = tmp_path / "risk_outputs"
    result = risk.run_risk_weight_scan(manifest, output, weights=[1e-9, .1])
    assert len(set(result["case_output"])) == 2
    assert not result["neutral_baseline_available"].any()
    assert result["expected_cost_before_cvar_yuan_per_year_delta_vs_neutral"].isna().all()
