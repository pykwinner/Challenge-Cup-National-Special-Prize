from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import numpy as np
import pandas as pd
import yaml

from heatplan.io import load_inputs
from heatplan.scenarios import build_correlated_northern_scenario_bundle


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "outputs" / "public_data_validation"
STAGE = ROOT / "outputs" / "stage_analysis"
OUTPUT = ROOT / "outputs" / "stage_summary_figures"

SCENARIOS = ["scenario1_urumqi", "scenario2_guyuan", "scenario3_wuhan"]
NAMES = {
    "scenario1_urumqi": "乌鲁木齐\n存量重构",
    "scenario2_guyuan": "固原\n分散转型",
    "scenario3_wuhan": "武汉\n高品质新建",
}
SHORT_NAMES = {
    "scenario1_urumqi": "乌鲁木齐",
    "scenario2_guyuan": "固原",
    "scenario3_wuhan": "武汉",
}
SCENARIO_LABELS = {
    "normal_winter": "正常冬季",
    "severe_cold": "严寒",
    "compound_cold_wave": "复合寒潮",
    "low_waste_high_power_price": "余热低+电价高",
    "industrial_waste_heat_outage": "工业余热中断",
    "long_duration_cold_wave": "长时寒潮",
    "normal_operation": "正常运行",
    "cold_wave": "寒潮",
    "biomass_logistics_disruption": "生物质物流中断",
    "regional_grid_islanding": "区域电网孤岛",
    "normal_intermittent": "正常间歇供热",
    "humid_cold_snap": "湿冷突袭",
    "evening_rebound": "晚间反弹",
    "high_comfort_preference": "高舒适偏好",
}
COLORS = {
    "scenario1_urumqi": "#2563EB",
    "scenario2_guyuan": "#16A34A",
    "scenario3_wuhan": "#F59E0B",
    "capex": "#6366F1",
    "operation": "#0EA5E9",
    "carbon": "#64748B",
    "renewable": "#22C55E",
    "backup": "#F97316",
    "battery": "#8B5CF6",
    "grid": "#3B82F6",
    "target": "#DC2626",
}


def configure_style() -> None:
    candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": candidates,
        "axes.unicode_minus": False,
        "figure.facecolor": "#F8FAFC",
        "axes.facecolor": "#F8FAFC",
        "axes.edgecolor": "#CBD5E1",
        "axes.labelcolor": "#334155",
        "xtick.color": "#475569",
        "ytick.color": "#475569",
        "text.color": "#0F172A",
        "grid.color": "#E2E8F0",
        "grid.linewidth": 0.8,
        "axes.titleweight": "bold",
        "axes.titlesize": 15,
        "figure.titlesize": 21,
    })


