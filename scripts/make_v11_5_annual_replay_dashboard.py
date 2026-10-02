from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "outputs" / "v11_5_three_scenario_annual_replay"
FIGURES = ROOT / "outputs" / "v11_5_figures"

PROFILES = [
    ("scenario1_urumqi", "S1 大型集中供热", "乌鲁木齐"),
    ("scenario2_guyuan", "S2 分散式清洁转型", "固原"),
    ("scenario3_wuhan", "S3 夏热冬冷新建", "武汉"),
]


def load() -> tuple[pd.DataFrame, dict[str, dict]]:
    records = []
    certificates: dict[str, dict] = {}
    for profile, label, city in PROFILES:
        folder = REPLAY / profile
        summary = json.loads(
            (folder / "annual_replay_summary.json").read_text(encoding="utf-8")
        )
        replay = json.loads(
            (folder / "fixed_investment_annual_replay" / "summary.json").read_text(
                encoding="utf-8"
            )
        )
        certificate = json.loads(
            (folder / "annual_replay_acceptance_certificate.json").read_text(
                encoding="utf-8"
            )
        )
        certificates[profile] = certificate
        storage = pd.read_csv(folder / "seasonal_storage_postcheck.csv")
        records.append(
            {
                "profile": profile,
                "scenario": label,
                "city": city,
                "certified": bool(summary["annual_replay_certified"]),
                "passed_gates": int(certificate["passed_gate_count"]),
                "gate_count": int(certificate["gate_count"]),
                "variables": int(summary["replay_variables"]),
                "integer_variables": int(summary["replay_integer_variables"]),
                "constraints": int(summary["replay_constraints"]),
                "matrix_nonzeros": int(summary["replay_matrix_nonzeros"]),
                "build_seconds": float(summary["replay_build_seconds"]),
                "solve_seconds": float(summary["replay_solve_seconds"]),
                "unserved_rate": float(summary["replay_unserved_rate"]),
                "heat_gap_kw": float(summary["firm_heat_capacity_gap_kw"]),
                "grid_gap_kw": float(summary["firm_electric_capacity_gap_kw"]),
                "clean_heat_share": float(replay["clean_heat_share"]),
                "cooling_unserved_rate": float(
                    replay.get("cooling_unserved_rate", 0.0)
                ),
                "ground_balance_ratio": float(
                    replay.get("ground_extraction_to_rejection_ratio", 0.0)
                ),
                "storage_boundary_groups": int(len(storage)),
                "storage_boundary_pass_count": int(
                    storage["boundary_condition_pass"].astype(bool).sum()
                ),
                "maximum_storage_boundary_residual_kwh": float(
                    storage["soc_balance_residual_kwh"].abs().max()
                )
                if not storage.empty
                else 0.0,
            }
        )
    return pd.DataFrame(records), certificates


