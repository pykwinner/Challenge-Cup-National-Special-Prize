from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

import pandas as pd
import yaml

from heatplan.chronology import model_structure_profile
from heatplan.io import load_config, validate_inputs
from heatplan.model import HeatPlanningModel


ROOT = Path(__file__).resolve().parents[1]
OLD_MANIFEST = (
    ROOT
    / "outputs"
    / "v11_6_multiyear_representative_days"
    / "robust_multiyear_manifest.yaml"
)
NEW_MANIFEST = (
    ROOT
    / "outputs"
    / "v11_8_multiyear_stochastic_bundle"
    / "stochastic_multiyear_manifest.yaml"
)
OLD_RESULTS = ROOT / "outputs" / "v11_6_multiyear_readiness_robust"
NEW_RESULTS = ROOT / "outputs" / "v11_8_multiyear_stochastic_readiness"
OUTPUT = ROOT / "outputs" / "v11_8_multiyear_stochastic_comparison"


def profiles(path: Path) -> dict[str, dict]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(item["name"]): item for item in payload.get("profiles", [])}


def build_profile(config_path: str, timeseries_path: str) -> dict:
    config = load_config(config_path)
    frame = pd.read_csv(timeseries_path)
    validate_inputs(config, frame)
    model = HeatPlanningModel(config, frame)
    started = perf_counter()
    model.build()
    elapsed = perf_counter() - started
    profile = model_structure_profile(model, build_seconds=elapsed)
    profile.update(
        {
            "representative_days": int(
                frame["representative_day"].nunique()
            )
            if "representative_day" in frame.columns
            else None,
            "scenario_count": int(frame["scenario"].nunique())
            if "scenario" in frame.columns
            else 1,
        }
    )
    return profile


def main() -> None:
    old_profiles = profiles(OLD_MANIFEST)
    new_profiles = profiles(NEW_MANIFEST)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    investment_blocks: list[pd.DataFrame] = []
    structure: dict[str, dict] = {}
    for name in sorted(set(old_profiles) & set(new_profiles)):
        old_profile = build_profile(
            str(old_profiles[name]["config"]),
            str(old_profiles[name]["representative_timeseries"]),
        )
        new_profile = build_profile(
            str(new_profiles[name]["config"]),
            str(new_profiles[name]["representative_timeseries"]),
        )
        structure[name] = {"v11_6": old_profile, "v11_8": new_profile}
        old_summary = json.loads(
            (
                OLD_RESULTS / name / "representative_planning" / "summary.json"
            ).read_text(encoding="utf-8")
        )
        new_summary = json.loads(
            (
                NEW_RESULTS / name / "representative_planning" / "summary.json"
            ).read_text(encoding="utf-8")
        )
        premium = float(
            new_summary.get("components_yuan_per_year", {}).get(
                "cvar_risk_premium", 0.0
            )
        )
        scenario_costs = [
            float(item["operating_risk_cost_yuan"])
            for item in new_summary.get("scenario_results", {}).values()
        ]
        new_readiness = pd.read_csv(
            NEW_RESULTS / name / "multiyear_readiness_audit.csv"
        )
        rows.append(
            {
                "profile": name,
                "v11_6_representative_rows": old_profile["time_steps"],
                "v11_8_representative_rows": new_profile["time_steps"],
                "v11_6_representative_days": old_profile["representative_days"],
                "v11_8_representative_days": new_profile["representative_days"],
                "v11_8_weather_scenarios": new_profile["scenario_count"],
                "v11_6_variables": old_profile["variables"],
                "v11_8_variables": new_profile["variables"],
                "v11_6_integer_variables": old_profile["integer_variables"],
                "v11_8_integer_variables": new_profile["integer_variables"],
                "v11_6_constraints": old_profile["constraints"],
                "v11_8_constraints": new_profile["constraints"],
                "v11_6_build_seconds": old_profile["build_seconds"],
                "v11_8_build_seconds": new_profile["build_seconds"],
                "v11_6_objective_yuan_per_year": old_summary[
                    "objective_yuan_per_year"
                ],
                "v11_8_expected_cost_before_cvar_yuan_per_year": (
                    new_summary["objective_yuan_per_year"] - premium
                ),
                "v11_8_cvar_risk_premium_yuan_per_year": premium,
                "v11_8_total_objective_yuan_per_year": new_summary[
                    "objective_yuan_per_year"
                ],
                "v11_8_max_weather_year_operating_cost_yuan": max(
                    scenario_costs, default=0.0
                ),
                "v11_8_min_weather_year_operating_cost_yuan": min(
                    scenario_costs, default=0.0
                ),
                "v11_8_readiness_pass_years": int(
                    new_readiness["readiness_pass"].astype(bool).sum()
                ),
                "v11_8_years": int(len(new_readiness)),
                "v11_8_worst_scenario_unserved_rate": new_summary[
                    "worst_scenario_unserved_rate"
                ],
                "v11_8_worst_scenario_cooling_unserved_rate": new_summary[
                    "worst_scenario_cooling_unserved_rate"
                ],
            }
        )

        old_investment = pd.read_csv(
            OLD_RESULTS / name / "representative_planning" / "investments.csv"
        )
        new_investment = pd.read_csv(
            NEW_RESULTS / name / "representative_planning" / "investments.csv"
        )
        delta = old_investment.merge(
            new_investment,
            on=["category", "asset", "option", "unit"],
            how="outer",
            suffixes=("_v11_6", "_v11_8"),
        ).fillna(0.0)
        delta.insert(0, "profile", name)
        delta["delta_v11_8_minus_v11_6"] = (
            delta["value_v11_8"] - delta["value_v11_6"]
        )
        investment_blocks.append(delta)

    comparison = pd.DataFrame(rows)
    comparison.to_csv(
        OUTPUT / "algorithm_comparison.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.concat(investment_blocks, ignore_index=True).to_csv(
        OUTPUT / "investment_delta.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (OUTPUT / "model_structure_profiles.json").write_text(
        json.dumps(structure, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(comparison.to_string(index=False))


if __name__ == "__main__":
    main()
