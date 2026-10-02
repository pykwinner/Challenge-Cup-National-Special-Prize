"""Check release rejection paths using tiny explicitly synthetic file fixtures."""
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest


spec = importlib.util.spec_from_file_location("release_package", Path(__file__).parents[1]/"scripts/package_competition_release.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def setup_fixture(tmp_path, *, approved=False):
    (tmp_path/"data").mkdir()
    data = tmp_path/"data/tiny_test_fixture.txt"
    data.write_text("test fixture, not observational data", encoding="utf-8")
    gate = {"reviewed_by": "C0", "approved_for_submission": approved,
            "approved_data_files": {"data/tiny_test_fixture.txt": package.digest(data)}}
    (tmp_path/"gate.json").write_text(json.dumps(gate), encoding="utf-8")
    selection = {"selected_by": "C0", "runs": ["run"], "data_files": ["data/tiny_test_fixture.txt"], "provenance_gate": "gate.json"}
    (tmp_path/"selection.json").write_text(json.dumps(selection), encoding="utf-8")
    return data


def invoke(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["package", "--root", str(tmp_path), "--selection", str(tmp_path/"selection.json"), "--output", str(tmp_path/"release.zip")])
    package.main()


def test_unapproved_provenance_blocks_release(tmp_path, monkeypatch):
    setup_fixture(tmp_path)
    with pytest.raises(ValueError, match="data-provenance review"):
        invoke(tmp_path, monkeypatch)
    assert not (tmp_path/"release.zip").exists()


def test_data_edit_after_provenance_review_is_rejected(tmp_path, monkeypatch):
    data = setup_fixture(tmp_path, approved=True)
    data.write_text("changed test value", encoding="utf-8")
    with pytest.raises(ValueError, match="data changed"):
        invoke(tmp_path, monkeypatch)
    assert not (tmp_path/"release.zip").exists()


def test_unreviewed_extra_data_cannot_enter_whitelist(tmp_path, monkeypatch):
    setup_fixture(tmp_path, approved=True)
    path = tmp_path/"selection.json"
    selection = json.loads(path.read_text(encoding="utf-8"))
    selection["data_files"].append("data/unreviewed.csv")
    path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(ValueError, match="whitelist"):
        invoke(tmp_path, monkeypatch)


def test_inside_accepts_relative_roots_but_rejects_escaping_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/"run").mkdir()
    assert package.inside(Path("run"), "summary.json") == tmp_path/"run/summary.json"
    with pytest.raises(ValueError, match="escapes"):
        package.inside(Path("run"), "../outside.txt")


def test_approved_mathematical_fixture_package_has_verified_bytes(tmp_path, monkeypatch):
    setup_fixture(tmp_path, approved=True)
    run = tmp_path/"run"
    run.mkdir()
    (run/"summary.json").write_text('{"test_fixture_only":true}', encoding="utf-8")
    (run/"manifest.json").write_text(json.dumps({"complete": True, "files": {
        "summary.json": package.digest(run/"summary.json")}}), encoding="utf-8")
    gate_path = tmp_path/"gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["approved_runs"] = {"run": {"manifest_sha256": package.digest(run/"manifest.json"), "purpose": "mathematical_validation"}}
    gate_path.write_text(json.dumps(gate), encoding="utf-8")
    invoke(tmp_path, monkeypatch)
    with zipfile.ZipFile(tmp_path/"release.zip") as archive:
        record = json.loads(archive.read("north_heat_challenge/release_manifest.json"))
        assert record["files"]["data/tiny_test_fixture.txt"] == package.digest(tmp_path/"data/tiny_test_fixture.txt")
        assert archive.testzip() is None


def test_unreviewed_document_or_supplement_cannot_bypass_data_review(tmp_path, monkeypatch):
    setup_fixture(tmp_path, approved=True)
    path = tmp_path/"selection.json"
    selected = json.loads(path.read_text(encoding="utf-8"))
    selected["supplementary_files"] = ["data/unreviewed.csv"]
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="supplementary_files.*whitelist"):
        invoke(tmp_path, monkeypatch)
    assert not (tmp_path/"release.zip").exists()


def test_old_documents_are_not_automatically_packaged(tmp_path, monkeypatch):
    (tmp_path/"docs").mkdir()
    (tmp_path/"docs/obsolete_claim.md").write_text("obsolete test claim", encoding="utf-8")
    test_approved_mathematical_fixture_package_has_verified_bytes(tmp_path, monkeypatch)
    with zipfile.ZipFile(tmp_path/"release.zip") as archive:
        assert not any("obsolete_claim" in name for name in archive.namelist())


def test_external_manifest_verifies_original_inputs_and_outputs(tmp_path):
    data = tmp_path/"original.txt"
    data.write_text("explicit analytic fixture", encoding="utf-8")
    run = tmp_path/"run"
    run.mkdir()
    result = run/"result.json"
    result.write_text("{}", encoding="utf-8")
    (run/"manifest.json").write_text(json.dumps({"complete": True,
        "inputs": [{"file": "original.txt", "sha256": package.digest(data)}],
        "outputs": [{"file": "result.json", "sha256": package.digest(result)}]}), encoding="utf-8")
    assert package.checked_manifest(run, tmp_path)["files"] == {"result.json": package.digest(result)}
    data.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="external input"):
        package.checked_manifest(run, tmp_path)


def test_research_approval_cannot_be_used_for_submission(tmp_path, monkeypatch):
    setup_fixture(tmp_path, approved=False)
    path = tmp_path/"gate.json"
    gate = json.loads(path.read_text(encoding="utf-8"))
    gate["approved_for_research_delivery"] = True
    path.write_text(json.dumps(gate), encoding="utf-8")
    with pytest.raises(ValueError, match="data-provenance review"):
        invoke(tmp_path, monkeypatch)
    assert not (tmp_path/"release.zip").exists()
