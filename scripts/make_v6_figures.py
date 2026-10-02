from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "outputs/v6_policy_pathway/policy_pathway_summary.csv"
PATHWAY = ROOT / "data/example_policy_pathway.yaml"
OUTPUT = ROOT / "outputs/figures/v6_policy_pathway.png"


def main() -> None:
    summary = pd.read_csv(SUMMARY)
    pathway = yaml.safe_load(PATHWAY.read_text(encoding="utf-8"))
    stages = [str(item["name"]) for item in pathway["stages"]]
    policy = {str(item["name"]): item["policy"] for item in pathway["stages"]}
    x = range(len(stages))

    font_path = ROOT / "tmp/fonts/NotoSansSC.ttf"
    if font_path.exists():
        font_manager.fontManager.addfont(font_path)
    plt.rcParams.update({
        "font.family": ["Noto Sans SC", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "axes.edgecolor": "#A8B3BF",
        "axes.titleweight": "bold",
        "axes.titlesize": 11,
        "font.size": 9,
    })
    fig, axes = plt.subplots(2, 2, figsize=(11.2, 7.2), constrained_layout=True)
    fig.suptitle("V6 北方供热政策约束与分阶段实施路径（示例数据）", fontsize=16, fontweight="bold")

    ax = axes[0, 0]
    actual = summary["emission_intensity_kg_per_kwh"]
    cap = [policy[s]["emission_intensity_cap_kg_per_kwh"] for s in stages]
    ax.plot(x, actual, "o-", color="#1976A3", linewidth=2.2, label="模型结果")
    ax.plot(x, cap, "s--", color="#D86F45", linewidth=1.8, label="情景上限")
    ax.set_title("单位供热碳强度")
    ax.set_ylabel("kgCO₂/kWh")
    ax.legend(frameon=False)

    ax = axes[0, 1]
    waste = summary["waste_heat_share"] * 100
    waste_target = [policy[s]["minimum_waste_heat_share"] * 100 for s in stages]
    retrofit = summary["retrofitted_heat_share"] * 100
    retrofit_target = [policy[s].get("minimum_retrofit_heat_share", 0.0) * 100 for s in stages]
    ax.plot(x, waste, "o-", color="#2E8B57", linewidth=2.2, label="回收余热份额")
    ax.plot(x, waste_target, "--", color="#86B98B", linewidth=1.6, label="余热情景下限")
    ax.plot(x, retrofit, "o-", color="#7B61A8", linewidth=2.2, label="低温末端改造覆盖")
    ax.plot(x, retrofit_target, "--", color="#B6A5D1", linewidth=1.6, label="改造情景下限")
    ax.set_title("资源利用与用户侧改造")
    ax.set_ylabel("占比（%）")
    ax.legend(frameon=False, ncol=2, fontsize=8)

    ax = axes[1, 0]
    lcoh = summary["regulated_levelized_heat_cost_yuan_per_kwh"]
    lcoh_cap = [policy[s]["maximum_levelized_heat_cost_yuan_per_kwh"] for s in stages]
    ax.bar(x, lcoh, width=0.56, color="#4C83B6", label="含历史资产成本代理")
    ax.plot(x, lcoh_cap, "s--", color="#D86F45", linewidth=1.8, label="情景上限")
    ax.set_title("供热成本可负担性约束")
    ax.set_ylabel("元/kWh")
    ax.set_ylim(0, max(lcoh_cap) * 1.18)
    ax.legend(frameon=False)

    ax = axes[1, 1]
    capex = summary["legacy_annualized_capex_yuan_per_year"] / 10000
    subsidy = summary["public_subsidy_yuan_per_year"] / 10000
    ax.bar(x, capex, width=0.56, color="#577590", label="结转年化私人资本成本")
    ax.plot(x, subsidy, "o-", color="#F08A5D", linewidth=2.2, label="当期公共补贴")
    ax.set_title("财政—企业成本分账")
    ax.set_ylabel("万元/年")
    ax.legend(frameon=False)

    for ax in axes.flat:
        ax.set_xticks(list(x), stages)
        ax.grid(axis="y", color="#DCE3E8", linewidth=0.7, alpha=0.8)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)

    fig.text(
        0.5,
        -0.01,
        "注：份额、成本上限与补贴比例为模型演示情景，不是任何城市的正式政策指标。",
        ha="center",
        color="#596773",
        fontsize=8.5,
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
