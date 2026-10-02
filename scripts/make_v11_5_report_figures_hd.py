from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "outputs" / "v11_5_three_scenario_annual_replay"
OUTPUT = ROOT / "outputs" / "v11_5_figures_hd"

BG = "#F4F7FA"
INK = "#102A43"
MUTED = "#62748A"
GRID = "#DCE5ED"
WHITE = "#FFFFFF"
BLUE = "#2563EB"
TEAL = "#0F9D7A"
PURPLE = "#7C3AED"
AMBER = "#E69A22"
RED = "#D64545"
SCENARIO_COLORS = [BLUE, TEAL, PURPLE]

PROFILES = [
    ("scenario1_urumqi", "S1 大型集中供热", "乌鲁木齐"),
    ("scenario2_guyuan", "S2 分散式清洁转型", "固原"),
    ("scenario3_wuhan", "S3 夏热冬冷新建", "武汉"),
]

GATE_LABELS = {
    "continuous_full_calendar_year": "连续完整\n公历年",
    "representative_period_coverage": "代表时段\n覆盖",
    "representative_planning_optimal": "代表期\n规划最优",
    "annual_replay_optimal": "全年回放\n最优",
    "all_investments_frozen": "投资全部\n冻结",
    "prebuild_firm_capacity_readiness": "可靠容量\n就绪",
    "annual_heat_reliability": "全年供热\n可靠性",
    "worst_scenario_heat_reliability": "最差场景\n可靠性",
    "annual_cooling_reliability": "全年供冷\n可靠性",
    "storage_boundary_conditions": "储热边界\n闭合",
    "ground_thermal_annual_balance": "地源年度\n热平衡",
    "minimum_clean_heat_share": "清洁热\n约束",
}


def setup_style() -> None:
    mpl.rcParams.update(
        {
            "font.sans-serif": [
                "Microsoft YaHei",
                "Noto Sans CJK SC",
                "SimHei",
                "Arial",
            ],
            "axes.unicode_minus": False,
            "font.size": 12,
            "axes.titleweight": "bold",
            "axes.edgecolor": GRID,
            "axes.labelcolor": MUTED,
            "xtick.color": MUTED,
            "ytick.color": INK,
            "figure.facecolor": BG,
            "savefig.facecolor": BG,
        }
    )


def load_data() -> tuple[pd.DataFrame, dict[str, dict]]:
    frame = pd.read_csv(REPLAY / "annual_replay_batch_summary.csv")
    certificates = {
        profile: json.loads(
            (REPLAY / profile / "annual_replay_acceptance_certificate.json").read_text(
                encoding="utf-8"
            )
        )
        for profile, _, _ in PROFILES
    }
    return frame, certificates


def save_figure(fig: plt.Figure, stem: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / f"{stem}.png", dpi=240, facecolor=BG)
    fig.savefig(OUTPUT / f"{stem}.svg", facecolor=BG)
    plt.close(fig)


def rounded_box(
    ax: plt.Axes,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    face: str = WHITE,
    edge: str = GRID,
    linewidth: float = 1.2,
    radius: float = 0.018,
) -> FancyBboxPatch:
    patch = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle=f"round,pad=0.012,rounding_size={radius}",
        facecolor=face,
        edgecolor=edge,
        linewidth=linewidth,
    )
    ax.add_patch(patch)
    return patch


def add_source_note(ax: plt.Axes, text: str) -> None:
    ax.text(0.05, 0.025, text, color=MUTED, fontsize=10.5, va="bottom")
    ax.text(
        0.95,
        0.025,
        "north_heat_challenge · V11.5",
        color="#8292A6",
        fontsize=10.5,
        ha="right",
        va="bottom",
    )