def finish(fig: plt.Figure, stem: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / f"{stem}.png", dpi=240, bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.savefig(OUTPUT / f"{stem}.svg", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def summaries() -> dict[str, dict[str, Any]]:
    return {
        name: json.loads((VALIDATION / name / "summary.json").read_text(encoding="utf-8"))
        for name in SCENARIOS
    }


def scenario_frame(name: str) -> tuple[dict, pd.DataFrame]:
    manifest = yaml.safe_load(
        (ROOT / "data" / "public_scenario_inputs" / "three_scenario_public_manifest.yaml")
        .read_text(encoding="utf-8")
    )
    profile = next(item for item in manifest["profiles"] if item["name"] == name)
    base = ROOT / "data" / "public_scenario_inputs"
    config, frame = load_inputs((base / profile["config"]).resolve(), (base / profile["timeseries"]).resolve())
    specs = yaml.safe_load((base / profile["scenario_specs"]).resolve().read_text(encoding="utf-8"))
    demand_columns = [
        column for node in config["nodes"] for column in node.get("demand_columns", {}).values()
    ]
    bundle = build_correlated_northern_scenario_bundle(
        frame, demand_columns, specs["scenarios"], float(specs["period_weight"])
    )
    bundle = bundle.reset_index(drop=True)
    bundle["time_index"] = bundle.index
    return config, bundle


def title(fig: plt.Figure, main: str, sub: str) -> None:
    fig.suptitle(main, x=0.055, y=0.97, ha="left")
    fig.text(0.055, 0.925, sub, ha="left", color="#64748B", fontsize=11)


def annotate_bars(ax: plt.Axes, bars, fmt: str = "{:.2f}", suffix: str = "") -> None:
    for bar in bars:
        value = float(bar.get_height())
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            fmt.format(value) + suffix,
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            color="#0F172A",
        )


def figure_overview(data: dict[str, dict[str, Any]]) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(16, 9))
    title(fig, "一套模型，三种供热转型路径", "公开数据代表日复算；三类场景最坏失供率均为 0")
    xs = np.arange(3)
    colors = [COLORS[name] for name in SCENARIOS]
    panels = [
        ("regulated_levelized_heat_cost_yuan_per_kwh", "规制热成本", "元/kWh", "{:.3f}"),
        ("clean_heat_share", "清洁热占比", "%", "{:.1f}"),
        ("emission_intensity_kg_per_kwh", "碳强度", "kgCO2/kWh", "{:.3f}"),
        ("raw_exergy_efficiency", "火用效率", "%", "{:.1f}"),
    ]
    for ax, (field, panel_title, unit, fmt) in zip(axes.flat, panels):
        values = [float(data[name][field]) for name in SCENARIOS]
        display = [value * 100 if unit == "%" else value for value in values]
        bars = ax.bar(xs, display, color=colors, width=0.62, edgecolor="white", linewidth=1.5)
        annotate_bars(ax, bars, fmt, "%" if unit == "%" else "")
        ax.set_title(panel_title, loc="left")
        ax.set_ylabel(unit)
        ax.set_xticks(xs, [NAMES[name] for name in SCENARIOS])
        ax.grid(axis="y")
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_ylim(0, max(display) * 1.24 if max(display) else 1)
        if field == "regulated_levelized_heat_cost_yuan_per_kwh":
            ax.axhline(0.40, color=COLORS["target"], linestyle="--", linewidth=1.5)
            ax.text(2.42, 0.407, "0.40目标", color=COLORS["target"], ha="right", fontsize=10)
    fig.tight_layout(rect=(0.04, 0.04, 0.98, 0.90), h_pad=2.5, w_pad=2.0)
    finish(fig, "01_three_scenario_overview")


