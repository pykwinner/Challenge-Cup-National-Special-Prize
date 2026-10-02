"""Portable, read-only reproduction of the two frozen N2 RC studies.

Checks the selected computation inputs, not unused discovery downloads/caches.
Creates a new output directory. --refit repeats the original training rules;
it never searches another structure, loss, split, bound or initial point.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/"outputs/competition_20261001"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(output: Path, refit: bool) -> None:
    output.mkdir(parents=True, exist_ok=False)
    first = load_module("rc_reproduction_first", ROOT/"scripts/validate_external_rc_benchmark.py")
    follow = load_module("rc_reproduction_followup", ROOT/"scripts/validate_external_rc_followup.py")
    required = {}
    first_manifest_file = BASE/"C1_external/manifest.json"
    first_manifest = json.loads(first_manifest_file.read_text(encoding="utf-8"))
    if first_manifest.get("complete") is not True:
        raise ValueError("incomplete first study")
    for item in first_manifest["inputs"]:
        path = ROOT/item["file"]
        if digest(path) != item["sha256"]:
            raise ValueError(f"changed selected source input: {item['file']}")
        required[item["file"]] = item["sha256"]
    required[first_manifest_file.relative_to(ROOT).as_posix()] = digest(first_manifest_file)
    original_observations, _ = first.prepare_observations(
        pd.read_excel(first.EVIDENCE/first.HOUSE_FILE), pd.read_excel(first.EVIDENCE/first.WEATHER_FILE))
    rounded_observations = pd.read_csv(first.DATA/"observations.csv")
    results = {}
    for study, observations, module in (("C1_external", original_observations, first),
                                        ("C1_external_followup", rounded_observations, follow)):
        directory = BASE/study
        manifest_file = directory/"manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest.get("complete") is not True:
            raise ValueError(f"incomplete {study}")
        for item in manifest["outputs"]:
            path = directory/item["file"]
            if digest(path) != item["sha256"]:
                raise ValueError(f"changed frozen result: {path.name}")
        for item in manifest["inputs"]:
            if item["file"] in {"scripts/validate_external_rc_followup.py", "tests/test_external_rc_followup.py"}:
                if digest(ROOT/item["file"]) != item["sha256"]:
                    raise ValueError("follow-up implementation identity changed")
                required[item["file"]] = item["sha256"]
        report_file = directory/"validation_report.json"
        report = json.loads(report_file.read_text(encoding="utf-8"))
        for path in (manifest_file, report_file):
            required[path.relative_to(ROOT).as_posix()] = digest(path)
        expected = np.array([report["fit"]["parameters"][key]["estimate"] for key in
                             ("H_kw_per_c", "C_kwh_per_c", "effective_solar_area_m2")])
        parameters = expected
        fit_check = {"refit_executed": False}
        if refit:
            parameters, fit = module.fit_training_only(observations, 7192)
            if parameters is None:
                raise ValueError("reproduction fit failed")
            if not np.allclose(parameters, expected, rtol=1e-5, atol=1e-7):
                raise ValueError("same training protocol did not reproduce saved parameters")
            fit_check = {"refit_executed": True, "parameters": parameters.tolist(),
                         "maximum_absolute_parameter_difference": float(np.max(np.abs(parameters-expected))),
                         "parameter_comparison_rtol": 1e-5, "parameter_comparison_atol": 1e-7}
        trajectory, scores, _ = follow.evaluate(observations, parameters)
        deltas = []
        for split in ("train", "validation", "test"):
            for mode, values in scores[split].items():
                old = report["metrics"][split][mode]
                for metric in ("mae_c", "rmse_c", "bias_predicted_minus_observed_c", "max_abs_error_c"):
                    deltas.append(abs(values[metric]-old[metric]))
                if not np.allclose([values[k] for k in ("mae_c", "rmse_c", "bias_predicted_minus_observed_c", "max_abs_error_c")],
                                   [old[k] for k in ("mae_c", "rmse_c", "bias_predicted_minus_observed_c", "max_abs_error_c")],
                                   rtol=1e-5 if refit else 0, atol=1e-7 if refit else 1e-9):
                    raise ValueError("saved metrics did not reproduce")
        trajectory.to_csv(output/f"{study}_reproduced.csv", index=False, float_format="%.12g")
        results[study] = {"passed": True, "scalar_metrics_checked": len(deltas),
                          "max_abs_metric_difference": max(deltas), "fit": fit_check,
                          "metrics": scores, "exploratory_after_first_test_inspection": study.endswith("followup")}
    for file, sha in required.items():
        if digest(ROOT/file) != sha:
            raise ValueError("selected input changed during reproduction")
    result = {"complete": True, "studies": results, "selected_inputs_unchanged": True,
              "unused_discovery_files_not_needed": True, "original_results_not_overwritten": True,
              "accuracy_approval": False, "scope": "same-study numerical reproduction, including adverse results"}
    (output/"reproduction_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    required[Path(__file__).relative_to(ROOT).as_posix()] = digest(Path(__file__))
    manifest = {"complete": True, "purpose": "same_study_read_only_reproduction",
                "inputs": [{"file": k, "sha256": v} for k, v in sorted(required.items())],
                "files": {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()},
                "unused_protected_discovery_files_are_not_computation_dependencies": True}
    (output/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"complete": True, "refit_executed": refit,
                      "metrics_checked": sum(r["scalar_metrics_checked"] for r in results.values())}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--refit", action="store_true")
    args = parser.parse_args()
    run(args.output_dir.resolve(), args.refit)
