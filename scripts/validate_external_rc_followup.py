"""One approved exploratory fit with a fixed 24-hour block RC loss.

The first test set was inspected before this plan; this is not fresh confirmation.
Reads the first experiment only; creates a new output directory exclusively.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
import scipy
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / "outputs/competition_20261001/C1_external"
OUTPUT = ROOT / "outputs/competition_20261001/C1_external_followup"
PLAN = ROOT / "docs/execution_20261001/C1_external_followup_plan.md"
APPROVAL = ROOT / "outputs/competition_20261001/C0/external_followup_approval_01.json"
BLOCK_LENGTH = 144
STARTS = [[.1, 2., 3.], [.1, 10., 3.], [.1, 30., 7.], [.3, 10., 7.]]
MODES = ("rc_one_step", "persistence_one_step", "rc_continuous_open_loop",
         "persistence_continuous_open_loop", "rc_segment_open_loop", "persistence_segment_open_loop")
spec = importlib.util.spec_from_file_location("external_rc_first_read_only", ROOT / "scripts/validate_external_rc_benchmark.py")
first = importlib.util.module_from_spec(spec)
spec.loader.exec_module(first)


def write_json_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def write_csv_new(path: Path, frame: pd.DataFrame) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        frame.to_csv(handle, index=False, float_format="%.12g")


def verify_approval() -> dict:
    approval = json.loads(APPROVAL.read_text(encoding="utf-8"))
    if (approval["approved_by"] != "C0" or approval["plan"] != PLAN.relative_to(ROOT).as_posix()
            or first.digest(PLAN) != approval["plan_sha256"]
            or first.digest(FIRST / "manifest.json") != approval["first_manifest_sha256"]
            or not all(approval[key] is True for key in ("first_results_read_only",
                "exploratory_after_first_test_inspection", "no_further_loss_model_or_start_search",
                "report_all_metrics_even_if_worse"))):
        raise ValueError("approval identity or fixed exploratory restrictions mismatch")
    manifest = json.loads((FIRST / "manifest.json").read_text(encoding="utf-8"))
    if manifest["complete"] is not True:
        raise ValueError("first-round manifest incomplete")
    for category, base in (("inputs", ROOT), ("outputs", FIRST)):
        for item in manifest[category]:
            source = base / item["file"]
            if category == "inputs" and item["file"] in {
                    "scripts/validate_external_rc_benchmark.py", "tests/test_external_rc_benchmark.py"}:
                source = FIRST / "code_version" / item["file"]
            if first.digest(source) != item["sha256"]:
                raise ValueError(f"frozen first-round identity mismatch: {item['file']}")
    verification = json.loads((FIRST / "final_verification.json").read_text(encoding="utf-8"))
    for item in verification["supplemental_identities"]:
        if first.digest(ROOT / item["file"]) != item["sha256"]:
            raise ValueError(f"frozen supplemental identity mismatch: {item['file']}")
    return approval


def snapshot_first_round() -> dict[str, str]:
    files = {p for directory in (first.DATA, FIRST) for p in directory.rglob("*") if p.is_file()}
    files.update((ROOT / "scripts/validate_external_rc_benchmark.py",
                  ROOT / "tests/test_external_rc_benchmark.py",
                  ROOT / "docs/execution_20261001/C1_external_validation.md", PLAN, APPROVAL))
    return {p.relative_to(ROOT).as_posix(): first.digest(p) for p in sorted(files)}


def compare_snapshots(before: dict[str, str], after: dict[str, str]) -> dict:
    changes = [{"file": name, "before_sha256": before.get(name), "after_sha256": after.get(name)}
               for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    return {"unchanged": not changes, "files_checked": len(before), "changes": changes,
            "before": before, "after": after}


def blocks(train_stop: int) -> list[tuple[int, int, int]]:
    if train_stop < 2:
        raise ValueError("training requires an anchor and a target")
    return [(anchor, anchor + 1, min(anchor + BLOCK_LENGTH + 1, train_stop))
            for anchor in range(0, train_stop - 1, BLOCK_LENGTH)]


def block_predictions(training: pd.DataFrame, parameters: np.ndarray) -> np.ndarray:
    """Only each fixed anchor's temperature is read into its block forecast."""
    a, f = first.coefficients(parameters)
    equilibrium = first.equilibrium(training, parameters)
    predicted = np.full(len(training), np.nan)
    for anchor, start, stop in blocks(len(training)):
        state = equilibrium[anchor] + a / f * (
            float(training["indoor_temperature_mean_c"].iloc[anchor]) - equilibrium[anchor])
        for row in range(start, stop):
            predicted[row] = equilibrium[row] + f * (state - equilibrium[row])
            state = equilibrium[row] + a * (state - equilibrium[row])
    return predicted


