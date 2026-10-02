"""Recompute the admitted one-zone ideal-source experiment from exported files.

No heatplan model, solver, IO, audit or accounting functions are imported. This
checker deliberately supports only this explicit one-zone/no-network reference
scope; it cannot certify a general equipment/network model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from math import exp
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/implementation_20261002/reference_experiments"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--timeseries", type=Path,
                        default=ROOT / "data/derived/evidence_case_20261002/wuhan_2024_2025_hourly.csv")
    parser.add_argument("--kinds", nargs="+", default=["rc", "steady"])
    parser.add_argument("--reference-import-upper-kw", type=float)
    args = parser.parse_args()
    frame = pd.read_csv(args.timeseries)
    stamps = pd.DatetimeIndex(pd.to_datetime(frame.time))
    expected = pd.date_range("2024-11-16T00:00:00+08:00", "2025-03-16T00:00:00+08:00", freq="h", inclusive="left")
    assert stamps.equals(expected)
    records = []
    for kind in args.kinds:
        manifest = json.loads((args.base / kind / "manifest.json").read_text(encoding="utf-8"))
        protocol = json.loads((args.base / kind / "protocol.json").read_text(encoding="utf-8"))
        assert digest(args.timeseries) in protocol["input_sha256"].values(), "timeseries differs from frozen run"
        for name, sha in manifest["files"].items():
            assert digest(args.base / kind / name) == sha, f"changed output: {kind}/{name}"
        paths = sorted((args.base / kind).glob("*/summary.json"))
        assert paths, "no experiment artifacts found"
        for path in paths:
            directory = path.parent
            config = yaml.safe_load((directory / "effective_config.yaml").read_text(encoding="utf-8"))
            summary = json.loads(path.read_text(encoding="utf-8"))
            dispatch = pd.read_csv(directory / "dispatch.csv")
            assert not config["pipes"] and not config["storages"] and len(config["sources"]) == 1
            source = config["sources"][0]
            assert source["type"] == "electric_boiler" and source["max_new_capacity_kw"] == 0
            cooling_spec = config["multi_energy_services"]["cooling"]["sources"][source["name"]]
            def values(category):
                rows = dispatch[dispatch.category == category].sort_values("time_index")
                assert len(rows) == len(frame) and np.array_equal(rows.time_index, np.arange(len(frame)))
                return rows["value"].to_numpy()
            heat, cool = values("source_heat"), values("cooling_output")
            electric = heat / source["efficiency"] + cool / cooling_spec["eer"]
            bill = float(np.dot(electric, frame.electricity_price))
            carbon = float(np.dot(electric, frame.grid_emission_factor))
            assert np.max(heat + cool - source["existing_capacity_kw"]) <= 1e-6
            assert np.min(heat) >= -1e-6 and np.min(cool) >= -1e-6
            if args.reference_import_upper_kw is not None:
                assert float(electric.max()) < args.reference_import_upper_kw
            assert abs(bill - summary["objective_yuan_per_year"]) < 1e-5
            assert abs(carbon - summary["total_service_emissions_kg_per_year"]) < 1e-5
            record = {"experiment": f"{kind}/{directory.name}", "hours": len(frame),
                      "heat_kwh": float(heat.sum()), "cooling_kwh": float(cool.sum()),
                      "electricity_kwh": float(electric.sum()), "reference_energy_charge_yuan": bill,
                      "maximum_ideal_source_electric_import_kw": float(electric.max()),
                      "reference_average_factor_co2_kg": carbon, "capacity_max_excess_kw": float(max(0, (heat + cool - source["existing_capacity_kw"]).max())),
                      "numerical_audit_passed": summary["numerical_audit_passed"]}
            if config.get("building_thermal", {}).get("enabled", False):
                building = config["building_thermal"]["buildings"][0]
                trajectory = pd.read_csv(directory / "building_trajectory.csv").sort_values("time_index")
                h, c = building["heat_loss_kw_per_c"], building["thermal_capacity_kwh_per_c"]
                a, b = exp(-h/c), (1-exp(-h/c))/h
                temperature = trajectory.temperature_end_c.to_numpy()
                previous = np.r_[building["initial_temperature_c"], temperature[:-1]]
                predicted = a*previous + (1-a)*frame.outdoor_temp + b*(heat-cool+frame.internal_gain_kw+frame.solar_gain_kw)
                residual = float(np.max(np.abs(predicted-temperature)))
                under = np.maximum(building["min_temperature_c"]-temperature,0)
                over = np.maximum(temperature-building["max_temperature_c"],0)
                assert residual < 1e-6 and max(under.max(),over.max()) < 1e-6
                assert abs(temperature[-1]-building["final_temperature_c"]) < 1e-6
                assert np.max(np.minimum(heat,cool)) < 1e-6
                assert np.max(np.abs(trajectory.heat_kw-heat)) < 1e-6
                assert np.max(np.abs(trajectory.cooling_kw-cool)) < 1e-6
                record.update(rc_max_residual_c=residual, max_temperature_c=float(temperature.max()),
                              min_temperature_c=float(temperature.min()), outside_comfort_degree_hours=float(under.sum()+over.sum()))
            else:
                assert np.max(np.abs(heat-frame.steady_heat_demand_kw)) < 1e-6
                assert np.max(np.abs(cool-frame.steady_cooling_demand_kw)) < 1e-6
            records.append(record)
    report = {"passed": True, "scope": "independent one-zone ideal-source chronology, RC, service, power, electricity bill and emissions audit",
              "empirical_validation": False, "whole_engineering_economics": False,
              "cases_checked": len(records), "records": records,
              "verifier_sha256": digest(Path(__file__))}
    report["timeseries"] = str(args.timeseries)
    report["timeseries_sha256"] = digest(args.timeseries)
    report["reference_import_screen_upper_kw"] = args.reference_import_upper_kw
    report["actual_grid_connection_verified"] = False
    (args.base / "independent_verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    pd.DataFrame(records).to_csv(args.base / "comparison.csv", index=False)
    print(json.dumps({"passed":True,"cases_checked":len(records)},ensure_ascii=False))


if __name__ == "__main__":
    main()
