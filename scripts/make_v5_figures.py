from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-heatplan"))
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.font_manager import FontProperties


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "figures" / "v5_scenario_investment.png"
FONT_PATH = ROOT / "tmp" / "fonts" / "NotoSansSC.ttf"


def investment_map(path: Path, categories: set[str]) -> dict[str, float]:
    frame = pd.read_csv(path)
    frame = frame[(frame["category"].isin(categories)) & (frame["value"] > 1e-7)]
    return dict(zip(frame["asset"], frame["value"]))


def main() -> None:
    font = FontProperties(fname=str(FONT_PATH)) if FONT_PATH.exists() else FontProperties()
    plt.rcParams["axes.unicode_minus"] = False
    v4 = ROOT / "outputs" / "v4_resilient" / "investments.csv"
    v5 = ROOT / "outputs" / "v5_scenario" / "investments_expected_cost_plus_cvar.csv"
    source_categories = {"source_capacity", "grid_connection_capacity"}
    v4_source = investment_map(v4, source_categories)
    v5_source = investment_map(v5, source_categories)
    source_assets = [
        "industrial_waste_heat",
        "central_waste_source_hp",
        "distributed_air_hp_b",
        "electric_backup_boiler",
        "district_a_emergency_eb",
        "system",
    ]
    source_labels = ["工业余热", "集中余热热泵", "分布式空气热泵", "中心电锅炉", "A区应急电锅炉", "新增配网"]

    v4_storage = investment_map(v4, {"storage_capacity"})
    v5_storage = investment_map(v5, {"storage_capacity"})
    storage_assets = ["central_low_temp_tank", "district_a_pcm"]
    storage_labels = ["中心低温水罐", "A区PCM"]

    scenario = json.loads((ROOT / "outputs" / "v5_scenario" / "scenario_results.json").read_text(encoding="utf-8"))
    scenario_labels = ["常态冬季", "严寒", "复合寒潮", "余热低谷+高电价"]
    scenario_names = list(scenario)
    costs = [scenario[name]["operating_risk_cost_yuan"] / 10000 for name in scenario_names]
    emissions = [scenario[name]["emissions_kg"] / 1000 for name in scenario_names]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.7), gridspec_kw={"width_ratios": [1.55, 0.85, 1.25]})
    width = 0.36

    x = np.arange(len(source_assets))
    axes[0].bar(x - width / 2, [v4_source.get(a, 0) for a in source_assets], width, label="V4 单场景", color="#9BB7D4")
    axes[0].bar(x + width / 2, [v5_source.get(a, 0) for a in source_assets], width, label="V5 多场景", color="#E76F51")
    axes[0].set_xticks(x, source_labels, rotation=32, ha="right", fontproperties=font, fontsize=8.5)
    axes[0].set_ylabel("新增功率/接入容量（kW）", fontproperties=font)
    axes[0].set_title("多场景规划提高极端工况容量", fontproperties=font, fontsize=12)
    axes[0].legend(prop=font, frameon=False)
    axes[0].grid(axis="y", alpha=0.2)

    x2 = np.arange(len(storage_assets))
    axes[1].bar(x2 - width / 2, [v4_storage.get(a, 0) for a in storage_assets], width, color="#9BB7D4")
    axes[1].bar(x2 + width / 2, [v5_storage.get(a, 0) for a in storage_assets], width, color="#E76F51")
    axes[1].set_xticks(x2, storage_labels, fontproperties=font, fontsize=9)
    axes[1].set_ylabel("新增储热容量（kWh）", fontproperties=font)
    axes[1].set_title("储热投资变化", fontproperties=font, fontsize=12)
    axes[1].grid(axis="y", alpha=0.2)

    x3 = np.arange(len(scenario_names))
    axes[2].bar(x3 - width / 2, costs, width, color="#2A9D8F", label="运行风险成本")
    right = axes[2].twinx()
    right.bar(x3 + width / 2, emissions, width, color="#F4A261", label="排放")
    axes[2].set_xticks(x3, scenario_labels, rotation=25, ha="right", fontproperties=font, fontsize=8.5)
    axes[2].set_ylabel("场景运行风险成本（万元）", fontproperties=font)
    right.set_ylabel("场景排放（tCO2）", fontproperties=font)
    axes[2].set_title("极端场景代价不能被平均值掩盖", fontproperties=font, fontsize=12)
    axes[2].grid(axis="y", alpha=0.2)
    handles1, labels1 = axes[2].get_legend_handles_labels()
    handles2, labels2 = right.get_legend_handles_labels()
    axes[2].legend(handles1 + handles2, labels1 + labels2, prop=font, frameon=False, loc="upper left")

    for ax in axes:
        for label in ax.get_yticklabels():
            label.set_fontproperties(font)
    for label in right.get_yticklabels():
        label.set_fontproperties(font)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=220, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
