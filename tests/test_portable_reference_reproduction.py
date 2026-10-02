"""Real missing-PDF bundle checks and one full 2880h numerical baseline.

No check/model/audit is mocked. Mutations use copies; original inputs stay fixed.
"""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/reproduce_admitted_reference.py"
SPEC = spec_from_file_location("portable_reference", SCRIPT)
portable = module_from_spec(SPEC)
SPEC.loader.exec_module(portable)


def make_processed_bundle(destination):
    destination = portable.filesystem_path(destination)
    admission = json.loads((ROOT / portable.ADMISSION_PATH).read_text(encoding="utf-8"))
    names = [portable.ADMISSION_PATH] + [admission["input_paths"][role] for role in portable.PROCESSED_ROLES]
    destination.mkdir()
    for name in names:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    assert not list(destination.rglob("*.pdf"))
    return destination


@pytest.fixture(scope="module")
def missing_original_bundle(tmp_path_factory):
    return make_processed_bundle(tmp_path_factory.mktemp("portable_bundle") / "inputs")


def cli(root, output, *options):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(SCRIPT), "--input-root", str(root), "--out", str(output), *options],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=150)


def test_processed_bundle_is_byte_exact_and_sources_are_not_claimed_reverified(missing_original_bundle):
    admission, config, frame, report = portable.inspect_reference(missing_original_bundle)
    assert report["passed"] is True
    assert report["source_statement"] == "原始来源沿用既有审核，本次没有重新核验全部原件"
    assert report["original_source_hashes_verified_this_run"] is False
    assert report["original_content_review_repeated"] is False
    assert report["registered_claims_are_inherited_not_current_verification"] is True
    assert report["chronology"]["hours"] == len(frame) == 2880
    assert report["field_provenance"]["covered_rows"] == 89
    checked = [row for row in report["checks"] if row["checked_this_run"]]
    assert len(checked) == len(portable.PROCESSED_ROLES) == 6
    assert all(row["status"] == "sha256_match" for row in checked)
    bestest = next(row for row in report["checks"] if row["source_id"] == "BESTEST")
    assert bestest["status"] == "missing_not_required_for_numerical_reproduction"
    assert bestest["checked_this_run"] is False
    assert not config["storages"]
    assert admission["full_economics_admitted"] is False


def test_strict_mode_uses_only_isolated_root_and_lists_original_acquisition(missing_original_bundle):
    with pytest.raises(portable.ReferenceRejected) as rejected:
        portable.inspect_reference(missing_original_bundle, verify_original_sources=True)
    report = rejected.value.report
    assert report["passed"] is False
    assert report["original_source_hashes_verified_this_run"] is False
    assert report["failed_files"]
    bestest = next(row for row in report["failed_files"] if row["source_id"] == "BESTEST")
    assert bestest["status"] == "missing"
    assert bestest["acquisition_url"] == "https://www.iea-ebc.org/Data/publications/EBC_Annex_21_BESTEST.pdf"
    # A clean clone can omit every original. If this checkout happens to have
    # it, also prove the selected-root check did not report its bytes as checked.
    if (ROOT / bestest["path"]).is_file():
        assert report["input_root"] == str(portable.filesystem_path(missing_original_bundle))
        assert "actual_sha256" not in bestest
    assert not (missing_original_bundle / bestest["path"]).exists()
    assert all(row["checked_this_run"] for row in report["checks"])
    assert all(row["acquisition_url"] for row in report["failed_files"] if row["path"].endswith(".pdf"))


def test_real_cli_check_only_starts_without_original_pdfs(missing_original_bundle, tmp_path):
    output = portable.filesystem_path(tmp_path / "check_only")
    process = cli(missing_original_bundle, output, "--check-only")
    assert process.returncode == 0, process.stdout + process.stderr
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["passed"] is True
    assert manifest["full_2880h_solver_runs"] == 0
    assert manifest["status"] == "input_checks_only"
    assert not (output / "baseline").exists()
    assert not list((output / "input_bundle").rglob("*.pdf"))
    assert portable.inspect_reference(output / "input_bundle")[3]["passed"] is True