def evidence_chain(frame: pd.DataFrame) -> None:
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.05,
        0.94,
        "从代表时段规划到完整逐时验证",
        fontsize=30,
        fontweight="bold",
        color=INK,
        va="top",
    )
    ax.text(
        0.05,
        0.885,
        "三类供热转型场景的可审计证据链",
        fontsize=16,
        color=MUTED,
        va="top",
    )

    nodes = [
        ("01", "216 h", "代表时段规划", "9 个代表日 × 24 h", BLUE),
        ("02", "全部冻结", "固定投资方案", "容量、退役、管径与储能", TEAL),
        ("03", "8,760 h", "连续逐时回放", "逐时热平衡与状态递推", PURPLE),
        ("04", "3 / 3", "机器验收通过", "32 / 32 项适用门通过", TEAL),
    ]
    xs = [0.05, 0.285, 0.52, 0.755]
    for idx, ((number, value, title, note, color), x) in enumerate(zip(nodes, xs)):
        rounded_box(ax, x, 0.64, 0.19, 0.17, edge=color, linewidth=1.6)
        ax.text(x + 0.018, 0.782, number, color=color, fontsize=11.5, fontweight="bold")
        ax.text(x + 0.018, 0.738, value, color=color, fontsize=25, fontweight="bold")
        ax.text(x + 0.018, 0.690, title, color=INK, fontsize=15, fontweight="bold")
        ax.text(x + 0.018, 0.660, note, color=MUTED, fontsize=10.8)
        if idx < len(nodes) - 1:
            ax.add_patch(
                FancyArrowPatch(
                    (x + 0.197, 0.725),
                    (xs[idx + 1] - 0.012, 0.725),
                    arrowstyle="-|>",
                    mutation_scale=16,
                    linewidth=1.5,
                    color="#A5B4C4",
                )
            )

    ax.text(
        0.05,
        0.565,
        "三场景固定投资全年回放结果",
        fontsize=19,
        fontweight="bold",
        color=INK,
    )

    max_variables = float(frame["variables"].max())
    row_y = [0.445, 0.325, 0.205]
    for i, ((_, scenario, city), (_, row), y) in enumerate(
        zip(PROFILES, frame.iterrows(), row_y)
    ):
        color = SCENARIO_COLORS[i]
        rounded_box(ax, 0.05, y - 0.045, 0.90, 0.09, edge="#E3EAF0", linewidth=0.9)
        ax.add_patch(
            FancyBboxPatch(
                (0.05, y - 0.045),
                0.009,
                0.09,
                boxstyle="round,pad=0,rounding_size=0.004",
                facecolor=color,
                edgecolor=color,
            )
        )
        ax.text(0.075, y + 0.012, scenario, color=INK, fontsize=14.5, fontweight="bold")
        ax.text(0.075, y - 0.020, city, color=MUTED, fontsize=11.5)

        track_x, track_w = 0.285, 0.29
        ax.add_patch(
            FancyBboxPatch(
                (track_x, y - 0.009),
                track_w,
                0.018,
                boxstyle="round,pad=0,rounding_size=0.009",
                facecolor="#E9EFF5",
                edgecolor="none",
            )
        )
        width = track_w * float(row["variables"]) / max_variables
        ax.add_patch(
            FancyBboxPatch(
                (track_x, y - 0.009),
                width,
                0.018,
                boxstyle="round,pad=0,rounding_size=0.009",
                facecolor=color,
                edgecolor="none",
            )
        )
        ax.text(
            0.60,
            y,
            f"变量 {int(row['variables']):,}",
            color=INK,
            fontsize=12.5,
            va="center",
        )
        ax.text(
            0.725,
            y,
            f"求解 {float(row['solve_seconds']):.1f} s",
            color=INK,
            fontsize=12.5,
            va="center",
        )
        ax.text(
            0.84,
            y,
            f"{int(row['passed_gates'])}/{int(row['gate_count'])} 门",
            color=TEAL,
            fontsize=12.5,
            fontweight="bold",
            va="center",
        )
        ax.text(0.925, y, "通过", color=TEAL, fontsize=12.5, fontweight="bold", ha="right", va="center")

    ax.text(
        0.05,
        0.095,
        "结论：216 h 用于规划压缩；8760 h 用于连续状态、逐时平衡与可靠性复核。三场景热失供率均为 0。",
        color=INK,
        fontsize=13.2,
        fontweight="bold",
    )
    add_source_note(
        ax,
        "数据源：annual_replay_batch_summary.csv 与三场景验收证书；固定投资年度回放。",
    )
    save_figure(fig, "fig01_dynamic_evidence_chain")