def training_residuals(frame: pd.DataFrame, parameters: np.ndarray, train_stop: int) -> np.ndarray:
    if not 2 <= train_stop <= len(frame):
        raise ValueError("invalid training boundary")
    training = frame.iloc[:train_stop].copy()
    return block_predictions(training, parameters)[1:] - training["indoor_temperature_mean_c"].to_numpy()[1:]


def fit_training_only(frame: pd.DataFrame, train_stop: int) -> tuple[np.ndarray | None, dict]:
    if not 2 <= train_stop <= len(frame):
        raise ValueError("invalid training boundary")
    training = frame.iloc[:train_stop].copy()
    target = training["indoor_temperature_mean_c"].to_numpy()[1:]
    residuals = lambda p: block_predictions(training, p)[1:] - target
    runs, solutions = [], []
    for start in STARTS:
        try:
            result = least_squares(residuals, start, bounds=(first.LOWER, first.UPPER), x_scale="jac",
                                   ftol=1e-11, xtol=1e-11, gtol=1e-11, max_nfev=2000)
            score = float(np.dot(result.fun, result.fun))
            usable = bool(result.success and np.isfinite(result.x).all() and np.isfinite(score))
            solutions.append(result)
            runs.append({"initial_parameters": list(start), "final_parameters": result.x.tolist(),
                         "success": bool(result.success), "usable_for_selection": usable,
                         "status": int(result.status), "message": str(result.message),
                         "nfev": int(result.nfev), "training_24h_block_sse_c2": score if np.isfinite(score) else None})
        except Exception as exc:
            solutions.append(None)
            runs.append({"initial_parameters": list(start), "final_parameters": None, "success": False,
                         "usable_for_selection": False, "status": None,
                         "message": f"{type(exc).__name__}: {exc}", "nfev": None,
                         "training_24h_block_sse_c2": None})
    metadata = {"loss": "fixed_144_target_block_interval_mean_training_SSE",
                "training_rows": train_stop, "training_targets": train_stop - 1,
                "block_length_targets": BLOCK_LENGTH,
                "blocks": [{"anchor_row": a, "target_start_inclusive": b, "target_stop_exclusive": c,
                            "targets": c - b} for a, b, c in blocks(train_stop)], "all_starts": runs}
    successful = [i for i, run in enumerate(runs) if run["usable_for_selection"]]
    if not successful:
        return None, {**metadata, "status": "all_four_starts_failed_no_replacement_fit"}
    winner = min(successful, key=lambda i: runs[i]["training_24h_block_sse_c2"])
    chosen = solutions[winner]
    recomputed = residuals(chosen.x)
    if not np.allclose(recomputed, chosen.fun, rtol=1e-12, atol=1e-12):
        raise ValueError("selected training residuals do not reproduce solver objective")
    score = float(np.dot(recomputed, recomputed))
    condition = float(np.linalg.cond(chosen.jac))
    parameters = {name: {"estimate": float(value), "lower_bound": float(lo), "upper_bound": float(hi),
                         "near_lower_bound": bool(value - lo <= .001 * (hi - lo)),
                         "near_upper_bound": bool(hi - value <= .001 * (hi - lo))}
                  for name, value, lo, hi in zip(("H_kw_per_c", "C_kwh_per_c", "effective_solar_area_m2"),
                                                chosen.x, first.LOWER, first.UPPER)}
    return chosen.x, {**metadata, "status": "selected_by_training_block_sse_only", "parameters": parameters,
                      "selected_start_index_by_training_sse_only": winner, "training_24h_block_sse_c2": score,
                      "training_24h_block_rmse_c": float(np.sqrt(score / len(recomputed))),
                      "thermal_time_constant_hours": float(chosen.x[1] / chosen.x[0]),
                      "jacobian_condition_number": condition if np.isfinite(condition) else None,
                      "warning": "Equivalent parameters under an exploratory changed loss, not certified physical values."}


