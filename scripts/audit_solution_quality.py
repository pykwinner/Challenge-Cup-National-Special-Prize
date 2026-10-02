"""Read-only historical summary audit; write new sidecars, never old manifests.

This does not rerun, re-certify physics or infer KPI uncertainty from MIP gap.
Missing stages/scope/tolerances remain missing. Inaccessible inventory is logged.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from heatplan.solution_quality import finite_number, solution_quality_from_summary

DEFAULT_ROOTS = [ROOT / "outputs", ROOT / "docs/implementation_20261002/reference_experiments",
    ROOT / "docs/implementation_20261002/reference_experiments_tou",
    ROOT / "docs/implementation_20261002/frontier_comparison/weighted_points"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def infer_scope(config):
    """Historical config declaration only; absent config cannot reveal investments."""
    if not isinstance(config, dict) or not all(k in config for k in ("planning", "sources", "nodes", "pipes", "storages")):
        return {"investment_scope": "unknown_investment_scope", "scope_evidence": "no complete sibling effective configuration"}
    frozen = bool(config.get("fixed_investment_accounting") or config.get("replay_service_policy"))
    def candidates(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.startswith(("max_new_", "max_retire_")) and (finite_number(item) or 0) > 0:
                    return True
                if key == "diameter_options" and item:
                    return True
                if key in ("temperature_retrofit", "retrofit") and isinstance(item, dict) and "fixed_value" not in item:
                    return True
                if candidates(item):
                    return True
        elif isinstance(value, list):
            return any(candidates(item) for item in value)
        return False
    free = candidates(config)
    scope = "mixed_fixed_and_free_investment_dispatch" if frozen and free else "fixed_investment_dispatch" if frozen else "joint_investment_dispatch" if free else "existing_assets_dispatch"
    return {"investment_scope": scope, "scope_evidence": "sibling effective configuration declarations; historical variable bounds not reconstructed",
            "fixed_plan_marker_present": frozen, "free_candidate_declarations_present": free}


def audit(roots, out):
    if out.exists():
        raise ValueError("output exists; select a new sidecar directory")
    paths, inventory_errors, skipped = set(), [], []
    for root in roots:
        if not root.is_dir():
            inventory_errors.append({"path": str(root), "reason": "input root missing or inaccessible"})
            continue
        for current, directories, files in os.walk(root, onerror=lambda e: inventory_errors.append({"path": e.filename, "reason": str(e)})):
            directories[:] = [name for name in directories if name != "__pycache__"]
            for name in files:
                if name.endswith("summary.json") or name == "failure.json":
                    paths.add((Path(current)/name).resolve())
    identities, records = {}, []
    for path in sorted(paths):
        try:
            identities[str(path)] = sha(path)
            summary = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                skipped.append({"path": str(path), "reason": "not a flat summary object"})
                continue
            if path.name == "failure.json":
                if any(p.parent == path.parent and p.name.endswith("summary.json") for p in paths):
                    skipped.append({"path": str(path), "reason": "failure attached to sibling summary; counted once"})
                    continue
                summary = {"failure_reason": summary.get("reason", "recorded failure"), "solver_status": summary.get("solver_status")}
            elif "solver_status" not in summary and "solver_stages" not in summary:
                skipped.append({"path": str(path), "reason": "aggregate/preflight artifact, not a solver result"})
                continue
            else:
                summary = dict(summary)
                if path.parent / "failure.json" in paths:
                    failure = json.loads((path.parent/"failure.json").read_text(encoding="utf-8"))
                    summary["failure_reason"] = failure.get("reason", "recorded run failure")
            config_path = path.parent / "effective_config.yaml"
            config = None
            if config_path.is_file():
                identities[str(config_path)] = sha(config_path)
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            scope = summary.get("solution_quality", {}).get("scope") or infer_scope(config)
            # Only a matching frozen manifest + protocol can supply a missing
            # historical solve target. A present config default is insufficient.
            tolerance_source = None
            for directory in [path.parent, *list(path.parents)[1:5]]:
                protocol_path, manifest_path = directory/"protocol.json", directory/"manifest.json"
                if protocol_path.is_file() and manifest_path.is_file():
                    identities[str(protocol_path)], identities[str(manifest_path)] = sha(protocol_path), sha(manifest_path)
                    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    rel = str(path.relative_to(directory))
                    hashes = manifest.get("files", {})
                    match = hashes.get(rel, hashes.get(rel.replace("\\", "/"))) == identities[str(path)]
                    target = finite_number(protocol.get("mip_gap_target"))
                    if match and target is not None:
                        summary.setdefault("solver_requested_mip_gap", target)
                        summary.setdefault("solver_time_limit_seconds", protocol.get("time_limit_per_stage_seconds"))
                        tolerance_source = str(protocol_path)
                    break
            quality = solution_quality_from_summary(summary, scope=scope)
            records.append({"path": str(path), "summary_sha256": identities[str(path)],
                "historical": True, "recomputed_solver_or_physics": False,
                "registered_tolerance_source": tolerance_source, "properties": quality})
        except (OSError, ValueError, TypeError) as error:
            records.append({"path": str(path), "summary_sha256": identities.get(str(path)),
                "read_error": str(error), "properties": solution_quality_from_summary({})})
    changed = [path for path, digest in identities.items() if sha(path) != digest]
    if changed:
        raise RuntimeError(f"input files changed during read-only audit: {changed}")
    out.mkdir(parents=True)
    save(out/"summary_properties.json", records)
    table, stage_table = [], []
    for record in records:
        q = record["properties"]
        table.append({"source_summary":record["path"], "summary_sha256":record["summary_sha256"],
            "quality":q["quality"], "investment_scope":q["scope"]["investment_scope"],
            "scope_evidence":q["scope"]["scope_evidence"], "feasible_incumbent_audited":q["feasible_incumbent_audited"],
            "fixed_or_existing_assets_feasible_operation":q["fixed_or_existing_assets_feasible_operation"],
            "lexicographic_claim":q["lexicographic_claim"], "solver_stages":len(q["solver_stages"]),
            "strict_global_lexicographic_optimum_claimed":False, "read_error":record.get("read_error")})
        for stage in q["solver_stages"]:
            stage_table.append({"source_summary":record["path"], **{k:stage[k] for k in
                ("stage_id","quality","objective_mode","objective_unit","objective_value","mip_dual_bound","mip_gap",
                 "solver_status","solver_message","solver_requested_mip_gap","solver_time_limit_seconds","solver_elapsed_seconds")},
                "same_objective_interval_lower":stage["objective_interval"]["lower"],
                "same_objective_interval_upper":stage["objective_interval"]["upper"],
                "interval_status":stage["objective_interval"]["status"]})
    pd.DataFrame(table).to_csv(out/"summary_properties.csv",index=False)
    pd.DataFrame(stage_table).to_csv(out/"stage_properties.csv",index=False)
    save(out/"inventory_errors.json", inventory_errors)
    save(out/"skipped_non_solver_artifacts.json", skipped)
    report = {"schema_version":"heatplan.historical_quality_sidecar.v1", "created_utc":datetime.now(timezone.utc).isoformat(),
        "source_roots":[str(r) for r in roots], "audited_result_records":len(records),
        "quality_counts":dict(Counter(r["properties"]["quality"] for r in records)),
        "investment_scope_counts":dict(Counter(r["properties"]["scope"]["investment_scope"] for r in records)),
        "inventory_errors":len(inventory_errors), "skipped_non_solver_artifacts":len(skipped),
        "read_only_input_hash_check_passed":not changed, "source_sha256":identities,
        "code_sha256":{str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),ROOT/"src/heatplan/solution_quality.py"]},
        "not_claimed":["re-solved historical optimization", "re-certified physics", "missing stage recovered", "KPI intervals inferred from gap", "old frozen epoch updated"]}
    save(out/"audit_report.json",report)
    save(out/"manifest.json", {"files":{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob("*")) if p.is_file()}})
    print(json.dumps({key:report[key] for key in ["audited_result_records","quality_counts","investment_scope_counts","inventory_errors","read_only_input_hash_check_passed"]},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",action="append",type=Path)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    audit([p.resolve() for p in (args.root or DEFAULT_ROOTS)],args.out.resolve())


if __name__ == "__main__":
    main()
