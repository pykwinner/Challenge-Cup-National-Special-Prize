"""Approved exploratory loss checks; analytic fixtures are not empirical data."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
import pytest
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("external_rc_followup", ROOT / "scripts/validate_external_rc_followup.py")
followup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(followup)


@pytest.fixture(scope="module")
def observations():
    return pd.read_csv(followup.first.DATA / "observations.csv")


@pytest.fixture(scope="module")
def final_report():
    return json.loads((followup.OUTPUT / "validation_report.json").read_text(encoding="utf-8"))


def test_approval_and_first_round_entire_tree_unchanged():
    approval = followup.verify_approval()
    assert approval["plan_sha256"] == followup.first.digest(followup.PLAN)
    before = json.loads((followup.OUTPUT / "first_round_before.json").read_text(encoding="utf-8"))
    current = followup.snapshot_first_round()
    assert before == current
    record = json.loads((followup.OUTPUT / "first_round_freeze_check.json").read_text(encoding="utf-8"))
    assert record["unchanged"] is True and record["changes"] == []
    assert record["before"] == record["after"] == current


def test_fixed_blocks_cover_each_training_target_once_including_tail():
    blocks = followup.blocks(7192)
    assert len(blocks) == 50
    assert [b[0] for b in blocks] == list(range(0, 7057, 144))
    assert [b[2] - b[1] for b in blocks] == [144] * 49 + [135]
    targets = [row for _, start, stop in blocks for row in range(start, stop)]
    assert targets == list(range(1, 7192))
    with pytest.raises(ValueError):
        followup.blocks(1)


def test_block_interval_means_match_independent_continuous_integrals():
    # Mathematical fixture only; spans two resets and a partial third block.
    params = np.array([.2, 3., 2.])
    dt, initial = 1 / 6, 18.
    equilibria = 22. + 4. * np.sin(np.arange(290) / 19)
    observed = []
    for equilibrium in equilibria:
        trajectory = lambda t: equilibrium + (initial - equilibrium) * np.exp(-params[0] / params[1] * t)
        observed.append(quad(trajectory, 0, dt, epsabs=1e-11)[0] / dt)
        initial = trajectory(dt)
    fixture = pd.DataFrame({"indoor_temperature_mean_c": observed,
        "outdoor_temperature_mean_c": np.zeros(290), "total_room_heat_input_kw": params[0] * equilibria,
        "solar_reference_kw_m2": np.zeros(290)})
    predicted = followup.block_predictions(fixture, params)
    assert np.isnan(predicted[0])
    np.testing.assert_allclose(predicted[1:], observed[1:], rtol=0, atol=2e-11)


def test_block_forecasts_read_only_anchors_not_future_temperatures(observations):
    sample = observations.head(300).copy()
    params = np.array([.14, 5.8, 1.8])
    expected = followup.block_predictions(sample, params)
    anchors = [b[0] for b in followup.blocks(len(sample))]
    sample.loc[~sample.index.isin(anchors), "indoor_temperature_mean_c"] = 1e9
    np.testing.assert_array_equal(followup.block_predictions(sample, params), expected)
    sample = observations.head(300).copy()
    sample.loc[144, "indoor_temperature_mean_c"] += 10
    actual = followup.block_predictions(sample, params)
    np.testing.assert_array_equal(actual[:145], expected[:145])
    assert np.all(actual[145:289] > expected[145:289])
    np.testing.assert_array_equal(actual[289:], expected[289:])


def test_training_residuals_ignore_all_holdout_fields(observations):
    params = np.array([.14, 5.8, 1.8])
    expected = followup.training_residuals(observations, params, 7192)
    changed = observations.copy()
    changed.loc[7192:, ["indoor_temperature_mean_c", "outdoor_temperature_mean_c",
                       "total_room_heat_input_kw", "solar_reference_kw_m2"]] = 1e9
    np.testing.assert_array_equal(followup.training_residuals(changed, params, 7192), expected)
    with pytest.raises(ValueError):
        followup.training_residuals(observations, params, len(observations) + 1)


def test_solver_uses_four_fixed_starts_and_training_only_selection(monkeypatch, observations):
    calls = []
    def fake_solver(residuals, start, **kwargs):
        # Evaluate a fixed candidate without performing any extra empirical fit.
        assert kwargs["x_scale"] == "jac" and kwargs["max_nfev"] == 2000
        assert kwargs["ftol"] == kwargs["xtol"] == kwargs["gtol"] == 1e-11
        np.testing.assert_array_equal(kwargs["bounds"][0], followup.first.LOWER)
        np.testing.assert_array_equal(kwargs["bounds"][1], followup.first.UPPER)
        calls.append(list(start))
        candidate = np.array(start)
        return SimpleNamespace(x=candidate, fun=residuals(candidate), jac=np.eye(3),
                               success=True, status=1, message="fixed test stub", nfev=1)
    monkeypatch.setattr(followup, "least_squares", fake_solver)
    sample = observations.head(500).copy()
    expected, result = followup.fit_training_only(sample, 300)
    assert calls == followup.STARTS
    winner = min(range(4), key=lambda i: result["all_starts"][i]["training_24h_block_sse_c2"])
    assert result["selected_start_index_by_training_sse_only"] == winner
    np.testing.assert_array_equal(expected, followup.STARTS[winner])
    calls.clear()
    sample.loc[300:, ["indoor_temperature_mean_c", "outdoor_temperature_mean_c",
                       "total_room_heat_input_kw", "solar_reference_kw_m2"]] = 1e9
    actual, after = followup.fit_training_only(sample, 300)
    np.testing.assert_array_equal(actual, expected)
    assert after["all_starts"] == result["all_starts"]
    assert calls == followup.STARTS


def test_all_four_failures_retained_without_replacement_fit(monkeypatch, observations):
    calls = []
    def failing_solver(residuals, start, **kwargs):
        calls.append(list(start))
        raise RuntimeError("planned test failure")
    monkeypatch.setattr(followup, "least_squares", failing_solver)
    parameters, metadata = followup.fit_training_only(observations.head(200), 150)
    assert parameters is None and calls == followup.STARTS
    assert metadata["status"] == "all_four_starts_failed_no_replacement_fit"
    assert len(metadata["all_starts"]) == 4
    assert all(not trial["success"] and "planned test failure" in trial["message"]
               for trial in metadata["all_starts"])


def test_selected_training_objective_recomputed_from_saved_residuals(final_report):
    trajectory = pd.read_csv(followup.OUTPUT / "predictions_and_residuals.csv")
    fitting = final_report["fit"]
    residuals = trajectory["training_24h_block_residual_c"].dropna().to_numpy()
    assert len(residuals) == 7191
    assert trajectory.loc[7192:, "training_24h_block_c"].isna().all()
    assert fitting["training_24h_block_sse_c2"] == pytest.approx(np.dot(residuals, residuals), rel=1e-9, abs=1e-8)
    assert fitting["training_24h_block_rmse_c"] == pytest.approx(np.sqrt(np.mean(residuals ** 2)), abs=1e-9)
    starts = fitting["all_starts"]
    assert [trial["initial_parameters"] for trial in starts] == followup.STARTS
    eligible = [i for i, trial in enumerate(starts) if trial["usable_for_selection"]]
    assert fitting["selected_start_index_by_training_sse_only"] == min(
        eligible, key=lambda i: starts[i]["training_24h_block_sse_c2"])
    assert [block["targets"] for block in fitting["blocks"]] == [144] * 49 + [135]


def test_every_metric_and_acf_recomputed_from_all_predictions(final_report):
    trajectory = pd.read_csv(followup.OUTPUT / "predictions_and_residuals.csv")
    assert len(trajectory) == 11988
    count = 0
    for split in ("train", "validation", "test"):
        subset = trajectory.loc[trajectory["split"] == split]
        assert set(final_report["metrics"][split]) == set(followup.MODES)
        for mode, scores in final_report["metrics"][split].items():
            errors = (subset[mode + "_c"] - subset["indoor_temperature_mean_c"]).to_numpy()
            np.testing.assert_allclose(subset[mode + "_residual_c"], errors, atol=1e-9, rtol=0)
            assert scores["n"] == len(errors)
            assert scores["mae_c"] == pytest.approx(np.abs(errors).mean(), abs=1e-9)
            assert scores["rmse_c"] == pytest.approx(np.sqrt(np.mean(errors ** 2)), abs=1e-9)
            assert scores["bias_predicted_minus_observed_c"] == pytest.approx(errors.mean(), abs=1e-9)
            assert scores["max_abs_error_c"] == pytest.approx(np.abs(errors).max(), abs=1e-9)
            centered = errors - errors.mean()
            for lag in (1, 6, 24, 144):
                acf = np.dot(centered[:-lag], centered[lag:]) / np.dot(centered, centered)
                assert scores["residual_acf"][str(lag)] == pytest.approx(acf, abs=2e-8)
            count += 1
    assert count == 18


def test_comparison_keeps_all_metrics_and_unchanged_persistence_baselines(final_report):
    original = json.loads((followup.FIRST / "validation_report.json").read_text(encoding="utf-8"))
    comparison = pd.read_csv(followup.OUTPUT / "first_vs_followup_metrics.csv")
    assert len(comparison) == len(final_report["first_vs_followup"]) == 18
    for _, row in comparison.iterrows():
        previous = original["metrics"][row["split"]][row["mode"]]
        current = final_report["metrics"][row["split"]][row["mode"]]
        for metric in ("mae_c", "rmse_c", "bias_predicted_minus_observed_c", "max_abs_error_c"):
            assert row["first_" + metric] == pytest.approx(previous[metric], abs=1e-9)
            assert row["followup_" + metric] == pytest.approx(current[metric], abs=1e-9)
            assert row["delta_" + metric] == pytest.approx(current[metric] - previous[metric], abs=1e-9)
            if row["mode"].startswith("persistence"):
                assert abs(row["delta_" + metric]) < 1e-9


def test_exploratory_scope_and_fixed_model_are_explicit(final_report):
    assert final_report["exploratory_after_first_test_inspection"] is True
    assert final_report["fresh_independent_confirmation"] is False
    assert final_report["no_Wuhan_parameter_transfer"] is True
    assert final_report["no_engineering_MILP"] is True
    assert final_report["one_loss_four_starts_only"] is True
    assert final_report["licence"] == "CC BY-SA 4.0"
    assert final_report["dataset_doi"] == "10.24406/fordatis/76.2"
    assert final_report["splits"] == json.loads((followup.FIRST / "validation_report.json").read_text(encoding="utf-8"))["splits"]
    for result, low, high in zip(final_report["fit"]["parameters"].values(), followup.first.LOWER, followup.first.UPPER):
        assert result["lower_bound"] == low and result["upper_bound"] == high
        assert low <= result["estimate"] <= high


def test_existing_output_or_file_is_never_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "existing.json"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        followup.write_json_new(target, {"replacement": True})
    assert target.read_text(encoding="utf-8") == "original"
    monkeypatch.setattr(followup, "fit_training_only", lambda *args: pytest.fail("must not fit into existing output"))
    with pytest.raises(FileExistsError):
        followup.run()


def test_final_manifest_hashes_and_approved_identities():
    manifest = json.loads((followup.OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True and manifest["first_round_unchanged"] is True
    assert manifest["exploratory_after_first_test_inspection"] is True
    assert manifest["fresh_independent_confirmation"] is False
    assert manifest["plan_sha256"] == followup.first.digest(followup.PLAN)
    assert manifest["approval_sha256"] == followup.first.digest(followup.APPROVAL)
    assert manifest["first_manifest_sha256"] == followup.first.digest(followup.FIRST / "manifest.json")
    for category, base in (("inputs", ROOT), ("outputs", followup.OUTPUT)):
        for item in manifest[category]:
            assert followup.first.digest(base / item["file"]) == item["sha256"]
