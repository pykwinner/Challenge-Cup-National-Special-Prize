# -*- coding: utf-8 -*-
"""时间口径：同一个「2880 小时」的三种含义。

数据源：docs/final_optimization_20261002/time_registry/run_final/registry.json
  —— 11 条登记时间轴（heatplan.time_axis.v1）

口径：
  sampled_interval_hours 实际采样覆盖的物理时长
  accounting_weighted_hours 记账加权小时（代表日按权重折算）
  calendar_envelope_hours 首个采样块到末个采样块之间的日历包络
  代表日轴登记 state_continuity = not established between representative blocks，
  即加权小时不等于物理连续小时。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors as mcolors
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "docs" / "final_optimization_20261002" / "time_registry" / "run_final" / "registry.json"
OUT = ROOT / "outputs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 文章色板（照现稿）：暖米黄底 / 管道蓝灰=记账权重 / 炉火橙=包络与极端日
CREAM = "#FBF7EE"        # 底色 暖米黄
INK = "#173F42"          # 正文深青黑
GREY = "#7C8F91"         # 辅助灰（浅）
LIGHT = "#D9D2C0"        # 浅底（暖灰）
EMBER = "#EF8F3C"        # 炉火橙 基准
EMBER_DEEP = "#C4652A"   # 炉火橙 深端
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

CITIES = [("urumqi", "乌鲁木齐"), ("guyuan", "固原"), ("wuhan", "武汉")]
CMAP_W = mcolors.LinearSegmentedColormap.from_list(
    "w", [STEEL_MID, STEEL, STEEL_DARK])


def parse(t):
    return datetime.fromisoformat(t)


def main():
    d = json.load(open(REG, encoding="utf-8"))
    rep = {}
    cont = None
    for e in d["entries"]:
        if e["axis"]["kind"] == "representative":
            rep[e["id"].split("_")[0]] = e
        elif e["id"] == "urumqi_common_120d":
            cont = e["axis"]

    t0 = parse(cont["start_utc"])
    t1 = parse(cont["end_exclusive_utc"])
    span_days = (t1 - t0).total_seconds() / 86400

    fig = plt.figure(figsize=(13.6, 8.4))
    gs = fig.add_gridspec(2, 1, height_ratios=[1.15, 1.0], left=0.085,
                          right=0.93, top=0.885, bottom=0.115, hspace=0.60)

    # ---------- a 采样日在采暖季中的位置
    ax = fig.add_subplot(gs[0])
    ROW = 1.55
    ticks, ticklabels = [], []
    for i, (key, cn) in enumerate(CITIES):
        a = rep[key]["axis"]
        y = (len(CITIES) - 1 - i) * ROW
        ticks.append(y)
        ticklabels.append(cn)
        # 连续对照轴
        ax.plot([0, span_days], [y + 0.40, y + 0.40], color=LIGHT, lw=3.0,
                solid_capstyle="butt", zorder=1)
        lo, hi = None, None
        for b in a["blocks"]:
            s = (parse(b["start_utc"]) - t0).total_seconds() / 86400
            e_ = (parse(b["end_exclusive_utc"]) - t0).total_seconds() / 86400
            w = b["accounting_weight"]
            lo = s if lo is None else min(lo, s)
            hi = e_ if hi is None else max(hi, e_)
            if w <= 1.0:  # 极端日：空心标记
                ax.add_patch(plt.Rectangle((s, y - 0.20), e_ - s, 0.40,
                                           facecolor=CREAM, edgecolor=EMBER_DEEP,
                                           lw=1.3, zorder=3))
            else:
                ax.add_patch(plt.Rectangle((s, y - 0.20), e_ - s, 0.40,
                                           facecolor=CMAP_W((w - 0.5) / 28.0),
                                           edgecolor=CREAM, lw=0.8, zorder=3))
                ax.text((s + e_) / 2, y, f"{w:.0f}", fontsize=7.5, ha="center",
                        va="center", color=CREAM, zorder=4)
        # 日历包络括线
        ax.plot([lo, hi], [y - 0.42, y - 0.42], color=EMBER_DEEP, lw=1.6, zorder=3)
        for xx in (lo, hi):
            ax.plot([xx, xx], [y - 0.50, y - 0.34], color=EMBER_DEEP, lw=1.6,
                    zorder=3)
        ax.text(hi + 2.0, y - 0.42, f"包络 {a['calendar_envelope_hours']:.0f} h",
                fontsize=8, va="center", color=EMBER_DEEP)

    ax.set_xlim(-0.5, span_days + 16)
    ax.set_ylim(-0.95, (len(CITIES) - 1) * ROW + 0.72)
    ax.set_yticks(ticks)
    ax.set_yticklabels(ticklabels, fontsize=10.5)
    xt = np.arange(0, span_days + 1, 20)
    ax.set_xticks(xt)
    ax.set_xticklabels([datetime.fromordinal(t0.toordinal() + int(v))
                        .strftime("%m-%d") for v in xt], fontsize=8)
    ax.set_xlabel("采暖季日历日（2024-11-16 起，共 120 日）", fontsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(LIGHT)
    ax.tick_params(axis="y", length=0)
    ax.text(-0.078, 1.16, "a", transform=ax.transAxes, fontsize=15,
            fontweight="bold", va="bottom")
    ax.text(-0.055, 1.16, "代表日落在采暖季的哪些位置", transform=ax.transAxes,
            fontsize=10.5, va="bottom")
    ax.text(-0.055, 1.075,
            "每城 8 个采样日、192 行。灰带 = 连续 120 日对照轴；实心块 = 聚类代表日，"
            "块内数字为其记账权重（日）；橙空心块 = 权重 1 日的极端日；橙括线 = 日历包络",
            transform=ax.transAxes, fontsize=8.5, color=GREY, va="bottom")

    cb = fig.colorbar(plt.cm.ScalarMappable(
        norm=mcolors.Normalize(0, 28), cmap=CMAP_W), ax=ax, fraction=0.026,
        pad=0.015)
    cb.set_label("该采样日的记账权重（日）", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    # ---------- b 三种口径的长度对照
    ax = fig.add_subplot(gs[1])
    rows = []
    for key, cn in CITIES:
        a = rep[key]["axis"]
        rows.append((cn,
                     a["rows"] / 24.0,                       # 采样覆盖（日）
                     a["calendar_envelope_hours"] / 24.0,    # 日历包络（日）
                     a["accounting_weighted_hours"] / 24.0))  # 加权（日）
    ymax = len(rows)
    band_h = 0.20
    styles = [("采样覆盖", STEEL_MID), ("日历包络", EMBER),
              ("记账加权", STEEL_DARK)]
    for i, (cn, samp, env, wt) in enumerate(rows):
        y = ymax - 1 - i
        for j, (nm, col) in enumerate(styles):
            val = (samp, env, wt)[j]
            yy = y + (1 - j) * (band_h + 0.055)
            ax.add_patch(plt.Rectangle((0, yy - band_h / 2), val, band_h,
                                       facecolor=col, edgecolor="none"))
            ax.text(val + 2.0, yy, f"{val:.0f} 日", fontsize=8.5, va="center",
                    color=INK)
        ax.text(-3.0, y, cn, fontsize=10.5, ha="right", va="center")
    for j, (nm, col) in enumerate(styles):
        yy = ymax - 0.25 + (1 - j) * (band_h + 0.055)
        ax.add_patch(plt.Rectangle((0, yy - band_h / 2), 6, band_h,
                                   facecolor=col, edgecolor="none"))
        ax.text(8.0, yy, nm, fontsize=9, va="center", color=INK)
    ax.axvline(120, color=EMBER_DEEP, ls="--", lw=1.3)
    ax.text(120, ymax + 0.42, "120 日（采暖季）", fontsize=8.5,
            color=EMBER_DEEP, ha="center")
    ax.set_xlim(-0.5, 138)
    ax.set_ylim(-0.75, ymax + 0.35)
    ax.set_yticks([])
    ax.set_xlabel("折算天数（日）", fontsize=9)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.text(-0.078, 1.14, "b", transform=ax.transAxes, fontsize=15,
            fontweight="bold", va="bottom")
    ax.text(-0.055, 1.14, "同一个「2880 小时」的三种长度", transform=ax.transAxes,
            fontsize=10.5, va="bottom")
    ax.text(-0.055, 1.055,
            "三城代表日轴均登记 state_continuity = not established between representative blocks",
            transform=ax.transAxes, fontsize=8.5, color=EMBER_DEEP, va="bottom")

    fig.text(0.085, 0.018,
             "数据：docs/final_optimization_20261002/time_registry/run_final/registry.json"
             "（rc6 研究候选）。时间口径不得解释为数据同源、气象、价格、资产或最优性证明；"
             "本图仅说明三种小时数的差别。",
             fontsize=7.5, color=GREY, ha="left", va="bottom")

    png = OUT / "time_axis_three_meanings.png"
    tmp = png.with_name(png.stem + "_tmp" + png.suffix)
    fig.savefig(tmp, dpi=300, bbox_inches="tight", facecolor=CREAM)
    tmp.replace(png)
    for cn, s, e_, w in rows:
        print(f"{cn}: 采样 {s:.0f} 日 | 包络 {e_:.0f} 日 | 加权 {w:.0f} 日")
    print("saved ->", png)


if __name__ == "__main__":
    main()
