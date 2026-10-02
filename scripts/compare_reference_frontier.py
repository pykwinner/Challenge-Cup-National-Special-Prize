"""Frozen, bounded weighted-sum comparison on the admitted ideal RC TOU case.

Only three new solves; existing lexicographic endpoints and five epsilon solves
are read without modification. Independent exported-dispatch accounting never
uses the research carbon-weighted objective as the electricity bill.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from math import exp
from pathlib import Path
import shutil
import sys
from time import perf_counter

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_evidence_case_experiments import (chronology_check, code_identity,
    load_inputs, run_one, save, sha, verify_admission)

CONFIG = ROOT / "configs/evidence_case_20261002/wuhan_bestest600_rc_tou.yaml"
SERIES = ROOT / "data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv"
ADMISSION = ROOT / "docs/implementation_20261002/data/admission_tou.json"
BASE = ROOT / "docs/implementation_20261002/reference_experiments_tou/rc"
OUT = ROOT / "docs/implementation_20261002/frontier_comparison"
LAMBDAS = [0., .25, .5, .75, 1.]
START, END = "2024-11-16T00:00:00+08:00", "2025-03-16T00:00:00+08:00"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def relative(path):
    return str(Path(path).resolve().relative_to(ROOT))


def require(condition, message):
    if not bool(condition):
        raise ValueError(message)


def carbon_weight(lam, delta_cost, delta_emissions):
    require(np.isfinite(delta_cost) and delta_cost > 0, "nonpositive/undefined cost normalization range")
    require(np.isfinite(delta_emissions) and delta_emissions > 0, "nonpositive/undefined emissions normalization range")
    require(0 < lam < 1, "only internal lambda values have a finite research carbon weight")
    return float(lam / (1-lam) * delta_cost / delta_emissions)


def accounting(electric_kw, prices, factors, dt, configured_weight):
    electric = np.asarray(electric_kw, dtype=float)
    require(np.isfinite(electric).all() and np.min(electric) >= -1e-6, "invalid electricity dispatch")
    cost = float(np.dot(electric, prices) * dt)
    emissions = float(np.dot(electric, factors) * dt)
    return cost, emissions, cost + configured_weight * emissions


def audit_export(directory, frame, admitted_config):
    """Independent one-zone physical/electricity ledger; no model metrics reused."""
    config = yaml.safe_load((directory / "effective_config.yaml").read_text(encoding="utf-8"))
    summary, runner_audit = read(directory / "summary.json"), read(directory / "audit.json")
    expected = deepcopy(admitted_config)
    expected["planning"]["carbon_price_yuan_per_kg"] = config["planning"].get("carbon_price_yuan_per_kg", 0)
    if "emissions_cap_kg_per_year" in config["planning"]:
        expected["planning"]["emissions_cap_kg_per_year"] = config["planning"]["emissions_cap_kg_per_year"]
    else:
        expected["planning"].pop("emissions_cap_kg_per_year", None)
    require(config == expected, "effective configuration changed outside declared objective/cap")
    require(not config["pipes"] and not config["storages"] and len(config["sources"]) == 1,
            "independent checker only supports the admitted one-source/no-network reference")
    source = config["sources"][0]
    require(source["type"] == "electric_boiler" and source["max_new_capacity_kw"] == 0,
            "independent checker requires fixed ideal electric source")
    cooling = config["multi_energy_services"]["cooling"]["sources"][source["name"]]
    require(source["efficiency"] == 1 and cooling["eer"] == 1, "this experiment requires ideal efficiency/EER=1")
    investments = pd.read_csv(directory / "investments.csv")
    require(investments.empty, "reference comparison must have zero technology investment decisions")
    dispatch = pd.read_csv(directory / "dispatch.csv")

    def values(category):
        rows = dispatch[dispatch.category == category].sort_values("time_index")
        require(len(rows) == len(frame) and np.array_equal(rows.time_index, np.arange(len(frame))),
                f"missing/duplicate dispatch hours: {category}")
        require(rows.time.tolist() == frame.time.tolist(), f"dispatch timestamp mismatch: {category}")
        require(rows.scenario.tolist() == frame.scenario.tolist() and
                rows.boundary_group.tolist() == frame.boundary_group.tolist(), f"dispatch metadata mismatch: {category}")
        require((rows.unit == "kW").all(), f"unexpected dispatch unit: {category}")
        return rows.value.to_numpy(dtype=float)

    heat, cool = values("source_heat"), values("cooling_output")
    dt = float(config["planning"]["time_step_hours"])
    require(np.isfinite(heat).all() and np.isfinite(cool).all(), "nonfinite dispatch")
    require(min(heat.min(), cool.min()) >= -1e-6, "negative service")
    excess = float(max(0, (heat+cool-source["existing_capacity_kw"]).max()))
    require(excess <= 1e-6, "source capacity exceeded")
    electric = heat/source["efficiency"] + cool/cooling["eer"]
    weight = float(config["planning"].get("carbon_price_yuan_per_kg", 0))
    bill, carbon, expected_objective = accounting(electric, frame.electricity_price,
                                                frame.grid_emission_factor, dt, weight)
    objective_residual = float(summary["objective_yuan_per_year"] - expected_objective)
    carbon_residual = float(summary["total_service_emissions_kg_per_year"] - carbon)
    require(abs(objective_residual) <= 1e-5, "objective is not independent electricity bill + p*CO2")
    require(abs(carbon_residual) <= 1e-5, "summary carbon differs from independent dispatch carbon")
    cap = config["planning"].get("emissions_cap_kg_per_year")
    require(cap is None or carbon <= float(cap)+1e-5, "independent emissions cap exceeded")
    building = config["building_thermal"]["buildings"][0]
    trajectory = pd.read_csv(directory / "building_trajectory.csv").sort_values("time_index")
    require(len(trajectory) == len(frame) and np.array_equal(trajectory.time_index, np.arange(len(frame))), "incomplete RC trajectory")
    temperatures = trajectory.temperature_end_c.to_numpy(dtype=float)
    require(np.isfinite(temperatures).all(), "nonfinite RC trajectory")
    h, c = building["heat_loss_kw_per_c"], building["thermal_capacity_kwh_per_c"]
    a = exp(-h*dt/c)
    previous = np.r_[building["initial_temperature_c"], temperatures[:-1]]
    predicted = a*previous + (1-a)*frame.outdoor_temp + (1-a)/h*(heat-cool+frame.internal_gain_kw+frame.solar_gain_kw)
    rc_residual = float(np.max(np.abs(predicted-temperatures)))
    under, over = np.maximum(building["min_temperature_c"]-temperatures, 0), np.maximum(temperatures-building["max_temperature_c"], 0)
    require(rc_residual <= 1e-6 and max(under.max(), over.max()) <= 1e-6, "independent RC/comfort failure")
    require(abs(temperatures[-1]-building["final_temperature_c"]) <= 1e-6, "terminal temperature failure")
    require(np.max(np.minimum(heat,cool)) <= 1e-6, "simultaneous heating/cooling")
    require(np.max(np.abs(trajectory.heat_kw-heat)) <= 1e-6 and
            np.max(np.abs(trajectory.cooling_kw-cool)) <= 1e-6, "RC/source delivery mismatch")
    require(runner_audit["passed"] and read(directory / "independent_building_audit.json")["passed"], "recorded numerical/RC audit failed")
    return {
        "name": directory.name, "directory": relative(directory), "passed": True,
        "hours": len(frame), "heat_kwh": float(heat.sum()*dt), "cooling_kwh": float(cool.sum()*dt),
        "electricity_kwh": float(electric.sum()*dt), "electricity_energy_bill_yuan": bill,
        "average_factor_co2_kg": carbon, "configured_carbon_weight_yuan_per_kg": weight,
        "research_carbon_component_yuan": weight*carbon,
        "objective_yuan_from_summary": summary["objective_yuan_per_year"],
        "objective_yuan_recomputed": expected_objective, "objective_identity_residual_yuan": objective_residual,
        "solver_objective_mode": summary["objective_mode"], "solver_objective_value": summary["objective_value"],
        "emissions_summary_residual_kg": carbon_residual, "emissions_cap_kg": cap,
        "solver_status": summary["solver_status"], "mip_gap": summary.get("mip_gap"),
        "mip_dual_bound_in_solver_objective_units": summary.get("mip_dual_bound"),
        "solver_seconds": summary["solver_elapsed_seconds"],
        "primary_solver_seconds": summary.get("primary_solver_elapsed_seconds", 0.),
        "primary_mip_gap": summary.get("primary_mip_gap"),
        "all_stages_solver_seconds": summary.get("endpoint_solver_elapsed_seconds", summary["solver_elapsed_seconds"]),
        "solver_stages": 2 if "primary_objective" in summary else 1,
        "recorded_wall_seconds": runner_audit["wall_seconds"], "rc_max_residual_c": rc_residual,
        "outside_comfort_degree_hours": float((under.sum()+over.sum())*dt),
        "source_capacity_max_excess_kw": excess, "new_technology_investment_rows": len(investments),
        "maximum_ideal_source_electric_import_kw": float(electric.max()),
        "summary_sha256": sha(directory / "summary.json"), "dispatch_sha256": sha(directory / "dispatch.csv")}


def input_check():
    config, frame = load_inputs(CONFIG, SERIES)
    admission = verify_admission(ADMISSION, CONFIG, SERIES, rc=True)
    chronology_check(frame, config, START, END)
    require(np.allclose(frame.scenario_probability, 1) and np.allclose(frame.period_weight, 1), "reference weights must be 1")
    require(admission["full_economics_admitted"] is False, "scope changed; re-review required")
    return config, frame


def freeze(out):
    require(not out.exists(), "output exists; choose a new output directory")
    config, frame = input_check()
    manifest, baseline_protocol = read(BASE / "manifest.json"), read(BASE / "protocol.json")
    require(manifest["all_requested_experiments_passed"] and manifest["code_stable_during_run"], "existing baseline did not pass")
    for name, digest in manifest["files"].items():
        require(sha(BASE / name) == digest, f"existing evidence changed: {name}")
    codes = code_identity()
    require(codes == baseline_protocol["code_sha256"], "model/audit code differs from existing endpoint/epsilon run")
    require(baseline_protocol["time_limit_per_stage_seconds"] == 120 and baseline_protocol["mip_gap_target"] == .0001, "baseline solve budget differs")
    records = [audit_export(BASE / name, frame, config) for name in
               ["cost_endpoint", "emission_endpoint"] + [f"epsilon_{i}" for i in range(5)]]
    cost, emission = records[:2]
    delta_cost = emission["electricity_energy_bill_yuan"]-cost["electricity_energy_bill_yuan"]
    delta_carbon = cost["average_factor_co2_kg"]-emission["average_factor_co2_kg"]
    weights = [carbon_weight(lam, delta_cost, delta_carbon) for lam in LAMBDAS[1:-1]]
    codes[relative(Path(__file__))] = sha(Path(__file__))
    sources = {relative(p): sha(p) for p in [CONFIG, SERIES, ADMISSION, BASE / "manifest.json", BASE / "protocol.json"]}
    sources.update({relative(BASE / name): digest for name, digest in manifest["files"].items()})
    protocol = {
        "schema_version": "reference_frontier_comparison.v1", "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "lambdas": LAMBDAS, "lambda_semantics": "emissions weight in (1-lambda)*(C-Cmin)/deltaC + lambda*(E-Emin)/deltaE",
        "normalization": {"cost_min_yuan": cost["electricity_energy_bill_yuan"], "emissions_min_kg": emission["average_factor_co2_kg"],
            "delta_cost_yuan": delta_cost, "delta_emissions_kg": delta_carbon,
            "endpoint_tolerances_retained": True},
        "internal_carbon_weights_yuan_per_kg": weights,
        "weight_formula": "p=lambda/(1-lambda)*deltaC/deltaE; research scalarization coefficient, not a market carbon price",
        "lambda0": "reuse cost_endpoint: cost primary, emissions secondary",
        "lambda1": "reuse emission_endpoint: emissions primary, cost secondary; no infinite-price solve",
        "time_limit_per_stage_seconds": 120., "mip_gap_target": .0001,
        "shared_endpoint_solver_stages": 4, "additional_weighted_solver_stages": 3,
        "additional_epsilon_solver_stages": 5, "equal_compute_budget": False,
        "coverage_rule": "sorted observed normalized emissions; maximum adjacent gap; no adaptive lambdas, replacement, or interpolation proof",
        "distinct_point_normalized_tolerance": 1e-5,
        "new_technology_investment_decisions": 0,
        "scope": "2880h public ideal RC prototype, conditional industrial TOU electricity energy bill and fixed average-factor CO2 only",
        "not_claimed": ["field building validation", "account eligibility", "grid feasibility", "full engineering costs", "different engineering structures", "equal-budget acceleration", "complete Pareto frontier"],
        "source_sha256": sources, "code_sha256": codes}
    out.mkdir(parents=True)
    save(out / "reused_point_ledgers.json", records)
    save(out / "protocol.json", protocol)
    (out / "frozen_code").mkdir()
    shutil.copyfile(Path(__file__), out / "frozen_code/compare_reference_frontier.py")
    save(out / "protocol.lock.json", {"protocol_sha256": sha(out / "protocol.json"),
        "reused_point_ledgers_sha256": sha(out / "reused_point_ledgers.json"),
        "script_snapshot_sha256": sha(out / "frozen_code/compare_reference_frontier.py")})
    print(json.dumps({"frozen": True, "protocol_sha256": sha(out / "protocol.json"), "weights": weights}))


def check_lock(out):
    protocol, lock = read(out / "protocol.json"), read(out / "protocol.lock.json")
    for key, file in [("protocol_sha256", "protocol.json"), ("reused_point_ledgers_sha256", "reused_point_ledgers.json"),
                      ("script_snapshot_sha256", "frozen_code/compare_reference_frontier.py")]:
        require(sha(out / file) == lock[key], f"frozen file changed: {file}")
    for name, digest in {**protocol["source_sha256"], **protocol["code_sha256"]}.items():
        require(sha(ROOT / name) == digest, f"frozen source/code changed: {name}")
    return protocol


def normalized(records, protocol):
    n = protocol["normalization"]
    for record in records:
        if record.get("passed"):
            record["normalized_cost"] = (record["electricity_energy_bill_yuan"]-n["cost_min_yuan"])/n["delta_cost_yuan"]
            record["normalized_emissions"] = (record["average_factor_co2_kg"]-n["emissions_min_kg"])/n["delta_emissions_kg"]
            if record.get("lambda") is not None:
                lam = record["lambda"]
                record["normalized_weighted_objective"] = (1-lam)*record["normalized_cost"]+lam*record["normalized_emissions"]
    return records


def coverage(points, tolerance):
    emissions = sorted(p["normalized_emissions"] for p in points if p.get("passed"))
    distinct = []
    for e in emissions:
        if not distinct or e-distinct[-1] > tolerance:
            distinct.append(e)
    return {"passed_points": len(emissions), "distinct_emission_positions": len(distinct),
        "minimum_normalized_emissions": min(emissions) if emissions else None,
        "maximum_normalized_emissions": max(emissions) if emissions else None,
        "normalized_emissions_span": max(emissions)-min(emissions) if emissions else None,
        "maximum_adjacent_normalized_emissions_gap": max(np.diff(distinct)) if len(distinct)>1 else None,
        "sorted_distinct_normalized_emissions": distinct}


def report(out, records, protocol, failures, run_wall):
    records = normalized(records, protocol)
    save(out / "point_ledgers.json", records)
    pd.DataFrame(records).to_csv(out / "point_ledgers.csv", index=False)
    endpoints = [r for r in records if r["method"] == "shared_endpoint"]
    weighted = endpoints + [r for r in records if r["method"] == "normalized_weighted"]
    epsilon = [r for r in records if r["method"] == "epsilon"]
    comparison = {"all_requested_points_passed": not failures and all(r["passed"] for r in records),
        "weighted_five_positions": coverage(weighted, 1e-5), "epsilon_five_positions": coverage(epsilon, 1e-5),
        "epsilon_with_shared_endpoint_anchors": coverage(epsilon+endpoints, 1e-5),
        "shared_endpoint_solver_stages": 4, "weighted_additional_solver_stages": 3, "epsilon_additional_solver_stages": 5,
        "shared_endpoint_solver_seconds": sum(r["all_stages_solver_seconds"] for r in endpoints),
        "weighted_additional_solver_seconds": sum(r.get("all_stages_solver_seconds",0) for r in records if r["method"] == "normalized_weighted"),
        "epsilon_additional_solver_seconds": sum(r["all_stages_solver_seconds"] for r in epsilon),
        "shared_endpoint_recorded_wall_seconds": sum(r["recorded_wall_seconds"] for r in endpoints),
        "weighted_additional_recorded_wall_seconds": sum(r.get("recorded_wall_seconds",0) for r in records if r["method"] == "normalized_weighted"),
        "epsilon_additional_recorded_wall_seconds": sum(r["recorded_wall_seconds"] for r in epsilon),
        "current_run_wall_seconds_before_report": run_wall,
        "equal_compute_budget": False, "acceleration_claim": False, "engineering_structure_comparison": False,
        "empirical_validation": False, "full_economics_admitted": False,
        "interpretation": "Observed sampled coverage only; different extra solve counts and reused historical timing prevent equal-budget speed claims."}
    save(out / "comparison.json", comparison)
    save(out / "failures.json", failures)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.8, 4.8))
    for rows, marker, label in [(epsilon,"s","epsilon: 5 existing solves"), (weighted,"o","weighted: shared endpoints + 3 new solves")]:
        valid = [r for r in rows if r.get("passed")]
        ax.scatter([r["average_factor_co2_kg"] for r in valid], [r["electricity_energy_bill_yuan"] for r in valid], marker=marker, s=52, label=label)
    for r in weighted:
        if r.get("passed"):
            ax.annotate(f"lambda={r['lambda']:g}", (r["average_factor_co2_kg"],r["electricity_energy_bill_yuan"]), xytext=(4,5), textcoords="offset points",fontsize=8)
    ax.set(xlabel="Fixed average-factor CO2 over 2880h (kg)", ylabel="Electricity energy bill over 2880h (yuan)",
           title="Public ideal RC prototype / conditional TOU reference")
    ax.grid(alpha=.2); ax.legend(fontsize=8); fig.tight_layout()
    fig.savefig(out / "frontier_points.png", dpi=180); plt.close(fig)
    lines = ["# 有界运行费用—碳对照（E5补充）", "",
        "公开BESTEST派生单建筑RC原型、武汉天气与条件工业TOU电量价。2880小时完整供暖季；不是中国三城现场验证。仅计电量费与固定平均因子CO2，不计全工程成本；0项新技术投资，点之间改变调度而非工程结构。", "",
        "λ给排放权重。预冻结λ=[0,0.25,0.5,0.75,1]；内点目标系数p=λ/(1−λ)·ΔC/ΔE，属于研究权重，不是市场碳价。归一范围来自已有双阶段端点的实际电费与CO2；端点容差保留。λ=0/1复用双阶段端点，共4 solver stages；加权内点另3次求解，既有ε另5次，均120s/stage、gap目标1e-4。求解次数不同，不能称等预算加速。", "",
        "账本从source_heat和cooling_output分别按效率/EER换算用电，再逐小时乘原始TOU与排放因子。所有点校验objective_yuan=C+p·E，且独立复算RC、温度、边界、容量与调度时序。端点第二阶段的solver_objective_value可能是排放，不可直接当元；图纵轴只使用独立电量账单。", "",
        "|方法/点|λ|研究权重 元/kg|电量费 元|CO2 kg|gap|全部阶段 solver秒|记录wall秒|", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in records:
        if r.get("passed"):
            lam = "—" if r.get("lambda") is None else f"{r['lambda']:g}"
            p = "极限端点" if r.get("lambda") == 1 else f"{r['configured_carbon_weight_yuan_per_kg']:.8f}"
            lines.append(f"|{r['method']}/{r['name']}|{lam}|{p}|{r['electricity_energy_bill_yuan']:.6f}|{r['average_factor_co2_kg']:.6f}|{r['mip_gap']:.6g}|{r['all_stages_solver_seconds']:.6f}|{r['recorded_wall_seconds']:.6f}|")
        else:
            lines.append(f"|{r['method']}/{r['name']}|{r.get('lambda')}|失败|—|—|—|—|—|")
    lines += ["", "## 观察到的覆盖", "",
        f"加权5点最大相邻归一碳间隔：{comparison['weighted_five_positions']['maximum_adjacent_normalized_emissions_gap']:.8f}；既有ε5点：{comparison['epsilon_five_positions']['maximum_adjacent_normalized_emissions_gap']:.8f}。详细位置、范围与加入共享端点后的指标见comparison.json。只比较这批样点，未证明完整Pareto前沿，也不预设方法普遍优胜。", "",
        f"共享端点solver合计{comparison['shared_endpoint_solver_seconds']:.6f}s；新增加权3次{comparison['weighted_additional_solver_seconds']:.6f}s；既有ε5次{comparison['epsilon_additional_solver_seconds']:.6f}s。记录wall分别{comparison['shared_endpoint_recorded_wall_seconds']:.6f}/{comparison['weighted_additional_recorded_wall_seconds']:.6f}/{comparison['epsilon_additional_recorded_wall_seconds']:.6f}s；独立重算/绘图与重复验证耗时另列或不属于solver时间。历史ε与本次加权不是同时计时。", "",
        f"失败记录：{len(failures)}项。协议、源码快照、原始来源与既有结果hash见protocol.json/protocol.lock.json；未改原结果。manifest.json覆盖本批输出。", "",
        "## 复现", "", "工程根目录，使用D:/app/anaconda3/python.exe：", "", "```powershell",
        "$env:PYTHONIOENCODING='utf-8'",
        "D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py self-check",
        "D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py verify",
        "# 新求解必须新目录；不覆盖本批记录",
        "D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py freeze --out docs/implementation_20261002/frontier_comparison/reproduction_01",
        "D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py run --out docs/implementation_20261002/frontier_comparison/reproduction_01",
        "D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py verify --out docs/implementation_20261002/frontier_comparison/reproduction_01",
        "```", "", "来源准入仍有账户资格、电网接入、设备效率、无太阳增益、初末20℃预处理等限制；研究加权目标不升级这些限制。未重跑842项主线回归，本任务未改src/tests。"]
    (out / "README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def run(out):
    protocol = check_lock(out)
    require(not (out / "weighted_points").exists(), "run exists; preserve it and freeze a new directory")
    config, frame = input_check()
    records = read(out / "reused_point_ledgers.json")
    for index, record in enumerate(records):
        record["method"] = "shared_endpoint" if index < 2 else "epsilon"
        record["lambda"] = [0.,1.][index] if index < 2 else None
        record["reused_existing_solve"] = True
    (out / "weighted_points").mkdir()
    failures, started = [], perf_counter()
    for lam, weight in zip(LAMBDAS[1:-1], protocol["internal_carbon_weights_yuan_per_kg"]):
        name = f"lambda_{str(lam).replace('.', '_')}"
        candidate = deepcopy(config)
        candidate["planning"]["carbon_price_yuan_per_kg"] = weight
        candidate["planning"].pop("emissions_cap_kg_per_year", None)
        print(f"Solving frozen lambda={lam:g}, research p={weight:.10g}", flush=True)
        try:
            run_one(name, candidate, frame, out / "weighted_points", 120., .0001)
            record = audit_export(out / "weighted_points" / name, frame, config)
            record.update(method="normalized_weighted", **{"lambda":lam}, reused_existing_solve=False)
        except Exception as error:
            failure = {"name":name, "lambda":lam, "reason":str(error), "type":type(error).__name__}
            failures.append(failure)
            record = {**failure, "method":"normalized_weighted", "passed":False, "reused_existing_solve":False}
        records.append(record)
    check_lock(out)
    report(out, records, protocol, failures, perf_counter()-started)
    save(out / "manifest.json", {"completed_utc":datetime.now(timezone.utc).isoformat(),
        "all_requested_points_passed": not failures, "code_and_sources_stable":True,
        "protocol_sha256":sha(out / "protocol.json"),
        "files": {str(p.relative_to(out)):sha(p) for p in sorted(out.rglob("*")) if p.is_file() and p.name != "manifest.json"}})
    print(json.dumps(read(out / "comparison.json"), ensure_ascii=False))


def verify(out):
    protocol = check_lock(out)
    manifest = read(out / "manifest.json")
    for name, digest in manifest["files"].items():
        require(sha(out / name) == digest, f"output changed: {name}")
    config, frame = input_check()
    records = read(out / "point_ledgers.json")
    checked = 0
    for record in records:
        if not record.get("passed"):
            continue
        independent = audit_export(ROOT / record["directory"], frame, config)
        for field in ["electricity_energy_bill_yuan", "average_factor_co2_kg", "objective_yuan_recomputed",
                      "electricity_kwh", "rc_max_residual_c", "summary_sha256", "dispatch_sha256"]:
            require(independent[field] == record[field], f"ledger recomputation changed: {record['name']}/{field}")
        checked += 1
    reproduced = normalized(deepcopy(records), protocol)
    for method, subset in [("weighted_five_positions",[r for r in reproduced if r["method"] != "epsilon"]),
                           ("epsilon_five_positions",[r for r in reproduced if r["method"] == "epsilon"])]:
        require(coverage(subset,1e-5) == read(out / "comparison.json")[method], f"coverage does not reproduce: {method}")
    result = {"passed":True, "checked_successful_point_files":checked, "failed_point_files":len(records)-checked,
        "all_requested_points_passed":manifest["all_requested_points_passed"], "protocol_sha256":sha(out / "protocol.json"),
        "verification_scope":"hashes and independent source/cooling electric bill, CO2, objective identity, RC/comfort/capacity/chronology plus coverage recomputation"}
    print(json.dumps(result,ensure_ascii=False))


def self_check():
    require(carbon_weight(.5,40,20) == 2, "weight mapping")
    for lam in [.25,.5,.75]:
        p = carbon_weight(lam,40,20)
        c, e = 30., 10.
        require(np.isclose((1-lam)*c/40+lam*e/20, (1-lam)/40*(c+p*e)), "scalarization equivalence")
    rejected = 0
    for lam, dc, de in [(0,40,20),(1,40,20),(.5,0,20),(.5,40,0),(.5,float('nan'),20)]:
        try: carbon_weight(lam,dc,de)
        except ValueError: rejected += 1
    require(rejected == 5, "undefined-range/endpoint rejection")
    bill, co2, objective = accounting(np.array([2.,3.]), [1.,2.], [.4,.5], 1, 7)
    require(bill == 8 and co2 == 2.3 and np.isclose(objective,24.1), "pure bill vs weighted objective separation")
    c = coverage([{"passed":True,"normalized_emissions":e} for e in [0,.25,.25+1e-8,1]],1e-5)
    require(c["distinct_emission_positions"] == 3 and c["maximum_adjacent_normalized_emissions_gap"] == .75, "duplicate/spacing handling")
    print(json.dumps({"passed":True,"checks":["finite weight mapping","normalized scalarization equivalence at 3 internal lambdas","five undefined range/endpoint rejections","energy bill separated from research objective","duplicate-aware observed spacing"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze","run","verify","self-check"])
    parser.add_argument("--out",type=Path,default=OUT)
    args = parser.parse_args()
    if args.action == "self-check": self_check()
    else: {"freeze":freeze,"run":run,"verify":verify}[args.action](args.out.resolve())


if __name__ == "__main__":
    main()
