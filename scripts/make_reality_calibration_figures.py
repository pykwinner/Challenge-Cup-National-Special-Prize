from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / "outputs" / "reality_calibration"
OUTPUT = ROOT / "outputs" / "reality_calibration_figures"
PUBLIC = ROOT / "data" / "public_sources"

BLUE = "#2563EB"
CYAN = "#0891B2"
GREEN = "#16A34A"
AMBER = "#D97706"
RED = "#DC2626"
PURPLE = "#7C3AED"
SLATE = "#64748B"
INK = "#0F172A"
GRID = "#DCE3EC"
BG = "#F8FAFC"


def configure() -> None:
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "axes.edgecolor": GRID,
        "axes.labelcolor": "#334155",
        "xtick.color": "#475569",
        "ytick.color": "#475569",
        "text.color": INK,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.titleweight": "bold",
        "axes.titlesize": 15,
    })


def finish(fig: plt.Figure, stem: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / f"{stem}.png", dpi=260, bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.savefig(OUTPUT / f"{stem}.svg", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def summary(name: str) -> dict:
    return json.loads((CAL / name / "summary.json").read_text(encoding="utf-8"))


def annotate_bars(ax: plt.Axes, bars, fmt: str = "{:.3f}", pad: float = 0.01) -> None:
    for bar in bars:
        value = float(bar.get_height())
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + pad,
            fmt.format(value),
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )


def data_coverage() -> None:
    sources = pd.read_csv(PUBLIC / "source_catalog.csv")
    facts = pd.read_csv(PUBLIC / "scenario_public_facts.csv")
    order = [
        "scenario1_existing_network",
        "scenario2_distributed_clean",
        "scenario3_high_quality_newbuild",
        "common",
    ]
    labels = ["乌鲁木齐\n存量重构", "固原\n分散转型", "武汉\n高品质新建", "公共参数"]
    source_counts = sources.groupby("scenario").size().reindex(order, fill_value=0)
    fact_counts = facts.groupby("scenario").size().reindex(order, fill_value=0)
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.5), gridspec_kw={"wspace": 0.35})
    fig.subplots_adjust(top=0.72)
    colors = [BLUE, GREEN, AMBER, SLATE]
    for ax, values, title_text, unit in (
        (axes[0], source_counts, "可追溯权威来源", "项"),
        (axes[1], fact_counts, "可直接入模的量化事实", "条"),
    ):
        bars = ax.bar(labels, values.to_numpy(), color=colors, width=0.62)
        ax.set_title(title_text, loc="left")
        ax.grid(axis="y", alpha=0.85)
        ax.set_axisbelow(True)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + max(values) * 0.025, f"{int(value)}{unit}",
                    ha="center", va="bottom", fontweight="bold")
        ax.set_ylim(0, max(values) * 1.18)
    fig.suptitle("现实证据库已从资料清单升级为模型字段库", fontsize=20, fontweight="bold", x=0.06, y=0.98, ha="left")
    fig.text(0.06, 0.86, "67个来源 · 312条事实 · 每条均保留来源、口径、模型映射和局限", color="#475569")
    finish(fig, "01_reality_evidence_coverage")


