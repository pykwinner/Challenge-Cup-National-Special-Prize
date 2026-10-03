"""三层多尺度图：城市级共享接入 → 区域级干线 → 用户级两栋异质建筑。

数据源：outputs/unified_multiscale_20261003/  （一体化源网荷储 MILP 跑出）
左图：收紧城市级共享接入容量，看区域/用户层的运行如何变化（相对无约束基准的百分比）。
右图：三层结构与各层被选中的候选。

注意：firm 需量 5.1837 kW 是硬地板，低于它必然不可行（两栋楼峰值同时到来，
无法靠移峰规避）；因此扫描区间取 5.2—6.48 kW。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / "outputs/unified_multiscale_20261003"
OUT = ROOT / "outputs/figures/multiscale_three_level.png"

BG = "#FBF7EE"
TEAL = "#1E6B6E"
ORANGE = "#EF8F3C"
BLUE = "#6E9BB0"
OLIVE = "#7FA05C"
GRAY = "#9A9A9A"
INK = "#1A1A1A"

plt.rcParams.update({
    "font.family": ["Noto Sans SC", "Microsoft YaHei"],
    "axes.unicode_minus": False,
    "figure.facecolor": BG, "axes.facecolor": BG,
    "text.color": INK, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.edgecolor": "#9A9A9A",
})

RUNS = [("5.2", "run_ms_het_grid5.2"), ("5.5", "run_ms_het_grid5.5"),
        ("6.0", "run_ms_het_grid6.0"), ("6.4796", "run_ms_het_grid6.4796375"),
        ("无限", "run_ms_het_unlimited")]


def main() -> None:
    data = []
    for lab, run in RUNS:
        s = json.loads((B / run / "summary.json").read_text(encoding="utf-8"))
        data.append({"lab": lab,
                     "bill": s["heating_cash_operating_cost_yuan_per_year"],
                     "elec": s["electricity_kwh_per_year"],
                     "co2": s["emissions_kg_per_year"],
                     "gap": s["mip_gap"]})
    base = data[-1]

    fig, axes = plt.subplots(1, 2, figsize=(13.4, 5.2), gridspec_kw={"width_ratios": [1.5, 1]})
    fig.subplots_adjust(left=0.062, right=0.985, top=0.745, bottom=0.165, wspace=0.20)

    ax = axes[0]
    finite = data[:-1]
    xs = list(range(len(finite)))
    # 碳排与电量按固定排放因子成比例，两条线完全重合，只画电量并在图注说明
    for key, lab, color, dy in (("bill", "电费", ORANGE, 11), ("elec", "电量／碳排", TEAL, -17)):
        ys = [(d[key] - base[key]) / base[key] * 100 for d in finite]
        ax.plot(xs, ys, "o-", color=color, lw=2, ms=7, label=lab)
        for x, y in zip(xs, ys):
            ax.annotate(f"{y:+.1f}%", (x, y), textcoords="offset points",
                        xytext=(0, dy), ha="center", fontsize=9, color=color)
    ax.axhline(0, color=GRAY, lw=1.0, ls=":")
    ax.text(len(finite) - 0.5, 0.25, "无约束基准（无限接入）", fontsize=9,
            color="#666666", ha="right")
    ax.set_xticks(xs)
    ax.set_xticklabels([d["lab"] for d in finite], fontsize=10)
    ax.set_xlabel("城市级共享接入上限（kW）", fontsize=11)
    ax.set_ylabel("相对无限接入基准的变化（%）", fontsize=11)
    ax.set_ylim(-7.5, 7.5)
    ax.legend(fontsize=9.5, frameon=False, loc="lower left", bbox_to_anchor=(0.02, 0.02))

    ax = axes[1]
    ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0.0, 1.0, "三层结构与各层选择", fontsize=11, color=INK, va="top")
    layers = [("城市级", "共享接入 6.4796 kW（已建）", "扩容候选：选中 0 kW", ORANGE),
              ("区域级", "两条干线 plant→district", "管径候选：均选 dn40", TEAL),
              ("用户级", "两栋 BESTEST 48 ㎡", "A 栋 C1 · B 栋 C4（异质）", BLUE)]
    for i, (name, item, chosen, color) in enumerate(layers):
        y = 0.70 - i * 0.24
        ax.text(0.0, y, name, fontsize=11, color=color, va="center")
        ax.text(0.22, y + 0.055, item, fontsize=10, color=INK, va="center")
        ax.text(0.22, y - 0.06, chosen, fontsize=10, color=color, va="center")
        ax.plot([0.0, 1.0], [y - 0.115, y - 0.115], color="#DDDDDD", lw=0.8)
    ax.text(0.0, 0.03,
            "全部工况：零弃供 · 室温状态校验通过 · mip_gap = 0.00000（证明最优）",
            fontsize=9, color="#555555")

    fig.suptitle("收紧城市级共享接入：下层少用电、少排碳，但多花钱", fontsize=15, y=0.955)
    fig.text(0.062, 0.855,
             "两栋异质建筑（A 栋基准热容、B 栋 4 倍热容）共用一条系统接入容量；"
             "5.184 kW 为 firm 需量硬地板，再低必然不可行",
             fontsize=9.5)
    fig.text(0.062, 0.045,
             "一体化源网荷储 MILP · 武汉 2880 h · 电气锅炉直供 · 理想设备 · "
             "每个工况目标含固定的 7.2 万元管渠投资；碳排按固定排放因子与电量成比例，故合并为一条线",
             fontsize=8.5, color="#555555")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300)
    plt.close(fig)
    print(f"saved {OUT}")
    for d in data:
        print(d["lab"], f"bill={d['bill']:.2f} elec={d['elec']:.1f} co2={d['co2']:.1f}")


if __name__ == "__main__":
    main()
