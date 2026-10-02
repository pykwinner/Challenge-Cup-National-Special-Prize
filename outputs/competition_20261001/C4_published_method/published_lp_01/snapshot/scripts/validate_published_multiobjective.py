"""Reproduce a published two-variable LP through the project numerical layer.

This is an independent lexicographic wrapper, not HeatPlanningModel's API or
the complete AUGMECON implementation. Published targets are validation only.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from pathlib import Path
import platform
import shutil
import sys
from time import perf_counter
import traceback

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from heatplan.linear import LinearMILP

DATA = ROOT / "data/published_multiobjective_20261002"
OUTPUT = ROOT / "outputs/competition_20261001/C4_published_method"
FLAGS = {
    "evidence_purpose": "published_mathematical_method_validation",
    "empirical_validation": False,
    "heating_engineering_validation": False,
    "parameter_provenance_approved_for_heating_scenario": False,
    "calls_HeatPlanningModel_solve_lexicographic": False,
    "implements_full_AUGMECON": False,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity" if value < 0 else "NaN"
    return value


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(clean(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def rational(value):
    return Fraction(str(value))


def source_halfspaces(model, resource_cap, epsilon=None, primary_fix=None):
    """Independent exact geometry. Never reads a LinearMILP instance."""
    halfspaces = []
    for i, (lo, hi) in enumerate(zip(model["lower_bounds"], model["upper_bounds"])):
        unit = [Fraction(0), Fraction(0)]
        unit[i] = Fraction(1)
        halfspaces.append((tuple(unit), rational(hi)))
        halfspaces.append((tuple(-a for a in unit), -rational(lo)))
    halfspaces.append((tuple(map(rational, model["resource_coefficients"])), rational(resource_cap)))
    if epsilon is not None:
        halfspaces.append((tuple(-rational(a) for a in model["objectives"]["f2"]), -rational(epsilon)))
    if primary_fix is not None:
        name, value = primary_fix
        coef = tuple(map(rational, model["objectives"][name]))
        halfspaces.extend([(coef, rational(value)), (tuple(-a for a in coef), -rational(value))])
    return halfspaces


def exact_vertices(halfspaces):
    vertices = set()
    for (a, rhs_a), (b, rhs_b) in combinations(halfspaces, 2):
        determinant = a[0] * b[1] - a[1] * b[0]
        if not determinant:
            continue
        point = ((rhs_a * b[1] - a[1] * rhs_b) / determinant,
                 (a[0] * rhs_b - rhs_a * b[0]) / determinant)
        if all(coef[0] * point[0] + coef[1] * point[1] <= rhs for coef, rhs in halfspaces):
            vertices.add(point)
    return sorted(vertices)


def exact_optimum(model, resource_cap, objective, epsilon=None, primary_fix=None):
    vertices = exact_vertices(source_halfspaces(model, resource_cap, epsilon, primary_fix))
    if not vertices:
        return {"feasible": False, "bound": None, "maximizers": []}
    coef = tuple(map(rational, model["objectives"][objective]))
    values = [sum(a * x for a, x in zip(coef, point)) for point in vertices]
    bound = max(values)
    return {"feasible": True, "bound": bound,
            "maximizers": [point for point, value in zip(vertices, values) if value == bound]}


def exact_lexicographic(model, resource_cap, order, epsilon=None):
    first = exact_optimum(model, resource_cap, order[0], epsilon)
    if not first["feasible"]:
        return None
    second = exact_optimum(model, resource_cap, order[1], epsilon, (order[0], first["bound"]))
    if len(second["maximizers"]) != 1:
        raise ValueError("Expected unique final lexicographic point in declared example")
    point = second["maximizers"][0]
    return {"x": point, "objectives": {
        name: sum(rational(a) * x for a, x in zip(coef, point))
        for name, coef in model["objectives"].items()
    }}


def build_lp(model, resource_cap, objective, epsilon=None, primary_fix=None):
    lp = LinearMILP()
    for i, name in enumerate(model["variable_names"]):
        lp.add_var(name, lb=model["lower_bounds"][i], ub=model["upper_bounds"][i],
                   integer=False, obj=-model["objectives"][objective][i])
    lp.add_constraint(dict(enumerate(model["resource_coefficients"])), ub=resource_cap)
    if epsilon is not None:
        lp.add_constraint(dict(enumerate(model["objectives"]["f2"])), lb=epsilon)
    if primary_fix is not None:
        name, value = primary_fix
        lp.add_constraint(dict(enumerate(model["objectives"][name])), lb=value, ub=value)
    return lp


def model_record(lp):
    return {"names": lp.names, "lower": lp.lower, "upper": lp.upper,
            "integrality": lp.integrality, "objective_minimization": lp.objective,
            "matrix": lp.constraint_matrix().toarray(), "row_lower": lp.row_lower,
            "row_upper": lp.row_upper, "variables": len(lp.names), "rows": len(lp.rows),
            "nonzeros": int(lp.constraint_matrix().nnz), "binary_variables": 0}


def detailed_audit(lp, vector):
    rows = []
    for i, (row, lo, hi) in enumerate(zip(lp.rows, lp.row_lower, lp.row_upper)):
        activity = sum(coef * vector[j] for j, coef in row.items())
        rows.append({"row_index": i, "coefficients": row, "lower": lo, "upper": hi,
                     "activity": activity, "lower_slack": activity - lo,
                     "upper_slack": hi - activity,
                     "violation": max(0.0, lo - activity, activity - hi)})
    variables = [{"name": name, "value": vector[i], "lower": lp.lower[i],
                  "upper": lp.upper[i], "integrality": lp.integrality[i],
                  "violation": max(0.0, lp.lower[i] - vector[i], vector[i] - lp.upper[i])}
                 for i, name in enumerate(lp.names)]
    return {"all_rows": rows, "all_variables": variables,
            "objective_recomputed_minimization": float(np.asarray(lp.objective) @ vector)}


def close(actual, expected, protocol):
    return bool(np.allclose(actual, expected,
                           atol=protocol["analytic_comparison_absolute_tolerance"],
                           rtol=protocol["analytic_comparison_relative_tolerance"]))


def oracle_record(oracle):
    return {"feasible": oracle["feasible"],
            "optimal_bound_maximization": float(oracle["bound"]) if oracle["feasible"] else None,
            "exact_bound": str(oracle["bound"]) if oracle["feasible"] else None,
            "maximizer_vertices_exact": [[str(x) for x in v] for v in oracle["maximizers"]],
            "bound_origin": "independent exact rational polygon; not a solver-exposed bound"}


def solve_stage(directory, model, cap, objective, protocol, *, epsilon=None,
                primary_fix=None, analytic_primary_fix=None, expected_infeasible=False):
    directory.mkdir(parents=True, exist_ok=False)
    lp = build_lp(model, cap, objective, epsilon, primary_fix)
    oracle = exact_optimum(model, cap, objective, epsilon, analytic_primary_fix)
    write_json(directory / "model.json", model_record(lp))
    started_utc = utc_now()
    write_json(directory / "started.json", {"started_utc": started_utc,
               "resource_cap": cap, "objective": objective, "epsilon": epsilon,
               "primary_fix_from_actual_stage_1": primary_fix,
               "expected_infeasible": expected_infeasible, **FLAGS})
    started = perf_counter()
    result = lp.solve(mip_gap=protocol["mip_rel_gap_option"],
                      time_limit=protocol["time_limit_seconds_per_stage"])
    elapsed = perf_counter() - started
    vector = None if result.x is None else np.asarray(result.x, dtype=float)
    objectives = None if vector is None else {
        name: float(np.asarray(coef) @ vector) for name, coef in model["objectives"].items()}
    native_fields = {name: result.get(name, None) for name in
                     ["status", "success", "message", "fun", "mip_gap", "mip_dual_bound", "mip_node_count"]}
    audit = None if vector is None else lp.audit_solution(
        vector, absolute_tolerance=protocol["audit_absolute_tolerance"],
        relative_tolerance=protocol["audit_relative_tolerance"])
    checks = {"status_matches_expected": int(result.status) == (2 if expected_infeasible else 0),
              "analytic_feasibility_matches_expected": oracle["feasible"] != expected_infeasible}
    if expected_infeasible:
        checks["no_solution_vector_claimed"] = vector is None
    else:
        checks.update({"solver_success": bool(result.success),
                       "full_matrix_audit_passed": audit is not None and audit["numerical_audit_passed"],
                       "optimal_objective_matches_independent_exact_bound": objectives is not None and
                       oracle["feasible"] and close(objectives[objective], float(oracle["bound"]), protocol)})
    record = {"stage_directory": directory.name, "started_utc": started_utc,
              "finished_utc": utc_now(), "elapsed_seconds": elapsed,
              "resource_cap": cap, "objective_maximization": objective, "epsilon": epsilon,
              "actual_primary_fix": primary_fix, "solver_fields": native_fields,
              "solver_fields_not_exposed": [k for k, v in native_fields.items() if v is None],
              "solution_vector": vector, "objectives": objectives, "independent_exact_oracle": oracle_record(oracle),
              "matrix_audit": audit, "all_matrix_details": None if vector is None else detailed_audit(lp, vector),
              "expected_infeasible": expected_infeasible, "checks": checks,
              "protocol_checks_passed": all(checks.values()), **FLAGS}
    write_json(directory / "result.json", record)
    if not record["protocol_checks_passed"]:
        raise RuntimeError(f"Unexpected solver/audit failure in {directory}: {checks}")
    return record


def solve_lexicographic(directory, model, cap, order, protocol, epsilon=None):
    exact = exact_lexicographic(model, cap, order, epsilon)
    if exact is None:
        raise ValueError("Declared lexicographic positive test is analytically infeasible")
    first = solve_stage(directory / "stage_1", model, cap, order[0], protocol, epsilon=epsilon)
    actual_primary = first["objectives"][order[0]]
    # Fix the realized stage-1 optimum, never a published table target.
    second = solve_stage(directory / "stage_2", model, cap, order[1], protocol, epsilon=epsilon,
                         primary_fix=(order[0], actual_primary),
                         analytic_primary_fix=(order[0], exact["objectives"][order[0]]))
    expected = [float(exact["objectives"][name]) for name in ["f1", "f2"]]
    actual = [second["objectives"][name] for name in ["f1", "f2"]]
    checks = {"final_objectives_match_rational_oracle": close(actual, expected, protocol),
              "final_vector_matches_rational_oracle": close(second["solution_vector"], [float(x) for x in exact["x"]], protocol),
              "primary_value_preserved": close(second["objectives"][order[0]], actual_primary, protocol)}
    record = {"case": directory.name, "resource_cap": cap, "order": list(order), "epsilon": epsilon,
              "solution_vector": second["solution_vector"], "objectives": second["objectives"],
              "analytic_exact_vector": [str(x) for x in exact["x"]],
              "analytic_exact_objectives": {k: str(v) for k, v in exact["objectives"].items()},
              "primary_degradation_allowance": 0.0,
              "stage_1": first, "stage_2": second, "checks": checks,
              "protocol_checks_passed": all(checks.values()), **FLAGS}
    write_json(directory / "lexicographic_result.json", record)
    if not record["protocol_checks_passed"]:
        raise RuntimeError(f"Lexicographic comparison failed: {checks}")
    return record


def nondominated_count(points, tolerance=1e-7):
    unique = []
    for point in points:
        if not any(np.allclose(point, x, atol=tolerance, rtol=0) for x in unique):
            unique.append(np.asarray(point))
    survivors = [p for p in unique if not any(
        np.all(q >= p - tolerance) and np.any(q > p + tolerance) for q in unique)]
    return {"actual_point_count": len(points), "unique_point_count": len(unique),
            "nondominated_unique_point_count": len(survivors)}


def file_hashes(paths):
    return {str(Path(path).relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in paths}


def write_csv(path, rows):
    with Path(path).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot_results(directory, model, endpoints, epsilon_cases):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    base = [case["objectives"] for case in epsilon_cases]
    analytic = [exact_lexicographic(model, 200, order) for order in [("f1", "f2"), ("f2", "f1")]]
    axes[0].plot([float(p["objectives"]["f1"]) for p in analytic],
                 [float(p["objectives"]["f2"]) for p in analytic], color="#176b8d", alpha=.55,
                 label="Independent analytic frontier")
    axes[0].scatter([p["f1"] for p in base], [p["f2"] for p in base], color="#c75a25", s=65,
                    zorder=3, label="Actual LP solutions")
    for case in epsilon_cases:
        axes[0].annotate(f"epsilon={case['epsilon']:g}", (case["objectives"]["f1"], case["objectives"]["f2"]),
                         xytext=(6, 7), textcoords="offset points", fontsize=9)
    axes[0].set_title("Published example: resource cap 200")
    axes[0].set_xlim(6, 23.8)
    axes[0].set_ylim(155, 190)
    axes[0].legend(loc="lower left", fontsize=9)
    for cap, color in [(180, "#777777"), (200, "#176b8d"), (220, "#40844a")]:
        pair = endpoints[cap]
        exact = [exact_lexicographic(model, cap, order) for order in [("f1", "f2"), ("f2", "f1")]]
        axes[1].plot([float(p["objectives"]["f1"]) for p in exact],
                     [float(p["objectives"]["f2"]) for p in exact], color=color,
                     label=f"cap {cap}: {'published' if cap == 200 else 'mathematical test'}")
        axes[1].scatter([p["objectives"]["f1"] for p in pair], [p["objectives"]["f2"] for p in pair], color=color, s=38)
    axes[1].set_title("Predeclared resource boundary checks")
    axes[1].legend(loc="lower left", fontsize=9)
    for axis in axes:
        axis.set_xlabel("f1 (maximize)")
        axis.set_ylabel("f2 (maximize)")
        axis.grid(alpha=.18)
    figure.suptitle("Published mathematical method validation - no heating or field evidence", fontsize=12)
    for suffix in ["png", "svg"]:
        figure.savefig(directory / f"published_frontier.{suffix}", dpi=180)
    plt.close(figure)


def run_experiment(run_id):
    if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in run_id):
        raise ValueError("run_id must be a simple exclusive directory name")
    inputs = json.loads((DATA / "published_input.json").read_text(encoding="utf-8"))
    plan = json.loads((DATA / "predeclared_plan.json").read_text(encoding="utf-8"))
    source = json.loads((DATA / "source_manifest.json").read_text(encoding="utf-8"))
    reviewed_identity = json.loads((DATA / "predeclared_review_identity.json").read_text(encoding="utf-8"))
    if any(sha256(DATA / name) != digest for name, digest in reviewed_identity["reviewed_files_sha256"].items()):
        raise ValueError("Reviewed source/input/predeclared plan identity mismatch")
    if sha256(DATA / "epscm.pdf") != inputs["source_sha256"] or source["source_sha256"] != plan["source_sha256"]:
        raise ValueError("Original PDF identity mismatch")
    if not plan["declared_before_any_experiment_solver_call"] or datetime.fromisoformat(plan["declared_utc"]) >= datetime.now(timezone.utc):
        raise ValueError("Plan must be persisted before solving")
    if plan["implementation_boundary"]["calls_HeatPlanningModel_solve_lexicographic"]:
        raise ValueError("Cannot label independent wrapper as heating-model API")
    model, protocol = inputs["model"], plan["numerical_protocol"]
    directory = OUTPUT / run_id
    directory.mkdir(parents=True, exist_ok=False)
    code_paths = [Path(__file__).resolve(), ROOT / "src/heatplan/linear.py", ROOT / "tests/test_published_multiobjective.py"]
    source_paths = sorted(path for path in DATA.iterdir() if path.is_file())
    identities = {"code_files": file_hashes(code_paths), "input_source_files": file_hashes(source_paths)}
    write_json(directory / "identity_before.json", identities)
    for path in code_paths + source_paths:
        dest = directory / "snapshot" / path.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    write_json(directory / "started.json", {"started_utc": utc_now(), "run_id": run_id,
               "predeclared_plan_sha256": sha256(DATA / "predeclared_plan.json"),
               "runtime": {"python": sys.version, "python_executable": sys.executable,
                           "platform": platform.platform(), "scipy": scipy.__version__, "numpy": np.__version__}, **FLAGS})
    started = perf_counter()
    try:
        endpoints = {}
        cases = []
        for cap in plan["boundary_tests"]["caps"]:
            endpoints[cap] = []
            for order in plan["endpoint_orders"]:
                case = solve_lexicographic(directory / f"cap_{cap}_{order[0]}_then_{order[1]}",
                                            model, cap, order, protocol)
                endpoints[cap].append(case)
                cases.append(case)
        published = endpoints[model["resource_upper_bound"]]
        table_checks = {
            "table_2_" + "_then_".join(case["order"]): close(
                [case["objectives"]["f1"], case["objectives"]["f2"]],
                inputs["validation_reference"]["table_2"]["_then_".join(case["order"])], protocol)
            for case in published}
        low, high = sorted(case["objectives"]["f2"] for case in published)
        grid = np.linspace(low, high, plan["epsilon_grid"]["interval_count"] + 1)
        if not close(grid, plan["epsilon_grid"]["predeclared_check_values"], protocol):
            raise RuntimeError("Actual solved grid does not match predeclared grid")
        epsilon_cases = []
        for i, epsilon in enumerate(grid):
            case = solve_lexicographic(directory / f"published_epsilon_{i:02d}", model,
                                        model["resource_upper_bound"], ["f1", "f2"], protocol, float(epsilon))
            cases.append(case)
            epsilon_cases.append(case)
        negative_plan = plan["boundary_tests"]["negative_case"]
        negative = solve_stage(directory / "negative_cap_180_epsilon_184", model,
                               negative_plan["resource_cap"], "f1", protocol,
                               epsilon=negative_plan["epsilon"], expected_infeasible=True)
        stage_records = [case[stage] for case in cases for stage in ["stage_1", "stage_2"]] + [negative]
        counts = nondominated_count([[case["objectives"]["f1"], case["objectives"]["f2"]] for case in epsilon_cases])
        identities_after = {"code_files": file_hashes(code_paths), "input_source_files": file_hashes(source_paths)}
        checks = {**table_checks, "all_stages_pass": all(s["protocol_checks_passed"] for s in stage_records),
                  "all_final_lexicographic_checks_pass": all(c["protocol_checks_passed"] for c in cases),
                  "solver_call_count_matches_plan": len(stage_records) == plan["expected_solver_call_count"],
                  "five_unique_nondominated_epsilon_points": counts["nondominated_unique_point_count"] == 5,
                  "input_and_code_unchanged": identities == identities_after}
        write_json(directory / "identity_after.json", identities_after)
        if not all(checks.values()):
            raise RuntimeError(f"Final protocol checks failed: {checks}")
        stage_rows = []
        for i, stage in enumerate(stage_records):
            native, oracle = stage["solver_fields"], stage["independent_exact_oracle"]
            stage_rows.append({"stage_index": i, "resource_cap": stage["resource_cap"], "epsilon": stage["epsilon"],
                               "objective_maximization": stage["objective_maximization"],
                               "status": native["status"], "success": native["success"], "elapsed_seconds": stage["elapsed_seconds"],
                               "native_minimization_fun": native["fun"], "solver_mip_dual_bound": native["mip_dual_bound"],
                               "solver_mip_gap": native["mip_gap"], "independent_exact_max_bound": oracle["optimal_bound_maximization"],
                               "x1": None if stage["solution_vector"] is None else stage["solution_vector"][0],
                               "x2": None if stage["solution_vector"] is None else stage["solution_vector"][1],
                               "f1": None if stage["objectives"] is None else stage["objectives"]["f1"],
                               "f2": None if stage["objectives"] is None else stage["objectives"]["f2"],
                               "matrix_audit_passed": None if stage["matrix_audit"] is None else stage["matrix_audit"]["numerical_audit_passed"],
                               "expected_infeasible": stage["expected_infeasible"], "protocol_checks_passed": stage["protocol_checks_passed"]})
        point_rows = [{"case": case["case"], "resource_cap": case["resource_cap"], "epsilon": case["epsilon"],
                       "order": "_then_".join(case["order"]), "x1": case["solution_vector"][0],
                       "x2": case["solution_vector"][1], "f1": case["objectives"]["f1"], "f2": case["objectives"]["f2"],
                       "classification": "published_base" if case["resource_cap"] == 200 else "mathematical_boundary_protocol"}
                      for case in cases]
        write_csv(directory / "stages.csv", stage_rows)
        write_csv(directory / "points.csv", point_rows)
        summary = {"run_id": run_id, "finished_utc": utc_now(), "wall_seconds": perf_counter() - started,
                   "solver_seconds_total": sum(s["elapsed_seconds"] for s in stage_records),
                   "actual_solver_call_count": len(stage_records), "optimal_stage_count": sum(s["solver_fields"]["status"] == 0 for s in stage_records),
                   "expected_infeasible_stage_count": sum(s["expected_infeasible"] for s in stage_records),
                   "published_endpoints": [{"order": c["order"], "x": c["solution_vector"], "objectives": c["objectives"]} for c in published],
                   "actual_epsilon_grid": grid, "published_epsilon_points": [p for p in point_rows if p["epsilon"] is not None],
                   "epsilon_point_counts": counts, "checks": checks,
                   "max_matrix_row_violation": max(s["matrix_audit"]["numerical_constraint_max_violation"] for s in stage_records if s["matrix_audit"]),
                   "max_matrix_bound_violation": max(s["matrix_audit"]["numerical_bound_max_violation"] for s in stage_records if s["matrix_audit"]),
                   "max_objective_recompute_error": max(s["matrix_audit"]["numerical_objective_recompute_error"] for s in stage_records if s["matrix_audit"]), **FLAGS}
        write_json(directory / "summary.json", summary)
        plot_results(directory, model, endpoints, epsilon_cases)
        lines = ["# 已发表双目标 LP 的方法复现", "", "这是工程实际 LinearMILP 数值层加独立两阶段词典序包装的数学验证。未调用 HeatPlanningModel.solve_lexicographic；未复现完整 AUGMECON 松弛加权/提前退出。供热工程前沿和实测验证均为 false。", "", "来源为 [George Mavrotas 官方 GAMS 作者报告](https://www.gams.com/modlib/adddocs/epscm.pdf)，第 4.1 节（物理第 6 页）、表 2/图 2（第 7 页）、四等分规则（第 8 页）、图 4（第 9 页）。模型为 max f1=x1，max f2=3x1+4x2，x1≤20，x2≤40，5x1+4x2≤200。非负域依据图 2 解释，公式未单列下界。", "", f"原 PDF SHA256：`{inputs['source_sha256']}`。下载时间与 HTTP 信息见输入快照 source_manifest.json；未将 HTTP 修改时间当论文发表日期。", "", "先保存 predeclared_plan.json，后运行实验。原文表 2 仅作验证参照；第二阶段等式固定实际第一阶段求解值。ε 由实际端点范围四等分后核对预声明 160/166/172/178/184，三个中间值是推导值。180/220 资源上限是数学边界检验协议，不是论文数据或现场数据。", "", "| 顺序 | 实际 x1 | 实际 x2 | 实际 f1 | 实际 f2 |", "|---|---:|---:|---:|---:|"]
        lines += [f"| {'→'.join(c['order'])} | {c['solution_vector'][0]:g} | {c['solution_vector'][1]:g} | {c['objectives']['f1']:g} | {c['objectives']['f2']:g} |" for c in published]
        lines += ["", "| ε | 实际 x1 | 实际 x2 | 实际 f1 | 实际 f2 |", "|---:|---:|---:|---:|---:|"]
        lines += [f"| {c['epsilon']:g} | {c['solution_vector'][0]:g} | {c['solution_vector'][1]:g} | {c['objectives']['f1']:g} | {c['objectives']['f2']:g} |" for c in epsilon_cases]
        lines += ["", f"共 {len(stage_records)} 次小 LP：22 次 status=0，预声明负例（资源180、ε184）status=2 且无解向量；无第二阶段。5 个 ε 点均唯一且互不支配。数值全矩阵审计和独立有理数多边形解析界核对全部通过。求解累计 {summary['solver_seconds_total']:.6f} 秒；每阶段时间/状态/界见 stages.csv。", "", "LP 未暴露的 mip_dual_bound/mip_gap 保留 null，不虚报为零；独立解析最优界另列，不冒称求解器给出的界。所有变量边界、完整矩阵、逐行活动/松弛、实际向量和目标重算保存在每阶段 JSON。容差和10秒时限是数值协议，非原文参数。", "", f"最大行违反 {summary['max_matrix_row_violation']:.3g}，最大变量界违反 {summary['max_matrix_bound_violation']:.3g}，最大目标重算误差 {summary['max_objective_recompute_error']:.3g}。源文件/输入/协议/执行脚本/数值核心/测试文件均有 SHA256 快照，开始和结束身份相等；complete manifest 最后写入。", "", "![数学算例前沿与边界检验](published_frontier.png)", "", "此证据不证明建筑 RC 参数、工程费用/碳排改善、完整年季可行性或现场预测精度，也不改变任何供热情景数据准入状态。"]
        with (directory / "report.md").open("x", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        artifact_hashes = {path.relative_to(directory).as_posix(): sha256(path)
                           for path in sorted(directory.rglob("*")) if path.is_file()}
        write_json(directory / "manifest.json", {"complete": True, "completed_utc": utc_now(),
                   "checks": checks, "artifact_sha256": artifact_hashes, "identities": identities,
                   "source_page_mapping": {"section_4_1": 6, "table_2_and_figure_2": 7, "four_intervals": 8, "figure_4": 9}, **FLAGS})
        return clean(summary)
    except Exception as error:
        write_json(directory / "failure.json", {"complete": False, "failed_utc": utc_now(),
                   "error": str(error), "traceback": traceback.format_exc(), **FLAGS})
        raise


def verify_run(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    checks = {"complete": manifest.get("complete") is True,
              "evidence_flags_preserved": all(manifest.get(k) == v for k, v in FLAGS.items())}
    for relative, digest in manifest["artifact_sha256"].items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Manifest path escapes run directory")
        checks[relative] = path.is_file() and sha256(path) == digest
    return {"verified": all(checks.values()), "checked_file_count": len(manifest["artifact_sha256"]),
            "failed_checks": [key for key, value in checks.items() if not value], **FLAGS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--run-id", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_experiment(args.run_id) if args.command == "run" else verify_run(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    if args.command == "verify" and not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