def scenario2_cost() -> None:
    frame = pd.read_csv(CAL / "reality_calibration_comparison.csv").set_index("variant")
    keys = [
        "scenario2_legacy_assumptions",
        "scenario2_evidence_resident_tariff",
        "scenario2_evidence_general_meter_proxy",
        "scenario2_pellet_stress_1_20",
        "scenario2_biomass_policy_20pct",
    ]
    labels = ["旧参数\n(含免费存量)", "证据基准\n居民电价", "证据基准\n一般总表", "颗粒压力\n1.20元/kg", "证据基准\n强制20%生物质"]
    values = frame.loc[keys, "regulated_lcoh_yuan_per_kwh"].to_numpy()
    colors = [SLATE, GREEN, BLUE, RED, PURPLE]
    fig, ax = plt.subplots(figsize=(12.8, 6.2))
    fig.subplots_adjust(top=0.78)
    bars = ax.bar(labels, values, color=colors, width=0.64)
    annotate_bars(ax, bars, pad=0.007)
    ax.axhline(values[1], color=GREEN, linewidth=1.5, linestyle="--", alpha=0.8)
    ax.text(0.55, values[1] + 0.009, "证据基准", color=GREEN, fontsize=10)
    uplift = (values[1] / values[0] - 1.0) * 100
    ax.annotate(
        f"纠正颗粒价格与免费资产后\n现金热价 +{uplift:.1f}%",
        xy=(1, values[1]), xytext=(0.15, 0.39),
        arrowprops={"arrowstyle": "->", "color": GREEN, "lw": 1.6},
        fontsize=11, fontweight="bold", color=INK,
    )
    ax.set_ylabel("现金口径平准供热成本（元/kWh）")
    ax.set_ylim(0, max(values) * 1.25)
    fig.suptitle("固原：现实证据修正了“看起来过于便宜”的方案", x=0.10, y=0.98, ha="left", fontsize=19, fontweight="bold")
    fig.text(0.10, 0.88, "20%生物质硬下限与无下限方案结果完全一致，说明它在证据基准下并非有效驱动约束",
             color="#475569")
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    finish(fig, "02_guyuan_reality_cost_calibration")


def scenario2_tradeoff() -> None:
    frame = pd.read_csv(CAL / "reality_calibration_comparison.csv").set_index("variant")
    keys = [
        "scenario2_legacy_assumptions",
        "scenario2_evidence_resident_tariff",
        "scenario2_evidence_general_meter_proxy",
        "scenario2_pellet_stress_1_20",
    ]
    labels = ["旧参数", "证据+居民电价", "证据+一般总表", "颗粒1.20元/kg"]
    colors = [SLATE, GREEN, BLUE, RED]
    fig, ax = plt.subplots(figsize=(11.6, 6.5))
    fig.subplots_adjust(top=0.79)
    x = frame.loc[keys, "biomass_fuel_kg_per_year"].to_numpy() / 1000
    y = frame.loc[keys, "regulated_lcoh_yuan_per_kwh"].to_numpy()
    emissions = frame.loc[keys, "emissions_kg_per_year"].to_numpy() / 1000
    sizes = 140 + emissions * 0.65
    for xi, yi, size, label, color, emission in zip(x, y, sizes, labels, colors, emissions):
        ax.scatter(xi, yi, s=size, color=color, alpha=0.88, edgecolor=BG, linewidth=2)
        ax.annotate(f"{label}\n{emission:.0f} tCO2/年", (xi, yi), xytext=(8, 7), textcoords="offset points", fontsize=10)
    ax.set_xlabel("年生物质颗粒采购量（吨）")
    ax.set_ylabel("现金口径平准供热成本（元/kWh）")
    fig.suptitle("固原：颗粒涨价会降低生物质用量，但不自动降低系统碳排", x=0.11, y=0.98, ha="left", fontsize=18, fontweight="bold")
    fig.text(0.11, 0.89, "气泡大小表示年碳排；需要同时约束本地污染、清洁率、电网碳因子和用户负担",
             color="#475569")
    ax.grid(alpha=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    finish(fig, "03_guyuan_biomass_cost_emission_tradeoff")


def scenario1_reliability() -> None:
    base = summary("scenario1_public_baseline")
    stress = summary("scenario1_reliability_overlay")
    metrics = [
        ("现金热价\n元/kWh", base["regulated_levelized_heat_cost_yuan_per_kwh"], stress["regulated_levelized_heat_cost_yuan_per_kwh"]),
        ("年化投资\n万元/年", base["components_yuan_per_year"]["annualized_capex"] / 1e4, stress["components_yuan_per_year"]["annualized_capex"] / 1e4),
        ("配电接入\nkW", base["grid_connection_capacity_kw"], stress["grid_connection_capacity_kw"]),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 5.4), gridspec_kw={"wspace": 0.38})
    fig.subplots_adjust(top=0.70)
    for ax, (label, before, after) in zip(axes, metrics):
        ax.plot([0, 1], [before, after], color=SLATE, linewidth=2.5, zorder=1)
        ax.scatter([0], [before], s=120, color=BLUE, zorder=2)
        ax.scatter([1], [after], s=120, color=RED, zorder=2)
        ax.set_xticks([0, 1], ["基础规划", "可靠性叠加"])
        ax.set_title(label)
        ax.text(0, before, f" {before:.3f}" if before < 1 else f" {before:.1f}", ha="right", va="bottom")
        ax.text(1, after, f" {after:.3f}" if after < 1 else f" {after:.1f}", ha="left", va="bottom", fontweight="bold")
        ax.text(0.5, (before + after) / 2, f"+{(after / before - 1) * 100:.1f}%", ha="center", va="bottom", color=RED, fontweight="bold")
        span = max(abs(after - before), abs(before) * 0.08, 1e-4)
        ax.set_ylim(min(before, after) - span * 0.65, max(before, after) + span * 0.85)
        ax.grid(axis="y")
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0)
    fig.suptitle("乌鲁木齐：可靠性不是免费附件，而是一项可量化的容量保险", fontsize=20, fontweight="bold", x=0.05, y=0.98, ha="left")
    fig.text(0.05, 0.86, "余热链不计独立可靠容量 + CHP/燃气应急资产保留 + 热源/骨干管N−1", color="#475569")
    finish(fig, "04_urumqi_reliability_premium")


