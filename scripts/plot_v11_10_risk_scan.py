from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BG = "#F6F2E9"
INK = "#183044"
MUTED = "#66717A"
TEAL = "#267A73"
RED = "#C84A3D"
GOLD = "#D4A13B"
BLUE = "#3F6F95"

CITY = {
    "scenario1_urumqi": "乌鲁木齐",
    "scenario2_guyuan": "固原",
    "scenario3_wuhan": "武汉",
}
COLORS = {
    "scenario1_urumqi": BLUE,
    "scenario2_guyuan": RED,
    "scenario3_wuhan": TEAL,
}


def _style() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.facecolor": BG,
            "axes.facecolor": BG,
            "axes.edgecolor": "#A8AEA9",
            "axes.labelcolor": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "text.color": INK,
            "font.size": 10,
        }
    )


def _clean_axes(ax: plt.Axes) -> None:
    ax.grid(color="#C9CDC8", alpha=0.65, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)


def plot_cost_risk_frontier(summary: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.4), facecolor=BG)
    for ax, profile in zip(axes, CITY):
        block = summary[summary["profile"] == profile].sort_values("cvar_weight")
        x = block[
            "expected_cost_before_cvar_yuan_per_year_delta_vs_neutral"
        ].astype(float)
        y = -block[
            "cvar_operating_risk_cost_yuan_delta_vs_neutral"
        ].astype(float)
        if float(x.abs().max()) < 0.01 and float(y.abs().max()) < 0.01:
            ax.axis("off")
            ax.set_title(CITY[profile], loc="left", fontsize=15, fontweight="bold")
            ax.text(
                0.5,
                0.54,
                "投资方案与尾部成本\n在扫描区间内保持不变",
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=16,
                color=TEAL,
            )
            ax.text(
                0.5,
                0.37,
                "λ = 0 → 0.10",
                transform=ax.transAxes,
                ha="center",
                color=MUTED,
            )
            continue
        ax.plot(x, y, color=COLORS[profile], linewidth=2.2, alpha=0.9)
        ax.scatter(
            x,
            y,
            s=90,
            color=COLORS[profile],
            edgecolors=BG,
            linewidths=1.5,
            zorder=3,
        )
        for row, x_value, y_value in zip(block.itertuples(), x, y):
            ax.annotate(
                f"λ={row.cvar_weight:g}",
                (x_value, y_value),
                xytext=(6, 7),
                textcoords="offset points",
                fontsize=9,
                color=INK,
            )
        ax.set_title(CITY[profile], loc="left", fontsize=15, fontweight="bold")
        ax.set_xlabel("相对风险中性方案的期望成本增量（元/年）")
        if ax is axes[0]:
            ax.set_ylabel("CVaR尾部运行成本降低（元/年）")
        _clean_axes(ax)
    fig.suptitle(
        "用很小的期望成本换取气象年尾部风险下降",
        x=0.06,
        y=0.995,
        ha="left",
        fontsize=20,
        fontweight="bold",
    )
    fig.text(
        0.06,
        0.925,
        "共享投资 · α=0.80 · λ∈{0, 0.02, 0.05, 0.10}；越靠左上越优",
        color=MUTED,
        fontsize=11,
    )
    fig.tight_layout(rect=(0.03, 0.02, 1, 0.875))
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def plot_investment_breakpoints(stability: pd.DataFrame, output: Path) -> None:
    profiles = list(CITY)
    weights = sorted(stability["cvar_weight"].astype(float).unique())
    fig, ax = plt.subplots(figsize=(13, 5.8), facecolor=BG)
    y_positions = np.arange(len(profiles))[::-1]
    for y, profile in zip(y_positions, profiles):
        block = stability[stability["profile"] == profile].sort_values(
            "cvar_weight"
        )
        ax.plot(weights, [y] * len(weights), color="#AAB1AC", linewidth=2)
        for row in block.itertuples():
            changed = int(row.materially_changed_asset_count)
            ax.scatter(
                row.cvar_weight,
                y,
                s=100 + changed * 95,
                color=COLORS[profile] if changed else "#C9CDC8",
                edgecolors=BG,
                linewidths=1.8,
                zorder=3,
            )
            ax.text(
                row.cvar_weight,
                y - 0.24,
                "稳定" if changed == 0 else f"{changed}项变化",
                ha="center",
                va="top",
                fontsize=9,
                color=MUTED,
            )
        first = block["first_material_change_weight"].dropna()
        label = (
            f"首个转折 λ={float(first.iloc[0]):g}"
            if len(first)
            else "扫描区间内无转折"
        )
        ax.text(0.106, y, label, va="center", fontsize=10, color=INK)
    ax.set_yticks(y_positions, [CITY[item] for item in profiles])
    ax.set_xticks(weights)
    ax.set_xlim(-0.006, 0.135)
    ax.set_ylim(-0.65, len(profiles) - 0.25)
    ax.set_xlabel("CVaR权重 λ")
    ax.set_title(
        "三类场景呈现不同的投资风险响应",
        loc="left",
        fontsize=23,
        fontweight="bold",
        pad=22,
    )
    ax.text(
        0,
        1.01,
        "实质变化阈值：1 kW/kWh + 风险中性容量的0.1%；圆越大表示变化资产越多",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=10,
    )
    ax.grid(axis="x", color="#C9CDC8", alpha=0.65, linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def plot_cvar_ledger_correction(
    summary: pd.DataFrame,
    old_results: Path,
    output: Path,
) -> None:
    rows = []
    for profile in CITY:
        old = json.loads(
            (
                old_results
                / profile
                / "representative_planning"
                / "summary.json"
            ).read_text(encoding="utf-8")
        )
        old_premium = float(
            old["components_yuan_per_year"].get("cvar_risk_premium", 0.0)
        )
        old_reported = max(
            float(item["operating_risk_cost_yuan"])
            for item in old["scenario_results"].values()
        )
        new = summary[
            (summary["profile"] == profile)
            & np.isclose(summary["cvar_weight"].astype(float), 0.05)
        ].iloc[0]
        rows.append(
            {
                "profile": profile,
                "old_gap": old_premium / 0.05 - old_reported,
                "new_gap": float(new["cvar_consistency_absolute_error_yuan"]),
            }
        )
    frame = pd.DataFrame(rows)
    x = np.arange(len(frame))
    fig, ax = plt.subplots(figsize=(11.5, 6.3), facecolor=BG)
    width = 0.34
    old_bars = ax.bar(
        x - width / 2,
        frame["old_gap"] / 1000.0,
        width,
        color=RED,
        label="修正前：毛成本CVaR与净成本报告差额",
    )
    new_bars = ax.bar(
        x + width / 2,
        frame["new_gap"] / 1000.0,
        width,
        color=TEAL,
        label="修正后：完整净成本台账误差",
    )
    ax.set_xticks(x, [CITY[item] for item in frame["profile"]])
    ax.set_ylabel("一致性差额（千元/年）")
    ax.set_title(
        "CVaR约束与场景净成本报告已统一到同一台账",
        loc="left",
        fontsize=22,
        fontweight="bold",
        pad=18,
    )
    ax.legend(frameon=False, loc="upper right")
    _clean_axes(ax)
    maximum = max(float(frame["old_gap"].max() / 1000.0), 1.0)
    for bar in old_bars:
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + maximum * 0.025,
            f"{bar.get_height():.1f}",
            ha="center",
            va="bottom",
            fontsize=10,
        )
    for bar in new_bars:
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            maximum * 0.012,
            "≈0",
            ha="center",
            va="bottom",
            fontsize=10,
            color=TEAL,
        )
    ax.set_ylim(0, maximum * 1.16)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def plot_annual_replay_impact(old_path: Path, new_path: Path, output: Path) -> None:
    old = pd.read_csv(old_path, dtype={"weather_year": str})
    new = pd.read_csv(new_path, dtype={"weather_year": str})
    keys = ["profile", "weather_year"]
    frame = old.merge(new, on=keys, suffixes=("_old", "_new"))
    frame["label"] = [
        f"{CITY[row.profile]}·{row.weather_year}" for row in frame.itertuples()
    ]
    frame["emission_delta_t"] = (
        frame["emissions_kg_per_year_new"]
        - frame["emissions_kg_per_year_old"]
    ) / 1000.0
    frame["cost_delta_milliyuan"] = 1000.0 * (
        frame["levelized_heat_cost_yuan_per_kwh_new"]
        - frame["levelized_heat_cost_yuan_per_kwh_old"]
    )
    y = np.arange(len(frame))
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 7.0), facecolor=BG)
    panels = [
        ("emission_delta_t", "年度排放变化", "tCO2e/年"),
        ("cost_delta_milliyuan", "度热成本变化", "厘元/kWh"),
    ]
    for index, (column, title, unit) in enumerate(panels):
        ax = axes[index]
        values = frame[column].astype(float)
        colors = [TEAL if value < -1e-9 else RED if value > 1e-9 else "#B8BDB8" for value in values]
        bars = ax.barh(y, values, color=colors, height=0.58)
        ax.axvline(0, color=INK, linewidth=1.0)
        ax.set_yticks(y, frame["label"] if index == 0 else [""] * len(frame))
        ax.invert_yaxis()
        ax.set_title(title, loc="left", fontsize=15, fontweight="bold")
        ax.set_xlabel(unit)
        _clean_axes(ax)
        span = max(values.abs().max(), 0.1)
        for bar, value in zip(bars, values):
            display_value = 0.0 if abs(float(value)) < 0.0005 else float(value)
            ax.text(
                value + (0.035 * span if value >= 0 else -0.035 * span),
                bar.get_y() + bar.get_height() / 2,
                f"{display_value:+.3f}",
                va="center",
                ha="left" if value >= 0 else "right",
                fontsize=9,
            )
        ax.set_xlim(-span * 1.28, span * 1.28)
    fig.suptitle(
        "修正后0.05方案的完整年度影响",
        x=0.055,
        y=0.995,
        ha="left",
        fontsize=20,
        fontweight="bold",
    )
    fig.text(
        0.055,
        0.920,
        "同一气象年、同一逐时验收口径；负值表示下降",
        color=MUTED,
        fontsize=10,
    )
    fig.tight_layout(rect=(0.03, 0.02, 1, 0.87))
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan-output", required=True)
    parser.add_argument("--old-planning-results", required=True)
    parser.add_argument("--old-replay-summary", required=True)
    parser.add_argument("--new-replay-summary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    _style()
    scan = Path(args.scan_output).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = pd.read_csv(scan / "risk_weight_scan_summary.csv")
    stability = pd.read_csv(scan / "investment_stability_summary.csv")
    plot_cost_risk_frontier(summary, output / "cost_risk_frontier.png")
    plot_investment_breakpoints(stability, output / "investment_breakpoints.png")
    plot_cvar_ledger_correction(
        summary,
        Path(args.old_planning_results).resolve(),
        output / "cvar_ledger_correction.png",
    )
    plot_annual_replay_impact(
        Path(args.old_replay_summary).resolve(),
        Path(args.new_replay_summary).resolve(),
        output / "corrected_plan_annual_impact.png",
    )


if __name__ == "__main__":
    main()
