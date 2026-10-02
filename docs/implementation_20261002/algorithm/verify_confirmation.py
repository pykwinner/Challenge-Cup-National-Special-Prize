"""Independent source-to-result audit; does not import the experiment adapter.

Recomputes complete days, weighted CDF-W1, energy, peaks and true adjacent
ramps directly from the source CSVs. It does not rerun KMeans or engineering.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def close(actual, expected, label):
    if expected is None:
        if actual is not None:
            raise AssertionError(f"{label}: expected null, got {actual}")
    elif actual is None or not np.isclose(actual, expected, rtol=1e-11, atol=1e-11):
        raise AssertionError(f"{label}: recomputed {actual}, saved {expected}")


def cdf_w1(original, selected, selected_weights):
    # Integrate absolute CDF difference on the union of values. This does not
    # call scipy's Wasserstein routine used by the benchmark.
    order = np.argsort(original, kind="stable")
    observed = original[order]
    order = np.argsort(selected, kind="stable")
    chosen, mass = selected[order], selected_weights[order]
    support = np.unique(np.concatenate((observed, chosen)))
    if len(support) < 2:
        return 0.0
    left = support[:-1]
    f = np.searchsorted(observed, left, side="right") / len(observed)
    cumulative = np.concatenate(([0.0], np.cumsum(mass)))
    g = cumulative[np.searchsorted(chosen, left, side="right")] / cumulative[-1]
    return float(np.sum(np.abs(f-g) * np.diff(support)))


def main():
    lock, cases = read(OUT / "protocol_lock.json"), read(OUT / "selection.json")["cases"]
    for record in lock["files"]:
        assert sha(ROOT / record["path"]) == record["sha256"], record["path"]
    for record in lock["sources"].values():
        assert sha(Path(record["path"])) == record["sha256"], record["path"]
    run = read(OUT / "run_manifest.json")
    for item in run["artifacts"]:
        assert sha(ROOT / item["path"]) == item["sha256"], item["path"]
    data = Path(lock["sources"]["metadata.csv"]["path"]).parent
    weather = pd.read_csv(data / "weather.csv", usecols=["timestamp", "site_id", "airTemperature"])
    weather["timestamp"] = pd.to_datetime(weather["timestamp"])
    meters = {}
    for category in dict.fromkeys(c["category"] for c in cases):
        ids = [c["building_id"] for c in cases if c["category"] == category]
        meters[category] = pd.read_csv(data / f"{category}_cleaned.csv", usecols=["timestamp"]+ids)
        meters[category]["timestamp"] = pd.to_datetime(meters[category]["timestamp"])
    records = read(OUT / "results.json")
    lookup = {(r["case_id"], r["method"]): r for r in records}
    verified = []
    for case in cases:
        folder = OUT / "cases" / case["case_id"] / str(case["year"])
        source = meters[case["category"]].merge(
            weather.loc[weather.site_id == case["site_id"], ["timestamp", "airTemperature"]],
            on="timestamp", how="left", validate="one_to_one")
        year = source.loc[source.timestamp.dt.year == case["year"]].copy()
        year = year.set_index("timestamp").reindex(pd.date_range(f"{case['year']}-01-01", f"{case['year']+1}-01-01", freq="h", inclusive="left"))
        load = year[case["building_id"]].to_numpy(dtype=float)
        temp = year.airTemperature.to_numpy(dtype=float)
        meter_valid = np.isfinite(load) & (load >= 0)
        joint = meter_valid & np.isfinite(temp)
        keep = np.repeat(joint.reshape(-1, 24).all(axis=1), 24)
        retained, times = load[keep], year.index.to_numpy()[keep]
        saved_calendar = pd.read_csv(folder / "observed_calendar.csv")
        assert np.array_equal(load, saved_calendar.energy_kwh.to_numpy(), equal_nan=True)
        assert np.array_equal(temp, saved_calendar.outdoor_temp.to_numpy(), equal_nan=True)
        assert np.array_equal(keep, saved_calendar.retained_complete_day.to_numpy())
        coverage = read(folder / "coverage.json")
        assert int(keep.sum()) == coverage["retained_hours"]
        scope = read(folder / "original_observed_scope_audit.json")
        close(float(load[meter_valid].sum()-retained.sum()), scope["observed_energy_excluded_kwh"], "filtered energy")
        assert int((meter_valid & ~keep).sum()) == scope["valid_meter_hours_excluded_by_complete_day_filter"]
        original = lookup[(case["case_id"], "extreme_aware")]["selection"]
        for method in ("ordinary", "extreme_aware", "extreme_calibrated", "adoption_rule"):
            record = lookup[(case["case_id"], method)]
            if record["status"] != "ok":
                verified.append({"case_id": case["case_id"], "method": method, "status": "failure_retained"})
                continue
            selection = record["selection"] if method != "adoption_rule" else {
                "days": original["days"], "weights": record["adoption"]["selected_weights"]}
            days = selection["days"]
            weights = np.asarray(selection["weights"], dtype=float)
            indices = np.concatenate([np.arange(d*24, (d+1)*24) for d in days])
            selected = retained[indices]
            hour_weights = np.repeat(weights, 24)
            assert len(days) == record["actual_m"] == lookup[(case["case_id"], "extreme_aware")]["actual_m"]
            close(float(weights.sum()), len(retained)/24, "weight conservation")
            metric = record["metrics"]
            bias = abs(float(selected @ hour_weights / retained.sum()-1)) if retained.sum() else None
            close(bias, metric["retained_scope_abs_energy_bias"], method+" energy bias")
            distance = cdf_w1(retained, selected, hour_weights) / retained.mean() if retained.mean() else None
            close(distance, metric["ldc_w1_over_mean_load"], method+" CDF-W1")
            close(float(selected.max()/retained.max()) if retained.max() else None, metric["peak_retention_ratio"], method+" peak")
            adjacent = np.diff(times) == np.timedelta64(1, "h")
            jumps = np.abs(np.diff(retained))
            selected_hours = np.zeros(len(retained), dtype=bool)
            selected_hours[indices] = True
            chosen_edges = adjacent & selected_hours[:-1] & selected_hours[1:]
            maximum = float(jumps[adjacent].max()) if adjacent.any() else 0
            chosen_ramp = float(jumps[chosen_edges].max()) if chosen_edges.any() else 0
            close(chosen_ramp/maximum if maximum else None, metric["maximum_real_ramp_retention_ratio"], method+" true ramp")
            if method != "adoption_rule":
                saved = pd.read_csv(folder / f"{method}_actual_days.csv")
                assert np.array_equal(saved.load_kw.to_numpy(), selected), method+" changed source observations"
            verified.append({"case_id": case["case_id"], "method": method, "status": "verified"})
        diagnostic = read(folder / "exact_matching_feasibility.json")
        if diagnostic["status"] == "feasible":
            witness = np.asarray(diagnostic["witness_weights"])
            daily = retained.reshape(-1, 24).sum(axis=1)[original["days"]]
            close(float(daily @ witness / retained.sum()), 1, "LP exact matching witness")
            close(float(witness.sum()), len(retained)/24, "LP day sum")
            lower = np.array([1 if original["reasons"].get(str(d), "") else 1e-6 for d in original["days"]])
            assert (witness >= lower-1e-8).all()
        decision = read(folder / "adoption_rule_result.json")
        assert decision["statistical_candidate_adopted"] == (not decision["candidate_rejection_reasons"])
        if not decision["statistical_candidate_adopted"]:
            assert decision["selected_weights"] == original["weights"]
        if scope["original_peak_lost_by_complete_day_filter"]:
            assert not decision["selected_passes_research_policy"]
    output = {"status": "passed", "source_files_verified": len(lock["sources"]),
              "case_count": len(cases), "method_records_verified": len(verified),
              "verifier_sha256": sha(Path(__file__)),
              "method": "independent CSV adapter and weighted CDF integration; no experiment or scipy W1 import",
              "engineering_operation_validated": False, "records": verified}
    (OUT / "independent_verification.json").write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in output.items() if k != "records"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
