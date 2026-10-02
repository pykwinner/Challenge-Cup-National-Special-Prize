"""Prepare source-locked, weather-only inputs for one common BESTEST prototype.

No network, solver, tariff, emissions factor, local load observation, or SCADA
requirement. UTC originals are selected once, then verified from local snapshots.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = Path("data/derived/three_city_common_reference_20261002")
DOC = Path("docs/implementation_20261002_followup/parallel_stage_02/data")
EVIDENCE = DOC / "evidence"
DEFAULT_SOURCES = Path("D:/competion/xiaotiao/三城市数据包/三城市论文核验数据包_20261002/verified_public/nasa_power_utc")
BASE_CONFIG = Path("configs/evidence_case_20261002/wuhan_bestest600_rc.yaml")
BASE_ADMISSION = Path("docs/implementation_20261002/data/admission.json")
BASE_ADMISSION_SHA256 = "bb85ea2f260dd4aa439f5b7fc84d44e535398d291673fe06009f35a155a218eb"
SCRIPT = Path("scripts/prepare_three_city_common_reference.py")
START = "2024-11-16T00:00:00+08:00"
END = "2025-03-16T00:00:00+08:00"
HOURS = 2880
H = (32.715 + 1.892 + 15.253 + 36. + 18.440) / 1000
C = (63.6 * .012 * 950 * 840 + 48 * .025 * 650 * 1200 + 48 * .010 * 950 * 840) / 3_600_000
PARAMETERS = {"T2M": ("outdoor_temp", "C"), "RH2M": ("relative_humidity_pct", "%"),
              "WS10M": ("wind_speed_10m_m_s", "m/s"), "ALLSKY_SFC_SW_DWN": ("solar_irradiation_wh_m2", "Wh/m^2")}
CITIES = {
    "urumqi": {"profile": "scenario1_urumqi", "label": "乌鲁木齐", "request_lon": 87.6168, "request_lat": 43.8256,
               "geometry": [87.617, 43.826, 737.35],
               "raw_sha256": "73eaee32e09fde57d63351b9446978cb24240425ce8fe66aa9c6d95105586b5d",
               "receipt_sha256": "e97c5321f2e642575190c9e7bde6bf2587d87a2c2108554807f707e99a3302a7"},
    "guyuan": {"profile": "scenario2_guyuan", "label": "固原", "request_lon": 106.2426, "request_lat": 36.0159,
               "geometry": [106.243, 36.016, 1847.81],
               "raw_sha256": "9751ff2bdf51261526a68f901660cb651b5e153b8ff6ce9dfb74d0114ee72600",
               "receipt_sha256": "b66358215130401c8a061b61e8993a84230868c38db53c37f8fa5939ada67bef"},
    "wuhan": {"profile": "scenario3_wuhan", "label": "武汉", "request_lon": 114.3055, "request_lat": 30.5928,
              "geometry": [114.305, 30.593, 29.3],
              "raw_sha256": "008d00e509ec288d76fb6f01df1dcbb85d5090783786cfc9feb63a919044fa70",
              "receipt_sha256": "35f728e1cd22531b01fee93498e3f5f0def55666bc84b0850890911e0f087d0b"},
}
CONTEXT = {
    "prior_package_audit.json": "docs/source_intake_20261002/city_batch_02/common/package_independent_audit.json",
    "prior_lst_identity.md": "data/verified_weather_20261002/README.md",
    "three_city_decisions.md": "docs/source_intake_20261002/planning_user_update/THREE_CITY_DECISIONS.md",
    "prior_wuhan_method_protocol.json": "docs/implementation_20261002_followup/representative_season_confirmation/run_01/protocol.json",
}
MANIFEST = DOC / "manifest.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=reject_duplicate_keys)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_names(city):
    profile = CITIES[city]["profile"]
    return profile + "_UTC.json", profile + "_download.json"


def exact_clock():
    return pd.date_range(pd.Timestamp(START).tz_convert("UTC"), pd.Timestamp(END).tz_convert("UTC"), freq="h", inclusive="left")


def verify_source_hashes(directory):
    for city, info in CITIES.items():
        for name, expected in zip(source_names(city), (info["raw_sha256"], info["receipt_sha256"])):
            path = Path(directory) / name
            if not path.is_file() or sha(path) != expected:
                raise ValueError(f"Original source missing or changed: {name}")


def validate_original(raw, receipt, city):
    info = CITIES[city]
    header = raw["header"]
    if header["time_standard"] != "UTC":
        raise ValueError("Need source-declared UTC; LST cannot be relabelled as Beijing time")
    if set(header["sources"]) != {"SYN1DEG", "MERRA2", "POWER"} or header["api"]["version"] != "v2.10.2":
        raise ValueError("Source product/API identity changed")
    if raw["geometry"]["type"] != "Point" or raw["geometry"]["coordinates"] != info["geometry"]:
        raise ValueError("NASA point/elevation identity changed; not a WMO station")
    if receipt["profile"] != info["profile"] or receipt["file"] != source_names(city)[0] or receipt["status"] != "downloaded":
        raise ValueError("Receipt object identity mismatch")
    if receipt["header"] != header or receipt["parameters"] != raw["parameters"]:
        raise ValueError("Receipt/source metadata disagree")
    url = urlsplit(receipt["url"])
    query = parse_qs(url.query)
    if url.netloc != "power.larc.nasa.gov" or url.path != "/api/temporal/hourly/point":
        raise ValueError("Receipt is not the archived NASA point API request")
    if query.get("time-standard") != ["UTC"] or query.get("start") != [header["start"]] or query.get("end") != [header["end"]]:
        raise ValueError("Request/source UTC date identity mismatch")
    if set(query["parameters"][0].split(",")) != set(PARAMETERS):
        raise ValueError("Request parameter identity mismatch")
    if abs(float(query["longitude"][0]) - info["request_lon"]) > 1e-9 or abs(float(query["latitude"][0]) - info["request_lat"]) > 1e-9:
        raise ValueError("Request coordinate identity mismatch")
    values = raw["properties"]["parameter"]
    if set(values) != set(PARAMETERS):
        raise ValueError("Need the four archived native weather fields only")
    full_clock = pd.date_range(pd.to_datetime(header["start"], format="%Y%m%d", utc=True),
                               pd.to_datetime(header["end"], format="%Y%m%d", utc=True) + pd.Timedelta(days=1), freq="h", inclusive="left")
    expected_keys = full_clock.strftime("%Y%m%d%H").tolist()
    for key, (_, unit) in PARAMETERS.items():
        if raw["parameters"][key]["units"] != unit:
            raise ValueError(f"Native unit mismatch: {key}")
        if list(values[key]) != expected_keys:
            raise ValueError("Original hourly keys are not exact continuous ordered UTC; no year stitching")
        numeric = np.asarray(list(values[key].values()), dtype=float)
        if not np.isfinite(numeric).all() or np.any(numeric == header["fill_value"]):
            raise ValueError("Missing/nonfinite native weather; no imputation")
    if not exact_clock().isin(full_clock).all():
        raise ValueError("Original does not cover the predeclared common 120-day window")


def build_frame(city, directory):
    raw_name, receipt_name = source_names(city)
    raw, receipt = read_json(Path(directory) / raw_name), read_json(Path(directory) / receipt_name)
    validate_original(raw, receipt, city)
    clock = exact_clock()
    keys = clock.strftime("%Y%m%d%H")
    local = clock.tz_convert("Asia/Shanghai")
    frame = pd.DataFrame({"time": [x.isoformat() for x in local],
                          "interval_end": [x.isoformat() for x in local + pd.Timedelta(hours=1)],
                          "time_utc": [x.isoformat() for x in clock], "source_time_utc": keys,
                          "city_id": city, "scenario": f"{city}_common_prototype_climate_2024_2025",
                          "boundary_group": "continuous_common_120_days", "dt_hours": 1.,
                          "scenario_probability": 1., "period_weight": 1.})
    for key, (column, _) in PARAMETERS.items():
        frame[column] = [raw["properties"]["parameter"][key][label] for label in keys]
    frame["internal_gain_kw"] = .2
    frame["solar_gain_kw"] = 0.  # Excluded pathway, not observed zero radiation.
    frame["heating_service"] = 1
    frame["heat_reference_no_gain_kw"] = H * np.maximum(20 - frame.outdoor_temp, 0)
    frame["steady_heat_demand_kw"] = np.maximum(H * (20 - frame.outdoor_temp) - .2, 0)
    frame["steady_cooling_demand_kw"] = np.maximum(H * (frame.outdoor_temp - 27) + .2, 0)
    frame["steady_temperature_c"] = np.clip(frame.outdoor_temp + .2 / H, 20, 27)
    return frame


def validate_frame(frame, city, expected):
    if len(frame) != HOURS or any(pd.Timestamp(x).tzinfo is None for x in frame.time):
        raise ValueError("Need 2880 explicitly timezone-aware rows")
    if not pd.DatetimeIndex(pd.to_datetime(frame.time, utc=True)).equals(exact_clock()):
        raise ValueError("Common clock changed: gap, duplicate, order or timezone shift")
    if any(name in frame for name in ("electricity_price", "grid_emission_factor", "demand_fee", "capacity_fee")):
        raise ValueError("Weather-only phase must not inherit financial/carbon defaults")
    if not np.isfinite(frame.select_dtypes(include="number").to_numpy()).all():
        raise ValueError("Nonfinite derived input")
    try:
        pd.testing.assert_frame_equal(frame.reset_index(drop=True), expected, check_dtype=False, rtol=0, atol=1e-10)
    except AssertionError as exc:
        raise ValueError("Source weather, physical formula, column, time convention or city identity changed") from exc


def common_physics(root=ROOT):
    if sha(root / BASE_ADMISSION) != BASE_ADMISSION_SHA256:
        raise ValueError("Previously admitted base report identity changed")
    gate = read_json(root / BASE_ADMISSION)
    for relative, expected in gate["input_sha256"].items():
        if sha(root / relative) != expected:
            raise ValueError(f"Existing base admission changed: {relative}")
    config = yaml.safe_load((root / BASE_CONFIG).read_text(encoding="utf-8"))
    b = config["building_thermal"]["buildings"][0]
    if not (abs(b["heat_loss_kw_per_c"] - H) < 1e-12 and abs(b["thermal_capacity_kwh_per_c"] - C) < 1e-12 and b["area_m2"] == 48):
        raise ValueError("Common prototype H/C/geometry changed")
    return {
        "physical_reference_admitted": gate["physical_reference_admitted"] and gate["rc_reference_admitted"],
        "reference_config": BASE_CONFIG.as_posix(), "reference_config_sha256": sha(root / BASE_CONFIG),
        "reference_admission": BASE_ADMISSION.as_posix(), "reference_admission_sha256": sha(root / BASE_ADMISSION),
        "geometry": {"length_m": 8., "width_m": 6., "height_m": 2.7, "floor_area_m2": 48., "volume_m3": 129.6,
                     "south_window_area_m2": 12., "opaque_wall_area_m2": 63.6},
        "heat_loss_kw_per_c": H, "inner_material_capacity_kwh_per_c": C,
        "building_thermal": deepcopy(config["building_thermal"]),
        "grades": deepcopy(config["grades"]), "nodes": deepcopy(config["nodes"]),
        "ideal_source": {key: deepcopy(config["sources"][0][key]) for key in ("name", "type", "node", "resource_kind", "allowed_grades", "supply_temp_c", "existing_capacity_kw", "max_new_capacity_kw", "max_retire_capacity_kw", "efficiency")},
        "ideal_cooling": {"enabled": True, "eer": 1., "eer_min": 1., "eer_max": 1., "capacity_equivalence": 1.},
        "internal_gain_kw": .2, "solar_gain_path": "excluded, gain0; native radiation remains in weather metadata",
        "model_scope": "Same BESTEST-derived structural approximation, high-altitude reference infiltration retained in H, ideal 1000kW upper bound/EER1, same20-27C service and initial/terminal20C. Not a local building or calibrated effective C.",
        "economic_configuration_inherited": False,
        "not_a_complete_solver_config": True,
    }


def confirmation_rows():
    result = []
    for city in CITIES:
        result.append({"city_id": city, "start_inclusive": START, "end_exclusive": END,
                       "window_role": "known Wuhan development/diagnostic window" if city == "wuhan" else "proposed confirmation window reserved for new price-coupled method",
                       "new_method_development_use_status": "Wuhan permitted development; existing same-season diagnostics recorded" if city == "wuhan" else "pending algorithm usage record and parent freeze audit",
                       "held_out_confirmation_certified": False,
                       "prior_project_weather_exposure": "Already present and audited in the project; not globally unseen weather",
                       "confirmation_claim_limit": "Climate transfer of same public prototype only; not external local field observations or full local heating-season validation"})
    return pd.DataFrame(result)


def protocol(physics):
    return {
        "case_id": "BESTEST600_same_prototype_three_city_common_climate_20261002",
        "stage": "physical_driver_preparation_only",
        "time": {"start_inclusive": START, "end_exclusive": END, "timezone": "Asia/Shanghai", "hours": HOURS, "days": 120,
                 "step_hours": 1, "interval_convention": "UTC native hour label converted to local start of [t,t+1h); driver treated as piecewise constant; not a claim of instantaneous site measurements",
                 "local_heating_season_claimed": False, "annualized": False, "continuous_not_representative_days": True},
        "comparison": "Same geometry/H/C/internal gain/solar exclusion/comfort/initial-terminal state/ideal capacity. Only weather fields and identifying city labels differ. T2M is the active thermal weather driver; RH/wind/radiation preserved as unused weather metadata.",
        "physical_reference": physics,
        "derived_fields": {"heat_reference_no_gain_kw": "H*max(20-Tout,0); capacity/compression anchor, not metered heat or actual RC delivery",
                           "steady_heat_demand_kw": "max(H*(20-Tout)-0.2,0)", "steady_cooling_demand_kw": "max(H*(Tout-27)+0.2,0)",
                           "steady_temperature_c": "clip(Tout+0.2/H,20,27); algebraic reference, not simulated RC state or observation"},
        "pricing_scope": "No price/basic charge/emissions factor in these tables. No Hubei tariff applied to other cities. Before any monetary optimization, use a separately frozen admissible tariff/account or explicitly declared common methodological tariff, never constructor defaults.",
        "excluded": ["annual-year concatenation", "TMY mixed with calendar weather", "gap imputation", "random OSM allocation of city planning loads", "actual local heat-load observations", "actual local device/SCADA validation", "full economics"],
        "scada_required_for_progress": False,
        "confirmation_status": "Urumqi/Guyuan proposed reserved windows pending algorithm usage records and parent freeze. Wuhan is a known reused development/diagnostic window. No claim entire project has never seen any city weather.",
        "score_mapping": {"S1_model_architecture_20": "Preserve common physical interface; no additional architecture proof in this stage",
                          "S2_dynamic_simulation_15": "Admit exact shared continuous1h weather calendar; subsequent RC solve/audit not yet performed",
                          "S3_scenario_completeness_15": "Three climate drivers for the same public prototype; not three validated local engineering systems",
                          "S4_multiobjective_15": "No new frontier/price experiment in this stage",
                          "S5_multiscale_10": "No new network/aggregation experiment in this stage",
                          "S6_analysis_visualization_15": "Source/calendar comparison and limitations; no solver-result chart yet",
                          "S7_code_reproducibility_10": "Offline source snapshots, exact hashes, field mapping and negative-input tests"},
        "solver_run_performed": False, "full_economics_admitted": False, "local_prediction_accuracy_claimed": False,
    }


def provenance(frames):
    units = {"time": "ISO8601+08:00", "interval_end": "ISO8601+08:00", "time_utc": "ISO8601 UTC", "source_time_utc": "YYYYMMDDHH UTC",
             "dt_hours": "h", "scenario_probability": "1", "period_weight": "1", "outdoor_temp": "degC", "relative_humidity_pct": "%",
             "wind_speed_10m_m_s": "m/s", "solar_irradiation_wh_m2": "Wh/m2 per hour", "internal_gain_kw": "kW", "solar_gain_kw": "kW",
             "heating_service": "1", "heat_reference_no_gain_kw": "kW", "steady_heat_demand_kw": "kW", "steady_cooling_demand_kw": "kW", "steady_temperature_c": "degC"}
    reverse = {column: parameter for parameter, (column, _) in PARAMETERS.items()}
    rows = []
    for city, frame in frames.items():
        info = CITIES[city]
        for field in frame:
            weather = field in reverse or field in {"time", "time_utc", "source_time_utc", "interval_end"}
            derived = field.startswith("steady_") or field == "heat_reference_no_gain_kw"
            formula = reverse.get(field, protocol({})["derived_fields"].get(field, "Explicit common comparison boundary/identifier; not an external observation"))
            if weather and field not in reverse:
                formula = "Parse original UTC hourly keys; convert to Asia/Shanghai; exact selected [start,end); end=start+1h"
            if field == "internal_gain_kw":
                formula = "BESTEST prescribed200W=0.2kW sensible internal gain; one-state lump"
            if field == "solar_gain_kw":
                formula = "Excluded gain pathway=0; no observed-zero-solar claim"
            rows.append({"artifact": (DATA / f"{city}_hourly.csv").as_posix(), "city_id": city, "field": field,
                         "unit": units.get(field, "identifier"), "evidence_kind": "native gridded weather" if weather else "derived public prototype reference" if derived else "prescribed reference/protocol",
                         "source_id": f"NASA_{city.upper()}" + (";BESTEST_REFERENCE" if derived else "") if weather or derived else "BESTEST_REFERENCE;COMPARISON_PROTOCOL",
                         "source_sha256": info["raw_sha256"] if weather or derived else "see common_physical_reference and base admission",
                         "formula_or_locator": formula, "allowed_scope": "Common120-day climate comparison only; reference load/state fields are calculated, not locally metered/calibrated"})
    return pd.DataFrame(rows)


def source_inventory(evidence, context_hashes):
    rows = {}
    for city, info in CITIES.items():
        raw_name, receipt_name = source_names(city)
        raw, receipt = read_json(evidence / raw_name), read_json(evidence / receipt_name)
        rows[city] = {"city_label": info["label"], "raw_path": (EVIDENCE / raw_name).as_posix(), "raw_sha256": info["raw_sha256"],
                      "receipt_path": (EVIDENCE / receipt_name).as_posix(), "receipt_sha256": info["receipt_sha256"],
                      "request_url": receipt["url"], "requested_lat_lon": [info["request_lat"], info["request_lon"]],
                      "returned_lon_lat_elevation_m": raw["geometry"]["coordinates"],
                      "identity": "NASA POWER point output from MERRA2/SYN1DEG/POWER gridded products; no WMO station ID or site-meter identity",
                      "original_header": raw["header"], "native_units": raw["parameters"], "original_hours": 4560,
                      "original_coverage_utc": ["2024-10-09T00:00:00+00:00", "2025-04-17T00:00:00+00:00"],
                      "original_download_time": None, "download_time_note": "Archived receipt has no original fetch timestamp; not invented during this copy"}
    return {"cities": rows, "context_snapshots": {name: {"original_path": original, "snapshot_path": (EVIDENCE / name).as_posix(), "sha256": context_hashes[name]} for name, original in CONTEXT.items()},
            "excluded_weather_sources": "VerifiedWeather clean LST labels retained in prior archive, not relabelled UTC+8. CSWD TMY is separate and not used. No unlocked Open-Meteo/Meteostat bias adjustment.",
            "source_authenticity_scope": "Exact previously archived NASA responses/receipts; hashing is file identity, not proof that local microclimate error is zero"}


def acceptance(frames):
    cities = {}
    for city, frame in frames.items():
        cities[city] = {"hours": len(frame), "days": frame.time.str[:10].nunique(), "start": frame.time.iloc[0], "last_start": frame.time.iloc[-1],
                        "last_interval_end": frame.interval_end.iloc[-1], "same_common_clock": True,
                        "timezone_conversion_exact": True, "original_source_coverage_complete": True,
                        "duplicate_or_missing_hours": 0, "nonfinite_or_fill_values": 0, "source_weather_columns_equal": True,
                        "native_units_and_point_receipt_checked": True, "derived_formulas_checked": True,
                        "financial_or_carbon_columns_present": False}
    return {"physical_drivers_ready": True, "calendar_and_source_acceptance_passed": True, "cities": cities,
            "same_geometry_H_C_service": True, "common_window_not_local_full_seasons": True,
            "source_imputation_or_year_stitching": False, "solver_matrix_or_simulation_run": False,
            "method_confirmation_certified": False, "scada_required": False, "full_economics_admitted": False}


def prepare(root=ROOT, source_dir=None):
    root = Path(root)
    if (root / MANIFEST).exists():
        raise FileExistsError("Completed common-reference evidence exists; use --verify-only, never overwrite its freeze")
    physics = common_physics(root)
    # Prefer portable local snapshots after an interrupted first preparation.
    sources = Path(source_dir) if source_dir else root / EVIDENCE
    if source_dir is None and not all((sources / name).exists() for city in CITIES for name in source_names(city)):
        sources = DEFAULT_SOURCES
    verify_source_hashes(sources)
    frames = {city: build_frame(city, sources) for city in CITIES}
    for city, frame in frames.items():
        validate_frame(frame, city, build_frame(city, sources))
    context_bytes = {name: (root / original).read_bytes() for name, original in CONTEXT.items()}
    for directory in (DATA, DOC, EVIDENCE):
        (root / directory).mkdir(parents=True, exist_ok=True)
    for city in CITIES:
        for name in source_names(city):
            if (sources / name).resolve() != (root / EVIDENCE / name).resolve():
                (root / EVIDENCE / name).write_bytes((sources / name).read_bytes())
        frames[city].to_csv(root / DATA / f"{city}_hourly.csv", index=False, float_format="%.12g")
    for name, content in context_bytes.items():
        (root / EVIDENCE / name).write_bytes(content)
    context_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in context_bytes.items()}
    write_json(root / DOC / "source_inventory.json", source_inventory(root / EVIDENCE, context_hashes))
    write_json(root / DOC / "common_physical_reference.json", physics)
    write_json(root / DOC / "protocol.json", protocol(physics))
    write_json(root / DOC / "validation.json", acceptance(frames))
    provenance(frames).to_csv(root / DOC / "field_provenance.csv", index=False, encoding="utf-8")
    confirmation_rows().to_csv(root / DOC / "confirmation_candidates.csv", index=False, encoding="utf-8")
    gate = read_json(root / BASE_ADMISSION)
    artifacts = [DATA / f"{city}_hourly.csv" for city in CITIES]
    artifacts += [DOC / name for name in ("source_inventory.json", "common_physical_reference.json", "protocol.json", "validation.json", "field_provenance.csv", "confirmation_candidates.csv")]
    artifacts += [EVIDENCE / name for city in CITIES for name in source_names(city)] + [EVIDENCE / name for name in CONTEXT]
    manifest = {"schema_version": 1, "stage": "source_calendar_and_physical_driver_acceptance_only",
                "input_sha256": {**gate["input_sha256"], BASE_ADMISSION.as_posix(): sha(root / BASE_ADMISSION)},
                "code_sha256": {SCRIPT.as_posix(): sha(root / SCRIPT)},
                "artifact_sha256": {path.as_posix(): sha(root / path) for path in artifacts},
                "time_start_inclusive": START, "time_end_exclusive": END,
                "city_count": 3, "hour_count_per_city": HOURS, "field_provenance_rows": len(provenance(frames)),
                "solver_run_performed": False, "full_economics_admitted": False,
                "confirmation_claim": "Proposed Urumqi/Guyuan holdouts awaiting parent/algorithm freeze; not yet certified"}
    write_json(root / MANIFEST, manifest)
    return verify(root)


def verify(root=ROOT):
    root = Path(root)
    manifest = read_json(root / MANIFEST)
    for category in ("input_sha256", "code_sha256", "artifact_sha256"):
        for relative, expected in manifest[category].items():
            if sha(root / relative) != expected:
                raise ValueError(f"Frozen {category} file changed: {relative}")
    verify_source_hashes(root / EVIDENCE)
    physics = common_physics(root)
    if read_json(root / DOC / "common_physical_reference.json") != physics or read_json(root / DOC / "protocol.json") != protocol(physics):
        raise ValueError("Common physical/protocol scope changed")
    frames = {}
    for city in CITIES:
        expected = build_frame(city, root / EVIDENCE)
        frame = pd.read_csv(root / DATA / f"{city}_hourly.csv", dtype={"source_time_utc": str})
        validate_frame(frame, city, expected)
        frames[city] = frame
    if read_json(root / DOC / "validation.json") != acceptance(frames):
        raise ValueError("Calendar/source acceptance changed")
    fields = pd.read_csv(root / DOC / "field_provenance.csv")
    pd.testing.assert_frame_equal(fields, provenance(frames), check_dtype=False)
    if fields[["artifact", "field"]].duplicated().any() or len(fields) != sum(len(frame.columns) for frame in frames.values()):
        raise ValueError("Field coverage incomplete/duplicated")
    pd.testing.assert_frame_equal(pd.read_csv(root / DOC / "confirmation_candidates.csv"), confirmation_rows(), check_dtype=False)
    inventory = read_json(root / DOC / "source_inventory.json")
    context_hashes = {name: sha(root / EVIDENCE / name) for name in CONTEXT}
    if inventory != source_inventory(root / EVIDENCE, context_hashes):
        raise ValueError("Source/point/receipt or context inventory changed")
    return {"passed": True, "city_count": 3, "hours_per_city": HOURS, "days": 120,
            "start_inclusive": START, "end_exclusive": END, "timezone": "Asia/Shanghai",
            "field_provenance_rows": len(fields), "prices_or_emission_factors_inherited": False,
            "solver_run_performed": False, "confirmation_holdout_certified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Read-only verification of the completed local freeze")
    parser.add_argument("--source-dir", type=Path, help="Existing archived NASA UTC JSON/receipt directory; no fetching")
    args = parser.parse_args()
    result = verify() if args.verify_only else prepare(source_dir=args.source_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
