from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "v11_1_figures"
RESULTS = ROOT / "outputs" / "v11_1_heat_metering_demo"


def main() -> None:
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    investments = pd.read_csv(RESULTS / "investments.csv")
    config = yaml.safe_load(
        (ROOT / "data" / "v11_heat_metering_demo.yaml").read_text(encoding="utf-8")
    )
    metering_rows = investments[
        investments["category"] == "heat_metering_coverage"
    ].set_index("asset")
    node_specs = {
        str(node["name"]): node["heat_metering"]
        for node in config["nodes"]
        if "heat_metering" in node
    }

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "SimHei",
        "Arial",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(13.5, 7.6), facecolor="#F3F7FA")
    grid = fig.add_gridspec(
        2, 3, width_ratios=[0.88, 1.25, 1.05], hspace=0.42, wspace=0.36
    )
    axes = [
        fig.add_subplot(grid[:, 0]),
        fig.add_subplot(grid[0, 1:]),
        fig.add_subplot(grid[1, 1]),
        fig.add_subplot(grid[1, 2]),
    ]
    for ax in axes:
        ax.set_facecolor("#FFFFFF")
        for spine in ax.spines.values():
            spine.set_color("#DCE5EC")

    fig.suptitle(
        "V11.1 供热计量与按热收费情景 · 结果看板",
        fontsize=21,
        fontweight="bold",
        color="#17324D",
        x=0.055,
        ha="left",
        y=0.98,
    )

    coverage = float(summary["heat_metering_coverage_fraction"])
    ax = axes[0]
    ax.pie(
        [coverage, 1.0 - coverage],
        startangle=90,
        counterclock=False,
        colors=["#2E74B5", "#E4EBF1"],
        wedgeprops={"width": 0.28, "edgecolor": "white"},
    )
    ax.text(
        0,
        0.06,
        f"{coverage:.0%}",
        ha="center",
        va="center",
        fontsize=30,
        fontweight="bold",
        color="#17324D",
    )
    ax.text(0, -0.18, "系统加权覆盖率", ha="center", fontsize=11, color="#5B6573")
    ax.set_title("计量覆盖", fontsize=13, fontweight="bold", color="#17324D", pad=18)
    ax.text(
        0,
        -1.23,
        "项目约束 ≥ 60%\n覆盖率按基准热需求加权",
        ha="center",
        va="top",
        fontsize=10.5,
        color="#64748B",
        linespacing=1.5,
    )

    ax = axes[1]
    gross = float(summary["demand_kwh_per_year"]) / 1e6
    effective = float(summary["effective_heat_demand_kwh_per_year"]) / 1e6
    saved = float(summary["metered_heat_savings_kwh_per_year"]) / 1e6
    bars = ax.barh(
        ["计量后有效需求", "基准热需求"],
        [effective, gross],
        color=["#1E8E6E", "#9CB7CC"],
        height=0.48,
    )
    ax.set_title("热需求变化", fontsize=13, fontweight="bold", color="#17324D", loc="left")
    ax.set_xlabel("GWh / 年", color="#5B6573")
    ax.grid(axis="x", alpha=0.18)
    ax.set_xlim(0, gross * 1.18)
    for bar, value in zip(bars, [effective, gross]):
        ax.text(
            value + gross * 0.018,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.3f}",
            va="center",
            fontsize=11,
            color="#334155",
            fontweight="bold",
        )
    ax.text(
        0.98,
        0.88,
        f"节热 {saved:.3f} GWh / 年（{saved / gross:.1%}）",
        transform=ax.transAxes,
        ha="right",
        fontsize=11,
        color="#1E8E6E",
        fontweight="bold",
        bbox={
            "boxstyle": "round,pad=0.32",
            "facecolor": "#E7F5EF",
            "edgecolor": "none",
        },
    )

    ax = axes[2]
    names = list(node_specs)
    existing = np.array(
        [float(node_specs[name]["existing_coverage_fraction"]) for name in names]
    )
    addition = np.array(
        [float(metering_rows.loc[name, "value"]) for name in names]
    )
    y = np.arange(len(names))
    ax.barh(y, existing, color="#9CB7CC", height=0.5, label="既有")
    ax.barh(y, addition, left=existing, color="#7A5AF8", height=0.5, label="新增")
    ax.set_yticks(y, [name.replace("district_", "片区 ").upper() for name in names])
    ax.set_xlim(0, 1.0)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0", "25%", "50%", "75%", "100%"])
    ax.set_title("节点改造组合", fontsize=13, fontweight="bold", color="#17324D", loc="left")
    ax.grid(axis="x", alpha=0.16)
    ax.legend(frameon=False, ncol=2, loc="lower right", fontsize=9.5)
    for index, total in enumerate(existing + addition):
        ax.text(total + 0.02, index, f"{total:.1%}", va="center", fontsize=10, color="#334155")

    ax = axes[3]
    bill = float(summary["average_indicative_household_heat_bill_yuan_per_year"])
    cap = float(config["policy"]["maximum_average_household_heat_bill_yuan_per_year"])
    ax.barh([0], [cap], color="#E4EBF1", height=0.34)
    ax.barh([0], [bill], color="#D97706", height=0.34)
    ax.axvline(cap, color="#9A3412", linewidth=1.4, linestyle="--")
    ax.set_xlim(0, cap * 1.08)
    ax.set_yticks([])
    ax.set_title("户均热费可负担性", fontsize=13, fontweight="bold", color="#17324D", loc="left")
    ax.set_xlabel("元 / 户·年", color="#5B6573")
    ax.grid(axis="x", alpha=0.16)
    ax.text(
        bill,
        0,
        f"  {bill:,.0f}",
        va="center",
        ha="left",
        fontsize=12,
        fontweight="bold",
        color="#9A3412",
    )
    ax.text(
        0.99,
        0.08,
        f"情景上限 {cap:,.0f} 元 / 户·年",
        transform=ax.transAxes,
        ha="right",
        fontsize=10.5,
        color="#64748B",
    )

    fig.text(
        0.055,
        0.025,
        "注：覆盖率、节热率、热价、固定费与户数均为功能演示假设，不代表任何城市正式政策或收费标准。",
        fontsize=9.5,
        color="#8291A3",
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / "metering_demo_dashboard.png", dpi=220, bbox_inches="tight")
    fig.savefig(OUTPUT / "metering_demo_dashboard.svg", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