def scale_and_efficiency(frame: pd.DataFrame) -> None:
    fig = plt.figure(figsize=(16, 9))
    canvas = fig.add_axes([0, 0, 1, 1])
    canvas.axis("off")
    canvas.text(
        0.05,
        0.94,
        "三场景全年模型规模、效率与清洁性",
        fontsize=29,
        fontweight="bold",
        color=INK,
        va="top",
    )
    canvas.text(
        0.05,
        0.885,
        "连续 8760 小时固定投资回放｜同机实测",
        fontsize=15.5,
        color=MUTED,
        va="top",
    )

    labels = ["S1 乌鲁木齐", "S2 固原", "S3 武汉"]
    y = np.arange(len(frame))

    size_ax = fig.add_axes([0.075, 0.47, 0.48, 0.32], facecolor=WHITE)
    variables = frame["variables"].to_numpy(dtype=float) / 10000.0
    constraints = frame["constraints"].to_numpy(dtype=float) / 10000.0
    height = 0.28
    size_ax.barh(y - height / 2, variables, height=height, color=BLUE, label="变量")
    size_ax.barh(y + height / 2, constraints, height=height, color="#8DB3F4", label="约束")
    size_ax.set_yticks(y, labels)
    size_ax.invert_yaxis()
    size_ax.set_xlim(0, max(constraints) * 1.24)
    size_ax.set_xlabel("规模（万）")
    size_ax.set_title("模型变量与约束规模", loc="left", fontsize=17, color=INK, pad=14)
    size_ax.grid(axis="x", color=GRID, alpha=0.65, linewidth=0.8)
    size_ax.set_axisbelow(True)
    size_ax.legend(frameon=False, loc="lower right", ncol=2)
    for pos, value in zip(y - height / 2, variables):
        size_ax.text(value + 0.7, pos, f"{value:.1f}", va="center", color=INK, fontsize=11.5)
    for pos, value in zip(y + height / 2, constraints):
        size_ax.text(value + 0.7, pos, f"{value:.1f}", va="center", color=INK, fontsize=11.5)

    time_ax = fig.add_axes([0.625, 0.47, 0.30, 0.32], facecolor=WHITE)
    solve = frame["solve_seconds"].to_numpy(dtype=float)
    build = frame["build_seconds"].to_numpy(dtype=float)
    time_ax.barh(y, solve, color=SCENARIO_COLORS, height=0.50)
    time_ax.scatter(build, y, s=95, color=WHITE, edgecolor=INK, linewidth=1.3, zorder=4)
    time_ax.set_yticks(y, ["乌鲁木齐", "固原", "武汉"])
    time_ax.invert_yaxis()
    time_ax.set_xlim(0, max(solve) * 1.28)
    time_ax.set_xlabel("时间（秒）")
    time_ax.set_title("构模与求解时间", loc="left", fontsize=17, color=INK, pad=14)
    time_ax.grid(axis="x", color=GRID, alpha=0.65, linewidth=0.8)
    time_ax.set_axisbelow(True)
    for pos, value, btime in zip(y, solve, build):
        time_ax.text(value + 1.8, pos, f"求解 {value:.1f}s", va="center", color=INK, fontsize=11.5)
        time_ax.text(
            btime + 1.8,
            pos + 0.22,
            f"构模 {btime:.1f}s",
            va="center",
            color=MUTED,
            fontsize=9.8,
        )

    clean_ax = fig.add_axes([0.075, 0.12, 0.48, 0.23], facecolor=WHITE)
    clean = frame["clean_heat_share"].to_numpy(dtype=float) * 100.0
    clean_ax.barh(y, np.full(3, 100.0), color="#E9EFF5", height=0.44)
    clean_ax.barh(y, clean, color=SCENARIO_COLORS, height=0.44)
    clean_ax.set_yticks(y, labels)
    clean_ax.invert_yaxis()
    clean_ax.set_xlim(0, 108)
    clean_ax.set_xlabel("清洁热占比（%）")
    clean_ax.set_title("清洁热供给结构", loc="left", fontsize=17, color=INK, pad=12)
    clean_ax.grid(axis="x", color=GRID, alpha=0.45, linewidth=0.8)
    clean_ax.set_axisbelow(True)
    for pos, value in zip(y, clean):
        clean_ax.text(value + 1.2, pos, f"{value:.1f}%", va="center", color=INK, fontsize=11.5, fontweight="bold")

    proof_ax = fig.add_axes([0.625, 0.12, 0.30, 0.23])
    proof_ax.axis("off")
    rounded_box(proof_ax, 0.0, 0.0, 1.0, 1.0, face=WHITE, edge="#D7E8E2", linewidth=1.0, radius=0.025)
    proof_ax.text(0.07, 0.77, "可靠性结果", fontsize=17, color=INK, fontweight="bold")
    proof_ax.text(0.07, 0.50, "0", fontsize=36, color=TEAL, fontweight="bold", va="center")
    proof_ax.text(0.20, 0.53, "热失供", fontsize=19, color=TEAL, fontweight="bold", va="center")
    proof_ax.text(0.07, 0.28, "三场景热失供率均为 0；武汉供冷失供率亦为 0。", fontsize=11.5, color=MUTED)
    proof_ax.text(0.07, 0.12, "模型规模增长未改变年度可靠性门结论。", fontsize=11.5, color=INK)

    add_source_note(
        canvas,
        "数据源：annual_replay_batch_summary.csv；变量、约束与时间均为实测回放输出。",
    )
    save_figure(fig, "fig02_scale_efficiency_cleanliness")


