"""API label compatibility after the frozen hard-weight experiment erratum."""
import pytest
from heatplan.representative_adoption import assess_weight_candidate


def decision(**kwargs):
    return assess_weight_candidate([[1, 3]], [8], [3, 1], [2, 2], [1, 1],
        baseline_w1=.1, candidate_w1=.09, peak_retention_ratio=1,
        ramp_retention_ratio=1, original_observed_peak_retention_ratio=1, **kwargs)


def test_default_soft_candidate_label_remains_compatible():
    result = decision()
    assert result["candidate_method"] == result["selected_method"] == "extreme_calibrated"


def test_explicit_hard_candidate_identity_is_returned_when_adopted():
    result = decision(candidate_method="hard_energy_minimum_W1")
    assert result["candidate_method"] == result["selected_method"] == "hard_energy_minimum_W1"
    assert result["selected_weights"] == [2, 2]


def test_rejected_hard_candidate_keeps_identity_and_explicit_fallback():
    result = decision(candidate_method="hard_energy_minimum_W1", candidate_solver_success=False)
    assert result["candidate_method"] == "hard_energy_minimum_W1"
    assert result["selected_method"] == "extreme_aware_fallback"
    assert result["selected_weights"] == [3, 1]


@pytest.mark.parametrize("method", ["", "   ", None, False])
def test_invalid_method_identifier_is_rejected(method):
    with pytest.raises(ValueError, match="candidate_method"):
        decision(candidate_method=method)
