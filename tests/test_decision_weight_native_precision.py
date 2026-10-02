"""Only the additive precision audit; frozen producer and outputs are unchanged."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("precision_audit", ROOT/"scripts/verify_three_city_decision_weight_native_precision.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_one_ulp_round_trip_preserves_verified_native_ordinal_contract():
    saved = [{"policy_id": i, "actual_bill_yuan": 5000.+i, "estimated_bill_yuan": 5000.} for i in range(2)]
    recomputed = [dict(row) for row in saved]
    recomputed[0]["estimated_bill_yuan"] += 9.094947017729282e-13
    accepted = module.verified_native_rows(recomputed, saved)
    assert accepted == saved
    protocol = {"bill_tie_absolute_tolerance": 1e-8}
    assert module.base.rank_rows(accepted, protocol)["estimated_order"] == [0, 1]
    assert module.base.rank_rows(recomputed, protocol)["estimated_order"] == [1, 0]
    assert module.base.rank_rows(accepted, protocol)["false_estimated_ties"] == 1


def test_real_native_bill_error_cannot_be_disguised_as_rank_precision():
    saved = [{"policy_id": 0, "actual_bill_yuan": 5000., "estimated_bill_yuan": 5000.}]
    recomputed = [{**saved[0], "estimated_bill_yuan": 5000.001}]
    with pytest.raises(ValueError, match="beyond"):
        module.verified_native_rows(recomputed, saved)


def test_duplicate_or_missing_native_scores_fail_closed():
    row = {"policy_id": 0, "actual_bill_yuan": 1., "estimated_bill_yuan": 1.}
    with pytest.raises(ValueError, match="identities"):
        module.verified_native_rows([row, row], [row, row])
    with pytest.raises(ValueError, match="population"):
        module.verified_native_rows([row], [])


def test_nontrivial_order_change_fails_even_within_ledger_numeric_allowance():
    saved = [{"policy_id": 0, "actual_bill_yuan": 5000., "estimated_bill_yuan": 5000.},
             {"policy_id": 1, "actual_bill_yuan": 5001., "estimated_bill_yuan": 5000.+2e-8}]
    recomputed = [dict(row) for row in saved]
    recomputed[0]["estimated_bill_yuan"] += 5e-8
    with pytest.raises(ValueError, match="order changed"):
        module.precision_rank(recomputed, saved, {"bill_tie_absolute_tolerance": 1e-8})


def test_precision_receipt_retains_strict_metrics_from_recomputed_scores():
    saved = [{"policy_id": i, "actual_bill_yuan": 5000.+i, "estimated_bill_yuan": 5000.} for i in range(2)]
    recomputed = [dict(row) for row in saved]
    recomputed[0]["estimated_bill_yuan"] += 9.094947017729282e-13
    combined, rebuilt, native, pairs = module.precision_rank(recomputed, saved, {"bill_tie_absolute_tolerance": 1e-8})
    assert combined["estimated_order"] == native["estimated_order"] == [0, 1]
    assert pairs[0]["below_declared_bill_tie_tolerance"]
    for key in ["strict_actual_pairs", "strict_inversions", "false_estimated_ties"]:
        assert combined[key] == rebuilt[key]
