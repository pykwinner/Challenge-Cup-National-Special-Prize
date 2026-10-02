from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-heatplan"))
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)


def architecture() -> None:
    fig, ax = plt.subplots(figsize=(11, 5.8))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 7)
    ax.axis("off")
    boxes = [
        (0.4, 3.1, 2.2, 1.4, "Sources", "waste heat / CHP\nheat pump / boiler", "#dcefe3"),
        (3.4, 3.1, 2.2, 1.4, "Network", "bidirectional pipes\ncold-dependent loss", "#dce8f4"),
        (6.4, 3.1, 2.2, 1.4, "Loads", "radiator / floor heat\nbuilding inertia", "#f9ebd2"),
        (9.4, 3.1, 2.2, 1.4, "Storage", "daily TES / PCM\nseasonal BTES", "#eadff3"),
    ]
    for x, y, w, h, title, detail, color in boxes:
        patch = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.08",
                               linewidth=1.5, edgecolor="#29465b", facecolor=color)
        ax.add_patch(patch)
        ax.text(x + w / 2, y + 0.96, title, ha="center", va="center", fontsize=13, fontweight="bold")
        ax.text(x + w / 2, y + 0.42, detail, ha="center", va="center", fontsize=10)
    for start, end in [((2.6, 3.8), (3.4, 3.8)), ((5.6, 3.8), (6.4, 3.8)), ((8.6, 3.8), (9.4, 3.8))]:
        ax.annotate("", xy=end, xytext=start, arrowprops=dict(arrowstyle="->", lw=2, color="#29465b"))
    ax.annotate("", xy=(6.4, 3.35), xytext=(9.4, 3.35), arrowprops=dict(arrowstyle="<->", lw=1.7, color="#6a4c93"))

    dimensions = [
        (0.8, 5.6, "GRADE", "temperature/exergy matching"),
        (3.7, 5.6, "TIME", "hourly + seasonal linking"),
        (6.6, 5.6, "SPACE", "topology + pipe sizing"),
        (9.5, 5.6, "RISK", "cold wave + reserve margin"),
    ]
    for x, y, title, detail in dimensions:
        ax.text(x, y, title, fontsize=11, fontweight="bold", color="#0b4f6c")
        ax.text(x, y - 0.38, detail, fontsize=9.3, color="#444444")
    ax.text(6, 6.65, "Northern source-network-load-storage planning framework", ha="center",
            fontsize=17, fontweight="bold", color="#17324d")
    ax.text(6, 0.75, "Planning MILP  →  day-ahead robust dispatch  →  rolling MPC correction",
            ha="center", fontsize=12, color="#17324d")
    fig.tight_layout()
    fig.savefig(OUT / "northern_framework.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def heat_pump_curve() -> None:
    with (ROOT / "data" / "example_city.yaml").open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    hp = next(x for x in config["sources"] if x["name"] == "distributed_air_hp_b")
    temperatures = np.arange(-30, 6, 1)
    supply_k = 45 + 273.15
    eta = float(hp["carnot_efficiency"])
    cop = eta * supply_k / np.maximum(supply_k - (temperatures + 273.15), 4.0)
    band = hp["defrost_temperature_band_c"]
    cop[(temperatures >= band[0]) & (temperatures <= band[1])] *= float(hp["defrost_cop_multiplier"])
    cop = np.clip(cop, hp["cop_min"], hp["cop_max"])
    d = hp["low_temperature_derating"]
    factor = 1 - np.maximum(0, d["threshold_c"] - temperatures) * d["slope_per_c"]
    factor = np.clip(factor, d["minimum_factor"], 1.0)
    factor[temperatures < hp["cutoff_temperature_c"]] = 0.0

    fig, ax1 = plt.subplots(figsize=(8.6, 4.8))
    ax2 = ax1.twinx()
    ax1.plot(temperatures, cop, color="#1f77b4", lw=2.5, label="COP")
    ax2.plot(temperatures, factor * 100, color="#d95f02", lw=2.5, label="available capacity")
    ax1.axvline(-7, color="#777777", ls="--", lw=1)
    ax1.axvline(-25, color="#9b1c1c", ls=":", lw=1.5)
    ax1.set(xlabel="outdoor temperature (°C)", ylabel="COP", title="Air-source heat pump performance in severe cold")
    ax2.set_ylabel("available capacity (%)")
    ax1.grid(alpha=0.22)
    lines = ax1.get_lines()[:1] + ax2.get_lines()[:1]
    ax1.legend(lines, [x.get_label() for x in lines], loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "air_hp_cold_performance.png", dpi=200)
    plt.close(fig)


def stress_and_ablation() -> None:
    stress = pd.read_csv(ROOT / "outputs" / "experiments" / "northern_cold_wave_stress.csv")
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.2))
    labels = ["Nominal", "Cold wave"]
    axes[0].bar(labels, stress["objective_yuan_per_year"] / 10000, color=["#4c78a8", "#e45756"])
    axes[0].set_title("Annualized cost")
    axes[0].set_ylabel("10k CNY")
    axes[1].bar(labels, stress["emissions_kg_per_year"] / 1000, color=["#4c78a8", "#e45756"])
    axes[1].set_title("Carbon emissions")
    axes[1].set_ylabel("tCO2")
    axes[2].bar(labels, stress["clean_heat_share"] * 100, color=["#4c78a8", "#e45756"])
    axes[2].set_title("Clean heat share")
    axes[2].set_ylabel("%")
    axes[2].set_ylim(0, 105)
    fig.suptitle("Frozen-investment compound cold-wave stress test", fontsize=15, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "cold_wave_stress.png", dpi=200)
    plt.close(fig)

    ablation = pd.read_csv(ROOT / "outputs" / "experiments" / "ablation.csv")
    names = ["Full model", "No exergy", "No DR", "No new TES"]
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    bars = ax.bar(names, ablation["objective_yuan_per_year"] / 10000,
                  color=["#2a9d8f", "#8ecae6", "#ffb703", "#e76f51"])
    ax.bar_label(bars, fmt="%.1f", padding=3)
    ax.set(ylabel="annualized cost (10k CNY)", title="Ablation study: contribution of key modules")
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    fig.savefig(OUT / "ablation_cost.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    architecture()
    heat_pump_curve()
    stress_and_ablation()
