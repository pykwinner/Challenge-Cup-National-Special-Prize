"""Actual solves under a new quality-metadata epoch on admitted public TOU data.

Existing-assets optimum, fixed-plan replay and a two-stage emissions endpoint.
No investment benefit, field validation, or old protocol coverage is implied.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from run_evidence_case_experiments import (ROOT, chronology_check, code_identity,
    freeze_investment_plan, load_inputs, run_one, save, sha, verify_admission)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        parser.error("output exists; choose a new epoch directory")
    config_path = ROOT / "configs/evidence_case_20261002/wuhan_bestest600_rc_tou.yaml"
    series_path = ROOT / "data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv"
    admission_path = ROOT / "docs/implementation_20261002/data/admission_tou.json"
    config, frame = load_inputs(config_path, series_path)
    verify_admission(admission_path, config_path, series_path, rc=True)
    chronology = chronology_check(frame, config, "2024-11-16T00:00:00+08:00", "2025-03-16T00:00:00+08:00")
    def code():
        identities = code_identity()
        identities[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
        identities["src/heatplan/solution_quality.py"] = sha(ROOT/"src/heatplan/solution_quality.py")
        return identities
    codes = code()
    out.mkdir(parents=True)
    save(out/"protocol.json", {"epoch": "11.12.0rc2_solution_quality_examples",
        "frozen_utc":datetime.now(timezone.utc).isoformat(), "chronology":chronology,
        "code_sha256":codes, "input_sha256":{str(p.relative_to(ROOT)):sha(p) for p in [config_path,series_path,admission_path]},
        "time_limit_per_stage_seconds":120., "mip_gap_target":.0001,
        "runs":["existing_assets_cost","fixed_plan_replay","emission_endpoint"], "solver_stages":4,
        "scope":"admitted public ideal RC prototype electricity bill/average-factor CO2; new metadata epoch",
        "full_economics_admitted":False,"empirical_validation":False,"old_protocol_covers_new_source":False})
    baseline,_ = run_one("existing_assets_cost",config,frame,out,120.,.0001)
    fixed = freeze_investment_plan(config,baseline,relax_reliability=False)
    replay,_ = run_one("fixed_plan_replay",fixed,frame,out,120.,.0001)
    endpoint,_ = run_one("emission_endpoint",config,frame,out,120.,.0001,primary="emissions")
    rows=[]
    for name, result in [("existing_assets_cost",baseline),("fixed_plan_replay",replay),("emission_endpoint",endpoint)]:
        q=result.summary["solution_quality"]
        rows.append({"name":name,"solution_scope":result.summary["solution_scope"],"quality":q["quality"],
            "lexicographic_claim":q["lexicographic_claim"],"solver_stages":q["solver_stages"],
            "cash_cost_KPI_interval":q["cash_cost_KPI_interval"],"carbon_KPI_interval":q["carbon_KPI_interval"]})
    save(out/"quality_examples.json",rows)
    save(out/"manifest.json",{"code_stable_during_run":code()==codes,
        "all_run_one_audits_passed":True,"old_protocol_covers_new_source":False,
        "files":{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob("*")) if p.is_file()}})
    print([{r["name"]:(r["solution_scope"],r["quality"],r["lexicographic_claim"])} for r in rows])


if __name__ == "__main__":
    main()