def acceptance_matrix(certificates: dict[str, dict]) -> None:
    gate_order: list[str] = []
    for profile, _, _ in PROFILES:
        for gate in certificates[profile]["gates"]:
            if gate["gate"] not in gate_order:
                gate_order.append(gate["gate"])

    matrix = np.full((len(PROFILES), len(gate_order)), -1, dtype=int)
    for row, (profile, _, _) in enumerate(PROFILES):
        mapping = {
            gate["gate"]: 1 if gate["passed"] else 0
            for gate in certificates[profile]["gates"]
        }
        for col, gate in enumerate(gate_order):
            if gate in mapping:
                matrix[row, col] = mapping[gate]

    fig = plt.figure(figsize=(16, 9))
    canvas = fig.add_axes([0, 0, 1, 1])
    canvas.axis("off")
    canvas.text(
        0.05,
        0.94,
        "三类场景年度回放验收矩阵",
        fontsize=29,
        fontweight="bold",
        color=INK,
        va="top",
    )
    canvas.text(
        0.05,
        0.885,
        "32 / 32 项适用门通过｜绿色为通过，灰色为不适用",
        fontsize=15.5,
        color=MUTED,
        va="top",
    )

    # Reserve enough left margin for the full Chinese scenario labels.
    ax = fig.add_axes([0.15, 0.23, 0.80, 0.53], facecolor=WHITE)
    cmap = ListedColormap(["#E7EDF3", RED, TEAL])
    norm = BoundaryNorm([-1.5, -0.5, 0.5, 1.5], cmap.N)
    ax.imshow(matrix, cmap=cmap, norm=norm, aspect="auto")

    for row in range(matrix.shape[0]):
        for col in range(matrix.shape[1]):
            value = matrix[row, col]
            label = "—" if value == -1 else ("通过" if value == 1 else "未通过")
            color = MUTED if value == -1 else WHITE
            ax.text(
                col,
                row,
                label,
                ha="center",
                va="center",
                color=color,
                fontsize=11.2,
                fontweight="bold",
            )

    ax.set_xticks(range(len(gate_order)), [GATE_LABELS.get(g, g) for g in gate_order])
    ax.set_yticks(range(3), [item[1] for item in PROFILES])
    ax.tick_params(axis="x", top=True, bottom=False, labeltop=True, labelbottom=False, length=0, pad=13)
    ax.tick_params(axis="y", length=0, pad=13)
    for label in ax.get_xticklabels():
        label.set_fontsize(10.7)
        label.set_color(INK)
    for label in ax.get_yticklabels():
        label.set_fontsize(13.0)
        label.set_color(INK)
    ax.set_xticks(np.arange(-0.5, len(gate_order), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, 3, 1), minor=True)
    ax.grid(which="minor", color=WHITE, linewidth=5)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    rounded_box(canvas, 0.08, 0.105, 0.87, 0.07, face=WHITE, edge=GRID, linewidth=0.9)
    canvas.text(
        0.105,
        0.14,
        "适用范围说明",
        fontsize=12.5,
        color=INK,
        fontweight="bold",
        va="center",
    )
    canvas.text(
        0.205,
        0.14,
        "S1/S2 不包含全年供冷与地源年度平衡，因此对应门记为不适用；不是缺失或失败。",
        fontsize=11.7,
        color=MUTED,
        va="center",
    )
    add_source_note(
        canvas,
        "数据源：三场景 annual_replay_acceptance_certificate.json；验收门为机器可读结果。",
    )
    save_figure(fig, "fig03_annual_replay_acceptance_matrix")


