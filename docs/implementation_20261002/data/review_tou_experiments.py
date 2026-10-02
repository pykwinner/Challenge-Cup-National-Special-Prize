"""Independent TOU mapping/bill/RC/peak review, without importing heatplan.

Reads frozen results only, writes review_tou.json/CSV in the delegated data dir.
"""
from pathlib import Path
import json

import numpy as np
import pandas as pd

from review_base_experiments import ROOT, DATA, review, read, digest


def main():
    gate = read(DATA / "admission_tou.json")
    base = pd.read_csv(ROOT / "data/derived/evidence_case_20261002/wuhan_2024_2025_hourly.csv")
    tou = pd.read_csv(ROOT / gate["input_paths"]["timeseries"])
    table = pd.read_csv(ROOT / gate["input_paths"]["monthly_tariff"]).set_index("tariff_month")
    config = ROOT / gate["input_paths"]["rc_config"]
    assert config.read_bytes() == (ROOT / "configs/evidence_case_20261002/wuhan_bestest600_rc.yaml").read_bytes()
    same_columns = [column for column in base if column != "electricity_price"]
    pd.testing.assert_frame_equal(base[same_columns], tou[same_columns], check_dtype=False, rtol=0, atol=0)
    # Independent vectorized interval construction (not prepare_tou.hour_bucket).
    stamp = pd.to_datetime(tou.time, utc=True).dt.tz_convert("Asia/Shanghai")
    hours = stamp.dt.hour
    buckets = np.select([(hours >= 18) & (hours < 20),
                         ((hours >= 16) & (hours < 18)) | (hours >= 20),
                         ((hours >= 6) & (hours < 12)) | ((hours >= 14) & (hours < 16))],
                        ["sharp", "peak", "flat"], default="valley")
    month = stamp.dt.strftime("%Y-%m")
    assert set(month) == set(table.index)
    prices = np.array([table.loc[m, b + "_yuan_per_kwh"] for m, b in zip(month, buckets)])
    assert np.array_equal(buckets, tou.tariff_bucket)
    assert np.array_equal(month, tou.tariff_month)
    assert np.allclose(prices, tou.electricity_price, rtol=0, atol=1e-12)
    assert np.array_equal(tou.tariff_source_id, [table.loc[m, "source_id"] for m in month])
    assert np.array_equal(tou.tariff_pdf_page, [table.loc[m, "pdf_page_one_based"] for m in month])
    assert not gate["full_economics_admitted"] and not gate["account_eligibility_verified"] and not gate["grid_connection_feasibility_verified"]
    report = review(timeseries=gate["input_paths"]["timeseries"], config_path=gate["input_paths"]["rc_config"],
                    gate_file="admission_tou.json", runs=ROOT / "docs/implementation_20261002/reference_experiments_tou",
                    modes=("rc",), output_name="review_tou.json", variant="conditional_industrial_TOU")
    report["source_mapping_checks"] = {"physical_config_byte_identical": True, "all_non_price_base_columns_exact": True,
                                       "local_month_hour_mapping_recomputed": True, "monthly_rate_lookup_recomputed": True,
                                       "all_hourly_source_page_locators_match": True, "account_and_grid_unverified_explicit": True}
    assert len(report["stages"]) == 10
    assert not report["current_code_differences"]["rc"]
    peak = max(row["peak_import_kw"] for row in report["stages"])
    report["peak_import_review"] = {"max_across_all_stages_kw": peak, "below_100kw_policy_capacity_lower_edge": peak < 100,
                                    "actual_connection_and_power_factor_verified": False,
                                    "scope": "Only an active-power scale check; not an actual kVA rating/connection validation. Ideal model 1000kW upper bound is not the observed import."}
    assert peak < 100
    rows = {row["stage"]: row for row in report["stages"]}
    caps = report["controls"]["requested_epsilon_caps_kg"]
    front = []
    for index, requested in enumerate(caps):
        row = rows[f"epsilon_{index}"]
        effective = row["effective_emissions_cap_kg"]
        assert abs(effective - (requested + 1e-7 + 1e-8 * abs(requested))) < 1e-10
        front.append({"stage": row["stage"], "requested_cap_kg": requested, "effective_cap_kg": effective,
                      "cap_relaxation_kg": effective - requested,
                      "electricity_kwh": row["electricity_kwh"], "energy_charge_yuan": row["reference_energy_charge_yuan"],
                      "reference_co2_kg": row["reference_average_factor_co2_kg"]})
    frame = pd.DataFrame(front)
    assert (np.diff(frame.reference_co2_kg) > 1.).all()
    assert (np.diff(frame.energy_charge_yuan) < -.1).all()
    assert not report["controls"]["frontier_range_degenerate"]
    frame.to_csv(DATA / "review_tou_frontier.csv", index=False, float_format="%.12g")
    cost, low_co2 = rows["cost_endpoint"], rows["emission_endpoint"]
    saving = low_co2["reference_energy_charge_yuan"] - cost["reference_energy_charge_yuan"]
    extra = cost["electricity_kwh"] - low_co2["electricity_kwh"]
    report["frontier_review"] = {"five_epsilon_samples_strictly_ordered": True, "nondegenerate": True,
        "endpoint_cost_saving_yuan": saving, "endpoint_cost_saving_percent": saving / low_co2["reference_energy_charge_yuan"] * 100,
        "endpoint_extra_electricity_kwh": extra, "endpoint_extra_electricity_percent": extra / low_co2["electricity_kwh"] * 100,
        "endpoint_extra_reference_co2_kg": cost["reference_average_factor_co2_kg"] - low_co2["reference_average_factor_co2_kg"],
        "requested_vs_effective_cap": "Runner uses a numerical relaxation of 1e-7 + 1e-8 * abs(cap) (0.0000124–0.0000129kg here); all solutions satisfy effective caps. This is explicitly separated from requested caps.",
        "reported_frontier_scope": "Sampled operating electricity-energy-charge vs fixed-factor CO2 tradeoff under one conditional account, not global/whole-life economics or temporal marginal-carbon savings."}
    # Independent bucket energy/bill decomposition; physical H/C unchanged.
    by_bucket = []
    for stage in ("cost_endpoint", "emission_endpoint", "fixed_lower_temperature"):
        tr = pd.read_csv(ROOT / "docs/implementation_20261002/reference_experiments_tou/rc" / stage / "building_trajectory.csv")
        energy = tr.heat_kw.to_numpy() + tr.cooling_kw.to_numpy()
        for bucket in ("valley", "flat", "peak", "sharp"):
            selected = buckets == bucket
            by_bucket.append({"stage": stage, "bucket": bucket, "electricity_kwh": float(energy[selected].sum()),
                              "energy_charge_yuan": float(np.dot(energy[selected], prices[selected]))})
    pd.DataFrame(by_bucket).to_csv(DATA / "review_tou_bucket_bill.csv", index=False, float_format="%.12g")
    report["review_artifacts"] = {"frontier": "docs/implementation_20261002/data/review_tou_frontier.csv",
                                  "bucket_bill": "docs/implementation_20261002/data/review_tou_bucket_bill.csv"}
    report["review_artifact_sha256"] = {path: digest(ROOT / path) for path in report["review_artifacts"].values()}
    (DATA / "review_tou.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "stages": len(rows), "peak_import_review": report["peak_import_review"],
                      "frontier_review": report["frontier_review"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
