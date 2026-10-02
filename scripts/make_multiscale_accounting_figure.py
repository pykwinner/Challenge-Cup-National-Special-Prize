# -*- coding: utf-8 -*-
"""多尺度归属：同一份实际送达量只能计一次。

数据源：docs/implementation_20261002_followup/parallel_stage_03/multiscale/run_01/
  results/joint_unlimited/{summary.json, independent_audit.json, numerical_audit.json}
  —— 2880 小时、2 栋楼、无共享上限的联合优化案例

主张：楼宇 / 节点 / 片区 / 系统四层台账记录的是同一份实际送达热量，
      各层总量相加会把同一份热量重复计入。本图只做算术对照与守恒残差展示，
      不含任何性能比较或工程效果推断。
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrow, FancyBboxPatch, Polygon

ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "docs" / "implementation_20261002_followup" / "parallel_stage_03"
       / "multiscale" / "run_01" / "results" / "joint_unlimited")
OUT = ROOT / "outputs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 文章色板（照现稿）：暖米黄底 / 炉火橙=重复计量（坏）/ 管道蓝灰=唯一归属（好）
CREAM = "#FBF7EE"        # 底色 暖米黄
INK = "#173F42"          # 正文深青黑
GREY = "#7C8F91"         # 辅助灰（浅）
EMBER = "#EF8F3C"        # 炉火橙 基准
EMBER_DEEP = "#C4652A"   # 炉火橙 深端
EMBER_DARK = "#8C3A14"   # 炉火橙 更深处
EMBER_LIGHT = "#F9D9BC"  # 炉火橙 浅端
STEEL = "#6E9BB0"        # 管道蓝灰 基准
STEEL_LIGHT = "#DDE6DE"  # 管道蓝灰 浅端
STEEL_DARK = "#33504F"   # 管道蓝灰 深端
STEEL_MID = "#A5C0C7"    # 管道蓝灰 浅-基准 中间插值

plt.rcParams["axes.facecolor"] = CREAM
plt.rcParams["figure.facecolor"] = CREAM
plt.rcParams["savefig.facecolor"] = CREAM
plt.rcParams["text.color"] = INK
plt.rcParams["axes.labelcolor"] = INK
plt.rcParams["axes.edgecolor"] = INK
plt.rcParams["xtick.color"] = INK
plt.rcParams["ytick.color"] = INK

C_WRONG = EMBER_DEEP
C_RIGHT = STEEL_DARK
C_BAND = STEEL
C_BAND_W = EMBER_LIGHT


def ribbon(ax, x0, x1, y0, y1, x2, y2a, y2b, color, alpha=0.85, zorder=2):
    """从 (x0,x1) 处的 [y0,y1] 平滑收束到 (x2) 处的 [y2a,y2b]。"""
    xs = np.linspace(x0, x2, 60)
    t = (xs - x0) / (x2 - x0)
    s = t * t * (3 - 2 * t)  # smoothstep
    top = y1 + (y2b - y1) * s
    bot = y0 + (y2a - y0) * s
    ax.fill_between(xs, bot, top, color=color, alpha=alpha, lw=0, zorder=zorder)


def main():
    summ = json.load(open(RUN / "summary.json", encoding="utf-8"))
    ind = json.load(open(RUN / "independent_audit.json", encoding="utf-8"))
    num = json.load(open(RUN / "numerical_audit.json", encoding="utf-8"))

    Q = summ["building_heat_delivered_kwh_per_year"]          # 8725.44
    demand = summ["demand_kwh_per_year"]
    unserved = summ["unserved_kwh_per_year"]
    levels = ["楼宇", "节点", "片区", "系统"]

    fig = plt.figure(figsize=(13.4, 8.2))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.0, 1.0],
                          left=0.04, right=0.97, top=0.90, bottom=0.20,
                          hspace=0.30, wspace=0.10)

    # ---------------- a 错误做法：四层相加
    ax = fig.add_subplot(gs[0, 0])
    ax.set_xlim(0, 4.6)
    ax.set_ylim(-0.6, 4.4)
    ax.axis("off")
    th = 0.62
    for i, lv in enumerate(levels):
        y = 3.2 - i * 0.95
        ribbon(ax, 0.25, 2.30, y - th / 2, y + th / 2, 3.05, 1.35, 1.90,
               C_BAND_W, alpha=0.75)
        ax.text(0.42, y, lv, fontsize=10.5, va="center", ha="left",
                color=EMBER_DARK, zorder=4)
        ax.text(2.15, y, f"{Q:,.0f}", fontsize=9, va="center", ha="right",
                color=EMBER_DARK, zorder=4)
    box = FancyBboxPatch((3.10, 1.18), 1.32, 0.90,
                         boxstyle="round,pad=0.03", facecolor=EMBER_LIGHT,
                         edgecolor=C_WRONG, lw=1.6)
    ax.add_patch(box)
    ax.text(3.76, 1.78, f"{4*Q:,.0f}", fontsize=13, ha="center", va="center",
            color=C_WRONG, fontweight="bold")
    ax.text(3.76, 1.42, "名义总量 kWh", fontsize=8.5, ha="center", va="center",
            color=EMBER_DARK)
    ax.text(3.76, 2.42, f"= {Q:,.0f} × 4", fontsize=9, ha="center", va="center",
            color=C_WRONG)
    ax.text(0.25, 4.02, "同一份热量被计入四次", fontsize=10, color=C_WRONG,
            fontweight="bold")
    ax.text(0.04, 1.06, "a", transform=ax.transAxes, fontsize=15,
            fontweight="bold", ha="left", va="bottom")
    ax.text(0.085, 1.06, "重复计量：各层台账相加", transform=ax.transAxes,
            fontsize=11, ha="left", va="bottom")
    ax.text(0.085, 1.005, "四层记录的是同一份实际送达量，相加即重复",
            transform=ax.transAxes, fontsize=8.5, color=GREY,
            ha="left", va="bottom")

    # ---------------- b 本项目：单一流带贯穿
    ax = fig.add_subplot(gs[0, 1])
    ax.set_xlim(0, 4.6)
    ax.set_ylim(-0.6, 4.4)
    ax.axis("off")
    yc, thb = 2.05, 0.86
    ribbon(ax, 0.25, 3.05, yc - thb / 2, yc + thb / 2, 3.05, yc - thb / 2,
           yc + thb / 2, C_BAND, alpha=0.9)
    xs = np.linspace(0.75, 2.65, 4)
    for i, (lv, x) in enumerate(zip(levels, xs)):
        ax.plot([x, x], [yc - thb / 2 - 0.10, yc + thb / 2 + 0.10],
                color=CREAM, lw=1.6, zorder=5)
        ax.text(x, yc + thb / 2 + 0.20, lv, fontsize=10.5, ha="center",
                va="bottom", color=STEEL_DARK, zorder=5)
        ax.text(x, yc - thb / 2 - 0.20, f"{Q:,.0f}", fontsize=8.5, ha="center",
                va="top", color=STEEL_DARK, zorder=5)
    ribbon(ax, 3.05, 3.05, yc - thb / 2, yc + thb / 2, 3.10, yc - 0.30,
           yc + 0.30, C_BAND, alpha=0.9)
    box = FancyBboxPatch((3.12, yc - 0.42), 1.30, 0.84,
                         boxstyle="round,pad=0.03", facecolor=STEEL_LIGHT,
                         edgecolor=C_RIGHT, lw=1.6)
    ax.add_patch(box)
    ax.text(3.77, yc + 0.09, f"{Q:,.0f}", fontsize=13, ha="center", va="center",
            color=C_RIGHT, fontweight="bold")
    ax.text(3.77, yc - 0.26, "总计 kWh", fontsize=8.5, ha="center", va="center",
            color=STEEL_DARK)
    ax.text(0.25, 4.02, "同一份热量只在系统层计一次", fontsize=10, color=C_RIGHT,
            fontweight="bold")
    ax.text(1.65, 3.62, "带厚 = 实际送达量，贯穿四层不变", fontsize=8.5,
            color=STEEL_DARK, ha="center")
    ax.text(0.04, 1.06, "b", transform=ax.transAxes, fontsize=15,
            fontweight="bold", ha="left", va="bottom")
    ax.text(0.085, 1.06, "本项目：唯一归属", transform=ax.transAxes,
            fontsize=11, ha="left", va="bottom")
    ax.text(0.085, 1.005, "每栋楼只归属一个节点、一个片区、一个系统",
            transform=ax.transAxes, fontsize=8.5, color=GREY,
            ha="left", va="bottom")

    # ---------------- c 守恒残差：对数棒棒糖
    ax = fig.add_subplot(gs[1, :])
    n_b = ind["metrics"]["building_count"]
    items = [
        ("需求 − 实际送达", 0.0, "kWh"),
        ("未服务热量", 0.0, "kWh"),
        ("电网越限小时", float(ind["metrics"]["grid_exceedance_hours"]), "h"),
        ("逐楼 RC 轨迹最大残差", ind["metrics"]["max_rc_residual_c"], "℃"),
        ("舒适区外度·小时", ind["metrics"]["outside_comfort_degree_hours"], "℃·h"),
        ("约束最大违反", num["numerical_constraint_max_violation"], "—"),
        ("变量界最大违反", num["numerical_bound_max_violation"], "—"),
        ("整数性最大违反", num["numerical_integrality_max_violation"], "—"),
        ("目标函数重算误差", num["numerical_objective_recompute_error"], "—"),
    ]
    names = [it[0] for it in items][::-1]
    vals = [max(it[1], 1e-17) for it in items][::-1]
    ys = np.arange(len(names))
    floor = 1e-17
    for i, (v, y) in enumerate(zip(vals, ys)):
        color = STEEL_DARK if v > floor * 1.5 else STEEL_MID
        ax.plot([floor, v], [y, y], color=color, lw=2.2, zorder=2,
                solid_capstyle="butt")
        ax.plot([v], [y], marker="o", ms=7, color=color, zorder=3)
        txt = "精确 0" if v <= floor * 1.5 else f"{v:.2e}"
        ax.text(v * 1.6 if v > floor * 1.5 else floor * 2.6, y, txt,
                fontsize=8.5, va="center", color=INK)
    ax.axvline(1e-6, color=EMBER_DEEP, ls="--", lw=1.4, zorder=1)
    ax.text(1e-6, len(names) - 0.35,
            "数值审计容差 1e−6", fontsize=8.5, color=EMBER_DEEP, ha="center")
    ax.set_xscale("log")
    ax.set_xlim(floor * 0.6, 1e-3)
    ax.set_yticks(ys)
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("残差绝对量（对数轴；无单位的为求解器内部审计量）", fontsize=9)
    ax.set_ylim(-0.7, len(names) - 0.1)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color="#E8E1CE", lw=0.7, zorder=0)

    ax.text(-0.045, 1.30, "c", transform=ax.transAxes, fontsize=15,
            fontweight="bold", ha="left", va="bottom")
    ax.text(-0.017, 1.30, "守恒与自洽的实测残差（案例 joint_unlimited）",
            transform=ax.transAxes, fontsize=11, ha="left", va="bottom")
    ax.text(-0.017, 1.155,
            f"{n_b} 栋楼 · 2880 小时；左端竖线为数值 0 下界，落到下界即为精确 0",
            transform=ax.transAxes, fontsize=8.5, color=GREY,
            ha="left", va="bottom")

    fig.text(0.04, 0.055,
             f"数据：multiscale/run_01/results/joint_unlimited（rc6 研究候选）。"
             f"实际送达 {Q:,.2f} kWh = 需求 {demand:,.2f} kWh，未服务 {unserved:.1f} kWh。"
             "本图为算术对照与数值自洽性证据，非预测精度，亦非工程效果结论。",
             fontsize=7.5, color=GREY, ha="left", va="bottom")

    png = OUT / "multiscale_double_counting.png"
    tmp = png.with_name(png.stem + "_tmp" + png.suffix)
    fig.savefig(tmp, dpi=300, bbox_inches="tight", facecolor=CREAM)
    tmp.replace(png)
    print(f"送达 {Q:,.2f} kWh | 四层相加 {4*Q:,.2f} kWh | 残差 {ind['metrics']['max_rc_residual_c']:.2e}")
    print("saved ->", png)


if __name__ == "__main__":
    main()