def test_real_cli_strict_rejects_missing_sources_before_solver(missing_original_bundle, tmp_path):
    output = tmp_path / "strict_rejected"
    process = cli(missing_original_bundle, output, "--verify-original-sources")
    assert process.returncode == 2
    source = json.loads((output / "source_identity.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert not manifest["passed"]
    assert any(row["path"].endswith("bestest_6231.pdf") for row in source["failed_files"])
    assert source["original_source_hashes_verified_this_run"] is False
    assert not (output / "baseline").exists()


def test_mutating_each_csv_column_is_rejected_by_real_admission_checks(tmp_path):
    root = make_processed_bundle(tmp_path / "altered_inputs")
    admission = json.loads((root / portable.ADMISSION_PATH).read_text(encoding="utf-8"))
    name = admission["input_paths"]["timeseries"]
    path = root / name
    pristine = path.read_bytes()
    frame = pd.read_csv(path)
    for column in frame.columns:
        altered = frame.copy()
        if pd.api.types.is_numeric_dtype(altered[column]):
            altered[column] = altered[column].astype(float)
            altered.loc[0, column] = float(altered.loc[0, column]) + 0.1
        else:
            altered.loc[0, column] = str(altered.loc[0, column]) + "_changed"
        altered.to_csv(path, index=False)
        with pytest.raises(portable.ReferenceRejected) as rejected:
            portable.inspect_reference(root)
        changed = next(row for row in rejected.value.report["failed_files"] if row["path"] == name)
        assert changed["status"] == "sha256_mismatch", column
        path.write_bytes(pristine)
    assert portable.inspect_reference(root)[3]["passed"] is True


@pytest.mark.parametrize("role", ["rc_config", "field_provenance", "monthly_tariff", "tariff_rules", "base_admission"])
def test_any_required_processed_artifact_change_is_rejected_without_solving(tmp_path, role):
    root = make_processed_bundle(tmp_path / "changed")
    admission = json.loads((root / portable.ADMISSION_PATH).read_text(encoding="utf-8"))
    name = admission["input_paths"][role]
    with (root / name).open("ab") as changed:
        changed.write(b"\n# unadmitted change\n")
    with pytest.raises(portable.ReferenceRejected) as rejected:
        portable.inspect_reference(root)
    assert any(row["path"] == name and row["status"] == "sha256_mismatch" for row in rejected.value.report["failed_files"])


def test_new_physical_parameter_and_rehashed_admission_are_both_refused(tmp_path):
    root = make_processed_bundle(tmp_path / "unadmitted")
    admission_path = root / portable.ADMISSION_PATH
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    config_path = root / admission["input_paths"]["rc_config"]
    with config_path.open("ab") as changed:
        changed.write(b"\nunadmitted_device_cost: 12345\n")
    with pytest.raises(portable.ReferenceRejected):
        portable.inspect_reference(root)
    admission["input_sha256"][admission["input_paths"]["rc_config"]] = portable.sha(config_path)
    admission["full_economics_admitted"] = True
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    with pytest.raises(portable.ReferenceRejected, match="admission_tou.json"):
        portable.inspect_reference(root)


@pytest.mark.parametrize("defect", ["gap", "duplicate", "reordered", "naive", "weighted", "probability", "boundary", "service"])
def test_exact_hour_sequence_and_boundaries_are_checked_numerically(missing_original_bundle, defect):
    admission, config, frame, _ = portable.inspect_reference(missing_original_bundle)
    altered = frame.copy()
    if defect == "gap":
        altered.loc[5, "time"] = "2024-11-16T06:30:00+08:00"
    if defect == "duplicate":
        altered.loc[5, "time"] = altered.loc[4, "time"]
    if defect == "reordered":
        altered = altered.iloc[::-1]
    if defect == "naive":
        altered["time"] = pd.to_datetime(altered.time).dt.tz_localize(None)
    if defect == "weighted":
        altered.loc[5, "period_weight"] = 2
    if defect == "probability":
        altered["scenario_probability"] = altered["scenario_probability"].astype(float)
        altered.loc[5, "scenario_probability"] = 0.5
    if defect == "boundary":
        altered.loc[5, "boundary_group"] = "new_group"
    if defect == "service":
        altered.loc[5, "heating_service"] = 0
    with pytest.raises(ValueError):
        portable.check_chronology(altered, config, admission["time_scope"])


def test_provenance_coverage_cannot_drop_a_config_leaf(missing_original_bundle):
    admission, config, frame, _ = portable.inspect_reference(missing_original_bundle)
    fields = pd.read_csv(missing_original_bundle / admission["input_paths"]["field_provenance"], keep_default_na=False)
    with pytest.raises(ValueError, match="exactly once"):
        portable.check_field_provenance(config, frame, fields.iloc[1:], admission)


def test_existing_output_cannot_be_overwritten(missing_original_bundle, tmp_path):
    with pytest.raises(ValueError, match="output exists"):
        portable.reproduce(missing_original_bundle, tmp_path, check_only=True)


def test_full_2880_baseline_once_without_original_pdfs_and_saved_numerical_audit(missing_original_bundle, tmp_path):
    """The ONLY solver invocation in this test file; preserve outputs under basetemp."""
    output = portable.filesystem_path(tmp_path / "default_baseline_run")
    process = cli(missing_original_bundle, output)
    assert process.returncode == 0, process.stdout + process.stderr
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["passed"] is True
    assert manifest["full_2880h_solver_runs"] == 1
    assert manifest["actual_solver_scope"]["investment_scope"] == "existing_assets_dispatch"
    assert manifest["actual_solver_scope"]["free_investment_variables"] == 0
    assert manifest["algorithm_advantage_claimed"] is False
    assert manifest["original_source_hashes_verified_this_run"] is False
    for name, digest in manifest["files_sha256"].items():
        assert portable.sha(output / name) == digest, name
    destination = output / "baseline"
    for name in ("effective_config.yaml", "input_identity.json", "investments.csv", "dispatch.csv",
                 "building_trajectory.csv", "summary.json", "model_summary_raw.json", "audit.json",
                 "independent_building_audit.json", "independent_saved_output_audit.json"):
        assert (destination / name).is_file(), name
    assert len(pd.read_csv(destination / "building_trajectory.csv")) == 2880
    assert pd.read_csv(destination / "investments.csv").empty
    summary = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
    assert summary["numerical_audit_passed"] is True
    assert summary["source_statement"] == portable.DEFAULT_SOURCE_STATEMENT
    assert all(value["value"] is None for value in summary["excluded_financial_quantities"].values())
    assert summary["storage_SOC"] is None
    assert not list(output.rglob("*soc*"))
    admission, config, frame, _ = portable.inspect_reference(output / "input_bundle")
    positive = portable.audit_saved_baseline(destination, config, frame)
    assert positive["passed"] is True
    assert all(row["passed"] for row in positive["independent_ledger_checks"].values())
    # A saved-output fault injection performs no second solve. The original
    # output and its hashes remain intact, and independent audit must reject.
    negative = portable.filesystem_path(tmp_path / "negative_control_saved_outputs")
    shutil.copytree(destination, negative)
    trajectory = pd.read_csv(negative / "building_trajectory.csv")
    trajectory.loc[1, "heat_kw"] += 0.1
    trajectory.to_csv(negative / "building_trajectory.csv", index=False)
    failed = portable.audit_saved_baseline(negative, config, frame)
    assert failed["passed"] is False
    assert failed["independent_saved_building_audit"]["passed"] is False
    assert not all(row["passed"] for row in failed["independent_ledger_checks"].values())
    portable.save(tmp_path / "saved_output_negative_control.json", failed)
    portable.save(tmp_path / "isolation_validation.json", {
        "input_root": str(missing_original_bundle), "default_output": str(output),
        "original_PDF_count_in_selected_input_root": len(list(missing_original_bundle.rglob("*.pdf"))),
        "full_2880h_solver_runs": 1, "default_numerical_audit_passed": positive["passed"],
        "saved_output_negative_control_rejected": not failed["passed"],
        "metrics": positive["metrics"], "existing_admission_sha256": portable.sha(output / "input_bundle" / portable.ADMISSION_PATH)
    })
