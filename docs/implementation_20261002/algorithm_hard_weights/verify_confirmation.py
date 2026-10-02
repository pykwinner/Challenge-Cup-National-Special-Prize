"""Independent raw-CSV/CDF audit of the separate hard-weight confirmation."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
# Reuse independent CDF integration/check helpers, never benchmark adapters.
spec = importlib.util.spec_from_file_location("independent_cdf_audit", OUT.parent / "algorithm/verify_confirmation.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def main():
    lock, sample = audit.read(OUT / "protocol_lock.json"), audit.read(OUT / "selection.json")
    for item in lock["files"]:
        assert audit.sha(ROOT / item["path"]) == item["sha256"], item["path"]
    for item in lock["sources"].values():
        assert audit.sha(Path(item["path"])) == item["sha256"], item["path"]
    manifest = audit.read(OUT / "run_manifest.json")
    for item in manifest["artifacts"]:
        assert audit.sha(ROOT / item["path"]) == item["sha256"], item["path"]
    previous = set(sample["development_buildings"])
    assert all(c["building_id"] not in previous for c in sample["cases"])
    assert len({c["building_id"] for c in sample["cases"]}) == len(sample["cases"])
    data = Path(lock["sources"]["metadata.csv"]["path"]).parent
    weather = pd.read_csv(data / "weather.csv", usecols=["timestamp", "site_id", "airTemperature"])
    weather["timestamp"] = pd.to_datetime(weather["timestamp"])
    meters = {}
    for category in {c["category"] for c in sample["cases"]}:
        ids = [c["building_id"] for c in sample["cases"] if c["category"] == category]
        meters[category] = pd.read_csv(data / f"{category}_cleaned.csv", usecols=["timestamp"]+ids)
        meters[category]["timestamp"] = pd.to_datetime(meters[category]["timestamp"])
    records = audit.read(OUT / "results.json")
    lookup = {(r["case_id"], r["method"]): r for r in records}
    verified, lp_verified = [], 0
    for case in sample["cases"]:
        folder = OUT / "cases" / case["case_id"] / "2017"
        source = meters[case["category"]].merge(
            weather.loc[weather.site_id == case["site_id"], ["timestamp", "airTemperature"]],
            on="timestamp", how="left", validate="one_to_one")
        source = source.loc[source.timestamp.dt.year == 2017].set_index("timestamp")
        source = source.reindex(pd.date_range("2017-01-01", "2018-01-01", freq="h", inclusive="left"))
        raw = source[case["building_id"]].to_numpy(dtype=float)
        temp = source.airTemperature.to_numpy(dtype=float)
        valid = np.isfinite(raw) & (raw >= 0)
        joint = valid & np.isfinite(temp)
        keep = np.repeat(joint.reshape(-1, 24).all(axis=1), 24)
        retained, times = raw[keep], source.index.to_numpy()[keep]
        saved = pd.read_csv(folder / "observed_calendar.csv")
        assert np.array_equal(raw, saved.energy_kwh.to_numpy(), equal_nan=True)
        assert np.array_equal(temp, saved.outdoor_temp.to_numpy(), equal_nan=True)
        assert np.array_equal(keep, saved.retained_complete_day.to_numpy())
        scope = audit.read(folder / "original_observed_scope_audit.json")
        audit.close(float(raw[valid].sum()-retained.sum()), scope["observed_energy_excluded_kwh"], "raw excluded energy")
        original = lookup[(case["case_id"], "extreme_aware")]["selection"]
        for method in ("extreme_aware", "extreme_calibrated", "hard_energy_minimum_W1", "adoption_soft", "adoption_hard"):
            record = lookup[(case["case_id"], method)]
            if record["status"] != "ok":
                verified.append({"case_id": case["case_id"], "method": method, "status": "failure_retained"})
                continue
            selection = record.get("selection", {"days": original["days"],
                                   "weights": record.get("adoption", {}).get("selected_weights")})
            days, weights = selection["days"], np.asarray(selection["weights"], dtype=float)
            positions = np.concatenate([np.arange(d*24, (d+1)*24) for d in days])
            selected, masses = retained[positions], np.repeat(weights, 24)
            assert len(days) == original["actual_m"] == record["actual_m"]
            audit.close(float(weights.sum()), len(retained)/24, "day sum")
            metric = record["metrics"]
            bias = abs(float(selected @ masses / retained.sum()-1)) if retained.sum() else None
            audit.close(bias, metric["retained_scope_abs_energy_bias"], method+" energy")
            distance = audit.cdf_w1(retained, selected, masses) / retained.mean() if retained.mean() else None
            audit.close(distance, metric["ldc_w1_over_mean_load"], method+" weighted CDF-W1")
            audit.close(float(selected.max()/retained.max()) if retained.max() else None,
                        metric["peak_retention_ratio"], method+" peak")
            adjacent = np.diff(times) == np.timedelta64(1, "h")
            jumps = np.abs(np.diff(retained))
            selected_mask = np.zeros(len(retained), dtype=bool)
            selected_mask[positions] = True
            chosen_edges = adjacent & selected_mask[:-1] & selected_mask[1:]
            maximum = float(jumps[adjacent].max()) if adjacent.any() else 0
            chosen = float(jumps[chosen_edges].max()) if chosen_edges.any() else 0
            audit.close(chosen/maximum if maximum else None,
                        metric["maximum_real_ramp_retention_ratio"], method+" actual ramp")
            if "adoption" not in record:
                frame = pd.read_csv(folder / f"{method}_actual_days.csv")
                assert np.array_equal(frame.load_kw.to_numpy(), selected), method+" changed raw observations"
            else:
                decision = record["adoption"]
                assert decision["statistical_candidate_adopted"] == (not decision["candidate_rejection_reasons"])
                if decision["statistical_candidate_adopted"]:
                    actual = lookup[(case["case_id"], decision["candidate_method"])]
                    assert decision["selected_weights"] == actual["selection"]["weights"]
                else:
                    assert decision["selected_weights"] == original["weights"]
                if scope["original_peak_lost_by_complete_day_filter"]:
                    assert not decision["selected_passes_research_policy"]
            if method == "hard_energy_minimum_W1":
                lower = np.array([1 if original["reasons"].get(str(d), "") else 1e-6 for d in days])
                assert (weights >= lower-1e-8).all()
                assert bias <= 1e-8
                diagnostic = record["hard_LP_diagnostics"]
                audit.close(distance, diagnostic["normalized_W1"], "LP independently recomputed CDF objective")
                lp_verified += 1
            verified.append({"case_id": case["case_id"], "method": method, "status": "verified"})
    output = {"status": "passed", "source_files_verified": len(lock["sources"]),
              "new_buildings": len(sample["cases"]), "method_records_verified": len(verified),
              "hard_LP_witnesses_verified": lp_verified,
              "verifier_sha256": audit.sha(Path(__file__)),
              "independent_CDF_helper_sha256": audit.sha(OUT.parent / "algorithm/verify_confirmation.py"),
              "method": "direct CSV reconstruction and independent weighted CDF integration; no experiment adapter",
              "engineering_operation_validated": False, "records": verified}
    (OUT / "independent_verification.json").write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in output.items() if k != "records"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