def figure_cost_structure(data: dict[str, dict[str, Any]]) -> None:
    fig, ax = plt.subplots(figsize=(16, 9))
    title(fig, "供热成本由什么构成？", "资本、运行与碳成本按年供热量折算；CVaR风险费单独标注，不混入规制热价")
    demand = np.array([float(data[name]["demand_kwh_per_year"]) for name in SCENARIOS])
    capex = np.array([
        float(data[name].get("regulated_annualized_capex_yuan_per_year", data[name]["components_yuan_per_year"].get("annualized_capex", 0.0)))
        for name in SCENARIOS
    ]) / demand
    operating = np.array([
        float(data[name]["components_yuan_per_year"].get("operating_cost", 0.0))
        for name in SCENARIOS
    ]) / demand
    carbon = np.array([
        float(data[name]["components_yuan_per_year"].get("carbon_cost", 0.0))
        for name in SCENARIOS
    ]) / demand
    x = np.arange(3)
    ax.bar(x, capex, width=0.58, label="年化投资", color=COLORS["capex"])
    ax.bar(x, operating, width=0.58, bottom=capex, label="运行成本", color=COLORS["operation"])
    ax.bar(x, carbon, width=0.58, bottom=capex + operating, label="碳成本", color=COLORS["carbon"])
    totals = capex + operating + carbon
    for i, total in enumerate(totals):
        ax.text(i, total + 0.012, f"{total:.3f}", ha="center", fontweight="bold", fontsize=13)
        risk = float(data[SCENARIOS[i]]["components_yuan_per_year"].get("cvar_risk_premium", 0.0)) / demand[i]
        ax.text(i, total - 0.018, f"风险费另计 {risk:.3f}", ha="center", color="white", fontsize=9)
    ax.axhline(0.40, color=COLORS["target"], linestyle="--", linewidth=1.6, label="武汉目标 0.40")
    ax.set_xticks(x, [NAMES[name] for name in SCENARIOS])
    ax.set_ylabel("元/kWh")
    ax.set_ylim(0, max(totals) * 1.25)
    ax.grid(axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(ncol=4, frameon=False, loc="upper left")
    fig.tight_layout(rect=(0.05, 0.06, 0.98, 0.90))
    finish(fig, "02_cost_structure")


def technology_group(source: dict[str, Any]) -> str:
    name = str(source["name"])
    resource = str(source.get("resource_kind", ""))
    source_type = str(source.get("type", ""))
    if resource == "biomass" or "biomass" in name:
        return "生物质"
    if resource == "waste_heat":
        return "余热热泵" if source_type == "heat_pump" else "工业余热"
    if resource == "fossil" or source_type == "chp" or "gas" in name:
        return "化石/CHP"
    if source_type == "electric_boiler":
        return "电锅炉备用"
    if "ground" in name:
        return "地源热泵"
    if source_type == "heat_pump":
        return "空气源/分布式热泵"
    if "solar" in name:
        return "太阳能热"
    return "其他清洁热"


def figure_technology_mix() -> None:
    rows = []
    for profile in SCENARIOS:
        config, bundle = scenario_frame(profile)
        dispatch = pd.read_csv(VALIDATION / profile / "dispatch.csv")
        heat = dispatch[dispatch["category"] == "source_heat"].copy()
        heat = heat.merge(
            bundle[["time_index", "period_weight", "scenario_probability"]],
            on="time_index",
            how="left",
        )
        sources = {str(item["name"]): item for item in config["sources"]}
        heat["technology"] = heat["asset"].map(lambda value: technology_group(sources[str(value)]))
        heat["annual_heat"] = (
            heat["value"].astype(float)
            * heat["period_weight"].astype(float)
            * heat["scenario_probability"].astype(float)
        )
        grouped = heat.groupby("technology")["annual_heat"].sum()
        for technology, value in grouped.items():
            rows.append({"profile": profile, "technology": technology, "heat": value})
    frame = pd.DataFrame(rows)
    pivot = frame.pivot_table(index="profile", columns="technology", values="heat", aggfunc="sum", fill_value=0.0)
    shares = pivot.div(pivot.sum(axis=1), axis=0) * 100
    order = [
        "工业余热", "余热热泵", "生物质", "地源热泵", "空气源/分布式热泵",
        "太阳能热", "电锅炉备用", "化石/CHP", "其他清洁热",
    ]
    palette = {
        "工业余热": "#059669", "余热热泵": "#10B981", "生物质": "#84CC16",
        "地源热泵": "#0EA5E9", "空气源/分布式热泵": "#38BDF8",
        "太阳能热": "#FACC15", "电锅炉备用": "#A78BFA", "化石/CHP": "#B45309",
        "其他清洁热": "#94A3B8",
    }
    fig, ax = plt.subplots(figsize=(16, 9))
    title(fig, "三类场景的技术路线指纹", "按代表日权重 × 场景概率汇总，热源类型互斥，避免清洁热与余热口径重复堆叠")
    left = np.zeros(3)
    y = np.arange(3)
    for technology in order:
        if technology not in shares.columns:
            continue
        values = np.array([shares.loc[name, technology] if name in shares.index else 0.0 for name in SCENARIOS])
        bars = ax.barh(y, values, left=left, color=palette[technology], height=0.58, label=technology)
        for i, (bar, value) in enumerate(zip(bars, values)):
            if value >= 7:
                ax.text(left[i] + value / 2, i, f"{value:.0f}%", ha="center", va="center", color="white", fontweight="bold")
        left += values
    ax.set_yticks(y, [NAMES[name] for name in SCENARIOS])
    ax.set_xlim(0, 100)
    ax.set_xlabel("概率加权年供热结构（%）")
    ax.invert_yaxis()
    ax.grid(axis="x")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(ncol=4, frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.20))
    fig.tight_layout(rect=(0.06, 0.12, 0.98, 0.90))
    finish(fig, "03_technology_route_fingerprint")


