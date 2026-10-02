"""Capacity dial: system import by clock hour against the shared distribution limit.

Draft figure for the 2x2 thermal-capacity x shared-capacity factorial.
Angle is the local clock hour, radius is the system import the two buildings
draw together. The ring is the registered 6.4796375 kW shared limit K.
Every number is read from the rc6 analysis exports; nothing is typed in.

The radial axis is broken at K: 0..K fills the inner 60% of the radius, K..RMAX
is compressed into the outer 40%, so that both the "inside the boundary" texture
and the "how far past" spikes stay legible. Ring values are printed, not implied.

Run:  python scripts/make_capacity_dial_figure.py
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


plt.rcParams["font.family"] = "Noto Sans SC"
plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "docs/final_optimization_20261002/multiscale/run_03"
HOURLY = CASE_DIR / "analysis/hourly.csv"
METRICS = CASE_DIR / "analysis/case_metrics.csv"
INPUT = CASE_DIR / "input_cases/C1_shared/input.csv"
OUT = ROOT / "outputs/figures/capacity_dial.png"

CASES = ["C1_unlimited", "C4_unlimited", "C1_shared", "C4_shared"]
ROWS = [["C1_unlimited", "C4_unlimited"], ["C1_shared", "C4_shared"]]
# mathtext for the subscripts: Noto Sans SC has no U+2081/U+2084
HEADER = {
    "C1_unlimited": r"建筑热容 $\mathregular{C_1}$ · 不施加共享容量约束",
    "C4_unlimited": r"建筑热容 $\mathregular{C_4}$ · 不施加共享容量约束",
    "C1_shared": r"建筑热容 $\mathregular{C_1}$ · 施加共享容量约束",
    "C4_shared": r"建筑热容 $\mathregular{C_4}$ · 施加共享容量约束",
}

CREAM = "#FBF7EE"
TEAL = "#1E6B6E"
EMBER = "#EF8F3C"
STEEL = "#6E9BB0"
OLIVE = "#7FA05C"
RED = "#C0392B"
INK = "#2B2B2B"

TARIFF_COLOR = {"valley": OLIVE, "flat": STEEL, "peak": EMBER, "sharp": "#C4652A"}
TARIFF_CN = {"valley": "谷", "flat": "平", "peak": "峰", "sharp": "尖"}

R_MAX = 26.0
INNER = 0.60          # radius fraction where the axis breaks at K
INNER_TICKS = (2, 4, 6)
OUTER_TICKS = (10, 15, 20, 25)


def load_system_series():
    """One row per (case, hour). The export repeats the system total on both
    building rows, so we keep variant_A and assert variant_B agrees."""
    a: dict[str, dict[int, float]] = {}
    b: dict[str, dict[int, float]] = {}
    k_values = set()
    for row in csv.DictReader(open(HOURLY, encoding="utf-8")):
        case, i = row["case"], int(row["time_index"])
        val = float(row["system_import_kw"])
        (a if row["building"] == "variant_A" else b).setdefault(case, {})[i] = val
        k_values.add(round(float(row["reference_grid_capacity_kw"]), 12))
    for case in CASES:
        assert a[case].keys() == b[case].keys()
        assert max(abs(a[case][i] - b[case][i]) for i in a[case]) == 0.0, case
    assert len(k_values) == 1, k_values
    return {c: np.array([a[c][i] for i in range(2880)]) for c in CASES}, k_values.pop()


def load_tariff():
    buckets = []
    for row in csv.DictReader(open(INPUT, encoding="utf-8")):
        buckets.append(row["tariff_bucket"])
    assert len(buckets) == 2880
    return buckets


def load_metrics():
    return {r["case"]: r for r in csv.DictReader(open(METRICS, encoding="utf-8"))}


def rmap(v, k):
    """kW -> plotted radius in [0, 1], piecewise linear, broken at K."""
    v = np.asarray(v, dtype=float)
    inner = np.clip(v / k, 0, 1) * INNER
    outer = INNER + (v - k) / (R_MAX - k) * (1 - INNER)
    return np.where(v <= k, inner, outer)


def draw_panel(ax, series, k, buckets, slot):
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_ylim(0, 1.13)
    ax.set_axis_off()

    hours = np.arange(2880) % 24
    days = np.arange(2880) // 24
    theta = (hours / 24.0) * 2 * np.pi + (slot[days] - 0.5) * (2 * np.pi / 24) * 0.78
    radius = rmap(series, k)

    over = series > k + 1e-9
    ax.scatter(theta[~over], radius[~over], s=3.2, c=STEEL, alpha=0.50, linewidths=0, zorder=3)
    if over.any():
        ax.scatter(theta[over], radius[over], s=6.5, c=RED, alpha=0.78, linewidths=0, zorder=4)

    ang = np.linspace(0, 2 * np.pi, 721)
    for tick in INNER_TICKS + OUTER_TICKS:
        r = (tick / k) * INNER if tick <= k else INNER + (tick - k) / (R_MAX - k) * (1 - INNER)
        ax.plot(ang, np.full_like(ang, r), color=INK, lw=0.5, alpha=0.13,
                ls=(0, (2, 4)), zorder=1)

    # capacity ring
    ax.plot(ang, np.full_like(ang, INNER), color=TEAL, lw=2.4, zorder=5)

    # only two on-figure scale readings; the rest live in the footnote
    lab = np.deg2rad(206)
    ax.text(lab, INNER, f" K = {k:.2f}", fontsize=7.0, color=TEAL, va="center",
            ha="left", zorder=6, bbox=dict(fc=CREAM, ec="none", pad=1.2))
    r20 = INNER + (20 - k) / (R_MAX - k) * (1 - INNER)
    ax.text(lab, r20, " 20 kW", fontsize=7.0, color=INK, va="center", ha="left",
            alpha=0.5, zorder=6, bbox=dict(fc=CREAM, ec="none", pad=1.2))

    # tariff ring just outside the clock circle, contiguous sectors
    for h in range(24):
        b = Counter(buckets[i] for i in range(2880) if i % 24 == h).most_common(1)[0][0]
        ax.bar((h + 0.5) / 24.0 * 2 * np.pi, 0.048, width=2 * np.pi / 24,
               bottom=1.045, color=TARIFF_COLOR[b], alpha=0.75,
               linewidth=0, zorder=2)

    for h, label in ((0, "00"), (6, "06"), (12, "12"), (18, "18")):
        ax.text(h / 24.0 * 2 * np.pi, 1.118, label, ha="center", va="center",
                fontsize=7.8, color=INK, alpha=0.75, zorder=6)


def main():
    series_all, k = load_system_series()
    buckets = load_tariff()
    metrics = load_metrics()
    slot = np.array([(d % 120) / 119.0 for d in range(120)])

    fig = plt.figure(figsize=(12.0, 14.5), dpi=300, facecolor=CREAM)

    pw, ph = 0.432, 0.3575
    col_x = (0.048, 0.520)
    row_y = (0.5545, 0.1285)

    for r_i, row in enumerate(ROWS):
        for c_i, case in enumerate(row):
            rect = [col_x[c_i], row_y[r_i], pw, ph]
            ax = fig.add_axes(rect, projection="polar")
            s = series_all[case]
            draw_panel(ax, s, k, buckets, slot)

            ctr = rect[0] + pw / 2
            fig.text(ctr, row_y[r_i] + ph + 0.006, HEADER[case], ha="center",
                     va="bottom", fontsize=12, color=INK)

            n_over = int((s > k + 1e-9).sum())
            peak = float(s.max())
            cost = float(metrics[case]["reference_energy_charge_yuan"])
            if n_over:
                head, col = f"越限 {n_over} 小时", RED
                sub = f"峰值 {peak:.2f} kW · 运行费用 {cost:,.2f} 元"
            else:
                head, col = "越限 0 小时", TEAL
                n_bind = int((s > k - 1e-6).sum())
                sub = f"顶到上限 {n_bind} 小时 · 运行费用 {cost:,.2f} 元"

            yb = row_y[r_i] - 0.010
            fig.text(ctr, yb, head, ha="center", va="top",
                     fontsize=17, color=col, fontweight="bold")
            fig.text(ctr, yb - 0.022, sub, ha="center", va="top",
                     fontsize=11, color=INK)

    fig.text(0.5, 0.972, "让建筑灵活性在容量边界内创造价值",
             ha="center", va="center", fontsize=23, color=INK, fontweight="bold")
    fig.text(0.5, 0.941,
             f"共享上限 {k:.7f} kW · 热容提升使费用降低 4.66% · 越限保持为 0",
             ha="center", va="center", fontsize=13, color=TEAL)

    handles = [
        Line2D([], [], marker="o", ls="", ms=6, mfc=STEEL, mec="none", alpha=0.7,
               label="容量内的小时"),
        Line2D([], [], marker="o", ls="", ms=7, mfc=RED, mec="none", alpha=0.85,
               label="超出上限的小时"),
        Line2D([], [], color=TEAL, lw=2.4, label="共享配电上限 K"),
        Line2D([], [], color=OLIVE, lw=6, alpha=0.75, label="外圈：谷电段"),
        Line2D([], [], color=EMBER, lw=6, alpha=0.75, label="平 / 峰 / 尖段"),
    ]
    fig.legend(handles=handles, loc="center", bbox_to_anchor=(0.5, 0.062),
               ncol=5, frameon=False, fontsize=10.5, columnspacing=2.4, handletextpad=0.5)

    fig.text(0.5, 0.026,
             "2880 小时研究算例 · 武汉参考气象 · 2 栋公开原型建筑 · 模型账本口径 · 径向在 K 处折段 · 电量 +4.17%，属移峰省费",
             ha="center", va="center", fontsize=8.4, color=INK, alpha=0.6)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300, facecolor=CREAM)
    print("wrote", OUT)
    for case in CASES:
        s = series_all[case]
        print(f"{case:14s} cost={float(metrics[case]['reference_energy_charge_yuan']):9.2f} "
              f"peak={s.max():7.3f} over={int((s > k + 1e-9).sum()):4d} "
              f"bind={int((s > k - 1e-6).sum()):4d}")


if __name__ == "__main__":
    main()
