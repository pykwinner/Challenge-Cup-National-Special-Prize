"""New-building/new-site BDG2 confirmation: freeze, run, report, verify.

No observations are filled, transformed to improve a result or overwritten.
The original benchmark helper and all previous experiments remain unchanged.
"""
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

import bdg2_external_benchmark as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/implementation_20261002/algorithm"
YEAR = 2017
TARGET = 3
SETTINGS = {"year": YEAR, "target_per_category": TARGET, "base_k": 6,
            "seed": 42, "repeats": 3, "threads": 1, "coverage_gate": 0.95,
            "selection": "new sites first; stable site/building IDs; round robin sites; unique building across meter types",
            "no_acceleration_enabled": True}


def adoption_module(path=ROOT / "src/heatplan/representative_adoption.py"):
    spec = importlib.util.spec_from_file_location("frozen_representative_adoption", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def select_confirmation(inventories, development_cases):
    development_ids = {c["building_id"] for c in development_cases}
    development_sites = {c["site_id"] for c in development_cases}
    selected, decisions, seen, counts = [], [], set(development_ids), {}
    for category in base.CATEGORIES:
        sites = sorted({r["site_id"] for r in inventories[category]},
                       key=lambda s: (s in development_sites, s))
        queues = {site: sorted([r for r in inventories[category] if r["site_id"] == site],
                               key=lambda r: r["building_id"]) for site in sites}
        number, round_number = 0, 0
        while number < TARGET:
            round_number += 1
            changed = False
            # Use only entirely unseen sites while sufficient eligible records remain.
            new_sites = [s for s in sites if s not in development_sites and queues[s]]
            active = new_sites or [s for s in sites if queues[s]]
            for site in active:
                if number >= TARGET:
                    break
                while queues[site]:
                    record = queues[site].pop(0)
                    reasons = base.quality_reasons(record, YEAR)
                    if record["building_id"] in seen:
                        reasons.append("development_or_confirmation_building_already_used")
                    decisions.append({"category": category, "building_id": record["building_id"],
                                      "site_id": site, "round": round_number,
                                      "accepted": not reasons, "reasons": reasons,
                                      "quality": record["years"][str(YEAR)]})
                    if reasons:
                        continue
                    selected.append({"case_id": f"{category}__{record['building_id']}",
                                     "category": category, "building_id": record["building_id"],
                                     "site_id": site, "year": YEAR,
                                     "unseen_building": True, "unseen_site": site not in development_sites,
                                     "quality": record["years"][str(YEAR)]})
                    seen.add(record["building_id"])
                    number += 1
                    changed = True
                    break
            if not changed and not any(queues[s] for s in sites):
                break
        counts[category] = {"selected": number, "shortfall": max(TARGET-number, 0),
                            "sites": sorted({c["site_id"] for c in selected if c["category"] == category})}
    return {"settings": SETTINGS, "development_buildings": sorted(development_ids),
            "development_sites": sorted(development_sites), "cases": selected,
            "decisions": decisions, "counts": counts,
            "independence": "New buildings and preferably new sites; shared-site buildings are not independent site replications."}


def freeze():
    if (OUT / "protocol_lock.json").exists():
        verify()
        print("Existing confirmation lock verified; no sample or method changed.")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    old = base.read_json(base.OUT / "protocol_lock.json")
    for record in old["files"]:
        if base.sha(ROOT / record["path"]) != record["sha256"]:
            raise ValueError("Original benchmark frozen input or helper changed: " + record["path"])
    inventories = {c: base.read_json(base.AUDIT / f"{c}_cleaned_channels.json") for c in base.CATEGORIES}
    selection = select_confirmation(inventories, base.read_json(base.OUT / "selection.json")["cases"])
    base.write_json(OUT / "selection.json", selection)
    base.write_json(OUT / "environment.json", base.environment())
    version = OUT / "version"
    version.mkdir()
    source_files = [ROOT / "src/heatplan/scenarios.py", ROOT / "src/heatplan/representative_adoption.py"]
    for source in source_files:
        shutil.copyfile(source, version / source.name)
    module = adoption_module()
    protocol = {"frozen_utc": base.utc(), "settings": SETTINGS,
                "adoption_policy": asdict(module.WeightAdoptionPolicy()),
                "policy_origin": "research choices, not official score requirements",
                "scope": "overseas metering compression, not Chinese three-city engineering validation",
                "methods": ["ordinary", "extreme_aware", "extreme_calibrated", "adoption_rule"],
                "soft_calibration": {"regularization": 1e-4, "minimum_extreme_weight": 1.0, "minimum_day_weight": 1e-6},
                "budget": "ordinary uses extreme actual positive day count; fixed selection reused for soft calibration and adoption",
                "zero_energy": "retain case and failures; relative gates undefined; fall back without qualification",
                "no_engineering_claims": ["costs", "carbon", "comfort", "storage", "MILP operation", "full-calendar planning feasibility"],
                "selection_freeze_before_algorithm_run": True}
    base.write_json(OUT / "protocol.json", protocol)
    locked_files = [Path(__file__), ROOT / "scripts/bdg2_external_benchmark.py",
                    OUT / "protocol.json", OUT / "selection.json", OUT / "environment.json",
                    ROOT / "tests/test_representative_adoption.py", ROOT / "tests/test_bdg2_confirmation.py",
                    base.OUT / "selection.json", base.OUT / "protocol_lock.json"]
    locked_files += [base.AUDIT / f"{c}_cleaned_channels.json" for c in base.CATEGORIES]
    locked_files += [version / p.name for p in source_files]
    sources = {}
    for name, record in old["sources"].items():
        if base.sha(base.DATA / name) != record["sha256"]:
            raise ValueError("Raw source hash changed: " + name)
        sources[name] = record
    base.write_json(OUT / "protocol_lock.json", {
        "schema_version": 1, "frozen_utc": base.utc(), "source_repository": old["source_repository"],
        "source_commit": old["source_commit"], "settings": SETTINGS,
        "files": [{"path": str(p.relative_to(ROOT)), "sha256": base.sha(p)} for p in locked_files],
        "sources": sources, "case_count": len(selection["cases"])})
    print(json.dumps({"cases": [(c["case_id"], c["site_id"]) for c in selection["cases"]],
                      "all_unseen_sites": all(c["unseen_site"] for c in selection["cases"]),
                      "protocol_lock_sha256": base.sha(OUT / "protocol_lock.json")}, ensure_ascii=False, indent=2))


def verify():
    lock = base.read_json(OUT / "protocol_lock.json")
    if lock["settings"] != SETTINGS:
        raise ValueError("Confirmation settings differ from frozen protocol")
    for record in lock["files"]:
        if base.sha(ROOT / record["path"]) != record["sha256"]:
            raise ValueError("Frozen artifact changed: " + record["path"])
    for name, record in lock["sources"].items():
        if base.sha(base.DATA / name) != record["sha256"]:
            raise ValueError("Raw source changed: " + name)
    environment = base.read_json(OUT / "environment.json")
    actual = base.environment()
    if environment["versions"] != actual["versions"] or environment["python"] != actual["python"]:
        raise ValueError("Confirmation runtime differs from frozen environment")
    return lock


def observed_scope_audit(full, retained, selection, quality):
    load = full["load_kw"].to_numpy(dtype=float)
    valid = np.isfinite(load) & (load >= 0)
    keep = full["retained_complete_day"].to_numpy(dtype=bool)
    retained_energy = float(retained["load_kw"].sum())
    observed_energy = float(load[valid].sum())
    selected_times = {pd.Timestamp(t) for d in selection["days"] for t in retained.iloc[d*24:(d+1)*24]["time"]}
    selected_hours = np.array([pd.Timestamp(t) in selected_times for t in full["time"]])
    peak = float(load[valid].max()) if valid.any() else None
    selected_peak = float(load[selected_hours].max()) if selected_hours.any() else None
    positive_edges = valid[:-1] & valid[1:]
    jumps = np.abs(np.diff(load))
    maximum = float(jumps[positive_edges].max()) if positive_edges.any() else None
    chosen_edges = positive_edges & selected_hours[:-1] & selected_hours[1:]
    chosen_ramp = float(jumps[chosen_edges].max()) if chosen_edges.any() else None
    deficit = np.maximum(load[valid] - selected_peak, 0) if selected_peak is not None else np.array([])
    return {"all_valid_meter_hours": int(valid.sum()), "retained_complete_day_valid_hours": int((valid & keep).sum()),
            "valid_meter_hours_excluded_by_complete_day_filter": int((valid & ~keep).sum()),
            "observed_partial_year_energy_kwh": observed_energy, "retained_energy_kwh": retained_energy,
            "observed_energy_excluded_kwh": observed_energy-retained_energy,
            "observed_energy_excluded_fraction": (observed_energy-retained_energy)/observed_energy if observed_energy else None,
            "original_observed_peak_kw": peak, "selected_peak_kw": selected_peak,
            "original_observed_peak_retention_ratio": selected_peak/peak if peak is not None and peak > 0 else None,
            "original_peak_lost_by_complete_day_filter": quality["original_peak_excluded_from_retained_scope"],
            "maximum_original_valid_adjacent_ramp_kw_per_hour": maximum,
            "maximum_selected_original_adjacent_ramp_kw_per_hour": chosen_ramp,
            "original_observed_ramp_retention_ratio": chosen_ramp/maximum if maximum is not None and maximum > 0 and chosen_ramp is not None else None,
            "capacity_only_observed_deficit_hours": int((deficit > 0).sum()),
            "capacity_only_observed_deficit_energy_kwh": float(deficit.sum()),
            "interpretation": "Meter observations only. Calendar gaps remain missing; capacity-only diagnostic is not engineering dispatch."}


def run():
    started = perf_counter()
    lock = verify()
    if (OUT / "run_manifest.json").exists():
        raise ValueError("Confirmation run already completed; retained evidence must not be overwritten")
    module = adoption_module(OUT / "version/representative_adoption.py")
    core = base.load_core(OUT / "version/scenarios.py", "confirmation")
    cases = base.read_json(OUT / "selection.json")["cases"]
    io_started = perf_counter()
    metadata = pd.read_csv(base.DATA / "metadata.csv").set_index("building_id")
    weather = pd.read_csv(base.DATA / "weather.csv", usecols=["timestamp", "site_id", "airTemperature"])
    weather["timestamp"] = pd.to_datetime(weather["timestamp"])
    if weather.duplicated(["site_id", "timestamp"]).any():
        raise ValueError("Duplicate source weather timestamp")
    meters = {}
    for category in base.CATEGORIES:
        ids = [c["building_id"] for c in cases if c["category"] == category]
        meters[category] = pd.read_csv(base.DATA / f"{category}_cleaned.csv", usecols=["timestamp"]+ids)
        meters[category]["timestamp"] = pd.to_datetime(meters[category]["timestamp"])
    io_elapsed = perf_counter()-io_started
    results, quality_records, adoption_records = [], [], []
    for case in cases:
        case_started = perf_counter()
        case_dir = OUT / "cases" / case["case_id"] / str(YEAR)
        case_dir.mkdir(parents=True, exist_ok=True)
        if str(metadata.loc[case["building_id"], "site_id"]) != case["site_id"]:
            raise ValueError("Locked metadata site differs")
        meter = meters[case["category"]][["timestamp", case["building_id"]]]
        temperatures = weather.loc[weather["site_id"] == case["site_id"], ["timestamp", "airTemperature"]]
        raw = meter.merge(temperatures, on="timestamp", how="left", validate="one_to_one", sort=False).rename(
            columns={"timestamp": "time", case["building_id"]: "energy_kwh", "airTemperature": "outdoor_temp"})
        full, retained, exclusions, quality = base.prepare_year(raw, YEAR)
        quality.update(case_id=case["case_id"], site_id=case["site_id"], category=case["category"])
        if quality["meter_coverage"] < .95 or quality["joint_valid_coverage"] < .95 or quality["negative_meter_hours"]:
            raise ValueError("Selected source no longer meets frozen quality gate")
        quality_records.append(quality)
        base.write_json(case_dir / "coverage.json", quality)
        base.write_json(case_dir / "excluded_days.json", exclusions)
        full.to_csv(case_dir / "observed_calendar.csv", index=False, na_rep="")
        input_hash = base.sha(case_dir / "observed_calendar.csv")
        outputs, metrics = {}, {}
        budget = None
        for method in ("extreme_aware", "ordinary", "extreme_calibrated"):
            record = {**case, "method": method, "observed_calendar_sha256": input_hash,
                      "core_sha256": base.sha(OUT / "version/scenarios.py")}
            method_started = perf_counter()
            try:
                if method == "extreme_aware":
                    output = base.execute_method(core, retained, method, SETTINGS["base_k"])
                    budget = output["actual_m"]
                elif method == "ordinary":
                    if budget is None:
                        raise ValueError("Extreme method failed; paired day budget unavailable")
                    output = base.execute_method(core, retained, method, budget)
                else:
                    output = base.execute_calibration(core, retained, outputs["extreme_aware"])
                statistic, events = base.compute_metrics(retained, output["days"], output["weights"])
                outputs[method], metrics[method] = output, statistic
                record.update(status="ok", actual_m=output["actual_m"], paired_actual_budget=budget,
                              metrics=statistic, selection=output)
                frame = base.representative_frame(retained, output)
                frame.to_csv(case_dir / f"{method}_actual_days.csv", index=False)
                base.write_json(case_dir / f"{method}_ramp_events.json", events)
            except Exception as exc:
                record.update(status="failed", exception_type=type(exc).__name__, exception=str(exc),
                              traceback=traceback.format_exc(), elapsed_seconds=getattr(exc, "benchmark_elapsed_seconds", []))
            record["total_method_seconds_including_metric_audit"] = perf_counter()-method_started
            base.write_json(case_dir / f"{method}_result.json", record)
            results.append(record)
        if "extreme_aware" in outputs:
            original = outputs["extreme_aware"]
            scope = observed_scope_audit(full, retained, original, quality)
            base.write_json(case_dir / "original_observed_scope_audit.json", scope)
            design = retained["load_kw"].to_numpy().reshape(-1, 24).sum(axis=1)[original["days"]][None, :]
            target = [float(retained["load_kw"].sum())]
            lower = [1.0 if original["reasons"].get(str(d), "") else 1e-6 for d in original["days"]]
            lp_times, lp_outputs = [], []
            for _ in range(SETTINGS["repeats"]):
                lp_started = perf_counter()
                with threadpoolctl.threadpool_limits(limits=1):
                    exact = module.diagnose_exact_weight_feasibility(design, target, original["weights"], lower)
                lp_times.append(perf_counter()-lp_started)
                lp_outputs.append(exact)
            if any(r != lp_outputs[0] for r in lp_outputs[1:]):
                raise ValueError("Exact matching diagnostic differs across repetitions")
            exact.update(elapsed_seconds=lp_times, elapsed_median_seconds=float(np.median(lp_times)))
            base.write_json(case_dir / "exact_matching_feasibility.json", exact)
            proposed = outputs.get("extreme_calibrated")
            gate_started = perf_counter()
            decision = module.assess_weight_candidate(
                design, target, original["weights"], proposed["weights"] if proposed else None, lower,
                baseline_w1=metrics["extreme_aware"]["ldc_w1_over_mean_load"],
                candidate_w1=metrics.get("extreme_calibrated", {}).get("ldc_w1_over_mean_load"),
                peak_retention_ratio=metrics["extreme_aware"]["peak_retention_ratio"],
                ramp_retention_ratio=metrics["extreme_aware"]["maximum_real_ramp_retention_ratio"],
                original_observed_peak_retention_ratio=scope["original_observed_peak_retention_ratio"],
                candidate_solver_success=proposed is not None)
            decision.update(case_id=case["case_id"], category=case["category"], site_id=case["site_id"], year=YEAR,
                            exact_matching_status=exact["status"], elapsed_seconds=perf_counter()-gate_started)
            chosen_metrics, _ = base.compute_metrics(retained, original["days"], decision["selected_weights"])
            decision["selected_metrics"] = chosen_metrics
            decision["original_observed_scope"] = scope
            decision["paired_actual_budget"] = budget
            decision["total_case_seconds"] = perf_counter()-case_started
            base.write_json(case_dir / "adoption_rule_result.json", decision)
            adoption_records.append(decision)
            results.append({**case, "method": "adoption_rule", "status": "ok", "actual_m": budget,
                            "metrics": chosen_metrics, "adoption": decision})
        base.write_json(OUT / "results.json", results)
        base.write_json(OUT / "adoption_records.json", adoption_records)
        print(f"{case['case_id']} {YEAR}: days={len(retained)//24}, M={budget}, candidate_adopted={adoption_records[-1]['statistical_candidate_adopted'] if adoption_records else None}", flush=True)
    # Recheck raw bytes after experiment; this does not modify any input.
    verify()
    base.write_json(OUT / "coverage.json", quality_records)
    base.write_json(OUT / "failures.json", [r for r in results if r["status"] != "ok"])
    base.write_json(OUT / "run_manifest.json", {
        "completed_utc": base.utc(), "protocol_lock_sha256": base.sha(OUT / "protocol_lock.json"),
        "case_count": len(cases), "method_records": len(results),
        "failed_method_records": sum(r["status"] != "ok" for r in results),
        "source_load_io_seconds": io_elapsed, "total_elapsed_seconds": perf_counter()-started,
        "no_acceleration_enabled": True,
        "timing_scope": "3 independent stock single-thread method invocations; calibration reuses fixed selection; LP and gate additional work; case/end-to-end totals retained",
        "artifacts": [{"path": str(p.relative_to(ROOT)), "sha256": base.sha(p)} for p in sorted(OUT.rglob("*")) if p.is_file() and p.name != "run_manifest.json"]})


def report():
    verify()
    manifest = base.read_json(OUT / "run_manifest.json")
    for item in manifest["artifacts"]:
        if base.sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("Run artifact changed: " + item["path"])
    results = base.read_json(OUT / "results.json")
    decisions = base.read_json(OUT / "adoption_records.json")
    keys = ("retained_scope_abs_energy_bias", "ldc_w1_over_mean_load", "peak_retention_ratio",
            "maximum_real_ramp_retention_ratio", "hot_day_coverage", "ramp_tail_event_coverage", "compression_n_over_m")
    summaries = []
    for method in ("ordinary", "extreme_aware", "extreme_calibrated", "adoption_rule"):
        good = [r for r in results if r["method"] == method and r["status"] == "ok"]
        row = {"method": method, "successful_records": len(good),
               "failed_records": sum(r["method"] == method and r["status"] != "ok" for r in results)}
        for key in keys:
            values = [r["metrics"][key] for r in good if r["metrics"][key] is not None]
            row["median_" + key] = float(np.median(values)) if values else None
        summaries.append(row)
    base.write_json(OUT / "summary.json", {
        "generated_utc": base.utc(), "summaries": summaries,
        "candidate_adopted_cases": sum(d["statistical_candidate_adopted"] for d in decisions),
        "selected_passes_research_policy_cases": sum(d["selected_passes_research_policy"] for d in decisions),
        "fallback_cases": sum(not d["statistical_candidate_adopted"] for d in decisions),
        "LP_feasible_cases": sum(d["exact_matching_status"] == "feasible" for d in decisions),
        "LP_infeasible_cases": sum(d["exact_matching_status"] == "infeasible" for d in decisions),
        "LP_feasible_but_candidate_rejected_cases": sum(d["exact_matching_status"] == "feasible" and not d["statistical_candidate_adopted"] for d in decisions),
        "complete_day_filter_original_peak_loss_cases": sum(d["original_observed_scope"]["original_peak_lost_by_complete_day_filter"] for d in decisions),
        "timing": manifest, "engineering_operation_validated": False})
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
