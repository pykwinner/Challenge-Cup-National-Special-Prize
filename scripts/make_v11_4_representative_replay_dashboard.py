from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
QUALITY = ROOT / "outputs" / "v11_4_representative_days"
REPLAY = ROOT / "outputs" / "v11_4_wuhan_annual_replay"
OUTPUT = ROOT / "outputs" / "v11_4_figures"


def card(ax, x, title, value, note, color):
    patch = FancyBboxPatch(
        (x, 0.72),
        0.205,
        0.15,
        boxstyle="round,pad=0.018,rounding_size=0.024",
        linewidth=1.3,
        edgecolor=color,
        facecolor="#FFFFFF",
    )
    ax.add_patch(patch)
    ax.text(x + 0.022, 0.845, title, fontsize=10.1, color="#64748B", va="top")
    ax.text(
        x + 0.022,
        0.797,
        value,
        fontsize=19.0,
        fontweight="bold",
        color=color,
        va="top",
    )
    ax.text(x + 0.022, 0.744, note, fontsize=9.1, color="#64748B", va="top")


def main() -> None:
    quality = pd.read_csv(QUALITY / "representative_quality_summary.csv")
    original_failure = json.loads(
        (REPLAY / "original" / "annual_replay_failure_summary.json").read_text(
            encoding="utf-8"
        )
    )
    candidate = json.loads(
        (REPLAY / "candidate" / "annual_replay_summary.json").read_text(
            encoding="utf-8"
        )
    )
    wuhan = quality[quality["profile"] == "scenario3_wuhan"].iloc[0]

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "SimHei",
        "Arial",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(13.5, 7.3), facecolor="#F3F7FA")
    canvas = fig.add_axes([0, 0, 1, 1])
    canvas.set_xlim(0, 1)
    canvas.set_ylim(0, 1)
    canvas.axis("off")
    canvas.text(
        0.055,
        0.958,
        "V11.4 代表日修复与武汉8760小时回放闭环",
        fontsize=21,
        fontweight="bold",
        color="#17324D",
        va="top",
    )
    canvas.text(
        0.055,
        0.915,
        "逐负荷峰值/爬坡 + 最坏COP锚点 + 受约束权重校准；原始文件保持不变",
        fontsize=10.8,
        color="#5B6573",
        va="top",
    )
    card(canvas, 0.055, "代表天数守恒", "365 → 365", "非负权重，极端日至少1天", "#2E74B5")
    card(canvas, 0.285, "武汉覆盖告警", "2 → 0", "单列爬坡捕获率提升至100%", "#1E8E6E")
    card(canvas, 0.515, "配电可靠容量缺口", "−0.917 → 0 kW", "最坏低温高湿除霜COP已锚定", "#7A5AF8")
    card(canvas, 0.745, "正式全年回放", "OPTIMAL", "8760 h，热/冷失供率均为0", "#1E8E6E")

    day_ax = fig.add_axes([0.075, 0.20, 0.38, 0.40], facecolor="#FFFFFF")
    closure_ax = fig.add_axes([0.55, 0.20, 0.38, 0.40], facecolor="#FFFFFF")
    for ax in (day_ax, closure_ax):
        for spine in ax.spines.values():
            spine.set_color("#DCE5EC")

    labels = ["乌鲁木齐", "固原", "武汉"]
    original_days = quality["original_representative_days"].to_numpy(dtype=float)
    candidate_days = quality["candidate_representative_days"].to_numpy(dtype=float)
    positions = np.arange(3)
    width = 0.32
    day_ax.barh(positions - width / 2, original_days, height=width, color="#94A3B8", label="原方案")
    day_ax.barh(positions + width / 2, candidate_days, height=width, color="#2E74B5", label="V11.4")
    day_ax.set_yticks(positions, labels)
    day_ax.invert_yaxis()
    day_ax.set_xlim(0, 16)
    day_ax.set_xlabel("代表日数量", color="#5B6573")
    day_ax.set_title("按风险增减代表日，而非固定追求更多", loc="left", fontsize=12.6, fontweight="bold", color="#17324D", pad=12)
    day_ax.grid(axis="x", alpha=0.16)
    day_ax.legend(
        frameon=False,
        ncol=2,
        loc="upper right",
        bbox_to_anchor=(1.0, 1.13),
    )
    for p, before, after in zip(positions, original_days, candidate_days):
        day_ax.text(before + 0.2, p - width / 2, f"{before:.0f}", va="center", fontsize=9.5)
        day_ax.text(after + 0.2, p + width / 2, f"{after:.0f}", va="center", fontsize=9.5)

    metrics = ["最小爬坡捕获率", "年能量准确率"]
    before_values = [
        100.0 * float(wuhan["minimum_demand_ramp_capture_before"]),
        100.0 * (1.0 - float(wuhan["maximum_absolute_energy_bias_before"])),
    ]
    after_values = [
        100.0 * float(wuhan["minimum_demand_ramp_capture_after"]),
        100.0 * (1.0 - float(wuhan["maximum_absolute_energy_bias_after"])),
    ]
    positions = np.arange(2)
    closure_ax.barh(positions - width / 2, before_values, height=width, color="#D97706", label="原方案")
    closure_ax.barh(positions + width / 2, after_values, height=width, color="#1E8E6E", label="V11.4")
    closure_ax.set_yticks(positions, metrics)
    closure_ax.invert_yaxis()
    closure_ax.set_xlim(80, 102)
    closure_ax.axvline(100.0, color="#64748B", linewidth=1.0, linestyle="--")
    closure_ax.set_xlabel("覆盖/准确率（%）", color="#5B6573")
    closure_ax.set_title("武汉代表时段质量修复", loc="left", fontsize=12.6, fontweight="bold", color="#17324D", pad=12)
    closure_ax.grid(axis="x", alpha=0.16)
    closure_ax.legend(
        frameon=False,
        ncol=2,
        loc="upper right",
        bbox_to_anchor=(1.0, 1.13),
    )
    for p, before, after in zip(positions, before_values, after_values):
        closure_ax.text(before - 0.25, p - width / 2, f"{before:.1f}%", ha="right", va="center", fontsize=9.5)
        closure_ax.text(after - 0.25, p + width / 2, f"{after:.1f}%", ha="right", va="center", fontsize=9.5)

    canvas.text(
        0.055,
        0.105,
        (
            f"旧方案：规划状态0、代表期零失供，但全年构模前配电缺口 "
            f"{original_failure['firm_electric_capacity_gap_kw']:.3f} kW；V11.4：规划与回放状态均0。"
        ),
        fontsize=10.2,
        color="#334155",
    )
    canvas.text(
        0.055,
        0.064,
        (
            f"全年实测：{candidate['replay_variables']:,}变量 / {candidate['replay_integer_variables']:,}整数变量，"
            f"构模{candidate['replay_build_seconds']:.2f}s，求解{candidate['replay_solve_seconds']:.2f}s，储热首末SOC漂移0。"
        ),
        fontsize=9.7,
        color="#64748B",
    )
    canvas.text(0.945, 0.025, "north_heat_challenge · 11.4.0", ha="right", fontsize=9.2, color="#8291A3")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / "representative_replay_closure_dashboard.png", dpi=220)
    fig.savefig(OUTPUT / "representative_replay_closure_dashboard.svg")
    plt.close(fig)


if __name__ == "__main__":
    main()
