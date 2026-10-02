"""Export the completed development experiment after an independent audit."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd


def fp(path):
    path = Path(path).resolve()
    name = str(path)
    if os.name == "nt" and not name.startswith("\\\\?\\"):
        return Path("\\\\?\\UNC\\"+name[2:] if name.startswith("\\\\") else "\\\\?\\"+name)
    return path


def read(path):
    return json.loads(fp(path).read_text(encoding="utf-8"))


def save(path, value):
    fp(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def report(run, out, audit):
    run, out = fp(run), fp(out)
    owned = fp(Path(__file__).resolve().parents[1]/"docs/implementation_20261002_followup/parallel_stage_03/algorithm")
    if not out.is_relative_to(owned) or out == owned:
        raise ValueError("report must use a new owned algorithm subdirectory")
    if out.exists():
        raise ValueError("report output exists; preserve evidence")
    if not read(audit)["passed"]:
        raise ValueError("independent audit must pass before claims export")
    for name, digest in read(run/"manifest.json")["files"].items():
        if hashlib.sha256((run/name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"run artifact changed after audit: {name}")
    protocol, summary = read(run/"protocol.json"), read(run/"summary.json")
    predictions, choices, ranks, runtime = read(run/"predictions.json"), read(run/"selections.json"), read(run/"rankings.json"), read(run/"runtime.json")
    methods, cities = protocol["methods"], protocol["cities"]
    out.mkdir(parents=True)
    for name, rows in [("method_summary", summary["methods"]), ("policy_predictions", predictions),
                       ("budget_selections", choices), ("rank_summary", ranks)]:
        pd.DataFrame(rows).to_csv(out/f"{name}.csv", index=False)
    os.environ["MPLCONFIGDIR"] = str(out/"plot_cache")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["#64748b", "#0f766e", "#2563eb", "#a16207", "#be123c", "#7c3aed"]
    labels = ["Frequency", "Soft energy", "Hard energy + W1", "Hard energy + L1", "Price/windows + L1", "W1 + candidate L1 cap"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    x, width = np.arange(len(cities)), 0.12
    for i, method in enumerate(methods):
        errors = [100*np.mean([r["absolute_percentage_error_bill_yuan"] for r in predictions if r["city"] == city and r["method_id"] == method])
                  if any(r["city"] == city and r["method_id"] == method for r in predictions) else np.nan for city in cities]
        axes[0].bar(x+(i-2.5)*width, errors, width, label=labels[i], color=colors[i])
    axes[0].set(xticks=x, xticklabels=[c.title() for c in cities], ylabel="Bill MAPE (%) across all nine policies",
                title="Estimation accuracy: seen development cities")
    qualified = [sum(r["qualified"] for r in choices if r["method_id"] == method) for method in methods]
    unsafe = [sum(r["selection_status"] == "selected_actual_infeasible" for r in choices if r["method_id"] == method) for method in methods]
    missing = [12-qualified[i]-unsafe[i] for i in range(len(methods))]
    x = np.arange(len(methods))
    axes[1].bar(x, qualified, color="#0f766e", label="Actual feasible + service pass")
    axes[1].bar(x, unsafe, bottom=qualified, color="#be123c", label="Selected actual cap/service failure")
    axes[1].bar(x, missing, bottom=np.array(qualified)+unsafe, color="#cbd5e1", label="No choice / calibration or library failure")
    axes[1].set(xticks=x, xticklabels=["Freq", "Soft E", "Hard W1", "Hard L1", "Price", "W1 cap"], yticks=[0, 4, 8, 12],
                ylim=(0, 12.8), ylabel="Choices across 3 cities x 4 budgets", title="Decision coverage: actual caps gate valid regret")
    axes[1].legend(fontsize=8, loc="upper left", bbox_to_anchor=(0, -0.17))
    axes[0].legend(fontsize=8, ncol=2, loc="upper left", bbox_to_anchor=(0, -0.17))
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.18)
        ax.set_axisbelow(True)
    fig.savefig(out/"accuracy_and_actual_feasibility.png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    lines = ["三城价格/时窗耦合权重：有限策略开发确认实验", "",
             f"冻结协议：{protocol['frozen_utc']}；协议SHA256见run manifest。",
             "范围：乌鲁木齐/固原/武汉，已见开发数据；同一BESTEST派生RC原型、20–27℃服务、20℃初末状态。",
             "共同湖北条件电价作方法参考；不是三城当地账单。理想效率1、共享容量1000kW；无投资及完整财务。",
             "九条策略=零预热+四个固定谷时窗×1/3℃。三城全部连续2880h模拟后才按真实日期切片评分。",
             "六种权重相同代表日/极端下界；候选硬守恒参考热量、价格矩±0.5%、05/13时窗热量±1%，最小L1。",
             "minL1能量控制与候选位移上限W1控制只隔离部分因素，不能把联合约束效果单独归因于价格。",
             "校准仅用外生代理/价格/掩码，先完成权重保存，再产生所有新策略真值；软基线为soft_energy而非soft_W1。",
             "预算为真实零预热电量×1/1.02/1.05/1.10；固定排放因子使碳预算与电量预算重复。",
             "遗憾仅对实际满足服务/预算的选择计算；oracle也仅限相同九策略库，不是全局运行最优。", "",
             "方法 | 费用MAPE% | 电量最大APE% | 合格选择/12 | 选到实际超限 | 最坏有效相对遗憾% | 排名逆序对"]
    for row in summary["methods"]:
        def percent(key):
            return "NA" if row[key] is None else f"{100*row[key]:.6f}"
        lines.append(f"{row['method_id']} | {percent('bill_MAPE')} | {percent('energy_max_APE')} | {row['qualified_choices']}/12 | {row['actual_infeasible_choices']} | {percent('worst_relative_regret')} | {row['strict_rank_inversions']}")
    lines += ["", f"按冻结的覆盖优先规则，本开发库最稳推荐：{summary['development_recommendation']}。",
              "这是一项有限开发诊断，不能推出新留出泛化、真实工程投资改进或仿真加速；保留失败/无选择/不改善。",
              f"实际仿真：{runtime['full_policy_count']}条连续轨迹，{runtime['full_simulated_hours']}小时；共享仿真与评分耗时{runtime['shared_policy_simulation_and_scoring_wall_seconds']:.3f}秒。",
              f"共同输入核验/代表日选择/快照另计{runtime['shared_input_check_selection_snapshot_wall_seconds']:.3f}秒；不把离线真值工作省略。",
              "所有方法计入共享真值成本；位移上限W1还计入候选依赖成本，进程启动/独立输入校验计入校准耗时。",
              "独立复核重算时钟、RC卷积、初末/舒适度/容量/排他、逐时/日/总账、矩/权重、全部预测/排名/选策/遗憾。",
              "完整原始轨迹、权重、候选表、失败记录、协议及各文件哈希保留；原始来源沿用既有审核，没有重验全部原件。"]
    (out/"claims.txt").write_text("\n".join(lines)+"\n", encoding="utf-8")
    save(out/"report_manifest.json", {"run_protocol_sha256": hashlib.sha256((run/"protocol.json").read_bytes()).hexdigest(),
         "audit_report_sha256": hashlib.sha256(fp(audit).read_bytes()).hexdigest(),
         "files": {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob("*") if p.is_file() and "plot_cache" not in p.parts},
         "development_recommendation": summary["development_recommendation"]})
    return {"reported": True, "recommended_method": summary["development_recommendation"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(report(args.run, args.out, args.audit), ensure_ascii=False))
