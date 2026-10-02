"""Independent 1R1C applicability test on published Fraunhofer N2 measurements.

Offline after the original files have been downloaded. No heatplan core imports,
synthetic benchmark generation, MILP, Wuhan parameter changes or random splits.
"""
from __future__ import annotations

import argparse
from datetime import timedelta, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile
import zlib

import numpy as np
import pandas as pd
import scipy
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/external_rc_benchmark_20261001"
EVIDENCE = DATA / "evidence"
OUTPUT = ROOT / "outputs/competition_20261001/C1_external"
HOUSE_FILE = "Twin_house_N2_exp1_full1_10min.xlsx"
WEATHER_FILE = "Twin_house_weather_exp1_10min_compensated.xlsx"
ROOM_AREAS = {"bath": 6.92, "bed": 11.19, "child1": 34.60, "child2": 36.56,
              "corridor": 5.46, "dining": 11.19, "doorway": 5.84,
              "kitchen": 7.44, "living": 33.65, "stairs": 12.90}
SOLAR_COLUMNS = ["Radiation_Global", "Radiation_South", "Radiation_West", "Radiation_East", "Radiation_North"]
LOWER = np.array([.02, .1, 0.])
UPPER = np.array([1., 100., 16.77 * .62])
DT = 1 / 6


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def verified_members(evidence: Path = EVIDENCE) -> list[dict]:
    """Read only named members, CRC-check them, never unpack archive paths."""
    log = json.loads((evidence / "downloads.json").read_text(encoding="utf-8"))
    archive = evidence / "annex71_main_experiment.zip"
    expected = next(r["sha256"] for r in log if r["file"] == archive.name and r["status"] == "downloaded")
    if digest(archive) != expected:
        raise ValueError("original measurement ZIP SHA256 differs from download record")
    records = []
    with zipfile.ZipFile(archive) as bundle, archive.open("rb") as raw:
        for name in (HOUSE_FILE, WEATHER_FILE):
            info = next(i for i in bundle.infolist() if Path(i.filename).name == name)
            target = evidence / name
            if not target.exists():
                if info.compress_type == 9:
                    from inflate64 import Inflater
                    raw.seek(info.header_offset)
                    header = raw.read(30)
                    if header[:4] != b"PK\x03\x04":
                        raise ValueError("invalid ZIP member header")
                    n, e = struct.unpack_from("<HH", header, 26)
                    raw.read(n + e)
                    content = Inflater().inflate(raw.read(info.compress_size))
                else:
                    content = bundle.read(info)
                target.write_bytes(content)
            content = target.read_bytes()
            if len(content) != info.file_size or zlib.crc32(content) != info.CRC:
                raise ValueError(f"original archive member CRC/size mismatch: {name}")
            records.append({"archive_member": info.filename, "file": name, "bytes": len(content),
                            "crc32": info.CRC, "sha256": digest(target)})
    return records


