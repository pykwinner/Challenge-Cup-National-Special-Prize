"""Thermal reservoir: how full the buildings' heat storage is, hour by hour.

Each building's comfort band 20-27 C times its thermal capacitance is a usable
tank, C * 7 kWh per building. The curve is the live contents of that tank,
inventory = C * (T - 20), summed over the two buildings. Valley tariff windows
are shaded so the fill-and-drain rhythm lines up with the prices.

Run:  python scripts/make_thermal_reservoir_figure.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "Noto Sans SC"
plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "docs/final_optimization_20261002/multiscale/run_03"
HOURLY = CASE_DIR / "analysis/hourly.csv"
INPUT = CASE_DIR / "input_cases/C1_shared/input.csv"
OUT = ROOT / "outputs/figures/thermal_reservoir.png"

CREAM = "#FBF7EE"
TEAL = "#1E6B6E"
EMBER = "#EF8F3C"
STEEL = "#6E9BB0"
OLIVE = "#7FA05C"
INK = "#173F42"
GREY = "#7C8F91"
DEEP = "#C4652A"

T_SETPOINT = 20.0
ROWS = [
    ("C1_unlimited", r"热容 $C_1$ · 无共享约束", EMBER, DEEP),
    ("C1_shared",    r"热容 $C_1$ · 有共享约束", STEEL, TEAL),
    ("C4_unlimited", r"热容 $C_4$ · 无共享约束", EMBER, DEEP),
    ("C4_shared",    r"热容 $C_4$ · 有共享约束", STEEL, TEAL),
]


def load():
    inv: dict[str, np.ndarray] = {}
    cap: dict[str, float] = {}
    per_building: dict[str, dict[str, float]] = {}
    for row in csv.DictReader(open(HOURLY, encoding="utf-8")):
        case, i = row["case"], int(row["time_index"])
        c = float(row["thermal_capacity_kwh_per_c"])
        t_end = float(row["temperature_end_c"])
        inv.setdefault(case, np.zeros(2880))
        inv[case][i] += c * (t_end - T_SETPOINT)
        per_building.setdefault(case, {})[row["building"]] = c
    for case, bld in per_building.items():
        cap[case] = sum(c * 7.0 for c in bld.values())    # 20 -> 27 C, each building
    buckets = [r["tariff_bucket"] for r in csv.DictReader(open(INPUT, encoding="utf-8"))]
    assert len(buckets) == 2880
    return inv, cap, buckets


def pick_window(inv, cases, span_days=14):
    """Start day of the span_days window whose per-case mean inventory sits
    closest to that case's whole-season mean. Deterministic, no cherry-picking."""
    span = span_days * 24
    best, best_cost = 0, None
    for start in range(0, 2880 - span + 1, 24):
        cost = 0.0
        for c in cases:
            season = inv[c].mean()
            window = inv[c][start:start + span].mean()
            cost += ((window - season) / season) ** 2
        if best_cost is None or cost < best_cost:
            best, best_cost = start, cost
    return best


def main():
    inv, cap, buckets = load()
    cases = [r[0] for r in ROWS]
    span = 14 * 24
    start = pick_window(inv, cases)
    print(f"window: day {start // 24} .. {start // 24 + 14}")

    fig = plt.figure(figsize=(12.4, 9.8), dpi=300, facecolor=CREAM)
    left, right = 0.088, 0.955
    top, bottom = 0.800, 0.112
    gap = 0.055
    n = len(ROWS)
    h = (top - bottom - gap * (n - 1)) / n

    hrs = np.arange(span)
    y_full = max(cap.values())

    ax_t = fig.add_axes([left, top + 0.028, right - left, 0.032])
    for i in range(span):
        ax_t.axvspan(hrs[i], hrs[i] + 1, color={
            "valley": OLIVE, "flat": STEEL, "peak": EMBER, "sharp": DEEP,
        }[buckets[start + i]], alpha=0.9, lw=0)
    ax_t.set_xlim(0, span)
    ax_t.set_xticks([])
    ax_t.set_yticks([])
    for sp in ax_t.spines.values():
        sp.set_visible(False)
    ax_t.set_title("电价档位    绿＝谷电    蓝灰＝平    橙＝峰    深橙＝尖峰",
                   fontsize=10.5, color=INK, pad=7)

    for k, (case, label, fill_c, line_c) in enumerate(ROWS):
        y0 = top - (k + 1) * h - k * gap
        ax = fig.add_axes([left, y0, right - left, h])
        y = inv[case][start:start + span]
        yc = cap[case]

        for i in range(span):
            if buckets[start + i] == "valley":
                ax.axvspan(hrs[i], hrs[i] + 1, color=OLIVE, alpha=0.13, lw=0, zorder=0)

        ax.fill_between(hrs, 0, y, color=fill_c, alpha=0.55, lw=0, zorder=2)
        ax.plot(hrs, y, color=line_c, lw=1.1, zorder=3)
        ax.axhline(yc, color=INK, lw=1.2, ls=(0, (5, 3)), alpha=0.7, zorder=4)

        ax.set_xlim(0, span)
        ax.set_ylim(0, y_full * 1.34)
        ax.set_yticks([0, 5, 10, 15, 20])
        ax.tick_params(colors=INK, labelsize=9, length=3)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(GREY)
            ax.spines[sp].set_linewidth(0.7)
        ax.set_xticks(np.arange(0, span + 1, 48))
        ax.set_xticklabels(["第 0 天", "第 2 天", "第 4 天", "第 6 天",
                            "第 8 天", "第 10 天", "第 12 天", "第 14 天"])
        if k < n - 1:
            ax.set_xticklabels([])

        ax.text(6, y_full * 1.24, label, fontsize=11.8, color=INK,
                va="top", ha="left", zorder=6)
        ax.text(span - 6, y_full * 1.24,
                f"库容 {yc:.1f} · 窗口峰值 {y.max():.1f} kWh",
                fontsize=10, color=line_c, va="top", ha="right", zorder=6,
                bbox=dict(fc=CREAM, ec="none", pad=1.6))

    fig.text(0.5, 0.955, "热库水位：谷电进水，峰电放水",
             ha="center", va="center", fontsize=21, color=INK, fontweight="bold")
    fig.text(0.5, 0.918,
             "库容 = 建筑热容 × 舒适区 7 ℃；只把 B 栋热容翻 4 倍，两栋合计库容 "
             f"{cap['C1_shared']:.1f} → {cap['C4_shared']:.1f} kWh",
             ha="center", va="center", fontsize=11.4, color=TEAL)
    fig.text(0.5, 0.020,
             f"窗口取第 {start // 24}–{start // 24 + 14} 日（各案全季均值最接近的一段 14 日）· "
             "库存按 C×(室温−20 ℃) 计 · 2880 小时研究算例 · 武汉参考气象 · 2 栋公开原型建筑合计 · 模型账本口径",
             ha="center", va="center", fontsize=8.4, color=INK, alpha=0.6)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300, facecolor=CREAM)
    print("wrote", OUT)
    for case, label, *_ in ROWS:
        y = inv[case]
        print(f"{case:14s} cap={cap[case]:7.3f} season_peak={y.max():7.3f} "
              f"use={y.max() / cap[case]:6.1%}  min={y.min():7.3f}")


if __name__ == "__main__":
    main()
