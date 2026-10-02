"""Separately frozen six-building confirmation for hard-energy minimum-W1 LP."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from time import perf_counter
import traceback

import numpy as np
import pandas as pd
import threadpoolctl

import bdg2_confirmation_benchmark as confirmation
import bdg2_external_benchmark as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/implementation_20261002/algorithm_hard_weights"
SETTINGS = {"year": 2017, "target_per_category": 2, "base_k": 6,
            "seed": 42, "repeats": 3, "threads": 1,
            "coverage_gate": .95, "no_acceleration_enabled": True}


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def freeze():
    if (OUT / "protocol_lock.json").exists():
        verify()
        print("Existing separate hard-weight lock verified.")
        return
    confirmation.verify()
    OUT.mkdir(parents=True, exist_ok=True)
    prior_cases = base.read_json(base.OUT / "selection.json")["cases"] + base.read_json(confirmation.OUT / "selection.json")["cases"]
    inventories = {c: base.read_json(base.AUDIT / f"{c}_cleaned_channels.json") for c in base.CATEGORIES}
    previous_target = confirmation.TARGET
    try:
        confirmation.TARGET = SETTINGS["target_per_category"]
        selection = confirmation.select_confirmation(inventories, prior_cases)
    finally:
        confirmation.TARGET = previous_target
    selection["settings"] = SETTINGS
    selection["prior_nine_case_role"] = "development_reference_for_new_hard_weight_method; excluded_from_this_confirmation"
    base.write_json(OUT / "selection.json", selection)
    base.write_json(OUT / "environment.json", base.environment())
    version = OUT / "version"
    version.mkdir()
    files = [ROOT / "src/heatplan" / name for name in (
        "scenarios.py", "representative_adoption.py", "hard_weight_calibration.py")]
    for source in files:
        shutil.copyfile(source, version / source.name)
    policy = confirmation.adoption_module().WeightAdoptionPolicy()
    base.write_json(OUT / "protocol.json", {
        "frozen_utc": base.utc(), "settings": SETTINGS, "adoption_policy": asdict(policy),
        "policy_origin": "same_predeclared_research_rule_as_prior_nine; thresholds_not_moved",
        "methods": ["extreme_aware", "extreme_calibrated", "hard_energy_minimum_W1", "adoption_soft", "adoption_hard"],
        "budget": "same fixed actual extreme-day selection for both weight candidates; K=6 and actual M recorded",
        "hard_method": "standard LP; sum weights and retained energy hard equalities; day floors; minimum 1D CDF W1 with absolute epigraphs",
        "soft_method": "unchanged regularization 1e-4; same extreme floor1 and ordinary floor1e-6",
        "old_nine_role": "development_reference_only_for_new_method; not pooled into_confirmation",
        "unseen_buildings_required": True, "unseen_sites_preferred_but_not_required": True,
        "failure_policy": "retain all frozen cases, infeasibility and gate failures; no replacement or threshold relaxation",
        "observations": "2017 complete days only; preserve missingness and all valid raw-hour scope losses",
        "scope": "overseas metering compression; no engineering cost, comfort, carbon or chronological dispatch certification"})
    locked = [Path(__file__), ROOT / "scripts/bdg2_confirmation_benchmark.py", ROOT / "scripts/bdg2_external_benchmark.py",
              ROOT / "tests/test_hard_weight_calibration.py", OUT / "protocol.json", OUT / "selection.json",
              OUT / "environment.json", base.OUT / "selection.json", confirmation.OUT / "selection.json",
              confirmation.OUT / "protocol_lock.json"]
    locked += [version / p.name for p in files]
    old = base.read_json(base.OUT / "protocol_lock.json")
    sources = old["sources"]
    for name, item in sources.items():
        if base.sha(base.DATA / name) != item["sha256"]:
            raise ValueError("Raw source changed: " + name)
    base.write_json(OUT / "protocol_lock.json", {
        "schema_version": 1, "frozen_utc": base.utc(), "settings": SETTINGS,
        "source_repository": old["source_repository"], "source_commit": old["source_commit"],
        "sources": sources, "case_count": len(selection["cases"]),
        "files": [{"path": str(p.relative_to(ROOT)), "sha256": base.sha(p)} for p in locked]})
    print(json.dumps({"cases": [(c["case_id"], c["site_id"], c["unseen_site"]) for c in selection["cases"]],
                      "protocol_lock_sha256": base.sha(OUT / "protocol_lock.json")}, ensure_ascii=False, indent=2))


def verify():
    lock = base.read_json(OUT / "protocol_lock.json")
    if lock["settings"] != SETTINGS:
        raise ValueError("Hard-weight settings changed")
    for item in lock["files"]:
        if base.sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("Frozen input/code changed: " + item["path"])
    for name, item in lock["sources"].items():
        if base.sha(base.DATA / name) != item["sha256"]:
            raise ValueError("Raw source changed: " + name)
    old, actual = base.read_json(OUT / "environment.json"), base.environment()
    if old["versions"] != actual["versions"] or old["python"] != actual["python"]:
        raise ValueError("Runtime differs from frozen environment")
    return lock


def run():
    started = perf_counter()
    verify()
    if (OUT / "run_manifest.json").exists():
        raise ValueError("Existing hard-weight run must not be overwritten")
    core = base.load_core(OUT / "version/scenarios.py", "hard_confirmation")
    hard = module(OUT / "version/hard_weight_calibration.py", "hard_weight_frozen")
    gate = confirmation.adoption_module(OUT / "version/representative_adoption.py")
    cases = base.read_json(OUT / "selection.json")["cases"]
    weather = pd.read_csv(base.DATA / "weather.csv", usecols=["timestamp", "site_id", "airTemperature"])
    weather["timestamp"] = pd.to_datetime(weather["timestamp"])
    metadata = pd.read_csv(base.DATA / "metadata.csv").set_index("building_id")
    meters = {}
    for category in base.CATEGORIES:
        ids = [c["building_id"] for c in cases if c["category"] == category]
        meters[category] = pd.read_csv(base.DATA / f"{category}_cleaned.csv", usecols=["timestamp"]+ids)
        meters[category]["timestamp"] = pd.to_datetime(meters[category]["timestamp"])
    records, decisions = [], []
    for case in cases:
        case_started = perf_counter()
        folder = OUT / "cases" / case["case_id"] / "2017"
        folder.mkdir(parents=True, exist_ok=True)
        assert str(metadata.loc[case["building_id"], "site_id"]) == case["site_id"]
        raw = meters[case["category"]][["timestamp", case["building_id"]]].merge(
            weather.loc[weather.site_id == case["site_id"], ["timestamp", "airTemperature"]],
            on="timestamp", how="left", validate="one_to_one").rename(columns={
                "timestamp": "time", case["building_id"]: "energy_kwh", "airTemperature": "outdoor_temp"})
        full, retained, excluded, quality = base.prepare_year(raw, 2017)
        if quality["meter_coverage"] < .95 or quality["joint_valid_coverage"] < .95 or quality["negative_meter_hours"]:
            raise ValueError("Locked quality no longer holds")
        full.to_csv(folder / "observed_calendar.csv", index=False, na_rep="")
        base.write_json(folder / "coverage.json", quality)
        base.write_json(folder / "excluded_days.json", excluded)
        outputs, statistics = {}, {}
        original = None
        for method in ("extreme_aware", "extreme_calibrated", "hard_energy_minimum_W1"):
            method_started = perf_counter()
            record = {**case, "method": method, "observed_calendar_sha256": base.sha(folder / "observed_calendar.csv")}
            try:
                if method == "extreme_aware":
                    output = base.execute_method(core, retained, method, SETTINGS["base_k"])
                    original = output
                elif method == "extreme_calibrated":
                    output = base.execute_calibration(core, retained, original)
                else:
                    if original is None:
                        raise ValueError("Extreme selection failed; no fixed candidates")
                    profiles = retained.load_kw.to_numpy().reshape(-1, 24)[original["days"]]
                    lower = [1 if original["reasons"].get(str(d), "") else 1e-6 for d in original["days"]]
                    elapsed, answers = [], []
                    for _ in range(3):
                        with threadpoolctl.threadpool_limits(limits=1):
                            begin = perf_counter()
                            answer = hard.minimum_w1_energy_weights(retained.load_kw.to_numpy(), profiles, original["weights"], lower)
                            elapsed.append(perf_counter()-begin)
                        answers.append(answer)
                    if any(a != answers[0] for a in answers[1:]):
                        raise ValueError("Fixed LP repetitions differ")
                    record["hard_LP_diagnostics"] = answer
                    record["elapsed_seconds"] = elapsed
                    base.write_json(folder / "hard_LP_diagnostics.json", {**answer, "elapsed_seconds": elapsed})
                    if answer["status"] != "ok":
                        record.update(status=answer["status"], reason=answer["reason"])
                        raise RuntimeError("Hard candidate did not produce usable weights: " + answer["reason"])
                    days, weights = base.validate_representatives(original["days"], answer["weights"], len(retained)//24,
                                                                 requested=len(original["days"]))
                    output = {**original, "days": days, "weights": weights,
                              "elapsed_seconds": elapsed, "elapsed_median_seconds": float(np.median(elapsed))}
                statistic, events = base.compute_metrics(retained, output["days"], output["weights"])
                outputs[method], statistics[method] = output, statistic
                record.update(status="ok", actual_m=output["actual_m"], selection=output, metrics=statistic)
                base.representative_frame(retained, output).to_csv(folder / f"{method}_actual_days.csv", index=False)
                base.write_json(folder / f"{method}_ramp_events.json", events)
            except Exception as exc:
                record.update(status=record.get("status", "failed"), exception_type=type(exc).__name__,
                              exception=str(exc), traceback=traceback.format_exc())
            record["total_method_seconds"] = perf_counter()-method_started
            records.append(record)
            base.write_json(folder / f"{method}_result.json", record)
        if original is not None:
            scope = confirmation.observed_scope_audit(full, retained, original, quality)
            base.write_json(folder / "original_observed_scope_audit.json", scope)
            profiles = retained.load_kw.to_numpy().reshape(-1, 24)[original["days"]]
            lower = [1 if original["reasons"].get(str(d), "") else 1e-6 for d in original["days"]]
            for method, candidate in (("adoption_soft", "extreme_calibrated"), ("adoption_hard", "hard_energy_minimum_W1")):
                proposed = outputs.get(candidate)
                decision = gate.assess_weight_candidate(
                    profiles.sum(axis=1)[None, :], [float(retained.load_kw.sum())], original["weights"],
                    proposed["weights"] if proposed else None, lower,
                    baseline_w1=statistics["extreme_aware"]["ldc_w1_over_mean_load"],
                    candidate_w1=statistics.get(candidate, {}).get("ldc_w1_over_mean_load"),
                    peak_retention_ratio=statistics["extreme_aware"]["peak_retention_ratio"],
                    ramp_retention_ratio=statistics["extreme_aware"]["maximum_real_ramp_retention_ratio"],
                    original_observed_peak_retention_ratio=scope["original_observed_peak_retention_ratio"],
                    candidate_solver_success=proposed is not None)
                chosen, _ = base.compute_metrics(retained, original["days"], decision["selected_weights"])
                decision.update(case_id=case["case_id"], method=method, candidate_method=candidate,
                                year=2017, category=case["category"], selected_metrics=chosen,
                                original_observed_scope=scope, actual_m=original["actual_m"],
                                total_case_seconds=perf_counter()-case_started)
                decisions.append(decision)
                records.append({**case, "method": method, "status": "ok", "actual_m": original["actual_m"],
                                "metrics": chosen, "adoption": decision})
                base.write_json(folder / f"{method}_result.json", decision)
        base.write_json(OUT / "results.json", records)
        base.write_json(OUT / "adoption_records.json", decisions)
        print(f"{case['case_id']}: days={len(retained)//24}, M={original['actual_m'] if original else None}, soft={decisions[-2]['statistical_candidate_adopted'] if original else None}, hard={decisions[-1]['statistical_candidate_adopted'] if original else None}", flush=True)
    verify()
    base.write_json(OUT / "failures.json", [r for r in records if r["status"] != "ok"])
    base.write_json(OUT / "run_manifest.json", {"completed_utc": base.utc(), "total_elapsed_seconds": perf_counter()-started,
        "protocol_lock_sha256": base.sha(OUT / "protocol_lock.json"), "case_count": len(cases), "method_records": len(records),
        "failed_method_records": sum(r["status"] != "ok" for r in records), "no_acceleration_enabled": True,
        "timing_scope": "3 stock single-thread independent calls; weight calls reuse same extreme selection; source loading, failures, audits and exports included in end-to-end total",
        "artifacts": [{"path": str(p.relative_to(ROOT)), "sha256": base.sha(p)} for p in sorted(OUT.rglob("*")) if p.is_file() and p.name != "run_manifest.json"]})


def report():
    verify()
    manifest = base.read_json(OUT / "run_manifest.json")
    for item in manifest["artifacts"]:
        if base.sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("Run artifact changed: " + item["path"])
    records, decisions = base.read_json(OUT / "results.json"), base.read_json(OUT / "adoption_records.json")
    summaries = []
    for method in ("extreme_aware", "extreme_calibrated", "hard_energy_minimum_W1", "adoption_soft", "adoption_hard"):
        good = [r for r in records if r["method"] == method and r["status"] == "ok"]
        row = {"method": method, "successful_records": len(good),
               "failed_records": sum(r["method"] == method and r["status"] != "ok" for r in records)}
        for key in ("retained_scope_abs_energy_bias", "ldc_w1_over_mean_load", "peak_retention_ratio", "maximum_real_ramp_retention_ratio"):
            values = [r["metrics"][key] for r in good if r["metrics"][key] is not None]
            row["median_"+key] = float(np.median(values)) if values else None
        row["candidate_adopted_cases"] = sum(d["method"] == method and d["statistical_candidate_adopted"] for d in decisions)
        summaries.append(row)
    base.write_json(OUT / "summary.json", {"summaries": summaries, "case_count": manifest["case_count"],
        "failed_method_records": manifest["failed_method_records"], "total_elapsed_seconds": manifest["total_elapsed_seconds"],
        "old_nine_role": "development_reference_only_for_this_new_method", "engineering_operation_validated": False})
    pd.DataFrame(summaries).to_csv(OUT / "summary.csv", index=False)
    print(json.dumps(base.read_json(OUT / "summary.json"), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "run", "report", "verify"))
    args = parser.parse_args()
    {"freeze": freeze, "run": run, "report": report, "verify": verify}[args.phase]()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
