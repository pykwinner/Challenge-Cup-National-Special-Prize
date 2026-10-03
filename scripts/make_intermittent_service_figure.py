"""间歇供热对照图：连续服务 vs 作息间歇服务（06—09、17—23，空置下限 12 ℃）。

数据源：
  outputs/sensitivity_intermittent_20261003/run_continuous_control  （无 service_schedule，等价于连续口径）
  outputs/sensitivity_intermittent_20261003/run_intermittent        （含 service_schedule）

两者由同一派生脚本产生；连续对照组与既有 27 ℃ 对照逐位一致（差 0.000e+00）。
放大窗口按预先声明的规则选取：室外温度最低的连续 7 天（不按观感挑选）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/sensitivity_intermittent_20261003"
OUT = ROOT / "outputs/figures/intermittent_service.png"

BG = "#FBF7EE"
TEAL = "#1E6B6E"
ORANGE = "#EF8F3C"
BLUE = "#6E9BB0"
GRAY = "#9A9A9A"
INK = "#1A1A1A"

plt.rcParams.update({
    "font.family": ["Noto Sans SC", "Microsoft YaHei"],
    "axes.unicode_minus": False,
    "figure.facecolor": BG, "axes.facecolor": BG,
    "text.color": INK, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.edgecolor": "#9A9A9A",
})

OCC = [(6, 9), (17, 23)]


def load(run: str, case: str):
    d = list(csv.DictReader(open(BASE / run / case / "dispatch.csv", encoding="utf-8")))
    s = json.loads((BASE / run / case / "summary.json").read_text(encoding="utf-8"))
    T = np.array([float(r["temperature_end_c"]) for r in d])
    t = pd.DatetimeIndex(pd.to_datetime([r["time"] for r in d]))
    return T, t, s


def main() -> None:
    Tc, tc, sc = load("run_continuous_control", "compromise")
    Ti, ti, si = load("run_intermittent", "compromise")
    _, _, sb_c = load("run_continuous_control", "baseline")
    sb_i = json.loads((BASE / "run_intermittent" / "baseline" / "summary.json").read_text(encoding="utf-8"))

    # 预先声明的窗口规则：室外温度（逐时）最低的连续 7 天
    inp = list(csv.DictReader(open(BASE / "run_continuous_control" / "input_bundle" / "input.csv", encoding="utf-8")))
    tout = np.array([float(r["outdoor_temp"]) for r in inp])
    day_mean = tout.reshape(-1, 24).mean(axis=1)
    start = int(np.argmin([day_mean[i:i + 7].mean() for i in range(len(day_mean) - 6)]))
    a, b = start * 24, (start + 7) * 24
    hour_of_day = np.arange(a, b) % 24
    occ_mask = np.array([any(lo <= h < hi for lo, hi in OCC) for h in hour_of_day])

    fig, axes = plt.subplots(1, 2, figsize=(13.4, 5.4), gridspec_kw={"width_ratios": [1.55, 1]})
    fig.subplots_adjust(left=0.062, right=0.985, top=0.80, bottom=0.155, wspace=0.20)

    # 左：典型周温度轨迹
    ax = axes[0]
    for s in range(7):
        ax.axvspan(a + s * 24, a + s * 24 + 9, color=ORANGE, alpha=0.055, lw=0)
        ax.axvspan(a + s * 24 + 17, a + s * 24 + 23, color=ORANGE, alpha=0.055, lw=0)
    ax.axhspan(20, 27, color="#7FA05C", alpha=0.10, lw=0)
    ax.axhline(12, color=GRAY, lw=1.0, ls="--")
    ax.plot(np.arange(a, b), Tc[a:b], color=GRAY, lw=1.4, label="连续服务")
    ax.plot(np.arange(a, b), Ti[a:b], color=TEAL, lw=1.4, label="作息间歇服务")
    ax.text(a + 1, 12.35, "退温下限 12 ℃", fontsize=9, color="#666666")
    ax.text(a + 1, 26.2, "舒适带 20—27 ℃（底色）　橙色底为服务时段", fontsize=9, color="#555555")
    ax.set_ylim(10.5, 28.5)
    ax.set_xlim(a, b)
    ax.set_xticks([a + s * 24 for s in range(8)])
    ax.set_xticklabels([f"第{s}天" for s in range(8)], fontsize=9)
    ax.set_ylabel("室内温度（℃）", fontsize=11)
    ax.set_title("室外温度最低的连续 7 天（预先声明的窗口规则）", fontsize=11, pad=8)
    ax.legend(fontsize=9.5, frameon=False, loc="lower right")

    # 右：三项指标对比
    ax = axes[1]
    items = ["窗口电费\n(元)", "窗口碳排\n(kg)", "电量\n(kWh)"]
    cont = [sc["window_energy_bill_cny"], sc["window_co2_kg"], sc["electricity_kwh"]]
    itmt = [si["window_energy_bill_cny"], si["window_co2_kg"], si["electricity_kwh"]]
    xs = np.arange(3)
    w = 0.36
    ax.bar(xs - w / 2, cont, w, color=GRAY, label="连续服务")
    ax.bar(xs + w / 2, itmt, w, color=TEAL, label="作息间歇服务")
    for x, v in zip(xs - w / 2, cont):
        ax.annotate(f"{v:,.0f}", (x, v), textcoords="offset points", xytext=(0, 5), ha="center", fontsize=9, color="#555555")
    for x, v in zip(xs + w / 2, itmt):
        ax.annotate(f"{v:,.0f}", (x, v), textcoords="offset points", xytext=(0, 5), ha="center", fontsize=9, color=TEAL)

    for i in range(3):
        pct = (itmt[i] - cont[i]) / cont[i] * 100
        ax.annotate(f"{pct:+.1f}%", (i, max(cont[i], itmt[i])), textcoords="offset points",
                    xytext=(0, 22), ha="center", fontsize=10, color=ORANGE)
    ax.set_xticks(xs)
    ax.set_xticklabels(items, fontsize=10)
    ax.set_ylim(0, max(cont) * 1.38)
    ax.set_title("折中候选方案：分期窗口三项指标", fontsize=11, pad=8)
    ax.legend(fontsize=9.5, frameon=False, loc="upper right")

    fig.suptitle("作息间歇供热：省运行费，但推高设备容量需求", fontsize=15, y=0.955)
    fig.text(0.062, 0.865,
             f"服务时段 06—09 与 17—23，空置下限 12 ℃（声明的情景假设，非实测作息）。"
             f"省费比例由 {((sb_c['window_energy_bill_cny']-sc['window_energy_bill_cny'])/sb_c['window_energy_bill_cny']*100):.2f}% "
             f"微升至 {((sb_i['window_energy_bill_cny']-si['window_energy_bill_cny'])/sb_i['window_energy_bill_cny']*100):.2f}%",
             fontsize=10)
    fig.text(0.062, 0.045,
             "参数化条件投资实验 · 理想设计设备与设计代理 COP · 单栋原型 · 不含储热与管网 · "
             "连续对照组与既有 27 ℃ 对照逐位一致（差 0.000e+00）",
             fontsize=8.5, color="#555555")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300)
    plt.close(fig)
    print(f"saved {OUT}")
    print(f"窗口起于第 {start} 天")


if __name__ == "__main__":
    main()
