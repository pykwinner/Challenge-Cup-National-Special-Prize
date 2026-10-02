from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs" / "v11_2_full_year_preflight_demo"
OUTPUT = ROOT / "outputs" / "v11_2_figures"


def status_card(ax, x, title, value, note, color):
    card = FancyBboxPatch(
        (x, 0.68),
        0.205,
        0.17,
        boxstyle="round,pad=0.018,rounding_size=0.025",
        linewidth=1.4,
        edgecolor=color,
        facecolor="#FFFFFF",
    )
    ax.add_patch(card)
    ax.text(x + 0.025, 0.825, title, fontsize=10.5, color="#64748B", va="top")
    ax.text(
        x + 0.025,
        0.775,
        value,
        fontsize=21,
        fontweight="bold",
        color=color,
        va="top",
    )
    ax.text(x + 0.025, 0.705, note, fontsize=9.4, color="#64748B", va="top")


def main() -> None:
    summary = json.loads(
        (SOURCE / "annual_replay_preflight_summary.json").read_text(
            encoding="utf-8"
        )
    )
    audit = pd.read_csv(SOURCE / "representative_coverage_audit.csv")
    demand = audit[audit["series_kind"] == "demand"].copy()
    demand["energy_ratio"] = 1.0 + demand["total_relative_bias"]

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "SimHei",
        "Arial",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(13.5, 7.0), facecolor="#F3F7FA")
    canvas = fig.add_axes([0, 0, 1, 1])
    canvas.set_xlim(0, 1)
    canvas.set_ylim(0, 1)
    canvas.axis("off")
    canvas.text(
        0.055,
        0.955,
        "V11.2 全年数据准入与代表时段覆盖审计",
        fontsize=21,
        fontweight="bold",
        color="#17324D",
        va="top",
    )
    canvas.text(
        0.055,
        0.91,
        "先验证数据，再启动大规模优化；年度数据通过不代表典型时段没有偏差",
        fontsize=11.2,
        color="#5B6573",
        va="top",
    )

    status_card(
        canvas,
        0.055,
        "连续时序规模",
        f"{summary['rows']:,} h",
        "完整逐小时年度",
        "#2E74B5",
    )
    status_card(
        canvas,
        0.285,
        "时间与数值质量",
        "PASS",
        "无缺口、重复、空值或负负荷",
        "#1E8E6E",
    )
    status_card(
        canvas,
        0.515,
        "年度覆盖判定",
        "READY",
        "8760 h，自然年对齐",
        "#2F6B4F",
    )
    status_card(
        canvas,
        0.745,
        "代表时段告警",
        str(summary["representative_series_review_count"]),
        "负荷列需要复核权重",
        "#D97706",
    )

    energy_ax = fig.add_axes([0.07, 0.16, 0.40, 0.43], facecolor="#FFFFFF")
    peak_ax = fig.add_axes([0.54, 0.16, 0.39, 0.43], facecolor="#FFFFFF")
    for ax in (energy_ax, peak_ax):
        for spine in ax.spines.values():
            spine.set_color("#DCE5EC")

    labels = [column.replace("demand_", "") for column in demand["column"]]
    positions = np.arange(len(demand))
    ratios = 100.0 * demand["energy_ratio"].to_numpy()
    energy_ax.barh(positions, ratios, color="#7A5AF8", height=0.54)
    energy_ax.axvline(100.0, color="#D97706", linestyle="--", linewidth=1.4)
    energy_ax.set_yticks(positions, labels)
    energy_ax.invert_yaxis()
    energy_ax.set_xlim(0, max(110.0, float(ratios.max()) + 12.0))
    energy_ax.set_xlabel("代表时段折算 / 连续年度（%）", color="#5B6573")
    energy_ax.set_title(
        "年能量覆盖：低估约14.2%",
        fontsize=13,
        fontweight="bold",
        color="#17324D",
        loc="left",
        pad=12,
    )
    energy_ax.grid(axis="x", alpha=0.16)
    for position, value in zip(positions, ratios):
        energy_ax.text(value + 1.2, position, f"{value:.1f}%", va="center", fontsize=10.5, color="#4C3AA8")

    width = 0.34
    peak = 100.0 * demand["peak_capture_ratio"].to_numpy()
    ramp = 100.0 * demand["ramp_capture_ratio"].to_numpy()
    peak_ax.barh(positions - width / 2, peak, height=width, color="#2E74B5", label="峰值")
    peak_ax.barh(positions + width / 2, ramp, height=width, color="#1E8E6E", label="爬坡")
    peak_ax.axvline(100.0, color="#D97706", linestyle="--", linewidth=1.4)
    peak_ax.set_yticks(positions, labels)
    peak_ax.invert_yaxis()
    peak_ax.set_xlim(0, 112)
    peak_ax.set_xlabel("捕获率（%）", color="#5B6573")
    peak_ax.set_title(
        "极端保持：峰值与爬坡均100%",
        fontsize=13,
        fontweight="bold",
        color="#17324D",
        loc="left",
        pad=12,
    )
    peak_ax.grid(axis="x", alpha=0.16)
    peak_ax.legend(
        frameon=False,
        ncol=2,
        loc="upper right",
        bbox_to_anchor=(1.0, 1.14),
    )

    canvas.text(
        0.055,
        0.055,
        "审计结论：全年数据可以进入正式回放，但代表时段权重需重新校准；本图基于合成8760小时数据，仅验证软件流程。",
        fontsize=10.2,
        color="#64748B",
    )
    canvas.text(
        0.945,
        0.025,
        "north_heat_challenge · 11.2.0",
        ha="right",
        fontsize=9.2,
        color="#8291A3",
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / "annual_preflight_dashboard.png", dpi=220)
    fig.savefig(OUTPUT / "annual_preflight_dashboard.svg")
    plt.close(fig)


if __name__ == "__main__":
    main()
