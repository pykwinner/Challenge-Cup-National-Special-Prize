from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-heatplan"))
import matplotlib.pyplot as plt

from .experiments import (
    ablation_study,
    epsilon_constraint_pareto,
    northern_cold_wave_stress_test,
    northern_resilience_stress_suite,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Pareto and ablation experiments")
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeseries", required=True)
    parser.add_argument("--output", default="outputs/experiments")
    parser.add_argument("--pareto-points", type=int, default=7)
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    pareto = epsilon_constraint_pareto(args.config, args.timeseries, points=args.pareto_points)
    ablation = ablation_study(args.config, args.timeseries)
    stress = northern_cold_wave_stress_test(args.config, args.timeseries)
    resilience = northern_resilience_stress_suite(args.config, args.timeseries)
    pareto.to_csv(output / "pareto_front.csv", index=False)
    (output / "pareto_sampling_evidence.json").write_text(json.dumps(pareto.attrs,ensure_ascii=False,indent=2),encoding="utf-8")
    ablation.to_csv(output / "ablation.csv", index=False)
    stress.to_csv(output / "northern_cold_wave_stress.csv", index=False)
    resilience.to_csv(output / "northern_resilience_suite.csv", index=False)

    fig, ax = plt.subplots(figsize=(7, 5))
    emission_column = "total_service_emissions_kg_per_year"
    ax.plot(pareto[emission_column] / 1000, pareto["objective_yuan_per_year"] / 10000, "o-")
    compromise = pareto[pareto["recommended_compromise"]]
    ax.scatter(compromise[emission_column] / 1000, compromise["objective_yuan_per_year"] / 10000,
               s=150, marker="*", color="#d62728", label="closest normalized ideal", zorder=4)
    for i, row in pareto.iterrows():
        ax.annotate(str(i + 1), (row[emission_column] / 1000, row["objective_yuan_per_year"] / 10000))
    ax.set(xlabel="annual emissions (tCO2)", ylabel="annualized cost (10k CNY)", title="Cost-emission Pareto frontier")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output / "pareto_front.png", dpi=180)
    plt.close(fig)

    print(pareto[[emission_column, "objective_yuan_per_year", "marginal_abatement_cost_yuan_per_kg", "recommended_compromise"]].to_string(index=False))


if __name__ == "__main__":
    main()
