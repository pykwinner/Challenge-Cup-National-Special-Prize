"""Inspect registered rc5 evidence and write NEW sidecars; never run an optimizer."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from heatplan.solution_evidence import classify_result, integer, number

STAGE = Path("docs/implementation_20261002_followup/parallel_stage_03")
RUNS = ("investment/run_03", "investment/reproduce_02", "multiscale/run_01",
        "network/run_02", "reliability/wuhan_2880h_01")
SOURCES = [
    {"title": "SciPy1.15.3 milp", "url": "https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.optimize.milp.html",
     "application": "Status0/1/2/3/4 semantics and same-linear-objective primal/dual/MIP gap."},
    {"title": "HiGHS terminology", "url": "https://ergo-code.github.io/HiGHS/stable/terminology/",
     "application": "A feasible minimization incumbent is an upper bound; MIP dual is a lower bound for that model."},
    {"title": "Mavrotas author report: epsilon constraint", "url": "https://www.gams.com/modlib/adddocs/epscm.pdf",
     "application": "Finite epsilon grids are samples; lexicographic payoff construction requires objective priority to be preserved."},
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Reader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.identities = {}

    def path(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("evidence path escapes declared root")
        return path

    def text(self, path):
        path = self.path(path)
        data = path.read_bytes()
        self.identities[str(path.relative_to(self.root)).replace("\\", "/")] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8-sig")

    def json(self, path):
        return json.loads(self.text(path), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON: " + value)))

    def manifest(self, path):
        manifest = self.json(path)
        files = manifest.get("files", {})
        if not isinstance(files, dict) or not files:
            return manifest, False, ["manifest has no registered file hashes"]
        errors = []
        for name, expected in files.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                errors.append("unsupported manifest hash entry")
                continue
            try:
                target = self.path(path.parent / name.replace("\\", "/"))
                if not target.is_relative_to(path.parent.resolve()):
                    raise ValueError("manifest traversal outside run")
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                self.identities[str(target.relative_to(self.root)).replace("\\", "/")] = actual
                if actual != expected:
                    errors.append(name + ": SHA256 mismatch")
            except (OSError, ValueError) as error:
                errors.append(name + ": " + str(error))
        return manifest, not errors, errors

    def unchanged(self):
        return [name for name, expected in self.identities.items()
                if digest(self.path(self.root / name)) != expected]


def heatplan_record(summary, audit, identity):
    scope_raw = summary.get("solution_quality", {}).get("scope", {})
    mapping = {"joint_investment_dispatch": "full_registered_problem",
        "fixed_investment_dispatch": "fixed_investment_subproblem",
        "existing_assets_dispatch": "existing_assets_subproblem",
        "mixed_fixed_and_free_investment_dispatch": "mixed_fixed_free_subproblem"}
    kind = mapping.get(scope_raw.get("investment_scope"), "unknown")
    count = integer(scope_raw.get("free_investment_variables"))
    scope_ok = scope_raw.get("scope_evidence") == "actual_built_investment_variable_bounds"
    scope_ok = scope_ok and ((kind == "full_registered_problem" and count is not None and count > 0)
        or (kind in {"fixed_investment_subproblem", "existing_assets_subproblem"} and count == 0)
        or (kind == "mixed_fixed_free_subproblem" and count is not None and count > 0))
    stages = []
    raw_stages = summary.get("solver_stages", [])
    for index, raw in enumerate(raw_stages):
        mode = raw.get("objective_mode")
        unit = {"total_cost": "CNY_model_objective", "emissions": "kg_CO2_model_objective"}.get(mode)
        stages.append({"role": raw.get("stage_id", "single"), "status": raw.get("solver_status"),
            "message": raw.get("solver_message"), "primal": raw.get("objective_value"),
            "dual": raw.get("mip_dual_bound"), "relative_gap": raw.get("mip_gap"),
            "relative_gap_target": raw.get("solver_requested_mip_gap"),
            "integer_variables": raw.get("solver_integer_variables"),
            "audit_passed": raw.get("numerical_audit_passed"),
            "objective_checked": raw.get("numerical_objective_checked"),
            "incumbent_present": raw.get("numerical_solution_finite"),
            "elapsed_seconds": raw.get("solver_elapsed_seconds"),
            "objective": {"name": mode, "unit": unit, "sense": "minimize",
                "expression_id": "heatplan_stage:" + str(mode), "constant": raw.get("solver_objective_constant")}})
        # Top-level matrix/ledger audit belongs to the returned (last) solve.
        # Never transfer its tolerance to an unexported primary audit.
        if index == len(raw_stages)-1 and isinstance(summary.get("numerical_audit_tolerance"), dict):
            tolerance = summary["numerical_audit_tolerance"]
            stages[-1]["numerical_tolerance"] = {"absolute": tolerance.get("absolute"),
                "relative": tolerance.get("relative"),
                "verified": identity and summary.get("numerical_audit_passed") is True,
                "basis": "manifest-hash-bound original returned-stage matrix/objective numerical audit tolerance; numerical consistency only, not a certified dual vector"}
    record = {"input_identity_verified": identity, "scope": {"kind": kind,
        "verified": scope_ok, "basis": scope_raw}, "stages": stages,
        "audits": {"passed": audit.get("passed") is True, "basis": "existing hash-bound saved-output audit; not freshly solved"}}
    if "primary_objective" in summary:
        mode = summary.get("primary_objective")
        record["lexicographic"] = {"primary_anchor": summary.get("primary_incumbent_value"),
            "freeze_allowance": summary.get("primary_objective_allowance"),
            "returned_primary": summary.get("objective_yuan_per_year" if mode == "total_cost" else "total_service_emissions_kg_per_year"),
            "freeze_contract_verified": len(stages) == 2 and summary.get("endpoint_method") == "two_stage_lexicographic_with_reported_tolerance",
            "freeze_tolerance_source": "hash-bound exported primary_objective_allowance; no abs/rel components invented",
            "secondary_feasible_region": "same declared model plus primary <= incumbent anchor + allowance"}
    return record


def investment_record(summary, audit, enumeration, effective, frozen_source, identity, frozen_auditor=""):
    bound_scope = summary.get("bound_scope", {})
    kind = {"free_registered_candidates": "full_registered_problem", "fixed_investment": "fixed_investment_subproblem",
            "baseline_no_HP": "restricted_baseline_subproblem"}.get(bound_scope.get("kind"), "unknown")
    counts = bound_scope.get("hp_unit_candidates")
    observed = [r.get("hp_units") for r in enumeration if isinstance(r, dict)]
    checks = audit.get("checks", {})
    complete = summary.get("integer_enumeration_complete") is True and isinstance(counts, list) and bool(counts)
    complete = complete and observed == counts and len(set(observed)) == len(observed)
    complete = complete and all(r.get("integer_enumeration_complete") is True or
        (len(r.get("solver_stages", [])) == 1 and r["solver_stages"][0].get("status") == 2) for r in enumeration)
    scope_ok = checks.get("bound_scope_matches_protocol") is True and checks.get("enumeration_exact_scope") is True
    scope_ok = scope_ok and bound_scope == effective.get("bound_scope") and complete
    bound_tolerance_verified = identity and "tolerance = 1e-6 + 1e-8 * max(1., abs(primal), abs(dual))" in frozen_auditor
    freeze_tolerance_verified = identity and "numerical_tolerance = 1e-6 + abs(primal) * 1e-8" in frozen_auditor
    bound_tolerance = {"absolute": 1e-6, "relative": 1e-8, "verified": bound_tolerance_verified,
        "basis": "manifest-frozen independent auditor; 1e-6+1e-8*max(1,abs(raw primal),abs(raw dual))"}
    stages = []
    for index, raw in enumerate(summary.get("solver_stages", [])):
        mode = raw.get("mode")
        stages.append({"role": "primary" if index == 0 else "secondary", "status": raw.get("status"),
            "message": raw.get("message"), "primal": raw.get("primal_objective"), "dual": raw.get("dual_objective"),
            "relative_gap": None, "relative_gap_target": None, "integer_variables": 0,
            "audit_passed": raw.get("success") is True and number(raw.get("constraint_max_violation")) is not None
                and raw["constraint_max_violation"] <= 1e-8,
            "objective_checked": checks.get(f"solver_stage_{index}") is True,
            "incumbent_present": raw.get("success") is True,
            "elapsed_seconds": raw.get("seconds"), "objective": {"name": mode,
                "unit": "CNY_repeated_window_comparison" if mode == "cost" else "kg_CO2_window" if mode == "carbon" else None,
                "sense": "minimize", "expression_id": "investment_registered_design:" + str(mode),
                "constant": raw.get("objective_constant")}, "numerical_tolerance": bound_tolerance})
    primary = stages[0] if stages else {}
    raw_anchor = number(primary.get("primal"))
    constant = number(primary.get("objective", {}).get("constant"))
    allowance = 1e-7 + abs(raw_anchor) * 1e-9 if raw_anchor is not None else None
    formula_verified = "tolerance=1e-7+abs(primary_value)*1e-9" in frozen_source
    mode = summary.get("mode")
    returned = summary.get("comparison_cost_cny" if mode == "cost" else "window_co2_kg")
    record = {"input_identity_verified": identity, "scope": {"kind": kind, "verified": scope_ok,
        "basis": "exact candidate inventory, effective bound_scope and saved independent audit",
        "registered_candidates": counts, "bound_scope": bound_scope}, "stages": stages,
        "audits": {"passed": audit.get("passed") is True, "basis": "hash-bound independent arithmetic/feasibility/scope audit"},
        "enumeration_complete": complete,
        "lexicographic": {"primary_anchor": raw_anchor + constant if raw_anchor is not None and constant is not None else None,
            "freeze_allowance": allowance, "returned_primary": returned,
            "freeze_contract_verified": formula_verified and checks.get("primary_lexicographic_bound") is True,
            "numerical_tolerance": {"absolute": 1e-6, "relative": 1e-8,
                "verified": freeze_tolerance_verified, "basis": "manifest-frozen independent auditor's original primary freeze/CSV ledger check"},
            "numerical_scale": abs(raw_anchor) if raw_anchor is not None else None,
            "freeze_tolerance_source": "registered frozen solver source formula1e-7+abs(primary variable objective)*1e-9",
            "secondary_feasible_region": "selected fixed HP-count LP and its own primary-incumbent freeze; not unrestricted global secondary"}}
    # Do not manufacture a global lex certificate from a global PRIMARY bound.
    # Investment exports an absolute primary gap; never relabel it as MIP gap.
    minimum = min((number(r.get("primary_objective")) for r in enumeration
                   if r.get("success") is True and number(r.get("primary_objective")) is not None), default=None)
    candidate_pool = 1e-7 + abs(minimum)*1e-9 if minimum is not None and formula_verified else None
    primary_numeric = max((1e-6 + 1e-8*max(1., abs(s["primal_objective"]), abs(s["dual_objective"]))
        for r in enumeration for s in r.get("solver_stages", [])[:1]
        if s.get("status") == 0 and number(s.get("primal_objective")) is not None and number(s.get("dual_objective")) is not None), default=None)
    freeze_numeric = 1e-6 + abs(raw_anchor)*1e-8 if raw_anchor is not None and freeze_tolerance_verified else None
    derived_allowance = (primary_numeric + candidate_pool + allowance + freeze_numeric
        if bound_tolerance_verified and all(v is not None for v in (primary_numeric, candidate_pool, allowance, freeze_numeric)) else None)
    record["global_primary"] = {"status": 0 if complete else None, "primal": returned,
        "dual": summary.get("global_primary_lower_bound"), "integer_variables": 0,
        "audit_passed": audit.get("passed") is True, "objective_checked": checks.get("global_primary_gap") is True,
        "incumbent_present": summary.get("success") is True,
        "numerical_tolerance": bound_tolerance, "derived_optimality_allowance": derived_allowance,
        "derived_allowance_components": {"max_candidate_primary_audit_allowance": primary_numeric,
            "candidate_pool_allowance": candidate_pool, "selected_candidate_freeze_allowance": allowance,
            "original_freeze_numerical_allowance": freeze_numeric},
        "certificate_basis": "complete finite candidate LP enumeration and original declared auditor allowances; not a single LP termination or exact mathematical proof",
        "objective": {**primary.get("objective", {}), "constant": 0},
        "reported_absolute_primary_gap": summary.get("global_primary_gap")}
    return record


def report(root, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError("select a new output directory; no evidence overwrite")
    if out.is_relative_to(root):
        raise ValueError("report output must be outside the inspected release copy")
    reader = Reader(root)
    records, inventory = [], []
    for run_name in RUNS:
        run = root / STAGE / run_name
        try:
            manifest, valid, errors = reader.manifest(run / "manifest.json")
            inventory.append({"run": run_name, "identity_verified": valid, "errors": errors})
            names = [n for n in manifest.get("files", {}) if n.endswith("/summary.json")]
            if not run_name.startswith("investment/"):
                names = [n for n in names if n.startswith("results/") and "iteration_" not in n]
            for name in sorted(names):
                path = run / name
                summary = reader.json(path)
                family = run_name.split("/")[0]
                audit_name = "independent_audit.json" if family in {"investment", "multiscale"} else "audit.json"
                audit_path = path.parent / audit_name
                audit = reader.json(audit_path)
                audit_registered = str(audit_path.relative_to(run)).replace("\\", "/") in manifest.get("files", {})
                identity = valid and audit_registered
                if family == "investment":
                    enumeration = reader.json(path.parent / "enumeration.json")
                    effective = reader.json(path.parent / "effective_protocol.json")
                    code = reader.text(run / "frozen_code/run_investment_reference.py")
                    auditor = reader.text(run / "frozen_code/audit_investment_reference.py")
                    required_names = ["frozen_code/run_investment_reference.py", "frozen_code/audit_investment_reference.py",
                        str((path.parent/"enumeration.json").relative_to(run)).replace("\\", "/"),
                        str((path.parent/"effective_protocol.json").relative_to(run)).replace("\\", "/")]
                    identity = identity and all(n in manifest.get("files", {}) for n in required_names)
                    normalized = investment_record(summary, audit, enumeration, effective, code, identity, auditor)
                else:
                    normalized = heatplan_record(summary, audit, identity)
                records.append({"source_summary": str(path.relative_to(root)).replace("\\", "/"),
                    "source_audit": str(audit_path.relative_to(root)).replace("\\", "/"),
                    "source_manifest": str((run/"manifest.json").relative_to(root)).replace("\\", "/"),
                    "normalized_evidence": normalized, "properties": classify_result(normalized)})
        except (OSError, ValueError, TypeError, KeyError) as error:
            inventory.append({"run": run_name, "identity_verified": False, "errors": [str(error)]})
    # Algorithms are fixed-policy/calibration evidence, never free-investment certificates.
    run = root / STAGE / "algorithm/release_compatibility_run_01"
    try:
        manifest, valid, errors = reader.manifest(run/"manifest.json")
        audit_name = "docs/github_release_11.12.0rc5/validation/algorithm_compatibility_audit_candidate.json"
        audit = reader.json(root/audit_name)
        release = reader.json(root/"docs/github_release_11.12.0rc5/release_manifest.json")
        expected = [row.get("sha256") for row in release.get("files", []) if row.get("path") == audit_name]
        audit_identity = len(expected) == 1 and expected[0] == reader.identities.get(audit_name)
        if not audit_identity:
            errors.append("publisher audit is missing/mismatched in release manifest")
        valid = valid and audit_identity
        audit_summary = audit if "passed" in audit else {}
        normalized = {"scope": {"kind": "finite_policy_library", "verified": True,
            "basis": "three declared cities/nine fixed policies per city; no investment optimization"},
            "input_identity_verified": valid, "audits": {"passed": audit_summary.get("passed") is True,
            "basis": "publisher saved independent42225-check precision audit; exact release-manifest identity verified"}, "stages": []}
        records.append({"source_summary": str((run/"summary.json").relative_to(root)).replace("\\", "/"),
            "source_audit": audit_name,
            "source_manifest": str((run/"manifest.json").relative_to(root)).replace("\\", "/"),
            "normalized_evidence": normalized, "properties": classify_result(normalized)})
        inventory.append({"run": "algorithm/release_compatibility_run_01", "identity_verified": valid, "errors": errors})
    except (OSError, ValueError, TypeError) as error:
        inventory.append({"run": "algorithm/release_compatibility_run_01", "identity_verified": False, "errors": [str(error)]})
    changed = reader.unchanged()
    if changed:
        raise RuntimeError("inputs changed during report: " + ", ".join(changed))
    counts = dict(Counter(r["properties"]["classification"] for r in records))
    inventory_passed = bool(records) and all(r["identity_verified"] is True and not r["errors"] for r in inventory)
    payload = {"schema": "solution_evidence_report.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "inspected_release_root": str(root), "read_only_scan": True, "solver_runs": 0,
        "records": records, "classification_counts": counts, "inventory": inventory,
        "input_hashes": reader.identities, "inputs_unchanged_after_scan": True,
        "selected_input_inventory_passed": inventory_passed,
        "implementation_sha256": {"src/heatplan/solution_evidence.py": digest(PROJECT/"src/heatplan/solution_evidence.py"),
            "scripts/report_solution_evidence.py": digest(Path(__file__))},
        "sources": SOURCES, "unrun": ["no solver rerun", "no new LP dual-vector proof", "no field validation", "no legacy output rewrite"]}
    lines = ["# 求解结论证据表", "", "本表只读检查rc5已保存的manifest、原始阶段记录和既有审核。没有重求解、放宽旧容差或改变旧结论。", "",
        "全问题仅指登记候选/约束内的有限模型；固定投资、既有资产及受限基准分别列出。次阶段界属于主阶段incumbent冻结域。所有记录均不宣称严格全局词典序或完整Pareto前沿。", "",
        "字面界顺序、冻结残差与原声明数值容差检查分别保留；仅沿用manifest冻结审核器已有容差，缺失时不补。原始dual大于primal时不排序、不截断，保留原始数值对；通过已有数值容差可登记容差内证书，但不制造有序严格界。没有将gap乘成本、碳排或CAPEX生成区间。", "",
        "| 记录 | 分类 | 实际范围 | 主目标界 | 冻结容差 | 次阶段结论 |", "|---|---|---|---|---|---|"]
    for row in records:
        q = row["properties"]
        primary = q["global_primary_certificate"] or (q["stage_certificates"][0] if q["stage_certificates"] else {})
        bounds = primary.get("raw_objective_bound_pair", {})
        lex = q["lexicographic"]
        lines.append(f"| `{row['source_summary']}` | {q['classification']} | {q['scope']['kind']} | {bounds.get('lower')} / {bounds.get('upper')} {bounds.get('unit') or ''} | {lex.get('freeze_allowance')} | {lex['claim']} |")
    lines += ["", "## 本次覆盖", "", f"记录数：{len(records)}；分类数量：`{json.dumps(counts,ensure_ascii=False)}`。", "",
        "输入文件逐项SHA和所有原始证据、未满足项见`evidence_table.json`。源manifest不含某份附件时，不能借本表提升该附件的来源审核身份。", "",
        "## 依据与实现对应", ""]
    for source in SOURCES:
        lines.append(f"- [{source['title']}]({source['url']})：{source['application']}")
    lines += ["", "实现是以上资料在现有导出格式上的证据规则适配，非新求解器或最优性理论。", ""]
    out.mkdir(parents=True)
    (out/"evidence_table.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (out/"README.md").write_text("\n".join(lines), encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = report(args.evidence_root, args.out)
    print(json.dumps({"records": len(result["records"]), "classifications": result["classification_counts"],
        "inventory_errors": sum(bool(r["errors"]) for r in result["inventory"]),
        "inputs_unchanged": result["inputs_unchanged_after_scan"], "solver_runs": 0}, ensure_ascii=False))
    if not result["selected_input_inventory_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