def prepare_observations(house: pd.DataFrame, weather: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    for label, frame in (("house", house), ("weather", weather)):
        if "DATE" not in frame or frame["DATE"].isna().any():
            raise ValueError(f"{label} DATE missing")
        time = pd.DatetimeIndex(pd.to_datetime(frame["DATE"]))
        if time.tz is not None or time.has_duplicates or not time.is_monotonic_increasing:
            raise ValueError(f"{label} original DATE must be ordered, unique naive CET")
        if not np.allclose(np.diff(time.asi8) / 1e9, 600, rtol=0, atol=1e-6):
            raise ValueError(f"{label} time series has gaps or wrong sampling")
    if not pd.DatetimeIndex(house["DATE"]).equals(pd.DatetimeIndex(weather["DATE"])):
        raise ValueError("measured weather and house time grids do not match exactly")
    temperature_columns = [f"n2_aroom_{room}_110_AT" for room in ROOM_AREAS]
    heat_columns = [f"n2_aroom_{room}_heat_elP" for room in ROOM_AREAS]
    gain_columns = [f"n2_aroom_{room}_IHS_elP" for room in ROOM_AREAS
                    if f"n2_aroom_{room}_IHS_elP" in house]
    selected = house[temperature_columns + heat_columns + gain_columns].apply(pd.to_numeric, errors="raise")
    weather_selected = weather[["AmbientAirTemperature", *SOLAR_COLUMNS]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(selected.to_numpy()).all() or not np.isfinite(weather_selected.to_numpy()).all():
        raise ValueError("selected original measurements contain missing/nonfinite values; never fill them here")
    if not selected[temperature_columns].apply(lambda x: x.between(-20, 60)).all().all():
        raise ValueError("measured indoor temperature outside plausibility range")
    if (selected[heat_columns + gain_columns] < 0).any().any():
        raise ValueError("negative room electrical input")
    if not weather_selected["AmbientAirTemperature"].between(-60, 60).all():
        raise ValueError("ambient temperature outside plausibility range")
    radiation = weather_selected[SOLAR_COLUMNS]
    if (radiation < 0).any().any() or (radiation > 1800).any().any():
        raise ValueError("source irradiance outside physical range; no clipping performed")
    utc = pd.DatetimeIndex(house["DATE"]).tz_localize(timezone(timedelta(hours=1))).tz_convert("UTC")
    total_area = sum(ROOM_AREAS.values())
    indoor = sum(selected[f"n2_aroom_{room}_110_AT"] * area for room, area in ROOM_AREAS.items()) / total_area
    frame = pd.DataFrame({"source_time_cet": house["DATE"].astype(str),
                          "time_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "indoor_temperature_mean_c": indoor,
                          "outdoor_temperature_mean_c": weather_selected["AmbientAirTemperature"],
                          "measured_room_heating_kw": selected[heat_columns].sum(axis=1) / 1000,
                          "measured_internal_gain_kw": selected[gain_columns].sum(axis=1) / 1000,
                          "solar_reference_kw_m2": radiation.max(axis=1) / 1000,
                          "global_irradiance_w_m2": radiation["Radiation_Global"],
                          "dt_hours": DT})
    frame["total_room_heat_input_kw"] = frame["measured_room_heating_kw"] + frame["measured_internal_gain_kw"]
    frame["observation_identity"] = "derived_spatial_average_of_publisher_processed_measurements"
    qa = {"passed": True, "rows": len(frame), "sample_minutes": 10, "matched_time_rows": len(frame),
          "first_original_cet": str(house["DATE"].iloc[0]), "last_original_cet": str(house["DATE"].iloc[-1]),
          "first_utc_label": frame["time_utc"].iloc[0], "last_utc_label": frame["time_utc"].iloc[-1],
          "duplicate_rows": 0, "missing_intervals": 0, "nonfinite_selected_values": 0,
          "temperature_columns": temperature_columns, "heater_columns": heat_columns,
          "internal_gain_columns": gain_columns, "radiation_columns": SOLAR_COLUMNS,
          "room_floor_areas_m2": ROOM_AREAS, "total_area_m2": total_area,
          "aggregation_basis": "Figure 9 room floor areas and Table 12 total 165.75 m2; area weighted sensor average, not measured volume-mean temperature",
          "heat_quantity_basis": "measured electric heater and IHS input, converted to room sensible heat with unity model factor; omitted sockets/fan heat",
          "publisher_processing": "Short temperature gaps interpolated and power/flow gaps set zero, without per-row flags. Their exact count cannot be independently recovered.",
          "alignment": "DATE labels match exactly; source reports bin averages. Absolute within-bin label phase unconfirmed; averaged-observation equation used, no artificial power shift."}
    return frame.reset_index(drop=True), qa


def split_ranges(n: int) -> dict[str, tuple[int, int]]:
    if n < 100:
        raise ValueError("insufficient continuous measurements for temporal train/validation/test")
    train, validation = int(.6 * n), int(.2 * n)
    return {"train": (1, train), "validation": (train, train + validation), "test": (train + validation, n)}


def coefficients(parameters: np.ndarray, dt: float = DT) -> tuple[float, float]:
    h, c, _ = parameters
    if not np.isfinite(parameters).all() or h <= 0 or c <= 0 or dt <= 0:
        raise ValueError("RC parameters/dt must be finite and H/C/dt positive")
    x = h * dt / c
    return float(np.exp(-x)), float(-np.expm1(-x) / x)


def equilibrium(frame: pd.DataFrame, parameters: np.ndarray) -> np.ndarray:
    h, _, area = parameters
    return frame["outdoor_temperature_mean_c"].to_numpy() + (
        frame["total_room_heat_input_kw"].to_numpy() + area * frame["solar_reference_kw_m2"].to_numpy()) / h


def one_step(frame: pd.DataFrame, parameters: np.ndarray) -> np.ndarray:
    """Predict this bin mean using the previous measured bin mean, never T_end."""
    a, f = coefficients(parameters)
    eq = equilibrium(frame, parameters)
    observed = frame["indoor_temperature_mean_c"].to_numpy()
    result = np.full(len(frame), np.nan)
    result[1:] = a * observed[:-1] + (f - a) * eq[:-1] + (1 - f) * eq[1:]
    return result


def open_loop(frame: pd.DataFrame, parameters: np.ndarray, start: int = 1, stop: int | None = None) -> np.ndarray:
    """Initialize once from the preceding observed bin, then use inputs only."""
    stop = len(frame) if stop is None else stop
    if not 1 <= start < stop <= len(frame):
        raise ValueError("invalid open-loop interval")
    a, f = coefficients(parameters)
    eq = equilibrium(frame, parameters)
    # For a bin mean M, T_end = E + a/f*(M-E), derived by exact RC integration.
    state = eq[start - 1] + a / f * (float(frame["indoor_temperature_mean_c"].iloc[start - 1]) - eq[start - 1])
    prediction = np.full(len(frame), np.nan)
    for t in range(start, stop):
        prediction[t] = eq[t] + f * (state - eq[t])
        state = eq[t] + a * (state - eq[t])
    return prediction


def metrics(observed: np.ndarray, predicted: np.ndarray) -> dict:
    error = predicted - observed
    if not len(error) or not np.isfinite(error).all():
        raise ValueError("invalid predictions for metric computation")
    centered = error - error.mean()
    denominator = float(np.dot(centered, centered))
    acf = {str(lag): float(np.dot(centered[:-lag], centered[lag:]) / denominator)
           if denominator > 1e-20 else 0.0 for lag in (1, 6, 24, 144) if lag < len(error)}
    return {"n": len(error), "mae_c": float(np.mean(np.abs(error))),
            "rmse_c": float(np.sqrt(np.mean(error ** 2))), "bias_predicted_minus_observed_c": float(error.mean()),
            "max_abs_error_c": float(np.abs(error).max()), "residual_acf": acf}


def fit_training_only(frame: pd.DataFrame, train_stop: int) -> tuple[np.ndarray, dict]:
    training = frame.iloc[:train_stop].copy()
    target = training["indoor_temperature_mean_c"].to_numpy()[1:]
    def residuals(p):
        return one_step(training, p)[1:] - target
    starts = [[.1, 2., 3.], [.1, 10., 3.], [.1, 30., 7.], [.3, 10., 7.]]
    runs = []
    solutions = []
    for start in starts:
        fit = least_squares(residuals, start, bounds=(LOWER, UPPER), x_scale="jac",
                            ftol=1e-11, xtol=1e-11, gtol=1e-11, max_nfev=2000)
        solutions.append(fit)
        runs.append({"initial_parameters": start, "final_parameters": fit.x.tolist(),
                     "success": bool(fit.success), "status": int(fit.status), "message": fit.message,
                     "nfev": int(fit.nfev), "training_sse_c2": float(np.dot(fit.fun, fit.fun))})
    successful = [i for i, result in enumerate(solutions) if result.success and np.isfinite(result.x).all()]
    if not successful:
        raise ValueError("all training parameter fits failed")
    winner = min(successful, key=lambda i: runs[i]["training_sse_c2"])
    chosen = solutions[winner]
    names = ["H_kw_per_c", "C_kwh_per_c", "effective_solar_area_m2"]
    parameters = {name: {"estimate": float(value), "lower_bound": float(lo), "upper_bound": float(hi),
                         "near_lower_bound": bool(value - lo <= .001 * (hi - lo)),
                         "near_upper_bound": bool(hi - value <= .001 * (hi - lo))}
                  for name, value, lo, hi in zip(names, chosen.x, LOWER, UPPER)}
    return chosen.x, {"parameters": parameters, "thermal_time_constant_hours": float(chosen.x[1] / chosen.x[0]),
                      "selected_start_index_by_training_sse_only": winner, "all_starts": runs,
                      "training_rows": train_stop, "training_pairs": train_stop - 1,
                      "jacobian_condition_number": float(np.linalg.cond(chosen.jac)),
                      "warning": "H/C are equivalent parameters for a derived sensor aggregate; bounds are analyst constraints. No local-Wuhan calibration or statistical confidence guarantee."}


def save_plot(trajectory: pd.DataFrame, ranges: dict, scores: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    time = pd.to_datetime(trajectory["time_utc"], utc=True)
    figure, axes = plt.subplots(2, 1, figsize=(13, 8), constrained_layout=True)
    axes[0].plot(time, trajectory["indoor_temperature_mean_c"], color="#172B4D", linewidth=1, label="Measured room aggregate")
    axes[0].plot(time, trajectory["rc_continuous_open_loop_c"], color="#D95F02", linewidth=1, label="RC continuous open loop")
    axes[0].plot(time, trajectory["persistence_continuous_open_loop_c"], color="#4477AA", linewidth=1, label="Persistence open loop")
    for name in ("validation", "test"):
        start = ranges[name][0]
        axes[0].axvline(time.iloc[start], color="#777777", linestyle="--", linewidth=.8)
        axes[0].text(time.iloc[start], axes[0].get_ylim()[1], name, ha="left", va="top")
    axes[0].set_ylabel("Interval-mean temperature (deg C)")
    axes[0].set_xlabel("UTC labels (all published N2 main-experiment rows)")
    axes[0].set_title("External measurements expose open-loop 1R1C error; no Wuhan calibration claim")
    axes[0].legend(loc="lower left", fontsize=9)
    modes = ["rc_one_step", "persistence_one_step", "rc_continuous_open_loop", "persistence_continuous_open_loop", "rc_segment_open_loop", "persistence_segment_open_loop"]
    x = np.arange(len(modes))
    for offset, split, color in ((-.2, "validation", "#4477AA"), (.2, "test", "#D95F02")):
        axes[1].bar(x + offset, [scores[split][mode]["rmse_c"] for mode in modes], width=.4, color=color, label=split)
    axes[1].set_xticks(x, ["RC\n1-step", "Persistence\n1-step", "RC\ncontinuous", "Persistence\ncontinuous", "RC\nsegment", "Persistence\nsegment"])
    axes[1].set_ylabel("Held-out RMSE (deg C)")
    axes[1].legend()
    axes[1].grid(axis="y", alpha=.2)
    figure.savefig(path, dpi=160)
    plt.close(figure)


def run(output: Path = OUTPUT) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").unlink(missing_ok=True)
    plan = json.loads((DATA / "analysis_plan.json").read_text(encoding="utf-8"))
    if not plan["registered_before_parameter_fitting"] or not np.allclose(UPPER, [1, 100, plan["parameter_bounds"]["effective_solar_area_m2"][1]]):
        raise ValueError("analysis plan disagrees with implementation")
    members = verified_members()
    observations, qa = prepare_observations(pd.read_excel(EVIDENCE / HOUSE_FILE), pd.read_excel(EVIDENCE / WEATHER_FILE))
    observations.to_csv(DATA / "observations.csv", index=False, float_format="%.12g", encoding="utf-8")
    write_json(DATA / "data_quality.json", qa)
    write_json(DATA / "extracted_members.json", members)
    ranges = split_ranges(len(observations))
    parameters, fitting = fit_training_only(observations, ranges["train"][1])
    observed = observations["indoor_temperature_mean_c"].to_numpy()
    step = one_step(observations, parameters)
    continuous = open_loop(observations, parameters)
    persistence_step = np.r_[np.nan, observed[:-1]]
    persistence_continuous = np.full(len(observed), observed[0])
    segment = np.full(len(observed), np.nan)
    persistence_segment = np.full(len(observed), np.nan)
    split_column = np.full(len(observed), "initialization", dtype=object)
    scores = {}
    split_identity = {}
    for name, (start, stop) in ranges.items():
        segment[start:stop] = open_loop(observations, parameters, start, stop)[start:stop]
        persistence_segment[start:stop] = observed[start - 1]
        split_column[start:stop] = name
        scores[name] = {mode: metrics(observed[start:stop], values[start:stop]) for mode, values in (
            ("rc_one_step", step), ("persistence_one_step", persistence_step),
            ("rc_continuous_open_loop", continuous), ("persistence_continuous_open_loop", persistence_continuous),
            ("rc_segment_open_loop", segment), ("persistence_segment_open_loop", persistence_segment))}
        split_identity[name] = {"target_row_start_inclusive": start, "target_row_stop_exclusive": stop,
                               "first_utc": observations["time_utc"].iloc[start], "last_utc": observations["time_utc"].iloc[stop - 1]}
    trajectory = observations.copy()
    trajectory["split"] = split_column
    for name, values in (("rc_one_step_c", step), ("persistence_one_step_c", persistence_step),
                         ("rc_continuous_open_loop_c", continuous), ("persistence_continuous_open_loop_c", persistence_continuous),
                         ("rc_segment_open_loop_c", segment), ("persistence_segment_open_loop_c", persistence_segment)):
        trajectory[name] = values
        trajectory[name.removesuffix("_c") + "_residual_c"] = values - observed
    trajectory.to_csv(output / "predictions_and_residuals.csv", index=False, float_format="%.12g")
    save_plot(trajectory, ranges, scores, output / "external_validation.png")
    report = {"status": "external_applicability_diagnostic_on_processed_measurements", "data_source_found": True,
              "dataset_doi": "10.24406/fordatis/76.2", "licence": "CC BY-SA 4.0", "data_quality": qa,
              "fit": fitting, "splits": split_identity, "metrics": scores,
              "predictions_definition": plan["predictions"], "source_and_model_limitations": plan["source_limitations"],
              "temperature_sampling": "exact interval-mean observation equation, not period-end measurement",
              "runtime": {"python": sys.version, "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__},
              "no_Wuhan_parameter_transfer": True, "no_random_split": True}
    write_json(output / "validation_report.json", report)
    source_files = [DATA / "analysis_plan.json", DATA / "source_provenance.json", DATA / "observations.csv",
                    DATA / "data_quality.json", DATA / "extracted_members.json",
                    DATA / "README.md", DATA / "field_evidence_map.json", Path(__file__),
                    ROOT / "tests/test_external_rc_benchmark.py",
                    EVIDENCE / "downloads.json", EVIDENCE / "annex71_specification.pdf",
                    EVIDENCE / "annex71_landing.html", EVIDENCE / "annex71_main_experiment.zip",
                    EVIDENCE / HOUSE_FILE, EVIDENCE / WEATHER_FILE]
    manifest = {"complete": True, "status": report["status"], "inputs": [
        {"file": p.relative_to(ROOT).as_posix(), "sha256": digest(p)} for p in source_files],
        "outputs": [{"file": name, "sha256": digest(output / name)} for name in
                    ("validation_report.json", "predictions_and_residuals.csv", "external_validation.png")],
        "supplemental_logs": "run_stdout.json and test logs are supplementary records, not model output hash identities",
        "licence": "CC BY-SA 4.0 for selected and derived measurement data",
        "excluded_discovery_files": "DTU CTSM example retained locally, licence unconfirmed; not selected for analysis or public redistribution",
        "certificate_claim": "No full physical validation, Wuhan calibration or general accuracy guarantee"}
    write_json(output / "manifest.json", manifest)
    return {"rows": len(observations), "parameters": parameters.tolist(),
            "validation": scores["validation"], "test": scores["test"], "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir.resolve()), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
