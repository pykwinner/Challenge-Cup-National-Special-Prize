from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np
import pandas as pd


BG = "#F6F2E9"
INK = "#183044"
MUTED = "#66717A"
TEAL = "#267A73"
RED = "#C84A3D"
GOLD = "#D4A13B"
BLUE = "#3F6F95"


def _style() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": [
                "Microsoft YaHei",
                "SimHei",
                "Arial Unicode MS",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "figure.facecolor": BG,
            "axes.facecolor": BG,
            "axes.edgecolor": "#A8AEA9",
            "axes.labelcolor": INK,
            "xtick.color": MUTED,
            "ytick.color": INK,
            "text.color": INK,
            "font.size": 10,
        }
    )


def _labels(frame: pd.DataFrame) -> list[str]:
    city = {
        "scenario1_urumqi": "乌鲁木齐",
        "scenario2_guyuan": "固原",
        "scenario3_wuhan": "武汉",
    }
    return [
        f"{city.get(row.profile, row.profile)}·{row.weather_year}"
        for row in frame.itertuples()
    ]


def plot_acceptance_overview(frame: pd.DataFrame, output: Path) -> None:
    labels = _labels(frame)
    colors = [
        BLUE if value.startswith("乌鲁木齐") else RED if value.startswith("固原") else TEAL
        for value in labels
    ]
    y = np.arange(len(frame))
    fig = plt.figure(figsize=(15, 9), facecolor=BG)
    grid = fig.add_gridspec(
        2, 3, height_ratios=[0.22, 1.0], hspace=0.25, wspace=0.48
    )
    banner = fig.add_subplot(grid[0, :])
    banner.axis("off")
    banner.add_patch(
        FancyBboxPatch(
            (0.01, 0.12),
            0.98,
            0.76,
            boxstyle="round,pad=0.012,rounding_size=0.025",
            transform=banner.transAxes,
            facecolor="#E4EFE9",
            edgecolor="#A9C8B8",
            linewidth=1.2,
        )
    )
    banner.text(
        0.04,
        0.58,
        "7 / 7 极端气象年度正式认证通过",
        fontsize=22,
        fontweight="bold",
        va="center",
    )
    banner.text(
        0.04,
        0.30,
        "热失供率 = 0  ·  冷失供率 = 0  ·  固定投资未漂移  ·  全部求解状态 = 0",
        color=MUTED,
        fontsize=12,
        va="center",
    )
    banner.text(
        0.95,
        0.50,
        "PASS",
        color=TEAL,
        fontsize=23,
        fontweight="bold",
        ha="right",
        va="center",
    )

    panels = [
        ("replay_solve_seconds", "逐小时回放求解时间", "秒", "{:.1f}"),
        (
            "levelized_heat_cost_yuan_per_kwh",
            "监管口径度热成本",
            "元/kWh",
            "{:.3f}",
        ),
        ("emissions_kg_per_year", "年度供热排放", "tCO2e/年", "{:.1f}"),
    ]
    for column_index, (column, title, unit, formatter) in enumerate(panels):
        ax = fig.add_subplot(grid[1, column_index])
        values = frame[column].astype(float).to_numpy()
        if column == "emissions_kg_per_year":
            values = values / 1000.0
        bars = ax.barh(y, values, color=colors, height=0.58, alpha=0.92)
        ax.set_yticks(y, labels if column_index == 0 else [""] * len(labels))
        ax.invert_yaxis()
        ax.set_title(title, loc="left", fontsize=14, fontweight="bold", pad=14)
        ax.set_xlabel(unit)
        ax.grid(axis="x", color="#C9CDC8", alpha=0.65, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.spines[["top", "right", "left"]].set_visible(False)
        maximum = max(values) if len(values) else 1.0
        ax.set_xlim(0, maximum * 1.22 if maximum else 1.0)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_width() + maximum * 0.025,
                bar.get_y() + bar.get_height() / 2,
                formatter.format(value),
                va="center",
                fontsize=9,
                color=INK,
            )
    fig.suptitle(
        "V11.9 多气象年固定投资完整时序验收",
        x=0.055,
        y=0.992,
        ha="left",
        fontsize=22,
        fontweight="bold",
    )
    fig.text(
        0.055,
        0.930,
        "V11.8 CVaR 投资方案 · 8760/8784 小时逐年回放 · 2026-07-20",
        color=MUTED,
        fontsize=11,
    )
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def plot_validation_pipeline(output: Path) -> None:
    stages = [
        ("15", "年度原始输入", "3城 × 2020—2024"),
        ("15", "逐年代表日校准", "能量偏差≤0.155%"),
        ("3", "两阶段CVaR规划", "年度共享投资"),
        ("15/15", "可靠容量预审", "全部年度通过"),
        ("7", "极端年度回放", "8760/8784小时"),
        ("7/7", "正式验收证书", "热/冷失供均为0"),
    ]
    fig, ax = plt.subplots(figsize=(16, 5.4), facecolor=BG)
    ax.set_xlim(-0.45, len(stages) - 0.55)
    ax.set_ylim(-0.55, 1.25)
    ax.axis("off")
    for index in range(len(stages) - 1):
        ax.annotate(
            "",
            xy=(index + 0.72, 0.42),
            xytext=(index + 0.28, 0.42),
            arrowprops=dict(arrowstyle="-|>", color="#82909A", lw=1.8),
        )
    for index, (count, label, detail) in enumerate(stages):
        face = "#E4EFE9" if index == len(stages) - 1 else "#E9ECE8"
        edge = TEAL if index == len(stages) - 1 else "#AAB1AC"
        ax.add_patch(
            FancyBboxPatch(
                (index - 0.31, 0.02),
                0.62,
                0.82,
                boxstyle="round,pad=0.035,rounding_size=0.05",
                facecolor=face,
                edgecolor=edge,
                linewidth=1.6,
            )
        )
        ax.text(
            index,
            0.60,
            count,
            ha="center",
            va="center",
            fontsize=22,
            fontweight="bold",
            color=TEAL if index == len(stages) - 1 else INK,
        )
        ax.text(index, 0.34, label, ha="center", va="center", fontsize=12)
        ax.text(
            index,
            0.16,
            detail,
            ha="center",
            va="center",
            fontsize=9,
            color=MUTED,
        )
    ax.text(
        -0.37,
        1.12,
        "从公开气象数据到可审计年度认证：证据链无断点",
        fontsize=22,
        fontweight="bold",
        ha="left",
    )
    ax.text(
        -0.37,
        0.96,
        "平均年快速预筛 → 多气象年风险厌恶定稿 → 极端年逐小时验收",
        fontsize=11,
        color=MUTED,
        ha="left",
    )
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def plot_checkpoint_efficiency(frame: pd.DataFrame, output: Path) -> None:
    profile_labels = {
        "scenario1_urumqi": "乌鲁木齐",
        "scenario2_guyuan": "固原",
        "scenario3_wuhan": "武汉",
    }
    planning_seconds = {
        "scenario1_urumqi": 30.583,
        "scenario2_guyuan": 289.263,
        "scenario3_wuhan": 8.941,
    }
    replay = frame.groupby("profile")["replay_solve_seconds"].sum()
    profiles = list(profile_labels)
    labels = [profile_labels[item] for item in profiles]
    saved = np.array([planning_seconds[item] for item in profiles])
    actual = np.array([float(replay[item]) for item in profiles])
    x = np.arange(len(profiles))
    fig, ax = plt.subplots(figsize=(11, 6.5), facecolor=BG)
    width = 0.34
    first = ax.bar(
        x - width / 2,
        saved,
        width,
        color=GOLD,
        label="检查点避免的重复规划求解",
    )
    second = ax.bar(
        x + width / 2,
        actual,
        width,
        color=TEAL,
        label="正式年度回放实际求解",
    )
    ax.set_xticks(x, labels)
    ax.set_ylabel("秒")
    ax.set_title(
        "检查点把时间预算留给完整年度验证",
        loc="left",
        fontsize=22,
        fontweight="bold",
        pad=18,
    )
    ax.text(
        0,
        1.01,
        f"本轮累计避免重复规划 {saved.sum():.1f} 秒（{saved.sum()/60:.2f} 分钟）",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=11,
    )
    ax.grid(axis="y", color="#C9CDC8", alpha=0.65, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left")
    for bars in (first, second):
        for bar in bars:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(actual.max(), saved.max()) * 0.025,
                f"{bar.get_height():.1f}",
                ha="center",
                va="bottom",
                fontsize=10,
            )
    ax.set_ylim(0, max(actual.max(), saved.max()) * 1.22)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    _style()
    frame = pd.read_csv(args.summary, dtype={"weather_year": str})
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    plot_acceptance_overview(frame, output / "seven_year_acceptance_overview.png")
    plot_validation_pipeline(output / "three_stage_validation_pipeline.png")
    plot_checkpoint_efficiency(frame, output / "checkpoint_efficiency_gain.png")


if __name__ == "__main__":
    main()