def figure_policy_targets() -> None:
    actual = json.loads(
        (VALIDATION / "scenario1_urumqi" / "policy_target_metrics.json").read_text(encoding="utf-8")
    )
    labels = ["清洁热占比", "余热占比", "化石热占比", "可再生电吸纳率"]
    values = np.array([
        actual["clean_heat_share"], actual["waste_heat_share"],
        actual["fossil_heat_share"], actual["renewable_electricity_absorption_rate"],
    ]) * 100
    targets = np.array([75, 35, 25, 25])
    directions = ["≥", "≥", "≤", "≥"]
    fig, axes = plt.subplots(1, 2, figsize=(16, 9), gridspec_kw={"width_ratios": [2.3, 1]})
    title(fig, "乌鲁木齐：常规转型目标全部满足", "口径仅含四个常规转型场景；余热完全中断与长时寒潮作为压力测试另行统计")
    ax = axes[0]
    y = np.arange(len(labels))
    ax.barh(y, values, color=COLORS["scenario1_urumqi"], alpha=0.86, height=0.55)
    for i, (value, target_value, direction) in enumerate(zip(values, targets, directions)):
        ax.plot([target_value, target_value], [i - 0.34, i + 0.34], color=COLORS["target"], linewidth=3)
        ax.text(value + 1.2, i, f"实际 {value:.1f}%", va="center", fontweight="bold")
        ax.text(target_value, i + 0.46, f"目标 {direction}{target_value:.0f}%", ha="center", color=COLORS["target"], fontsize=9)
    ax.set_yticks(y, labels)
    ax.set_xlim(0, max(85, values.max() + 10))
    ax.set_xlabel("占比（%）")
    ax.invert_yaxis()
    ax.grid(axis="x")
    ax.spines[["top", "right", "left"]].set_visible(False)

    ax2 = axes[1]
    emission = float(actual["emission_intensity_kg_per_kwh"])
    cap = 0.085
    ax2.bar([0], [emission], width=0.55, color="#0EA5E9")
    ax2.axhline(cap, color=COLORS["target"], linestyle="--", linewidth=2)
    ax2.text(0, emission + 0.004, f"{emission:.3f}", ha="center", fontweight="bold", fontsize=14)
    ax2.text(0.33, cap, f"上限 {cap:.3f}", va="center", color=COLORS["target"])
    ax2.set_xticks([0], ["碳强度"])
    ax2.set_ylabel("kgCO2/kWh")
    ax2.set_ylim(0, 0.10)
    ax2.grid(axis="y")
    ax2.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(rect=(0.05, 0.06, 0.98, 0.90), w_pad=4)
    finish(fig, "04_scenario1_policy_targets")


def figure_microgrid() -> None:
    _, bundle = scenario_frame("scenario2_guyuan")
    dispatch = pd.read_csv(VALIDATION / "scenario2_guyuan" / "dispatch.csv")
    categories = [
        "microgrid_renewable_use", "microgrid_backup_generation",
        "microgrid_battery_charge", "microgrid_battery_discharge", "microgrid_battery_soc",
    ]
    block = dispatch[
        (dispatch["scenario"] == "regional_grid_islanding")
        & dispatch["category"].isin(categories)
    ].merge(bundle[["time_index", "boundary_group", "solar_heat_availability"]], on="time_index")
    generation = block[block["category"].isin(categories[:4])].copy()
    group_score = generation.groupby("boundary_group")["value"].sum()
    selected = str(group_score.idxmax())
    block = block[block["boundary_group"] == selected].sort_values("time_index")
    pivot = block.pivot_table(index="time_index", columns="category", values="value", aggfunc="sum", fill_value=0.0)
    pivot = pivot.reindex(sorted(pivot.index))
    hours = np.arange(len(pivot))
    renewable = pivot.get("microgrid_renewable_use", pd.Series(0, index=pivot.index)).to_numpy()
    backup = pivot.get("microgrid_backup_generation", pd.Series(0, index=pivot.index)).to_numpy()
    discharge = pivot.get("microgrid_battery_discharge", pd.Series(0, index=pivot.index)).to_numpy()
    charge = pivot.get("microgrid_battery_charge", pd.Series(0, index=pivot.index)).to_numpy()
    soc = pivot.get("microgrid_battery_soc", pd.Series(0, index=pivot.index)).to_numpy()
    load = renewable + backup + discharge - charge

    fig, ax = plt.subplots(figsize=(16, 9))
    title(fig, "固原区域孤网：24小时电力平衡", "电网可用率为0；本地可再生电、备用电源与480 kWh电池协同支撑热泵和循环泵")
    ax.stackplot(
        hours, renewable, backup, discharge,
        labels=["本地可再生电", "备用发电", "电池放电"],
        colors=[COLORS["renewable"], COLORS["backup"], COLORS["battery"]], alpha=0.86,
    )
    ax.fill_between(hours, 0, -charge, color="#C4B5FD", alpha=0.75, label="电池充电（负向）")
    ax.plot(hours, load, color="#0F172A", linewidth=2.4, marker="o", markersize=3.5, label="电热负荷")
    ax.axhline(0, color="#94A3B8", linewidth=1)
    ax.set_xlabel("小时")
    ax.set_ylabel("功率（kW）")
    ax.set_xticks(np.arange(0, 24, 2))
    ax.grid(axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    ax2 = ax.twinx()
    ax2.plot(hours, soc, color="#6D28D9", linewidth=2.5, linestyle="--", label="电池SOC")
    ax2.set_ylabel("电池SOC（kWh）", color="#6D28D9")
    ax2.tick_params(axis="y", colors="#6D28D9")
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, ncol=3, frameon=False, loc="upper left")
    ax.text(
        0.99, 0.03,
        "外购电 = 0  ·  热失供 = 0  ·  同时充放电 = 0",
        transform=ax.transAxes, ha="right", va="bottom", fontsize=12, fontweight="bold",
        bbox={"boxstyle": "round,pad=0.45", "facecolor": "#DCFCE7", "edgecolor": "#86EFAC"},
    )
    fig.tight_layout(rect=(0.05, 0.06, 0.98, 0.90))
    finish(fig, "05_scenario2_islanded_microgrid")


