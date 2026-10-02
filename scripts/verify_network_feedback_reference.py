"""Rebuild saved network constraints and check vectors, mode exports and cuts."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from heatplan.network_feedback import build_feedback_model, audit_feedback_solution
from run_evidence_case_experiments import save, sha
from run_network_feedback_reference import verify


def equal_export(expected, actual):
    if list(expected.columns) != list(actual.columns) or expected.shape != actual.shape:
        return False
    for col in expected:
        if pd.api.types.is_numeric_dtype(expected[col]):
            if not np.allclose(expected[col], actual[col], rtol=1e-8, atol=1e-7, equal_nan=True):
                return False
        elif expected[col].fillna("").astype(str).tolist() != actual[col].fillna("").astype(str).tolist():
            return False
    return True


def audit_saved(root):
    protocol = verify(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for path, digest in manifest["files"].items():
        if sha(root / path) != digest:
            raise ValueError(f"saved file identity changed: {path}")
    frame = pd.read_csv(root / "hourly.csv")
    checks, controls = [], []
    for case in protocol["cases"]:
        folder = root / "results" / case["name"]
        config = yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8"))
        model, catalog, flags = build_feedback_model(config, frame)
        history = json.loads((folder / "history.json").read_text(encoding="utf-8"))
        for record in history:
            iteration = folder / f"iteration_{record['iteration']:02d}"
            solution = SimpleNamespace(values=np.load(iteration / "solution_values.npy", allow_pickle=False),
                dispatch=pd.read_csv(iteration / "core_dispatch.csv"),
                summary=json.loads((iteration / "summary.json").read_text(encoding="utf-8")))
            rebuilt, audit = audit_feedback_solution(config, model, solution, catalog)
            numerical_checks = {k: v for k, v in audit["checks"].items() if k != "engineering_modes"}
            checks.append({"case": case["name"], "iteration": record["iteration"],
                "checks": {"numerical_and_all_ledgers": all(numerical_checks.values()),
                    "saved_mode_export": equal_export(rebuilt, pd.read_csv(iteration / "mode_dispatch.csv")),
                    "engineering_status": audit["passed"] == (record["status"] == "accepted"),
                    "valid_cut_identity": sorted(audit["invalid_modes"]) == record["added_mode_cuts"]}})
            for mode in record["added_mode_cuts"]:
                for t in model.times:
                    model.problem.add_constraint({flags[mode, t]: 1.}, ub=0.)
        checks.append({"case": case["name"], "iteration": "final", "checks": {
            "full_variable_identity": model.problem.names == json.loads((folder / "variable_names.json").read_text(encoding="utf-8")),
            "final_mode_export": equal_export(rebuilt, pd.read_csv(folder / "mode_dispatch.csv")),
            "final_values_match_accepted_iteration": np.array_equal(solution.values, np.load(folder / "solution_values.npy", allow_pickle=False))}})
        # Controlled corruption stays in memory; retained source artifacts are
        # immutable and these are negative checks, not additional experiments.
        forged = deepcopy(solution)
        index = next(iter(model.variables["source_heat"].values()))
        forged.values[index] += .1
        _, negative = audit_feedback_solution(config, model, forged, catalog)
        controls.append({"case": case["name"], "mutation": "source_vector_plus_0.1kW", "rejected": not negative["passed"]})
        forged_export = rebuilt.copy()
        forged_export.loc[0, "pump_kw"] += .1
        controls.append({"case": case["name"], "mutation": "exported_pump_plus_0.1kW", "rejected": not equal_export(rebuilt, forged_export)})
    return {"passed": all(all(row["checks"].values()) for row in checks) and all(c["rejected"] for c in controls),
        "checks": checks, "check_count": sum(len(row["checks"]) for row in checks),
        "negative_controls": controls, "negative_control_count": len(controls),
        "source_manifest_sha256": sha(root / "manifest.json"),
        "evidence_scope": "saved-vector/export physical and numerical reconstruction; no fresh solver optimality or field validation"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("preserve existing audit")
    result = audit_saved(args.input.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save(args.out, result)
    print(json.dumps({"passed": result["passed"], "check_count": result["check_count"], "negative_controls": result["negative_control_count"]}))
    raise SystemExit(0 if result["passed"] else 1)
