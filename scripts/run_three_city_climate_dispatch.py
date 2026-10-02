"""Controlled climate comparison of continuous, fixed-asset building dispatch.

The Wuhan reference account is a COMMON methodological price for every climate.
This does not estimate local city bills, equipment investment or field savings.
Freeze first; run records every failure without changing caps or service.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from heatplan.io import validate_inputs
from run_evidence_case_experiments import (code_identity, chronology_check, load_inputs,
                                         run_one, save, sha, verify_admission)
from run_representative_season_confirmation import rollout

BASE = Path("docs/implementation_20261002_followup/parallel_stage_02/data")
CONFIG = Path("configs/evidence_case_20261002/wuhan_bestest600_rc_tou.yaml")
SERIES = Path("data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv")
ADMISSION = Path("docs/implementation_20261002/data/admission_tou.json")
CITIES = ("urumqi", "guyuan", "wuhan")
START, END = "2024-11-16T00:00:00+08:00", "2025-03-16T00:00:00+08:00"
PRICE_COLUMNS = ("electricity_price", "grid_emission_factor", "tariff_month",
                 "tariff_bucket", "tariff_source_id", "tariff_pdf_page")


def require(condition, message):
    if not bool(condition):
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def join_common_economics(climate, prices):
    """Exact calendar match, never positional assignment across different hours."""
    left = pd.DatetimeIndex(pd.to_datetime(climate.time, utc=True, errors="raise"))
    right = pd.DatetimeIndex(pd.to_datetime(prices.time, utc=True, errors="raise"))
    require(not left.has_duplicates and left.equals(right), "climate/tariff calendar mismatch")
    require(not set(PRICE_COLUMNS).intersection(climate.columns), "unexpected pre-existing economics")
    result = climate.copy()
    for key in PRICE_COLUMNS:
        result[key] = prices[key].to_numpy()
    return result


def independent_metrics(config, frame, trajectory, *, cap=None, summary=None):
    """Reconstruct continuous states by convolution, plus the ideal-source ledger.

    The producer uses one-step dynamics. This independent finite-horizon form
    checks all hours and reconstructs energy/money/carbon from exported powers.
    General equipment/network validity remains outside this narrow reference.
    """
    bld = config["building_thermal"]["buildings"][0]
    h, c = bld["heat_loss_kw_per_c"], bld["thermal_capacity_kwh_per_c"]
    require(len(trajectory) == len(frame), "trajectory length mismatch")
    expected_time = pd.DatetimeIndex(pd.to_datetime(frame.time, utc=True))
    actual_time = pd.DatetimeIndex(pd.to_datetime(trajectory.time, utc=True))
    require(actual_time.equals(expected_time), "trajectory calendar mismatch")
    heating, cooling = trajectory.heat_kw.to_numpy(), trajectory.cool_kw.to_numpy()
    end = trajectory.T_end.to_numpy()
    start = trajectory.T_start.to_numpy()
    require(np.isfinite(np.r_[heating, cooling, start, end]).all(), "nonfinite trajectory")
    decay = np.exp(-h / c)
    forcing = ((1 - decay) * frame.outdoor_temp.to_numpy() + (1 - decay) / h *
               (heating - cooling + frame.internal_gain_kw.to_numpy() + frame.solar_gain_kw.to_numpy()))
    rebuilt = 20 * decay ** np.arange(1, len(frame) + 1) + np.convolve(
        forcing, decay ** np.arange(len(frame)), mode="full")[:len(frame)]
    energy = heating + cooling
    bill = float(energy @ frame.electricity_price.to_numpy())
    carbon = float(energy @ frame.grid_emission_factor.to_numpy())
    checks = {
        "continuous_RC": float(np.max(np.abs(rebuilt - end))) <= 1e-6,
        "state_continuity": bool(np.max(np.abs(start - np.r_[20, end[:-1]])) <= 1e-6),
        "comfort": bool(end.min() >= 20 - 1e-6 and end.max() <= 27 + 1e-6),
        "terminal": bool(abs(end[-1] - 20) <= 1e-6),
        "nonnegative": bool(min(heating.min(), cooling.min()) >= -1e-7),
        "exclusive": bool(not np.any((heating > 1e-7) & (cooling > 1e-7))),
        "ideal_capacity": bool(max(heating.max(), cooling.max(), energy.max()) <= 1000 + 1e-7),
        "carbon_cap": cap is None or carbon <= cap + 1e-6,
    }
    if summary is not None:
        checks["objective_ledger"] = bool(np.isclose(bill, summary["objective_yuan_per_year"], rtol=1e-8, atol=1e-6))
        checks["emission_ledger"] = bool(np.isclose(carbon, summary["total_service_emissions_kg_per_year"], rtol=1e-8, atol=1e-6))
    return {"passed": all(checks.values()), "checks": checks,
            "maximum_temperature_residual_c": float(np.max(np.abs(rebuilt - end))),
            "bill_yuan": bill, "energy_kwh": float(energy.sum()), "carbon_kg": carbon,
            "heating_kwh": float(heating.sum()), "cooling_kwh": float(cooling.sum()),
            "peak_import_kw": float(energy.max()), "minimum_temperature_c": float(end.min()),
            "maximum_temperature_c": float(end.max()),
            "warm_degree_hours_above_20": float(np.maximum(end - 20, 0).sum())}


def freeze(out):
    require(not out.exists(), "output exists; preserve prior record")
    config, price = load_inputs(ROOT / CONFIG, ROOT / SERIES)
    admitted = verify_admission(ROOT / ADMISSION, ROOT / CONFIG, ROOT / SERIES, rc=True)
    require(admitted["conditional_operating_tariff_reference_admitted"], "tariff reference not admitted")
    manifest = read(ROOT / BASE / "manifest.json")
    for name, digest in manifest["artifact_sha256"].items():
        require(sha(ROOT / name) == digest, f"physical input changed: {name}")
    # The preparation protocol carries the exact common physical interface.
    physics = read(ROOT / BASE / "protocol.json")["physical_reference"]
    require(physics["building_thermal"] == config["building_thermal"], "physical service differs")
    sources = [CONFIG, SERIES, ADMISSION, BASE / "manifest.json", BASE / "protocol.json",
               BASE / "field_provenance.csv", BASE / "common_physical_reference.json"]
    for city in CITIES:
        path = Path(f"data/derived/three_city_common_reference_20261002/{city}_hourly.csv")
        frame = join_common_economics(pd.read_csv(ROOT / path), price)
        chronology_check(frame, config, START, END)
        validate_inputs(config, frame)
        require(len(frame) == 2880 and (frame.scenario_probability == 1).all(), "wrong window")
        sources.append(path)
    codes = {name.replace("\\", "/"): digest for name, digest in code_identity().items()}
    for path in (Path(__file__), ROOT / "scripts/run_representative_season_confirmation.py"):
        codes[path.relative_to(ROOT).as_posix()] = sha(path)
    out.mkdir(parents=True)
    protocol = {
        "frozen_utc": datetime.now(timezone.utc).isoformat(), "cities": CITIES,
        "start_inclusive": START, "end_exclusive": END, "hours_per_city": 2880,
        "time_limit_per_stage_seconds": 60, "mip_gap_target": 1e-4,
        "input_sha256": {p.as_posix(): sha(ROOT / p) for p in sources}, "code_sha256": codes,
        "scope": "same public prototype/ideal existing assets, climate-only continuous operation comparison",
        "common_economics": "Audited Hubei monthly TOU account and EF applied identically as a methodological control, NOT local Urumqi/Guyuan tariff or account eligibility",
        "cost_scope": "120-day variable electricity bill only; no lifecycle/ROI/investment claim",
        "service": "20..27C, initial/final20C, continuous heat/cool; natural drift allowed",
        "rows": ["baseline_delta0", "cost_endpoint", "carbon_endpoint", "midpoint_cap"],
        "baseline": "same frozen delta0 feedback rollout; no valley preheating, natural drift up to27, final restore20",
        "endpoint": "existing two-stage solver; incumbent primary bound plus 1e-7+1e-8*abs(primary); both stage statuses/bounds retained",
        "midpoint_rule": "0.5*(carbon endpoint emission + cost endpoint emission), plus numerical cap allowance1e-7+1e-8*abs(cap); no adaptive threshold",
        "failure_rule": "retain every attempted failure; no endpoint => midpoint skipped with reason; no relabel/fallback",
        "comparison_acceptance": "physical/ledger checks and solver numerical/quality gates pass before accepted comparison",
        "fixed_factor_carbon_energy_redundant": True, "free_investment_variables": 0,
        "local_full_heating_seasons_claimed": False, "field_validation": False,
        "new_weight_candidate_evaluated": False, "source_originals_checked_at_freeze": True,
        "known_exposure": "Wuhan window already used; three climate outcomes are mechanism comparison, not held-out prediction. No weight-candidate thresholds are tuned here.",
        "storage_state": "not_applicable: no storage in admitted configuration",
    }
    save(out / "protocol.json", protocol)
    save(out / "protocol_lock.json", {"protocol.json": sha(out / "protocol.json")})
    print(json.dumps({"frozen": True, "protocol_sha256": sha(out / "protocol.json")}))


def verify(out):
    for name, digest in read(out / "protocol_lock.json").items():
        require(sha(out / name) == digest, "protocol modified")
    protocol = read(out / "protocol.json")
    for category in ("input_sha256", "code_sha256"):
        for name, digest in protocol[category].items():
            require(sha(ROOT / name) == digest, f"frozen identity changed: {name}")
    if (out / "manifest.json").exists():
        for name, digest in read(out / "manifest.json")["files"].items():
            require(sha(out / name) == digest, f"output changed: {name}")
    return protocol


def run(out):
    protocol = verify(out)
    require(not (out / "results").exists(), "run already started; do not overwrite")
    dest = out / "results"
    dest.mkdir()
    config, prices = load_inputs(ROOT / CONFIG, ROOT / SERIES)
    rows, failures = [], []
    start = perf_counter()
    for city in CITIES:
        citydir = dest / city
        citydir.mkdir()
        frame = join_common_economics(pd.read_csv(ROOT / f"data/derived/three_city_common_reference_20261002/{city}_hourly.csv"), prices)
        frame.to_csv(citydir / "chronology.csv", index=False)
        (citydir / "common_config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        baseline = rollout(config, frame, 0)
        baseline.to_csv(citydir / "baseline_delta0.csv", index=False)
        metrics = independent_metrics(config, frame, baseline)
        save(citydir / "baseline_audit.json", metrics)
        require(metrics["passed"], "baseline audit failed")
        rows.append({"city": city, "case": "baseline_delta0", "result_attribute": "predeclared_feasible_feedback_policy", **metrics})
        endpoints = {}
        for name, primary in (("cost_endpoint", "total_cost"), ("carbon_endpoint", "emissions"), ("midpoint_cap", None)):
            candidate = deepcopy(config)
            cap = None
            if name == "midpoint_cap":
                if len(endpoints) != 2:
                    failures.append({"city": city, "case": name, "status": "not_run_missing_endpoint"})
                    continue
                low, high = endpoints["carbon_endpoint"], endpoints["cost_endpoint"]
                if low > high + 1e-6 + abs(high) * 1e-8:
                    failures.append({"city": city, "case": name, "status": "invalid_endpoint_order"})
                    continue
                requested = (low + high) / 2
                cap = requested + 1e-7 + abs(requested) * 1e-8
                candidate["planning"]["emissions_cap_kg_per_year"] = cap
                save(citydir / "midpoint_cap_rule.json", {"requested_cap_kg": requested, "applied_cap_kg": cap})
            try:
                result, audit = run_one(name, candidate, frame, citydir, 60, 1e-4, primary=primary)
                trajectory = result.building_trajectory.rename(columns={"temperature_start_c": "T_start", "temperature_end_c": "T_end", "cooling_kw": "cool_kw"})
                metrics = independent_metrics(candidate, frame, trajectory, cap=cap, summary=result.summary)
                save(citydir / name / "independent_ledger_audit.json", metrics)
                require(metrics["passed"], "independent ledger/state audit failed")
                if primary:
                    endpoints[name] = metrics["carbon_kg"]
                quality = result.summary["solution_quality"]
                rows.append({"city": city, "case": name, "result_attribute": "fixed_existing_assets_optimized_dispatch",
                             "solution_quality": quality, "solver_status": result.summary["solver_status"],
                             "mip_gap": result.summary.get("mip_gap"), "wall_seconds": audit["wall_seconds"], **metrics})
            except Exception as error:
                failures.append({"city": city, "case": name, "status": "failed", "reason": str(error)})
            print(json.dumps({"city": city, "case": name, "completed_rows": len(rows), "failures": len(failures)}), flush=True)
    save(out / "comparison.json", rows)
    pd.json_normalize(rows).to_csv(out / "comparison.csv", index=False)
    save(out / "failures.json", failures)
    verify(out)
    save(out / "manifest.json", {"all_requested_cases_passed": not failures and len(rows) == 12,
        "accepted_rows": len(rows), "failures": failures, "wall_seconds": perf_counter() - start,
        "free_investment_variables": 0, "field_validation": False,
        "files": {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*")) if p.is_file()}})
    print(json.dumps({"accepted_rows": len(rows), "failures": failures}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "verify"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    {"freeze": freeze, "run": run, "verify": verify}[args.action](args.out.resolve())