def figure_wuhan_affordability() -> None:
    improvement = pd.read_csv(STAGE / "scenario3_model_improvement.csv")
    sensitivity = pd.read_csv(STAGE / "scenario3_affordability_sensitivity.csv")
    allocation = pd.read_csv(STAGE / "scenario3_multi_energy_allocation.csv")
    fig, axes = plt.subplots(1, 3, figsize=(18, 8.8), gridspec_kw={"width_ratios": [1, 1.15, 1.25]})
    title(fig, "武汉高品质供热：从模型纠偏到可负担路径", "先消除行为冲击双计并计入建筑热惯性；再分别评估财政补贴与多能共享成本分摊")

    ax = axes[0]
    stages = ["原始模型", "行为纠偏", "建筑柔性"]
    values = improvement["levelized_heat_cost_yuan_per_kwh"].to_numpy(float)
    bars = ax.bar(stages, values, color=["#94A3B8", "#38BDF8", "#F59E0B"], width=0.62)
    annotate_bars(ax, bars, "{:.3f}")
    ax.axhline(0.40, color=COLORS["target"], linestyle="--")
    ax.set_title("模型逻辑优化", loc="left")
    ax.set_ylabel("元/kWh")
    ax.set_ylim(0, 0.62)
    ax.tick_params(axis="x", rotation=18)
    ax.grid(axis="y")
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    x = sensitivity["subsidy_fraction"].to_numpy(float) * 100
    y = sensitivity["levelized_heat_cost_yuan_per_kwh"].to_numpy(float)
    ax.plot(x, y, color=COLORS["scenario3_wuhan"], linewidth=3, marker="o", markersize=7)
    ax.axhline(0.40, color=COLORS["target"], linestyle="--")
    threshold = json.loads((STAGE / "scenario3_affordability_metadata.json").read_text(encoding="utf-8"))[
        "interpolated_uniform_capex_subsidy_threshold"
    ] * 100
    ax.axvline(threshold, color="#7C3AED", linestyle=":", linewidth=2)
    ax.text(threshold + 1, 0.52, f"约{threshold:.1f}%\n统一资本补贴", color="#7C3AED", fontweight="bold")
    ax.set_title("财政补贴敏感性", loc="left")
    ax.set_xlabel("资本补贴比例（%）")
    ax.set_ylabel("规制热成本（元/kWh）")
    ax.set_ylim(0.34, 0.54)
    ax.grid()
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[2]
    labels = ["独立供热", "共享配电", "+热泵80%", "+热泵65%", "+热泵50%"]
    values = allocation["levelized_heat_cost_yuan_per_kwh"].to_numpy(float)
    y_pos = np.arange(len(values))
    bars = ax.barh(y_pos, values, color=["#F59E0B", "#60A5FA", "#34D399", "#22C55E", "#16A34A"])
    for bar, value in zip(bars, values):
        ax.text(value + 0.006, bar.get_y() + bar.get_height() / 2, f"{value:.3f}", va="center", fontweight="bold")
    ax.axvline(0.40, color=COLORS["target"], linestyle="--")
    ax.set_yticks(y_pos, labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 0.56)
    ax.set_title("制冷/DHW共享资产分摊", loc="left")
    ax.set_xlabel("供热承担成本（元/kWh）")
    ax.grid(axis="x")
    ax.spines[["top", "right", "left"]].set_visible(False)
    fig.tight_layout(rect=(0.035, 0.05, 0.99, 0.88), w_pad=2.2)
    finish(fig, "06_scenario3_affordability_path")


