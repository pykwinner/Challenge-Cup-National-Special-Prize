"""Gather this follow-up's real runs, fault tests and preserved legacy identities."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
import run_three_city_climate_dispatch_resilient as resilient

HERE = Path(__file__).resolve().parent


def main():
    destination = HERE / "validation_manifest.json"
    resilient.base.require(not destination.exists(), "validation manifest exists; preserve prior evidence")
    tests = []
    for label in ("01", "02", "03"):
        path = HERE / f"tests_{label}.xml"
        tree = ET.parse(path)
        cases = list(tree.iter("testcase"))
        counts = {"total": len(cases), "passed": sum(not any(c.find(tag) is not None for tag in ("failure", "error", "skipped")) for c in cases),
                  "failed": sum(c.find("failure") is not None for c in cases),
                  "errors": sum(c.find("error") is not None for c in cases),
                  "skipped": sum(c.find("skipped") is not None for c in cases), "deselected": 0}
        tests.append({"round": label, **counts,
            "results": path.relative_to(ROOT).as_posix(), "results_sha256": resilient.base.sha(path),
            "failed_cases": [c.attrib["name"] for c in cases if c.find("failure") is not None or c.find("error") is not None],
            "classification": "functional fault injection; admitted real 2880h drivers and true feedback RC; optimization replaced by explicitly labelled test double"})
    legacy = []
    for name in ("climate_dispatch_run_01", "climate_dispatch_refinement_02"):
        folder = ROOT / "docs/implementation_20261002_followup/parallel_stage_02" / name
        protocol = resilient.base.verify(folder)
        legacy.append({"run": folder.relative_to(ROOT).as_posix(), "verified": True,
            "protocol_sha256": resilient.base.sha(folder / "protocol.json"),
            "manifest_sha256": resilient.base.sha(folder / "manifest.json"),
            "frozen_input_identity_count": len(protocol["input_sha256"]),
            "frozen_code_identity_count": len(protocol["code_sha256"]),
            "result_file_identity_count": len(resilient.base.read(folder / "manifest.json")["files"]),
            "rerun_in_this_stage": False, "raw_bytes_preserved": True})
    run = HERE / "wuhan_2880h_01"
    protocol = resilient.verify(run)
    manifest = resilient.base.read(run / "manifest.json")
    export = resilient.base.read(HERE / "wuhan_export_audit_01.json")
    controls = []
    directory = HERE / "pytest_fault_injection_03"
    for path in sorted(directory.glob("test*/batch/checkpoint.json")):
        checkpoint = resilient.base.read(path)
        if checkpoint["failures"] or checkpoint["status"] != "completed" or "concurrent" in path.parent.parent.name:
            controls.append({"test_directory": path.parent.parent.name,
                "checkpoint": path.relative_to(ROOT).as_posix(), "checkpoint_sha256": resilient.base.sha(path),
                "status": checkpoint["status"], "retained_rows": len(checkpoint["rows"]),
                "failures": checkpoint["failures"], "comparison_valid": checkpoint["comparison_valid"]})
    report = {"recorded_utc": datetime.now(timezone.utc).isoformat(),
        "status": "implementation follow-up; research candidate evidence only; no upload/tag performed",
        "new_entry": {"path": "scripts/run_three_city_climate_dispatch_resilient.py",
                      "sha256": resilient.base.sha(ROOT / "scripts/run_three_city_climate_dispatch_resilient.py")},
        "new_test": {"path": "tests/test_three_city_climate_dispatch_resilient.py",
                     "sha256": resilient.base.sha(ROOT / "tests/test_three_city_climate_dispatch_resilient.py")},
        "runtime_identity": protocol["runtime_identity"], "test_rounds": tests,
        "final_tests_passed": tests[-1]["failed"] == tests[-1]["errors"] == tests[-1]["skipped"] == 0,
        "legacy_identity_verification": legacy,
        "legacy_script_sha256": {name: resilient.base.sha(ROOT / "scripts" / name) for name in
            ("run_three_city_climate_dispatch.py", "refine_three_city_climate_dispatch.py")},
        "fresh_numeric_runs": [{"path": run.relative_to(ROOT).as_posix(), "cities": protocol["cities"],
            "hours_per_city": 2880, "new_feedback_baselines": manifest["new_baseline_rows"],
            "new_optimization_cases": manifest["new_optimization_cases"], "solver_stage_count": 5,
            "accepted_rows": manifest["accepted_rows"], "failures": manifest["failures"],
            "all_requested_cases_passed": manifest["all_requested_cases_passed"],
            "baseline_rows_reused": 0, "protocol_sha256": resilient.base.sha(run / "protocol.json"),
            "manifest_sha256": resilient.base.sha(run / "manifest.json"),
            "time_limit_per_stage_seconds": 60., "mip_gap_target": 0., "acceleration_requested": False,
            "input_sha256": protocol["input_sha256"], "code_sha256": protocol["code_sha256"],
            "wall_seconds": manifest["wall_seconds"]}],
        "independent_export_audit": {"path": (HERE / "wuhan_export_audit_01.json").relative_to(ROOT).as_posix(),
            "sha256": resilient.base.sha(HERE / "wuhan_export_audit_01.json"), "passed": export["passed"],
            "checks": export["check_count"], "source_zone_tamper_examples": export["source_zone_tamper_examples"],
            "power_to_state_tamper_examples": export["power_to_state_tamper_examples"]},
        "retained_failure_control_snapshots": controls,
        "not_run": [{"item": "new Urumqi/Guyuan2880h optimization", "reason": "Wuhan verifies the new numerical entry; both other cities are exercised with fault-injection control doubles; rc4 numerical evidence is preserved separately and not counted anew"},
                    {"item": "entire previous882 regression suite", "reason": "unchanged core/model; this follow-up runs the new reliability tests and actual newly affected entry"}],
        "limits": ["same public prototype and common methodological tariff/EF", "not local calibrated building/field savings",
                   "zero free investment variables", "all historical/fault evidence retained; earlier failures not erased",
                   "initial protocol and lock pinned per run; atomic claim/checkpoint; interrupted run cannot resume/overwrite"]}
    resilient._write(destination, report)
    print({"final_tests": tests[-1], "legacy_runs_verified": len(legacy),
           "fresh_rows": manifest["accepted_rows"], "export_checks": export["check_count"]})
    return 0 if report["final_tests_passed"] and manifest["all_requested_cases_passed"] and export["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
