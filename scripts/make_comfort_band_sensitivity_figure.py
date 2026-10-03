"""舒适带上限敏感性图（27/26/25/24 ℃）。

数据源：outputs/sensitivity_comfort_20261003/{run_max27_control,run_max26,run_max25,run_max24}
这四个目录由 scripts/run_investment_reference.py 的 pd3 派生副本生成，仅改 protocol.physics.max_c。
max_c=27 为对照组，用于校验派生管线能复现原始 run_03。
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/sensitivity_comfort_20261003"
OUT = ROOT / "outputs/figures/comfort_band_sensitivity.png"

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

RUNS = [(27, "run_max27_control"), (26, "run_max26"), (25, "run_max25"), (24, "run_max24")]


def load(run: str, case: str) -> dict:
    return json.loads((BASE / run / case / "summary.json").read_text(encoding="utf-8"))


def main() -> None:
    caps, save_pct, carb_pct, elec, costs = [], [], [], [], []
    for mc, d in RUNS:
        b, c = load(d, "baseline"), load(d, "compromise")
        caps.append(mc)
        save_pct.append((b["window_energy_bill_cny"] - c["window_energy_bill_cny"]) / b["window_energy_bill_cny"] * 100)
        carb_pct.append((b["window_co2_kg"] - c["window_co2_kg"]) / b["window_co2_kg"] * 100)
        elec.append(c["electricity_kwh"])
        costs.append(c["window_energy_bill_cny"])

    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.4))
    fig.subplots_adjust(left=0.075, right=0.975, top=0.76, bottom=0.235, wspace=0.24)

    ax = axes[0]
    ax.plot(caps, save_pct, "o-", color=TEAL, lw=2, ms=7, label="相对基准的省电费比例")
    ax.plot(caps, carb_pct, "s--", color=ORANGE, lw=2, ms=7, label="相对基准的碳排降幅")
    for x, y in zip(caps, save_pct):
        ax.annotate(f"{y:.2f}%", (x, y), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=9, color=TEAL)
    for x, y in zip(caps, carb_pct):
        ax.annotate(f"{y:.2f}%", (x, y), textcoords="offset points", xytext=(0, -16),
                    ha="center", fontsize=9, color=ORANGE)
    ax.set_xlabel("舒适带上限（℃）", fontsize=11)
    ax.set_ylabel("相对基准的降幅（%）", fontsize=11)
    ax.set_xticks(caps)
    ax.set_ylim(70, 79)
    ax.legend(fontsize=9.5, frameon=False, loc="upper left", bbox_to_anchor=(0.02, 0.97))

    ax = axes[1]
    ax.plot(caps, elec, "o-", color=BLUE, lw=2, ms=7)
    for x, y in zip(caps, elec):
        ax.annotate(f"{y:.1f}", (x, y), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=9, color=BLUE)
    ax.set_xlabel("舒适带上限（℃）", fontsize=11)
    ax.set_ylabel("折中方案窗口电量（kWh）", fontsize=11)
    ax.set_xticks(caps)
    ax.set_ylim(min(elec) - 8, max(elec) + 10)

    fig.suptitle("窄化舒适带，省费几乎不衰减", fontsize=15, y=0.965)
    fig.text(0.075, 0.885,
             "同一 2880 h 窗口、同一资产与电价，仅改舒适带上限（27/26/25/24 ℃）；"
             "电阻最优容量在四档中均为 2.2667 kW",
             fontsize=9.5)
    fig.text(0.075, 0.055,
             "参数化条件投资实验 · 理想设计设备与设计代理 COP · 单栋原型 · 不含储热与管网 · "
             "max_c=27 档为对照组（复现原始 run_03，关键量最大绝对差 3.9e-09）",
             fontsize=8.5, color="#555555")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=300)
    plt.close(fig)
    print(f"saved {OUT}")
    for mc, s, c, e in zip(caps, save_pct, carb_pct, elec):
        print(f"max_c={mc}  省费 {s:.2f}%  碳降 {c:.2f}%  电量 {e:.1f} kWh")


if __name__ == "__main__":
    main()
