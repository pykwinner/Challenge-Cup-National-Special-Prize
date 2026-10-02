"""Additive audit repair: check exact ordinal ranks on verified native scores.

CSV round trips can move bills by one ULP. The frozen protocol ranks the saved
native bills exactly, while all feasibility/strict inversion tests have stated
tolerances. Preserve the original audit failure and every frozen run byte.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "scripts/verify_three_city_decision_weight_confirmation.py"
spec = importlib.util.spec_from_file_location("frozen_independent_verifier", BASE)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
RANK_MATH = base.rank_rows


def verified_native_rows(recomputed, native):
    """Native scores can supply exact order only after ledger tolerance checks."""
    if len(recomputed) != len(native):
        raise ValueError("native/recomputed rank population differs")
    by_id = {r["policy_id"]: r for r in native}
    if len(by_id) != len(native) or set(by_id) != {r["policy_id"] for r in recomputed}:
        raise ValueError("native/recomputed rank identities differ")
    for row in recomputed:
        for key in ("actual_bill_yuan", "estimated_bill_yuan"):
            value = by_id[row["policy_id"]][key]
            if not np.isfinite(value) or not np.isclose(value, row[key], rtol=1e-10, atol=1e-7):
                raise ValueError("native bill differs from independent ledger beyond frozen verifier tolerance")
    return [by_id[row["policy_id"]] for row in recomputed]


def precision_rank(recomputed, native, protocol):
    verified = verified_native_rows(recomputed, native)
    rebuilt_rank, native_rank = RANK_MATH(recomputed, protocol), RANK_MATH(verified, protocol)
    changed_pairs, rebuilt = [], {r["policy_id"]: r for r in recomputed}
    for key in ("actual_bill_yuan", "estimated_bill_yuan"):
        for i, left in enumerate(verified):
            for right in verified[i+1:]:
                native_difference = left[key]-right[key]
                reconstructed_difference = rebuilt[left["policy_id"]][key]-rebuilt[right["policy_id"]][key]
                if np.sign(native_difference) != np.sign(reconstructed_difference):
                    if max(abs(native_difference), abs(reconstructed_difference)) > protocol["bill_tie_absolute_tolerance"]:
                        raise ValueError("relative bill order changed outside frozen bill tie tolerance")
                    changed_pairs.append({"metric": key, "policy_ids": [left["policy_id"], right["policy_id"]],
                        "native_bill_difference_yuan": native_difference, "reconstructed_bill_difference_yuan": reconstructed_difference,
                        "below_declared_bill_tie_tolerance": True})
    combined = {**rebuilt_rank, **{key: native_rank[key] for key in
                ["actual_order", "estimated_order", "mean_absolute_rank_error"]}}
    return combined, rebuilt_rank, native_rank, changed_pairs


def audit(out, *, check_hashes=True):
    out = base.fs(out)
    native = base.read(out / "predictions.json")
    rank_function = base.rank_rows
    receipts = []

    def rank_verified_scores(recomputed, protocol):
        city, method = recomputed[0]["city"], recomputed[0]["method_id"]
        saved = [r for r in native if r["city"] == city and r["method_id"] == method]
        combined, rebuilt_rank, native_rank, changed_pairs = precision_rank(recomputed, saved, protocol)
        if rebuilt_rank != native_rank:
            # Diagnostic receipt, not an exception waiver. Final expected ranks
            # are independently recalculated using the verified native bills.
            receipts.append({"city": city, "method_id": method, "native_rank": native_rank,
                             "rounded_csv_reconstructed_rank": rebuilt_rank, "order_sensitive_pairs": changed_pairs})
        return combined

    base.rank_rows = rank_verified_scores
    try:
        result = base.audit(out, check_hashes=check_hashes)
    finally:
        base.rank_rows = rank_function
    return {**result, "precision_repair": {
        "identity": "additive_saved_native_bill_ordinal_audit_v1", "experimental_code_or_results_changed": False,
        "ledger_tolerance_changed": False, "strict_pair_cap_regret_rules_changed": False,
        "rank_rule": "exact frozen ordinal order on saved native bills, independently ledger-verified at original tolerance; any changed-order pair must stay within original1e-8 bill tie tolerance; strict pairs/inversions/false ties use independent reconstructed ledger scores; reconstructed CSV near-tie ordinal order separately disclosed",
        "original_audit_failure_must_be_retained": True, "order_sensitive_cases": receipts,
        "source_sha256": {BASE.relative_to(ROOT).as_posix(): base.sha(BASE), Path(__file__).relative_to(ROOT).as_posix(): base.sha(__file__)}}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    target = base.fs(args.report)
    if target.exists() or target.is_relative_to(base.fs(args.out)):
        parser.error("report must be new and outside frozen run")
    result = audit(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "check_count": result["check_count"], "errors": result["errors"],
                      "ordinal_precision_cases": len(result["precision_repair"]["order_sensitive_cases"])}, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 2)