def evaluate(frame: pd.DataFrame, parameters: np.ndarray) -> tuple[pd.DataFrame, dict, dict]:
    ranges = first.split_ranges(len(frame))
    observed = frame["indoor_temperature_mean_c"].to_numpy()
    predictions = {"rc_one_step": first.one_step(frame, parameters),
                   "persistence_one_step": np.r_[np.nan, observed[:-1]],
                   "rc_continuous_open_loop": first.open_loop(frame, parameters),
                   "persistence_continuous_open_loop": np.r_[np.nan, np.full(len(frame) - 1, observed[0])],
                   "rc_segment_open_loop": np.full(len(frame), np.nan),
                   "persistence_segment_open_loop": np.full(len(frame), np.nan)}
    split_labels = np.full(len(frame), "initialization", dtype=object)
    scores, split_identity = {}, {}
    for split, (start, stop) in ranges.items():
        predictions["rc_segment_open_loop"][start:stop] = first.open_loop(frame, parameters, start, stop)[start:stop]
        predictions["persistence_segment_open_loop"][start:stop] = observed[start - 1]
        split_labels[start:stop] = split
        scores[split] = {mode: first.metrics(observed[start:stop], predictions[mode][start:stop]) for mode in MODES}
        split_identity[split] = {"target_row_start_inclusive": start, "target_row_stop_exclusive": stop,
                                "first_utc": frame["time_utc"].iloc[start], "last_utc": frame["time_utc"].iloc[stop - 1]}
    trajectory = frame.copy()
    trajectory["split"] = split_labels
    for mode, values in predictions.items():
        trajectory[mode + "_c"] = values
        trajectory[mode + "_residual_c"] = values - observed
    train_stop = ranges["train"][1]
    trajectory["training_24h_block_c"] = np.r_[block_predictions(frame.iloc[:train_stop], parameters),
                                              np.full(len(frame) - train_stop, np.nan)]
    trajectory["training_24h_block_residual_c"] = trajectory["training_24h_block_c"] - observed
    return trajectory, scores, split_identity


def comparisons(original: dict, scores: dict) -> list[dict]:
    rows = []
    for split in ("train", "validation", "test"):
        for mode in MODES:
            old, new = original["metrics"][split][mode], scores[split][mode]
            row = {"split": split, "mode": mode, "n": new["n"]}
            for metric in ("mae_c", "rmse_c", "bias_predicted_minus_observed_c", "max_abs_error_c"):
                row.update({"first_" + metric: old[metric], "followup_" + metric: new[metric],
                            "delta_" + metric: new[metric] - old[metric]})
            for lag in ("1", "6", "24", "144"):
                row.update({"first_acf_" + lag: old["residual_acf"][lag],
                            "followup_acf_" + lag: new["residual_acf"][lag],
                            "delta_acf_" + lag: new["residual_acf"][lag] - old["residual_acf"][lag]})
            rows.append(row)
    return rows


def save_plot_new(path: Path, trajectory: pd.DataFrame, original: dict, scores: dict) -> None:
    os.environ["MPLCONFIGDIR"] = str(path.parent / "matplotlib_runtime")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    old = pd.read_csv(FIRST / "predictions_and_residuals.csv")
    time = pd.to_datetime(trajectory["time_utc"], utc=True)
    figure = plt.figure(figsize=(14, 9), constrained_layout=True)
    grid = figure.add_gridspec(2, 2)
    top = figure.add_subplot(grid[0, :])
    for values, color, label, width in (
        (trajectory["indoor_temperature_mean_c"], "#172B4D", "Published measured room aggregate", 1.),
        (old["rc_continuous_open_loop_c"], "#AAAAAA", "First fit: continuous open loop", .7),
        (trajectory["rc_continuous_open_loop_c"], "#D95F02", "Exploratory 24h-loss fit: continuous open loop", 1.),
        (trajectory["persistence_continuous_open_loop_c"], "#4477AA", "Persistence continuous open loop", .8)):
        top.plot(time, values, color=color, linewidth=width, label=label)
    for split in ("validation", "test"):
        top.axvline(time.iloc[first.split_ranges(len(trajectory))[split][0]], color="#777777", linestyle="--", linewidth=.8)
    top.set(title="Exploratory loss comparison after first test inspection; no fresh confirmation",
            xlabel="UTC labels: all 11988 published N2 intervals", ylabel="Interval-mean temperature (deg C)")
    top.legend(fontsize=8, loc="lower left")
    labels = ["RC\n1-step", "Persistence\n1-step", "RC\ncontinuous", "Persistence\ncontinuous", "RC\nsegment", "Persistence\nsegment"]
    for column, split in enumerate(("validation", "test")):
        axis = figure.add_subplot(grid[1, column])
        x = np.arange(len(MODES))
        axis.bar(x - .2, [original["metrics"][split][mode]["rmse_c"] for mode in MODES], width=.4,
                 label="Frozen first fit", color="#AAAAAA")
        axis.bar(x + .2, [scores[split][mode]["rmse_c"] for mode in MODES], width=.4,
                 label="Exploratory 24h loss", color="#D95F02")
        axis.set_xticks(x, labels, fontsize=8)
        axis.set(title=split + " (previously inspected)", ylabel="RMSE (deg C)")
        axis.legend(fontsize=8)
        axis.grid(axis="y", alpha=.2)
    with path.open("xb") as handle:
        figure.savefig(handle, format="png", dpi=160)
    plt.close(figure)


