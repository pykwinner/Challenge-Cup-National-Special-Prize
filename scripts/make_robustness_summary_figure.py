"""稳健性汇总图：省费比例对舒适带上限与 H/C 参数的敏感性。

数据源：
  outputs/sensitivity_comfort_20261003/  （仅改 protocol.physics.max_c）
  outputs/sensitivity_hc_20261003/       （仅改 protocol.physics.H / C）
两组均由 scripts/run_investment_reference.py 的 pd3 派生副本生成，冻结脚本未改动。
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
CB = ROOT / "outputs/sensitivity_comfort_20261003"
HC = ROOT / "outputs/sensitivity_hc_20261003"
OUT = ROOT / "outputs/figures/robustness_summary.png"

BG = "#FBF7EE"
TEAL = "#1E6B6E"
ORANGE = "#EF8F3C"
BLUE = "#6E9BB0"
INK = "#1A1A1A"

plt.rcParams.update({
    "font.family": ["Noto Sans SC", "Microsoft YaHei"],
    "axes.unicode_minus": False,
    "figure.facecolor": BG, "axes.facecolor": BG,
    "text.color": INK, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.edgecolor": "#9A9A9A",
})


def pair(d: Path) -> tuple[float, float, float]:
    b = json.loads((d / "baseline" / "summary.json").read_text(encoding="utf-8"))
    c = json.loads((d / "compromise" / "summary.json").read_text(encoding="utf-8"))
    save = (b["window_energy_bill_cny"] - c["window_energy_bill_cny"]) / b["window_energy_bill_cny"] * 100
    carb = (b["window_co2_kg"] - c["window_co2_kg"]) / b["window_co2_kg"] * 100
    return save, carb, b["window_energy_bill_cny"]


def main() -> None:
    caps = [27, 26, 25, 24]
    cap_save = [pair(CB / f"run_max{c}" if c != 27 else CB / "run_max27_control")[0] for c in caps]
    base_save, base_carb, base_bill = pair(CB / "run_max27_control")

    labels = ["H −20%", "H +20%", "基准", "C −20%", "C +20%"]
    dirs = ["H_m20", "H_p20", None, "C_m20", "C_p20"]
    hc_save, hc_bill = [], []
    for name, lab in zip(dirs, labels):
        d = CB / "run_max27_control" if name is None else HC / f"run_{name}"
        s, _, bill = pair(d)
        hc_save.append(s)
        hc_bill.append(bill)

    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.2))
    fig.subplots_adjust(left=0.062, right=0.985, top=0.755, bottom=0.20, wspace=0.22)

    ax = axes[0]
    ax.plot(caps, cap_save, "o-", color=TEAL, lw=2, ms=8)
    ax.axhline(base_save, color="#999999", lw=0.9, ls=":")
    for x, y in zip(caps, cap_save):
        ax.annotate(f"{y:.2f}%", (x, y), textcoords="offset points", xytext=(0, 11),
                    ha="center", fontsize=10, color=TEAL)
    ax.set_xlabel("舒适带上限（℃）", fontsize=11)
    ax.set_ylabel("相对基准的省电费比例（%）", fontsize=11)
    ax.set_xticks(caps)
    ax.set_ylim(73.5, 74.8)

    ax = axes[1]
    xs = list(range(len(labels)))
    ax.plot(xs, hc_save, "o-", color=ORANGE, lw=2, ms=8)
    ax.axhline(base_save, color="#999999", lw=0.9, ls=":")
    for x, y, bill in zip(xs, hc_save, hc_bill):
        ax.annotate(f"{y:.2f}%", (x, y), textcoords="offset points", xytext=(0, 11),
                    ha="center", fontsize=10, color=ORANGE)
        ax.annotate(f"{bill:,.0f} 元", (x, y), textcoords="offset points", xytext=(0, -18),
                    ha="center", fontsize=8.5, color="#666666")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_xlabel("热损系数 H 与热容 C 各 ±20%（下方小字为基准方案电费）", fontsize=11)
    ax.set_ylim(73.5, 74.8)

    fig.suptitle("宽舒适带与移峰省费的稳健性：两类设定扰动下，省费比例仅变动 0.45 个百分点", fontsize=13.5, y=0.955)
    fig.text(0.062, 0.855,
             "同一 2880 h 窗口与资产，只改一个参数；五个工况下最优结构均为 1 台热泵、电阻容量 0、全程零违约",
             fontsize=10)
    fig.text(0.062, 0.05,
             "参数化条件投资实验 · 理想设计设备与设计代理 COP · 单栋原型 · 不含储热与管网 · "
             "max_c=27 档为对照组（复现原始 run_03，关键量最大绝对差 3.9e-09）",
             fontsize=8.5, color="#555555")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300)
    plt.close(fig)
    print(f"saved {OUT}")
    print("舒适带省费:", [f"{v:.2f}" for v in cap_save])
    print("H/C 省费:", [f"{v:.2f}" for v in hc_save], "对应基准电费", [f"{v:.0f}" for v in hc_bill])


if __name__ == "__main__":
    main()
