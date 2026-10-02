"""Posthoc scope sensitivity + fixed-selection calibration pairs + delivery checks.

Does not change the frozen protocol, selection, core snapshots or primary results.
  python scripts/bdg2_benchmark_diagnostics.py analyze
  python scripts/bdg2_benchmark_diagnostics.py verify
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/execution_20261002/external_benchmark"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def all_results():
    return [r for label in ("legacy", "new_core") for r in read(OUT / "versions" / label / "results.json")]


def delta_counts(values):
    values = np.asarray(values, dtype=float)
    return {"improved": int((values < -1e-12).sum()), "worsened": int((values > 1e-12).sum()),
            "tied": int((np.abs(values) <= 1e-12).sum())}


def analyze():
    records = all_results()
    lookup = {(r["core_label"], r["case_id"], r["year"], r["method"]): r for r in records}
    calibration = []
    for r in records:
        if r["method"] != "extreme_calibrated":
            continue
        row = {"case_id": r["case_id"], "year": r["year"], "status": r["status"]}
        if r["status"] == "ok":
            before = lookup[(r["core_label"], r["case_id"], r["year"], "extreme_aware")]
            row.update(unchanged_selected_dates=[x["date"] for x in before["selected_days"]] == [x["date"] for x in r["selected_days"]],
                       actual_m=r["actual_m"], conservation_residual=r["calibration_summary"]["weight_conservation_residual"],
                       maximum_bound_violation=r["calibration_summary"]["maximum_bound_violation"])
            for metric in ("retained_scope_abs_energy_bias", "ldc_w1_over_mean_load"):
                a, z = before["metrics"][metric], r["metrics"][metric]
                row[f"before_{metric}"] = a
                row[f"after_{metric}"] = z
                row[f"delta_{metric}"] = z-a if a is not None and z is not None else None
        else:
            row.update(exception_type=r["exception_type"], exception=r["exception"])
        calibration.append(row)
    pd.DataFrame(calibration).to_csv(OUT / "calibration_paired_comparisons.csv", index=False)
    scope = []
    calendars = {}
    for r in records:
        key = (r["case_id"], r["year"])
        if key not in calendars:
            calendars[key] = pd.read_csv(OUT / "cases" / key[0] / str(key[1]) / "observed_calendar.csv")
        calendar = calendars[key]
        values = calendar["energy_kwh"].to_numpy(dtype=float) / 1.0
        valid = np.isfinite(values) & (values >= 0)
        observed = values[valid]
        energy, peak = float(observed.sum()), float(observed.max())
        row = {"analysis": "posthoc_scope_sensitivity", "core_label": r["core_label"],
               "case_id": r["case_id"], "category": r["category"], "year": r["year"], "method": r["method"],
               "method_status": r["status"], "calendar_hours": len(calendar),
               "all_valid_observed_hours": len(observed), "all_valid_observed_hour_coverage": len(observed)/len(calendar),
               "all_valid_observed_energy_kwh_partial_year": energy, "all_valid_observed_peak_kw": peak,
               "observed_calendar_sha256": sha(OUT / "cases" / key[0] / str(key[1]) / "observed_calendar.csv")}
        if r["status"] == "ok":
            k = r["metrics"]["capacity_only_k_kw"]
            deficit = np.maximum(observed-k, 0)
            row.update(capacity_only_k_kw=k,
                       full_observed_peak_retention_ratio=k/peak if peak > 0 else None,
                       full_observed_deficit_hours=int((deficit > 0).sum()),
                       full_observed_deficit_hour_fraction=float((deficit > 0).mean()),
                       full_observed_deficit_energy_kwh=float(deficit.sum()),
                       full_observed_deficit_energy_fraction=float(deficit.sum())/energy if energy > 0 else None,
                       retained_scope_peak_retention_ratio=r["metrics"]["peak_retention_ratio"],
                       retained_scope_deficit_hours=r["metrics"]["capacity_only_deficit_hours"],
                       retained_scope_deficit_hour_fraction=r["metrics"]["capacity_only_deficit_hour_fraction"],
                       retained_scope_deficit_energy_kwh=r["metrics"]["capacity_only_deficit_energy_kwh"],
                       retained_scope_deficit_energy_fraction=r["metrics"]["capacity_only_deficit_energy_fraction"])
        else:
            row.update(diagnostic_status="unavailable", reason="method failed; no calibrated K", exception=r["exception"])
        scope.append(row)
    pd.DataFrame(scope).to_csv(OUT / "posthoc_scope_sensitivity.csv", index=False)
    write(OUT / "posthoc_scope_sensitivity.json", {
        "analysis": "posthoc_scope_sensitivity", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_lock_sha256": sha(OUT / "protocol_lock.json"), "sidecar_script_sha256": sha(__file__),
        "scope": "all finite nonnegative meter hours; temperature ignored; no fill; partial observed year",
        "selection_or_weights_changed": False, "records": scope})
    good = [r for r in calibration if r["status"] == "ok"]
    summary = {"calibration_successful_pairs": len(good), "calibration_failed_pairs": len(calibration)-len(good),
               "calibration_energy_bias": delta_counts([r["delta_retained_scope_abs_energy_bias"] for r in good]),
               "calibration_ldc": delta_counts([r["delta_ldc_w1_over_mean_load"] for r in good]),
               "calibration_maximum_conservation_residual": max(r["conservation_residual"] for r in good),
               "calibration_maximum_bound_violation": max(r["maximum_bound_violation"] for r in good),
               "scope_summaries": []}
    for metric in ("retained_scope_abs_energy_bias", "ldc_w1_over_mean_load"):
        summary[f"calibration_paired_median_before_{metric}"] = float(np.median([r[f"before_{metric}"] for r in good]))
        summary[f"calibration_paired_median_after_{metric}"] = float(np.median([r[f"after_{metric}"] for r in good]))
    for label in ("legacy", "new_core"):
        for method in sorted({r["method"] for r in records if r["core_label"] == label}):
            group = [r for r in scope if r["core_label"] == label and r["method"] == method and r["method_status"] == "ok"]
            item = {"core_label": label, "method": method, "successful_records": len(group),
                    "positive_full_scope_deficit_cases": sum(r["full_observed_deficit_hours"] > 0 for r in group),
                    "positive_retained_scope_deficit_cases": sum(r["retained_scope_deficit_hours"] > 0 for r in group)}
            for key in ("full_observed_peak_retention_ratio", "full_observed_deficit_hour_fraction", "full_observed_deficit_energy_fraction"):
                vals = [r[key] for r in group if r[key] is not None]
                item[f"median_{key}"] = float(np.median(vals)) if vals else None
            summary["scope_summaries"].append(item)
    write(OUT / "diagnostic_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def verify():
    spec = importlib.util.spec_from_file_location("bdg2_delivery_adapter", ROOT / "scripts/bdg2_external_benchmark.py")
    adapter = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = adapter
    spec.loader.exec_module(adapter)
    adapter.verify_lock()
    records = all_results()
    selection = read(OUT / "selection.json")
    assert len(selection["cases"]) == 12
    assert len({c["building_id"] for c in selection["cases"]}) == 12
    assert len(records) == 12*2*(2+3)
    assert len(read(OUT / "posthoc_scope_sensitivity.json")["records"]) == len(records)
    tested = ET.parse(OUT / "focused_tests.xml").getroot()
    suites = [tested] if tested.tag == "testsuite" else tested.findall("testsuite")
    assert sum(int(s.get("tests", 0)) for s in suites) == 15
    assert sum(int(s.get("failures", 0))+int(s.get("errors", 0)) for s in suites) == 0
    for label in ("legacy", "new_core"):
        manifest = read(OUT / "versions" / label / "run_manifest.json")
        for file in manifest["files"]:
            assert sha(ROOT / file["path"]) == file["sha256"], file["path"]
        assert manifest["protocol_lock_sha256"] == sha(OUT / "protocol_lock.json")
    checked = 0
    for r in records:
        folder = OUT / "cases" / r["case_id"] / str(r["year"])
        assert sha(folder / "observed_calendar.csv") == r["observed_calendar_sha256"]
        if r["status"] != "ok":
            assert r["exception_type"] and r["exception"] and r["traceback"]
            continue
        rep = pd.read_csv(folder / f"{r['core_label']}_{r['method']}_actual_days.csv")
        by_day = rep.groupby("original_day_index")
        assert by_day.size().eq(24).all()
        assert by_day["period_weight"].nunique().eq(1).all()
        assert np.isfinite(rep[["energy_kwh", "load_kw", "outdoor_temp", "period_weight"]].to_numpy()).all()
        assert (rep["period_weight"] > 0).all()
        assert np.isclose(by_day["period_weight"].first().sum(), r["input_retained_days"], rtol=0, atol=1e-8)
        assert len(by_day) == r["actual_m"]
        assert len(r["elapsed_seconds"]) == 3 and all(t >= 0 for t in r["elapsed_seconds"])
        if r["method"] == "ordinary":
            assert r["actual_m"] == r["paired_actual_budget"]
        if r["method"] == "extreme_calibrated":
            assert r["calibration_summary"]["solver_success"]
            assert r["calibration_summary"]["maximum_bound_violation"] <= 1e-8
        events = read(folder / f"{r['core_label']}_{r['method']}_ramp_events.json")
        assert all(pd.Timestamp(e["end"])-pd.Timestamp(e["start"]) == pd.Timedelta(hours=1) for e in events)
        checked += 1
    files = [p for p in OUT.rglob("*") if p.is_file() and p.name not in ("delivery_check.json", "status.json") and "__pycache__" not in p.parts]
    files += [Path(__file__), ROOT / "scripts/bdg2_external_benchmark.py", ROOT / "tests/test_bdg2_external_benchmark.py"]
    write(OUT / "delivery_check.json", {"verified_utc": datetime.now(timezone.utc).isoformat(), "all_checks_passed": True,
          "frozen_inputs_and_protocol_verified": True, "case_count": len(selection["cases"]), "case_years": 24,
          "total_method_records": len(records), "successful_records_verified": checked,
          "failed_method_records": len(records)-checked, "focused_tests_passed": 15,
          "files": [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(files)]})
    adapter.status("completed", algorithms_started=True, selection_frozen=True, pending=[],
                   completed_versions=["legacy", "new_core"], successful_method_records=checked,
                   failed_method_records=len(records)-checked, focused_tests_passed=15,
                   posthoc_scope_sensitivity_completed=True, delivery_check="delivery_check.json",
                   limitations="see findings.md and posthoc_scope_sensitivity.md")
    print(f"Verified {checked} successful / {len(records)} method records; {len(records)-checked} explicit failure; 15 focused tests.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("analyze", "verify"))
    args = parser.parse_args()
    analyze() if args.phase == "analyze" else verify()
