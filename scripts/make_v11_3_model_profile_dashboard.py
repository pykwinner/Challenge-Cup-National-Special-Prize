from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
ANNUAL = ROOT / "outputs" / "v11_3_full_year_preflight_estimate"
SHORT = ROOT / "outputs" / "v11_3_short_replay_profile"
OUTPUT = ROOT / "outputs" / "v11_3_figures"


def compact(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f} M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f} k"
    return f"{value:.0f}"


def metric_card(ax, x, title, value, note, color):
    card = FancyBboxPatch(
        (x, 0.715),
        0.205,
        0.155,
        boxstyle="round,pad=0.018,rounding_size=0.025",
        linewidth=1.3,
        edgecolor=color,
        facecolor="#FFFFFF",
    )
    ax.add_patch(card)
    ax.text(x + 0.022, 0.845, title, fontsize=10.2, color="#64748B", va="top")
    ax.text(
        x + 0.022,
        0.797,
        value,
        fontsize=19.5,
        fontweight="bold",
        color=color,
        va="top",
    )
    ax.text(x + 0.022, 0.74, note, fontsize=9.1, color="#64748B", va="top")


def main() -> None:
    annual = json.loads(
        (ANNUAL / "annual_replay_model_size_estimate.json").read_text(
            encoding="utf-8"
        )
    )
    short = json.loads(
        (SHORT / "model_execution_profile.json").read_text(encoding="utf-8")
    )
    projected = annual["projected_full_horizon"]
    planning = short["planning"]
    replay = short["fixed_investment_replay"]
    short_projection = short["pilot_estimate"]["projected_full_horizon"]

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
        "V11.3 全年模型规模先验与短周期运行画像",
        fontsize=21,
        fontweight="bold",
        color="#17324D",
        va="top",
    )
    canvas.text(
        0.055,
        0.915,
        "先用84/168小时试构模估计规模，再用168小时固定投资回放校验；不线性外推MILP求解时间",
        fontsize=10.8,
        color="#5B6573",
        va="top",
    )

    metric_card(
        canvas,
        0.055,
        "全年变量",
        compact(projected["variables"]),
        f"其中整数变量 {compact(projected['integer_variables'])}",
        "#2E74B5",
    )
    metric_card(
        canvas,
        0.285,
        "全年约束",
        compact(projected["constraints"]),
        f"非零系数 {compact(projected['matrix_nonzeros'])}",
        "#7A5AF8",
    )
    metric_card(
        canvas,
        0.515,
        "构模内存代理",
        f"{projected['conservative_python_build_memory_proxy_mb']:.0f} MB",
        f"稀疏矩阵与向量约 {projected['estimated_csr_and_vector_memory_mb']:.1f} MB",
        "#1E8E6E",
    )
    metric_card(
        canvas,
        0.745,
        "整数求解风险",
        "VERY HIGH",
        "风险分级，不等于求解时间预测",
        "#D97706",
    )

    size_ax = fig.add_axes([0.075, 0.19, 0.39, 0.42], facecolor="#FFFFFF")
    time_ax = fig.add_axes([0.55, 0.19, 0.38, 0.42], facecolor="#FFFFFF")
    for ax in (size_ax, time_ax):
        for spine in ax.spines.values():
            spine.set_color("#DCE5EC")

    labels = ["变量", "约束", "非零元"]
    values = np.array(
        [
            projected["variables"],
            projected["constraints"],
            projected["matrix_nonzeros"],
        ],
        dtype=float,
    )
    positions = np.arange(len(labels))
    size_ax.barh(positions, values / 1000.0, color=["#2E74B5", "#7A5AF8", "#1E8E6E"], height=0.5)
    size_ax.set_yticks(positions, labels)
    size_ax.invert_yaxis()
    size_ax.set_xlabel("规模（千）", color="#5B6573")
    size_ax.set_title(
        "8760小时结构投影",
        fontsize=13,
        fontweight="bold",
        color="#17324D",
        loc="left",
        pad=12,
    )
    size_ax.grid(axis="x", alpha=0.16)
    for position, value in zip(positions, values):
        size_ax.text(
            value / 1000.0 + max(values / 1000.0) * 0.018,
            position,
            compact(value),
            va="center",
            fontsize=10.2,
            color="#334155",
        )
    size_ax.set_xlim(0, max(values / 1000.0) * 1.16)

    phases = ["代表时段规划", "168h固定投资回放"]
    build = np.array([planning["build_seconds"], replay["build_seconds"]])
    solve = np.array([planning["solve_seconds"], replay["solve_seconds"]])
    positions = np.arange(len(phases))
    time_ax.barh(positions, build, color="#2E74B5", height=0.48, label="构模")
    time_ax.barh(
        positions,
        solve,
        left=build,
        color="#D97706",
        height=0.48,
        label="求解",
    )
    time_ax.set_yticks(positions, phases)
    time_ax.invert_yaxis()
    time_ax.set_xlabel("本机实测耗时（秒）", color="#5B6573")
    time_ax.set_title(
        "短周期实测：构模与求解分离",
        fontsize=13,
        fontweight="bold",
        color="#17324D",
        loc="left",
        pad=12,
    )
    time_ax.grid(axis="x", alpha=0.16)
    time_ax.legend(frameon=False, ncol=2, loc="lower right")
    for position, total in zip(positions, build + solve):
        time_ax.text(
            total + 0.018,
            position,
            f"{total:.3f}s",
            va="center",
            fontsize=10.2,
            color="#334155",
        )
    time_ax.set_xlim(0, max(build + solve) * 1.2)

    variable_error = 100.0 * abs(
        short_projection["variables"] - replay["variables"]
    ) / replay["variables"]
    constraint_error = 100.0 * abs(
        short_projection["constraints"] - replay["constraints"]
    ) / replay["constraints"]
    build_error = 100.0 * abs(
        short_projection["estimated_build_seconds"] - replay["build_seconds"]
    ) / replay["build_seconds"]
    canvas.text(
        0.055,
        0.105,
        (
            "168h校验误差：变量 "
            f"{variable_error:.2f}% ｜ 约束 {constraint_error:.2f}% ｜ "
            f"构模时间 {build_error:.1f}%"
        ),
        fontsize=10.3,
        color="#334155",
    )
    canvas.text(
        0.055,
        0.063,
        (
            "边界：内存为工程代理，不含预处理、分支定界树、割平面和操作系统开销；"
            "MILP求解时间必须由限定时长、 incumbent 与MIP gap实测报告。"
        ),
        fontsize=9.5,
        color="#64748B",
    )
    canvas.text(
        0.945,
        0.025,
        "north_heat_challenge · 11.3.0",
        ha="right",
        fontsize=9.2,
        color="#8291A3",
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / "model_size_runtime_dashboard.png", dpi=220)
    fig.savefig(OUTPUT / "model_size_runtime_dashboard.svg")
    plt.close(fig)


if __name__ == "__main__":
    main()
