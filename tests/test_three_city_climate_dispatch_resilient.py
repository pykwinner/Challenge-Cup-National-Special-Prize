"""Functional fault injection of orchestration; doubles never claim new solves.

The fixture uses admitted 2880h real city drivers and actual RC feedback/audits.
Only optimization is replaced by an explicitly labelled feedback test double.
The independent new 2880h solver run is retained under stage_03/reliability.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("resilient_climate", ROOT / "scripts/run_three_city_climate_dispatch_resilient.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class InjectedSolverFailure(RuntimeError):
    def __init__(self):
        super().__init__("injected middle-city second-stage solve failure")
        self.summary = {"primary_solver_status": 0, "solver_status": 1,
                        "solver_stages": [{"stage_id": "primary", "mip_gap": 0.},
                                          {"stage_id": "secondary", "mip_gap": .1}]}


@pytest.fixture
def batch(tmp_path, monkeypatch):
    out = tmp_path / "batch"
    m.freeze(out)
    original_rollout = m.base.rollout
    calls = []

    def feedback_double(name, config, frame, folder, budget, gap, *, primary=None):
        assert budget == 60. and gap == 0.
        calls.append((folder.name, name, budget, gap, primary))
        (folder / name).mkdir()
        trajectory = original_rollout(config, frame, 0)
        metrics = m.base.independent_metrics(config, frame, trajectory)
        key = "bill_yuan" if primary == "total_cost" else "carbon_kg"
        summary = {"objective_yuan_per_year": metrics["bill_yuan"],
                   "total_service_emissions_kg_per_year": metrics["carbon_kg"],
                   "solution_quality": {"scope": "fault_injection_feedback_test_double"},
                   "solver_status": 0, "mip_gap": 0.,
                   "primary_incumbent_value": metrics[key],
                   "primary_objective_allowance": 1e-7 + 1e-8 * abs(metrics[key])}
        m._write(folder / name / "test_double_summary.json", summary)
        return SimpleNamespace(building_trajectory=trajectory, summary=summary), {"wall_seconds": 0.}

    monkeypatch.setattr(m.base, "run_one", feedback_double)
    return out, calls, original_rollout, feedback_double


def _read(out, name):
    return json.loads((out / name).read_text(encoding="utf-8"))


def _assert_final(out, passed, rows):
    manifest = _read(out, "manifest.json")
    checkpoint = _read(out, "checkpoint.json")
    assert manifest["all_requested_cases_passed"] is passed
    assert manifest["accepted_rows"] == rows
    assert manifest["status"] == checkpoint["status"] == "completed"
    assert manifest["identity_verified"] and manifest["comparison_valid"]
    assert len(manifest["tasks"]) == 15
    assert all(t["status"] not in {"pending", "running"} for t in manifest["tasks"])
    assert manifest["failures"] == checkpoint["failures"] == _read(out, "failures.json")
    assert manifest["baseline_rows_reused"] == 0
    for name, digest in manifest["files"].items():
        assert m.base.sha(out / name) == digest
    m.verify(out)
    return manifest


def test_all_city_tasks_complete_and_checkpoint_every_transition(batch):
    out, calls, _, _ = batch
    m.run(out)
    manifest = _assert_final(out, True, 12)
    assert len(calls) == manifest["new_optimization_cases"] == 9
    assert manifest["new_baseline_rows"] == 3
    assert len({(r["city"], r["case"]) for r in _read(out, "comparison.json")}) == 12
    assert _read(out, "checkpoint.json")["revision"] == 32


def test_first_city_prepare_read_failure_does_not_stop_other_cities(batch, monkeypatch):
    out, calls, _, _ = batch
    reader = m.pd.read_csv

    def fail_first(path, *args, **kwargs):
        if str(path).endswith("urumqi_hourly.csv"):
            raise OSError("injected first-city preparation read failure")
        return reader(path, *args, **kwargs)

    monkeypatch.setattr(m.pd, "read_csv", fail_first)
    m.run(out)
    manifest = _assert_final(out, False, 8)
    failures = manifest["failures"]
    assert failures[0]["city"] == "urumqi" and failures[0]["stage"] == "climate_read"
    assert failures[0]["type"] == "OSError"
    assert {r["case"] for r in failures[1:]} == set(m.CASES)
    assert all(r["status"] == "skipped" and r["depends_on"] == ["prepare"] for r in failures[1:])
    assert {c[0] for c in calls} == {"guyuan", "wuhan"}


def test_first_city_baseline_rollout_failure_does_not_stop_other_cities(batch, monkeypatch):
    out, calls, original, _ = batch

    def fail_first(config, frame, delta):
        if frame.scenario.iloc[0].startswith("urumqi"):
            raise RuntimeError("injected first-city baseline rollout failure")
        return original(config, frame, delta)

    monkeypatch.setattr(m.base, "rollout", fail_first)
    m.run(out)
    manifest = _assert_final(out, False, 8)
    assert manifest["failures"][0]["stage"] == "baseline_rollout"
    assert manifest["failures"][0]["type"] == "RuntimeError"
    assert len(manifest["failures"]) == 4
    assert {c[0] for c in calls} == {"guyuan", "wuhan"}


def test_first_city_baseline_audit_failure_is_retained(batch, monkeypatch):
    out, _, original, _ = batch

    def corrupt_first(config, frame, delta):
        trajectory = original(config, frame, delta)
        if frame.scenario.iloc[0].startswith("urumqi"):
            trajectory.loc[1, "heat_kw"] += .1
        return trajectory

    monkeypatch.setattr(m.base, "rollout", corrupt_first)
    m.run(out)
    manifest = _assert_final(out, False, 8)
    assert manifest["failures"][0]["stage"] == "baseline_audit"
    assert not _read(out, "results/urumqi/baseline_audit.json")["passed"]


def test_middle_second_stage_failure_retains_summary_and_attempts_other_endpoint(batch, monkeypatch):
    out, calls, _, double = batch

    def fail_middle(name, config, frame, folder, budget, gap, *, primary=None):
        if folder.name == "guyuan" and name == "cost_endpoint":
            (folder / name).mkdir()
            raise InjectedSolverFailure()
        return double(name, config, frame, folder, budget, gap, primary=primary)

    monkeypatch.setattr(m.base, "run_one", fail_middle)
    m.run(out)
    manifest = _assert_final(out, False, 10)
    failure = manifest["failures"][0]
    assert failure["city"] == "guyuan" and failure["stage"] == "cost_endpoint_solve"
    assert failure["type"] == "InjectedSolverFailure"
    assert len(failure["solver_evidence"]["solver_stages"]) == 2
    assert manifest["failures"][1]["case"] == "midpoint_cap"
    assert ("guyuan", "carbon_endpoint") in {(c[0], c[1]) for c in calls}
    assert all(r["city"] == "guyuan" for r in manifest["failures"])
    assert not (out / "results/guyuan/midpoint_cap").exists()


@pytest.mark.parametrize("gate", ["primary", "returned", "allowance"])
def test_known_feasible_bound_gates_reject_endpoint_and_skip_midpoint(batch, monkeypatch, gate):
    out, _, original, double = batch

    def wrong_bound(name, config, frame, folder, budget, gap, *, primary=None):
        result, audit = double(name, config, frame, folder, budget, gap, primary=primary)
        if folder.name == "guyuan" and name == "cost_endpoint":
            if gate == "primary":
                result.summary["primary_incumbent_value"] += 1.
                result.summary["primary_objective_allowance"] = 1e-7 + 1e-8 * abs(result.summary["primary_incumbent_value"])
            elif gate == "returned":
                # A feasible all-hour warmer policy costs more than the known
                # baseline, while the claimed primary anchor remains valid.
                result.building_trajectory = original(config, frame.assign(tariff_bucket="valley"), 1)
                metrics = m.base.independent_metrics(config, frame, result.building_trajectory)
                result.summary["objective_yuan_per_year"] = metrics["bill_yuan"]
                result.summary["total_service_emissions_kg_per_year"] = metrics["carbon_kg"]
            else:
                result.summary["primary_objective_allowance"] = 1000.
        return result, audit

    monkeypatch.setattr(m.base, "run_one", wrong_bound)
    m.run(out)
    manifest = _assert_final(out, False, 10)
    assert manifest["failures"][0]["stage"] == "cost_endpoint_independent_audit"
    metrics = _read(out, "results/guyuan/cost_endpoint/independent_ledger_audit.json")
    key = {"primary": "known_feasible_primary_bound", "returned": "known_feasible_returned_bound",
           "allowance": "declared_primary_allowance"}[gate]
    assert metrics["checks"][key] is False and metrics["passed"] is False
    if gate == "returned":
        assert metrics["checks"]["known_feasible_primary_bound"]
        assert metrics["checks"]["declared_primary_allowance"]


@pytest.mark.parametrize("target", ["protocol", "source", "code", "runtime", "missing_code", "missing_input"])
def test_global_identity_failures_abort_before_any_city(batch, monkeypatch, target):
    out, calls, _, _ = batch
    if target == "protocol":
        (out / "protocol.json").write_text("{}", encoding="utf-8")
    elif target == "source":
        actual = m._source_identity()
        monkeypatch.setattr(m, "_source_identity", lambda: {**actual, "unexpected": "0" * 64})
    elif target == "code":
        actual = m.code_identity()
        monkeypatch.setattr(m, "code_identity", lambda: {**actual, "unexpected": "0" * 64})
    elif target == "runtime":
        actual = m.runtime_identity()
        monkeypatch.setattr(m, "runtime_identity", lambda: {**actual, "scipy": "injected"})
    else:
        protocol = _read(out, "protocol.json")
        protocol["code_sha256" if target == "missing_code" else "input_sha256"].popitem()
        m._write(out / "protocol.json", protocol)
        m._write(out / "protocol_lock.json", {"protocol.json": m.base.sha(out / "protocol.json")})
    with pytest.raises(m.IdentityFailure):
        m.run(out)
    assert calls == []
    manifest = _read(out, "manifest.json")
    assert manifest["accepted_rows"] == 0
    assert manifest["status"] == "aborted"
    assert manifest["identity_verified"] is False and manifest["comparison_valid"] is False
    assert manifest["all_requested_cases_passed"] is False
    assert manifest["global_failure"]["type"] == "IdentityFailure"
    assert all(t["status"] == "skipped" for t in manifest["tasks"])
    with pytest.raises(m.IdentityFailure):
        m.verify(out)


def test_identity_change_during_middle_solve_invalidates_prior_rows_and_stops(batch, monkeypatch):
    out, calls, _, double = batch

    def corrupt_after_solve(name, config, frame, folder, budget, gap, *, primary=None):
        result = double(name, config, frame, folder, budget, gap, primary=primary)
        if folder.name == "guyuan" and name == "cost_endpoint":
            (out / "protocol.json").write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(m.base, "run_one", corrupt_after_solve)
    with pytest.raises(m.IdentityFailure):
        m.run(out)
    manifest = _read(out, "manifest.json")
    assert manifest["accepted_rows"] == 5  # Retained only, explicitly invalidated.
    assert manifest["comparison_valid"] is False and manifest["all_requested_cases_passed"] is False
    assert not any(c[0] == "wuhan" for c in calls)


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_process_interrupt_is_not_swallowed(batch, monkeypatch, exception):
    out, calls, _, _ = batch

    def stop(*args):
        raise exception("injected user/process interruption")

    monkeypatch.setattr(m, "prepare_city", stop)
    with pytest.raises(exception):
        m.run(out)
    assert calls == [] and not (out / "manifest.json").exists()
    assert _read(out, "checkpoint.json")["tasks"][0]["status"] == "running"
    with pytest.raises(m.IdentityFailure, match="incomplete run"):
        m.verify(out)
    with pytest.raises(ValueError, match="output exists"):
        m.run(out)


def test_atomic_checkpoint_replace_failure_keeps_complete_prior_json(batch, monkeypatch):
    import heatplan.provenance as provenance
    out, calls, _, _ = batch
    replacer = provenance.os.replace

    def reject_checkpoint(source, target):
        if Path(target).name == "checkpoint.json" and (out / "checkpoint.json").exists():
            raise OSError("injected checkpoint replace failure")
        return replacer(source, target)

    monkeypatch.setattr(provenance.os, "replace", reject_checkpoint)
    with pytest.raises(OSError, match="checkpoint replace"):
        m.run(out)
    checkpoint = _read(out, "checkpoint.json")
    assert checkpoint["revision"] == 1
    assert all(t["status"] == "pending" for t in checkpoint["tasks"])
    assert calls == [] and not (out / "manifest.json").exists()
    assert not list(out.glob(".checkpoint.json.*.tmp"))


def test_existing_run_and_existing_freeze_refuse_overwrite(batch):
    out, _, _, _ = batch
    protocol_sha = m.base.sha(out / "protocol.json")
    with pytest.raises(ValueError, match="output exists"):
        m.freeze(out)
    m.run(out)
    before = {p.relative_to(out).as_posix(): m.base.sha(p) for p in out.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="output exists"):
        m.run(out)
    assert before == {p.relative_to(out).as_posix(): m.base.sha(p) for p in out.rglob("*") if p.is_file()}
    assert m.base.sha(out / "protocol.json") == protocol_sha


def test_final_output_tamper_and_extra_file_rejected(batch):
    out, _, _, _ = batch
    m.run(out)
    extra = out / "unexpected.json"
    extra.write_text("{}", encoding="utf-8")
    with pytest.raises(m.IdentityFailure, match="inventory changed"):
        m.verify(out)
    extra.unlink()
    (out / "comparison.json").write_text("[]", encoding="utf-8")
    with pytest.raises(m.IdentityFailure, match="artifact changed"):
        m.verify(out)


def test_empty_duplicate_unknown_city_selection_rejected(tmp_path):
    for cities in ((), ("wuhan", "wuhan"), ("unknown",)):
        with pytest.raises(ValueError, match="subset"):
            m.freeze(tmp_path / "invalid", cities)
    assert not (tmp_path / "invalid").exists()


def test_freeze_single_city_is_prospectively_registered(tmp_path):
    out = tmp_path / "single"
    m.freeze(out, ("wuhan",))
    assert m.verify(out)["cities"] == ["wuhan"]
    assert _read(out, "protocol.json")["acceleration_requested"] is False


def test_solver_double_results_carry_unambiguous_test_scope(batch):
    out, _, _, _ = batch
    m.run(out)
    rows = _read(out, "comparison.json")
    assert all(r["solution_quality"]["scope"] == "fault_injection_feedback_test_double"
               for r in rows if r["case"] != "baseline_delta0")


def test_explicit_identity_failure_cannot_be_downgraded_to_city_failure(batch, monkeypatch):
    out, calls, _, _ = batch

    def detected(*args):
        raise m.IdentityFailure("injected explicit identity detector")

    monkeypatch.setattr(m, "prepare_city", detected)
    with pytest.raises(m.IdentityFailure, match="explicit identity"):
        m.run(out)
    assert calls == []
    assert _read(out, "manifest.json")["global_failure"]["type"] == "IdentityFailure"


def test_self_consistent_protocol_replacement_during_run_is_rejected(batch, monkeypatch):
    out, _, _, double = batch

    def replace_protocol(name, config, frame, folder, budget, gap, *, primary=None):
        result = double(name, config, frame, folder, budget, gap, primary=primary)
        if folder.name == "guyuan" and name == "cost_endpoint":
            protocol = _read(out, "protocol.json")
            protocol["cities"] = ["wuhan"]
            m._write(out / "protocol.json", protocol)
            m._write(out / "protocol_lock.json", {"protocol.json": m.base.sha(out / "protocol.json")})
        return result

    monkeypatch.setattr(m.base, "run_one", replace_protocol)
    with pytest.raises(m.IdentityFailure, match="initial run identity changed"):
        m.run(out)
    manifest = _read(out, "manifest.json")
    assert manifest["accepted_rows"] == 5 and manifest["comparison_valid"] is False


def test_protocol_change_during_final_export_does_not_publish_success(batch, monkeypatch):
    out, _, _, _ = batch
    writer = m.pd.DataFrame.to_csv

    def change_on_export(frame, path, *args, **kwargs):
        result = writer(frame, path, *args, **kwargs)
        if Path(path) == out / "comparison.csv":
            protocol = _read(out, "protocol.json")
            protocol["cities"] = ["wuhan"]
            m._write(out / "protocol.json", protocol)
            m._write(out / "protocol_lock.json", {"protocol.json": m.base.sha(out / "protocol.json")})
        return result

    monkeypatch.setattr(m.pd.DataFrame, "to_csv", change_on_export)
    with pytest.raises(m.IdentityFailure, match="initial run identity changed"):
        m.run(out)
    manifest = _read(out, "manifest.json")
    assert manifest["accepted_rows"] == 12
    assert manifest["all_requested_cases_passed"] is False
    assert manifest["comparison_valid"] is False and manifest["status"] == "aborted"


def test_nested_manifest_addition_is_not_excluded_from_inventory(batch):
    out, _, _, _ = batch
    m.run(out)
    (out / "results/wuhan/cost_endpoint/manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(m.IdentityFailure, match="inventory changed"):
        m.verify(out)


@pytest.mark.parametrize("key,value", [("accepted_rows", 99), ("all_requested_cases_passed", False),
                                       ("failures", [{"reason": "invented"}]), ("protocol_sha256", "0" * 64)])
def test_manifest_accounting_tamper_is_rejected(batch, key, value):
    out, _, _, _ = batch
    m.run(out)
    manifest = _read(out, "manifest.json")
    manifest[key] = value
    m._write(out / "manifest.json", manifest)
    with pytest.raises(m.IdentityFailure, match="accounting mismatch"):
        m.verify(out)


def test_concurrent_invocations_have_one_owner_and_cannot_overwrite(batch, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    out, _, _, _ = batch
    iterator = Path.iterdir
    barrier = Barrier(2)

    def simultaneous_inspection(path):
        if path == out:
            entries = list(iterator(path))
            barrier.wait(timeout=30)
            return iter(entries)
        return iterator(path)

    monkeypatch.setattr(Path, "iterdir", simultaneous_inspection)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(m.run, out) for _ in range(2)]
        results, errors = [], []
        for future in futures:
            try:
                results.append(future.result(timeout=90))
            except Exception as error:
                errors.append(error)
    assert len(results) == len(errors) == 1
    assert isinstance(errors[0], FileExistsError)
    _assert_final(out, True, 12)


def test_deep_atomic_evidence_json_path_is_supported(tmp_path):
    path = tmp_path / ("a" * 70) / ("b" * 70) / "independent_ledger_audit.json"
    m._write(path, {"complete": True})
    # Ordinary Win32 reads are also subject to MAX_PATH; inspect the file
    # through the native extended form when its final filename exceeds260.
    read_path = Path("\\\\?\\" + str(path.resolve())) if sys.platform == "win32" else path
    assert json.loads(read_path.read_text(encoding="utf-8")) == {"complete": True}
    assert not list(read_path.parent.glob("*.tmp"))
