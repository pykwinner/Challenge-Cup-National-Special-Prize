"""Prepare traceable GB 50736-2012 references; never solve or rewrite old runs."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from heatplan.io import load_config

APPENDIX_URL = "https://gf.cabr-fire.com/m/article-12078.htm"
EPW_DICTIONARY = "https://bigladdersoftware.com/epx/docs/24-2/auxiliary-programs/energyplus-weather-file-epw-data-dictionary.html"
EVIDENCE = ROOT / "docs/source_intake_20261002/evidence"
CITIES = {
    "urumqi": {
        "city_cn": "乌鲁木齐", "station_id": "51463", "station_name": "乌鲁木齐",
        "latitude_deg": 43 + 47 / 60, "longitude_deg": 87 + 37 / 60,
        "elevation_m": 917.9, "statistical_period": [1971, 2000],
        "heating_outdoor_design_c": -19.7, "winter_ac_outdoor_design_c": -23.7,
        "summer_ac_dry_bulb_design_c": 33.5, "summer_ac_wet_bulb_design_c": 18.2,
        "winter_mean_wind_m_s": 1.6, "winter_pressure_hpa": 924.6,
        "design_heating_days_at_5c": 158, "design_heating_dates_at_5c": ["10-24", "03-30"],
        "design_heating_days_at_8c": 180, "design_heating_dates_at_8c": ["10-14", "04-11"],
        "image": "appendix_a_37.jpg", "image_url": "https://gf.cabr-fire.com/Web/uploads/allimg/221207/10-22120G05222U9.jpg",
        "table_column": "新疆；乌鲁木齐；台站51463",
        "base_config": "data/public_scenario_inputs/scenario1_urumqi_public.yaml",
    },
    "guyuan": {
        "city_cn": "固原", "station_id": "53817", "station_name": "固原",
        "latitude_deg": 36.0, "longitude_deg": 106 + 16 / 60,
        "elevation_m": 1753.0, "statistical_period": [1971, 2000],
        "heating_outdoor_design_c": -13.2, "winter_ac_outdoor_design_c": -17.3,
        "summer_ac_dry_bulb_design_c": 27.7, "summer_ac_wet_bulb_design_c": 19.0,
        "winter_mean_wind_m_s": 2.7, "winter_pressure_hpa": 826.8,
        "design_heating_days_at_5c": 166, "design_heating_dates_at_5c": ["10-21", "04-04"],
        "design_heating_days_at_8c": 189, "design_heating_dates_at_8c": ["10-10", "04-16"],
        "image": "appendix_a_36.jpg", "image_url": "https://gf.cabr-fire.com/Web/uploads/allimg/221207/10-22120G0520L13.jpg",
        "table_column": "宁夏；固原；台站53817",
        "base_config": "data/public_scenario_inputs/scenario2_guyuan_evidence_public.yaml",
    },
    "wuhan": {
        "city_cn": "武汉", "station_id": "57494", "station_name": "武汉",
        "latitude_deg": 30 + 37 / 60, "longitude_deg": 114 + 8 / 60,
        "elevation_m": 23.1, "statistical_period": [1971, 2000],
        "heating_outdoor_design_c": -0.3, "winter_ac_outdoor_design_c": -2.6,
        "summer_ac_dry_bulb_design_c": 35.2, "summer_ac_wet_bulb_design_c": 28.4,
        "winter_mean_wind_m_s": 2.7, "winter_pressure_hpa": 1023.5,
        "design_heating_days_at_5c": 50, "design_heating_dates_at_5c": ["12-22", "02-09"],
        "design_heating_days_at_8c": 98, "design_heating_dates_at_8c": ["11-27", "03-04"],
        "image": "appendix_a_18.jpg", "image_url": "https://gf.cabr-fire.com/Web/uploads/allimg/221207/10-22120G04114V5.jpg",
        "table_column": "湖北；武汉；台站57494",
        "base_config": "data/public_scenario_inputs/scenario3_wuhan_joint_public.yaml",
    },
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    data = {
        "standard": "GB 50736-2012 民用建筑供暖通风与空气调节设计规范",
        "source_kind": "规范设计参考表；不是逐时实测建筑负荷",
        "source_url": APPENDIX_URL,
        "user_link": "http://m.jianbiaoku.com/webarbs/book/16582/1663708.shtml",
        "user_link_role": "条文说明的附录A入口；正文表入口为1663640.shtml",
        "verification": "人工逐列读取中国建筑科学研究院建筑防火研究所刊载的表A原图，保留原图SHA-256",
        "applicability": "2012版设计参考；工程选用同时核对现行强制性通用规范及当地标准，尚未完成最新地方参数替代审核",
        "definition_url": "https://gf.cabr-fire.com/m/article-11766.htm",
        "heating_design_definition": "第4.1.2条：历年平均不保证5天的日平均温度；不是逐时最低温度",
        "design_heating_dates_role": "设计计算用的气候统计供暖期；不是当地行政供暖日期或本项目运行边界",
        "units": {"temperature": "degC", "pressure": "hPa", "wind": "m/s", "elevation": "m"},
        "profiles": {},
    }
    for city, original in CITIES.items():
        spec = dict(original)
        image_path = EVIDENCE / spec["image"]
        spec["source_image_sha256"] = sha(image_path)
        spec["source_image_file"] = str(image_path.relative_to(ROOT)).replace("\\", "/")
        data["profiles"][city] = spec
    save(ROOT / "data/design_parameters_gb50736_2012_verified.json", data)

    entries = [(city, spec["base_config"], "heating_outdoor_design_c", f"{city}_heating_reference")
               for city, spec in CITIES.items()]
    entries += [
        ("wuhan", "data/competition_20261001/wuhan_rc_candidate.yaml", "heating_outdoor_design_c", "wuhan_rc_heating_reference"),
        ("wuhan", "data/competition_20261001/wuhan_rc_candidate.yaml", "winter_ac_outdoor_design_c", "wuhan_rc_winter_ac_reference"),
    ]
    audit = []
    target_dir = ROOT / "data/design_reference_20261002"
    target_dir.mkdir(exist_ok=True)
    for city, base, field, name in entries:
        spec = data["profiles"][city]
        target = target_dir / f"{name}.yaml"
        parent = "../" + base.removeprefix("data/")
        target.write_text(
            f"# GB 50736-2012 Table A reference; independent cold-wave tests still required.\n"
            f"extends: {parent}\nplanning:\n"
            f"  design_outdoor_temperature_c: {spec[field]}\n"
            f"  design_parameter_basis:\n"
            f"    standard: GB50736-2012\n"
            f"    city: {city}\n"
            f"    station_id: '{spec['station_id']}'\n"
            f"    field: {field}\n"
            f"    role: standard_reference_case_not_extreme_weather_or_observation\n"
            f"    source_url: {APPENDIX_URL}\n"
            f"    source_image_sha256: {spec['source_image_sha256']}\n",
            encoding="utf-8",
        )
        resolved = load_config(target)
        inherited = load_config(ROOT / base)
        actual = resolved["planning"]["design_outdoor_temperature_c"]
        if actual != spec[field]:
            raise AssertionError("Reference overlay did not resolve correctly")
        audit.append({
            "city": city, "base_config": base, "overlay": str(target.relative_to(ROOT)).replace("\\", "/"),
            "base_design_c": inherited["planning"]["design_outdoor_temperature_c"],
            "reference_design_c": actual, "reference_field": field,
            "base_sha256": sha(ROOT / base), "overlay_sha256": sha(target),
            "config_resolution_pass": True, "new_optimization_run_performed": False,
            "legacy_load_regeneration_performed": False,
            "rc_reference_path_enabled": bool(resolved.get("building_thermal", {}).get("enabled", False)),
        })
    save(ROOT / "docs/source_intake_20261002/design_config_audit.json", audit)

    weather_root = ROOT / "data/cswd_verified_20261002"
    if (weather_root / "manifest.json").exists():
        manifest = json.loads((weather_root / "manifest.json").read_text(encoding="utf-8"))
        manifest["dataset"] = "CSWD典型气象年EPW"
        manifest["all_format_checks_pass"] = False
        manifest["status"] = "raw_files_cached_and_calendar_audited; zero-minute-convention_requires_explicit_adapter"
        manifest["epw_dictionary_url"] = EPW_DICTIONARY
        manifest["timezone_rule"] = "三个文件LOCATION时区均为UTC+8标准时；Year=2005标签不作为实测年份证明；原始Minute=0保留，未自动改写为60"
        for report in manifest["selected_cities"]:
            city = report["city"]
            report["city_cn"] = CITIES[city]["city_cn"]
            epw_path = weather_root / report["epw_file"]
            with epw_path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))[8:]
            actual = [(int(row[1]), int(row[2]), int(row[3])) for row in rows]
            first = datetime(2001, 1, 1)
            expected = [((first + timedelta(hours=i)).month,
                         (first + timedelta(hours=i)).day,
                         (first + timedelta(hours=i)).hour + 1) for i in range(8760)]
            report["checks"]["month_day_hour_complete_and_ordered"] = actual == expected
            report["calendar_check_pass"] = actual == expected
            report["raw_minute_values"] = sorted({int(row[4]) for row in rows})
            report["time_format_note"] = "所有Minute均为0，与引用字典1..60不一致；保留源文件，逐时导入时显式声明按Hour=1..24解释区间，尚未转换模型输入"
            report["archive_and_extracted_epw_sha_match"] = sha(epw_path) == report["epw_sha256"]
            if not report["archive_and_extracted_epw_sha_match"]:
                raise AssertionError("EPW bytes differ from recorded acquisition")
            report["format_check_pass"] = all(report["checks"].values())
            reference = CITIES[city]
            report["norm_vs_epw_station_metadata"] = {
                "standard_station_id": reference["station_id"],
                "epw_station_id": report["location_header"][5],
                "standard_elevation_m": reference["elevation_m"],
                "epw_elevation_m": float(report["location_header"][9]),
                "elevation_difference_m": float(report["location_header"][9]) - reference["elevation_m"],
                "interpretation": "分别保留规范与EPW原始台站信息；不自动合并坐标和海拔",
            }
            save(weather_root / city / "format_report.json", report)
        manifest["all_calendar_checks_pass"] = all(r["calendar_check_pass"] for r in manifest["selected_cities"])
        save(weather_root / "manifest.json", manifest)
    print(json.dumps({"reference_profiles": len(data["profiles"]), "config_overlays": len(audit),
                      "config_resolution_pass": all(row["config_resolution_pass"] for row in audit)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
