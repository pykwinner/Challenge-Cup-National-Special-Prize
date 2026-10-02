from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "v11_1_figures"


def box(ax, x, y, width, height, title, lines, color):
    patch = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.018,rounding_size=0.025",
        linewidth=1.6,
        edgecolor=color,
        facecolor="#F8FBFD",
    )
    ax.add_patch(patch)
    ax.text(
        x + 0.03,
        y + height - 0.075,
        title,
        fontsize=13.2,
        fontweight="bold",
        color=color,
        va="top",
    )
    ax.text(
        x + 0.03,
        y + height - 0.145,
        "\n".join(lines),
        fontsize=10.4,
        color="#334155",
        va="top",
        linespacing=1.55,
    )


def main() -> None:
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "SimHei",
        "Arial",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(13.5, 6.3))
    fig.patch.set_facecolor("#F3F7FA")
    ax.set_facecolor("#F3F7FA")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.05,
        0.925,
        "V11.1 需求侧计量与固定投资连续回放闭环",
        fontsize=21,
        fontweight="bold",
        color="#17324D",
    )
    ax.text(
        0.05,
        0.875,
        "代表时段负责选方案，连续时序只验证运行；任何回放阶段新增容量都会被审计拒绝",
        fontsize=11.5,
        color="#5B6573",
    )

    specs = [
        (
            0.05,
            "① 代表时段联合规划",
            ["源—网—荷—储共享投资", "多情景运行与政策约束", "极端日 + 周期权重"],
            "#2E74B5",
        ),
        (
            0.285,
            "② 投资冻结转换",
            ["新增/退役转为既有资产", "候选上限统一清零", "计量覆盖率同步冻结"],
            "#7A5AF8",
        ),
        (
            0.52,
            "③ 连续时序运行回放",
            ["8760 / 8784 h 保序运行", "可靠性约束保持不放宽", "跨季节 SOC 连续"],
            "#1E8E6E",
        ),
        (
            0.755,
            "④ 审计与反馈",
            ["零投资余量审计", "失供 / SOC / KPI 偏差", "失败则返回规划层修正"],
            "#D97706",
        ),
    ]
    for x, title, lines, color in specs:
        box(ax, x, 0.43, 0.19, 0.34, title, lines, color)
    for x1, x2 in ((0.24, 0.285), (0.475, 0.52), (0.71, 0.755)):
        ax.add_patch(
            FancyArrowPatch(
                (x1, 0.60),
                (x2, 0.60),
                arrowstyle="-|>",
                mutation_scale=16,
                linewidth=1.8,
                color="#64748B",
            )
        )

    box(
        ax,
        0.05,
        0.10,
        0.425,
        0.20,
        "需求侧新增决策",
        ["计量覆盖率投资  →  线性节热响应", "热价 + 固定费 + 户数  →  户均热费约束"],
        "#C44E52",
    )
    box(
        ax,
        0.52,
        0.10,
        0.425,
        0.20,
        "正式结论的两道机器判据",
        ["all_investments_frozen = true", "is_full_year_horizon = true"],
        "#2F6B4F",
    )
    ax.text(
        0.95,
        0.035,
        "north_heat_challenge · 11.1.0",
        ha="right",
        fontsize=9.5,
        color="#8291A3",
    )
    fig.tight_layout(pad=0.8)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / "metering_annual_replay_workflow.png", dpi=220)
    fig.savefig(OUTPUT / "metering_annual_replay_workflow.svg")
    plt.close(fig)


if __name__ == "__main__":
    main()