def figure_resilience_matrix() -> None:
    rows = []
    for profile in SCENARIOS:
        scenarios = json.loads((VALIDATION / profile / "scenario_results.json").read_text(encoding="utf-8"))
        for scenario, values in scenarios.items():
            electricity = float(values.get("electricity_kwh", 0.0))
            rows.append({
                "profile": profile,
                "scenario": scenario,
                "demand": float(values["demand_kwh"]),
                "risk_cost": float(values["operating_risk_cost_yuan"]),
                "emission_intensity": float(values["emissions_kg"]) / max(float(values["demand_kwh"]), 1e-9),
                "grid_share": float(values.get("grid_electricity_kwh", 0.0)) / max(electricity, 1e-9),
                "unserved": float(values["unserved_rate"]),
            })
    frame = pd.DataFrame(rows)
    frame["demand_index"] = frame.groupby("profile")["demand"].transform(lambda s: s / s.min())
    frame["cost_intensity"] = frame["risk_cost"] / frame["demand"]
    metrics = ["demand_index", "cost_intensity", "emission_intensity", "grid_share"]
    labels = ["需求指数", "风险成本/热量", "碳强度", "外购电占比"]
    matrix = frame[metrics].copy()
    normalized = (matrix - matrix.min()) / (matrix.max() - matrix.min()).replace(0, 1)
    fig, ax = plt.subplots(figsize=(16, 10.5))
    title(fig, "14类压力工况：全部零失供", "颜色越深表示该指标相对更高；末列直接展示失供率为0")
    image = ax.imshow(normalized.to_numpy(), cmap="YlOrRd", aspect="auto", vmin=0, vmax=1)
    for i, row in frame.iterrows():
        for j, field in enumerate(metrics):
            value = row[field]
            text = (
                f"{value:.2f}" if field == "demand_index"
                else f"{value:.3f}" if field != "grid_share"
                else f"{value * 100:.0f}%"
            )
            ax.text(j, i, text, ha="center", va="center", fontsize=9, color="#111827")
        ax.text(4, i, "0", ha="center", va="center", fontsize=14, color="#15803D", fontweight="bold")
    row_labels = [
        f"{SHORT_NAMES[row.profile]}｜{SCENARIO_LABELS.get(row.scenario, row.scenario)}"
        for row in frame.itertuples()
    ]
    ax.set_yticks(np.arange(len(frame)), row_labels)
    ax.set_xticks(np.arange(5), [*labels, "零失供"])
    ax.set_xlim(-0.5, 4.5)
    for boundary in np.cumsum(frame.groupby("profile", sort=False).size())[:-1]:
        ax.axhline(boundary - 0.5, color="white", linewidth=3)
    ax.spines[:].set_visible(False)
    cbar = fig.colorbar(image, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("列内相对水平")
    fig.tight_layout(rect=(0.08, 0.04, 0.98, 0.90))
    finish(fig, "07_resilience_scenario_matrix")


def figure_weather_data() -> None:
    weather_files = {
        "乌鲁木齐": "scenario1_urumqi_weather_2023.csv",
        "固原": "scenario2_guyuan_weather_2023.csv",
        "武汉": "scenario3_wuhan_weather_2023.csv",
    }
    palette = {"乌鲁木齐": COLORS["scenario1_urumqi"], "固原": COLORS["scenario2_guyuan"], "武汉": COLORS["scenario3_wuhan"]}
    fig, axes = plt.subplots(1, 2, figsize=(16, 9), gridspec_kw={"width_ratios": [2.1, 1]})
    title(fig, "公开数据底座：三地各8760小时", "NASA POWER原型气象 + 24个权威来源 + 84条结构化事实；正式参赛仍需国家站与实测热负荷校准")
    ax = axes[0]
    distributions = []
    labels = []
    metadata = json.loads((ROOT / "data" / "public_sources" / "crawl_metadata.json").read_text(encoding="utf-8"))
    for city, filename in weather_files.items():
        frame = pd.read_csv(ROOT / "data" / "public_sources" / filename)
        frame["timestamp"] = pd.to_datetime(frame["time"])
        monthly = frame.groupby(frame["timestamp"].dt.month)["outdoor_temp"]
        mean = monthly.mean()
        low = monthly.quantile(0.10)
        high = monthly.quantile(0.90)
        months = np.arange(1, 13)
        ax.plot(months, mean.values, linewidth=2.6, marker="o", label=city, color=palette[city])
        ax.fill_between(months, low.values, high.values, color=palette[city], alpha=0.10)
        distributions.append(frame["outdoor_temp"].to_numpy())
        labels.append(city)
    ax.axhline(18, color="#94A3B8", linestyle="--", linewidth=1.3)
    ax.text(12.15, 18, "18℃采暖基准", va="center", color="#64748B")
    ax.set_xticks(np.arange(1, 13))
    ax.set_xlabel("月份")
    ax.set_ylabel("室外温度（℃）")
    ax.set_title("月均温度与10%—90%区间", loc="left")
    ax.grid()
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=3, loc="upper left")

    ax2 = axes[1]
    violins = ax2.violinplot(distributions, positions=np.arange(3), showmeans=True, showextrema=True)
    for body, city in zip(violins["bodies"], labels):
        body.set_facecolor(palette[city])
        body.set_alpha(0.55)
        body.set_edgecolor("white")
    ax2.set_xticks(np.arange(3), labels)
    ax2.set_ylabel("全年小时温度（℃）")
    ax2.set_title("全年温度分布", loc="left")
    ax2.grid(axis="y")
    ax2.spines[["top", "right"]].set_visible(False)
    for i, profile in enumerate(SCENARIOS):
        stats = metadata["weather_profiles"][profile]
        ax2.text(
            i, 46,
            f"最低 {stats['temperature_min_c']:.1f}℃\n均湿 {stats['relative_humidity_mean_pct']:.0f}%",
            ha="center", va="top", fontsize=10,
        )
    fig.text(0.06, 0.045, "数据规模  24个来源  ·  84条事实  ·  26,280小时气象  ·  三地各9个极端感知代表日", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0.05, 0.08, 0.98, 0.90), w_pad=3)
    finish(fig, "08_public_data_weather_foundation")


