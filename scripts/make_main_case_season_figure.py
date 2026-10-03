"""主案例全季运行图（武汉 2024-11-16 → 2025-03-16，2880 h）。

数据源：docs/implementation_20261002_followup/parallel_stage_03/investment/run_03
不改任何源结果，只读取已保存的 input.csv 与 dispatch.csv 重绘。
RC 残差在本脚本内独立复算，用于核对保存轨迹，不引用审计文件的数值。
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "docs/implementation_20261002_followup/parallel_stage_03/investment/run_03"
CASE = "compromise"
OUT = ROOT / "outputs/figures/main_case_season.png"

BG = "#FBF7EE"
TEAL = "#1E6B6E"
ORANGE = "#EF8F3C"
BLUE = "#6E9BB0"
OLIVE = "#7FA05C"
INK = "#1A1A1A"

plt.rcParams.update(
    {
        "font.family": ["Noto Sans SC", "Microsoft YaHei"],
        "axes.unicode_minus": False,
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "text.color": INK,
        "axes.labelcolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.edgecolor": "#9A9A9A",
    }
)

PHYS = {"H": 0.1043, "C": 0.535576, "T0": 20.0, "TMIN": 20.0, "TMAX": 27.0, "DT": 1.0}
GRID_LIMIT_KW = 4.0
BUCKET_BANDS = [
    ("谷", [0, 1, 2, 3, 4, 5, 12, 13], OLIVE),
    ("平", [6, 7, 8, 9, 10, 11, 14, 15], BLUE),
    ("峰", [16, 17, 20, 21, 22, 23], ORANGE),
    ("尖", [18, 19], "#C0392B"),
]
HOUR_BUCKET = {h: (label, color) for label, hours, color in BUCKET_BANDS for h in hours}


def load(name: str) -> list[dict]:
    with (RUN / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def rc_residual(inp: list[dict], dis: list[dict]) -> float:
    a = math.exp(-PHYS["H"] * PHYS["DT"] / PHYS["C"])
    prev, worst = PHYS["T0"], 0.0
    for ri, rd in zip(inp, dis):
        gains = float(ri["internal_gain_kw"]) + float(ri["solar_gain_kw"])
        net = float(rd["q_hp_kw"]) + float(rd["q_resistance_kw"]) - float(rd["q_cooling_kw"])
        pred = a * prev + (1 - a) * float(ri["outdoor_temp"]) + ((1 - a) / PHYS["H"]) * (net + gains)
        worst = max(worst, abs(pred - float(rd["temperature_end_c"])))
        prev = float(rd["temperature_end_c"])
    return worst


def main() -> None:
    inp = load("input_bundle/input.csv")
    dis = load(f"{CASE}/dispatch.csv")
    n = len(dis)

    tout = np.array([float(r["outdoor_temp"]) for r in inp])
    tin = np.array([float(r["temperature_end_c"]) for r in dis])
    q_hp = np.array([float(r["q_hp_kw"]) for r in dis])
    q_res = np.array([float(r["q_resistance_kw"]) for r in dis])
    elec = np.array([float(r["electricity_kw"]) for r in dis])

    residual = rc_residual(inp, dis)
    comfort_viol = int(np.sum((tin < PHYS["TMIN"] - 1e-9) | (tin > PHYS["TMAX"] + 1e-9)))
    cap_viol = int(np.sum(elec > GRID_LIMIT_KW + 1e-9))
    at_top = int(np.sum(tin > PHYS["TMAX"] - 0.1))
    at_bottom = int(np.sum(tin < PHYS["TMIN"] + 0.05))

    grid = tin.reshape(n // 24, 24).T  # 24 x 120
    days = np.arange(n // 24)

    fig = plt.figure(figsize=(13.6, 10.2))
    gs = fig.add_gridspec(
        4, 1, height_ratios=[0.85, 1.5, 0.9, 0.9],
        hspace=0.30, left=0.085, right=0.985, top=0.885, bottom=0.105,
    )

    day_ticks = list(range(0, 2881, 480))

    def set_day_axis(axis, label: bool = False) -> None:
        axis.set_xlim(0, n)
        axis.set_xticks(day_ticks)
        axis.set_xticklabels([f"{t // 24} 天" for t in day_ticks], fontsize=9)
        if not label:
            axis.tick_params(labelbottom=False)

    # 1 室外温度
    ax = fig.add_subplot(gs[0])
    ax.plot(np.arange(n), tout, color=BLUE, lw=0.7)
    ax.set_ylabel("室外温度\n(℃)", fontsize=11)
    set_day_axis(ax)

    # 2 室内温度日×小时热力图
    ax = fig.add_subplot(gs[1])
    im = ax.imshow(
        grid, aspect="auto", origin="lower", cmap="YlGnBu",
        vmin=PHYS["TMIN"], vmax=PHYS["TMAX"], extent=(0, days[-1] + 1, -0.5, 23.5),
        interpolation="nearest",
    )
    ax.set_ylabel("时刻（小时）", fontsize=11)
    ax.set_yticks(range(0, 24, 3))
    ax.set_xlim(0, n // 24)
    # 逐小时画电价档位色条：谷电在 0—5 与 12—13 两段，不连续，不能按 min-max 画整块
    for h in range(24):
        label, color = HOUR_BUCKET[h]
        ax.add_patch(
            plt.Rectangle((-2.9, h - 0.5), 2.0, 1.0, color=color, alpha=0.7,
                          lw=0, clip_on=False)
        )
    # 时段说明放在图上方一行，避免与 y 轴标签、两小时宽带互撞
    legend_bits = [("电价时段", INK, 0.000), ("谷 0—5·12—13", OLIVE, 0.075),
                   ("平 6—11·14—15", BLUE, 0.215), ("峰 16—17·20—23", ORANGE, 0.360),
                   ("尖 18—19", "#C0392B", 0.515)]
    for text, color, xpos in legend_bits:
        ax.text(xpos, 1.045, text, transform=ax.transAxes, fontsize=9.5,
                color=color, ha="left", va="bottom", clip_on=False)
    cb = fig.colorbar(im, ax=ax, pad=0.012, fraction=0.03)
    cb.set_label("室内温度 (℃)", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    # 3 供热出力
    ax = fig.add_subplot(gs[2])
    ax.fill_between(np.arange(n), 0, q_hp, color=ORANGE, alpha=0.85, lw=0, label="热泵供热")
    ax.set_ylabel("供热出力\n(kW)", fontsize=11)
    set_day_axis(ax)
    ax.legend(loc="upper right", fontsize=9, frameon=False)
    ax.text(0.006, 0.78, "电阻供热全程为 0", transform=ax.transAxes, fontsize=9, color="#666666")

    # 4 取电与电网上限
    ax = fig.add_subplot(gs[3])
    ax.plot(np.arange(n), elec, color=TEAL, lw=0.7)
    ax.axhline(GRID_LIMIT_KW, color=ORANGE, lw=1.0, ls="--")
    ax.text(n * 0.006, GRID_LIMIT_KW + 0.13, "电网接入上限 4.0 kW", fontsize=9, color=ORANGE)
    ax.set_ylabel("取电功率\n(kW)", fontsize=11)
    ax.set_ylim(0, GRID_LIMIT_KW + 1.1)
    set_day_axis(ax, label=True)
    ax.set_xlabel("连续服务周期（2880 小时，北京时间 2024-11-16 00:00 起）", fontsize=11)

    fig.suptitle("武汉主案例：完整服务周期逐时运行", fontsize=15, y=0.962)
    fig.text(
        0.085, 0.920,
        f"折中方案（1 台热泵）　独立复算 RC 最大残差 {residual:.2e} ℃　"
        f"舒适违约 {comfort_viol} h　容量违约 {cap_viol} h　"
        f"顶到上限 {at_top} h　贴住下限 {at_bottom} h",
        fontsize=9.5,
    )
    fig.text(
        0.085, 0.048,
        "条件投资实验 · 理想设计设备与设计代理 COP · 不含储热与管网 · "
        "电价与碳因子为登记条件值 · 2880 h 为武昌热电公开项目服务周期",
        fontsize=8.5, color="#555555",
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300)
    plt.close(fig)
    print(f"saved {OUT}")
    print(f"RC residual={residual:.6e} comfort_viol={comfort_viol} cap_viol={cap_viol} "
          f"at_top={at_top} at_bottom={at_bottom} elec_max={elec.max():.4f} q_res_max={q_res.max()}")


if __name__ == "__main__":
    main()
