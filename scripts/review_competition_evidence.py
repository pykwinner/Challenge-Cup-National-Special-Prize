"""Review completed annual/season evidence without running an optimizer.

Recomputes RC and per-source ground ledgers from hash-verified saved inputs.
This is computational verification, not validation against measured buildings.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from heatplan.building_audit import audit_building_trajectory
from package_competition_release import checked_manifest, digest, inside


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def review(root: Path, run: Path, plan: Path, frozen_source: Path) -> dict:
    root, run, plan, frozen_source = [p.resolve() for p in (root, run, plan, frozen_source)]
    m, pm = checked_manifest(run), checked_manifest(plan)
    checked_manifest(frozen_source)
    if m.get("scope") not in {"annual", "season"}:
        raise ValueError("only complete annual/season fixed-investment runs are reviewed")
    s, cfg = read_json(run/"summary.json"), read_json(run/"model_config.json")
    ps = read_json(plan/"summary.json")
    frame = pd.read_csv(run/"model_timeseries.csv", float_precision="round_trip")
    trajectory = pd.read_csv(run/"building_trajectory.csv", float_precision="round_trip")
    dispatch = pd.read_csv(run/"dispatch.csv", float_precision="round_trip")
    checks: dict[str, bool] = {}
    checks["same_investment"] = m.get("parent_investment_sha256") == pm.get("investment_sha256") and bool(pm.get("investment_sha256"))
    checks["same_service_contract"] = m["service_contract_sha256"] == pm["service_contract_sha256"]
    checks["same_code_epoch"] = m["code_sha256"] == pm["code_sha256"]
    checks["frozen_code_bytes"] = all(digest(inside(frozen_source, path)) == value for path, value in m["code_sha256"].items())
    checks["source_bytes"] = all(digest(inside(root, path)) == value for path, value in m["source_sha256"].items())
    anchor = inside(root, m["c6_input_anchor_file"])
    checks["presolve_anchor_bytes"] = digest(anchor) == m["c6_input_anchor_sha256"]
    checks["strict_status_and_gap"] = (m.get("is_strict_formulation") is True and s["solver_status"] == 0
        and np.isfinite(s["mip_gap"]) and s["mip_gap"] <= m["requested_mip_gap"] + 1e-9)
    checks["original_runner_audits"] = bool(s.get("runner_audit")) and all(s["runner_audit"].values())
    checks["original_independent_audit"] = read_json(run/"c6_building_audit.json")["passed"] is True
    # CSV byte identity is established above; no claim that a post-solve CSV hash
    # equals the pre-solve in-memory typed-frame anchor is made.
    rc = audit_building_trajectory(cfg, frame, trajectory, dispatch=dispatch, summary=s, require_continuous_fixed=True)
    checks["fresh_serialized_rc_audit"] = rc["passed"] is True
    times = pd.to_datetime(frame["time"], utc=True)
    checks["continuous_hourly"] = len(times) > 1 and bool((times.diff().iloc[1:] == pd.Timedelta(hours=1)).all())
    checks["unit_period_weights"] = bool(np.allclose(frame["period_weight"], 1., atol=0., rtol=0.))
    if m["scope"] == "annual":
        declared = cfg["planning"]["annual_scope"]
        expected = pd.date_range(pd.Timestamp(declared["start_inclusive"]).tz_convert("UTC"),
            pd.Timestamp(declared["end_exclusive"]).tz_convert("UTC"), freq="h", inclusive="left")
        checks["declared_annual_clock"] = len(expected) == len(times) and bool(np.all(expected == times))
    else:
        declared = read_json(root/"data/competition_20261001/service_scope.json")
        seasonal = pd.read_csv(root/"data/competition_20261001/wuhan_heating_season_2023_2024_utc.csv")
        expected = pd.to_datetime(seasonal["time"], utc=True)
        checks["declared_season_clock"] = len(expected) == len(times) and bool(np.all(expected == times))

    dt = float(cfg["planning"].get("time_step_hours", 1.))
    weights = frame["period_weight"].to_numpy(float) * dt
    ground = []
    sources = {x["name"]: x for x in cfg["sources"]}
    grades = {x["name"]: x for x in cfg["grades"]}
    cooling = cfg.get("multi_energy_services", {}).get("cooling", {}).get("sources", {})
    for name, spec in cooling.items():
        balance = spec.get("ground_thermal_balance")
        if not balance:
            continue
        source = sources[name]
        # This reviewed case has constant ground temperature; fail rather than
        # silently apply this simple recomputation to a different COP model.
        if source.get("source_temp_column") or source.get("defrost_temperature_band_c"):
            raise ValueError("independent ground ledger requires constant ground temperature without defrost")
        extraction = 0.
        for grade, rows in dispatch.loc[(dispatch.category == "source_heat") & (dispatch.asset == name)].groupby("grade"):
            tk = float(grades[grade]["temperature_c"]) + 273.15
            lift = max(float(grades[grade]["temperature_c"]) - float(source["source_temp_c"]), 1.)
            cop = np.clip(float(source.get("carnot_efficiency", .45))*tk/lift, source.get("cop_min", 1.2), source.get("cop_max", 6.))
            extraction += float(np.dot(rows.value, weights[rows.time_index.to_numpy(int)])) * (1.-1./cop)
        rows = dispatch.loc[(dispatch.category == "cooling_output") & (dispatch.asset == name)]
        idx = rows.time_index.to_numpy(int)
        outdoor = frame.outdoor_temp.to_numpy(float)[idx]
        eer = np.clip(float(spec["eer"]) - float(spec.get("eer_loss_per_c_above_reference", 0.))
            * np.maximum(outdoor - float(spec.get("reference_outdoor_temp_c", 35.)), 0.), spec.get("eer_min", 1.), spec.get("eer_max", np.inf))
        rejection = float(np.dot(rows.value.to_numpy(float)*(1.+1./eer), weights[idx]))
        enforced = bool(balance.get("enforce", True))
        lo, hi = float(balance["minimum_extraction_to_rejection_ratio"]), float(balance["maximum_extraction_to_rejection_ratio"])
        passed = not enforced or (extraction >= lo*rejection-1e-5 and extraction <= hi*rejection+1e-5)
        ground.append({"source": name, "extraction_kwh": extraction, "rejection_kwh": rejection,
            "ratio": extraction/rejection if rejection > 1e-8 else None, "enforced": enforced, "passed": bool(passed)})
    checks["per_source_ground_balance"] = all(x["passed"] for x in ground)
    checks["ground_summary_matches"] = bool(np.isclose(sum(x["extraction_kwh"] for x in ground), s["ground_heat_extraction_kwh_per_year"], atol=1e-5, rtol=1e-8)
        and np.isclose(sum(x["rejection_kwh"] for x in ground), s["ground_heat_rejection_kwh_per_year"], atol=1e-5, rtol=1e-8))
    comp = s["components_yuan_per_year"]
    capital = float(comp.get("annualized_capex", 0.))
    checks["capital_once"] = bool(np.isclose(capital, ps["components_yuan_per_year"]["annualized_capex"] if m["scope"] == "annual" else 0., atol=1e-5))
    carbon_cost = float(comp.get("carbon_cost", 0.)) + float(comp.get("cooling_carbon_cost", 0.))
    checks["component_objective"] = bool(np.isclose(sum(comp.values()), s["objective_yuan_per_year"], atol=1e-5))
    checks["combined_emission_ledger"] = bool(np.isclose(s["emissions_kg_per_year"]+s["cooling_emissions_kg_per_year"], s["total_service_emissions_kg_per_year"], atol=1e-5))
    checks = {key: bool(value) for key, value in checks.items()}
    return {"complete": True, "reviewer": "C0", "created_utc": datetime.now(timezone.utc).isoformat(),
        "passed": all(checks.values()), "checks": checks,
        "empirical_validation": False,
        "parameter_provenance_approval": "separate_field_level_review_required",
        "run": run.relative_to(root).as_posix(), "manifest_sha256": digest(run/"manifest.json"),
        "planning_manifest_sha256": digest(plan/"manifest.json"), "scope": m["scope"], "hours": len(frame),
        "solver_gap_fraction": s["mip_gap"], "fresh_rc_metrics": rc["metrics"], "fresh_rc_failed_checks": {k:v for k,v in rc["checks"].items() if not v["passed"]},
        "per_source_ground_ledger": ground,
        "cost": {"annualized_capital_yuan": capital, "assumed_carbon_price_cost_yuan": carbon_cost,
            "model_objective_yuan": s["objective_yuan_per_year"], "objective_excluding_carbon_price_yuan": s["objective_yuan_per_year"]-carbon_cost},
        "limitations": ["公开气象驱动的假设建筑原型仿真，不是实测建筑校准或企业鉴定", "RC复算只认证时段末采样舒适；年度地源账本不证明多年地温稳定",
            "全矩阵可行性依赖已保存的数值审计；本报告未独立重建所有设备与网络方程", "碳价是情景核算，不据此声称企业实际现金支付"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--frozen-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite a completed review")
    result = review(Path(__file__).resolve().parents[1], args.run, args.plan, args.frozen_source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": result["checks"], "output": str(args.output)}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