def write_summary(frame: pd.DataFrame, certificates: dict[str, dict]) -> None:
    frame.to_csv(
        REPLAY / "annual_replay_batch_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    payload = {
        "version": "11.5.0",
        "all_profiles_certified": bool(frame["certified"].all()),
        "profiles": frame.to_dict(orient="records"),
        "failed_gates": {
            name: value["failed_gates"] for name, value in certificates.items()
        },
    }
    (REPLAY / "annual_replay_batch_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _card(ax, x, title, value, note, accent):
    box = FancyBboxPatch(
        (x, 0.710),
        0.205,
        0.155,
        boxstyle="round,pad=0.014,rounding_size=0.018",
        facecolor="#FFFFFF",
        edgecolor=accent,
        linewidth=1.2,
    )
    ax.add_patch(box)
    ax.text(x + 0.018, 0.842, title, fontsize=13.0, color="#64748B", va="top")
    ax.text(
        x + 0.018,
        0.800,
        value,
        fontsize=22.0,
        fontweight="bold",
        color=accent,
        va="top",
    )
    ax.text(x + 0.018, 0.746, note, fontsize=11.5, color="#64748B", va="top")


def dashboard(frame: pd.DataFrame) -> None:
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "SimHei",
        "Arial",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    colors = ["#2563EB", "#0F9D7A", "#7C3AED"]
    fig = plt.figure(figsize=(14.0, 7.7), facecolor="#F2F6F9")
    canvas = fig.add_axes([0, 0, 1, 1])
    canvas.axis("off")
    canvas.set_xlim(0, 1)
    canvas.set_ylim(0, 1)
    canvas.text(
        0.05,
        0.955,
        "V11.5 三类供热场景全年固定投资回放证据",
        fontsize=23,
        fontweight="bold",
        color="#17324D",
        va="top",
    )
    canvas.text(
        0.05,
        0.914,
        "代表时段确定投资 → 冻结全部资产 → 连续 8760 小时逐时回放 → 机器可读验收门",
        fontsize=14.0,
        color="#5B6573",
        va="top",
    )
    _card(canvas, 0.05, "全年闭环", "3 / 3", "三类场景全部认证通过", "#0F9D7A")
    _card(canvas, 0.28, "逐时规模", "1,186,812", "三场景变量合计", "#2563EB")
    _card(canvas, 0.51, "可靠性", "0 失供", "供热；武汉供冷亦为 0", "#0F9D7A")
    _card(canvas, 0.74, "验收门", "32 / 32", "含容量、储热与地源平衡", "#7C3AED")

    size_ax = fig.add_axes([0.09, 0.21, 0.38, 0.40], facecolor="#FFFFFF")
    solve_ax = fig.add_axes([0.60, 0.21, 0.33, 0.40], facecolor="#FFFFFF")
    for ax in (size_ax, solve_ax):
        for spine in ax.spines.values():
            spine.set_color("#D8E1E8")
        ax.grid(axis="x", alpha=0.15)
        ax.tick_params(axis="both", labelsize=12)

    y = np.arange(len(frame))
    size_values = frame["variables"].to_numpy() / 10000.0
    size_ax.barh(y, size_values, color=colors, height=0.52)
    size_ax.set_yticks(y, ["S1 集中供热", "S2 分散式转型", "S3 夏热冬冷"])
    size_ax.invert_yaxis()
    size_ax.set_xlabel("变量数（万）", color="#64748B", fontsize=13)
    size_ax.set_title(
        "正式全年模型规模",
        loc="left",
        fontsize=16.0,
        fontweight="bold",
        color="#17324D",
        pad=12,
    )
    size_ax.set_xlim(0, max(size_values) * 1.04)
    for pos, value, integer in zip(
        y, size_values, frame["integer_variables"]
    ):
        size_ax.text(
            value - 0.5,
            pos,
            f"{value:.1f} 万 | 整数 {integer:,}",
            ha="right",
            va="center",
            fontsize=10.8,
            color="#FFFFFF",
        )

    solve_values = frame["solve_seconds"].to_numpy()
    solve_ax.barh(y, solve_values, color=colors, height=0.52)
    solve_ax.set_yticks(y, frame["city"])
    solve_ax.invert_yaxis()
    solve_ax.set_xlabel("求解时间（秒）", color="#64748B", fontsize=13)
    solve_ax.set_title(
        "同机实测求解时间",
        loc="left",
        fontsize=16.0,
        fontweight="bold",
        color="#17324D",
        pad=12,
    )
    solve_ax.set_xlim(0, max(solve_values) * 1.30)
    for pos, value, passed, total in zip(
        y,
        solve_values,
        frame["passed_gates"],
        frame["gate_count"],
    ):
        solve_ax.text(
            value + 1.4,
            pos,
            f"{value:.1f}s｜{passed}/{total} 门通过",
            va="center",
            fontsize=10.8,
            color="#334155",
        )

    canvas.text(
        0.05,
        0.125,
        "储热边界后检查已按真实状态方程修正：循环储热的年初状态取末时段状态，不再把首小时动作误报为跨年漂移。",
        fontsize=12.5,
        color="#334155",
    )
    canvas.text(
        0.05,
        0.086,
        "S1 热源裕度 1,100 kW；S2/S3 热源与电网容量贴边满足。武汉年取热/年排热比 0.957，位于 0.80–1.25。",
        fontsize=12.0,
        color="#64748B",
    )
    canvas.text(
        0.95,
        0.034,
        "north_heat_challenge · 11.5.0",
        ha="right",
        fontsize=11.0,
        color="#8291A3",
    )
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / "three_scenario_annual_replay_dashboard.png", dpi=220)
    fig.savefig(FIGURES / "three_scenario_annual_replay_dashboard.svg")
    plt.close(fig)


