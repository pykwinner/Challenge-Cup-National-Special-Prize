"""Analytical, independently checked energy-constrained CDF-W1 LP cases."""
import numpy as np
import pytest
from scipy.stats import wasserstein_distance

from heatplan.hard_weight_calibration import minimum_w1_energy_weights


def test_analytical_exact_energy_and_zero_distribution_error():
    answer = minimum_w1_energy_weights([1, 1, 1, 3, 3, 3], [[1, 1], [3, 3]], [2, 1], [1, 1])
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([1.5, 1.5])
    assert answer["relative_energy_residual"] == pytest.approx(0)
    assert answer["normalized_W1"] == pytest.approx(0)


def test_optimizes_distribution_among_multiple_exact_energy_solutions():
    observed = [0, 2, 2, 2, 2, 4]
    profiles = [[0, 2], [2, 2], [2, 4]]
    answer = minimum_w1_energy_weights(observed, profiles, [.5, 2, .5], [0, 0, 0])
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([1, 1, 1])
    assert answer["normalized_W1"] == pytest.approx(0)
    assert wasserstein_distance(observed, np.ravel(profiles), v_weights=np.repeat([.5, 2, .5], 2)) > 0


def test_extreme_floor_has_a_real_distribution_cost():
    observed = [0, 2, 2, 2, 2, 4]
    profiles = [[0, 2], [2, 2], [2, 4]]
    answer = minimum_w1_energy_weights(observed, profiles, [.5, 2, .5], [0, 0, 1.5])
    assert answer["status"] == "ok"
    assert answer["weights"] == pytest.approx([1.5, 0, 1.5])
    independent = wasserstein_distance(observed, np.ravel(profiles), v_weights=np.repeat(answer["weights"], 2))
    assert answer["W1_original_units"] == pytest.approx(independent)
    assert independent == pytest.approx(1/3)


def test_hard_energy_failure_is_not_converted_to_soft_matching():
    answer = minimum_w1_energy_weights([1, 1, 1, 1], [[1, 1], [3, 3]], [1, 1], [1, 1])
    assert answer["status"] == "infeasible"
    assert answer["weights"] is None


def test_same_constant_profiles_need_no_cdf_epigraph_intervals():
    answer = minimum_w1_energy_weights([2, 2, 2, 2], [[2, 2], [2, 2]], [1, 1], [.5, .5])
    assert answer["status"] == "ok"
    assert answer["normalized_W1"] == 0
    assert answer["support_intervals"] == 0


def test_zero_reference_energy_is_explicitly_non_evaluable():
    answer = minimum_w1_energy_weights([0, 0], [[0, 0]], [1], [1])
    assert answer["status"] == "zero_energy_undefined"
    assert answer["weights"] is None


def test_inputs_and_weights_are_not_modified():
    observed = np.array([1., 1., 1., 3., 3., 3.])
    profiles, weights = np.array([[1., 1.], [3., 3.]]), np.array([2., 1.])
    originals = [x.copy() for x in (observed, profiles, weights)]
    minimum_w1_energy_weights(observed, profiles, weights, [1, 1])
    assert all(np.array_equal(a, b) for a, b in zip((observed, profiles, weights), originals))


@pytest.mark.parametrize("observed,profiles,weights,lower", [
    ([1, np.nan], [[1, 1]], [1], [0]),
    ([1, 1], [[-1, 1]], [1], [0]),
    ([1, 1, 1], [[1, 1]], [1], [0]),
    ([1, 1], [[1, 1]], [2], [0]),
    ([1, 1], [[1, 1]], [1], [-1]),
])
def test_invalid_observations_or_calendar_weights_rejected(observed, profiles, weights, lower):
    with pytest.raises(ValueError):
        minimum_w1_energy_weights(observed, profiles, weights, lower)
