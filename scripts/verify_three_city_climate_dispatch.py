"""Independent export joins and result-table checks for the narrow climate case.

No solve/model builder is called. State/ledger convolution is reused from the
separate verifier; source-to-zone joins and table values are checked here.
"""
import argparse
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_three_city_climate_dispatch as base


def source_zone_checks(dispatch, trajectory, frame):
    result = {}
    time = pd.DatetimeIndex(pd.to_datetime(frame.time, utc=True))
    for category, column in (("source_heat", "heat_kw"), ("cooling_output", "cooling_kw")):
        part = dispatch.loc[dispatch.category == category].sort_values("time_index")
        result[category + "_rows"] = bool(len(part) == len(frame) and np.array_equal(part.time_index, np.arange(len(frame))))
        result[category + "_time"] = pd.DatetimeIndex(pd.to_datetime(part.time, utc=True)).equals(time)
        result[category + "_unit"] = bool((part.unit == "kW").all())
        result[category + "_single_asset"] = bool(part.asset.nunique() == 1 and part.node.nunique() == 1)
        result[category + "_zone_balance"] = bool(len(part) == len(trajectory) and np.allclose(part.value, trajectory[column], rtol=0, atol=1e-7))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    base.require(not args.out.exists(), "preserve existing audit")
    protocol = base.verify(args.run)
    prior = base.ROOT / protocol["reference_run"]
    base.verify(prior)
    rows = base.read(args.run / "comparison.json")
    checks, cases = {}, []
    checks["twelve_unique_rows"] = len(rows) == len({(r["city"], r["case"]) for r in rows}) == 12
    for city in base.CITIES:
        frame = pd.read_csv(prior / "results" / city / "chronology.csv")
        config = yaml.safe_load((prior / "results" / city / "common_config.yaml").read_text(encoding="utf-8"))
        base.chronology_check(frame, config, base.START, base.END)
        for row in [r for r in rows if r["city"] == city]:
            key = city + "/" + row["case"]
            extra = {}
            if row["case"] == "baseline_delta0":
                tr = pd.read_csv(prior / "results" / city / "baseline_delta0.csv")
            else:
                folder = args.run / "results" / city / row["case"]
                original = pd.read_csv(folder / "building_trajectory.csv")
                dispatch = pd.read_csv(folder / "dispatch.csv")
                extra = source_zone_checks(dispatch, original, frame)
                tr = original.rename(columns={"temperature_start_c": "T_start", "temperature_end_c": "T_end", "cooling_kw": "cool_kw"})
                # A source-flow tamper must fail even without consulting hashes.
                corrupt = dispatch.copy()
                index = corrupt.index[corrupt.category == "source_heat"][100]
                corrupt.loc[index, "value"] += .1
                extra["source_tamper_rejected"] = not all(source_zone_checks(corrupt, original, frame).values())
            independent = base.independent_metrics(config, frame, tr)
            for metric in ("bill_yuan", "energy_kwh", "carbon_kg", "peak_import_kw"):
                extra["table_" + metric] = bool(np.isclose(independent[metric], row[metric], rtol=1e-10, atol=1e-7))
            extra["reconstructed_state_and_ledger"] = independent["passed"]
            checks.update({key + ":" + name: value for name, value in extra.items()})
            cases.append({"case": key, "maximum_temperature_residual_c": independent["maximum_temperature_residual_c"], "checks": extra})
    report = {"passed": all(checks.values()), "check_count": len(checks),
        "failed_checks": [name for name, ok in checks.items() if not ok], "cases": cases,
        "original_inputs_and_outputs_modified": False, "source_tamper_examples": 9,
        "manifest_sha256": base.sha(args.run / "manifest.json"), "script_sha256": base.sha(Path(__file__)),
        "scope": "12 result rows plus 9 exported source-to-zone balances; fixed ideal single-source case only; no general network, investment, observed validation or new source-content audit"}
    base.save(args.out, report)
    print({k: report[k] for k in ("passed", "check_count", "failed_checks")})
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