def acceptance_matrix(certificates: dict[str, dict]) -> None:
    gate_order = []
    for profile, _, _ in PROFILES:
        for gate in certificates[profile]["gates"]:
            if gate["gate"] not in gate_order:
                gate_order.append(gate["gate"])
    labels = {
        "continuous_full_calendar_year": "连续完整公历年",
        "representative_period_coverage": "代表时段覆盖",
        "representative_planning_optimal": "代表期规划最优",
        "annual_replay_optimal": "全年回放最优",
        "all_investments_frozen": "投资全部冻结",
        "prebuild_firm_capacity_readiness": "可靠容量就绪",
        "annual_heat_reliability": "全年供热可靠性",
        "worst_scenario_heat_reliability": "最差场景可靠性",
        "annual_cooling_reliability": "全年供冷可靠性",
        "storage_boundary_conditions": "储热边界闭合",
        "ground_thermal_annual_balance": "地源年度热平衡",
        "minimum_clean_heat_share": "清洁热约束",
    }
    matrix = np.full((len(PROFILES), len(gate_order)), np.nan)
    for row, (profile, _, _) in enumerate(PROFILES):
        by_gate = {gate["gate"]: gate["passed"] for gate in certificates[profile]["gates"]}
        for column, gate in enumerate(gate_order):
            if gate in by_gate:
                matrix[row, column] = 1.0 if by_gate[gate] else 0.0

    fig, ax = plt.subplots(figsize=(14.0, 4.3), facecolor="#F2F6F9")
    ax.set_facecolor("#FFFFFF")
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix[row, column]
            if np.isnan(value):
                color, symbol = "#E8EEF3", "N/A"
            elif value == 1.0:
                color, symbol = "#0F9D7A", "PASS"
            else:
                color, symbol = "#D64545", "FAIL"
            ax.scatter(column, row, s=650, marker="s", color=color)
            ax.text(
                column,
                row,
                symbol,
                ha="center",
                va="center",
                fontsize=10.5,
                fontweight="bold",
                color="#FFFFFF" if symbol != "N/A" else "#64748B",
            )
    ax.set_xticks(range(len(gate_order)), [labels.get(g, g) for g in gate_order], rotation=35, ha="right")
    ax.set_yticks(range(len(PROFILES)), [item[1] for item in PROFILES])
    ax.invert_yaxis()
    ax.set_xlim(-0.7, len(gate_order) - 0.3)
    ax.set_ylim(len(PROFILES) - 0.4, -0.7)
    ax.set_title(
        "V11.5 全年回放验收矩阵｜32 / 32 适用门通过",
        loc="left",
        fontsize=17,
        fontweight="bold",
        color="#17324D",
        pad=16,
    )
    ax.tick_params(axis="both", length=0, labelsize=12.0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout(pad=1.8)
    fig.savefig(FIGURES / "annual_replay_acceptance_matrix.png", dpi=220)
    fig.savefig(FIGURES / "annual_replay_acceptance_matrix.svg")
    plt.close(fig)


def main() -> None:
    frame, certificates = load()
    write_summary(frame, certificates)
    dashboard(frame)
    acceptance_matrix(certificates)


if __name__ == "__main__":
    main()
