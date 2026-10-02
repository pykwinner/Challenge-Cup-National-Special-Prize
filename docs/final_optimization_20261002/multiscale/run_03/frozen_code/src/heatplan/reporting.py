from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-heatplan"))
import matplotlib.pyplot as plt
import pandas as pd


def export_results(output_dir: str | Path, artifacts: Any, config: dict, timeseries: pd.DataFrame) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(artifacts.summary, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts.investments.to_csv(output / "investments.csv", index=False)
    artifacts.dispatch.to_csv(output / "dispatch.csv", index=False)
    independent_audit = getattr(artifacts, "building_independent_audit", None)
    if independent_audit is not None:
        (output / "building_independent_audit.json").write_text(json.dumps(independent_audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for field in ("building_trajectory","building_comfort"):
        table = getattr(artifacts,field,None)
        if table is not None:
            table.to_csv(output / f"{field}.csv",index=False)
    plot_dispatch(output / "dispatch.png", artifacts.dispatch, config, timeseries)
    plot_network(output / "network.png", artifacts.investments, config)


def _configured_demand(config: dict, block: pd.DataFrame, scenario: str) -> pd.Series:
    """Reconstruct the demand seen by the model before demand response.

    This includes intermittent schedules and user-behaviour multipliers.  The
    previous plot summed raw CSV columns, which overstated demand in the third
    archetype and mixed all scenario blocks on one axis.
    """
    demand = pd.Series(0.0, index=block.index, dtype=float)
    thermal = config.get("building_thermal", {})
    covered = {(str(b["node"]), str(b["heat_grade"])) for b in thermal.get("buildings", [])} if thermal.get("enabled", False) else set()
    for node in config["nodes"]:
        intermittent = node.get("intermittent_heating", {})
        behavior = node.get("user_behavior", {})
        for grade, column in node.get("demand_columns", {}).items():
            if (str(node["name"]), str(grade)) in covered:
                continue
            values = block[column].astype(float).copy()
            schedule_column = intermittent.get("schedule_column")
            if schedule_column:
                occupancy = block[schedule_column].astype(float).clip(0.0, 1.0)
                setback = float(intermittent.get("setback_fraction", 0.35))
                values *= setback + (1.0 - setback) * occupancy
            multiplier_column = behavior.get("multiplier_column")
            if multiplier_column:
                values *= block[multiplier_column].astype(float)
            values *= float(behavior.get("scenario_multipliers", {}).get(scenario, 1.0))
            demand += values
    return demand.reset_index(drop=True)


def plot_dispatch(path: Path, dispatch: pd.DataFrame, config: dict, timeseries: pd.DataFrame) -> None:
    scenarios = list(dict.fromkeys(dispatch.get("scenario", pd.Series(["base"])).astype(str)))
    fig, axes = plt.subplots(
        len(scenarios), 1, figsize=(10, max(4.0, 3.6 * len(scenarios))), squeeze=False
    )
    for ax, scenario in zip(axes[:, 0], scenarios):
        scenario_dispatch = dispatch[
            (dispatch["category"] == "source_heat")
            & (dispatch.get("scenario", "base").astype(str) == scenario)
        ]
        source = scenario_dispatch.pivot_table(
            index="time_index", columns="asset", values="value", aggfunc="sum", fill_value=0
        ).sort_index()
        if "scenario" in timeseries.columns:
            block = timeseries[timeseries["scenario"].astype(str) == scenario].copy()
        else:
            block = timeseries.copy()
        demand = _configured_demand(config, block.reset_index(drop=True), scenario)
        rc = config.get("building_thermal", {}).get("enabled", False)
        if rc:
            delivered = dispatch[(dispatch["category"] == "building_heat") & (dispatch["scenario"].astype(str) == scenario)].groupby("time_index")["value"].sum()
            demand += delivered.reindex(block.index, fill_value=0.0).to_numpy()
        if not source.empty:
            x = range(len(source))
            ax.stackplot(
                x, [source[column].to_numpy() for column in source],
                labels=list(source.columns), alpha=0.8,
            )
        ax.plot(range(len(demand)), demand, color="black", linewidth=2, label="actual building heat + fixed service demand" if rc else "configured demand")
        ax.set(
            xlabel="local time index",
            ylabel="heat power (kW)",
            title=f"Source dispatch and demand — {scenario}",
        )
        ax.legend(loc="upper left", ncol=2, fontsize=8)
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_network(path: Path, investments: pd.DataFrame, config: dict) -> None:
    nodes = {n["name"]: n for n in config["nodes"]}
    selected = set(
        investments.loc[(investments["category"] == "pipe_choice") & (investments["value"] > 0.5), "asset"]
    )
    fig, ax = plt.subplots(figsize=(7, 5))
    for pipe in config["pipes"]:
        is_existing = float(pipe.get("existing_capacity_kw", 0)) > 0
        is_selected = pipe["name"] in selected
        if not (is_existing or is_selected):
            continue
        a, b = nodes[pipe["from"]], nodes[pipe["to"]]
        ax.plot([a["x"], b["x"]], [a["y"], b["y"]], linewidth=3 if is_selected else 2,
                linestyle="-" if is_existing else "--", color="#d95f02" if is_selected else "#4c78a8")
        ax.text((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2, pipe["name"], fontsize=8)
    for name, node in nodes.items():
        ax.scatter(node["x"], node["y"], s=180, color="#2a9d8f", edgecolor="white", zorder=3)
        ax.text(node["x"], node["y"] + 0.08, name, ha="center", fontsize=9)
    ax.set_title("Selected district heating network")
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
