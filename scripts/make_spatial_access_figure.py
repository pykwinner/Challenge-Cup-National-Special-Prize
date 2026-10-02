# -*- coding: utf-8 -*-
"""空间接入的两道关：吸附阈值与绕行倍率。

数据源：docs/execution_20261002/spatial_reference/{urumqi,guyuan,wuhan}/
  snaps.csv    —— 6 栋固定建筑到最近道路的吸附距离与状态
  routes.csv   —— 已吸附建筑之间的参考路线长度与直线距离

口径（照录仓库 README）：
  吸附线仅从建筑质心连到道路，不是实测入口，也不是合法支管；
  150 m 超距与失联原样记录，不换点、不删点。
  本图为原型空间包，非实测接入。
"""
from __future__ import annotations

import csv
import zlib
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "execution_20261002" / "spatial_reference"
OUT = ROOT / "outputs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

CITIES = [("urumqi", "乌鲁木齐"), ("guyuan", "固原"), ("wuhan", "武汉")]
THRESHOLD_M = 150.0

# 文章色板（照现稿）：暖米黄底 / 管道蓝灰=吸附成功与直段 / 炉火橙=超距失败
CREAM = "#FBF7EE"        # 底色 暖米黄
INK = "#173F42"          # 正文深青黑
GREY = "#7C8F91"         # 辅助灰（浅）
LIGHT = "#D9D2C0"        # 浅底（暖灰）
EMBER_DEEP = "#C4652A"   # 炉火橙 深端（失败/超距）
STEEL = "#6E9BB0"        # 管道蓝灰 基准（成功）
STEEL_DARK = "#33504F"   # 管道蓝灰 深端

C_OK, C_FAIL = STEEL, EMBER_DEEP

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

plt.rcParams["axes.facecolor"] = CREAM
plt.rcParams["figure.facecolor"] = CREAM
plt.rcParams["savefig.facecolor"] = CREAM
plt.rcParams["text.color"] = INK
plt.rcParams["axes.labelcolor"] = INK
plt.rcParams["axes.edgecolor"] = INK
plt.rcParams["xtick.color"] = INK
plt.rcParams["ytick.color"] = INK


def load(city):
    snaps = list(csv.DictReader(open(SRC / city / "snaps.csv", encoding="utf-8-sig")))
    routes = list(csv.DictReader(open(SRC / city / "routes.csv", encoding="utf-8-sig")))
    return snaps, routes