def scenario3_joint_service() -> None:
    heat = summary("scenario3_heat_only")
    joint = summary("scenario3_joint_heating_cooling")
    labels = ["只算供热\n全部资本压给冬季", "冷热联合\n系统单位服务成本", "冷热联合\n能量比例分摊供热", "冷热联合\n能量比例分摊供冷"]
    values = [
        heat["regulated_levelized_heat_cost_yuan_per_kwh"],
        joint["joint_service_levelized_cost_yuan_per_kwh"],
        joint["energy_proportional_levelized_heat_cost_yuan_per_kwh"],
        joint["energy_proportional_levelized_cooling_cost_yuan_per_kwh"],
    ]
    colors = [SLATE, AMBER, RED, CYAN]
    fig, ax = plt.subplots(figsize=(12.6, 6.2))
    fig.subplots_adjust(top=0.78)
    bars = ax.bar(labels, values, color=colors, width=0.63)
    annotate_bars(ax, bars, pad=0.008)
    reduction = (1.0 - values[1] / values[0]) * 100
    ax.annotate(
        f"全年共享资产后\n系统单位服务成本 −{reduction:.1f}%",
        xy=(1, values[1]), xytext=(1.65, 0.50),
        arrowprops={"arrowstyle": "->", "color": AMBER, "lw": 1.8},
        fontsize=11, fontweight="bold",
    )
    ax.set_ylabel("平准成本（元/kWh服务量）")
    ax.set_ylim(0, 0.57)
    fig.suptitle("武汉：夏季制冷负荷让同一套热泵、钻孔和配电资产全年工作", x=0.09, y=0.98, ha="left", fontsize=19, fontweight="bold")
    fig.text(0.09, 0.88, "供热监管口径仍保留全资本成本；0.337元/kWh仅是透明的能量比例分摊结果",
             color="#475569")
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    finish(fig, "05_wuhan_joint_service_economics")


