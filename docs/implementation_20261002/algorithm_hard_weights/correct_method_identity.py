"""Recomputable label-only erratum; preserve all frozen results and locks."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import pandas as pd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    records = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    candidates = {(r["case_id"], r["method"]): r for r in records}
    rows = []
    for decision in json.loads((OUT / "adoption_records.json").read_text(encoding="utf-8")):
        adopted = decision["statistical_candidate_adopted"]
        correct = decision["candidate_method"] if adopted else "extreme_aware_fallback"
        actual = candidates[(decision["case_id"], decision["candidate_method"] if adopted else "extreme_aware")]
        assert decision["selected_weights"] == actual["selection"]["weights"], "Identity correction changed weights"
        path = OUT / "cases" / decision["case_id"] / "2017" / f"{decision['method']}_result.json"
        rows.append({"case_id": decision["case_id"], "year": 2017,
            "adoption_method": decision["method"], "candidate_method": decision["candidate_method"],
            "statistical_candidate_adopted": adopted,
            "old_selected_method": decision["selected_method"], "corrected_selected_method": correct,
            "needs_correction": decision["selected_method"] != correct,
            "original_result_sha256": sha(path), "selected_weights_unchanged_verified": True})
    pd.DataFrame(rows).to_csv(OUT / "method_identity_erratum.csv", index=False)
    result = {"scope": "label_only_erratum_no_weights_metrics_sources_or_frozen_locks_modified",
        "affected_records": sum(r["needs_correction"] for r in rows),
        "machine_consumption": "candidate_method if statistical_candidate_adopted else extreme_aware_fallback",
        "frozen_gate_sha256": sha(OUT / "version/representative_adoption.py"),
        "corrected_future_API_sha256": sha(ROOT / "src/heatplan/representative_adoption.py"),
        "future_API_not_covered_by_original_experiment_lock": True,
        "compatibility_tests_sha256": sha(ROOT / "tests/test_weight_adoption_method_identity.py"),
        "correction_script_sha256": sha(Path(__file__)), "records": rows}
    (OUT / "method_identity_erratum.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "records"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
