"""Export only the four fully compared official weather-product columns.

No generated demand, occupancy, tariff, building parameter or clock conversion.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/"data/verified_weather_20261002"
REPORT = ROOT/"outputs/competition_20261001/C0_weather_02/verification_report.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> None:
    if (DATA/"manifest.json").exists():
        raise FileExistsError("verified public-product package is immutable")
    audit_manifest = json.loads((REPORT.parent/"manifest.json").read_text(encoding="utf-8"))
    if digest(REPORT) != audit_manifest["files"][REPORT.name]:
        raise ValueError("weather comparison report changed")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if not report["complete"] or not report["all_archives_match"] or report["compared_profiles"] != 15:
        raise ValueError("full official comparison did not pass")
    (DATA/"clean").mkdir(exist_ok=True)
    exports = []
    for record in report["records"]:
        raw = ROOT/record["response_file"]
        if digest(raw) != record["response_sha256"]:
            raise ValueError("official response changed")
        payload = json.loads(raw.read_bytes())
        series = payload["properties"]["parameter"]
        keys = sorted(series["T2M"])
        frame = pd.DataFrame({
            "source_time_lst": pd.to_datetime(keys, format="%Y%m%d%H").strftime("%Y-%m-%d %H:%M:%S"),
            "outdoor_temp_c": [series["T2M"][k] for k in keys],
            "relative_humidity_pct": [series["RH2M"][k] for k in keys],
            "wind_speed_10m_m_s": [series["WS10M"][k] for k in keys],
            "solar_downward_energy_wh_m2": [series["ALLSKY_SFC_SW_DWN"][k] for k in keys],
        })
        out = DATA/f"clean/{record['profile']}_{record['year']}_lst.csv"
        if out.exists():
            raise FileExistsError(out)
        frame.to_csv(out, index=False, float_format="%.12g", encoding="utf-8")
        reread = pd.read_csv(out)
        pd.testing.assert_frame_equal(reread, frame, check_dtype=False, check_exact=True)
        exports.append({"file": out.relative_to(DATA).as_posix(), "sha256": digest(out),
                        "rows": len(frame), "raw_file": raw.relative_to(DATA).as_posix(),
                        "raw_sha256": digest(raw), "request_url": record["request_url"],
                        "request_identity": record["request_identity"], "header": record["header"]})
    (DATA/"clean_export_records.json").write_text(json.dumps(exports, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    selected = [p for p in DATA.rglob("*") if p.is_file() and p.name != "manifest.json"]
    manifest = {"complete": True, "evidence_purpose": "verified_official_weather_product_not_station_measurements",
                "rows": sum(r["rows"] for r in exports), "clean_files": len(exports),
                "files": {p.relative_to(DATA).as_posix(): digest(p) for p in sorted(selected)},
                "comparison_report": REPORT.relative_to(ROOT).as_posix(), "comparison_report_sha256": digest(REPORT),
                "export_script_sha256": digest(Path(__file__)),
                "includes_synthetic_buildings_prices_or_demand": False}
    (DATA/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"complete": True, "clean_files": len(exports), "hours": manifest["rows"]}))


if __name__ == "__main__":
    run()