def run(output: Path = OUTPUT) -> dict:
    output = output.resolve()
    if output != OUTPUT.resolve():
        raise ValueError("only the approved new output directory is authorized")
    approval = verify_approval()
    before = snapshot_first_round()
    output.mkdir(parents=True, exist_ok=False)
    write_json_new(output / "first_round_before.json", before)
    observations = pd.read_csv(first.DATA / "observations.csv")
    original = json.loads((FIRST / "validation_report.json").read_text(encoding="utf-8"))
    plan = json.loads((first.DATA / "analysis_plan.json").read_text(encoding="utf-8"))
    if len(observations) != 11988 or first.split_ranges(len(observations))["train"][1] != 7192:
        raise ValueError("approved measurement extent or split mismatch")
    parameters, fitting = fit_training_only(observations, 7192)
    write_json_new(output / "fit_result.json", fitting)
    if parameters is None:
        freeze = compare_snapshots(before, snapshot_first_round())
        write_json_new(output / "first_round_freeze_check.json", freeze)
        write_json_new(output / "manifest.json", {"complete": False, "status": fitting["status"],
            "exploratory_after_first_test_inspection": True, "first_round_unchanged": freeze["unchanged"],
            "no_replacement_fit": True, "outputs": [{"file": p.name, "sha256": first.digest(p)}
                for p in sorted(output.iterdir()) if p.is_file()]})
        raise RuntimeError("all four starts failed; failures retained and no replacement fit performed")
    trajectory, scores, split_identity = evaluate(observations, parameters)
    comparison = comparisons(original, scores)
    write_csv_new(output / "predictions_and_residuals.csv", trajectory)
    write_csv_new(output / "first_vs_followup_metrics.csv", pd.DataFrame(comparison))
    save_plot_new(output / "exploratory_validation.png", trajectory, original, scores)
    report = {"status": "exploratory_fixed_24h_loss_after_first_test_inspection",
              "exploratory_after_first_test_inspection": True, "fresh_independent_confirmation": False,
              "approval": approval, "dataset_doi": original["dataset_doi"], "licence": original["licence"],
              "attribution": "Kersken, Matthias; Strachan, Paul (2020), Fraunhofer IBP / Fordatis, DOI 10.24406/fordatis/76.2, CC BY-SA 4.0.",
              "derived_changes": "Same published processed measurements and transformations; changed training loss only, new equivalent parameters and predictions.",
              "data_quality": original["data_quality"], "fit": fitting, "splits": split_identity,
              "metrics": scores, "first_vs_followup": comparison,
              "predictions_definition": plan["predictions"], "source_and_model_limitations": plan["source_limitations"],
              "temperature_sampling": original["temperature_sampling"],
              "interpretation": "Previously inspected validation/test reused for exploratory comparison. All improvements and deteriorations retained; no new independent accuracy certificate.",
              "no_Wuhan_parameter_transfer": True, "no_random_split": True, "no_engineering_MILP": True,
              "one_loss_four_starts_only": True,
              "executed_at_Asia_Shanghai": datetime.now(timezone(timedelta(hours=8))).isoformat(),
              "runtime": {"python": sys.version, "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__}}
    write_json_new(output / "validation_report.json", report)
    freeze = compare_snapshots(before, snapshot_first_round())
    write_json_new(output / "first_round_freeze_check.json", freeze)
    if not freeze["unchanged"]:
        raise RuntimeError("first-round file change detected; stopped without restoring or overwriting it")
    inputs = dict(before)
    for path in (Path(__file__), ROOT / "tests/test_external_rc_followup.py"):
        inputs[path.relative_to(ROOT).as_posix()] = first.digest(path)
    summary = {"parameters": parameters.tolist(), "selected_start": fitting["selected_start_index_by_training_sse_only"],
               "training_24h_block_sse_c2": fitting["training_24h_block_sse_c2"],
               "continuous_open_loop_rmse_c": {split: scores[split]["rc_continuous_open_loop"]["rmse_c"] for split in scores},
               "first_round_unchanged": True, "output": str(output), "exploratory_after_first_test_inspection": True}
    write_json_new(output / "run_summary.json", summary)
    manifest = {"complete": True, "status": report["status"], "exploratory_after_first_test_inspection": True,
                "fresh_independent_confirmation": False, "first_round_unchanged": True,
                "plan_sha256": approval["plan_sha256"], "approval_sha256": first.digest(APPROVAL),
                "first_manifest_sha256": approval["first_manifest_sha256"],
                "inputs": [{"file": name, "sha256": digest} for name, digest in sorted(inputs.items())],
                "outputs": [{"file": p.name, "sha256": first.digest(p)} for p in sorted(output.iterdir()) if p.is_file()],
                "licence": "CC BY-SA 4.0 for selected and derived measurement data",
                "supplemental_records": "Tests, handoff and final closure created after this immutable model manifest.",
                "certificate_claim": "Exploratory comparison only; no Wuhan calibration or fresh independent validation."}
    write_json_new(output / "manifest.json", manifest)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=True, indent=2))
