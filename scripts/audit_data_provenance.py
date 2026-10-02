"""Read-only, conservative field/column provenance inventory for the candidate.

Run: python scripts/audit_data_provenance.py
Only this script's declared audit output directories are written. No input,
core, historic example or existing result is edited. Source URLs are references,
not evidence of support: reviewed, exact clauses and applicability are separate.
Network access is deliberately absent; review/downloads are explicit artifacts.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
import zipfile
import zlib
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
DOCS = Path("docs/execution_20261001/data_truth_audit")
OUTPUT = Path("outputs/competition_20261001/C3_data_truth")
CATEGORIES = {
    "public_measurement_or_weather_product": "公开实际测量或气象产品",
    "official_statistics_or_standard": "官方统计/规范",
    "published_experiment_parameter": "文献公开试验参数",
    "explainable_design_or_solver_setting": "可解释设计/求解设置",
    "unsubstantiated_assumption": "无依据假设",
    "actual_solver_output": "真实求解输出",
}
GENERATORS = (
    "crawl_public_data.py", "prepare_competition_case.py", "build_public_scenario_inputs.py",
    "build_multiyear_public_scenario_inputs.py", "build_v11_4_representative_days.py",
    "build_v11_6_multiyear_representative_days.py", "build_v11_8_multiyear_stochastic_bundle.py",
    "run_competition_case.py",
)
SETTINGS = {
    "time_step_hours", "period_weight", "representative_day_weight", "mip_gap",
    "requested_mip_gap", "time_limit_seconds", "time_limit", "random_state", "seed",
    "n_clusters", "clusters", "max_iterations", "iterations", "threads", "schema_version",
    "expected_hours", "rows", "hours", "year", "years", "days", "absolute_tolerance",
    "relative_tolerance", "feasibility_tolerance", "max_unserved_rate", "max_scenario_unserved_rate",
    "unserved_heat_penalty_yuan_per_kwh", "unserved_penalty_yuan_per_kwh", "risk_weight",
    "risk_aversion", "alpha", "cvar_alpha", "objective_tolerance", "initial_soc_fraction",
    "final_soc_min_fraction", "max_shift_fraction", "boundary_group", "scenario_probability",
}
RAW_WEATHER = {"outdoor_temp": "T2M", "relative_humidity_pct": "RH2M",
               "wind_speed_10m_m_s": "WS10M", "solar_irradiance_kwh_m2_hour": "ALLSKY_SFC_SW_DWN"}
WEATHER_COLUMNS = set(RAW_WEATHER) | {"relative_humidity", "solar_irradiance_w_m2"}
IDENTITY_COLUMNS = {"time", "timestamp", "timestamp_local_solar", "local_time", "interval_end_utc",
                    "source_time_lst", "source_weather_file", "boundary_group", "scenario", "name",
                    "node", "heat_grade", "building_id", "district_id", "archetype", "load_mode",
                    "parameter_identity", "heating_service", "cooling_service", "category", "asset",
                    "grade", "variable", "unit", "source_id", "indicator", "model_mapping", "value_type"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def delivery_policy(entries: list[dict], external_validation: dict, weather_addendum: dict | None = None) -> dict:
    """Positive, field-specific admission; category/source names never grant admission."""
    approved = [r for r in entries if r["allowed_true_project_experiment"]]
    weather = (weather_addendum or {}).get("entries", [])
    assert not weather or weather_addendum.get("complete") is True
    weather_ids = {r["id"] for r in weather}
    approved = [r for r in approved if r["id"] not in weather_ids] + weather
    external = [r for r in entries if "external_rc_benchmark_20261001/observations.csv" in r["file"]
                and r["allowed_documented_public_data_study"]]
    return {
        "schema_version": 2,
        "all_current_C1_S3_physical_experiments_pass_true_data_gate": False,
        "admission_rule": "Only exact file SHA256 + field + value + documented applicability in the positive whitelist is admitted. Every other field is blocked as a true-project input; category labels and source catalog matches do not grant admission.",
        "strict_input_field_whitelist": [{k: r[k] for k in ("id", "file", "field", "file_sha256", "value", "source_ids", "unit", "time_scope", "geographic_scope", "applicability")}
                                         for r in approved],
        "not_in_true_project_input_whitelist": [r["id"] for r in entries if not r["allowed_true_project_experiment"] and r["id"] not in weather_ids],
        "weather_incremental_review": {"file": (DOCS / "weather_field_addendum.json").as_posix(),
                                       "superseded_baseline_field_ids": sorted(weather_ids),
                                       "approved_product_fields": len(weather),
                                       "scope": "Four NASA product fields only; LST grid product, no building/load/tariff/ground-station certification"},
        "documented_public_product_or_reference_candidates": [r["id"] for r in entries if r["allowed_documented_public_data_study"] and r["category"] != "explainable_design_or_solver_setting"],
        "forbidden_as_true_project_inputs": [r["id"] for r in entries if r["category"] == "unsubstantiated_assumption"],
        "external_public_experiment_whitelist": {
            "scope": "Fraunhofer Annex71 N2 public German measurement study only; no Wuhan/site-parameter certification",
            "raw_reconstruction_status": external_validation.get("status"),
            "fields": [{k: r[k] for k in ("id", "file", "field", "file_sha256", "category", "verification_status", "value_method", "derivation_formula", "unit", "source_ids", "applicability")}
                       for r in external],
            "limitations": ["Publisher-filled gaps have no row flags", "Electrical input is not a direct delivered-heat meter", "Indoor temperature is a derived room-sensor spatial average", "Solar reference is an explicitly declared irradiance proxy", "Time-bin endpoint convention is inferred, not explicitly specified", "CC BY-SA4.0 attribution required", "Fitted H/C or fit bounds are analysis results/protocol, not source-certified Wuhan values"],
        },
        "historic_examples_policy": "Preserve originals, explicitly label algorithm fixtures; exclude from formal real-data experiment selection.",
        "solver_outputs_policy": "Preserve verified run files as program verification; do not claim real-site savings/investments until every relevant physical/economic input passes exact provenance/applicability.",
        "release_conditions": ["every consumed numerical input has exact-source mapping or documented non-empirical protocol status",
                               "unit/year/geography/customer/equipment condition compatible", "no unverified physical assumption in selected experiment",
                               "raw source checksums and exact locators retained", "source/parameter replacement triggers new solve and audit identity"],
    }


def verify_weather_addendum(root: Path) -> dict:
    """Independently compare archived raw API values; no network or C0 verifier import."""
    output = root / "outputs/competition_20261001/C0_weather_02"
    manifest_path = output / "manifest.json"
    report_path = output / "verification_report.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("complete") is True
    for name, sha in manifest["files"].items():
        assert digest(output / name) == sha, f"weather run artifact changed: {name}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("complete") is True and len(report["records"]) == 15
    metadata_path = root / "data/public_sources/crawl_metadata.json"
    assert digest(metadata_path) == manifest["source_metadata_sha256"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))["weather_profiles"]
    addendum_path = root / "data/verified_weather_20261002/request_provenance_addendum.json"
    addendum = json.loads(addendum_path.read_text(encoding="utf-8"))
    assert addendum["does_not_recover_original_retrieval_date_or_api_version"] is True
    mapping = {"T2M": ("outdoor_temp", "C"), "RH2M": ("relative_humidity_pct", "%"),
               "WS10M": ("wind_speed_10m_m_s", "m/s"),
               "ALLSKY_SFC_SW_DWN": ("solar_irradiance_kwh_m2_hour", "Wh/m^2")}
    expected_pairs = {(p, y) for p in ("scenario1_urumqi", "scenario2_guyuan", "scenario3_wuhan") for y in range(2020, 2025)}
    assert {(r["profile"], r["year"]) for r in report["records"]} == expected_pairs
    entries, checks = [], []
    for record in report["records"]:
        file, raw = root / record["archive"], root / record["response_file"]
        assert digest(file) == record["archive_sha256_before"] == record["archive_sha256_after"]
        assert digest(raw) == record["response_sha256"]
        parsed = urlparse(record["request_url"])
        query = parse_qs(parsed.query)
        year, profile = record["year"], record["profile"]
        assert parsed.scheme == "https" and parsed.netloc == "power.larc.nasa.gov" and parsed.path == "/api/temporal/hourly/point"
        assert query["time-standard"] == ["LST"]
        assert query["start"] == [f"{year}0101"] and query["end"] == [f"{year}1231"]
        assert set(query["parameters"][0].split(",")) == set(mapping)
        key = f"{profile}_{year}"
        original_url = metadata[key]["request_url"]
        if year == 2023:
            assert original_url == f"reused_existing_file:{file.name}"
            prior_query = parse_qs(urlparse(metadata[f"{profile}_2024"]["request_url"]).query)
            assert all(query[k] == prior_query[k] for k in ("latitude", "longitude", "parameters", "time-standard", "community"))
            assert record["request_identity"].startswith("new_verification_query")
        else:
            assert original_url == record["request_url"]
        payload = json.loads(raw.read_bytes())
        frame = pd.read_csv(file)
        times = pd.date_range(f"{year}-01-01", f"{year+1}-01-01", freq="h", inclusive="left")
        assert pd.DatetimeIndex(pd.to_datetime(frame["time"])).equals(times)
        assert payload["header"]["time_standard"] == "LST"
        assert payload["header"]["start"] == f"{year}0101" and payload["header"]["end"] == f"{year}1231"
        coords = payload["geometry"]["coordinates"]
        # These raw responses return coordinates to 3 decimal places. Retain
        # both identities; the rounded geometry is not an exact survey point.
        assert abs(coords[0] - float(query["longitude"][0])) <= 0.000501
        assert abs(coords[1] - float(query["latitude"][0])) <= 0.000501
        keys = times.strftime("%Y%m%d%H").tolist()
        for parameter, (column, unit) in mapping.items():
            values = payload["properties"]["parameter"][parameter]
            assert set(values) == set(keys)
            assert payload["parameters"][parameter]["units"] == unit
            reference = np.array([values[t] for t in keys], dtype=float)
            archived = frame[column].to_numpy(dtype=float)
            assert np.isfinite(reference).all() and np.isfinite(archived).all()
            assert not np.any(reference == payload["header"]["fill_value"])
            error = float(np.max(np.abs(reference - archived)))
            assert error <= 1e-9, (file, parameter, error)
            field = f"column:{column}"
            entry = {"id": hashlib.sha256((record["archive"] + "#" + field).encode()).hexdigest()[:20],
                     "file": record["archive"], "field": field, "kind": "csv_column",
                     "file_sha256": digest(file), "value": {"rows": len(frame), "minimum": float(archived.min()), "maximum": float(archived.max())},
                     "category": "public_measurement_or_weather_product", "verification_status": "every_hour_equal_to_archived_official_response_independently_checked",
                     "source_ids": ["COMMON_NASA_POWER"], "source_urls": [record["request_url"]],
                     "local_raw_materials": [record["response_file"], relative(report_path, root), relative(addendum_path, root)],
                     "source_sha256": [digest(raw), digest(report_path), digest(addendum_path)],
                     "unit": unit, "specific_page_table_clause": f"properties.parameter.{parameter}.YYYYMMDDHH; parameters.{parameter}.units; header.time_standard/api/start/end; geometry.coordinates",
                     "time_scope": f"{year} complete hourly LST; current response {payload['header']['api']['version']}; retrieved {record.get('retrieved_at_utc')}; original retrieval version/date not recovered",
                     "geographic_scope": {"profile": profile, "requested_longitude": query["longitude"][0], "requested_latitude": query["latitude"][0], "returned_geometry": payload["geometry"], "returned_coordinate_precision": "3 decimal places; rounding tolerance 0.000501 degree"},
                     "value_method": "raw_official_weather_product_column", "derivation_formula": None,
                     "applicability": "NASA grid product only, not ground-station/site calibration. Clock is LST; legal civil-time conversion must be explicitly verified before tariff/occupancy joins. Legacy radiation column stores Wh/m2, not kWh/m2. Equality to this current archived response does not reconstruct the historical API version or retrieval date.",
                     "allowed_true_project_experiment": True, "allowed_documented_public_data_study": True,
                     "maximum_absolute_error": error, "original_request_identity": record["request_identity"],
                     "supersedes_existing_field_ledger_record": True}
            entries.append(entry)
            checks.append({"file": record["archive"], "field": column, "rows": len(frame), "maximum_absolute_error": error,
                           "original_sha256": digest(file), "response_file": record["response_file"], "response_sha256": digest(raw)})
    return {"schema_version": 1, "complete": True, "reviewed_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "Independent incremental review only; frozen 8051-entry inventory is not rescanned",
            "network_calls": 0, "external_verifier_imported_or_executed": False,
            "source_manifest": relative(manifest_path, root), "source_manifest_sha256": digest(manifest_path),
            "source_report": relative(report_path, root), "source_report_sha256": digest(report_path),
            "archive_files": 15, "unique_hours": sum(r["rows"] for r in report["records"]),
            "numeric_values_independently_checked": sum(r["rows"] for r in checks),
            "field_count": len(entries), "checks": checks, "entries": entries,
            "certifies_derived_loads_buildings_prices_or_ground_station_accuracy": False}


def pointer(parts: tuple) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def walk(value: Any, parts: tuple = (), context: dict | None = None):
    context = context or {}
    if isinstance(value, dict):
        local = {**context, **{k: v for k, v in value.items() if not isinstance(v, (dict, list))}}
        for key, child in value.items():
            yield from walk(child, (*parts, key), local)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from walk(child, (*parts, i), context)
    elif numeric(value) or isinstance(value, bool):
        yield parts, value, context


def infer_unit(name: str, path: str = "") -> str | None:
    text = name.lower()
    rules = [
        ("heat_loss_kw_per_c", "kW/K"), ("thermal_capacity_kwh_per_c", "kWh/K"),
        ("heat_loss_w_per_m2_c", "W/(m2 K)"), ("capacity_wh_per_m2_c", "Wh/(m2 K)"),
        ("yuan_per_kwh", "CNY/kWh"), ("yuan_per_kw_year", "CNY/(kW yr)"),
        ("yuan_per_kw", "CNY/kW"), ("yuan_per_m", "CNY/m"),
        ("kg_per_kwh", "kg/kWh"), ("emission_factor", "kgCO2/kWh"),
        ("electricity_price", "CNY/kWh"), ("discount_rate", "fraction/year"),
        ("carbon_price", "CNY/kgCO2"), ("wind_speed", "m/s"),
        ("solar_irradiance_w_m2", "W/m2"), ("relative_humidity_pct", "%"),
        ("relative_humidity", "fraction"), ("per_hour", "1/h"),
        ("temperature", "degC"), ("temp_c", "degC"), ("outdoor_temp", "degC"),
        ("area_m2", "m2"), ("diameter_mm", "mm"), ("roughness_mm", "mm"),
        ("capacity_kwh", "kWh"), ("_kwh", "kWh"), ("_kw", "kW"),
        ("hours", "h"), ("seconds", "s"), ("years", "yr"),
        ("capex_total_yuan", "CNY"), ("fixed_yuan", "CNY"), ("yuan", "CNY"),
    ]
    if text == "solar_irradiance_kwh_m2_hour":
        return "Wh/m2 per 1h interval (misnamed legacy column)" if "public_sources/" in path else "kWh/m2 per 1h interval"
    for fragment, unit in rules:
        if fragment in text:
            return unit
    if text in {"x_m", "y_m", "length_m", "max_route_length_m"}:
        return "m"
    if any(w in text for w in ("cop", "eer", "efficiency", "fraction", "ratio", "share", "weight", "multiplier", "availability", "occupancy", "behavior", "credit", "gap")):
        return "dimensionless"
    if text in {"latitude", "longitude"}:
        return "degree (requested point; not survey validation)"
    if text in {"x", "y"}:
        return "UNDECLARED schematic coordinate"
    if text in SETTINGS or text in {"schema_version", "row", "count", "index", "step", "t"}:
        return "count/dimensionless setting"
    return None


def merge_pair(base: tuple, override: tuple) -> tuple:
    """Resolve named-list inheritance while retaining each original leaf's file/pointer."""
    a, ao = base
    b, bo = override
    if isinstance(a, dict) and isinstance(b, dict):
        v, origin = deepcopy(a), deepcopy(ao)
        for k, item in b.items():
            v[k], origin[k] = merge_pair((a[k], ao[k]), (item, bo[k])) if k in a else (deepcopy(item), deepcopy(bo[k]))
        return v, origin
    if isinstance(a, list) and isinstance(b, list) and all(isinstance(x, dict) and "name" in x for x in a + b):
        v, origin = deepcopy(a), deepcopy(ao)
        for i, item in enumerate(b):
            position = next((j for j, row in enumerate(v) if row["name"] == item["name"]), None)
            if item.get("_delete"):
                if position is not None:
                    v.pop(position); origin.pop(position)
            elif position is None:
                v.append(deepcopy(item)); origin.append(deepcopy(bo[i]))
            else:
                v[position], origin[position] = merge_pair((v[position], origin[position]), (item, bo[i]))
        return v, origin
    return deepcopy(b), deepcopy(bo)


