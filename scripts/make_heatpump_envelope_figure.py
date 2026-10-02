# -*- coding: utf-8 -*-
"""真机性能图谱与电加热兜底负担。

数据源：
  docs/final_optimization_20261002/heatpump/run_01/admitted_heating_points.csv
      —— 准入的厂家样本工况点（165 点，天加 TCAV035BHE 83 + TCAV065BHE 82）
  docs/final_optimization_20261002/heatpump/optimization_01/<city>__<model>_n1/dispatch.csv
      —— 三城 2880 小时逐时调度

口径：
  兜底份额 = resistance_heat_kw / (hp_heat_kw + resistance_heat_kw)
  热泵出力受 available_hp_heat_kw（该工况下的样本能力）约束，差额由电加热补足。
  工况点来源为厂家样本（manufacturer_catalogue_sample_not_measurement），非第三方实测。
"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors as mcolors
from matplotlib.colors import BoundaryNorm, ListedColormap

ROOT = Path(__file__).resolve().parents[1]
HP = ROOT / "docs" / "final_optimization_20261002" / "heatpump"
OUT = ROOT / "outputs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

CITIES = [("urumqi", "乌鲁木齐"), ("guyuan", "固原"), ("wuhan", "武汉")]
MODELS = ["TCAV035BHE", "TCAV065BHE"]

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 文章色板（照现稿）：暖米黄底 / 管道蓝灰=COP 场 / 炉火橙=兜底负担 / 深青黑正文
CREAM = "#FBF7EE"        # 底色 暖米黄
INK = "#173F42"          # 正文深青黑
GREY = "#7C8F91"         # 辅助灰（浅）
LIGHT = "#D9D2C0"        # 浅底（暖灰）
EMBER = "#EF8F3C"        # 炉火橙（兜底基准）
EMBER_DEEP = "#C4652A"   # 炉火橙 深端
EMBER_DARK = "#8C3A14"   # 炉火橙 更深处
EMBER_LIGHT = "#F9D9BC"  # 炉火橙 浅端
STEEL = "#6E9BB0"        # 管道蓝灰（COP 基准）
STEEL_LIGHT = "#DDE6DE"  # 管道蓝灰 浅端
STEEL_DARK = "#33504F"   # 管道蓝灰 深端

plt.rcParams["axes.facecolor"] = CREAM
plt.rcParams["figure.facecolor"] = CREAM
plt.rcParams["savefig.facecolor"] = CREAM
plt.rcParams["text.color"] = INK
plt.rcParams["axes.labelcolor"] = INK
plt.rcParams["axes.edgecolor"] = INK
plt.rcParams["xtick.color"] = INK
plt.rcParams["ytick.color"] = INK

CMAP_COP = mcolors.LinearSegmentedColormap.from_list(
    "cop", [STEEL_LIGHT, STEEL, STEEL_DARK])
CMAP_BACKUP = mcolors.LinearSegmentedColormap.from_list(
    "backup", [CREAM, EMBER_LIGHT, EMBER, EMBER_DEEP, EMBER_DARK])


def load_admitted():
    rows = list(csv.DictReader(
        open(HP / "run_01" / "admitted_heating_points.csv", encoding="utf-8-sig")))
    out = {}
    for r in rows:
        out.setdefault(r["model"], []).append(
            (float(r["ambient_c"]), float(r["leaving_water_c"]),
             float(r["q_kw"]), float(r["cop_derived"])))
    return out


def load_dispatch(city: str, model: str):
    f = HP / "optimization_01" / f"{city}__{model}_n1" / "dispatch.csv"
    rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
    amb = np.array([float(r["outdoor_temp_c"]) for r in rows])
    hp = np.array([float(r["hp_heat_kw"] or 0) for r in rows])
    res = np.array([float(r["resistance_heat_kw"] or 0) for r in rows])
    cap = np.array([float(r["available_hp_heat_kw"] or 0) for r in rows])
    lw = np.array([float(r["leaving_water_c"]) for r in rows])
    tot = hp + res
    share = np.divide(res, tot, out=np.zeros_like(res), where=tot > 0)
    return amb, hp, res, cap, share, lw


def cop_grid(points):
    """把样本点铺成 (ambient × leaving) 网格，缺格掩蔽。"""
    amb = np.array(sorted({p[0] for p in points}))
    lw = np.array(sorted({p[1] for p in points}))
    g = np.full((len(lw), len(amb)), np.nan)
    for a, l, _q, cop in points:
        g[np.where(lw == l)[0][0], np.where(amb == a)[0][0]] = cop
    return amb, lw, g


def draw_cop_field(ax, points, op_lo, op_hi, letter, model_label, op_row):
    """COP 场。只绘采暖相关档位；标出三城实际运行区间与出水工况行。"""
    pts = [p for p in points if p[0] <= 35.0]  # 48/55 ℃ 为非采暖工况档
    dropped = len(points) - len(pts)
    amb, lw, g = cop_grid(pts)
    gm = np.ma.masked_invalid(g)
    vmin, vmax = np.nanmin(g), np.nanmax(g)
    pcm = ax.pcolormesh(np.arange(len(amb) + 1) - 0.5,
                        np.arange(len(lw) + 1) - 0.5, gm,
                        cmap=CMAP_COP, vmin=vmin, vmax=vmax,
                        edgecolors=LIGHT, linewidth=0.4)
    ax.set_xticks(range(len(amb)))
    ax.set_xticklabels([f"{a:.0f}" for a in amb], fontsize=7.5)
    ax.set_yticks(range(len(lw)))
    ax.set_yticklabels([f"{l:.0f}" for l in lw], fontsize=7.5)
    ax.set_xlabel("源侧（室外）温度 ℃", fontsize=9)
    ax.set_ylabel("出水温度 ℃", fontsize=9)

    # 三城实际运行温度区间：竖直色带（画在网格之下，不遮数据）
    x0 = np.interp(op_lo, amb, np.arange(len(amb)))
    x1 = np.interp(op_hi, amb, np.arange(len(amb)))
    ax.axvspan(x0, x1, ymin=0.0, ymax=0.055, facecolor=EMBER, alpha=0.9,
               zorder=6, clip_on=False)
    ax.text((x0 + x1) / 2, -0.128, f"三城运行区间 {op_lo:.0f} ~ {op_hi:.0f} ℃",
            transform=ax.get_xaxis_transform(), fontsize=7.5, ha="center",
            va="top", color=EMBER_DEEP)

    # 实际出水工况行
    iw = int(np.argmin(np.abs(lw - op_row)))
    ax.add_patch(plt.Rectangle((-0.5, iw - 0.5), len(amb), 1, fill=False,
                               edgecolor=EMBER_DEEP, lw=1.8, zorder=7))

    ax.set_title(f"{model_label}　样本工况点 COP 场（{len(pts)} 点）",
                 fontsize=10, pad=10)
    ax.text(-0.02, 1.075, letter, transform=ax.transAxes, fontsize=15,
            fontweight="bold", ha="left", va="bottom")
    note = "样本为准入厂家样本，非第三方实测；橙框为三城实际出水工况行"
    if dropped:
        note += f"；{dropped} 个非采暖工况点未绘"
    ax.text(0.0, -0.20, note, transform=ax.transAxes, fontsize=7,
            color=GREY, va="top")
    return pcm


def main():
    admitted = load_admitted()
    disp = {(c, m): load_dispatch(c, m) for c, _ in CITIES for m in MODELS}

    spans = {}
    for c, cn in CITIES:
        lo = min(disp[(c, m)][0].min() for m in MODELS)
        hi = max(disp[(c, m)][0].max() for m in MODELS)
        spans[cn] = (lo, hi)
        print(f"{cn} 室外温度跨度 {lo:.1f} → {hi:.1f} ℃")
    op_lo = min(v[0] for v in spans.values())
    op_hi = max(v[1] for v in spans.values())
    print(f"三城合计运行区间 {op_lo:.1f} → {op_hi:.1f} ℃")
    op_rows = {round(float(d[5].mean()), 1) for d in disp.values()}
    op_row = sorted(op_rows)[0]
    print(f"出水工况行 {op_row:.1f} ℃（各格一致={len(op_rows)==1}）")

    fig = plt.figure(figsize=(13.6, 8.8))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 0.72], left=0.055,
                          right=0.965, top=0.90, bottom=0.10,
                          wspace=0.20, hspace=0.62)

    pcm = None
    for j, m in enumerate(MODELS):
        ax = fig.add_subplot(gs[0, j])
        pcm = draw_cop_field(ax, admitted[m], op_lo, op_hi, "ab"[j], m, op_row)

    cb = fig.colorbar(pcm, ax=fig.axes[:2], fraction=0.024, pad=0.02)
    cb.set_label("样本 COP（制热量/输入功率）", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    # ---- 下排：兜底份额时序热条
    ax = fig.add_subplot(gs[1, :])
    n = 2880
    M = np.full((len(CITIES) * len(MODELS), n), np.nan)
    labels = []
    for i, (c, cn) in enumerate(CITIES):
        for j, m in enumerate(MODELS):
            _amb, _hp, _res, _cap, share, _lw = disp[(c, m)]
            M[i * 2 + j] = share
            labels.append(f"{cn} · {m}")

    im = ax.pcolormesh(np.arange(n + 1), np.arange(len(labels) + 1), M,
                       cmap=CMAP_BACKUP, vmin=0, vmax=1, shading="flat")
    ax.set_yticks(np.arange(len(labels)) + 0.5)
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.invert_yaxis()

    xt = np.linspace(0, n, 7)
    ax.set_xticks(xt)
    ax.set_xticklabels([f"{int(v)}" for v in xt], fontsize=8)
    ax.set_xlabel("连续运行小时序号（2024-11-16 起 120 日，共 2880 小时）", fontsize=9)

    for i in range(1, len(labels)):
        ax.axhline(i, color=CREAM, lw=1.2)
    for i in (2, 4):
        ax.axhline(i, color=INK, lw=1.6)

    # 每行右侧标兜底小时数
    for i, lab in enumerate(labels):
        c, cn = CITIES[i // 2]
        m = MODELS[i % 2]
        _amb, _hp, res, _cap, share, _lw = disp[(c, m)]
        hrs = int((res > 0).sum())
        ax.text(n + 40, i + 0.5, f"{hrs} h", fontsize=8.5, va="center",
                color=EMBER_DEEP if hrs > 1000 else INK, fontweight="bold")

    ax.text(-0.075, 1.16, "c", transform=ax.transAxes, fontsize=15,
            fontweight="bold", ha="left", va="bottom")
    ax.text(-0.055, 1.16, "电加热兜底份额的逐时分布", transform=ax.transAxes,
            fontsize=10, ha="left", va="bottom")
    ax.text(-0.055, 1.075, "份额 = 电加热供热量 /(热泵供热量 + 电加热供热量)；浅米黄为纯热泵覆盖",
            transform=ax.transAxes, fontsize=8, color=GREY)

    cb2 = fig.colorbar(im, ax=ax, fraction=0.020, pad=0.075)
    cb2.set_label("电加热兜底份额", fontsize=9)
    cb2.ax.tick_params(labelsize=8)

    fig.text(0.055, 0.018,
             "数据：docs/final_optimization_20261002/heatpump（rc6）。工况来源为厂家样本，"
             "非第三方实测；三城为公开原型建筑 + 同一方法学价格，属研究候选结果，不代表真实工程效果。",
             fontsize=7.5, color=GREY, ha="left", va="bottom")

    png = OUT / "heatpump_envelope_and_backup.png"
    tmp = png.with_name(png.stem + "_tmp" + png.suffix)
    fig.savefig(tmp, dpi=300, bbox_inches="tight", facecolor=CREAM)
    tmp.replace(png)
    print("saved ->", png)


if __name__ == "__main__":
    main()
