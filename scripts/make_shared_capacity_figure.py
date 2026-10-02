# -*- coding: utf-8 -*-
"""共享配电容量：分别最优叠加会越限，联合优化把越限消到零。

数据源：docs/implementation_20261002_followup/parallel_stage_03/multiscale/run_01/
  analysis/hourly_system.csv —— 2880 小时 × 8 个案例的系统级取电功率

口径：
  越限 = power_kw 超过共同配电上限 K（6.4796375 kW）的部分
  independent_sum_counterfactual = 两栋楼各自最优方案逐时相加（反事实）
  coupled_flexible = 联合优化且保留各楼 20–27 ℃ 调节自由度
"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors as mcolors
from matplotlib.colors import ListedColormap

ROOT = Path(__file__).resolve().parents[1]
CSV = (ROOT / "docs" / "implementation_20261002_followup" / "parallel_stage_03"
       / "multiscale" / "run_01" / "analysis" / "hourly_system.csv")
OUT = ROOT / "outputs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 文章色板（照现稿）：暖米黄底 / 炉火橙=越限 / 管道蓝灰=顶限 / 深青黑正文
CREAM = "#FBF7EE"       # 底色 暖米黄
INK = "#173F42"         # 正文深青黑
GREY = "#7C8F91"        # 辅助灰（浅）
EMBER = "#EF8F3C"       # 炉火橙（越限基准）
EMBER_DEEP = "#C4652A"  # 炉火橙 深端
EMBER_DARK = "#8C3A14"  # 炉火橙 更深处
EMBER_LIGHT = "#F9D9BC"  # 炉火橙 浅端
STEEL = "#6E9BB0"       # 管道蓝灰（顶限/中性）

plt.rcParams["axes.facecolor"] = CREAM
plt.rcParams["figure.facecolor"] = CREAM
plt.rcParams["savefig.facecolor"] = CREAM
plt.rcParams["text.color"] = INK
plt.rcParams["axes.labelcolor"] = INK
plt.rcParams["axes.edgecolor"] = INK
plt.rcParams["xtick.color"] = INK
plt.rcParams["ytick.color"] = INK

CMAP_EXC = mcolors.LinearSegmentedColormap.from_list(
    "exc", [CREAM, EMBER_LIGHT, EMBER, EMBER_DEEP, EMBER_DARK])


def load():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
    by = {}
    for r in rows:
        by.setdefault(r["name"], []).append(r)
    K = float(rows[0]["common_K_kw"])
    out = {}
    for k, v in by.items():
        out[k] = {
            "p": np.array([float(x["power_kw"]) for x in v]),
            "bind": np.array([x["common_K_binding"].strip().lower() == "true"
                              for x in v]),
            "time": [x["time"] for x in v],
        }
    return out, K


def main():
    d, K = load()
    exc_ind = np.clip(d["independent_sum_counterfactual"]["p"] - K, 0, None)
    exc_cpl = np.clip(d["coupled_flexible"]["p"] - K, 0, None)
    bind = d["coupled_flexible"]["bind"]
    n = len(exc_ind)
    h_ind = int((exc_ind > 1e-9).sum())
    h_cpl = int((exc_cpl > 1e-9).sum())
    print(f"K={K:.7f} kW | 独立叠加越限 {h_ind} h (峰值 {exc_ind.max():.2f} kW 超出量)")
    print(f"联合优化越限 {h_cpl} h | 绑定 {int(bind.sum())} h")

    fig = plt.figure(figsize=(13.4, 7.6))
    gs = fig.add_gridspec(3, 1, height_ratios=[0.30, 0.30, 1.35], left=0.075,
                          right=0.93, top=0.88, bottom=0.10, hspace=0.62)

    # ---- a 独立叠加
    ax = fig.add_subplot(gs[0])
    ax.pcolormesh(np.arange(n + 1), [0, 1], exc_ind[None, :], cmap=CMAP_EXC,
                  vmin=0, vmax=exc_ind.max(), shading="flat")
    ax.set_yticks([])
    ax.set_xticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlim(0, n)
    ax.text(-0.048, 1.85, "a", transform=ax.transAxes, fontsize=15,
            fontweight="bold", va="bottom")
    ax.text(-0.020, 1.85, "分别最优后叠加（反事实）", transform=ax.transAxes,
            fontsize=10.5, va="bottom")
    ax.text(1.005, 0.5, f"{h_ind} h\n越限", transform=ax.transAxes, fontsize=10,
            va="center", ha="left", color=EMBER_DEEP, fontweight="bold")

    # ---- b 联合优化
    ax = fig.add_subplot(gs[1])
    ax.pcolormesh(np.arange(n + 1), [0, 1], exc_cpl[None, :], cmap=CMAP_EXC,
                  vmin=0, vmax=exc_ind.max(), shading="flat")
    ax.pcolormesh(np.arange(n + 1), [-1.05, -0.35], bind[None, :].astype(float),
                  cmap=ListedColormap(["#E8E1CE", STEEL]), vmin=0, vmax=1,
                  shading="flat")
    ax.set_ylim(-1.05, 1)
    ax.set_yticks([])
    ax.set_xticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlim(0, n)
    ax.text(-0.020, 1.85, "联合优化（保留 20–27 ℃ 调节自由度）",
            transform=ax.transAxes, fontsize=10.5, va="bottom")
    ax.text(1.005, 0.5, f"{h_cpl} h\n越限", transform=ax.transAxes, fontsize=10,
            va="center", ha="left", color="#1E6B6E", fontweight="bold")
    ax.text(-0.006, 0.72, "越限", transform=ax.transAxes, fontsize=8.5,
            color=GREY, va="center", ha="right")
    ax.text(-0.006, 0.02, "顶到上限", transform=ax.transAxes, fontsize=8.5,
            color="#33504F", va="center", ha="right")
    ax.text(1.005, 0.02, f"{int(bind.sum())} h", transform=ax.transAxes,
            fontsize=9.5, va="center", ha="left", color="#33504F")
    ax.text(-0.048, 1.85, "b", transform=ax.transAxes, fontsize=15,
            fontweight="bold", va="bottom")

    # ---- c 日历热图：越限发生在哪些日子与钟点
    ax = fig.add_subplot(gs[2])
    day = np.arange(n) // 24
    hour = np.arange(n) % 24
    G = np.full((24, int(day.max()) + 1), np.nan)
    G[hour, day] = exc_ind
    im = ax.pcolormesh(np.arange(G.shape[1] + 1), np.arange(25), G,
                       cmap=CMAP_EXC, vmin=0, vmax=exc_ind.max(), shading="flat")
    ax.set_ylabel("一日中的钟点", fontsize=9)
    ax.set_xlabel("连续运行日序号（2024-11-16 起，共 120 日）", fontsize=9)
    ax.set_yticks([0, 6, 12, 18, 24])
    ax.set_yticklabels(["00", "06", "12", "18", "24"], fontsize=8)
    ax.invert_yaxis()
    ax.text(0, 1.22, "c", transform=ax.transAxes, fontsize=15, fontweight="bold",
            va="bottom")
    ax.text(0.028, 1.22, "叠加方案越限的日—时分布", transform=ax.transAxes,
            fontsize=10.5, va="bottom")
    ax.text(0.028, 1.075,
            "橙格为超出共同配电上限的小时：229 小时中 227 小时落在谷电时段"
            "——两栋楼各自最优，都把机组压向同一段低价电",
            transform=ax.transAxes, fontsize=8.5, color=EMBER_DEEP, va="bottom")
    for hh, cnt in ((5, int((exc_ind * (hour == 5) > 1e-9).sum())),
                    (13, int((exc_ind * (hour == 13) > 1e-9).sum()))):
        ax.annotate(f"{hh:02d} 时 · {cnt} 次", xy=(60, hh + 0.5),
                    xytext=(60, hh + 5.0), fontsize=8.5, color=EMBER_DARK,
                    ha="center",
                    arrowprops=dict(arrowstyle="-", color=EMBER_DARK, lw=1.0))
    cb = fig.colorbar(im, ax=ax, fraction=0.022, pad=0.015)
    cb.set_label("超出上限的功率 kW", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    fig.text(0.075, 0.022,
             f"数据：multiscale/run_01/analysis/hourly_system.csv（rc6 研究候选）。"
             f"共同配电上限 K = {K:.4f} kW；2 栋公开原型建筑，同一 2880 小时窗口。"
             "单位为模型账本口径，不代表真实工程配电容量。",
             fontsize=7.5, color=GREY, ha="left", va="bottom")

    png = OUT / "shared_capacity_exceedance.png"
    tmp = png.with_name(png.stem + "_tmp" + png.suffix)
    fig.savefig(tmp, dpi=300, bbox_inches="tight", facecolor=CREAM)
    tmp.replace(png)
    print("saved ->", png)


if __name__ == "__main__":
    main()