def origin_tree(value: Any, file: str, parts: tuple = ()) -> Any:
    if isinstance(value, dict):
        return {k: origin_tree(v, file, (*parts, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [origin_tree(v, file, (*parts, i)) for i, v in enumerate(value)]
    return {"file": file, "pointer": pointer(parts)}


def resolve_config(path: Path, root: Path, stack: tuple = ()) -> tuple:
    path = path.resolve()
    if path in stack:
        raise ValueError("configuration inheritance cycle")
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    origin = origin_tree(data, relative(path, root))
    parent = data.pop("extends", None)
    origin.pop("extends", None)
    if not parent:
        return data, origin
    return merge_pair(resolve_config(path.parent / parent, root, (*stack, path)), (data, origin))


def at(value: Any, parts: tuple) -> Any:
    for p in parts:
        value = value[p]
    return value


class Audit:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.docs = self.root / DOCS
        self.output = self.root / OUTPUT
        self.entries: list[dict] = []
        self.inventory: dict[str, dict] = {}
        self.errors: list[dict] = []
        self.sources: dict[str, dict] = {}
        self.weather_metadata = json.loads((self.root / "data/public_sources/crawl_metadata.json").read_text(encoding="utf-8"))["weather_profiles"]
        self.review = json.loads((self.docs / "source_review.json").read_text(encoding="utf-8"))
        for row in pd.read_csv(self.root / "data/public_sources/source_catalog.csv").fillna("").to_dict("records"):
            self.sources[row["source_id"]] = {**row, "review_status": "catalog_claim_not_independently_verified",
                                            "specific_location": None, "local_raw_material": None, "sha256": None,
                                            "exact_parameters": {}}
        for row in self.review["sources"]:
            source = {**self.sources.get(row["source_id"], {}), **row}
            raw = self.root / source["local_raw_material"]
            source["sha256"] = digest(raw) if raw.exists() else None
            source["local_material_exists"] = raw.exists()
            self.sources[row["source_id"]] = source
        self.external_validation = self.validate_external_measurements()

    def validate_external_measurements(self) -> dict:
        """Rebuild the selected public trial columns directly from original XLSX.

        Checks ZIP member identity and all rows; no fitter/generator/model is
        called. Successful reconstruction does not certify model heat-transfer
        assumptions or migrate this German experiment to Wuhan.
        """
        base = self.root / "data/external_rc_benchmark_20261001"
        required = [base / n for n in ("observations.csv", "field_evidence_map.json", "source_provenance.json", "evidence/downloads.json")]
        if not all(p.exists() for p in required):
            return {"status": "absent_or_not_ready", "columns": {}}
        try:
            for p in required:
                self.track(p, "public_experiment_source_and_transformation")
            fields = json.loads(required[1].read_text(encoding="utf-8"))["fields"]
            source = json.loads(required[2].read_text(encoding="utf-8"))["selected_dataset"]
            downloads = json.loads(required[3].read_text(encoding="utf-8"))
            raw_names = ["annex71_main_experiment.zip", "annex71_specification.pdf",
                         "Twin_house_N2_exp1_full1_10min.xlsx", "Twin_house_weather_exp1_10min_compensated.xlsx"]
            for name in raw_names:
                self.track(base / "evidence" / name, "original_public_experiment_material")
            archive = base / "evidence/annex71_main_experiment.zip"
            expected = next(r["sha256"] for r in downloads if r["file"] == archive.name)
            assert digest(archive) == expected, "download ZIP hash mismatch"
            with zipfile.ZipFile(archive) as z:
                member_checks = {}
                for name in raw_names[2:]:
                    matches = [member for member in z.namelist() if Path(member).name == name]
                    assert len(matches) == 1, "original ZIP member name ambiguous"
                    info = z.getinfo(matches[0])
                    content = (base / "evidence" / name).read_bytes()
                    assert info.file_size == len(content) and info.CRC == zlib.crc32(content), "original ZIP member CRC/size mismatch"
                    # Deflate64 (ZIP method9) is unsupported by stdlib zipfile;
                    # retain explicit CRC/size verification instead of falsely
                    # asserting that this audit decompressed its original bytes.
                    method = "original_ZIP_member_CRC32_and_size"
                    if info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}:
                        assert hashlib.sha256(z.read(matches[0])).hexdigest() == hashlib.sha256(content).hexdigest()
                        method = "decompressed_original_member_SHA256_equal"
                    member_checks[name] = {"member": matches[0], "compression_method": info.compress_type,
                                          "check_method": method, "crc32": info.CRC, "bytes": len(content),
                                          "extracted_file_sha256": hashlib.sha256(content).hexdigest()}
            house = pd.read_excel(base / "evidence" / raw_names[2])
            weather = pd.read_excel(base / "evidence" / raw_names[3])
            obs = pd.read_csv(required[0])
            assert len(house) == len(weather) == len(obs)
            assert house["DATE"].equals(weather["DATE"])
            areas = fields["room_floor_areas"]["values"]
            heaters = [f"n2_aroom_{room}_heat_elP" for room in areas]
            gains = [f"n2_aroom_{room}_IHS_elP" for room in areas if f"n2_aroom_{room}_IHS_elP" in house]
            mean_temp = sum(house[f"n2_aroom_{room}_110_AT"] * area for room, area in areas.items()) / sum(areas.values())
            expected_values = {
                "indoor_temperature_mean_c": mean_temp,
                "outdoor_temperature_mean_c": weather["AmbientAirTemperature"],
                "measured_room_heating_kw": house[heaters].sum(axis=1) / 1000,
                "measured_internal_gain_kw": house[gains].sum(axis=1) / 1000,
                "global_irradiance_w_m2": weather["Radiation_Global"],
                "solar_reference_kw_m2": weather[["Radiation_Global", "Radiation_South", "Radiation_West", "Radiation_East", "Radiation_North"]].max(axis=1) / 1000,
            }
            expected_values["total_room_heat_input_kw"] = expected_values["measured_room_heating_kw"] + expected_values["measured_internal_gain_kw"]
            checks = {}
            for column, expected_series in expected_values.items():
                residual = np.asarray(obs[column]) - np.asarray(expected_series)
                error = float(np.max(np.abs(residual)))
                assert error < 1e-8, f"external CSV column {column} does not reconstruct"
                checks[column] = {"all_rows_reconstructed": True, "maximum_absolute_error": error,
                                  "source_field_evidence": fields.get(column, {})}
            expected_time = (pd.to_datetime(house["DATE"]) - pd.Timedelta(hours=1)).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            assert expected_time.equals(obs["time_utc"]), "CET to UTC conversion mismatch"
            original_time = pd.to_datetime(house["DATE"])
            assert original_time.equals(pd.to_datetime(obs["source_time_cet"])), "original CET timestamps mismatch"
            assert original_time.diff().iloc[1:].eq(pd.Timedelta(minutes=10)).all(), "original 10-minute sampling mismatch"
            assert np.allclose(obs["dt_hours"], 1 / 6, rtol=0, atol=1e-12), "declared interval length mismatch"
            assert obs["observation_identity"].eq("derived_spatial_average_of_publisher_processed_measurements").all(), "observation identity marker mismatch"
            identities = {
                "source_time_cet": {"unit": "timestamp in fixed CET (UTC+1)", "transform": "original workbook DATE copied", "evidence": "Original N2 and weather workbook DATE; specification weather/data description"},
                "time_utc": {"unit": "UTC timestamp", "transform": "original workbook DATE minus 1 hour; winter fixed CET", "evidence": "Original workbook DATE and explicitly recorded CET-to-UTC mapping"},
                "dt_hours": {"unit": "h", "transform": "10 minutes / 60; all consecutive original timestamps checked", "evidence": "Original workbook DATE intervals; specification 3.16 / 6.1 ten-minute averages"},
                "observation_identity": {"unit": "dimensionless text identifier", "transform": "declared observation-aggregation identity label", "evidence": "field_evidence_map indoor-temperature aggregation and original room channels"},
            }
            material = relative(archive, self.root)
            self.sources["EXTERNAL_ANNEX71_N2"] = {
                "source_id": "EXTERNAL_ANNEX71_N2", "publisher": source["publisher"], "url": source["landing_url"],
                "local_raw_material": material, "sha256": digest(archive),
                "review_status": "original_zip_and_xlsx_identity_and_all_selected_columns_independently_reconstructed",
                "specific_location": "Specification3.1/3.9/3.16/5.1/6.1/6.2; exact room channels in field_evidence_map; original ZIP member XLSX",
                "time_scope": "2018-12-07 to2019-03-01,10-minute averages,fixed CET; repository version2,2020-06-23",
                "geographic_scope": "Fraunhofer IBP Holzkirchen Germany,N2 experimental house,not Wuhan",
                "applicability": "Publisher-processed physical trial data. Short gaps have publisher fills and no row flags. Electric input is not directly measured delivered heat; solar proxy is not transmitted heat. CC BY-SA4.0 attribution required.",
                "exact_parameters": {}, "licence": source.get("licence"),
            }
            return {"status": "passed_independent_raw_reconstruction", "rows": len(obs), "columns": checks,
                    "identity_columns": identities,
                    "original_archive_member_checks": member_checks,
                    "time_mapping_verified": True, "source_id": "EXTERNAL_ANNEX71_N2",
                    "heat_unit_conversion_to_room_heat_is_a_model_assumption": True,
                    "fitted_H_C_not_source_certified_or_Wuhan_values": True}
        except Exception as exc:
            return {"status": "not_verified", "error": str(exc), "columns": {}}

    def track(self, path: Path, role: str) -> dict:
        key = relative(path, self.root)
        if key not in self.inventory:
            self.inventory[key] = {"file": key, "sha256_before": digest(path), "bytes": path.stat().st_size,
                                   "role": role, "audited_numeric_fields": 0, "audited_csv_columns": 0}
        return self.inventory[key]

    def entry(self, path: Path, field: str, value: Any, *, name: str = "", context: dict | None = None,
              kind: str = "numeric_field", origin: dict | None = None) -> dict:
        ctx = context or {}
        rec = {
            "id": hashlib.sha256((relative(path, self.root) + "#" + field).encode()).hexdigest()[:20],
            "file": relative(path, self.root), "file_sha256": self.track(path, "input")["sha256_before"],
            "field": field, "kind": kind, "value": value if not isinstance(value, float) or math.isfinite(value) else str(value),
            "entity": ctx.get("name", ctx.get("building_id", ctx.get("indicator"))),
            "unit": ctx.get("unit") or infer_unit(name, relative(path, self.root)),
            "unit_status": "declared_in_record" if ctx.get("unit") else "inferred_from_field_name_needs_review",
            "source_ids": [], "source_urls": [], "local_raw_materials": [], "source_sha256": [],
            "specific_page_table_clause": None, "time_scope": None, "geographic_scope": None,
            "value_method": "untraced", "derivation_formula": None, "applicability": None,
            "category": "unsubstantiated_assumption", "verification_status": "missing_parameter_specific_evidence",
            "allowed_true_project_experiment": False, "allowed_documented_public_data_study": False,
            "missing_evidence": [], "priority": "P1", "origin": origin,
        }
        if ctx.get("source_id") in self.sources:
            self.attach(rec, ctx["source_id"])
        return rec

    def attach(self, rec: dict, source_id: str) -> None:
        source = self.sources[source_id]
        rec["source_ids"] = [source_id]
        rec["source_urls"] = [source["url"]] if source.get("url") else []
        rec["local_raw_materials"] = [source["local_raw_material"]] if source.get("local_raw_material") else []
        rec["source_sha256"] = [source["sha256"]] if source.get("sha256") else []
        for k, dest in (("specific_location", "specific_page_table_clause"), ("time_scope", "time_scope"),
                        ("geographic_scope", "geographic_scope"), ("applicability", "applicability")):
            rec[dest] = source.get(k)
        if "experiment" in source.get("authority", "").lower() or "paper" in source.get("authority", "").lower():
            rec["category"] = "published_experiment_parameter"
        else:
            rec["category"] = "official_statistics_or_standard"
        # A URL or reviewed document is not permission to assert an arbitrary number.
        rec["verification_status"] = "source_identified_exact_parameter_not_verified"

    def setting(self, rec: dict, note: str) -> None:
        rec.update(category="explainable_design_or_solver_setting", verification_status="declared_experiment_protocol_not_empirical",
                   value_method="declared_design_or_algorithm_choice", applicability=note,
                   allowed_documented_public_data_study=True, priority="P2")
        rec["local_raw_materials"] = [rec["file"]]
        rec["source_sha256"] = [rec["file_sha256"]]
        rec["specific_page_table_clause"] = rec["field"]

    def classify_field(self, rec: dict, name: str, ctx: dict) -> None:
        file, field, value = rec["file"], rec["field"], rec["value"]
        leaf = name.lower()
        if isinstance(value, bool) or leaf in SETTINGS:
            self.setting(rec, "可复核的实验/时标/数值求解设置；不是现场测量或设备性能。若用于规范合规仍需单独出处。")
        if leaf in {"firm_capacity_credit", "max_new_capacity_kw", "max_new_capacity_kwh", "existing_capacity_kw", "existing_capacity_kwh"} and value == 0:
            self.setting(rec, "零容量/零信用是保守候选或技术禁用设定；不证明现实资产不存在。")
        if leaf == "factor" and "grid_emission_factors_2023_official.json" in file:
            province = {"scenario1_urumqi": "新疆", "scenario2_guyuan": "宁夏", "scenario3_wuhan": "湖北"}
            key = next((k for k in province if k in field), None)
            if key and value == self.sources["COMMON_GRID_EF_2023"]["exact_parameters"][province[key]]:
                self.attach(rec, "COMMON_GRID_EF_2023")
                rec.update(unit="kgCO2/kWh", unit_status="verified_source_table", value_method="direct_table_value",
                           verification_status="exact_official_value_verified", allowed_documented_public_data_study=True,
                           allowed_true_project_experiment=True, priority="P2")
        if "competition_spatial/" in file and not (leaf in {"schema_version", "passed", "failed", "tests", "errors", "skipped"}):
            # Synthetic route outputs inherit synthetic-input identity even if arithmetically exact.
            rec.update(category="unsubstantiated_assumption", allowed_documented_public_data_study=False,
                       verification_status="declared_synthetic_not_site_verified", value_method="synthetic_design_or_derived_from_synthetic_geometry",
                       priority="P0", applicability="仅程序验证；坐标、距离与管价无测绘/地下管线/供应商报价证据。")
        if any(x in leaf for x in ("capex", "om_yuan", "cop", "eer", "discount_rate", "lifetime", "carbon_price", "temperature", "heat_loss", "thermal_capacity", "area_m2")) and not rec["allowed_true_project_experiment"]:
            rec["priority"] = "P0"
        if "competition_20261001/" in file and any(x in leaf for x in ("area", "heat_loss", "capacity", "temperature")):
            rec.update(category="unsubstantiated_assumption", verification_status="declared_synthetic_building_not_calibrated",
                       allowed_documented_public_data_study=False, priority="P0")
        if "building_archetypes" in file:
            if leaf == "heat_loss_kw_per_c":
                rec["derivation_formula"] = "area_m2 * heat_loss_w_per_m2_c / 1000"
            if leaf == "thermal_capacity_kwh_per_c":
                rec["derivation_formula"] = "area_m2 * effective_capacity_wh_per_m2_c / 1000"
            rec["value_method"] = "formula_from_unverified_bases" if rec["derivation_formula"] else "declared_synthetic_archetype"
        if rec["source_ids"]:
            self.review_exact_fact(rec, ctx)

    def review_exact_fact(self, rec: dict, ctx: dict) -> None:
        sid = rec["source_ids"][0]
        metric = str(ctx.get("indicator", ctx.get("metric", "")))
        mapping = str(ctx.get("model_mapping", ctx.get("model_field", "")))
        value = rec["value"]
        reviewed = False
        if sid == "S3_WUHAN_LOW_ENERGY_DRAFT_2024":
            supported = {
                "large_ground_source_project_floor_area_threshold": 50000,
                "minimum_ground_balance_analysis_horizon": 10,
                "annual_heat_rejection_to_extraction_ratio_lower": 0.8,
                "annual_heat_rejection_to_extraction_ratio_upper": 1.25,
            }
            reviewed = metric in supported and value == supported[metric]
            if reviewed:
                rec.update(verification_status="exact_draft_value_verified_not_current_mandatory_rule",
                           value_method="direct_draft_clause", allowed_documented_public_data_study=True, priority="P1")
        elif sid == "S3_HUBEI_TOU_2024":
            reviewed = metric == "tou_applicability_capacity_threshold" and value == 100
            if reviewed:
                rec.update(verification_status="exact_official_policy_value_verified_customer_applicability_pending",
                           value_method="direct_policy_clause", allowed_documented_public_data_study=True, priority="P1")
        elif sid == "COMMON_GRID_EF_2023" and any(x in metric + mapping for x in ("hubei", "湖北", "xinjiang", "新疆", "ningxia", "宁夏")):
            province = next((p for p in ("湖北", "宁夏", "新疆") if p in metric + mapping), None)
            if province and value == self.sources[sid]["exact_parameters"][province]:
                rec.update(verification_status="exact_official_value_verified", value_method="direct_table_value",
                           allowed_true_project_experiment=True, allowed_documented_public_data_study=True)
        if not reviewed and not rec["allowed_true_project_experiment"]:
            rec["missing_evidence"].append("逐参数原文数值/公式及适用条件尚未核实；已读大类来源不构成支持")

    def finish(self, rec: dict) -> None:
        if not rec["unit"]:
            rec["missing_evidence"].append("单位未登记/不能安全推定")
        if rec["category"] not in {"explainable_design_or_solver_setting", "actual_solver_output"}:
            if not rec["source_urls"]:
                rec["missing_evidence"].append("缺逐参数权威原始出处URL")
            if not rec["local_raw_materials"] or not rec["source_sha256"]:
                rec["missing_evidence"].append("缺可复核原始材料及SHA256")
            if not rec["specific_page_table_clause"]:
                rec["missing_evidence"].append("缺具体页/表/条款/响应参数位置")
            if not rec["time_scope"]:
                rec["missing_evidence"].append("缺适用年份/服务时间口径")
            if not rec["geographic_scope"]:
                rec["missing_evidence"].append("缺地区/项目/设备型号或试验工况")
        rec["missing_evidence"] = list(dict.fromkeys(rec["missing_evidence"]))
        self.entries.append(rec)
        if rec["kind"] == "csv_column":
            self.inventory[rec["file"]]["audited_csv_columns"] += 1
        else:
            self.inventory[rec["file"]]["audited_numeric_fields"] += 1

    def audit_structured(self, path: Path, *, resolved: bool = False) -> None:
        self.track(path, "resolved_config" if resolved else "structured_input_or_metadata")
        try:
            if resolved:
                data, origins = resolve_config(path, self.root)
            else:
                data = json.loads(path.read_text(encoding="utf-8-sig")) if path.suffix == ".json" else yaml.safe_load(path.read_text(encoding="utf-8-sig"))
                origins = None
            for parts, value, ctx in walk(data):
                # Raw API has one field per hour: retain every exact numeric value/locator.
                name = str(next((p for p in reversed(parts) if isinstance(p, str)), ""))
                field = ("resolved:" if resolved else "") + pointer(parts)
                origin = at(origins, parts) if origins is not None else None
                rec = self.entry(path, field, value, name=name, context=ctx, origin=origin)
                self.classify_field(rec, name, ctx)
                if "evidence/wuhan_clock_" in rec["file"] and "/properties/parameter/" in field:
                    parameter = str(parts[-2])
                    self.attach(rec, "COMMON_NASA_POWER")
                    payload = data
                    column_unit = payload.get("parameters", {}).get(parameter, {}).get("units")
                    rec.update(category="public_measurement_or_weather_product", verification_status="archived_official_raw_api_response",
                               value_method="direct_raw_api_parameter", unit=column_unit,
                               unit_status="declared_raw_api_metadata", local_raw_materials=[rec["file"]],
                               source_sha256=[rec["file_sha256"]], specific_page_table_clause=field,
                               time_scope=f"{parts[-1]} {payload['header']['time_standard']}; API {payload['header']['api']['version']}",
                               geographic_scope=str(payload.get("geometry", {})),
                               allowed_documented_public_data_study=True, priority="P2")
                self.finish(rec)
        except Exception as exc:
            self.errors.append({"file": relative(path, self.root), "stage": "structured", "error": str(exc)})

    def weather_context(self, path: Path, frame: pd.DataFrame) -> dict:
        key = relative(path, self.root)
        profile = next((p for p in ("scenario1_urumqi", "scenario2_guyuan", "scenario3_wuhan") if p in key), "scenario3_wuhan" if "wuhan" in key else None)
        years = sorted(set(re.findall(r"20\d{2}", path.name)))
        refs = []
        if "source_weather_file" in frame:
            refs = sorted(frame["source_weather_file"].dropna().unique().tolist())
        elif profile:
            refs = [f"data/public_sources/{profile}_weather_{y}.csv" for y in years]
        refs = [p for p in refs if (self.root / p).is_file()]
        if not profile:
            profile = next((p for p in ("scenario1_urumqi", "scenario2_guyuan", "scenario3_wuhan") if any(p in ref for ref in refs)), None)
        if not years:
            years = sorted(set(y for ref in refs for y in re.findall(r"20\d{2}", Path(ref).name)))
        requests = []
        for ref in refs:
            stem = Path(ref).stem.replace("_weather_", "_")
            m = self.weather_metadata.get(stem, {})
            if m.get("request_url"):
                requests.append(m["request_url"])
        return {"profile": profile, "refs": refs, "requests": requests, "years": years}

    def classify_csv(self, rec: dict, column: str, frame: pd.DataFrame, weather: dict) -> None:
        file = rec["file"]
        if column in IDENTITY_COLUMNS or column.endswith("_column"):
            self.setting(rec, "列为时间、归属、字段名或原始身份；不把文字标签当测量证据。")
            rec["value_method"] = "identifier_or_time_mapping"
        elif column in SETTINGS or column in {"representative_day", "representative_day_index", "day_weight", "time_step_hours"}:
            self.setting(rec, "生成器时标、聚类/权重或实验协议；不宣称现场观测。")
        else:
            rec["value_method"] = "untraced_csv_column"
        is_weather_file = "/public_sources/" in file and "_weather_" in file
        has_weather_lineage = is_weather_file or weather["refs"]
        if column in WEATHER_COLUMNS and has_weather_lineage:
            self.attach(rec, "COMMON_NASA_POWER")
            parameter = RAW_WEATHER.get(column, "RH2M" if column == "relative_humidity" else "ALLSKY_SFC_SW_DWN")
            rec.update(category="public_measurement_or_weather_product", verification_status="archived_product_request_documented_full_response_not_preserved",
                       source_urls=weather["requests"] or rec["source_urls"], local_raw_materials=weather["refs"],
                       source_sha256=[digest(self.root / p) for p in weather["refs"]],
                       specific_page_table_clause=f"NASA properties.parameter.{parameter}; raw file column and original API metadata",
                       time_scope=f"{weather['years'] or 'from source_weather_file/time range'}; raw LST, C1 explicitly remaps UTC; legacy derived tables need civil-time alignment review",
                       geographic_scope=weather["profile"], value_method="direct_weather_product_or_unit_time_conversion",
                       priority="P1", applicability="NASA格点产品；原全年API响应及版本未归档。现有C1小样本能核Wuhan2023头48h，不能证明所有城市年份逐行相等。")
            if column == "relative_humidity":
                rec["derivation_formula"] = "RH2M percent / 100"
            elif column == "solar_irradiance_w_m2":
                rec["derivation_formula"] = "ALLSKY_SFC_SW_DWN Wh/m2 over 1 h / 1 h"
            elif column == "solar_irradiance_kwh_m2_hour" and not is_weather_file:
                rec["derivation_formula"] = "ALLSKY_SFC_SW_DWN Wh/m2 / 1000"
                if "competition_20261001/" not in file:
                    rec["missing_evidence"].append("旧public派生表可能仍沿用Wh/m2误名；需逐生成链核单位，不能仅按列名认kWh")
        if column == "grid_emission_factor" and ("competition_20261001/" in file or weather["profile"] == "scenario3_wuhan"):
            vals = pd.to_numeric(frame[column], errors="coerce")
            if vals.notna().all() and np.isclose(vals, 0.4044, rtol=0, atol=1e-12).all():
                self.attach(rec, "COMMON_GRID_EF_2023")
                rec.update(verification_status="exact_2023_provincial_value_applied_as_constant", value_method="direct_official_factor_repeated_by_generator",
                           unit="kgCO2/kWh", priority="P1", allowed_documented_public_data_study=True)
                rec["missing_evidence"].append("服务年包含2024小时；2023省级平均因子不得改称2024实测逐时因子")
        if column == "resident_heating_electricity_price":
            if "S2_NX_HEATING_TARIFF_2026" in self.sources:
                self.attach(rec, "S2_NX_HEATING_TARIFF_2026")
            rec.update(priority="P0", value_method="official_claimed_price_repeated_by_synthetic_clock_schedule")
            rec["missing_evidence"].extend(["2026居民清洁供暖资格与历史天气情景口径需明确", "旧S2时钟为LST，不能直接用数组序号当当地民用电价时段"])
        if column == "electricity_price":
            rec.update(category="unsubstantiated_assumption", priority="P0", value_method="24_hour_legacy_template_repeated",
                       local_raw_materials=["data/scenario3_timeseries.csv"] if "wuhan" in file or weather["profile"] == "scenario3_wuhan" else [],
                       applicability="缺当月市场/代理基价、客户类别、税费输配及合同。湖北倍率公告不支持这条绝对价格曲线。")
            rec["source_sha256"] = [digest(self.root / p) for p in rec["local_raw_materials"]]
            rec["specific_page_table_clause"] = "scripts/prepare_competition_case.py build_frame: template electricity_price"
        if any(w in column for w in ("gain_", "occupancy", "behavior", "demand_", "availability", "heating_degree", "temperature_")) and column not in WEATHER_COLUMNS:
            rec.update(category="unsubstantiated_assumption", priority="P0", verification_status="generator_traceable_unverified_physical_bases",
                       value_method="synthetic_formula_or_template", allowed_documented_public_data_study=False)
            if column.startswith("internal_gain_"):
                rec["derivation_formula"] = "area_m2*(idle_gain+(occupied_gain-idle_gain)*synthetic_occupancy)/1000; W/m2 coefficients (0.8,3),(1,8),(1,10)"
            elif column.startswith("solar_gain_"):
                rec["derivation_formula"] = "area_m2*0.015*hourly_radiation_Wh_m2/1000; 0.015 aperture/transmission/shading factor unverified"
            elif column.startswith("min_temperature_"):
                rec["derivation_formula"] = "18 C if synthetic occupancy>=0.5, else16 C"
            elif column.startswith("max_temperature_"):
                rec["derivation_formula"] = "26 C if synthetic occupancy>=0.5, else28 C"
            elif column.startswith("demand_"):
                rec["derivation_formula"] = "legacy_24h_demand*clip(max(18-Tout,0)/max(18-Ttemplate,1),0,1.8)"
            elif column.startswith("cooling_demand_"):
                rec["derivation_formula"] = "synthetic peak440/360kW*clip((Tout-24)/11,0,1.2)*synthetic occupancy/setback; conditional natural-ventilation suppression"
            elif column == "solar_heat_availability":
                rec["derivation_formula"] = "clip(raw_radiation / max(raw_radiation.quantile(0.95),1e-9),0,1); scripts/crawl_public_data.py; proxy, not measured collector availability"
            elif "occupancy" in column:
                rec["derivation_formula"] = "archetype civil-hour/week-day assumed schedule; district area-weighted mean where applicable"
            if "competition_20261001/" in file:
                rec["local_raw_materials"] = ["scripts/prepare_competition_case.py", "data/competition_20261001/building_archetypes.csv"]
                rec["source_sha256"] = [digest(self.root / p) for p in rec["local_raw_materials"]]
                rec["specific_page_table_clause"] = "occupancy() / build_frame(); a reproducible formula is not proof its synthetic bases are real"
        if "building_archetypes.csv" in file or "network_candidate_register.csv" in file:
            self.classify_field(rec, column, {})
        if "external_rc_benchmark_20261001/observations.csv" in file and column in self.external_validation.get("columns", {}):
            self.attach(rec, "EXTERNAL_ANNEX71_N2")
            check = self.external_validation["columns"][column]
            field_evidence = check["source_field_evidence"]
            rec.update(category="public_measurement_or_weather_product", verification_status="all_rows_independently_reconstructed_from_original_publication_workbooks",
                       unit=field_evidence.get("unit", field_evidence.get("output_unit", infer_unit(column))),
                       unit_status="original_sensor_units_and_recorded_transform", value_method="published_measurement_or_documented_sensor_aggregation",
                       derivation_formula=field_evidence.get("transform"),
                       specific_page_table_clause=field_evidence.get("evidence", rec["specific_page_table_clause"]),
                       allowed_documented_public_data_study=True, priority="P1")
            rec["raw_reconstruction"] = check
            rec["local_raw_materials"] += ["data/external_rc_benchmark_20261001/field_evidence_map.json"]
            rec["source_sha256"] += [digest(self.root / rec["local_raw_materials"][-1])]
            rec["missing_evidence"] = ["实验为德国N2楼宇，不能迁移为武汉建筑的实测参数"]
            if column == "solar_reference_kw_m2":
                rec["category"] = "explainable_design_or_solver_setting"
                rec["value_method"] = "declared_maximum_of_measured_facade_irradiance_proxy"
                rec["missing_evidence"].append("该列是可复算辐照代理，不是测得的透窗太阳热增益")
        if "external_rc_benchmark_20261001/observations.csv" in file and column in self.external_validation.get("identity_columns", {}):
            self.attach(rec, "EXTERNAL_ANNEX71_N2")
            identity = self.external_validation["identity_columns"][column]
            rec.update(category="explainable_design_or_solver_setting", verification_status="original_time_mapping_or_declared_identity_independently_checked",
                       unit=identity["unit"], unit_status="explicit_time_or_identity_semantics", value_method="checked_time_mapping_or_declared_observation_protocol",
                       derivation_formula=identity["transform"], specific_page_table_clause=identity["evidence"],
                       allowed_documented_public_data_study=True, priority="P2")
            rec["missing_evidence"] = []

    def audit_csv(self, path: Path, *, solver_manifest: dict | None = None) -> None:
        self.track(path, "csv_input_or_output")
        try:
            frame = pd.read_csv(path, low_memory=False)
            weather = self.weather_context(path, frame)
            time_column = next((c for c in ("time", "timestamp", "local_time") if c in frame), None)
            for column in frame.columns:
                series = pd.to_numeric(frame[column], errors="coerce")
                finite = series[np.isfinite(series)]
                values = {"rows": len(frame), "nonnull": int(frame[column].notna().sum()),
                          "numeric_count": int(series.notna().sum()), "minimum": float(finite.min()) if len(finite) else None,
                          "maximum": float(finite.max()) if len(finite) else None,
                          "distinct_count": int(frame[column].nunique(dropna=True))}
                rec = self.entry(path, "column:" + column, values, name=column, kind="csv_column")
                if "unit" in frame and column in {"value", "heat_kw", "heat_output", "flow"}:
                    rec["unit"] = "per-row unit column: " + ",".join(sorted(frame["unit"].dropna().astype(str).unique()))
                    rec["unit_status"] = "declared_per_row_unit"
                self.classify_csv(rec, column, frame, weather)
                if time_column:
                    rec["data_time_range"] = [str(frame[time_column].iloc[0]), str(frame[time_column].iloc[-1])] if len(frame) else []
                if solver_manifest and path.name not in {"model_timeseries.csv", "input_timeseries.csv"}:
                    rec.update(category="actual_solver_output", verification_status="completed_run_manifest_hash_consistent",
                               value_method="model_solution_or_derived_accounting", allowed_true_project_experiment=False,
                               allowed_documented_public_data_study=False, priority="P1",
                               local_raw_materials=[solver_manifest["file"], rec["file"]],
                               source_sha256=[solver_manifest["sha256"], rec["file_sha256"]],
                               specific_page_table_clause="run manifest files plus run_record/summary solver status",
                               applicability="真实文件/求解程序结果；C1/S3物理经济输入未核实，只能作为程序验证，不是真实工程实证。")
                    rec["run_identity"] = solver_manifest["file"]
                    rec["missing_evidence"] = ["输入真实性不随求解成功/哈希一致而成立"]
                self.finish(rec)
            if path.name == "scenario_public_facts.csv":
                for i, row in enumerate(frame.fillna("").to_dict("records")):
                    try:
                        val = json.loads(str(row["value"]))
                    except (ValueError, TypeError):
                        continue
                    leaves = [((), val, row)] if numeric(val) else list(walk(val))
                    for parts, value, _ in leaves:
                        rec = self.entry(path, f"row:{i+2}:value{pointer(parts)}", value, name=row["indicator"], context=row, kind="fact_row_numeric")
                        self.review_exact_fact(rec, row)
                        rec["value_method"] = "catalog_claim_direct_or_derived_fact_needs_exact_original_location" if not rec["allowed_documented_public_data_study"] else rec["value_method"]
                        self.finish(rec)
        except Exception as exc:
            self.errors.append({"file": relative(path, self.root), "stage": "csv", "error": str(exc)})

    def audit_generators(self) -> None:
        for filename in GENERATORS:
            path = self.root / "scripts" / filename
            if not path.exists():
                self.errors.append({"file": str(path), "stage": "generator_inventory", "error": "file not found"})
                continue
            self.track(path, "generator_or_case_runner")
            text = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(text)
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not numeric(node.value):
                    continue
                parent = parents.get(node)
                label = "numeric_literal"
                if isinstance(parent, ast.Dict):
                    for k, v in zip(parent.keys, parent.values):
                        if v is node and isinstance(k, ast.Constant):
                            label = str(k.value)
                elif isinstance(parent, ast.Assign) and parent.targets and isinstance(parent.targets[0], ast.Name):
                    label = parent.targets[0].id
                rec = self.entry(path, f"line:{node.lineno}:column:{node.col_offset}", node.value, name=label, kind="generator_numeric_literal")
                rec["code_context"] = text.splitlines()[node.lineno - 1].strip()
                self.classify_field(rec, label, {})
                if node.value in {1000, 3600, 273.15}:
                    self.setting(rec, "标准单位换算候选；结合该行确认，不当作现场物性或价格。")
                    rec["verification_status"] = "unit_conversion_literal_context_review_required"
                elif label in {"latitude", "longitude"}:
                    self.setting(rec, "NASA请求采样格点选择；不证明城市建筑/管线真实坐标。")
                elif "raise" in rec["code_context"] or "len(" in rec["code_context"]:
                    self.setting(rec, "输入维度/完整性检查的算法常量。")
                self.finish(rec)

    def result_runs(self) -> list[dict]:
        results = []
        base = self.root / "outputs/competition_20261001/C4"
        if not base.exists():
            return results
        for directory in sorted(base.iterdir()):
            if not directory.is_dir() or any(x in directory.name.lower() for x in ("pytest", "frozen", "input_anchor")):
                continue
            manifest_path = directory / "manifest.json"
            if not manifest_path.exists():
                results.append({"directory": relative(directory, self.root), "status": "no_completion_manifest_not_formal_evidence"})
                continue
            self.track(manifest_path, "result_manifest")
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            failures = []
            files = m.get("files", {})
            for name, expected in files.items():
                p = directory / name
                if not p.exists() or digest(p) != expected:
                    failures.append(name)
            run = {}
            for name in ("run_record.json", "summary.json"):
                p = directory / name
                if p.exists():
                    run[name] = json.loads(p.read_text(encoding="utf-8"))
            status = "hash_consistent_completed_program_output" if m.get("complete") is True and files and not failures else "incomplete_or_hash_invalid"
            result = {"directory": relative(directory, self.root), "manifest": relative(manifest_path, self.root),
                      "manifest_sha256": digest(manifest_path), "complete": m.get("complete"), "scope": m.get("scope"),
                      "hash_failures": failures, "status": status, "result_record": run,
                      "allowed_true_project_evidence": False,
                      "reason": "Input building, prices, equipment and spatial assumptions remain unverified. Solver truth is not site-data truth."}
            results.append(result)
            solver = {"file": relative(manifest_path, self.root), "sha256": digest(manifest_path)} if status.startswith("hash_consistent") and "run_record.json" in run else None
            for name in files:
                p = directory / name
                if p.suffix == ".csv" and p.exists():
                    self.audit_csv(p, solver_manifest=solver)
                elif name == "model_config.json" and p.exists():
                    self.audit_structured(p)
        return results

    def run(self) -> dict:
        started = datetime.now(timezone.utc).isoformat()
        # A previous completion marker must not describe an in-progress rerun.
        (self.output / "manifest.json").unlink(missing_ok=True)
        data_paths = sorted(p for p in (self.root / "data").rglob("*") if p.is_file())
        for p in data_paths:
            self.track(p, "data_source_or_artifact")
            if p.suffix in {".yaml", ".yml", ".json"}:
                self.audit_structured(p)
            elif p.suffix == ".csv":
                self.audit_csv(p)
        for name in ("wuhan_rc_candidate.yaml", "wuhan_rc_calendar_2023_candidate.yaml"):
            p = self.root / "data/competition_20261001" / name
            if p.exists():
                self.audit_structured(p, resolved=True)
        self.audit_generators()
        result_runs = self.result_runs()
        # Inventory source documents and their original URL/cache hashes without endorsing their claims.
        doc_inventory = []
        for p in sorted((self.root / "docs").rglob("*")):
            if not p.is_file() or DOCS.as_posix() in relative(p, self.root) or p.suffix not in {".md", ".json"}:
                continue
            doc_inventory.append({"file": relative(p, self.root), "sha256": digest(p), "bytes": p.stat().st_size})
        changed = []
        added_data_files = sorted(set(relative(p, self.root) for p in (self.root / "data").rglob("*") if p.is_file()) - set(relative(p, self.root) for p in data_paths))
        for file, record in self.inventory.items():
            p = self.root / file
            record["sha256_after"] = digest(p) if p.exists() else None
            record["unchanged_during_audit"] = record["sha256_after"] == record["sha256_before"]
            if not record["unchanged_during_audit"]:
                changed.append(file)
        counts = Counter(r["category"] for r in self.entries)
        summary = {
            "schema_version": 1, "complete_inventory": not self.errors and not changed and not added_data_files, "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(), "audit_date_local": "2026-10-02",
            "read_only_input_scan": True, "scan_errors": self.errors, "inputs_changed_during_scan": changed,
            "data_files_added_during_scan": added_data_files,
            "data_files": len(data_paths), "tracked_files": len(self.inventory), "ledger_entries": len(self.entries),
            "category_counts": dict(counts), "category_labels": CATEGORIES,
            "csv_columns": sum(r["kind"] == "csv_column" for r in self.entries),
            "yaml_json_numeric_fields": sum(r["kind"] == "numeric_field" for r in self.entries),
            "generator_numeric_literals": sum(r["kind"] == "generator_numeric_literal" for r in self.entries),
            "source_fact_numeric_items": sum(r["kind"] == "fact_row_numeric" for r in self.entries),
            "p0_entries": sum(r["priority"] == "P0" for r in self.entries),
            "true_project_inputs_verified": sum(r["allowed_true_project_experiment"] for r in self.entries),
            "sources_independently_reviewed": len(self.review["sources"]),
            "external_public_experiment_reconstruction": self.external_validation,
            "final_candidate_true_data_gate": "BLOCKED_UNVERIFIED_BUILDING_ECONOMIC_SPATIAL_INPUTS",
            "no_global_data_truth_claim": "Inventory completeness means coverage only; unresolved provenance is not certified truth.",
        }
        weather_path = self.docs / "weather_field_addendum.json"
        weather_addendum = verify_weather_addendum(self.root) if weather_path.exists() else None
        policy = delivery_policy(self.entries, self.external_validation, weather_addendum)
        summary["true_project_inputs_verified_in_original_inventory"] = summary["true_project_inputs_verified"]
        summary["true_project_inputs_verified"] = len(policy["strict_input_field_whitelist"])
        summary["incremental_weather_product_fields_verified"] = len((weather_addendum or {}).get("entries", []))
        self.docs.mkdir(parents=True, exist_ok=True); self.output.mkdir(parents=True, exist_ok=True)
        dump(self.docs / "field_ledger.json", {"schema_version": 1, "entries": self.entries})
        dump(self.docs / "source_evidence.json", {"sources": list(self.sources.values()), "reviewed_exact_support_only": True})
        dump(self.docs / "delivery_policy.json", policy)
        dump(self.docs / "file_inventory.json", list(self.inventory.values()))
        dump(self.docs / "docs_source_inventory.json", doc_inventory)
        dump(self.output / "result_identity_audit.json", result_runs)
        dump(self.output / "external_public_measurement_check.json", self.external_validation)
        dump(self.output / "audit_summary.json", summary)
        fields = ["id", "file", "field", "kind", "entity", "value", "category", "verification_status", "priority", "unit",
                  "source_ids", "source_urls", "local_raw_materials", "source_sha256", "specific_page_table_clause",
                  "time_scope", "geographic_scope", "value_method", "derivation_formula", "applicability", "missing_evidence",
                  "allowed_true_project_experiment", "allowed_documented_public_data_study", "file_sha256", "origin"]
        with (self.docs / "field_ledger.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
            for r in self.entries:
                writer.writerow({k: json.dumps(r.get(k), ensure_ascii=False) if isinstance(r.get(k), (list, dict)) else r.get(k) for k in fields})
        with (self.docs / "priority_gaps.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            fields2 = ["priority", "file", "field", "category", "verification_status", "missing_evidence", "id"]
            writer = csv.DictWriter(handle, fieldnames=fields2); writer.writeheader()
            for r in sorted(self.entries, key=lambda x: (x["priority"], x["file"], x["field"])):
                if r["missing_evidence"] or r["category"] == "unsubstantiated_assumption":
                    writer.writerow({k: json.dumps(r[k], ensure_ascii=False) if isinstance(r[k], list) else r[k] for k in fields2})
        return summary


def self_test() -> dict:
    base = {"nodes": [{"name": "a", "x": 1}, {"name": "b", "x": 2}], "planning": {"discount_rate": .05}}
    override = {"nodes": [{"name": "a", "x": 3}, {"name": "b", "_delete": True}]}
    value, origins = merge_pair((base, origin_tree(base, "base.yaml")), (override, origin_tree(override, "override.yaml")))
    assert value["nodes"] == [{"name": "a", "x": 3}]
    assert origins["nodes"][0]["x"]["file"] == "override.yaml"
    assert origins["planning"]["discount_rate"]["file"] == "base.yaml"
    assert pointer(("a/b", "x~y", 0)) == "/a~1b/x~0y/0"
    assert infer_unit("solar_irradiance_kwh_m2_hour", "data/public_sources/weather.csv").startswith("Wh/")
    assert infer_unit("solar_irradiance_kwh_m2_hour", "data/competition_20261001/weather.csv").startswith("kWh/")
    assert list(walk({"a": [1, True, "2"]})) == [(('a', 0), 1, {}), (('a', 1), True, {})]
    audit = Audit.__new__(Audit)
    unsupported = {"source_ids": ["S3_WUHAN_LOW_ENERGY_DRAFT_2024"], "value": 5.1,
                   "allowed_true_project_experiment": False, "allowed_documented_public_data_study": False,
                   "missing_evidence": []}
    audit.review_exact_fact(unsupported, {"metric": "generic_annual_ground_hp_eer"})
    assert not unsupported["allowed_documented_public_data_study"]
    supported = {**unsupported, "value": 10, "missing_evidence": []}
    audit.review_exact_fact(supported, {"metric": "minimum_ground_balance_analysis_horizon"})
    assert supported["allowed_documented_public_data_study"] and not supported["allowed_true_project_experiment"]
    return {"status": "passed", "checks": 9, "scope": "inheritance ownership, deletion, pointer escape, radiation unit distinction, numeric coverage, no broad-source endorsement, draft scope restriction"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--weather-addendum", action="store_true", help="Offline independent NASA-column supplement; no full rescan")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=True)); return
    if args.weather_addendum:
        (args.root / OUTPUT / "manifest.json").unlink(missing_ok=True)
        result = verify_weather_addendum(args.root)
        dump(args.root / DOCS / "weather_field_addendum.json", result)
        dump(args.root / OUTPUT / "weather_incremental_review.json", {k: v for k, v in result.items() if k != "entries"})
        print(json.dumps({k: v for k, v in result.items() if k not in {"entries", "checks"}}, ensure_ascii=True)); return
    summary = Audit(args.root).run()
    display = {k: v for k, v in summary.items() if k != "external_public_experiment_reconstruction"}
    external = summary["external_public_experiment_reconstruction"]
    display["external_experiment_check"] = {"status": external.get("status"), "rows": external.get("rows"),
                                            "columns_checked": len(external.get("columns", {}))}
    print(json.dumps(display, ensure_ascii=True, indent=2))
    if not summary["complete_inventory"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
