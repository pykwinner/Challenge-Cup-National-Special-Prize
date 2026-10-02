"""Offline, source-locked BESTEST-derived Wuhan weather reference inputs.

Not a Wuhan building survey, ASHRAE-140 certification, or full economic case.
All writes stay in the four delegated evidence-case directories.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path("configs/evidence_case_20261002")
DATA_DIR = Path("data/derived/evidence_case_20261002")
DOC_DIR = Path("docs/implementation_20261002/data")
EVIDENCE = DOC_DIR / "evidence"
TIME_PATH = DATA_DIR / "wuhan_2024_2025_hourly.csv"
FIELD_PATH = DOC_DIR / "field_provenance.csv"
START = "2024-11-16T00:00:00+08:00"
END = "2025-03-16T00:00:00+08:00"
HOURS = 2880
H = (32.715 + 1.892 + 15.253 + 36.000 + 18.440) / 1000
# Only the room-side material preceding the first insulation layer is lumped.
# These are the actual prescribed BESTEST layers, not assumed local layers.
C_PARTS_J_PER_K = {
    "wall_plasterboard": 63.6 * .012 * 950 * 840,
    "floor_timber": 48 * .025 * 650 * 1200,
    "ceiling_plasterboard": 48 * .010 * 950 * 840,
}
C = sum(C_PARTS_J_PER_K.values()) / 3_600_000
LOCKS = {
    "bestest_6231.pdf": "935ff9744c68ee27b83a41dbb508af59ce54cf37a77e513a66018384a40b0aff",
    "hubei_sales_price_2021.pdf": "939063dfc09bb1f00cbe413bcc9c28e977a9edceb5b152a40cc4bd38c121ac43",
    "wuhan_nasa_power_utc.json": "008d00e509ec288d76fb6f01df1dcbb85d5090783786cfc9feb63a919044fa70",
    "wuhan_weather_receipt.json": "35f728e1cd22531b01fee93498e3f5f0def55666bc84b0850890911e0f087d0b",
    "grid_factor_2023.pdf": "d43b60a3eaad9f3fe59ca37c204d3d0e1b6ea52742a092f0d856971a3f018e5a",
    "wuhan_heating_window_2024.html": "f39bb14a34bb1ab697e821f3e1ac3f41b15ed1e4cec844aa6f097d3ded446c8d",
}
BESTEST_URL = "https://www.iea-ebc.org/Data/publications/EBC_Annex_21_BESTEST.pdf"
PRICE_URL = "https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf/202110/P020211025720835003479.pdf"
GRID_URL = "https://www.mee.gov.cn/xxgk2018/xxgk/xxgk01/202512/W020251231726284332528.pdf"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_evidence(root: Path = ROOT) -> None:
    for name, expected in LOCKS.items():
        path = root / EVIDENCE / name
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"Locked original evidence missing or changed: {name}")


def source_registry(root: Path = ROOT) -> dict:
    receipt = json.loads((root / EVIDENCE / "wuhan_weather_receipt.json").read_text(encoding="utf-8-sig"))
    return {
        "BESTEST": {"path": (EVIDENCE / "bestest_6231.pdf").as_posix(), "url": BESTEST_URL,
                    "identity": "1995 NREL/TP-472-6231, prescribed reference prototype",
                    "locators": "PDF p34/36/39/41/48/49/50/53; printed 1-3/1-5/1-8/1-10/1-17/1-18/1-19/1-22"},
        "NASA": {"path": (EVIDENCE / "wuhan_nasa_power_utc.json").as_posix(), "url": receipt["url"],
                 "identity": "NASA POWER gridded MERRA2/SYN1DEG product, UTC hourly; not station measurement"},
        "PRICE": {"path": (EVIDENCE / "hubei_sales_price_2021.pdf").as_posix(), "url": PRICE_URL,
                  "identity": "2021 historical schedule, appendix 2 PDF p6; residential combined-meter <1kV class"},
        "GRID": {"path": (EVIDENCE / "grid_factor_2023.pdf").as_posix(), "url": GRID_URL,
                 "identity": "2023 Hubei province annual-average electricity CO2 factor, table 3; reference applied to 2024/25"},
        "WINDOW": {"path": (EVIDENCE / "wuhan_heating_window_2024.html").as_posix(),
                   "url": "https://www.wuhan.gov.cn/sy/whyw/202411/t20241117_2485509.shtml",
                   "identity": "Wuhan government news, 2024-11-17: Wuchang and surrounding district 11/16 to 3/15; borrowed experiment window, not citywide statutory season"},
        "PROTOCOL": {"identity": "Explicit model boundary/derived convention; not measured data", "url": ""},
    }


def weather_from_original(root: Path = ROOT) -> pd.DataFrame:
    raw = json.loads((root / EVIDENCE / "wuhan_nasa_power_utc.json").read_text(encoding="utf-8-sig"))
    if raw["header"]["time_standard"] != "UTC":
        raise ValueError("Weather time standard must be explicitly UTC")
    mapping = {"T2M": "outdoor_temp", "RH2M": "relative_humidity_pct",
               "WS10M": "wind_speed_10m_m_s", "ALLSKY_SFC_SW_DWN": "solar_irradiation_wh_m2"}
    frame = pd.DataFrame({column: raw["properties"]["parameter"][parameter] for parameter, column in mapping.items()})
    stamp = pd.to_datetime(frame.index, format="%Y%m%d%H", utc=True).tz_convert("Asia/Shanghai")
    frame.index = stamp
    frame = frame.loc[(stamp >= pd.Timestamp(START)) & (stamp < pd.Timestamp(END))].sort_index()
    for column in mapping.values():
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if (frame[column] == raw["header"]["fill_value"]).any():
            raise ValueError(f"NASA fill value in {column}; no filling allowed")
    frame.insert(0, "time", [value.isoformat() for value in frame.index])
    return frame.reset_index(drop=True)


def build_frame(root: Path = ROOT) -> pd.DataFrame:
    frame = weather_from_original(root)
    frame["scenario"] = "wuhan_weather_reference_2024_2025"
    frame["boundary_group"] = "full_heating_season"
    frame["scenario_probability"] = 1.
    frame["period_weight"] = 1.
    frame["electricity_price"] = .5800
    frame["grid_emission_factor"] = .4044
    frame["internal_gain_kw"] = .200
    # Disabled solar pathway. This is an exclusion, not an observed zero-sun record.
    frame["solar_gain_kw"] = 0.
    frame["heating_service"] = 1
    frame["heat_reference_no_gain_kw"] = H * np.maximum(20 - frame.outdoor_temp, 0)
    frame["steady_heat_demand_kw"] = np.maximum(H * (20 - frame.outdoor_temp) - .200, 0)
    frame["steady_cooling_demand_kw"] = np.maximum(H * (frame.outdoor_temp - 27) + .200, 0)
    frame["steady_temperature_c"] = np.clip(frame.outdoor_temp + .200 / H, 20, 27)
    validate_frame(frame)
    return frame


def validate_frame(frame: pd.DataFrame) -> None:
    if any(pd.Timestamp(value).tzinfo is None for value in frame.time):
        raise ValueError("Every timestamp must carry an explicit timezone")
    stamp = pd.to_datetime(frame.time, utc=True, errors="raise")
    expected = pd.date_range(pd.Timestamp(START).tz_convert("UTC"), pd.Timestamp(END).tz_convert("UTC"), freq="h", inclusive="left")
    if len(frame) != HOURS or not pd.DatetimeIndex(stamp).equals(expected):
        raise ValueError("Need exact ordered 2880-hour source-backed seasonal grid; no duplicates/gaps")
    numeric = frame.select_dtypes(include="number")
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Non-finite numeric input; no imputation allowed")
    if not (frame.period_weight.eq(1).all() and frame.scenario_probability.eq(1).all()):
        raise ValueError("Full season must be continuous, unweighted and deterministic")
    if frame.scenario.nunique() != 1 or frame.boundary_group.nunique() != 1:
        raise ValueError("One continuous seasonal state group required")
    if not frame.electricity_price.eq(.58).all() or not frame.grid_emission_factor.eq(.4044).all():
        raise ValueError("Historical reference tariff/factor changed; require separate admission")


def build_config(rc: bool) -> dict:
    grade = "ideal_zone_service"
    config = {
        "planning": {"region_profile": "BESTEST600_derived_Wuhan_weather_reference",
                     "time_step_hours": 1, "representative_day_weight": 1,
                     # Interface arithmetic only; cannot influence a disabled investment ledger.
                     "discount_rate": 1., "asset_lifetime_years": 1,
                     "carbon_price_yuan_per_kg": 0., "exergy_penalty_yuan_per_kwh": 0.,
                     "ambient_reference_c": 0., "max_unserved_rate": 0.,
                     "max_scenario_unserved_rate": 0., "unserved_heat_penalty_yuan_per_kwh": 0.,
                     "firm_capacity_reserve_margin": 0., "enforce_n_minus_one": False,
                     "design_outdoor_temperature_c": -4.85},
        # Fixed delivery tag at the maximum modeled zone temperature. An ideal
        # zero-approach electrical heater; no finite-temperature emitter claim.
        "grades": [{"name": grade, "temperature_c": 27.}],
        "nodes": [{"name": "reference_building", "demand_columns": {grade: "heat_reference_no_gain_kw" if rc else "steady_heat_demand_kw"}}],
        "sources": [{"name": "bestest_ideal_air_system", "type": "electric_boiler",
                     "node": "reference_building", "resource_kind": "electric",
                     "allowed_grades": [grade], "supply_temp_c": 27.,
                     "existing_capacity_kw": 1000., "max_new_capacity_kw": 0.,
                     "max_retire_capacity_kw": 0., "efficiency": 1.,
                     "variable_om_yuan_per_kwh_heat": 0.,
                     "firm_capacity_credit": 1., "firm_capacity_availability": 1.}],
        "pipes": [], "storages": [],
        "demand_response": {"max_shift_fraction": 0.},
        "multi_energy_services": {"cooling": {"enabled": True,
            "demand_columns": {"reference_building": "steady_cooling_demand_kw"},
            "max_unserved_rate": 0., "max_scenario_unserved_rate": 0.,
            "unserved_penalty_yuan_per_kwh": 0.,
            "sources": {"bestest_ideal_air_system": {"eer": 1., "eer_min": 1., "eer_max": 1.,
                "eer_loss_per_c_above_reference": 0., "capacity_equivalence": 1.,
                "variable_om_yuan_per_kwh_cooling": 0., "direct_emission_kg_per_kwh_cooling": 0.}}}},
        "building_thermal": {"enabled": rc},
    }
    if rc:
        config["building_thermal"].update({
            "outdoor_temperature_column": "outdoor_temp", "simultaneous_mode": "exclusive", "boundary_mode": "fixed",
            "buildings": [{"name": "bestest600_inner_mass_approximation", "node": "reference_building",
                "heat_grade": grade, "area_m2": 48., "heat_loss_kw_per_c": H,
                "thermal_capacity_kwh_per_c": C, "min_temperature_c": 20., "max_temperature_c": 27.,
                "initial_temperature_c": 20., "final_temperature_c": 20.,
                "internal_gain_column": "internal_gain_kw", "solar_gain_column": "solar_gain_kw",
                "heating_service_column": "heating_service", "heating_enabled": True, "cooling_enabled": True,
                "max_heat_kw": 1000., "max_cooling_kw": 1000.}]})
    return config


def leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        if not value:
            yield prefix, value
        for index, child in enumerate(value):
            yield from leaves(child, f"{prefix}[{index}]")
    else:
        yield prefix, value


def field_identity(path: str) -> tuple[str, str, str]:
    """Return explicit source, numerical identity and scope (no unknown defaults)."""
    key = path.rsplit(".", 1)[-1]
    if key == "heat_loss_kw_per_c":
        return "BESTEST", "(32.715+1.892+15.253+36+18.440)/1000 kW/K; table1-15", "Prototype H retains prescribed 1609m infiltration condition, not Wuhan measured H"
    if key == "thermal_capacity_kwh_per_c":
        return "BESTEST", "sum(A*d*rho*cp of room-side plasterboard/timber before insulation)/3600000; table1-15; corroborated table1-16", "1R1C inner-material lumping approximation; no air/furniture/outer layers; not calibrated effective C"
    if key in {"existing_capacity_kw", "max_heat_kw", "max_cooling_kw", "efficiency", "eer", "eer_min", "eer_max"}:
        return "BESTEST", "1000kW and 100% effective efficiency; section1.4.11.2", "Ideal sensible air benchmark, type electric_boiler is software adapter, not actual boiler/chiller"
    if key in {"min_temperature_c", "max_temperature_c", "area_m2"}:
        return "BESTEST", "Case600 20/27C, 8x6m; table1-14 and figure1-2", "Published prescribed reference geometry/comfort, not Wuhan survey/compliance"
    if key in {"supply_temp_c", "temperature_c"}:
        return "BESTEST;PROTOCOL", "Tag=max zone bound=27C, zero-approach ideal service convention", "Accounting delivery tag; no measured outlet temperature or full exergy efficiency claim"
    if key == "design_outdoor_temperature_c":
        return "NASA", "min T2M of exact admitted 2880 hours=-4.85C", "Weather-season capacity screen; not normative design outdoor temperature"
    if key in {"discount_rate", "asset_lifetime_years"}:
        return "PROTOCOL", "Interface-only unit arithmetic r=1,n=1; all new/retire capacities disabled", "Inactive finance placeholders; no financial assumptions/economic admission, must never enable investment"
    if key.startswith("variable_om_"):
        return "PROTOCOL", "Excluded OM ledger coefficient=0; no actual zero OM claim", "Report electric energy charges only; full economics blocked"
    if key in {"initial_temperature_c", "final_temperature_c"}:
        return "BESTEST;PROTOCOL", "20C conditioned initial state and equal terminal state", "Explicit comparison boundary, not observed initial indoor temperature; preconditioning outside boundary"
    if key in {"firm_capacity_credit", "firm_capacity_availability"}:
        return "BESTEST;PROTOCOL", "Deterministic ideal system availability=1", "No empirical reliability/failure probability claim"
    return "PROTOCOL", "Explicit identifiers, mapping, service equality or disabled branch; no external numerical observation", "Source-backed reference experiment scope only; controls/identifiers are declared model boundary"


def provenance(root: Path = ROOT) -> pd.DataFrame:
    rows = []
    for mode in ("steady", "rc"):
        for path, value in leaves(build_config(mode == "rc")):
            source, formula, limitation = field_identity(path)
            rows.append({"artifact": f"wuhan_bestest600_{mode}.yaml", "field": path,
                         "value_or_mapping": json.dumps(value, ensure_ascii=False), "source_ids": source,
                         "formula_or_locator": formula, "identity_and_scope": limitation})
    column_sources = {
        "time": ("NASA;WINDOW", "NASA UTC key -> Asia/Shanghai, select [2024-11-16,2025-03-16)"),
        "outdoor_temp": ("NASA", "T2M, degC"), "relative_humidity_pct": ("NASA", "RH2M, percent"),
        "wind_speed_10m_m_s": ("NASA", "WS10M, m/s"), "solar_irradiation_wh_m2": ("NASA", "ALLSKY_SFC_SW_DWN, Wh/m2 each hour; not a kW heating gain"),
        "electricity_price": ("PRICE", "0.5800 yuan/kWh, historical combined-meter residential <1kV design class"),
        "grid_emission_factor": ("GRID", "0.4044 kgCO2/kWh, 2023 Hubei annual factor, fixed reference"),
        "internal_gain_kw": ("BESTEST", "constant 200W=0.200kW; section1.4.4, sensible only; radiative/convective collapsed"),
        "solar_gain_kw": ("PROTOCOL", "solar pathway excluded=0; not zero solar radiation measurement"),
        "heat_reference_no_gain_kw": ("BESTEST;NASA", "H*max(20-Tout,0); RC capacity/reporting reference only"),
        "steady_heat_demand_kw": ("BESTEST;NASA", "max(H*(20-Tout)-0.200,0), no solar, quasi-steady service"),
        "steady_cooling_demand_kw": ("BESTEST;NASA", "max(H*(Tout-27)+0.200,0); warm-season-slice sensible cooling"),
        "steady_temperature_c": ("BESTEST;NASA", "clip(Tout+0.200/H,20,27); algebraic reference, not observation"),
    }
    for column in build_frame(root).columns:
        source, formula = column_sources.get(column, ("PROTOCOL", "single deterministic continuous boundary; weights/service=1; labels are identifiers"))
        rows.append({"artifact": TIME_PATH.name, "field": column, "value_or_mapping": "hourly column",
                     "source_ids": source, "formula_or_locator": formula,
                     "identity_and_scope": "Climate-driven public prototype experiment; no field observations/fitted parameters claimed"})
    return pd.DataFrame(rows)


def validate_case(config: dict, frame: pd.DataFrame, *, rc: bool) -> None:
    validate_frame(frame)
    if "extends" in config or config != build_config(rc):
        raise ValueError("Config differs from source-admitted reference; new branches require new field review")
    if not np.allclose(frame["steady_heat_demand_kw"], np.maximum(H*(20-frame.outdoor_temp)-.2, 0), rtol=0, atol=1e-10):
        raise ValueError("Steady heat formula mismatch")
    if config["sources"][0]["supply_temp_c"] < config["grades"][0]["temperature_c"]:
        raise ValueError("Illegal service temperature grade")
    sys.path.insert(0, str(ROOT / "src"))
    from heatplan.io import validate_inputs
    validate_inputs(config, frame)


def verify_admission(root: Path = ROOT) -> dict:
    report = json.loads((root / DOC_DIR / "admission.json").read_text(encoding="utf-8"))
    for relative, expected in report["input_sha256"].items():
        if sha256(root / relative) != expected:
            raise ValueError(f"Admission input hash mismatch: {relative}")
    verify_evidence(root)
    frame = pd.read_csv(root / TIME_PATH)
    for mode in ("steady", "rc"):
        config = yaml.safe_load((root / CONFIG_DIR / f"wuhan_bestest600_{mode}.yaml").read_text(encoding="utf-8"))
        validate_case(config, frame, rc=mode == "rc")
    pd.testing.assert_frame_equal(frame, build_frame(root), check_dtype=False, rtol=0, atol=1e-10)
    return report


def prepare(root: Path = ROOT) -> dict:
    verify_evidence(root)
    for directory in (CONFIG_DIR, DATA_DIR, DOC_DIR):
        (root / directory).mkdir(parents=True, exist_ok=True)
    frame = build_frame(root)
    for mode in ("steady", "rc"):
        config = build_config(mode == "rc")
        validate_case(config, frame, rc=mode == "rc")
        (root / CONFIG_DIR / f"wuhan_bestest600_{mode}.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    frame.to_csv(root / TIME_PATH, index=False, float_format="%.12g")
    fields = provenance(root)
    fields.to_csv(root / FIELD_PATH, index=False, encoding="utf-8")
    derivation = {"heat_loss_kw_per_c": H, "inner_mass_capacity_kwh_per_c": C,
                  "capacity_terms_j_per_k": C_PARTS_J_PER_K, "formula": "sum(A_m2*d_m*rho_kg_m3*cp_J_kg_K)/3600000",
                  "published_capacity_check": "Table1-16 1015.912Btu/F approximately 1.9293MJ/K; small rounding difference to SI-layer derivation",
                  "source_role": "public prescribed prototype -> declared 1R1C structural approximation, NOT identified effective thermal capacity",
                  "solar_pathway": "excluded; NASA solar still preserved as original weather metadata",
                  "source_registry": source_registry(root)}
    derived_path = DOC_DIR / "derivation.json"
    (root / derived_path).write_text(json.dumps(derivation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = [CONFIG_DIR / f"wuhan_bestest600_{mode}.yaml" for mode in ("steady", "rc")]
    paths += [TIME_PATH, FIELD_PATH, derived_path, Path("scripts/prepare_evidence_case_20261002.py")]
    paths += [EVIDENCE / name for name in LOCKS]
    report = {
        "schema_version": 1, "case_id": "BESTEST600_derived_Wuhan_weather_2024_2025_reference",
        "physical_reference_admitted": True, "rc_reference_admitted": True,
        "full_economics_admitted": False, "external_building_validation_passed": False,
        "admission_scope": "These exact hashes only: source-backed public prototype method reference, not local empirical case or BESTEST certification",
        "time_scope": {"start_inclusive": START, "end_exclusive": END, "timezone": "Asia/Shanghai", "hours": HOURS,
                       "identity": "borrowed Wuchang/surrounding-area 2024 government-news service window; not citywide law"},
        "input_paths": {"rc_config": (CONFIG_DIR / "wuhan_bestest600_rc.yaml").as_posix(),
                        "steady_config": (CONFIG_DIR / "wuhan_bestest600_steady.yaml").as_posix(),
                        "timeseries": TIME_PATH.as_posix(), "field_provenance": FIELD_PATH.as_posix()},
        "input_sha256": {path.as_posix(): sha256(root / path) for path in paths},
        "active_field_coverage": {"covered_rows": len(fields), "missing": [], "all_config_leaves_and_timeseries_columns": True},
        "sources": source_registry(root),
        "parameters": {"area_m2": 48., "volume_m3": 129.6, "H_kw_per_K": H, "C_kwh_per_K": C,
                       "internal_gain_kw": .2, "price_yuan_per_kwh": .58, "grid_factor_kg_per_kwh": .4044},
        "structure_approximations": ["H retains original prescribed high-altitude infiltration; Wuhan weather replacement does not make this a local building",
            "Inner-side material capacity is lumped into one air/zone state; no measured effective RC C or predictive accuracy claim",
            "No solar gains, latent loads, domestic hot water, thermal bridges, occupancy-based behavior or ground dynamics",
            "Ideal 1000kW 100%-efficient benchmark air system, no finite emitter/COP curve or actual rated equipment claim",
            "27C delivery tag is maximum-zone-bound ideal accounting convention, not a reported outlet temperature; exergy efficacy out of scope",
            "20C initial and terminal conditioned boundary; preconditioning outside seasonal accounting",
            "Shared ideal heating/cooling capacity with exclusive RC operation; no real compressor asset claimed"],
        "economic_scope": "Electricity energy charges only under explicitly selected historical residential combined-meter <1kV reference class; no tariff account eligibility observation",
        "excluded_economic_fields": ["CAPEX", "installation", "fixed_OM", "variable_OM", "equipment_lifetime", "discounted cash flow", "heat revenue", "subsidy", "payback"],
        "blocked_fields": ["Actual device model-linked performance/capacity/quotation for equipment extension", "Target-project installed cost, OM and life for full economic evaluation"],
        "inactive_interface_fields": {"discount_rate": 1., "asset_lifetime_years": 1, "reason": "required constructor arithmetic only; all investment/retirement disabled; never use for financial claims"},
        "disabled_branches": ["network", "storage", "capacity investment", "retirement", "legacy demand shifting", "fuel", "biomass", "shared waste heat", "grid capacity", "reliability events"],
        "excluded_intake_claims": ["Supplementary package per-layer 100mm ISO effective-C derivation", "Summer Yangtze residential air-conditioning survey as winter Wuhan schedule", "GB55015 schedule extraction with missing evening/kitchen midday hours", "Unlocked Open-Meteo and model-filled Meteostat as independent station bias proof", "Procurement minimum COP ratios as actual COP derating curves"],
        "checks": {"exact_source_hashes": True, "exact_hourly_grid": True, "no_inherited_defaults": True, "no_missing_weather_filling": True,
                   "positive_H_C": H > 0 and C > 0, "core_input_validation": True, "source_temperature_tag_admissible": True,
                   "full_solver_run": "owned by integration runner, not E0 source admission"},
    }
    (root / DOC_DIR / "admission.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verify_admission(root)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Verify exact admitted hashes without rewriting outputs")
    args = parser.parse_args()
    report = verify_admission() if args.verify_only else prepare()
    print(json.dumps({key: report[key] for key in ("case_id", "physical_reference_admitted", "rc_reference_admitted", "full_economics_admitted", "input_paths")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
