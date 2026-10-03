"""源网荷储联合选择图：储热候选与管网候选在同一次求解中的选择结果。

数据源：outputs/unified_joint_20261003/  （由 src/heatplan 的一体化模型跑出）
该模型与主案例的参数化条件投资模型是两套不同实现，数字不可直接互比。

左图：只改储热造价，观察最优容量——用于证明"容量可以为零"是一个被选择出来的结果，
      而不是因为储热没有进入模型。
右图：联合案例（源-网-荷-储同一次求解，mip_gap=0）中被选中与未选中的候选。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / "outputs/unified_joint_20261003"
OUT = ROOT / "outputs/figures/joint_selection.png"

BG = "#FBF7EE"
TEAL = "#1E6B6E"
ORANGE = "#EF8F3C"
BLUE = "#6E9BB0"
GRAY = "#9A9A9A"
INK = "#1A1A1A"

plt.rcParams.update({
    "font.family": ["Noto Sans SC", "Microsoft YaHei"],
    "axes.unicode_minus": False,
    "figure.facecolor": BG, "axes.facecolor": BG,
    "text.color": INK, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.edgecolor": "#9A9A9A",
})


def tank_capacity(run: str) -> float:
    rows = list(csv.DictReader(open(B / run / "investments.csv", encoding="utf-8")))
    tank = [r for r in rows if "tank" in str(r.get("asset", "")).lower()]
    return float(tank[0]["value"]) if tank else 0.0


def main() -> None:
    solo = [(5, "run_capex5"), (20, "run_capex20"), (30, "run_capex30"),
            (35, "run_capex35"), (40, "run_capex40"), (50, "run_capex50"),
            (100, "run_capex100"), (260, "run_capex260"), (600, "run_capex600")]
    joint = [(5, "run_joint_capex5"), (20, "run_joint_capex20"),
             (260, "run_joint")]

    s_x = [c for c, _ in solo]
    s_y = [tank_capacity(r) for _, r in solo]
    j_x = [c for c, _ in joint]
    j_y = [tank_capacity(r) for _, r in joint]

    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.0), gridspec_kw={"width_ratios": [1.45, 1]})
    fig.subplots_adjust(left=0.062, right=0.985, top=0.73, bottom=0.20, wspace=0.22)

    ax = axes[0]
    ax.plot(s_x, s_y, "o-", color=TEAL, lw=2, ms=7, label="单栋配置（无管网）")
    ax.plot(j_x, j_y, "s--", color=ORANGE, lw=2, ms=7, label="联合配置（含管网候选）")
    for x, y in zip(s_x, s_y):
        if y > 0:
            ax.annotate(f"{y:.1f}", (x, y), textcoords="offset points", xytext=(0, 9),
                        ha="center", fontsize=9, color=TEAL)
    for x, y in zip(j_x, j_y):
        if y > 0:
            ax.annotate(f"{y:.1f}", (x, y), textcoords="offset points", xytext=(0, -16),
                        ha="center", fontsize=9, color=ORANGE)
    ax.set_xscale("log")
    ax.set_xticks([5, 10, 20, 30, 50, 100, 200, 600])
    ax.set_xticklabels(["5", "10", "20", "30", "50", "100", "200", "600"], fontsize=9)
    ax.set_xlabel("储热造价（元/kWh，对数轴）", fontsize=11)
    ax.set_ylabel("优化器选中的储热容量（kWh）", fontsize=11)
    ax.set_ylim(-1, 16)
    ax.axhline(0, color=GRAY, lw=0.8)
    ax.legend(fontsize=9.5, frameon=False, loc="upper right")
    ax.set_title("造价越低越会配储热；高于约 30 元/kWh 后最优容量为零", fontsize=11, pad=8)

    ax = axes[1]
    ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0.0, 1.0, "联合案例：源-网-荷-储同一次求解", fontsize=11,
            color=INK, va="top", ha="left", transform=ax.transAxes)
    rows = [("热源（电气锅炉）", "已建 1000 kW", TEAL),
            ("管径候选 dn50（20 kW / 3.0 万元）", "选中", TEAL),
            ("管径候选 dn80（60 kW / 4.5 万元）", "未选", GRAY),
            ("储热候选（0—50 kWh / 260 元/kWh）", "选中，容量 0 kWh", ORANGE)]
    for i, (name, verdict, color) in enumerate(rows):
        y = 0.72 - i * 0.18
        ax.text(0.02, y, name, fontsize=10.5, color=INK, va="center")
        ax.text(0.98, y, verdict, fontsize=10.5, color=color, va="center", ha="right")
        ax.plot([0.02, 0.98], [y - 0.075, y - 0.075], color="#DDDDDD", lw=0.8)
    ax.text(0.02, 0.045,
            "mip_gap = 0.0（证明最优）· 建筑室温状态校验通过 · RC 残差 1.4e−14 ℃",
            fontsize=9, color="#555555")

    fig.suptitle("储热与管网都是候选：容量为零是被选择出来的结果，不是没有进入模型", fontsize=13.5, y=0.96)
    fig.text(0.062, 0.845,
             "造价压低到 5 元/kWh 时，优化器确实会配置储热（13—14 kWh）——证明该变量是活的；"
             "本次展示的 260 元/kWh 下选零，是经济性判断",
             fontsize=9.5)
    fig.text(0.062, 0.032,
             "一体化源网荷储 MILP · 武汉 2880 h · BESTEST 派生单栋（H=0.1043, C=0.535576，20—27 ℃）· "
             "该配置声明资产寿命 1 年，投资全额计入单年目标，非真实经济评价",
             fontsize=8.5, color="#555555")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300)
    plt.close(fig)
    print(f"saved {OUT}")
    print("单栋:", list(zip(s_x, s_y)))
    print("联合:", list(zip(j_x, j_y)))


if __name__ == "__main__":
    main()