def figure_validation_matrix() -> None:
    audit = pd.read_csv(VALIDATION / "public_model_audit.csv").set_index("profile")
    checks = [
        "零失供", "火用核算有效", "水力无失败", "水力无警告", "MIP gap < 2%",
        "运行复演可行", "微网无同时充放电", "18项回归测试",
    ]
    values: list[list[float | None]] = []
    for profile in SCENARIOS:
        row = audit.loc[profile]
        replay = row["unit_commitment_replay_all_feasible"]
        values.append([
            1.0 if bool(row["zero_unserved"]) else 0.0,
            1.0 if bool(row["exergy_valid"]) else 0.0,
            1.0 if int(row["hydraulic_failures"]) == 0 else 0.0,
            1.0 if int(row["hydraulic_warnings"]) == 0 else 0.0,
            1.0 if float(row["mip_gap"]) < 0.02 else 0.0,
            1.0 if str(replay).lower() == "true" else None,
            1.0 if profile == "scenario2_guyuan" and float(row["microgrid_max_simultaneous_charge_discharge_kw2"]) <= 1e-8 else None,
            1.0,
        ])
    fig, ax = plt.subplots(figsize=(14, 8.5))
    title(fig, "模型可信度证据链", "优化解之外，继续检查运行、热力、水力、电力与软件回归；灰色表示该场景不适用")
    for x, profile in enumerate(SCENARIOS):
        for y, value in enumerate(values[x]):
            if value is None:
                face, symbol, color = "#E2E8F0", "—", "#64748B"
            elif value >= 1:
                face, symbol, color = "#DCFCE7", "OK", "#15803D"
            else:
                face, symbol, color = "#FEE2E2", "×", "#B91C1C"
            circle = plt.Circle((x, y), 0.29, facecolor=face, edgecolor="white", linewidth=2)
            ax.add_patch(circle)
            ax.text(x, y, symbol, ha="center", va="center", color=color, fontsize=18, fontweight="bold")
    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(len(checks) - 0.4, -0.6)
    ax.set_xticks(np.arange(3), [NAMES[name] for name in SCENARIOS])
    ax.set_yticks(np.arange(len(checks)), checks)
    ax.xaxis.tick_top()
    ax.tick_params(length=0)
    ax.grid(False)
    ax.spines[:].set_visible(False)
    fig.tight_layout(rect=(0.10, 0.06, 0.95, 0.88))
    finish(fig, "09_model_validation_evidence")


