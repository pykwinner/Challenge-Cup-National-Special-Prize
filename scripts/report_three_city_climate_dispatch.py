"""Reproduce the all-city cost/carbon/peak comparison from frozen exported rows."""
import argparse
import json
import os
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_three_city_climate_dispatch import CITIES, read, require, save, sha, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    verify(args.run)
    require(not args.out.exists(), "preserve existing report")
    rows = read(args.run / "comparison.json")
    require(read(args.run / "manifest.json")["all_requested_cases_passed"], "incomplete run")
    require(len(rows) == 12 and all(row["passed"] for row in rows), "not all cases accepted")
    table, mechanisms = [], []
    for city in CITIES:
        cityrows = {row["case"]: row for row in rows if row["city"] == city}
        require(len(cityrows) == 4, "missing/duplicate case")
        baseline, cost, carbon, middle = [cityrows[key] for key in (
            "baseline_delta0", "cost_endpoint", "carbon_endpoint", "midpoint_cap")]
        denominator = baseline["bill_yuan"] - cost["bill_yuan"]
        require(denominator > 0, "undefined retained saving when maximum saving nonpositive")
        mechanisms.append({"city": city,
            "retained_saving_percent": 100 * (baseline["bill_yuan"] - middle["bill_yuan"]) / denominator,
            "carbon_span_fraction": (middle["carbon_kg"] - carbon["carbon_kg"]) / (cost["carbon_kg"] - carbon["carbon_kg"]),
            "maximum_cost_saving_percent": 100 * denominator / baseline["bill_yuan"],
            "midpoint_cost_saving_percent": 100 * (1 - middle["bill_yuan"] / baseline["bill_yuan"])})
        for case, row in cityrows.items():
            table.append({"city": city, "case": case,
                **{key: row[key] for key in ("bill_yuan", "energy_kwh", "carbon_kg", "peak_import_kw")},
                "cost_saving_percent": 100 * (1 - row["bill_yuan"] / baseline["bill_yuan"]),
                "carbon_increase_percent": 100 * (row["carbon_kg"] / baseline["carbon_kg"] - 1),
                "peak_ratio_to_baseline": row["peak_import_kw"] / baseline["peak_import_kw"],
                "result_attribute": row["result_attribute"],
                "solver_quality": row.get("solution_quality", {}).get("quality", "feasible feedback; not solver optimum"),
                "physical_and_ledger_passed": row["passed"]})
    args.out.mkdir(parents=True)
    frame = pd.DataFrame(table)
    frame.to_csv(args.out / "decision_table.csv", index=False)
    save(args.out / "mechanisms.json", mechanisms)
    os.environ.setdefault("MPLCONFIGDIR", str(args.out / "matplotlib_cache"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.6), constrained_layout=True)
    styles = {"baseline_delta0": ("o", "#7e8796", "Feedback baseline"),
              "carbon_endpoint": ("s", "#159276", "Carbon endpoint"),
              "midpoint_cap": ("D", "#2878b5", "Half carbon span"),
              "cost_endpoint": ("^", "#c56625", "Bill endpoint")}
    for axis, city, name in zip(axes, CITIES, ("Urumqi", "Guyuan", "Wuhan")):
        for case, (marker, color, label) in styles.items():
            row = frame[(frame.city == city) & (frame.case == case)].iloc[0]
            axis.scatter(row.carbon_increase_percent, row.cost_saving_percent, s=75, marker=marker, color=color, label=label, zorder=3)
        axis.set(title=name, xlabel="Reference CO2 change from baseline (%)", ylabel="Electricity-bill saving (%)")
        axis.set_xlim(-.3, 4.8)
        axis.set_ylim(-.10, 1.95)
        axis.grid(alpha=.2)
        axis.spines[["top", "right"]].set_visible(False)
        m = next(r for r in mechanisms if r["city"] == city)
        axis.text(.98, .08, f"Half carbon span retains\n{m['retained_saving_percent']:.1f}% of bill saving", transform=axis.transAxes, ha="right", fontsize=10)
    axes[0].legend(frameon=False, loc="upper left", fontsize=8)
    fig.suptitle("Same prototype, same reference prices, three continuous climate windows", fontsize=13)
    fig.savefig(args.out / "cost_carbon_tradeoff.png", dpi=170)
    fig.savefig(args.out / "cost_carbon_tradeoff.svg")
    plt.close(fig)
    save(args.out / "report_manifest.json", {"source_comparison_sha256": sha(args.run / "comparison.json"),
        "source_run_manifest_sha256": sha(args.run / "manifest.json"), "script_sha256": sha(Path(__file__)),
        "scope": "12 reference dispatch rows, 3x2880h common window; fixed ideal assets; common Hubei methodological price; no local tariff/field/investment claim",
        "figure_lines_interpolation": False,
        "files": {p.name: sha(p) for p in sorted(args.out.iterdir()) if p.is_file()}})
    print(json.dumps(mechanisms, ensure_ascii=False))


if __name__ == "__main__":
    main()
