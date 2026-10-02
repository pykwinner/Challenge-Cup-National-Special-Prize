from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    data = pd.read_csv(ROOT / "outputs/scenario1_v9_joint/stage_summary.csv")
    font = ROOT / "tmp/fonts/NotoSansSC.ttf"
    if font.exists():
        font_manager.fontManager.addfont(font)
    plt.rcParams.update({"font.family": ["Noto Sans SC", "DejaVu Sans"], "axes.unicode_minus": False})
    years = data["stage"].astype(str)
    figure, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
    figure.suptitle("第一类场景：既有大型集中供热系统低碳重构路径", fontsize=16, fontweight="bold")

    ax = axes[0, 0]
    ax.plot(years, data["fossil_installed_capacity_kw"], "o-", lw=2.4, color="#B85450")
    ax.set_title("存量化石热源有序退出")
    ax.set_ylabel("化石热源装机（kW）")

    ax = axes[0, 1]
    ax.plot(years, data["fossil_heat_share"] * 100, "o-", lw=2.4, label="化石供热", color="#B85450")
    ax.plot(years, data["waste_heat_share"] * 100, "o-", lw=2.4, label="源侧回收余热", color="#2E8B57")
    ax.set_title("年供热结构替代")
    ax.set_ylabel("供热份额（%）")
    ax.legend(frameon=False)

    ax = axes[1, 0]
    ax.plot(years, data["emission_intensity_kg_per_kwh"], "o-", lw=2.4, color="#2678A5")
    ax.set_title("单位供热碳强度")
    ax.set_ylabel("kgCO₂/kWh")

    ax = axes[1, 1]
    ax.bar(years, data["renewable_electricity_absorption_rate"] * 100, color="#E6A23C")
    ax.set_title("可再生电力转热消纳")
    ax.set_ylabel("可利用电量吸收率（%）")

    for ax in axes.flat:
        ax.grid(axis="y", color="#DCE3E8", lw=0.7)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
    figure.text(
        0.5, -0.01,
        "注：数值来自示例城市与情景参数，正式参赛需使用目标城市实测数据重新标定。",
        ha="center", fontsize=8.5, color="#596773",
    )
    output = ROOT / "outputs/figures/scenario1_v9_transition_path.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(figure)


if __name__ == "__main__":
    main()