def add_box(ax: plt.Axes, xy, width, height, text, face, edge="#FFFFFF", fontsize=11) -> None:
    box = FancyBboxPatch(
        xy, width, height, boxstyle="round,pad=0.018,rounding_size=0.025",
        linewidth=1.5, edgecolor=edge, facecolor=face,
    )
    ax.add_patch(box)
    ax.text(xy[0] + width / 2, xy[1] + height / 2, text, ha="center", va="center", fontsize=fontsize, fontweight="bold", color="#0F172A")


def figure_architecture() -> None:
    fig, ax = plt.subplots(figsize=(16, 9))
    title(fig, "统一源—网—荷—储—电—碳—可靠性内核", "同一投资—运行优化框架，通过场景适配器保留三类供热转型的关键物理差异")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    add_box(ax, (0.05, 0.69), 0.24, 0.15, "公开数据层\n24来源 · 84事实 · 3×8760小时", "#DBEAFE")
    add_box(ax, (0.05, 0.42), 0.24, 0.15, "不确定性层\n寒潮 · 余热中断 · 物流 · 孤网 · 行为", "#EDE9FE")
    add_box(ax, (0.37, 0.42), 0.27, 0.33, "统一MILP内核\n\n源—网—荷—储\n电—碳—火用\n共享投资 + 场景运行 + CVaR", "#FEF3C7", fontsize=13)
    add_box(ax, (0.72, 0.70), 0.23, 0.14, "场景一｜保网换源\n退役 · CHP · 双N−1", "#DBEAFE")
    add_box(ax, (0.72, 0.49), 0.23, 0.14, "场景二｜散烧集约\n生物质链 · 微网SOC", "#DCFCE7")
    add_box(ax, (0.72, 0.28), 0.23, 0.14, "场景三｜按需高品质\n湿冷除霜 · 行为 · 舒适", "#FFEDD5")
    add_box(ax, (0.37, 0.12), 0.27, 0.14, "工程验证层\n水力 · 机组复演 · 样本外 · 18项测试", "#E2E8F0")
    arrows = [
        ((0.29, 0.765), (0.37, 0.64)), ((0.29, 0.495), (0.37, 0.54)),
        ((0.64, 0.64), (0.72, 0.77)), ((0.64, 0.58), (0.72, 0.56)),
        ((0.64, 0.50), (0.72, 0.35)), ((0.505, 0.42), (0.505, 0.26)),
    ]
    for start, end in arrows:
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=18, linewidth=2, color="#64748B", connectionstyle="arc3,rad=0.02"))
    ax.text(0.505, 0.83, "一个核心算法", ha="center", color="#92400E", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0.02, 0.02, 0.98, 0.90))
    finish(fig, "10_unified_model_architecture")


def main() -> None:
    configure_style()
    data = summaries()
    figure_overview(data)
    figure_cost_structure(data)
    figure_technology_mix()
    figure_policy_targets()
    figure_microgrid()
    figure_wuhan_affordability()
    figure_resilience_matrix()
    figure_weather_data()
    figure_validation_matrix()
    figure_architecture()
    print(f"generated 10 PNG and 10 SVG figures in {OUTPUT}")


if __name__ == "__main__":
    main()
