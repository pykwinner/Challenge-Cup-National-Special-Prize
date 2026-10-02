"""Verify saved evidence and run only the optional spatial-reference tests.

The isolated directory is process-local, ignored, and excluded from the manifest.
No global environment, original CSV, or old audit evidence is modified.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CITIES = ("guyuan", "urumqi", "wuhan")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    now = datetime.now(timezone.utc).isoformat()
    status = json.loads((OUT / "status.json").read_text(encoding="utf-8"))
    status.update(stage="focused_tests_and_evidence_verification", state="running", updated_utc=now,
                  completed_stages=["protocol_registered", "selection_frozen", "bounded_native_intake", "topology_and_routes", "three_maps"],
                  next_stage="pdf_visual_qa_and_finalize")
    write("status.json", status)
    integrity = json.loads((OUT / "input_integrity.json").read_text(encoding="utf-8"))
    changes = [p for p, record in integrity["before"].items() if not Path(p).exists() or sha(Path(p)) != record["sha256"]]
    assert not changes, changes
    native_checks = []
    for city in CITIES:
        receipts = json.loads((OUT / city / "native_intake_receipts.json").read_text(encoding="utf-8"))
        for r in receipts:
            if r["status"] == "success":
                assert sha(OUT / r["raw_file"]) == r["sha256"]
                native_checks.append({"city": city, "raw_file": r["raw_file"], "sha256_valid": True, "retrieved_utc": r["utc"]})
    env = dict(os.environ)
    optional = str(OUT / "_runtime_packages")
    env["PYTHONPATH"] = optional + (os.pathsep+env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [sys.executable, "-B", "-m", "pytest", "tests/test_osm_reference.py", "-q", "-p", "no:cacheprovider"]
    test = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, encoding="utf-8", errors="replace")
    (OUT / "focused_tests.txt").write_text(test.stdout+test.stderr, encoding="utf-8")
    version_code = "import json,sys,numpy,pandas,scipy,shapely,networkx,pyproj,matplotlib,pytest; print(json.dumps({'python':sys.version,'executable':sys.executable,**{m.__name__:m.__version__ for m in [numpy,pandas,scipy,shapely,networkx,pyproj,matplotlib,pytest]}}))"
    versions = subprocess.run([sys.executable, "-B", "-c", version_code], cwd=ROOT, env=env, text=True, capture_output=True, encoding="utf-8")
    assert versions.returncode == 0, versions.stderr
    # The build script prefers the main environment's installed packages, appending
    # the isolated path only for missing pyproj. Preserve both actual environments.
    build_version_code = "import sys,json,importlib.util; from pathlib import Path; sys.path.append(str(Path('docs/execution_20261002/spatial_reference/_runtime_packages'))) if importlib.util.find_spec('pyproj') is None else None; import numpy,shapely,networkx,pyproj,matplotlib; print(json.dumps({'python':sys.version,'executable':sys.executable,**{m.__name__:m.__version__ for m in [numpy,shapely,networkx,pyproj,matplotlib]}}))"
    build_versions = subprocess.run([sys.executable, "-B", "-c", build_version_code], cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
    assert build_versions.returncode == 0, build_versions.stderr
    import fitz
    pdf_checks = []
    for city in CITIES:
        path = OUT / city / "map.pdf"
        with fitz.open(path) as document:
            assert len(document) == 1
            page = document[0]
            assert "OSM" in page.get_text()
            rendered = OUT / "qa" / f"{city}_pdf.png"; rendered.parent.mkdir(exist_ok=True)
            # Some PDF rasterizers reuse same-named subset fonts across documents.
            # Render each independent PDF in a fresh process to avoid that cache.
            render_code = "import fitz,sys; d=fitz.open(sys.argv[1]); d[0].get_pixmap(dpi=110).save(sys.argv[2])"
            subprocess.run([sys.executable, "-B", "-c", render_code, str(path), str(rendered)], check=True)
            pdf_checks.append({"city": city, "pages": len(document), "pdf_sha256": sha(path), "render_file": str(rendered.relative_to(OUT)), "render_sha256": sha(rendered)})
    verification = {"verified_utc": now, "state": "focused_tests_passed_awaiting_pdf_visual_qa" if test.returncode == 0 else "tests_failed",
                    "focused_tests": {"command": command, "process_local_PYTHONPATH": optional, "exit_code": test.returncode,
                                      "stdout_file": "focused_tests.txt", "output": test.stdout+test.stderr},
                    "test_environment": json.loads(versions.stdout), "build_environment": json.loads(build_versions.stdout),
                    "no_global_dependency_install": True, "runtime_packages_excluded_from_delivery": True,
                    "optional_test_without_pyproj": {"result": "module skipped as intended", "observed_output": "1 skipped in 0.03s", "not_claimed_as_test_pass": True},
                    "original_csv_count": integrity["source_csv_count"], "original_csv_and_old_audit_files": len(integrity["before"]),
                    "original_inputs_sha256_valid": not changes, "native_responses_sha256_checks": native_checks,
                    "png_visual_qa": {"passed": True, "checked_via": "view_image on all three maps; labels separated and Guyuan red-cross legend added"},
                    "pdf_checks": pdf_checks, "pdf_rendering_note": "Each independent PDF rendered in a fresh process to avoid cross-document subset-font cache reuse",
                    "pdf_visual_qa": {"passed": False, "state": "awaiting_view_image"},
                    "not_run": {"full_project_regression": "outside this delegated spatial scope; parent task handles project regression",
                                "MILP": "spatial-reference-only task; physical data not admitted", "heating_performance_and_cost": "no verified physical inputs", "GitHub_upload": "not authorized in this batch"}}
    write("verification.json", verification)
    results = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    results["state"] = verification["state"]; write("results.json", results)
    assert test.returncode == 0, test.stdout+test.stderr
    print(json.dumps({"focused_tests": test.stdout.strip(), "original_files_unchanged": len(integrity["before"]), "native_responses_verified": len(native_checks), "pdf_pages_each": 1}, ensure_ascii=True))


if __name__ == "__main__": main()