def heating_season_scope() -> None:
    """Render the declared 2024-2025 hourly replay horizons.

    These are modelling boundaries, not replay results. End dates are counted as
    complete service days, so the half-open hourly data intervals end at 00:00 on
    the following calendar day.
    """

    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.05,
        0.94,
        "2024—2025 三城市完整供暖季逐时回放口径",
        fontsize=28,
        fontweight="bold",
        color=INK,
        va="top",
    )
    ax.text(
        0.05,
        0.885,
        "统一 1 h 分辨率｜首尾日期完整计入｜与 216 h 代表日规划分层",
        fontsize=15.5,
        color=MUTED,
        va="top",
    )
    rounded_box(
        ax,
        0.815,
        0.875,
        0.13,
        0.048,
        face="#FFF7E6",
        edge="#F3C86A",
        linewidth=1.0,
        radius=0.012,
    )
    ax.text(
        0.88,
        0.899,
        "运行口径 · 待回放",
        ha="center",
        va="center",
        fontsize=10.7,
        color="#9A6500",
        fontweight="bold",
    )

    scope_path = ROOT / "data" / "heating_season_scopes_2024_2025.json"
    scopes = json.loads(scope_path.read_text(encoding="utf-8"))["scenarios"]
    palette = [
        (BLUE, "#EEF4FF"),
        (TEAL, "#EAF8F4"),
        (PURPLE, "#F4EFFF"),
    ]
    cards = []
    for scope, (color, tint) in zip(scopes, palette):
        start = datetime.fromisoformat(scope["start_inclusive"])
        end = datetime.fromisoformat(scope["end_exclusive"])
        calculated_hours = int((end - start).total_seconds() / 3600)
        expected_hours = int(scope["expected_hours"])
        if calculated_hours != expected_hours:
            raise ValueError(
                f"{scope['profile']}供暖季边界={calculated_hours} h，"
                f"与expected_hours={expected_hours} h不一致"
            )
        cards.append(
            {
                "scenario": scope["scenario"],
                "city": scope["city"],
                "period": scope["display_period"],
                "days": f"{expected_hours // 24:,} 天",
                "hours": f"{expected_hours:,} h",
                "tag": scope["scope_label"],
                "note": scope["scenario_note"],
                "color": color,
                "tint": tint,
            }
        )

    for card, x in zip(cards, [0.05, 0.36, 0.67]):
        rounded_box(ax, x, 0.565, 0.28, 0.245, edge=card["color"], linewidth=1.5)
        rounded_box(
            ax,
            x + 0.018,
            0.756,
            0.055,
            0.034,
            face=card["tint"],
            edge=card["tint"],
            linewidth=0,
            radius=0.009,
        )
        ax.text(
            x + 0.0455,
            0.773,
            card["scenario"],
            ha="center",
            va="center",
            fontsize=10.3,
            color=card["color"],
            fontweight="bold",
        )
        ax.text(
            x + 0.088,
            0.773,
            card["city"],
            va="center",
            fontsize=16.5,
            color=INK,
            fontweight="bold",
        )
        ax.text(x + 0.018, 0.718, card["hours"], fontsize=29, color=card["color"], fontweight="bold")
        ax.text(x + 0.018, 0.680, card["days"], fontsize=12.2, color=INK, fontweight="bold")
        ax.text(x + 0.018, 0.644, card["period"], fontsize=12.0, color=INK)
        ax.text(x + 0.018, 0.608, card["tag"], fontsize=10.7, color=card["color"], fontweight="bold")
        ax.text(x + 0.266, 0.608, card["note"], fontsize=10.0, color=MUTED, ha="right")

    flow = [
        ("216 h", "代表日规划", BLUE),
        ("冻结", "投资与容量", TEAL),
        ("2,880—4,392 h", "完整供暖季回放", PURPLE),
        ("逐时验收", "供需平衡与状态递推", TEAL),
    ]
    widths = [0.15, 0.15, 0.20, 0.20]
    xs = [0.07, 0.285, 0.50, 0.765]
    for idx, ((headline, caption, color), x, width) in enumerate(zip(flow, xs, widths)):
        rounded_box(ax, x, 0.355, width, 0.105, face=WHITE, edge=GRID, linewidth=1.0)
        ax.text(x + width / 2, 0.423, headline, ha="center", va="center", fontsize=15.0, color=color, fontweight="bold")
        ax.text(x + width / 2, 0.382, caption, ha="center", va="center", fontsize=10.4, color=MUTED)
        if idx < len(flow) - 1:
            ax.add_patch(
                FancyArrowPatch(
                    (x + width + 0.008, 0.407),
                    (xs[idx + 1] - 0.008, 0.407),
                    arrowstyle="-|>",
                    mutation_scale=15,
                    linewidth=1.35,
                    color="#A5B4C4",
                )
            )

    rounded_box(ax, 0.05, 0.105, 0.90, 0.155, face=WHITE, edge=GRID, linewidth=1.0)
    ax.text(0.075, 0.221, "口径说明", fontsize=12.7, color=INK, fontweight="bold", va="center")
    ax.text(
        0.075,
        0.183,
        "武汉无全市统一法定供暖季：采用武昌热电2024—2025项目服务周期，连续120天、2,880小时。",
        fontsize=11.2,
        color=MUTED,
        va="center",
    )
    ax.text(
        0.075,
        0.143,
        "固原市城市管理局通知明确本年度为2024.11.01—2025.04.15，共166天；模型生成3,984条连续逐时记录。",
        fontsize=11.2,
        color=MUTED,
        va="center",
    )
    add_source_note(
        ax,
        "来源口径：乌鲁木齐市政府、固原市城市管理局、武汉市政府门户网站；日期首尾完整计入。",
    )
    save_figure(fig, "fig04_2024_2025_heating_season_scope")


def main() -> None:
    setup_style()
    frame, certificates = load_data()
    evidence_chain(frame)
    scale_and_efficiency(frame)
    acceptance_matrix(certificates)
    heating_season_scope()
    print(f"Generated report figures in {OUTPUT}")


if __name__ == "__main__":
    main()
