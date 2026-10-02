"""Plot frozen implementation evidence; never generate or modify observations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/implementation_20261002"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tou", type=Path)
    args = parser.parse_args()
    out = BASE / "figures"
    out.mkdir(exist_ok=True)
    inputs = []

    def read(path):
        path = path.resolve()
        inputs.append(path)
        return json.loads(path.read_text(encoding="utf-8"))

    plt.rcParams.update({"font.family": "Microsoft YaHei", "axes.unicode_minus": False,
                         "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    records = read(BASE / "algorithm_hard_weights/results.json")
    cases = list(dict.fromkeys(r["case_id"] for r in records))
    lookup = {(r["case_id"], r["method"]): r for r in records}
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    x = np.arange(len(cases))
    for method, label, color in (("extreme_aware", "未校准极端日", "#9CA3AF"),
                                  ("extreme_calibrated", "原软权重", "#D78B32"),
                                  ("hard_energy_minimum_W1", "硬能量最小 W1", "#176A8A")):
        axes[0].plot(x, [100*lookup[c, method]["metrics"]["ldc_w1_over_mean_load"] for c in cases],
                     marker="o", label=label, color=color)
    lost = [100*(1-lookup[c, "extreme_aware"]["metrics"]["retained_scope_energy_kwh"] /
                 lookup[c, "extreme_aware"]["quality"]["sum_observed_values"]) for c in cases]
    adopted = [lookup[c, "adoption_hard"]["adoption"]["statistical_candidate_adopted"] for c in cases]
    axes[1].bar(x, lost, color=["#176A8A" if v else "#C75B39" for v in adopted])
    for i, value in enumerate(lost):
        axes[1].text(i, value+.5, f"{value:.2f}%", ha="center", fontsize=9)
    for ax in axes:
        ax.set_xticks(x, ["热水1", "热水2", "蒸汽1", "蒸汽2", "冷水1", "冷水2"])
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("W1 / 保留观测均值（%）")
    axes[0].set_title("同一组真实代表日，6 栋预冻结新建筑")
    axes[0].legend(frameon=False)
    axes[1].set_title("完整日筛选退出的原有效观测能量")
    axes[1].set_ylabel("能量退出比例（%）")
    axes[1].set_ylim(0, max(lost)*1.22)
    fig.suptitle("BDG2 统计压缩确认：保留改善，也保留缺失与拒绝\n橙红柱为原始峰值门槛未通过；蓝柱仅通过统计门槛")
    fig.savefig(out / "weight_confirmation.png", dpi=170)
    fig.savefig(out / "weight_confirmation.svg")
    plt.close(fig)

    network = [read(BASE / "spatial" / city / "candidate_network.json")
               for city in ("guyuan", "urumqi", "wuhan")]
    unique = [sum(e["route_length_m"] for e in n["candidate_edges"]) for n in network]
    path_total = [sum(p["route_length_m"] for p in n["paths"]) for n in network]
    fig, ax = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
    ax.bar(np.arange(3)-.18, unique, width=.36, color="#176A8A", label="唯一共享候选段并集")
    ax.bar(np.arange(3)+.18, path_total, width=.36, color="#9CA3AF", label="逐建筑路线直接相加")
    ax.set_xticks(range(3), ["固原（2成功/3失败）", "乌鲁木齐（5成功）", "武汉（5成功）"])
    ax.set_ylabel("参考几何长度（m）")
    ax.legend(frameon=False)
    ax.set_title("三城固定样本：共享段只登记一次\n含质心到道路参考连接；几何重复引用差异不是工程投资节省")
    fig.savefig(out / "shared_corridors.png", dpi=170)
    fig.savefig(out / "shared_corridors.svg")
    plt.close(fig)

    if args.tou:
        endpoints = read(args.tou / "endpoints.json")
        samples = read(args.tou / "all_epsilon_samples.json")
        fig, ax = plt.subplots(figsize=(8, 4.8), constrained_layout=True)
        ax.plot([s["total_service_emissions_kg_per_year"] for s in samples],
                [s["objective_yuan_per_year"] for s in samples], "o-", color="#176A8A", label="5 个预登记 ε 位置")
        for name, label, color in (("cost_endpoint", "费用优先端点", "#D78B32"),
                                    ("emission_endpoint", "排放优先端点", "#7C5D9B")):
            s = endpoints[name]
            ax.scatter(s["total_service_emissions_kg_per_year"], s["objective_yuan_per_year"], s=100,
                       marker="D", facecolors="none", edgecolors=color, linewidths=2, label=label)
        ax.set_xlabel("完整供暖季参考 CO2（kg；省级年度平均因子）")
        ax.set_ylabel("完整供暖季电量费用（元）")
        ax.set_title("公开原型 + 条件性湖北工商业分时账户\n同设备、同舒适范围；参考运行费用，未含工程投资")
        ax.legend(frameon=False)
        ax.grid(alpha=.2)
        fig.savefig(out / "tou_cost_carbon.png", dpi=170)
        fig.savefig(out / "tou_cost_carbon.svg")
        plt.close(fig)

        # A declared display selection, not an additional validation sample:
        # show seven days around the cost-endpoint's maximum heating output.
        trajectories = {}
        for name in ("cost_endpoint", "emission_endpoint"):
            path = (args.tou / name / "building_trajectory.csv").resolve()
            inputs.append(path)
            trajectories[name] = pd.read_csv(path).sort_values("time_index").reset_index(drop=True)
        first = min(max(int(trajectories["cost_endpoint"].heat_kw.idxmax())-72, 0),
                    len(trajectories["cost_endpoint"])-168)
        fig, axes = plt.subplots(2, 1, sharex=True, figsize=(11, 6), constrained_layout=True)
        for name, label, color in (("cost_endpoint", "费用优先", "#176A8A"),
                                   ("emission_endpoint", "排放优先", "#D78B32")):
            data = trajectories[name].iloc[first:first+168]
            start_times = pd.to_datetime(data.time, utc=True).dt.tz_convert("Asia/Shanghai")
            axes[0].plot(start_times, data.heat_kw, label=label, color=color, linewidth=1.4)
            axes[1].plot(start_times+pd.Timedelta(hours=1), data.temperature_end_c,
                         label=label, color=color, linewidth=1.4)
        axes[0].set_ylabel("小时平均供热功率（kW）")
        axes[1].set_ylabel("步末室温（℃）")
        axes[1].axhline(20, color="#666666", linestyle="--", linewidth=.8)
        axes[1].axhline(27, color="#666666", linestyle="--", linewidth=.8)
        axes[1].set_xlabel("北京时间；供热按小时起点、温度按小时末点绘制")
        for ax in axes:
            ax.grid(alpha=.2)
            ax.legend(frameon=False, loc="upper right")
        axes[0].set_title("相同20—27℃服务范围下的不同调度\n展示费用优先最大供热时刻附近7天；窗口选择仅用于显示")
        fig.savefig(out / "tou_operation_week.png", dpi=170)
        fig.savefig(out / "tou_operation_week.svg")
        plt.close(fig)

    comparison_path = BASE / "frontier_comparison/point_ledgers.json"
    if comparison_path.exists():
        points = read(comparison_path)
        epsilon = [r for r in points if r["method"] == "epsilon" and r["passed"]]
        weighted = [r for r in points if r["method"] != "epsilon" and r["passed"]]
        fig, ax = plt.subplots(figsize=(9, 5.2), constrained_layout=True)
        for rows, label, color, marker in ((epsilon, "ε扫描：5个位置", "#176A8A", "s"),
                                          (weighted, "加权：共享端点+3个新内点", "#D78B32", "o")):
            ax.scatter([r["average_factor_co2_kg"] for r in rows],
                       [r["electricity_energy_bill_yuan"] for r in rows],
                       s=65, c=color, marker=marker, label=label)
        clusters = []
        for row in sorted(weighted, key=lambda r: r["average_factor_co2_kg"]):
            if clusters and abs(row["average_factor_co2_kg"]-clusters[-1][0]["average_factor_co2_kg"]) < 1e-4:
                clusters[-1].append(row)
            else:
                clusters.append([row])
        for group in clusters:
            row = group[0]
            label = "λ=" + "/".join(f"{r['lambda']:g}" for r in sorted(group,key=lambda r:r['lambda']))
            offset = (7, -18) if len(group)>1 else (-30, 10) if row["lambda"]==0 else (5, 10)
            ax.annotate(label, (row["average_factor_co2_kg"], row["electricity_energy_bill_yuan"]),
                        xytext=offset, textcoords="offset points", fontsize=10)
        ax.set_xlabel("完整季参考 CO2（kg）")
        ax.set_ylabel("独立重算电量费用（元）")
        ax.set_title("ε与归一加权的采样覆盖\n同一公开原型及分时账户；研究权重不当作市场碳价")
        ax.legend(frameon=False, loc="upper right")
        ax.grid(alpha=.2)
        ax.margins(.07)
        fig.savefig(out / "frontier_method_comparison.png", dpi=170)
        fig.savefig(out / "frontier_method_comparison.svg")
        plt.close(fig)

    manifest = {"scope": "plots of recorded numerical/geometry evidence; no new measurement",
                "inputs": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "outputs": [p.name for p in out.glob("*") if p.suffix in {".png", ".svg"}]}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"plots": manifest["outputs"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
