"""Offline conditional Hubei industrial-account TOU reference; base E0 is immutable.

No actual account, grid connection, physical equipment, or full economics admitted.
Run from the project root, with --verify-only for read-only hash/identity checks.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("base_evidence_case", ROOT / "scripts/prepare_evidence_case_20261002.py")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)
DOC = base.DOC_DIR
EVIDENCE = DOC / "tou_evidence"
CONFIG = base.CONFIG_DIR / "wuhan_bestest600_rc_tou.yaml"
TIME = base.DATA_DIR / "wuhan_2024_2025_hourly_tou.csv"
FIELDS = DOC / "field_provenance_tou.csv"
MONTHS = DOC / "monthly_tariff_verified.csv"
RULES = DOC / "tou_account_and_hours.json"
ADMISSION = DOC / "admission_tou.json"
SCRIPT = DOC / "prepare_tou.py"
LOCKS = {
    "hubei_2024_11.pdf": "9fa81bc3be47c1917fbd0e0f6cac1bf1dfb5edc47559f155b9b82e97611f8b33",
    "hubei_2024_12.pdf": "74161836e189120f1f86c33f38010fc01a4e6e7e3706dfc55b07a6bf5a48163a",
    "hubei_2025_months.pdf": "efccc95604a44d0871a82d2315e3b30ff3c9f2aa4b7c2d5d29efd187294b632d",
    "hubei_tou_policy_77_2024.pdf": "086aea2d5388ed5fb18cbfb8a7c2ef362c66896c43027681d8a6a8347856edc4",
    "hubei_single_two_part_policy_163_2023.pdf": "40cf12a479b73eec03940773caf2fb7d8834481214075af1ada0a6f304f16182",
}
# Manual transcription checked against the five original PDF pages, not against
# the intake's extraction alone. These are complete published energy-charge
# columns; do not multiply them by TOU coefficients or add fees a second time.
RATE_ROWS = [
    ("2024-11", 1.1724, 1.0188, .7762, .5187, "TOU_NOV", 1),
    ("2024-12", 1.2908, 1.0368, .7927, .5187, "TOU_DEC", 1),
    ("2025-01", 1.1995, .9744, .7581, .5153, "TOU_2025", 12),
    ("2025-02", 1.0553, .9313, .7353, .5273, "TOU_2025", 11),
    ("2025-03", 1.1144, .9726, .7484, .5105, "TOU_2025", 10),
]
EXTRA_COLUMNS = ["tariff_month", "tariff_bucket", "tariff_source_id", "tariff_pdf_page"]


def source_registry():
    sources = base.source_registry()
    # The residential price source remains only in the base admission, not in
    # this variant's active price provenance.
    sources.pop("PRICE")
    rows = {
        "TOU_NOV": ("hubei_2024_11.pdf", "http://www.yingcheng.gov.cn/u/cms/yingcheng/202410/29154032ntk8.pdf", "2024-11, PDF p1, single-part industrial/commercial <1kV row and footnotes 1-3; government repost of State Grid notice"),
        "TOU_DEC": ("hubei_2024_12.pdf", "http://www.yingcheng.gov.cn/u/cms/yingcheng/202412/04105911f9b9.pdf", "2024-12, PDF p1, same row and footnotes; government repost of State Grid notice"),
        "TOU_2025": ("hubei_2025_months.pdf", "http://www.hb.sgcc.com.cn/html/files/2026-03/06/20260306144020453109213.pdf", "State Grid Hubei official 2025 combined notice: Jan p12, Feb p11, Mar p10; same row and footnotes; document order is reversed"),
        "POLICY77": ("hubei_tou_policy_77_2024.pdf", "https://www.liangzh.gov.cn/lzhxxgk/xxgkml/ggqsydwxxgk/sdq/202508/P020250825377454860287.pdf", "Stamped original E-fagai-jiaguan [2024]77 government repost: PDF p2 scope/hours/coefficients, p3 effective 2024-05-01"),
        "POLICY163": ("hubei_single_two_part_policy_163_2023.pdf", "https://www.liangzh.gov.cn/lzhxxgk/xxgkml/ggqsydwxxgk/sdq/202508/P020250825378015593092.pdf", "Stamped original E-fagai-jiaguan [2023]163 government repost: PDF p2 clause4 single/two-part account scope and minimum 12-month selection period"),
    }
    for key, (name, url, identity) in rows.items():
        sources[key] = {"path": (EVIDENCE / name).as_posix(), "url": url,
                        "sha256": LOCKS[name], "identity": identity}
    return sources


def rules():
    return {
        "identity": "Conditional public tariff-class experiment; no actual billing account observation",
        "selected_class": "Ordinary State Grid Hubei industrial/commercial agent-purchase, single-part, billing supply voltage <1kV",
        "account_eligibility_verified": False,
        "conditional_policy_requirements": [
            "Industrial/commercial account in the Hubei grid with registered supply capacity >=100kVA and <315kVA; single-part selected for a period of at least 12 months",
            "Not railway traction, commercial electricity, government/military/school/hospital/public-lighting or another non-residential-lighting exclusion; no separate special-policy exception is assumed",
            "Ordinary agent-purchase category; not unjustified return from direct market trading, coal captive-plant users or temporarily ineligible high-energy-consuming users subject to 1.5x agent generation price",
            "<1kV is the electricity-billing supply voltage, not an appliance secondary-side 380V label",
        ],
        "capacity_identity": "The policy capacity interval is only a conditional account criterion, not a claimed transformer rating, added model parameter or grid capacity constraint. The model's 1000kW is an ideal BESTEST numerical upper bound, not contracted power. Actual connection and power factor remain unverified.",
        "storage_exception": "No eligibility inferred from the core electric_boiler adapter: it represents an ideal air benchmark, not an actual electric storage boiler. No heat pump or tank is added.",
        "time_zone": "Asia/Shanghai",
        "interval_convention": "Each hourly timestamp labels [timestamp,timestamp+1h); local half-open intervals, no DST",
        "admitted_months": [row[0] for row in RATE_ROWS],
        "hours_non_july_august": {"sharp": [18, 19], "peak": [16, 17, 20, 21, 22, 23],
                                  "flat": [6, 7, 8, 9, 10, 11, 14, 15], "valley": [0, 1, 2, 3, 4, 5, 12, 13]},
        "coefficient_context_only": {"ordinary_months": {"sharp": 1.8, "peak": 1.49, "flat": 1, "valley": .48},
                                     "december_january": {"sharp": 2, "peak": 1.49, "flat": 1, "valley": .45},
                                     "rule": "Published complete rates used directly; these multipliers are NOT applied again to energy-charge columns"},
        "billing_scope": "Published monthly energy charges including published line-loss, transmission/distribution, government-fund and system-operation components. Single-part selected row has no capacity/demand basic-charge column. Power-factor adjustment, other reactive penalties, actual settlement corrections, taxes outside published columns, contract fees and other services are outside the experiment.",
        "policy_sources": {"hours_and_scope": "POLICY77 PDF p2/p3", "single_part_scope": "POLICY163 PDF p2 clause4", "ordinary_agent_category": "all monthly PDF footnote3"},
    }


def monthly_table():
    rows = []
    for month, sharp, peak, flat, valley, source, page in RATE_ROWS:
        start = pd.Timestamp(month + "-01", tz="Asia/Shanghai")
        rows.append({"tariff_month": month, "sharp_yuan_per_kwh": sharp, "peak_yuan_per_kwh": peak,
                     "flat_yuan_per_kwh": flat, "valley_yuan_per_kwh": valley,
                     "valid_from_inclusive": start.isoformat(), "valid_until_exclusive": (start + pd.offsets.MonthBegin(1)).isoformat(),
                     "user_class": "工商业代理购电-单一制", "billing_voltage_class": "不满1千伏",
                     "basic_capacity_demand_charge": "not applicable to selected single-part row (published dashes)",
                     "source_id": source, "pdf_page_one_based": page, "row_locator": "upper table: single-part, first voltage row; sharp/peak/flat/valley columns",
                     "original_page_visually_verified": True})
    return pd.DataFrame(rows)


def hour_bucket(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("TOU timestamp must carry its timezone")
    stamp = stamp.tz_convert("Asia/Shanghai")
    month = stamp.strftime("%Y-%m")
    if month not in {row[0] for row in RATE_ROWS}:
        raise ValueError(f"No source-admitted rate for {month}; no fallback")
    if stamp.minute or stamp.second or stamp.microsecond or stamp.nanosecond:
        raise ValueError("Only exact hourly labels admitted")
    for bucket, hours in rules()["hours_non_july_august"].items():
        if stamp.hour in hours:
            return month, bucket
    raise ValueError("TOU rules do not cover local hour")


def build_frame(root=ROOT):
    frame = pd.read_csv(root / base.TIME_PATH)
    rate_map = {row[0]: dict(zip(("sharp", "peak", "flat", "valley"), row[1:5])) for row in RATE_ROWS}
    locator = {row[0]: (row[5], row[6]) for row in RATE_ROWS}
    mapped = [hour_bucket(value) for value in frame.time]
    frame["electricity_price"] = [rate_map[month][bucket] for month, bucket in mapped]
    frame["tariff_month"] = [month for month, _ in mapped]
    frame["tariff_bucket"] = [bucket for _, bucket in mapped]
    frame["tariff_source_id"] = [locator[month][0] for month, _ in mapped]
    frame["tariff_pdf_page"] = [locator[month][1] for month, _ in mapped]
    return frame


def validate_case(config, frame, root=ROOT):
    if config != base.build_config(True):
        raise ValueError("TOU may only change electricity prices; physical config must equal the admitted base RC")
    expected = build_frame(root)
    if list(frame.columns) != list(expected.columns):
        raise ValueError("TOU columns differ from admitted source/mapping coverage")
    try:
        pd.testing.assert_frame_equal(frame.reset_index(drop=True), expected, check_dtype=False, rtol=0, atol=1e-10)
    except AssertionError as exc:
        raise ValueError("TOU time, monthly rate, bucket, metadata or immutable physical data mismatch") from exc
    physical = frame.drop(columns=EXTRA_COLUMNS).copy()
    physical["electricity_price"] = .58
    base.validate_case(config, physical, rc=True)
    sys.path.insert(0, str(ROOT / "src"))
    from heatplan.io import validate_inputs
    validate_inputs(config, frame)


def provenance(root=ROOT):
    fields = pd.read_csv(root / base.FIELD_PATH)
    fields = fields.loc[~fields.artifact.eq("wuhan_bestest600_steady.yaml")].copy()
    fields.loc[fields.artifact.eq("wuhan_bestest600_rc.yaml"), "artifact"] = CONFIG.name
    csv_mask = fields.artifact.eq(base.TIME_PATH.name)
    fields.loc[csv_mask, "artifact"] = TIME.name
    price = fields.artifact.eq(TIME.name) & fields.field.eq("electricity_price")
    fields.loc[price, "source_ids"] = "TOU_NOV;TOU_DEC;TOU_2025;POLICY77;POLICY163"
    fields.loc[price, "formula_or_locator"] = "Five visually verified monthly single-part <1kV rows; local hour mapping by POLICY77; complete published energy charges used directly"
    fields.loc[price, "identity_and_scope"] = "Conditional eligible industrial account reference; account eligibility/connection not verified; no residential-account TOU or real bill claim"
    extra = []
    for name in EXTRA_COLUMNS:
        extra.append({"artifact": TIME.name, "field": name, "value_or_mapping": "hourly source audit metadata, not an optimization parameter",
                      "source_ids": "POLICY77" if name == "tariff_bucket" else "TOU_NOV;TOU_DEC;TOU_2025",
                      "formula_or_locator": "Asia/Shanghai month/hour -> exact original monthly rate table and one-based PDF page",
                      "identity_and_scope": "Complete 2880-hour mapping, no missing-month fallback or interpolation"})
    return pd.concat([fields, pd.DataFrame(extra)], ignore_index=True)


def verify_originals(root=ROOT):
    for name, expected in LOCKS.items():
        path = root / EVIDENCE / name
        if not path.is_file() or base.sha256(path) != expected:
            raise ValueError(f"TOU original hash missing/changed: {name}")


def admission(root=ROOT):
    original = base.verify_admission(root)
    frame = pd.read_csv(root / TIME)
    fields = pd.read_csv(root / FIELDS)
    config = yaml.safe_load((root / CONFIG).read_text(encoding="utf-8"))
    validate_case(config, frame, root)
    expected_fields = {(CONFIG.name, name) for name, _ in base.leaves(config)} | {(TIME.name, name) for name in frame.columns}
    actual_fields = set(zip(fields.artifact, fields.field))
    if actual_fields != expected_fields or len(fields) != len(expected_fields):
        raise ValueError("TOU active field coverage is incomplete or duplicated")
    if any(set(value.split(";")) - set(source_registry()) for value in fields.source_ids):
        raise ValueError("Unknown TOU provenance source")
    paths = list(original["input_sha256"])
    paths += [(DOC / "admission.json").as_posix(), CONFIG.as_posix(), TIME.as_posix(), FIELDS.as_posix(), MONTHS.as_posix(), RULES.as_posix(), SCRIPT.as_posix()]
    paths += [(EVIDENCE / name).as_posix() for name in LOCKS]
    return {
        "schema_version": 1, "case_id": "BESTEST600_derived_Wuhan_weather_2024_2025_conditional_industrial_TOU_reference",
        "physical_reference_admitted": True, "rc_reference_admitted": True,
        "conditional_operating_tariff_reference_admitted": True,
        "account_eligibility_verified": False, "grid_connection_feasibility_verified": False,
        "full_economics_admitted": False, "external_building_validation_passed": False,
        "admission_scope": "These exact hashes only: unchanged public thermal prototype under a conditional policy-eligible industrial account's published monthly TOU energy charges. Not a real account/building/device, grid design, BESTEST certification or life-cycle economic result.",
        "input_paths": {"rc_config": CONFIG.as_posix(), "timeseries": TIME.as_posix(), "field_provenance": FIELDS.as_posix(), "monthly_tariff": MONTHS.as_posix(), "tariff_rules": RULES.as_posix(), "base_admission": (DOC / "admission.json").as_posix()},
        "input_sha256": {path: base.sha256(root / path) for path in dict.fromkeys(paths)},
        "time_scope": original["time_scope"], "parameters": {key: value for key, value in original["parameters"].items() if key != "price_yuan_per_kwh"},
        "active_field_coverage": {"covered_rows": len(fields), "missing": [], "all_config_leaves_and_timeseries_columns": True},
        "sources": source_registry(), "tariff_account_and_hours": rules(),
        "hour_counts": {str(key): int(value) for key, value in frame.tariff_bucket.value_counts().sort_index().items()},
        "month_hour_counts": {str(key): int(value) for key, value in frame.tariff_month.value_counts().sort_index().items()},
        "price_range_yuan_per_kwh": [float(frame.electricity_price.min()), float(frame.electricity_price.max())],
        "structure_approximations": original["structure_approximations"],
        "economic_scope": "2880-hour reference operating electricity energy charges and fixed-factor emissions only; no observed account settlement, capacity/demand fee, power-factor adjustment, CAPEX/OM/life/discounted cash-flow/frontier claim",
        "excluded_economic_fields": original["excluded_economic_fields"] + ["actual tariff eligibility", "actual account settlement", "power factor and reactive adjustment", "connection design and contracted capacity"],
        "inactive_interface_fields": original["inactive_interface_fields"], "disabled_branches": original["disabled_branches"],
        "checks": {"base_admission_preserved": True, "exact_original_hashes": True, "all_five_months_original_page_reviewed": True,
                   "physical_config_byte_identical_to_base": (root / CONFIG).read_bytes() == (root / base.CONFIG_DIR / "wuhan_bestest600_rc.yaml").read_bytes(),
                   "all_non_price_base_columns_preserved": True, "exact_local_hour_rate_mapping": True,
                   "no_missing_month_fallback": True, "fees_not_added_twice": True, "core_input_validation": True,
                   "full_solver_run": "owned by integration runner; record code/input freeze independently"},
    }


def verify_admission(root=ROOT):
    base.verify_admission(root)
    verify_originals(root)
    report = json.loads((root / ADMISSION).read_text(encoding="utf-8"))
    for relative, expected in report["input_sha256"].items():
        if base.sha256(root / relative) != expected:
            raise ValueError(f"TOU admitted input hash mismatch: {relative}")
    if report != admission(root):
        raise ValueError("TOU report scope or policy eligibility flags changed")
    expected_fields = provenance(root).fillna("")
    pd.testing.assert_frame_equal(pd.read_csv(root / FIELDS).fillna(""), expected_fields, check_dtype=False)
    pd.testing.assert_frame_equal(pd.read_csv(root / MONTHS).fillna(""), monthly_table().fillna(""), check_dtype=False)
    if json.loads((root / RULES).read_text(encoding="utf-8")) != rules():
        raise ValueError("TOU policy scope/hours changed")
    return report


def prepare(root=ROOT):
    base.verify_admission(root)
    verify_originals(root)
    # Copying the independent config deliberately preserves byte identity.
    (root / CONFIG).write_bytes((root / base.CONFIG_DIR / "wuhan_bestest600_rc.yaml").read_bytes())
    frame = build_frame(root)
    validate_case(base.build_config(True), frame, root)
    frame.to_csv(root / TIME, index=False, float_format="%.12g")
    provenance(root).to_csv(root / FIELDS, index=False, encoding="utf-8")
    monthly_table().to_csv(root / MONTHS, index=False, encoding="utf-8", float_format="%.4f")
    (root / RULES).write_text(json.dumps(rules(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = admission(root)
    (root / ADMISSION).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verify_admission(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    report = verify_admission() if args.verify_only else prepare()
    print(json.dumps({key: report[key] for key in ("case_id", "input_paths", "hour_counts", "month_hour_counts", "price_range_yuan_per_kwh", "full_economics_admitted", "account_eligibility_verified")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
