"""Summarize the frozen offline policy-weight experiment without rerunning it."""
import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("choose a new report directory")
    data = pd.read_csv(args.run / "policy_candidates.csv")
    # Each policy's cost/energy estimate is repeated for four budgets. Count it
    # once, keeping all eight policies rather than only the selected winner.
    data = data[data.budget_multiplier == 1.0].copy()
    if len(data) != 24 or data.duplicated(["method", "policy_delta_c"]).any():
        raise ValueError("expected three methods by eight distinct policies")
    measures = ["energy_kwh", "bill_yuan", "carbon_kg"]
    for key in measures:
        data[key + "_signed_error_pct"] = 100 * (data["estimated_" + key] / data["actual_" + key] - 1)
    metrics = []
    for method, frame in data.groupby("method", sort=False):
        row = {"method": method, "policy_count": len(frame)}
        for key in measures:
            error = frame[key + "_signed_error_pct"].abs()
            row[key + "_mean_absolute_percentage_error"] = float(error.mean())
            row[key + "_maximum_absolute_percentage_error"] = float(error.max())
        metrics.append(row)
    selections = pd.read_csv(args.run / "selections.csv")
    summary = {"scope": "same-season finite-library offline diagnostic; not investment improvement, out-of-sample validation or simulation speedup",
               "all_eight_policies_accounted": True, "method_metrics": metrics,
               "distinct_selected_policies": sorted(selections.selected_policy_delta_c.unique().tolist()),
               "decision_improvement_demonstrated": False,
               "candidate_file_sha256": hashlib.sha256((args.run / "policy_candidates.csv").read_bytes()).hexdigest(),
               "report_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    args.out.mkdir(parents=True)
    data.to_csv(args.out / "policy_estimation_errors.csv", index=False)
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.environ.setdefault("MPLCONFIGDIR", str(args.out / "matplotlib_cache"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = {"frequency": ("Frequency", "#748094"), "soft_energy": ("Soft energy", "#2878b5"),
              "hard_energy_minimum_W1": ("Hard energy + W1", "#159276")}
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    for axis, key, title in zip(axes, ["bill_yuan", "energy_kwh"], ["Electricity-bill estimate", "Electricity-use estimate"]):
        for method, (label, color) in labels.items():
            frame = data[data.method == method].sort_values("policy_delta_c")
            axis.plot(frame.policy_delta_c, frame[key + "_signed_error_pct"].abs(), marker="o", label=label, color=color, linewidth=2)
        axis.set(title=title, xlabel="Valley-period preheat increment (C)", ylabel="Absolute relative error (%)", xticks=range(8))
        axis.grid(alpha=.2)
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=9)
    fig.suptitle("Same eight policies, same actual days, different weights", fontsize=14)
    fig.savefig(args.out / "policy_estimation_errors.png", dpi=170)
    fig.savefig(args.out / "policy_estimation_errors.svg")
    plt.close(fig)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
