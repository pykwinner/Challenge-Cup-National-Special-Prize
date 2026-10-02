"""Feasibility map: cost against shared-capacity exceedance for the four cases.

The point of the plane is that the cheap corner is off limits. Cost runs up the
y axis, the number of hours over the shared limit runs along the x axis, and the
shared limit itself is the wall at x = 0. Marker area carries the depth of the
overshoot in kWh, so a case that misses by a little looks different from one
that misses by a lot.

Run:  python scripts/make_feasible_map_figure.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Circle

plt.rcParams["font.family"] = "Noto Sans SC"
plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "docs/final_optimization_20261002/multiscale/run_03"
HOURLY = CASE_DIR / "analysis/hourly.csv"
METRICS = CASE_DIR / "analysis/case_metrics.csv"
OUT = ROOT / "outputs/figures/feasible_map.png"

CREAM = "#FBF7EE"
TEAL = "#1E6B6E"
EMBER = "#EF8F3C"
STEEL = "#6E9BB0"
OLIVE = "#7FA05C"
INK = "#173F42"
GREY = "#7C8F91"

CASES = ["C1_unlimited", "C4_unlimited", "C1_shared", "C4_shared"]
LABEL = {
    "C1_unlimited": r"$C_1$ · 无共享约束",
    "C4_unlimited": r"$C_4$ · 无共享约束",
    "C1_shared": r"$C_1$ · 有共享约束",
    "C4_shared": r"$C_4$ · 有共享约束",
}


def load():
    series: dict[str, list[float]] = {}
    seen: dict[str, set[int]] = {}
    k = None
    for row in csv.DictReader(open(HOURLY, encoding="utf-8")):
        if row["building"] != "variant_A":
            continue                      # system_import_kw repeats on both rows
        case, i = row["case"], int(row["time_index"])
        series.setdefault(case, [0.0] * 2880)[i] = float(row["system_import_kw"])
        seen.setdefault(case, set()).add(i)
        k = float(row["reference_grid_capacity_kw"])
    for case in CASES:
        assert len(seen[case]) == 2880, case
    costs = {r["case"]: float(r["reference_energy_charge_yuan"])
             for r in csv.DictReader(open(METRICS, encoding="utf-8"))}
    return series, costs, k


def main():
    series, costs, k = load()

    stats = {}
    for case in CASES:
        v = np.array(series[case])
        stats[case] = dict(
            cost=costs[case],
            hours=int((v > k + 1e-9).sum()),
            excess=float(np.maximum(v - k, 0).sum()),
        )

    fig = plt.figure(figsize=(11.6, 7.6), dpi=300, facecolor=CREAM)
    ax = fig.add_axes([0.072, 0.150, 0.900, 0.720])
    ax.set_facecolor(CREAM)

    x_lo, x_hi = -78.0, 262.0
    y_lo, y_hi = 5440.0, 6000.0
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)

    # off-limits side of the wall
    ax.axvspan(0, x_hi, facecolor=EMBER, alpha=0.075, zorder=0)
    ax.axvspan(0, x_hi, facecolor="none", hatch="////", edgecolor=EMBER,
               alpha=0.16, lw=0, zorder=0)

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GREY)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=INK, labelsize=10, length=4, width=0.8)
    ax.set_xticks([0, 50, 100, 150, 200, 250])
    ax.set_xlabel("按统一上限复查时超限的小时数", fontsize=11.5, color=INK, labelpad=9)
    ax.set_ylabel("120 日运行费用（元）", fontsize=11.5, color=INK, labelpad=9)
    ax.grid(axis="y", color=GREY, alpha=0.18, lw=0.6, zorder=0)
    ax.set_axisbelow(True)

    # the wall
    ax.axvline(0, color=TEAL, lw=4.5, solid_capstyle="butt", zorder=2)
    ax.text(5, y_lo + 16, "可行墙（共享上限）", fontsize=9.6, color=TEAL,
            va="bottom", ha="left", zorder=6)
    ax.text(x_lo + 4, y_hi - 14, "合规侧", fontsize=11.5, color=TEAL,
            va="top", ha="left", fontweight="bold", zorder=6)
    ax.text(x_hi - 6, y_hi - 14, "越限区", fontsize=11.5, color="#B4661F",
            va="top", ha="right", fontweight="bold", zorder=6)

    for case in CASES:
        st = stats[case]
        rule = "shared" if case.endswith("shared") else "unlimited"
        ax.scatter([st["hours"]], [st["cost"]], s=130.0 + st["excess"] * 0.62,
                   c=TEAL if rule == "shared" else EMBER, alpha=0.88,
                   linewidths=1.6, edgecolors=CREAM, zorder=5)

    def tag(case, x, dy, ha, color):
        st = stats[case]
        ax.text(x, st["cost"] + dy, f"{LABEL[case]}  {st['cost']:,.2f} 元",
                fontsize=10.4, color=color, ha=ha, va="center", zorder=6)

    tag("C1_shared", -9, 0, "right", TEAL)
    tag("C4_shared", -9, 0, "right", TEAL)
    tag("C1_unlimited", 121, 40, "center", "#B4661F")
    tag("C4_unlimited", 229, 46, "center", "#B4661F")

    def arrow(p, q, color, style="-", lw=1.8, z=4):
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=14,
                                     color=color, lw=lw, ls=style, zorder=z,
                                     shrinkA=2, shrinkB=2))

    # the four gaps, straight out of the metrics table
    keep_c1 = stats["C1_shared"]["cost"] - stats["C1_unlimited"]["cost"]
    keep_c4 = stats["C4_shared"]["cost"] - stats["C4_unlimited"]["cost"]
    save_c1 = stats["C1_shared"]["cost"] - stats["C4_shared"]["cost"]
    save_c4 = stats["C1_unlimited"]["cost"] - stats["C4_unlimited"]["cost"]
    y1u = stats["C1_unlimited"]["cost"]
    y4u = stats["C4_unlimited"]["cost"]
    h1u = stats["C1_unlimited"]["hours"]
    h4u = stats["C4_unlimited"]["hours"]

    # crossing the wall: what the money buys, what it costs
    arrow((6, y1u), (h1u - 9, y1u), EMBER, style=(0, (4, 3)))
    arrow((6, y4u), (h4u - 9, y4u), EMBER, style=(0, (4, 3)))

    # descending, inside the off-limits band; the wall pair is labelled directly
    arrow((h1u, y1u - 8), (h4u, y4u + 8), EMBER, lw=2.2)

    ax.text(59, y1u - 14, f"过墙省 {keep_c1:.2f} 元，换 {stats['C1_unlimited']['hours']} 个越限小时",
            fontsize=9.6, color="#B4661F", ha="center", va="top", zorder=6)
    ax.text(150, y4u + 16, f"过墙省 {keep_c4:.2f} 元，换 {stats['C4_unlimited']['hours']} 个越限小时",
            fontsize=9.6, color="#B4661F", ha="center", va="bottom", zorder=6)
    ax.text(-9, 5798, f"热容提升省 {save_c1:.2f} 元", fontsize=9.8, color=TEAL,
            ha="right", va="center", zorder=6)
    ax.text(196, 5798, f"热容提升省 {save_c4:.2f} 元", fontsize=9.8, color="#B4661F",
            ha="center", va="center", zorder=6)

    handles = [
        plt.Line2D([], [], marker="o", ls="", ms=11, mfc=TEAL, mec=CREAM, mew=1.2,
                   label="施加共享容量约束"),
        plt.Line2D([], [], marker="o", ls="", ms=11, mfc=EMBER, mec=CREAM, mew=1.2,
                   label="不施加共享容量约束"),
        plt.Line2D([], [], marker="o", ls="", ms=6, mfc="none", mec=GREY,
                   label="圆面积 ∝ 越限电量"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.032),
               ncol=3, frameon=False, fontsize=10, columnspacing=2.6)

    fig.text(0.5, 0.952, "可行墙：省钱与合规不能同时要",
             ha="center", va="center", fontsize=20, color=INK, fontweight="bold")
    fig.text(0.5, 0.908,
             r"墙上最便宜的是热容 $\mathregular{C_4}$ + 共享约束："
             f"{stats['C4_shared']['cost']:,.2f} 元，越限 0 小时",
             ha="center", va="center", fontsize=11.2, color=TEAL)
    fig.text(0.5, 0.022,
             "2880 小时研究算例 · 武汉参考气象 · 2 栋公开原型建筑 · 模型账本口径；"
             "墙外两案的越限为按统一上限复查的反事实",
             ha="center", va="center", fontsize=8.4, color=INK, alpha=0.6)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300, facecolor=CREAM)
    print("wrote", OUT)
    for case in CASES:
        s = stats[case]
        print(f"{case:14s} cost={s['cost']:9.2f} hours={s['hours']:4d} excess={s['excess']:8.1f}")


if __name__ == "__main__":
    main()