def main():
    data = {c: load(c) for c, _ in CITIES}

    for c, cn in CITIES:
        s, r = data[c]
        ok = sum(1 for x in s if x["status"] == "snapped")
        bad = sum(1 for x in s if x["status"] != "snapped")
        rr = [float(x["detour_ratio_total_over_straight"]) for x in r
              if x["status"] == "routed"]
        print(f"{cn}: 吸附 {ok}/{len(s)} 成功 | 路线 {len(rr)} 条, 绕行倍率 "
              f"{min(rr):.2f}~{max(rr):.2f}" if rr else f"{cn}: 无路线")

    fig, (axL, axR) = plt.subplots(1, 2, figsize=(13.8, 5.9))
    fig.subplots_adjust(left=0.085, right=0.975, top=0.80, bottom=0.155,
                        wspace=0.28)

    # ---------------- a 吸附距离
    ROW = 1.0
    for i, (c, cn) in enumerate(CITIES):
        snaps, _ = data[c]
        y = (len(CITIES) - 1 - i) * ROW
        # 失败点按距离排序后固定错开，保证每一个都数得出来
        fails = sorted([x for x in snaps if x["status"] != "snapped"],
                       key=lambda x: float(x["distance_m"]))
        off = {x["osm_id"]: (k - (len(fails) - 1) / 2) * 0.30
               for k, x in enumerate(fails)}
        for x in snaps:
            d = float(x["distance_m"])
            ok = x["status"] == "snapped"
            if ok:
                # 确定性抖动：crc32 跨进程稳定，保证同数据同图
                jit = (zlib.crc32(x["osm_id"].encode()) % 1000 / 1000.0
                       - 0.5) * 0.30
            else:
                jit = off[x["osm_id"]]
            axL.plot([d], [y + jit],
                     marker="o", ms=8 if ok else 9,
                     color=C_OK if ok else C_FAIL,
                     markerfacecolor=C_OK if ok else CREAM,
                     markeredgewidth=1.6 if not ok else 1.0,
                     markeredgecolor=C_OK if ok else C_FAIL, zorder=3)
        n_bad = sum(1 for x in snaps if x["status"] != "snapped")
        axL.text(0.42, y, cn, fontsize=10.5, ha="right", va="center")
        lbl = f"{len(snaps)-n_bad}/{len(snaps)} 吸附成功"
        axL.text(300, y, lbl, fontsize=8.5, ha="left", va="center",
                 color=C_FAIL if n_bad else INK)
    axL.axvline(THRESHOLD_M, color=C_FAIL, ls="--", lw=1.5, zorder=1)
    axL.text(THRESHOLD_M, len(CITIES) - 0.42, "150 m 吸附阈值",
             fontsize=8.5, color=C_FAIL, ha="center")
    axL.set_xscale("log")
    axL.set_xlim(0.5, 900)
    axL.set_ylim(-0.6, len(CITIES) - 0.3)
    axL.set_yticks([])
    axL.set_xticks([1, 10, 100, 150])
    axL.set_xticklabels(["1", "10", "100", "150"], fontsize=8)
    axL.set_xlabel("建筑质心到最近道路的距离（m，对数轴）", fontsize=9)
    for s in ("top", "right", "left"):
        axL.spines[s].set_visible(False)
    axL.tick_params(axis="y", length=0)
    axL.grid(axis="x", color="#E8E1CE", lw=0.7, zorder=0)
    axL.text(-0.075, 1.14, "a", transform=axL.transAxes, fontsize=15,
             fontweight="bold", va="bottom")
    axL.text(-0.050, 1.14, "第一关：能不能吸附到路网", transform=axL.transAxes,
             fontsize=10.5, va="bottom")
    axL.text(-0.050, 1.055, "实心点 = 吸附成功，橙空心 = 超过 150 m 吸附失败；"
                            "吸附线为质心到道路，不是实测入口",
             transform=axL.transAxes, fontsize=8.5, color=GREY, va="bottom")

    # ---------------- b 绕行倍率
    ratios = {}
    for c, cn in CITIES:
        _, r = data[c]
        ratios[c] = [float(x["detour_ratio_total_over_straight"]) for x in r
                     if x["status"] == "routed"]
    maxr = max(max(v) for v in ratios.values())
    for i, (c, cn) in enumerate(CITIES):
        y = (len(CITIES) - 1 - i) * ROW
        rs = ratios[c]
        n = len(rs)
        step = 0.20 if n > 1 else 0.0
        for j, rr in enumerate(sorted(rs)):
            yy = y + (j - (n - 1) / 2) * step
            h = 0.115
            axR.add_patch(plt.Rectangle((0, yy - h / 2), 1.0, h,
                                        facecolor=LIGHT, edgecolor="none"))
            axR.add_patch(plt.Rectangle((1.0, yy - h / 2), rr - 1.0, h,
                                        facecolor=C_OK, edgecolor="none"))
            axR.text(rr + 0.10, yy, f"{rr:.2f}", fontsize=8, va="center",
                     color=STEEL_DARK)
        axR.text(-0.16, y, cn, fontsize=10.5, ha="right", va="center")
        if not rs:
            axR.text(0.05, y, "无成功路线", fontsize=9, color=C_FAIL,
                     va="center")
    axR.axvline(1.0, color=GREY, lw=1.0, ls=":", zorder=1)
    axR.text(1.0, len(CITIES) - 0.42, "1.0 = 直线距离", fontsize=8.5,
             color=GREY, ha="center")
    axR.set_xlim(-0.05, maxr + 1.15)
    axR.set_ylim(-0.6, len(CITIES) - 0.3)
    axR.set_yticks([])
    axR.set_xlabel("总参考长度 / 直线距离（倍）", fontsize=9)
    for s in ("top", "right", "left"):
        axR.spines[s].set_visible(False)
    axR.tick_params(axis="y", length=0)
    axR.grid(axis="x", color="#E8E1CE", lw=0.7, zorder=0)
    axR.text(-0.075, 1.14, "b", transform=axR.transAxes, fontsize=15,
             fontweight="bold", va="bottom")
    axR.text(-0.050, 1.14, "第二关：吸附上了，管线要绕多远", transform=axR.transAxes,
             fontsize=10.5, va="bottom")
    axR.text(-0.050, 1.055, "灰段 = 直线距离，蓝段 = 绕行多出的长度；"
                            "数字为总长与直线之比",
             transform=axR.transAxes, fontsize=8.5, color=GREY, va="bottom")

    fig.text(0.085, 0.030,
             "数据：docs/execution_20261002/spatial_reference（原型空间包，非实测接入）。"
             "每城 6 栋固定 OSM 建筑；道路与吸附为一次读取，不声称原子城市快照。"
             "超过 150 m 与失联的建筑原样保留、不换点。",
             fontsize=7.5, color=GREY, ha="left", va="bottom")

    png = OUT / "spatial_access_two_gates.png"
    tmp = png.with_name(png.stem + "_tmp" + png.suffix)
    fig.savefig(tmp, dpi=300, bbox_inches="tight", facecolor=CREAM)
    tmp.replace(png)
    print("saved ->", png)


if __name__ == "__main__":
    main()
