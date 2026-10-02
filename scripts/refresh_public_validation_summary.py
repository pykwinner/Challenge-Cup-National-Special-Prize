from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "public_data_validation"
MANIFEST = ROOT / "data" / "public_scenario_inputs" / "three_scenario_public_manifest.yaml"


def main() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    comparison: list[dict] = []
    audit: list[dict] = []

    for profile in manifest["profiles"]:
        name = str(profile["name"])
        directory = OUTPUT / name
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        comparison.append({
            "profile": name,
            "archetype": profile.get("archetype", ""),
            **{key: value for key, value in summary.items() if not isinstance(value, dict)},
        })

        hydraulic_path = directory / "hydraulic_postcheck.csv"
        try:
            hydraulic = pd.read_csv(hydraulic_path) if hydraulic_path.exists() else pd.DataFrame()
        except pd.errors.EmptyDataError:
            hydraulic = pd.DataFrame()
        failures = int((hydraulic.get("status", pd.Series(dtype=str)) == "fail").sum())
        warnings = int((hydraulic.get("status", pd.Series(dtype=str)) == "warning").sum())

        replay_path = directory / "unit_commitment_replay.csv"
        replay_status: bool | str = "not_applicable"
        if replay_path.exists():
            replay = pd.read_csv(replay_path)
            replay_status = bool(replay["feasible"].astype(bool).all())

        dispatch = pd.read_csv(directory / "dispatch.csv")
        battery = dispatch[dispatch["category"].isin([
            "microgrid_battery_charge", "microgrid_battery_discharge"
        ])]
        max_simultaneous = 0.0
        if not battery.empty:
            pivot = battery.pivot_table(
                index="time_index", columns="category", values="value", aggfunc="sum", fill_value=0.0
            )
            max_simultaneous = float(
                (pivot.get("microgrid_battery_charge", 0.0)
                 * pivot.get("microgrid_battery_discharge", 0.0)).max()
            )

        audit.append({
            "profile": name,
            "solver_has_solution": True,
            "solver_status": int(summary["solver_status"]),
            "mip_gap": float(summary["mip_gap"]),
            "zero_unserved": float(summary["worst_scenario_unserved_rate"]) <= 1e-9,
            "exergy_valid": bool(summary["exergy_accounting_valid"]),
            "hydraulic_failures": failures,
            "hydraulic_warnings": warnings,
            "microgrid_max_simultaneous_charge_discharge_kw2": max_simultaneous,
            "unit_commitment_replay_all_feasible": replay_status,
        })

    comparison_frame = pd.json_normalize(comparison)
    comparison_frame.to_csv(OUTPUT / "public_scenario_comparison.csv", index=False)
    # Keep the unified-run filename synchronized as well; previously the two
    # comparison files could silently describe different model revisions.
    comparison_frame.to_csv(OUTPUT / "three_scenario_comparison.csv", index=False)
    pd.DataFrame(audit).to_csv(OUTPUT / "public_model_audit.csv", index=False)
    print(pd.DataFrame(audit).to_string(index=False))


if __name__ == "__main__":
    main()
