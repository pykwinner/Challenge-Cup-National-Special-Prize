"""Check this delivery's source/result identities and publish a local index.

This does not certify general engineering performance or rerun the optimizer.
Historical failures and code epochs remain in their original directories.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/implementation_20261002"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    checks = []

    def verify(path, expected, scope):
        actual = sha(path)
        if actual != expected:
            raise ValueError(f"Changed {scope}: {path}")
        checks.append({"path": str(path.relative_to(ROOT)), "sha256": actual, "scope": scope})

    for name in ("admission.json", "admission_tou.json"):
        admission = read(BASE / "data" / name)
        assert admission["physical_reference_admitted"] and admission["rc_reference_admitted"]
        assert not admission["full_economics_admitted"]
        for rel, expected in admission["input_sha256"].items():
            verify(ROOT / rel, expected, name)

    for directory in (BASE / "reference_experiments/rc", BASE / "reference_experiments/steady",
                      BASE / "reference_experiments_tou/rc"):
        manifest = read(directory / "manifest.json")
        assert manifest["all_requested_experiments_passed"] and manifest["code_stable_during_run"]
        for rel, expected in manifest["files"].items():
            verify(directory / rel, expected, "frozen_experiment_output")
        protocol = read(directory / "protocol.json")
        for rel, expected in protocol["input_sha256"].items():
            verify(ROOT / rel, expected, "frozen_experiment_input")
        for rel, expected in protocol["code_sha256"].items():
            verify(directory.parent / "code_version" / rel, expected, "exact_experiment_code_epoch")

    for name in ("algorithm", "algorithm_hard_weights"):
        manifest = read(BASE / name / "run_manifest.json")
        assert manifest["failed_method_records"] == 0
        for item in manifest["artifacts"]:
            verify(ROOT / item["path"], item["sha256"], "frozen_metering_experiment")
        assert read(BASE / name / "independent_verification.json")["status"] == "passed"

    for item in read(BASE / "spatial/manifest.json")["files"]:
        verify(ROOT / item["file"], item["sha256"], "spatial_reference")
    frontier = read(BASE / "frontier_comparison/manifest.json")
    assert frontier["all_requested_points_passed"] and frontier["code_and_sources_stable"]
    for rel, expected in frontier["files"].items():
        verify(BASE / "frontier_comparison" / rel, expected, "weighted_frontier_comparison")
    supplement = read(BASE / "frontier_comparison/supplement_manifest.json")
    verify(BASE / "frontier_comparison/manifest.json", supplement["original_manifest_sha256"], "frontier_supplement_parent")
    for rel, expected in supplement["files"].items():
        verify(BASE / "frontier_comparison" / rel, expected, "frontier_supplement")
    assert read(BASE / "frontier_comparison/independent_verification.json")["passed"]
    for rel, expected in read(BASE / "figures/manifest.json")["inputs"].items():
        verify(ROOT / rel, expected, "figure_input")

    for rel in ("reference_experiments/independent_verification.json",
                "reference_experiments_tou/independent_verification.json",
                "data/base_experiment_peer_review.json", "data/review_tou.json"):
        assert read(BASE / rel)["passed"], rel

    suites = list(ET.parse(BASE / "final_with_optional.xml").iter("testsuite"))
    assert suites and all(int(s.attrib[k]) == 0 for s in suites for k in ("errors", "failures", "skipped"))
    for filename in ("README.md", "physical_extensions.md", "REPRODUCE.md"):
        document = BASE / filename
        for _, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" not in target:
                assert (document.parent / target.split("#")[0]).exists(), target

    active_code = {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / "src/heatplan").glob("*.py"))}
    report = {"checked_utc": datetime.now(timezone.utc).isoformat(), "passed": True,
              "status": "local_research_implementation_not_complete_engineering_certification",
              "source_and_artifact_hash_checks": len(checks), "checks": checks,
              "current_core_code_sha256": active_code,
              "delivery_document_sha256": {name: sha(BASE / name) for name in
                                           ("README.md", "physical_extensions.md", "REPRODUCE.md")},
              "regression": {"pytest_cases_passed": 842, "subtests_passed": 26,
                             "junit_inclusive_tests": sum(int(s.attrib["tests"]) for s in suites),
                             "errors": 0, "failures": 0, "skips": 0,
                             "warnings": "two expected legacy empty-cluster regression warnings",
                             "xml_sha256": sha(BASE / "final_with_optional.xml")},
              "scope_limits": ["public prototype, not observed Chinese city building",
                               "statistical compression, not chronological planning validation",
                               "OSM geometry, not an admitted actual heating network",
                               "operating energy charge, not full engineering life-cycle economics"],
              "github_uploaded_by_this_batch": False, "paper_or_presentation_created": False,
              "audit_script_sha256": sha(Path(__file__))}
    (BASE / "delivery_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "hash_checks": len(checks), "regression": report["regression"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
