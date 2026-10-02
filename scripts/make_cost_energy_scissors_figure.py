"""The scissors: cost falls while electricity and carbon rise.

Thermal storage is paid for with extra energy, so the money saved is not energy
saved. Both readings come from the same registered case metrics; the percentage
axis is the only honest way to put money, kWh and kgCO2 on one scale.

Run:  python scripts/make_cost_energy_scissors_figure.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Polygon

plt.rcParams["font.family"] = "Noto Sans SC"
plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "docs/final_optimization_20261002/multiscale/run_03"
METRICS = CASE_DIR / "analysis/case_metrics.csv"
OUT = ROOT / "outputs/figures/cost_energy_scissors.png"

CREAM = "#FBF7EE"
TEAL = "#1E6B6E"
EMBER = "#EF8F3C"
OLIVE = "#7FA05C"
INK = "#173F42"
GREY = "#7C8F91"

BASE, NEW = "C1_shared", "C4_shared"


def load_pct(col):
    vals = {r["case"]: float(r[col]) for r in csv.DictReader(open(METRICS, encoding="utf-8"))}
    b, n = vals[BASE], vals[NEW]
    return vals, (n - b) / b * 100.0


def main():
    costs, dp = load_pct("reference_energy_charge_yuan")
    kwh, de = load_pct("electricity_kwh")
    co2, dc = load_pct("reference_average_factor_co2_kg")

    fig = plt.figure(figsize=(11.6, 7.2), dpi=300, facecolor=CREAM)
    ax = fig.add_axes([0.075, 0.175, 0.885, 0.665])
    ax.set_facecolor(CREAM)

    x0, x1 = 0.26, 0.74
    lim = 6.6
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-lim, lim)
    ax.set_axis_off()
    ax.axhline(0, color=GREY, lw=1.0, alpha=0.55, xmin=0.03, xmax=0.97, zorder=1)
    ax.plot([x0, x0], [-lim * 0.88, lim * 0.62], color=GREY, lw=0.9,
            alpha=0.45, ls=(0, (4, 3)), zorder=1)
    ax.plot([x1, x1], [-lim * 0.88, lim * 0.62], color=GREY, lw=0.9,
            alpha=0.45, ls=(0, (4, 3)), zorder=1)
    ax.text(x0, lim * 0.86, r"热容 $C_1$ · 有共享约束（基准）", fontsize=12, color=INK,
            ha="center", va="center", zorder=6,
            bbox=dict(fc=CREAM, ec="none", pad=2.2))
    ax.text(x1, lim * 0.86, r"热容 $C_4$ · 有共享约束", fontsize=12, color=INK,
            ha="center", va="center", zorder=6,
            bbox=dict(fc=CREAM, ec="none", pad=2.2))

    for y in (-5, -2.5, 2.5, 5):
        ax.plot([x0 - 0.015, x1 + 0.015], [y, y], color=GREY, lw=0.5,
                alpha=0.16, ls=(0, (2, 4)), zorder=0)

    # the opening wedge
    ax.add_patch(Polygon([[x0, 0], [x1, de], [x1, dp]], closed=True,
                         facecolor=EMBER, alpha=0.07, lw=0, zorder=1))

    def arrow(y, color, lw=3.0):
        ax.add_patch(FancyArrowPatch((x0, 0), (x1, y), arrowstyle="-|>",
                                     mutation_scale=22, color=color, lw=lw,
                                     zorder=5, shrinkA=0, shrinkB=0))

    arrow(dp, TEAL)
    arrow(de, EMBER)

    # the two blades
    ax.text(x1 + 0.030, dp, f"运行费用  {dp:+.2f}%\n"
                            f"{cents(costs[BASE])} → {cents(costs[NEW])} 元",
            fontsize=10.8, color=TEAL, ha="left", va="center", zorder=6, linespacing=1.55)
    ax.text(x1 + 0.030, de, f"电量 与 碳排  {de:+.2f}%\n"
                            f"{cents(kwh[BASE], 2)} → {cents(kwh[NEW], 2)} kWh",
            fontsize=10.8, color=EMBER, ha="left", va="center", zorder=6, linespacing=1.55)

    ax.text(x0 - 0.030, dp, f"{costs[NEW] - costs[BASE]:+,.2f} 元",
            fontsize=11.5, color=TEAL, ha="right", va="center", zorder=6,
            fontweight="bold")
    ax.text(x0 - 0.030, de,
            f"{kwh[NEW] - kwh[BASE]:+,.2f} kWh\n"
            f"{co2[NEW] - co2[BASE]:+,.2f} kgCO2",
            fontsize=11.5, color=EMBER, ha="right", va="center", zorder=6,
            fontweight="bold", linespacing=1.5)

    ax.set_ylabel("相对基准的变化（%）", fontsize=11, color=INK, labelpad=14)
    ax.yaxis.set_label_coords(-0.055, 0.5)
    for yy, lab in ((-5, "−5%"), (0, "0"), (5, "+5%")):
        ax.text(-0.052, yy, lab, fontsize=9.8, color=GREY,
                ha="center", va="center", zorder=6)

    fig.text(0.5, 0.940, "省下的钱，不是省下的电",
             ha="center", va="center", fontsize=22, color=INK, fontweight="bold")
    fig.text(0.5, 0.892,
             r"同一共享容量约束下热容从 $C_1$ 换到 $C_4$："
             f"费用降 {abs(dp):.2f}%，电量和碳排同时升 {de:.2f}%",
             ha="center", va="center", fontsize=11.4, color=INK)
    fig.text(0.5, 0.062,
             "电量增加来自谷电时段预热，属移峰省费；碳排按固定平均因子核算，与电量同比例。",
             ha="center", va="center", fontsize=9.4, color=EMBER)
    fig.text(0.5, 0.026,
             "2880 小时研究算例 · 武汉参考气象 · 2 栋公开原型建筑 · 模型账本口径",
             ha="center", va="center", fontsize=8.4, color=INK, alpha=0.6)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300, facecolor=CREAM)
    print("wrote", OUT)
    print(f"费用 {costs[BASE]:.4f} -> {costs[NEW]:.4f}  {dp:+.4f}%")
    print(f"电量 {kwh[BASE]:.4f} -> {kwh[NEW]:.4f}  {de:+.4f}%")
    print(f"碳排 {co2[BASE]:.4f} -> {co2[NEW]:.4f}  {dc:+.4f}%")


def cents(v, nd=2):
    return f"{v:,.{nd}f}"


if __name__ == "__main__":
    main()
