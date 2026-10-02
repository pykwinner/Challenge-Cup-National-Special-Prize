"""Compare every archived weather hour with a fresh official NASA POWER response.

Read-only for old CSVs. Derived demand/occupancy is never certified by this audit.
New responses, including API metadata and physical units, are retained verbatim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/verified_weather_20261002"
OUT = ROOT / "outputs/competition_20261001/C0_weather"
FIELDS = {
    "T2M": ("outdoor_temp", "C"),
    "RH2M": ("relative_humidity_pct", "%"),
    "WS10M": ("wind_speed_10m_m_s", "m/s"),
    # The historical column name is wrong: the stored numbers are Wh/m2.
    "ALLSKY_SFC_SW_DWN": ("solar_irradiance_kwh_m2_hour", "Wh/m^2"),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def compare(original: pd.DataFrame, payload: dict, year: int) -> dict:
    header = payload["header"]
    if header["time_standard"].upper() != "LST":
        raise ValueError("original archive is LST; refuse another clock")
    expected_time = pd.date_range(f"{year}-01-01", f"{year+1}-01-01", freq="h", inclusive="left")
    actual_time = pd.DatetimeIndex(pd.to_datetime(original["time"]))
    if not actual_time.equals(expected_time):
        raise ValueError("archive has a missing, duplicate or shifted hour")
    keys = expected_time.strftime("%Y%m%d%H").tolist()
    fields = {}
    for source_name, (column, unit) in FIELDS.items():
        metadata = payload["parameters"][source_name]
        if metadata["units"] != unit:
            raise ValueError(f"unexpected official unit: {source_name}: {metadata['units']}")
        mapping = payload["properties"]["parameter"][source_name]
        if set(mapping) != set(keys):
            raise ValueError(f"official response clock differs: {source_name}")
        reference = np.array([mapping[key] for key in keys], dtype=float)
        archived = original[column].to_numpy(dtype=float)
        if not np.isfinite(reference).all() or not np.isfinite(archived).all():
            raise ValueError("nonfinite archive or official values")
        fill = float(header["fill_value"])
        if np.any(reference == fill) or np.any(archived == fill):
            raise ValueError("missing-value marker present; no filling or filtering allowed")
        error = np.abs(archived-reference)
        mismatch = np.flatnonzero(error > 1e-9)
        fields[source_name] = {
            "archive_column": column, "actual_unit": unit, "rows": len(keys),
            "absolute_comparison_tolerance": 1e-9,
            "maximum_absolute_difference": float(error.max()),
            "mismatched_rows": int(len(mismatch)),
            "first_mismatches": [{"time_lst": keys[i], "archive": float(archived[i]),
                                  "official_current": float(reference[i])} for i in mismatch[:10]],
            "all_values_match": len(mismatch) == 0,
        }
    return {"rows": len(keys), "fields": fields,
            "all_values_match": all(v["all_values_match"] for v in fields.values()),
            "scope": "four original NASA product columns only; not derived demand, occupancy or buildings"}


def run(fetch: bool, output: Path = OUT, previous_report: Path | None = None) -> None:
    if (output/"manifest.json").exists():
        raise FileExistsError("completed weather audit is immutable; choose a new experiment path")
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA/"evidence").mkdir(exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    metadata_file = ROOT/"data/public_sources/crawl_metadata.json"
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    plan = {"registered_before_full_comparison": True,
            "all_profiles_and_years_in_original_manifest": True,
            "source_metadata_sha256": digest(metadata_file), "fields": {k: list(v) for k, v in FIELDS.items()},
            "no_original_mutation_or_field_replacement": True,
            "comparison_tolerance": 1e-9,
            "limitations": ["Current API equality is not ground-station validation.",
                            "The old API version was not archived; do not backfill it.",
                            "Mismatches can reflect source revisions; retain them, do not silently replace inputs."]}
    plan_file = DATA/"analysis_plan.json"
    if not plan_file.exists():
        save(plan_file, plan)
    elif json.loads(plan_file.read_text(encoding="utf-8")) != plan:
        raise ValueError("weather audit plan changed")
    previous_path = previous_report or output/"verification_report.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    previous_records = {(r["profile"], r["year"]): r for r in previous.get("records", [])}
    records = []
    for key, item in metadata["weather_profiles"].items():
        if not key.startswith(("scenario1_urumqi_", "scenario2_guyuan_", "scenario3_wuhan_")):
            raise ValueError("unexpected original weather profile")
        year = int(item["year"])
        profile = key.rsplit("_", 1)[0]
        original_file = ROOT/f"data/public_sources/{profile}_weather_{year}.csv"
        before = digest(original_file)
        url = item["request_url"]
        request_identity = "original_archived_request_url"
        if url == f"reused_existing_file:{original_file.name}":
            # The old 2023 crawl record has no URL. A current verification query
            # is reconstructed transparently, never backfilled as historical.
            source_query = metadata["weather_profiles"][f"{profile}_2024"]["request_url"]
            parsed_source = urlparse(source_query)
            params = parse_qs(parsed_source.query)
            params["start"], params["end"] = [f"{year}0101"], [f"{year}1231"]
            url = parsed_source._replace(query=urlencode(params, doseq=True)).geturl()
            request_identity = "new_verification_query_using_same_profile_2024_coordinates_and_target_year"
            addendum = DATA/"request_provenance_addendum.json"
            if not addendum.exists():
                save(addendum, {"registered_before_2023_comparison": True,
                    "reason": "2023 original metadata records reused_existing_file, not an original request URL",
                    "method": "retain profile coordinates/parameters/clock from its archived 2024 query; change start/end to 2023",
                    "does_not_recover_original_retrieval_date_or_api_version": True})
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.scheme != "https" or parsed.netloc != "power.larc.nasa.gov" or parsed.path != "/api/temporal/hourly/point":
            raise ValueError("not the original public NASA endpoint")
        if query.get("time-standard") != ["LST"] or query.get("start") != [f"{year}0101"] or query.get("end") != [f"{year}1231"]:
            raise ValueError("request is not the complete matching LST year")
        response_file = DATA/f"evidence/{key}_lst.json"
        record = {"profile": profile, "year": year, "request_url": url,
                  "archived_request_record": item["request_url"], "request_identity": request_identity,
                  "archive": original_file.relative_to(ROOT).as_posix(), "archive_sha256_before": before}
        old = previous_records.get((profile, year), {})
        if old.get("retrieved_at_utc"):
            record["retrieved_at_utc"] = old["retrieved_at_utc"]
        try:
            if not response_file.exists():
                if not fetch:
                    raise FileNotFoundError("missing original response; explicitly use --fetch for network retrieval")
                with urlopen(Request(url, headers={"User-Agent": "HeatPlanningResearch/1.0"}), timeout=55) as response:
                    content = response.read()
                    json.loads(content)  # Never save an error HTML page as a dataset.
                    response_file.write_bytes(content)
                    record["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat()
            payload = json.loads(response_file.read_bytes())
            record.update({"response_file": response_file.relative_to(ROOT).as_posix(),
                           "response_sha256": digest(response_file), "header": payload["header"],
                           "official_parameter_metadata": payload["parameters"]})
            record.update(compare(pd.read_csv(original_file), payload, year))
            record["status"] = "compared"
        except Exception as error:
            record.update(status="failed", error=str(error), all_values_match=False)
        record["archive_sha256_after"] = digest(original_file)
        record["original_unchanged"] = before == record["archive_sha256_after"]
        records.append(record)
        save(output/"verification_report.json", {"complete": False, "records": records})
        print(json.dumps({"key": key, "status": record["status"],
                          "all_values_match": record["all_values_match"]}), flush=True)
    report = {"complete": True, "audit_scope": plan, "records": records,
              "compared_profiles": sum(r["status"] == "compared" for r in records),
              "total_hours_compared": sum(r.get("rows", 0) for r in records),
              "all_archives_match": all(r["all_values_match"] and r["original_unchanged"] for r in records),
              "certifies_building_prices_or_local_measurement_accuracy": False}
    save(output/"verification_report.json", report)
    (output/"verification_script.py").write_bytes(Path(__file__).read_bytes())
    save(output/"manifest.json", {"complete": True, "purpose": "official_weather_product_source_comparison",
         "all_archives_match": report["all_archives_match"],
         "files": {"verification_report.json": digest(output/"verification_report.json"),
                   "verification_script.py": digest(output/"verification_script.py")},
         "code_sha256": digest(Path(__file__)), "plan_sha256": digest(plan_file),
         "source_metadata_sha256": digest(metadata_file),
         "previous_retrieval_log_sha256": digest(previous_report) if previous_report else None})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--previous-report", type=Path)
    args = parser.parse_args()
    run(args.fetch, args.output_dir.resolve(), args.previous_report)