def scenario3_ground_balance() -> None:
    heat = summary("scenario3_heat_only")
    joint = summary("scenario3_joint_heating_cooling")
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.7), gridspec_kw={"wspace": 0.32})
    fig.subplots_adjust(top=0.80)
    services = np.array([
        [heat["demand_kwh_per_year"], 0.0],
        [joint["demand_kwh_per_year"], joint["cooling_demand_kwh_per_year"]],
    ]) / 1e6
    axes[0].bar(["只供热", "冷热联合"], services[:, 0], color=RED, label="供热")
    axes[0].bar(["只供热", "冷热联合"], services[:, 1], bottom=services[:, 0], color=CYAN, label="供冷")
    for x, total in enumerate(services.sum(axis=1)):
        axes[0].text(x, total + 0.035, f"{total:.2f} GWh", ha="center", fontweight="bold")
    axes[0].set_ylabel("年服务量（GWh）")
    axes[0].set_title("同等年化资本下的资产利用率")
    axes[0].legend(frameon=False, ncol=2, loc="upper left")
    axes[0].set_ylim(0, services.sum(axis=1).max() * 1.22)
    axes[0].grid(axis="y")

    extraction = joint["ground_heat_extraction_kwh_per_year"] / 1e6
    rejection = joint["ground_heat_rejection_kwh_per_year"] / 1e6
    bars = axes[1].bar(["冬季从地层吸热", "夏季向地层释热"], [extraction, rejection], color=[RED, CYAN], width=0.6)
    for bar, value in zip(bars, [extraction, rejection]):
        axes[1].text(bar.get_x() + bar.get_width() / 2, value + 0.025, f"{value:.3f} GWh", ha="center", fontweight="bold")
    ratio = joint["ground_extraction_to_rejection_ratio"]
    axes[1].text(0.5, 0.90, f"吸热/释热 = {ratio:.2f}\n约束区间 0.80–1.25", transform=axes[1].transAxes,
                 ha="center", va="center", fontsize=13, fontweight="bold",
                 bbox={"boxstyle": "round,pad=0.5", "facecolor": "#E0F2FE", "edgecolor": "#7DD3FC"})
    axes[1].set_ylabel("地源侧年热量（GWh）")
    axes[1].set_title("地源热泵年吸放热闭合")
    axes[1].set_ylim(0, max(extraction, rejection) * 1.35)
    axes[1].grid(axis="y")
    for ax in axes:
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("武汉联合方案通过了全年服务量与地源热平衡双重校核", fontsize=19, fontweight="bold", x=0.05, ha="left")
    finish(fig, "06_wuhan_asset_utilization_ground_balance")


def reality_impact() -> None:
    frame = pd.read_csv(CAL / "reality_calibration_comparison.csv").set_index("variant")
    pairs = [
        ("固原\n证据校准热价", "scenario2_legacy_assumptions", "scenario2_evidence_resident_tariff", "regulated_lcoh_yuan_per_kwh"),
        ("固原\n颗粒高价压力", "scenario2_evidence_resident_tariff", "scenario2_pellet_stress_1_20", "regulated_lcoh_yuan_per_kwh"),
        ("乌鲁木齐\n可靠性热价", "scenario1_public_baseline", "scenario1_reliability_overlay", "regulated_lcoh_yuan_per_kwh"),
        ("乌鲁木齐\n可靠性配电", "scenario1_public_baseline", "scenario1_reliability_overlay", "grid_connection_capacity_kw"),
        ("武汉\n冷热联合单位服务成本", "scenario3_heat_only", "scenario3_joint_heating_cooling", "joint_service_lcoe_yuan_per_kwh"),
    ]
    impacts = np.array([
        (frame.loc[after, metric] / frame.loc[before, metric] - 1.0) * 100
        for _, before, after, metric in pairs
    ])
    labels = [row[0] for row in pairs]
    colors = [RED if value > 0 else GREEN for value in impacts]
    fig, ax = plt.subplots(figsize=(11.8, 6.3))
    fig.subplots_adjust(top=0.78)
    y = np.arange(len(labels))
    bars = ax.barh(y, impacts, color=colors, height=0.58)
    ax.axvline(0, color=INK, linewidth=1.1)
    for bar, value in zip(bars, impacts):
        ax.text(value + (1.0 if value >= 0 else -1.0), bar.get_y() + bar.get_height() / 2,
                f"{value:+.1f}%", va="center", ha="left" if value >= 0 else "right", fontweight="bold")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("相对基准变化")
    ax.set_xlim(min(impacts.min() - 8, -35), max(impacts.max() + 8, 45))
    fig.suptitle("现实约束既会增加必要成本，也会释放被低估的全年价值", x=0.25, y=0.98, ha="left", fontsize=19, fontweight="bold")
    fig.text(0.25, 0.88, "红色：原模型低估的成本/容量；绿色：全年多服务共享带来的真实降本",
             color="#475569")
    ax.grid(axis="x")
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    finish(fig, "07_three_scenario_reality_impact")


def main() -> None:
    configure()
    data_coverage()
    scenario2_cost()
    scenario2_tradeoff()
    scenario1_reliability()
    scenario3_joint_service()
    scenario3_ground_balance()
    reality_impact()
    print(f"generated 7 PNG + 7 SVG figures in {OUTPUT}")


if __name__ == "__main__":
    main()
