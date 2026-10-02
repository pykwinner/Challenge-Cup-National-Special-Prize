"""Coordinator's read-only delivery checks, independent of experiment runners."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT.parents[1] / "datasets"
INTAKE = ROOT / "docs/source_intake_20261002/datasets_batch_03"


def digest(path):
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    def invalid(value):
        raise ValueError("Nonfinite JSON token: " + value)
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid)


def main():
    original_checks = []
    for directory, report in [("BDG2", "bdg2_integrity.json"), ("osm", "osm/audit.json")]:
        for entry in read(INTAKE / report)["files"]:
            path = DATA / directory / entry["file"]
            observed = digest(path)
            original_checks.append({"file": str(path), "sha256": observed,
                                    "matches_prior_audit": observed == entry["sha256"],
                                    "size_matches": path.stat().st_size == entry["bytes"]})
    json_checks = []
    for directory, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in {"_runtime_packages", "__pycache__", ".pytest_cache"}]
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix != ".json" or path == HERE / "delivery_verification.json":
                continue
            try:
                read(path)
                json_checks.append({"file": path.relative_to(ROOT).as_posix(), "valid": True, "sha256": digest(path)})
            except (ValueError, OSError) as exc:
                json_checks.append({"file": str(path), "valid": False, "error": str(exc)})
    tests = []
    for relative in ["full_regression.xml", "spatial_optional_tests.xml", "cross_drive_tests.xml"]:
        path = HERE / relative
        if not path.exists():
            tests.append({"file": relative, "missing": True})
            continue
        tree = ET.parse(path)
        suites = list(tree.getroot().iter("testsuite"))
        totals = {key: sum(int(s.get(key, "0")) for s in suites) for key in ["tests", "failures", "errors", "skipped"]}
        tests.append({"file": relative, **totals, "passed": totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]})
    source_paths = ["src/heatplan/scenarios.py", "src/heatplan/representative.py", "src/heatplan/chronology.py", "src/heatplan/chronology_cli.py",
                    "src/heatplan/osm_reference.py", "scripts/prepare_competition_case.py", "scripts/bdg2_external_benchmark.py", "scripts/build_osm_reference_cases.py"]
    result = {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "original_files": len(original_checks),
        "original_files_unchanged": all(c["matches_prior_audit"] and c["size_matches"] for c in original_checks),
        "all_json_valid": all(c["valid"] for c in json_checks),
        "tests": tests,
        "source_sha256": {p: digest(ROOT / p) for p in source_paths},
        "original_checks": original_checks, "json_checks": json_checks,
        "scope": "Integrity and actual test outputs only; independent scientific review is separate.",
    }
    result["passed"] = (result["original_files_unchanged"] and len(original_checks) == 34
                        and result["all_json_valid"] and all(not t.get("missing") and t["failures"] == 0 and t["errors"] == 0 for t in tests))
    (HERE / "delivery_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ["passed", "original_files", "original_files_unchanged", "all_json_valid", "tests"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
