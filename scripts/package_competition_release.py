"""Package reviewed evidence and portable project files without historical runs.

Use a C0-curated evidence JSON containing runs, data_files and a provenance_gate.
Only completed, hash-consistent run manifests and explicitly reviewed data files
are accepted. Historical example data are not copied implicitly. No optimizer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import zipfile


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"path escapes intended root: {relative}")
    return path


def checked_manifest(directory: Path, root: Path | None = None) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("complete") is not True:
        raise ValueError(f"incomplete evidence: {directory.name}")
    if "files" not in manifest and isinstance(manifest.get("artifact_sha256"), dict):
        manifest = {**manifest, "files": manifest["artifact_sha256"]}
    if isinstance(manifest.get("inputs"), list):
        if root is None:
            raise ValueError("project-relative input identities require a project root")
        names = [item["file"] for item in manifest["inputs"]]
        if len(names) != len(set(names)):
            raise ValueError("duplicate input identities")
        for item in manifest["inputs"]:
            path = inside(root, item["file"])
            if not path.is_file() or digest(path) != item["sha256"]:
                raise ValueError(f"changed or missing external input: {item['file']}")
    if "files" not in manifest and isinstance(manifest.get("outputs"), list):
        # The independent measured-data benchmark preserves project-relative
        # inputs and run-relative outputs. Validate both without rewriting it.
        if root is None or not manifest.get("inputs") or not manifest["outputs"]:
            raise ValueError("external evidence needs a project root and nonempty inputs/outputs")
        outputs = {item["file"]: item["sha256"] for item in manifest["outputs"]}
        inputs = {item["file"]: item["sha256"] for item in manifest["inputs"]}
        if len(outputs) != len(manifest["outputs"]) or len(inputs) != len(manifest["inputs"]):
            raise ValueError("duplicate external evidence identities")
        for name, expected in inputs.items():
            path = inside(root, name)
            if not path.is_file() or digest(path) != expected:
                raise ValueError(f"changed or missing external input: {name}")
        manifest = {**manifest, "files": outputs}
    if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError(f"empty or invalid evidence files: {directory.name}")
    for name, expected in manifest["files"].items():
        path = inside(directory, name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"changed or missing evidence: {directory.name}/{name}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    selection_path = args.selection.resolve()
    if not selection_path.is_relative_to(root):
        raise ValueError("selection must reside inside the project")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("selected_by") != "C0" or not selection.get("runs"):
        raise ValueError("a nonempty C0-reviewed run selection is required")
    gate_path = inside(root, selection.get("provenance_gate", ""))
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    release_kind = selection.get("release_kind", "submission")
    if release_kind not in {"submission", "research_delivery"}:
        raise ValueError("unknown release kind")
    approval_key = "approved_for_submission" if release_kind == "submission" else "approved_for_research_delivery"
    if gate.get("reviewed_by") != "C0" or gate.get(approval_key) is not True:
        raise ValueError("explicit data-provenance review is required before submission packaging")
    data_files = selection.get("data_files", [])
    if not data_files or set(data_files) != set(gate.get("approved_data_files", {})):
        raise ValueError("selected data must exactly match the provenance-reviewed whitelist")
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("refusing to overwrite a release")
    files: set[Path] = set()
    excluded_parts = {"__pycache__", ".pytest_cache", "node_modules", ".venv"}
    for directory in ("src", "tests", "scripts"):
        files.update(p for p in (root/directory).rglob("*") if p.is_file()
                     and not excluded_parts.intersection(p.parts) and p.suffix not in {".pyc", ".pyo"})
    files.update(p for pattern in ("requirements*.txt", "LICENSE*") for p in root.glob(pattern) if p.is_file())
    files.add(selection_path)
    files.add(gate_path)
    for relative in data_files:
        path = inside(root, relative)
        if not path.is_file() or digest(path) != gate["approved_data_files"][relative]:
            raise ValueError(f"data changed since provenance review: {relative}")
        files.add(path)
    # Documentation and supplements can themselves contain historical numbers
    # or copied inputs; they receive the same explicit review as data.
    for selection_key, gate_key in (("documentation_files", "approved_documentation_files"),
                                    ("supplementary_files", "approved_supplementary_files")):
        selected = selection.get(selection_key, [])
        approved = gate.get(gate_key, {})
        if set(selected) != set(approved):
            raise ValueError(f"{selection_key} must exactly match the reviewed whitelist")
        for relative in selected:
            path = inside(root, relative)
            if not path.is_file() or digest(path) != approved[relative]:
                raise ValueError(f"changed or missing reviewed {selection_key}: {relative}")
            files.add(path)
    manifests = {}
    if set(selection["runs"]) != set(gate.get("approved_runs", {})):
        raise ValueError("result inputs also require provenance-reviewed run identities")
    for relative in selection["runs"]:
        directory = inside(root, relative)
        manifest = checked_manifest(directory, root)
        approval = gate["approved_runs"][relative]
        if approval.get("manifest_sha256") != digest(directory/"manifest.json") or approval.get("purpose") not in {
            "mathematical_validation", "public_measurement_validation", "source_based_planning_case"
        }:
            raise ValueError(f"unreviewed result identity or evidence purpose: {relative}")
        manifests[relative] = digest(directory / "manifest.json")
        files.add(directory / "manifest.json")
        files.update(inside(directory, name) for name in manifest["files"])
        for item in manifest.get("inputs", []):
            path = inside(root, item["file"])
            if path not in files:
                raise ValueError(f"external input lacks explicit release selection: {item['file']}")
    record = {"schema_version": 1, "release_kind": release_kind,
              "approved_for_submission": gate.get("approved_for_submission") is True,
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": selection.get("scope"), "run_manifest_sha256": manifests,
              "files": {p.relative_to(root).as_posix(): digest(p) for p in sorted(files)}}
    # The package is readable even on systems without the original drive/conda.
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            archive.write(path, "north_heat_challenge/"+path.relative_to(root).as_posix())
        archive.writestr("north_heat_challenge/release_manifest.json", json.dumps(record, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"archive CRC failure: {bad}")
        for name, expected in record["files"].items():
            if sha256(archive.read("north_heat_challenge/"+name)).hexdigest() != expected:
                raise ValueError(f"archive content mismatch: {name}")
    print(json.dumps({"complete": True, "files": len(files), "bytes": output.stat().st_size,
                      "sha256": digest(output), "output": str(output)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
