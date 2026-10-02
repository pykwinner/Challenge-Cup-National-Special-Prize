"""Publish page-by-page Markdown from the explicitly selected C0 evidence.

No solver, PDF, or PowerPoint authoring is invoked. Unconfirmed human and
enterprise facts remain empty. Writes stay inside the assigned C5 output tree.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/competition_20261001/C5"
DEST = OUT / "答辩逐页Markdown"
MODULE = ROOT / "scripts/build_competition_deliverables.py"
spec = importlib.util.spec_from_file_location("c5_publisher", MODULE)
publisher = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(publisher)


def load(path):
    return publisher.read_json(path)


def link(path, label=None):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    program_receipt = "outputs/competition_20261001/C0/program_execution_receipt_01.json"
    program_references = {"outputs/competition_20261001/C0/optimized_annual_01_review.json", "outputs/competition_20261001/C0/optimized_season_03_review.json"}
    if relative.startswith("outputs/competition_20261001/C4/") or relative in program_references:
        portable = Path(os.path.relpath(ROOT / program_receipt, DEST)).as_posix()
        return f"[C0精简程序执行证据](<{portable}>)"
    historical_prefixes = ("data/competition_20261001/", "data/competition_spatial/")
    historical_files = {
        "docs/execution_20261001/C2_handoff.md", "docs/execution_20261001/C3_handoff.md",
        "outputs/competition_20261001/C0/optimized_annual_01_review.json", "outputs/competition_20261001/C0/optimized_season_03_review.json",
    }
    if relative.startswith(historical_prefixes) or relative in historical_files:
        return f"`{relative}`（历史程序证据定位；不随当前主稿打包）"
    portable = Path(os.path.relpath(path.resolve(), DEST.resolve())).as_posix()
    return f"[{label or path.name}](<{portable}>)"


def table(headers, rows):
    def row(values):
        return "| " + " | ".join(str(v).replace("|", "\\|") for v in values) + " |"
    return "\n".join([row(headers), row(["---"] * len(headers)), *[row(r) for r in rows]])


def bullets(items):
    return "\n".join("- " + item for item in items)


def page(number, title, body, figures, speech, rating, evidence):
    return {
        "number": number, "title": title, "body": body,
        "figures": figures, "speech": speech, "rating": rating,
        "evidence": evidence,
    }


def render(p):
    ordinal = f"{p['number']:02d}" if isinstance(p["number"], int) else str(p["number"])
    return (
        f"# 第{ordinal}页｜{p['title']}\n\n"
        f"## 正文建议\n\n{p['body']}\n\n"
        f"## 图表建议\n\n{p['figures']}\n\n"
        f"## 讲稿要点\n\n{p['speech']}\n\n"
        f"## 对应评分与证据\n\n{p['rating']}\n\n{p['evidence']}\n"
    )


def main():
    selected = OUT / "representative_evidence_spec.json"
    manifest_path = ROOT / "outputs/competition_20261001/C4/optimized_plan_01/manifest.json"
    evidence = publisher.bind_evidence(manifest_path, selected)
    manifest = load(manifest_path)
    if manifest.get("formal_scope") != "representative" or manifest.get("is_full_annual_evidence") is not False:
        raise ValueError("This edition must retain the selected representative scope")
    base = manifest_path.parent.resolve()
    verified_files = {}
    for rel, expected in manifest["files"].items():
        path = (base / rel).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Manifest result path escapes selected directory")
        if not path.is_file() or publisher.sha(path) != expected:
            raise ValueError(f"Selected result file changed: {rel}")
        verified_files[rel] = expected
    if not all(c["passed"] for c in evidence["checks"]):
        raise ValueError("Selected representative assertions failed")
    summary = load(base / "summary.json")
    profile = load(base / "model_profile.json")
    audit = load(base / "c6_building_audit.json")
    if not audit.get("passed") or audit.get("annual_certification") is not False:
        raise ValueError("Unexpected representative building audit scope")
    replay_runs = {}
    for name, scope, hours in [("optimized_annual_01", "annual", 8784), ("optimized_season_03", "season", 2928)]:
        review_path = ROOT / f"outputs/competition_20261001/C0/{name}_review.json"
        review = load(review_path)
        run_manifest_path = ROOT / f"outputs/competition_20261001/C4/{name}/manifest.json"
        run_manifest = load(run_manifest_path)
        if review.get("reviewer") != "C0" or not review.get("complete") or not review.get("passed") or not all(review["checks"].values()):
            raise ValueError("Selected C0 review is incomplete or failed")
        if publisher.sha(run_manifest_path) != review["manifest_sha256"] or review["planning_manifest_sha256"] != evidence["manifest_sha256"]:
            raise ValueError("C0 review identifies a different run or planning batch")
        if not run_manifest.get("complete") or run_manifest.get("formal_scope") != scope or run_manifest["hours"] != hours:
            raise ValueError("Replay has an unexpected formal scope")
        if run_manifest["parent_investment_sha256"] != manifest["investment_sha256"]:
            raise ValueError("Replay investment differs from the selected planning investment")
        for rel, expected in run_manifest["files"].items():
            file = (run_manifest_path.parent / rel).resolve()
            if not file.is_relative_to(run_manifest_path.parent.resolve()) or not file.is_file() or publisher.sha(file) != expected:
                raise ValueError(f"Replay file identity changed: {name}/{rel}")
        replay_runs[scope] = {"name": name, "manifest_path": run_manifest_path, "manifest": run_manifest,
            "review_path": review_path, "review": review, "summary": load(run_manifest_path.parent / "summary.json"),
            "profile": load(run_manifest_path.parent / "model_profile.json")}
    annual = replay_runs["annual"]
    annual_summary, annual_profile = annual["summary"], annual["profile"]
    annual_config = load(annual["manifest_path"].parent / "model_config.json")
    carbon_price_assumption = annual_config["planning"]["carbon_price_yuan_per_kg"]
    if carbon_price_assumption != 0.12:
        raise ValueError("Carbon-price scenario differs from C0's selected interpretation")
    annual_comp = annual_summary["components_yuan_per_year"]
    annual_evidence_note = "；".join(link(r["review_path"], r["name"] + "：C0复核") for r in replay_runs.values()) + "。同一投资，全18项复核通过；楼宇审核限于时段末和声明范围，全矩阵依赖已保存数值审计，并非独立重建全部设备/网络方程。"
    engineering_records = []
    for filename, expected, label in [("regression_004.xml", 628, "最新整合全回归"), ("release_gate_003.xml", 8, "先前发布准入专项，单列")]:
        test_path = ROOT / "outputs/competition_20261001/C0" / filename
        suites = ET.parse(test_path).getroot().findall(".//testsuite")
        counts = {field: sum(int(s.get(field, 0)) for s in suites) for field in ["tests", "failures", "errors", "skipped"]}
        if counts["tests"] != expected or any(counts[field] for field in ["failures", "errors", "skipped"]):
            raise ValueError("Engineering test record does not match C0's declared result")
        engineering_records.append({"label": label, "path": str(test_path), "sha256": publisher.sha(test_path),
            "counts": counts, "xml_seconds": sum(float(s.get("time", 0)) for s in suites),
            "scope": "current integration epoch; separate from historical C4 solver code hashes"})
    weather_manifest_path = ROOT / "outputs/competition_20261001/C0_weather_02/manifest.json"
    weather_manifest = load(weather_manifest_path)
    weather_report_path = weather_manifest_path.parent / "verification_report.json"
    if not weather_manifest.get("complete") or not weather_manifest.get("all_archives_match"):
        raise ValueError("Selected weather provenance audit is incomplete")
    for name, expected in weather_manifest["files"].items():
        path = (weather_manifest_path.parent / name).resolve()
        if not path.is_relative_to(weather_manifest_path.parent.resolve()) or publisher.sha(path) != expected:
            raise ValueError("Weather audit file identity changed")
    weather_report = load(weather_report_path)
    weather_records = weather_report["records"]
    if len(weather_records) != 15 or sum(r["rows"] for r in weather_records) != 131544:
        raise ValueError("Unexpected weather audit comparison scope")
    for row in weather_records:
        if not row.get("all_values_match") or not row.get("original_unchanged") or row["archive_sha256_before"] != row["archive_sha256_after"]:
            raise ValueError("Original weather archive changed or comparison failed")
        for name, expected in [(row["archive"], row["archive_sha256_after"]), (row["response_file"], row["response_sha256"])]:
            if publisher.sha(ROOT / name) != expected:
                raise ValueError("Weather archive or official-response bytes changed")
        if set(row["fields"]) != {"T2M", "RH2M", "WS10M", "ALLSKY_SFC_SW_DWN"} or not all(f["all_values_match"] for f in row["fields"].values()):
            raise ValueError("Weather field scope is inconsistent")
        if row["official_parameter_metadata"]["ALLSKY_SFC_SW_DWN"]["units"] != "Wh/m^2":
            raise ValueError("Actual official radiation unit changed")
    weather_addendum_path = ROOT / "data/verified_weather_20261002/request_provenance_addendum.json"
    weather_addendum = load(weather_addendum_path)
    history_dir = DEST / "历史快照_20261002_主体收紧_01"
    if not (history_dir / "归档清单.json").is_file():
        raise ValueError("Archive the previous 25-page edition before narrowing its main results")
    external_approval_path = ROOT / "outputs/competition_20261001/C0/external_citation_approval_01.json"
    external_approval = load(external_approval_path)
    external_manifest_path = ROOT / "outputs/competition_20261001/C1_external/manifest.json"
    external_review_path = ROOT / "outputs/competition_20261001/C6/external_measurement_review.json"
    if not external_approval.get("approved_for_limited_method_applicability_citation") or external_approval.get("empirical_planning_case_approved") is not False:
        raise ValueError("External citation lacks C0's limited-scope approval")
    if publisher.sha(external_manifest_path) != external_approval["external_manifest_sha256"] or publisher.sha(external_review_path) != external_approval["independent_review_sha256"]:
        raise ValueError("External approval identifies different evidence")
    external_manifest = load(external_manifest_path)
    if not external_manifest.get("complete"):
        raise ValueError("External measurement diagnostic is incomplete")
    for item in external_manifest["inputs"]:
        if publisher.sha(ROOT / item["file"]) != item["sha256"]:
            raise ValueError("Approved external input changed")
    for item in external_manifest["outputs"]:
        if publisher.sha(external_manifest_path.parent / item["file"]) != item["sha256"]:
            raise ValueError("Approved external output changed")
    external_review = load(external_review_path)
    if not external_review["verification_summary"]["passed"] or not external_review["decision"]["approved_for_limited_applicability_citation"]:
        raise ValueError("Independent external review did not pass")
    external_report_path = external_manifest_path.parent / "validation_report.json"
    external_report = load(external_report_path)
    external_test = external_report["metrics"]["test"]
    followup_approval_path = ROOT / "outputs/competition_20261001/C0/external_followup_citation_approval_01.json"
    followup_approval = load(followup_approval_path)
    followup_manifest_path = ROOT / "outputs/competition_20261001/C1_external_followup/manifest.json"
    reproduction_path = ROOT / "outputs/competition_20261001/C0_external_reproduction_01/reproduction_report.json"
    if not followup_approval.get("approved_for_exploratory_loss_comparison_citation") or followup_approval.get("fresh_independent_confirmation"):
        raise ValueError("Followup citation has an unexpected approval scope")
    if publisher.sha(followup_manifest_path) != followup_approval["source_manifest_sha256"] or publisher.sha(reproduction_path) != followup_approval["independent_reproduction_report_sha256"]:
        raise ValueError("Followup approval identifies a different study")
    followup_manifest = load(followup_manifest_path)
    if not followup_manifest.get("complete") or not followup_manifest.get("exploratory_after_first_test_inspection"):
        raise ValueError("Followup must remain exploratory after first test inspection")
    for item in followup_manifest["outputs"]:
        if publisher.sha(followup_manifest_path.parent / item["file"]) != item["sha256"]:
            raise ValueError("Followup output changed")
    reproduction = load(reproduction_path)
    if not reproduction.get("complete") or not all(s["passed"] and s["fit"]["refit_executed"] and s["max_abs_metric_difference"] == 0 and s["fit"]["maximum_absolute_parameter_difference"] == 0 for s in reproduction["studies"].values()):
        raise ValueError("C0 same-protocol refit did not reproduce the studies")
    followup_report_path = followup_manifest_path.parent / "validation_report.json"
    followup_test = load(followup_report_path)["metrics"]["test"]
    published_approval_path = ROOT / "outputs/competition_20261001/C0/published_method_citation_approval_01.json"
    published_approval = load(published_approval_path)
    published_base = ROOT / "outputs/competition_20261001/C4_published_method/published_lp_01"
    published_manifest_path = published_base / "manifest.json"
    published_manifest = load(published_manifest_path)
    if not published_approval.get("approved_for_published_mathematical_method_citation") or publisher.sha(published_manifest_path) != published_approval["manifest_sha256"]:
        raise ValueError("Published mathematical example lacks the selected approval")
    if not published_manifest.get("complete") or not all(published_manifest["checks"].values()) or published_manifest["heating_engineering_validation"] or published_manifest["implements_full_AUGMECON"] or published_manifest["calls_HeatPlanningModel_solve_lexicographic"]:
        raise ValueError("Published example has an unexpected validation scope")
    for name, expected in published_manifest["artifact_sha256"].items():
        path = (published_base / name).resolve()
        if not path.is_relative_to(published_base.resolve()) or not path.is_file() or publisher.sha(path) != expected:
            raise ValueError(f"Published example file changed: {name}")
    if len(published_manifest["artifact_sha256"]) != 106:
        raise ValueError("Published example file count changed")
    with (published_base / "points.csv").open(encoding="utf-8-sig", newline="") as handle:
        published_points = [row for row in csv.DictReader(handle) if row["case"].startswith("published_epsilon_")]
    if len(published_points) != 5:
        raise ValueError("Published epsilon sample count changed")
    truth_manifest_path = ROOT / "outputs/competition_20261001/C3_data_truth/manifest.json"
    truth_manifest = load(truth_manifest_path)
    if not truth_manifest.get("complete") or truth_manifest["baseline"]["ledger_entries"] != 8051 or truth_manifest["baseline"]["unsubstantiated_assumption_records"] != 4935 or truth_manifest["approved_true_project_experiment"]:
        raise ValueError("Source audit has an unexpected scope")
    for name in ["docs/execution_20261001/data_truth_audit/README.md", "outputs/competition_20261001/C3_data_truth/audit_summary.json"]:
        if publisher.sha(ROOT / name) != truth_manifest["files"][name]:
            raise ValueError("Selected source audit identity changed")
    portable_path = ROOT / "outputs/competition_20261001/C0/core_portable_verification_01.json"
    portable = load(portable_path)
    if not portable.get("complete") or portable["tests"]["passed"] != 55 or portable["tests"]["failed"] != 0 or portable["tests"]["deselected"] != 2 or not portable["published_example_verified"]:
        raise ValueError("Selected core-package portability record changed")
    program_receipt_path = ROOT / "outputs/competition_20261001/C0/program_execution_receipt_01.json"
    program_receipt = load(program_receipt_path)
    original_runs = [(manifest, profile, summary), (annual["manifest"], annual_profile, annual_summary),
        (replay_runs["season"]["manifest"], replay_runs["season"]["profile"], replay_runs["season"]["summary"])]
    if not program_receipt.get("complete") or program_receipt.get("curated_by") != "C0" or len(program_receipt["records"]) != 3:
        raise ValueError("Program execution extract has an unexpected scope")
    for row, (run_manifest, run_profile, run_summary) in zip(program_receipt["records"], original_runs):
        if row["engineering_data_approved"] or row["original_profile_sha256"] != run_manifest["files"]["model_profile.json"] or row["original_summary_sha256"] != run_manifest["files"]["summary.json"] or row["profile"] != run_profile:
            raise ValueError("Program execution extract differs from frozen original profile")
        if any(row["numerical_summary"][key] != run_summary[key] for key in ["solver_status", "mip_gap", "solver_elapsed_seconds"]):
            raise ValueError("Program execution extract differs from frozen solver record")
    annual_plot_spec = OUT / "markdown_plot_evidence_spec.json"
    annual_plot_spec.write_text(json.dumps({"selected_by": "C0", "selection": "C0 selected annual_01 and season_03 with formal review; no baseline or ablation selected", "plot_runs": [{"role": "optimized_annual", "label": "同一投资全年仿真", "manifest": {"path": str(annual["manifest_path"]), "sha256": annual["review"]["manifest_sha256"]}}]}, ensure_ascii=False, indent=2), encoding="utf-8")
    annual_plot_receipt_path = OUT / "plots/markdown_annual_v3/plot_manifest.json"
    if not annual_plot_receipt_path.exists():
        publisher.build_result_visuals(annual_plot_spec, "markdown_annual_v3")
    annual_plots = load(annual_plot_receipt_path)
    if annual_plots["spec_sha256"] != publisher.sha(annual_plot_spec):
        raise ValueError("Existing annual figures identify a different selected spec")
    for figure in annual_plots["figures"]:
        if publisher.sha(Path(figure["png"])) != figure["png_sha256"]:
            raise ValueError("Existing annual figure changed")
    with (base / "investments.csv").open(encoding="utf-8-sig", newline="") as stream:
        investments = list(csv.DictReader(stream))
    inputs = load(ROOT / "data/competition_20261001/manifest.json")
    building_path = ROOT / "data/competition_20261001/building_archetypes.csv"
    building_hash = next(r["sha256"] for r in inputs["inputs"] if r["file"].endswith("building_archetypes.csv"))
    if publisher.sha(building_path) != building_hash:
        raise ValueError("Building prototype table changed")
    with building_path.open(encoding="utf-8-sig", newline="") as stream:
        buildings = list(csv.DictReader(stream))
    sources = {r["id"]: r for r in load(ROOT / "docs/research_sources_20261001.json")}
    def refs(*ids):
        return "；".join(f"[{sources[i]['title']}]({sources[i]['url']})" for i in ids) + "。阅读范围与限制见研究来源登记。"
    def code(*paths):
        return "；".join(link(p) for p in paths) + "。"
    result_link = link(manifest_path, "选定规划manifest")
    result_note = f"结果身份：{result_link}，SHA256 `{evidence['manifest_sha256']}`；complete=true，formal_scope=representative。"
    module_note = "专项验证证明局部机制；不等同于完整年度、当地实测或比赛得分。"
    plots = load(OUT / "plots/representative_v1/plot_manifest.json")
    plot_by_id = {f["id"]: f for f in plots["figures"]}
    for f in plots["figures"]:
        if publisher.sha(Path(f["png"])) != f["png_sha256"]:
            raise ValueError("Existing figure changed")
    fig_links = {key: link(value["png"], value["title"]) for key, value in plot_by_id.items()}
    def finding(key, digits=2, factor=1):
        return f"{float(summary[key]) / factor:,.{digits}f}"
    blank_progress = lambda month: table(
        ["阶段事实", "实际内容", "日期/负责人", "可核验证据"],
        [[f"{month}阶段目标", "", "", ""], ["实际完成工作", "", "", ""],
         ["阶段成果与版本", "", "", ""], ["遇到问题及解决", "", "", ""]])
    progress_figure = "预留当月真实工作照片、版本记录或实验摘要；图片、日期和图注均留白。月份只作待填写页签，不预设发生过任何工作。"
    progress_speech = "讲稿：________。由团队填写真实时间、完成者和产出；未经确认前不口述这页的阶段故事。"
    progress_evidence = "无已确认的团队阶段事实。后续填写可追溯的会议记录、代码提交、原始数据或验收材料；不能用本次自动生成时间推断团队当月进展。"
    building_table = table(
        ["假设原型", "片区", "面积 m²", "H kW/K", "C kWh/K"],
        [[b["building_id"], b["district_id"], b["area_m2"], b["heat_loss_kw_per_c"], b["thermal_capacity_kwh_per_c"]] for b in buildings])
    investment_names = {
        "distributed_air_hp_b": "B区分布式空气源热泵",
        "district_a_emergency_eb": "A区备用电锅炉",
        "district_a_air_hp": "A区空气源热泵",
        "district_a_ground_hp": "A区地源热泵",
        "district_b_ground_hp": "B区地源热泵",
        "district_a_solar_thermal": "A区太阳热候选",
        "district_b_solar_thermal": "B区太阳热候选",
    }
    source_investments = [r for r in investments if r["category"] == "source_capacity" and r["option"] == "new" and r["unit"] == "kW"]
    if not source_investments:
        raise ValueError("Selected investment schema needs explicit source category mapping")
    capacity_table = table(["设备候选", "规划新建容量", "单位"],
        [[investment_names.get(r["asset"], r["asset"]), f"{float(r['value']):.2f}", r["unit"]] for r in source_investments])
    comp = summary["components_yuan_per_year"]
    cost_table = table(["成本项", "代表期加权年等效万元"], [
        ["年化资本费", f"{comp['annualized_capex']/10000:.2f}"],
        ["供热运行费", f"{comp['operating_cost']/10000:.2f}"],
        ["供热碳成本", f"{comp['carbon_cost']/10000:.2f}"],
        ["制冷运行费", f"{comp['cooling_operating_cost']/10000:.2f}"],
        ["制冷碳成本", f"{comp['cooling_carbon_cost']/10000:.2f}"],
        ["目标合计", finding("objective_yuan_per_year", factor=10000)],
    ])
    pages = [
        page(1, "选题原因：把供热规划落到真实服务约束", bullets([
            "拟研究问题：设备和管网投资如何共同满足不同建筑的逐时舒适服务？",
            "夏热冬冷地区同时存在供热与制冷，单看供热侧可能遗漏共享设备、电力和地源账本。",
            "选择S3新建高品质供热场景，以武汉公开气象和透明假设原型检验模型结构。",
            "团队自身选题经历、调研动机：________。"]),
            "建议画‘服务需求→源网荷储投资→逐时运行→独立审核’四步图；只用概念箭头。团队调研照片和出处留白。",
            "先说明拟解决的规划问题，再由团队补充真实选题经历。研究目标是使投资、实际供能和室温服务在同一模型内对应；成本或减排优势需要同服务对照支持。",
            "模型架构20分、场景验证15分。目标陈述不构成已经取得的收益。",
            code("docs/execution_20261001/评分逐项验收与算法优势.md", "data/competition_20261001/manifest.json")),
        page(2, "九位真实成员与实际分工", table(
            ["序号", "真实姓名", "实际分工", "本人完成成果/证据"],
            [[i, "", "", ""] for i in range(1, 10)]),
            "预留九位成员的真实姓名、照片和成果对应关系；所有姓名、照片、分工和身份留白。学校、团队、导师：________。",
            "讲稿：________。填写本人实际完成内容，分工可交叉。生成工具中的C0/C1等角色不能作为九位参赛成员。",
            "团队信息是答辩交付内容；不将成员人数或任务角色换算评分。",
            "当前没有已确认的九位真实成员信息。补充团队名单及本人工作记录后再写入。"),
        page(3, "7月实际进展", blank_progress("7月"), progress_figure, progress_speech,
            "若有实际成果，可关联建模、数据或方案调研评分；当前不作完成判定。", progress_evidence),
        page(4, "8月实际进展", blank_progress("8月"), progress_figure, progress_speech,
            "若有实际成果，可关联模型实现、专项验证或算法评分；当前不作完成判定。", progress_evidence),
        page(5, "10月实际进展", blank_progress("10月"), progress_figure, progress_speech,
            "若有实际成果，可关联实验、结果分析或交付评分；当前不作完成判定。", progress_evidence),
        page(6, "正式主要内容：任务、场景与验收边界", bullets([
            "主案例：S3夏热冬冷地区新建高品质供热；保留冷热服务、资源、设备上下限和舒适约束。",
            "输入服务年为2023-07-01至2024-07-01，8784h；项目假定供暖季为2023-11-15至2024-03-16，2928h；均为半开区间，分辨率1h。",
            "当前选定结果：34个代表日、816h，加权覆盖8784h；连续全年和供暖季仍待正式证据。"]),
            "用三列状态表：输入已准备／代表期规划已完成／连续季年待认证。禁止用‘加权8784h’画成全年连续通过的勾选。",
            "赛题要求完整逐时案例。现有规划提供候选投资和代表时段可行性；输入包含完整服务年，不代表已经完成对应年度求解和审核。",
            "动态仿真15分、场景验证15分；当前仅给出已完成的代表期范围。",
            code("data/competition_20261001/manifest.json", "data/competition_20261001/service_scope.json") + "\n\n" + result_note),
        page(7, "数据身份：公开气象、假设原型、模拟输出", table(
            ["类别", "实际身份", "可查证方式"], [
                ["气象", "NASA POWER公开格点气象产品；非本地站点实测", "原始缓存、请求/时钟记录、数据manifest"],
                ["建筑与占用", "H/C、面积、占用和得热为假设原型；未当地辨识", "building_archetypes.csv与配置"],
                ["价格与空间", "合成电价、原型坐标和候选造价情景", "候选YAML、空间包、继承链"],
                ["排放因子", "官方年度平均因子用于规划情景", "来源登记与采用值记录"],
                ["室温、调度和成本", "模型模拟结果；不能标成实测", "选定结果文件、哈希和审计"],
            ]),
            "建议用五行来源表或颜色标签，不使用‘真实数据’统一覆盖所有输入。图注写出具体数据身份。",
            "真实性在于来源、假设和变换可查证。公开气象产品有真实来源，但不能由此推导建筑参数、价格、空间和模拟室温都来自实测。",
            "场景验证15分、代码质量10分、分析深度15分。参数来源透明不等于已验证工程精度。",
            refs("D01", "D05") + "\n\n" + code("data/competition_20261001/manifest.json", "data/competition_20261001/building_archetypes.csv", "data/competition_spatial/s3_spatial.yaml")),
        page(8, "模型架构：源网荷储与冷热共同决策", bullets([
            "投资层：热源容量、储热容量、候选管径和电网接入。",
            "运行层：热源出力、网络输热、实际楼宇供热/制冷、储热充放及电力。",
            "服务层：逐楼室温、占用舒适、冷热互斥和明确初末态。",
            "节点供给＋网络净输入＋储热净输出＝实际楼宇服务＋独立固定服务。"]),
            "建议画系统结构图，分别标记热、电、状态连接；静态输热箭头不标动态水力或在途热量。",
            "容量选择与逐时服务联立：满足舒适所需的实际冷热供给直接进入区域平衡。热源、网络和储热不能与楼宇状态各算各的。",
            "模型架构20分；结构机制已有，完整年季闭环仍待连续运行与独立审核。",
            code("src/heatplan/model.py", "src/heatplan/building_thermal.py", "src/heatplan/spatial_quality.py")),
        page(9, "负荷模型：实际供能驱动一阶RC室温", bullets([
            "连续模型：C·dT/dt＝H(Tout−T)＋Qh−Qc＋G。H为散热系数，C为有效热容；G为内部与太阳得热。",
            "区间内常值假设下：a＝exp(−H·Δt/C)，b＝(1−a)/H。",
            "精确离散：Tend＝a·Tstart＋b·(H·Tout＋Qh−Qc＋G)。预计算a、b后仍为线性约束。",
            "舒适按各楼逐时边界检查；冷热采用exclusive模式。模型只含显热和区间末状态。"]),
            "建议放RC示意与公式；解析降温/断源恢复曲线只可使用已登记专项结果，不能画成当地实测验证。",
            "旧的虚拟缺供或移时指标不能替代室温。这里由实际供热和制冷改变室温，供给不足会使约束不可行。精确离散是对所设一阶模型的数值处理，不意味着建筑物理已被完整描述。",
            "模型架构20分、动态仿真15分；解析与反例是程序/数学验证。",
            refs("PH01", "PH02") + "\n\n" + code("src/heatplan/building_thermal.py", "docs/execution_20261001/C2_handoff.md")),
        page(10, "多尺度耦合：保留每栋楼的差异", building_table + "\n\n" + bullets([
            "4栋、3类合成楼宇；总面积44000m²。各楼拥有独立H/C、得热和舒适边界。",
            "逐楼实际送达量汇总到节点、片区和系统；上层共享设备、电网和投资约束。",
            "完整跨层受控对照待运行：改变某楼H/C或舒适边界，观察上层调度和投资是否变化。"]),
            "建议画楼宇→节点→片区→系统树；同一实际送达量只汇总一次，各层总量不能相加重复计量。",
            "不同热容和散热系数形成不同室温响应。唯一归属和逐层汇总提供耦合接口；证明‘多尺度最优’还需完整对照，不能只凭结构图。",
            "多尺度10分；唯一归属和汇总守恒有专项证据，完整跨层对照待验收。",
            link(building_path) + "；" + code("src/heatplan/spatial_quality.py", "docs/execution_20261001/C3_handoff.md")),
        page(11, "空间约束：候选接入改变可行域与投资", bullets([
            "原型路线长度、接入和跨系统规则筛选候选连接。",
            "管道投资：Ipipe＝固定费＋单位长度费×路线长度，进入规划成本。",
            "已有局部反例：600m允许；上限改为599m时，同服务连接方案不可行。",
            "坐标和造价是原型情景，当前未采用真实GIS、地下管线或工程询价。"]),
            "建议采用原型米制坐标示意和600/599m对照；图题明确‘原型空间包’，避免真实城市底图造成实测接入印象。",
            "空间不只用于展示，它筛除连接并改变投资成本。反例证明约束进入决策；正式工程布局还需要真实线路、接入条件和报价。",
            "模型架构20分、场景验证15分、多尺度10分；反例不等于城市工程验证。",
            code("src/heatplan/spatial_quality.py", "data/competition_spatial/s3_spatial.yaml", "docs/execution_20261001/C3_handoff.md")),
        page(12, "供热品质：温级准入与提温代价", bullets([
            "已有品质反例：50℃直接热源不能无功满足75℃服务。",
            "低温资源经热泵提温：W＝Qh/COP，Qenv＝Qh−W；电耗和环境取热同时入账。",
            "COP采用当前简化情景函数；系统性能要核对压缩机、泵、辅机和备用电加热的完整边界。",
            "已实现温级准入和转换账本；跨节点串联提温及全系统火用优化尚未实现。"]),
            "建议画低温热＋电→高温热的账本示意；不展示接近100%的原始火用字段作为全系统效率。",
            "不能把所有热量视为可无成本替换。模型用温级限制和电耗说明品质代价，现有账本的边界仍小于全系统火用评价边界。",
            "模型架构20分、场景验证15分；品质专项约束已有。",
            refs("PH07") + "\n\n" + code("src/heatplan/spatial_quality.py", "docs/execution_20261001/C3_handoff.md")),
        page(13, "求解结构：稀疏MILP与真实计算记录", bullets([
            "min cᵀx，约束l≤Ax≤u，离散投资与冷热互斥变量为整数。连续容量和逐时运行联立。",
            "行字典构造稀疏矩阵；模型记录状态、gap、对偶界、矩阵规模和分段耗时。",
            f"选定代表期：{profile['variables']:,}变量，{profile['constraints']:,}约束，{profile['matrix_nonzeros']:,}非零系数。",
            f"求解与提取{profile['solve_and_extract_seconds']:.2f}s，端到端{profile['wall_seconds']:.2f}s；实际相对MIP gap为{summary['mip_gap']*100:.4f}%。"]),
            "已有真实图可选：" + fig_links["optimized_representative_timing"] + "。保留原始分段名称；不外推全年耗时或与其他实现比较加速。",
            "这组耗时属于816h代表期和记录的本机环境。HiGHS报告成功终止，同时披露非零gap；没有对照时不能称速度提高多少。矩阵内存代理也不等于进程峰值内存。",
            "多目标与收敛15分、代码质量10分；这里证明单次代表期求解记录，不证明端点前沿或全年效率。",
            refs("ENV01", "ENV02") + "\n\n" + link(base / "model_profile.json") + "；" + link(base / "summary.json") + "\n\n" + result_note),
        page(14, "多目标算法：词典序端点与ε采样", bullets([
            "统一Etotal为冷热服务及共享电力的总运营排放；同一表达式用于碳目标和ε约束。",
            "成本端点先最小化成本，再在容差内最小化排放；低碳端点顺序相反。",
            "在端点之间设置ε，逐点求解；按容差去重，删除被支配的可行点。",
            "折中方案采用归一化理想点距离规则；有限采样不是完整连续Pareto前沿，也不称严格曲率膝点。"]),
            "当前仅放算法流程或伪代码。尚未选定正式端点与前沿结果，不画假定散点或节碳曲线。",
            "算法顺序：计算两类端点→检查端点状态和预算→建立ε网格→求解并保留状态/gap→去重和支配筛选→给出选点规则。文献说明方法依据，不能自动证明本轮所有端点已经完成。",
            "多目标协同15分；方法代码已有，本轮正式端点、边界变化与前沿待验收。",
            refs("OPT04", "OPT05") + "\n\n" + code("src/heatplan/model.py", "src/heatplan/experiments.py")),
        page(15, "规划反馈与一次冻结后的连续回放", bullets([
            "代表时段形成候选投资；若回放失败，按原因补充时段或必要约束，再规划。",
            "候选投资只冻结一次；连续供暖季和全年都读取同一投资身份。",
            "完整小时内连续推进室温和储热状态，按明确初末态与年度地源账本审核。",
            "runner分阶段执行；外层失败重规划由总控触发，未声称已自动实现全部反馈算法。"]),
            "建议画含‘失败原因→补时段/约束→重新规划’的流程图。规划节点标已完成；正式季年节点标待认证。",
            "循环代表日适合形成容量候选，但无法认证夏充冬放。每次投资改变都重新冻结和验证。冬季保留地源提取/回灌记录，年度比值须在全年边界判定。",
            "动态仿真15分、场景验证15分、代码质量10分。当前仅代表期正式结果已被选定。",
            refs("OPT06") + "\n\n" + code("src/heatplan/competition_runner.py", "src/heatplan/chronology.py", "src/heatplan/heating_season.py")),
        page(16, "验证层级：输入、专项、代表期与连续季年", table(
            ["证据层级", "当前结论", "不能推出"], [
                ["完整时间输入", "服务年/季数据已准备并QA", "solver已认证全年"],
                ["专项解析和负例", "RC、空间、品质和层级局部机制", "当地工程精度/完整年服务"],
                ["代表期数值审核", "选定解约束与目标复算记录", "全局精确最优/gap为零"],
                ["代表期楼宇独立审核", "C6 passed=true，3264楼宇小时行", "全系统源网/投资/最优性认证"],
                ["连续季/年和同服务基准", "尚未正式绑定", "收益和减排优势"],
            ]),
            "建议画分层证据阶梯，不汇总17/63/46专项数量为全项目测试数。C6页角标‘annual_certification=false’。",
            "代表期C6核对原始输入身份、时间与归属、RC、舒适、互斥、实际供给及相关汇总。它的scope是 supplied_trusted_frame_only，dispatch范围只到楼宇及可选节点别名；其通过不会扩大为设备网络或全年证书。",
            "模型架构20分、动态仿真15分、代码质量10分；各层证据独立列示。",
            link(base / "c6_building_audit.json") + "；" + link(base / "independent_rc_audit.json") + "；" + link(base / "summary.json") + "\n\n" + module_note),
        page(17, "已选定实验：34个代表日的候选投资", capacity_table + "\n\n" + bullets([
            "完整投资表还含太阳热、PCM、候选管道和电网；零选项也保留，容量与二元值不能跨单位相加。",
            "该解是代表期候选规划，尚未形成连续全年正式推荐。",
            "规划目标：最小化总成本；实际gap为0.0344%，requested_mip_gap为0.5%。"]),
            "已有真实图可选：" + fig_links["optimized_representative_investment"] + "。零容量候选必须保留。可补完整投资CSV表，不绘制未经回放的全年调度。",
            "先展示模型实际选了哪些设备，再解释选择与当前情景边界有关。PCM和管道零投资只说明该解未选，不证明它们对其他场景没有价值。全年是否可行和是否优于基准仍待检验。",
            "场景验证15分、结果分析15分；真实代表期选择已经可核验，连续季年验收尚缺。",
            link(base / "investments.csv") + "；" + link(base / "fixed_plan.json") + "\n\n" + result_note),
        page(18, "经济与碳核算：先说明口径再比较", cost_table + "\n\n" + bullets([
            f"冷热总运营排放：{finding('total_service_emissions_kg_per_year', factor=1000)}tCO₂（代表期加权年等效）。",
            f"实际楼宇供热：{finding('building_heat_delivered_kwh_per_year', factor=1000)}MWh；制冷：{finding('building_cooling_delivered_kwh_per_year', factor=1000)}MWh（均为代表期加权年等效）。",
            "目标成本包含已启用的成本项；年化资本费不同于建设期总投资，也不等于当年现金支出。",
            "同服务基线、实际收入与收费情景尚未绑定，不报告节省率、减排率、IRR或回收期。"]),
            "已有真实图可选：" + fig_links["optimized_representative_cost_components"] + "。图题保留‘代表期’与‘年等效’；正式基线未到，不添加对比柱。",
            "年等效来自代表期权重，不是全年连续结果。冻结回放若目标已含资本费，不再加一次。后续先比较建设投资、年度现金费、排放和盈亏平衡收费；NPV与回收期只在明确收入、贴现率和服务边界后计算。",
            "分析深度15分；当前可解释成本构成和核算边界，收益优势待同服务完整对照。",
            link(base / "summary.json") + "；" + code("src/heatplan/accounting.py", "src/heatplan/finance.py") + "\n\n" + result_note),
        page(19, "实验图表与受控对照：缺证据的图先留空", table(
            ["拟展示", "已有或待补", "必须标明的边界"], [
                ["投资容量、目标构成、分段耗时", "现有真实代表期图", "代表期/年等效/本机本次"],
                ["负荷持续曲线、冷热日历", "完整连续轨迹待选定", "实际送达量、每小时、缺值不补0"],
                ["极端连续周室温与调度/SOC", "正式季/年轨迹待选定", "各楼舒适带、小时末、初末态"],
                ["基准与优化成本碳", "同服务基准待验收", "同天气/建筑/冷热/占用/初末态"],
                ["H/C、空间、碳帽受控变化", "正式对照待选定", "一次只改变声明因素"],
                ["ε非支配样本", "正式端点与采样待选定", "采样点，不宣称完整前沿"],
            ]),
            "本页采用图表清单。未完成的实验区域留空，不放随机曲线、概念收益百分比或装饰性能图。现有图源与CSV登记在plot_manifest.json。",
            "每张图回答一个问题并保留数据、选择规则和来源身份。对照必须保持服务边界一致；不同舒适、天气或冷量下的差额无法归因于算法。",
            "分析深度15分、多目标15分、多尺度10分；待补图不是已完成评分证据。",
            link(OUT / "plots/representative_v1/plot_manifest.json") + "；" + link(OUT / "representative_evidence_spec.json") + "。现有PNG版式尚未逐图视觉验收，当前Markdown只登记可用来源。"),
        page(20, "逐项评分响应：亮点、证据与缺口", table(
            ["题面子项", "权重", "拟展示亮点", "当前完成边界"], [
                ["模型架构", 20, "实际冷热＋逐楼RC＋源网储", "机制/代表期已有；完整闭环待季年"],
                ["动态仿真", 15, "1h时钟与状态连续回放", "完整输入已有；连续季年待认证"],
                ["场景验证", 15, "S3舒适/容量/资源/地源账本", "代表期通过；完整主案例待验收"],
                ["多目标协同", 15, "共同碳边界、词典序与ε筛选", "代码已有；正式端点前沿待验收"],
                ["多尺度", 10, "逐楼状态、唯一归属与层级汇总", "专项已有；跨层受控对照待运行"],
                ["可视化与分析", 15, "真实图源、同服务成本碳", "代表期图源已有；收益比较待验收"],
                ["代码与可移植性", 10, "模块、配置、哈希、独立复算", "专项工程已有；最终包复现待验收"],
            ]),
            "建议使用七行完成状态矩阵；不画预测得分雷达图。原文逐条引用在评分登记文件，本页只作摘要。",
            "每个亮点要能定位机制、输入、验证和结果。未完成项明确留缺口，原有S1/S2配置或短窗测试不能替代本轮完整案例。冷源1.10超配参数当前尚无硬约束实现，不列为已实现亮点。",
            "权重合计100分；完成状态不推算比赛分数。",
            link(ROOT / "docs/execution_20261001/评分逐项验收与算法优势.md") + "。题面CP-202611第4–5页原文见该登记。"),
        page(21, "企业联合精进", table(
            ["待填写的真实合作信息", "内容", "日期/人员", "证据"], [
                ["企业全称与合作关系", "", "", ""],
                ["企业提出的实际需求/问题", "", "", ""],
                ["双方开展的具体工作", "", "", ""],
                ["形成的修改与验证结果", "", "", ""],
            ]),
            "企业名称、LOGO、交流照片、共同修改前后对照、图注与授权状态均留白；只使用团队可提供证据的材料。",
            "讲稿：________。本页没有已确认企业合作事实；由团队填写后再描述联合精进过程，不以拟合作方向替代已发生合作。",
            "可作为应用背景和约束依据；没有对应事实与成果时不列为已完成评分证据。",
            "当前无已确认企业名称、合作内容或记录。补充会议纪要、需求文件、版本差异及对应验证资料。"),
        page(22, "企业认可与鉴定", table(
            ["鉴定信息", "实际内容"], [
                ["企业/机构全称", ""], ["认可或鉴定的具体对象", ""],
                ["原文与结论范围", ""], ["日期、签署者和身份", ""],
                ["材料编号、附件与使用授权", ""],
            ]),
            "鉴定文件、签字盖章、照片、引用原文和图注全部留白。材料存在后按原文范围引用，不自行设计证明或印章。",
            "讲稿：________。当前没有已确认认可或鉴定事实。企业意见即使补充，也应与年度物理认证、比赛评分和实际节能测量分开。",
            "可作为外部评价材料；不能替代模型守恒、季年轨迹或同服务收益证据。",
            "当前无已确认的企业认可/鉴定材料。需要团队提供真实文件与可引用范围后再填写。"),
        page(23, "不足：模型假设与当前未完成证据", bullets([
            "未进行当地H/C、占用及得热辨识；气象为公开格点产品，空间与价格为情景原型。",
            "1R1C只含单显热状态和小时末舒适；不含湿度、潜热、多节点围护及小时内温控。",
            "静态kW网络未含动态水力、输热迟滞、真实GIS或跨节点串联提温；全系统火用优化未实现。",
            "年度地源能量比不是多年地下温度场恢复；循环代表日不能认证夏充冬放。",
            "当前正式证据限于代表期；连续季年、同服务基准、前沿及敏感性仍待验收。"]),
            "建议采用‘假设限制／模型未实现／证据待完成’三栏表，不把未实现功能写成已部署后仅需优化。",
            "数学残差通过能说明给定模型的计算一致性，不能说明当地工程误差。暂时能讨论机制与代表期选择；正式工程推荐和经济环境优势需要补足连续证据与实测依据。",
            "关联全部评分项；主动说明边界，不预测扣分或承诺剩余结果。",
            refs("PH03", "PH04", "PH05", "PH06", "PH07") + "\n\n" + link(base / "c6_building_audit.json") + "；" + link(ROOT / "docs/execution_20261001/评分逐项验收与算法优势.md")),
        page(24, "展望：按证据成熟度推进", bullets([
            "近期：同一投资的连续供暖季和全年回放，补齐状态、服务和地源账本审核。",
            "随后：同服务基准、正式多目标端点与采样、H/C及边界条件受控对照。",
            "具备条件后：以真实室温、热表、设备与GIS资料开展辨识、留出验证和工程成本核对。",
            "长期研究方向：动态热网、多年地下状态、完整系统性能与火用边界。",
            "团队后续计划、负责人和完成日期：________。"]),
            "建议画证据依赖路线图，不填未经团队确认的承诺日期、成员或企业。拟研究功能标‘计划’，不标‘已完成’。",
            "先补足连续结果和有效比较，再决定是否增加模型复杂度。未来扩展要有数据和验证对象支持；团队自身时间安排留待确认。",
            "动态15分、场景15分、多目标15分、多尺度10分、分析15分、工程10分的待验收任务。",
            link(ROOT / "docs/execution_20261001/评分逐项验收与算法优势.md") + "；" + code("src/heatplan/competition_runner.py", "src/heatplan/experiments.py")),
    ]
    # C0 subsequently selected the completed continuous annual and season runs.
    # Replace the affected pages before writing; preserve the human fact blanks.
    def amend(number, **values):
        pages[number - 1].update(values)
    annual_figures = {f["id"]: link(f["png"], f["title"]) for f in annual_plots["figures"]}
    amend(6, body=bullets([
        "当前验证原型：S3夏热冬冷地区新建高品质供热结构，冷热服务、设备与资源边界共同约束；最终主案例与工程结果尚未补齐来源依据。",
        "输入服务年2023-07-01至2024-07-01，8784h；项目假定供暖季2023-11-15至2024-03-16，2928h；半开区间、1h分辨率。",
        "34个代表日形成候选投资；同一投资的8784h全年和2928h供暖季已获C0正式复核通过。",
        "仿真只作为公开气象驱动假设原型的程序/数学验证；依据不足，不能充当真实工程实证。基准、代表边界消融和正式多目标样本尚未选定。"]),
        figures="建议画‘代表期规划→冻结投资→连续全年/季→同服务对照’流程。前三阶段有对应结果和复核；基准对照标待完成。",
        speech="完整逐时证据现在覆盖同一投资的服务年与供暖季。该结论成立于给定模型、价格和原型条件；当地实测精度、企业收益和多年地温稳定不在当前认证范围。",
        rating="动态仿真15分、场景验证15分：同一投资1h完整年/季有程序/数学验证证据；真实数据要求未全部满足，不认定工程案例或比赛得分。",
        evidence=code("data/competition_20261001/manifest.json", "data/competition_20261001/service_scope.json") + "\n\n" + annual_evidence_note)
    amend(7, title="真实数据与缺口：有出处的项才进入数据页", body=table(
        ["类别", "可核验来源", "本页允许表述"], [
            ["气象", "三城市2020–2024年15份NASA产品与新官方响应全量核对", "131544小时、四字段526176值全一致；不是地面站精度验证"],
            ["排放因子", "官方年度平均因子与采用值记录", "有来源的年度情景系数；不是逐时边际排放"],
            ["建筑面积与H/C", "", "真实值与精确依据留缺"],
            ["得热与占用", "", "真实值与精确依据留缺"],
            ["设备/价格/空间/造价", "", "逐字段审计后补有依据项"],
            ["当地室温与热表观测", "", "独立实测验证数据留缺"],
            ["德国N2外部试验房序列", "Fraunhofer/Annex71，DOI10.24406/fordatis/76.2", "发布方处理后的实测及派生聚合；有限方法诊断，非武汉校准"],
        ]) + "\n\n" + bullets([
            "四个产品字段：T2M ℃、RH2M %、WS10M m/s、ALLSKY_SFC_SW_DWN Wh/m²。旧辐射列名含kWh的误名不能作为单位依据。",
            "本次响应API v2.10.2，旧CSV哈希前后不变。2023原记录只记复用文件；本次按同城市2024坐标/字段/时钟重建2023新查询，不倒填旧API版本或原采集日期。",
            "原型设定和模型输出另列为程序/数学验证，不填入真实观测栏。",
            "注明假设不能代替来源依据，也不能据此宣称数据真实性条件已经全部满足。",
            "全量来源快照已审完：8051条记录中4935条缺依据；含重复字段/引用，不是独立参数数。真实工程主案例仍未准入。"]),
        figures="用来源/缺口表；仅对有精确出处的项目填值，其余真实数据格留空。不要以仿真图或假设参数填补观测缺口。",
        speech="这次复核在三城市15份年度产品四字段上逐值一致，保留了原CSV和新响应，能说明气象产品来源一致。它不验证地面站精度，也不认证生成负荷、建筑或价格。2023的原请求缺口如实登记；不将新的校验查询倒写成旧采集记录。",
        rating="场景验证15分、工程10分、分析15分：来源审计完成，真实工程数据与当地观测验证仍缺。",
        evidence=refs("D01", "D05") + "\n\n" + code("outputs/competition_20261001/C0_weather_02/verification_report.json", "outputs/competition_20261001/C0_weather_02/manifest.json", "data/verified_weather_20261002/request_provenance_addendum.json", "docs/execution_20261001/data_truth_audit/README.md", "outputs/competition_20261001/C3_data_truth/manifest.json"))
    amend(10, body=table(["层级", "保留的变量/约束", "耦合方式"], [
        ["楼宇", "各楼独立Hi、Ci、室温及舒适约束", "实际冷热送达量接入RC状态"],
        ["节点/片区", "唯一归属和逐层送达量汇总", "共享设备和热量平衡"],
        ["系统", "电力、资源、设备投资", "限制各片区共同可用能力"],
    ]) + "\n\n" + bullets([
        "实际面积与H/C等值尚无充分依据，本页只呈现异质状态与层级耦合结构。",
        "逐楼状态和实际送达量汇总到节点、片区和系统；上层共享设备、电网和投资约束。",
        "待补H/C改变影响上层调度与投资的受控对照；未证实任何当地实际效果。"]),
        speech="每栋楼保留独立热损失和热容，实际送达量再向上汇总。结构避免把异质楼宇当成一个平均建筑；它是否改善真实调度仍需有依据的参数和受控对照。")
    amend(13, title="求解结构：稀疏MILP与程序验证计算记录", body=bullets([
        "以下规模、耗时和gap仅来自假设原型的实际求解，不作为实测工程性能。",
        "min cᵀx，l≤Ax≤u；投资与运行联立。采用稀疏矩阵，记录状态、gap、界和分段耗时。",
        f"代表期816h：{profile['variables']:,}变量，{profile['constraints']:,}约束，{profile['matrix_nonzeros']:,}非零系数；端到端{profile['wall_seconds']:.2f}s。",
        f"固定投资全年8784h：{annual_profile['variables']:,}变量，{annual_profile['constraints']:,}约束，{annual_profile['matrix_nonzeros']:,}非零系数。",
        f"全年solver_elapsed_seconds＝{annual_summary['solver_elapsed_seconds']:.3f}s；求解与提取＝{annual_profile['solve_and_extract_seconds']:.3f}s；profile端到端＝{annual_profile['wall_seconds']:.3f}s；实际gap＝{annual_summary['mip_gap']*100:.5f}%。"]),
        speech="代表期包含投资决策，全年是在固定投资下回放，两次问题不同，不能据耗时直接称算法加速。全部数字来自实际字段；solver、求解与提取、端到端的范围不同。原始记录有小幅计时差异时保留字段身份，不拼接多处数字。",
        rating="多目标与收敛15分、工程10分：正式单目标代表期与连续年有状态/gap/规模/耗时记录；多目标端点与效率对照仍待证据。",
        evidence=refs("ENV01", "ENV02") + "\n\n" + link(base / "model_profile.json") + "；" + link(annual["manifest_path"].parent / "model_profile.json") + "；" + link(annual["manifest_path"].parent / "summary.json") + "\n\n" + annual_evidence_note)
    amend(15, body=bullets([
        "代表日生成候选；一次冻结投资后，全年与供暖季读取相同投资SHA256。",
        "全年8784h、供暖季2928h连续1h，保留楼宇状态和明确初末态，已获正式复核通过。",
        "全年按每个地源设备复算提取/回灌账本；供暖季比值不强制为年度阈值，保留原始能量。",
        "代表期每天恢复20℃带来近似偏差；边界消融尚未选定，不能宣称代表日近似已经高精度。",
        "runner分阶段执行，外层失败重规划由总控触发。"]),
        figures="建议画投资身份贯穿代表期、全年、供暖季；连续年/季标复核通过，代表日边界消融标待完成。",
        speech="代表期加权冷量高于连续年，已知独立代表日恢复20℃会影响动态需求。两者不是同服务基准，不能把差额解释为节能。年度地源账本通过也不等于多年地下温度场稳定。",
        rating="动态15分、场景15分、工程10分：同一投资完整年/季及18项复核已有；代表近似与外层反馈效果仍需正式对照。",
        evidence=refs("OPT06") + "\n\n" + annual_evidence_note)
    amend(16, body=table(["证据层级", "当前结论", "不能推出"], [
        ["输入和专项", "时钟/单位、RC解析、空间/品质负例", "当地实测精度"],
        ["代表期规划", "完整结果与数值/楼宇审核", "代表日等价连续年"],
        ["固定投资连续年/季", "8784h/2928h，C0每份18项通过", "企业鉴定/全年实测节能"],
        ["重新串行楼宇复算", "时段末舒适和实际冷热服务通过", "独立重建全部源网方程"],
        ["同服务基准与消融", "未选定正式结果", "成本/碳收益或近似精度优势"],
    ]), figures="建议分层显示两个C0复核表各18项通过；C6文件自身annual_certification=false如实保留，完整年范围由C0连续性和账本复核提供，不篡改原始标签。",
        speech="C0增加了同投资、同服务和代码身份、连续小时、单位权重、每源地源账本、资本只计一次、成本分量和冷热排放核对。全矩阵可行性依赖保存的数值审计，复核未独立重建所有设备/网络方程；楼宇重新复算仍按声明范围解释。",
        rating="模型20分、动态15分、场景15分、工程10分；完整运行已有，工程实测和额外对照边界继续保留。",
        evidence=annual_evidence_note + "\n\n" + link(annual["manifest_path"].parent / "c6_building_audit.json") + "；" + link(replay_runs["season"]["manifest_path"].parent / "c6_building_audit.json"))
    amend(17, title="程序验证求解结果：代表期候选设备决策", body="公开气象驱动的假设原型程序/数学验证；建筑、价格和空间的精确依据仍待来源审计。以下是模型候选决策，不是真实工程设备或采购方案。\n\n" + capacity_table + "\n\n" + bullets([
        "完整投资表保留太阳热、PCM、候选管道及电网的零/非零选择，不能跨单位相加。",
        "该代表期投资已在同一身份下完成连续全年和供暖季回放并通过正式复核。",
        "代表期规划目标为总成本，实际gap为0.0344%；全年固定投资回放gap为0.13437%。"]),
        speech="规划实际选择的设备已通过同投资连续回放。零投资不表示某项技术普遍无价值。当前没有正式同服务基准对照，不将可行性表述为投资节省或最优工程推荐。",
        figures="现有代表期候选容量图尚需添加最新来源边界，可先采用本页参数表。图旁须标：气象为NASA POWER；建筑/价格等尚未溯源，仅程序验证，非真实设备或实测。零投资候选必须保留。",
        rating="场景15分、动态15分、分析15分：候选设备决策与同投资连续年/季的程序证据已有；真实工程案例与收益比较仍待依据。",
        evidence=link(base / "investments.csv") + "；" + link(base / "fixed_plan.json") + "\n\n" + annual_evidence_note)
    energy_om = annual_comp["operating_cost"] + annual_comp["cooling_operating_cost"] + annual_comp.get("fixed_om_cost", 0)
    carbon_price_cost = annual_comp["carbon_cost"] + annual_comp["cooling_carbon_cost"]
    annual_capital = annual_comp["annualized_capex"]
    objective_without_carbon = annual_summary["objective_yuan_per_year"] - carbon_price_cost
    amend(18, title="全年程序验证的费用与碳账本", body="以下均为NASA POWER公开气象驱动假设原型的模型结果，建筑/价格尚未充分溯源；仅程序验证，非企业实际费用、实测碳排放或收益。\n\n" + table(
        ["口径", "连续服务年元/年"], [
            ["能源等运营费", f"{energy_om:,.4f}"], ["年化资本费（已含在目标中）", f"{annual_capital:,.4f}"],
            ["假设碳价成本（0.12元/kg）", f"{carbon_price_cost:,.4f}"],
            ["模型总费用", f"{annual_summary['objective_yuan_per_year']:,.4f}"],
            ["剔除情景碳价的费用口径", f"{objective_without_carbon:,.4f}"],
        ]) + "\n\n" + bullets([
            f"总运营排放{annual_summary['total_service_emissions_kg_per_year']:,.4f}kgCO₂；实际楼宇供热{annual_summary['building_heat_delivered_kwh_per_year']:,.4f}kWh、制冷{annual_summary['building_cooling_delivered_kwh_per_year']:,.4f}kWh。",
            "这里是8784h连续服务年的模型结果，区别于代表期加权年等效。",
            "runner的cash字段含假设碳价；不称企业实际支付现金。年化资本费只计一次，与建设投资和当年现金流分开。",
            "同服务基线和收入情景未绑定，不报告节省/减排率、IRR或回收期。"]),
        figures="建议使用本页精确费用表；可按三项真实模型费用画柱图。尚无正式基准，不添加比较柱；原代表期成本图只可另标代表期，不替代本页全年费用。",
        speech="先明示费用口径：能源等运营、年化资本、情景碳成本。0.12元/kg是模型假设，不能表示企业实际碳支付。后续可做同服务成本碳与盈亏平衡收费比较；投资回报必须有明确收入、贴现率和建设投资依据。",
        rating="分析深度15分：连续全年费用/排放和资本一次核算已有；收益优势待有效基准。",
        evidence=link(annual["manifest_path"].parent / "summary.json") + "；" + link(annual["manifest_path"].parent / "corrected_cost.json") + "\n\n" + annual_evidence_note)
    amend(19, title="连续全年程序验证的求解结果图", body="所有图为假设原型求解结果，不是实测；气象来自NASA POWER，建筑/价格等依据尚待全量来源审计。\n\n" + table(
        ["拟展示", "当前来源", "解读边界"], [
            ["全年实际冷热持续曲线/日历", "8784h正式轨迹，已生成图源", "实际送达，独立排序/北京时间"],
            ["全年最冷/最热连续周", "滚动168h平均室外温度选窗", "各楼舒适带、时段末温度"],
            ["源出力/冷出力/SOC", "同窗正式调度", "源端与送达量分开；零储热如实"],
            ["楼宇/节点/片区/系统", "逐层实际送达汇总", "同一服务不多层相加"],
            ["基准/优化、代表边界消融", "尚未选定", "同服务/一次改变一个因素"],
            ["多目标非支配样本", "尚未选定", "采样子集，不称完整前沿"],
        ]), figures=bullets([annual_figures[k] + "——气象：NASA POWER；建筑/价格等尚未溯源，仅假设原型程序验证，非实测。" for k in ["optimized_annual_duration", "optimized_annual_calendar", "optimized_annual_cold_temperature", "optimized_annual_hot_temperature", "optimized_annual_cold_dispatch", "optimized_annual_hot_dispatch", "optimized_annual_hierarchy"]]),
        speech="所有曲线来自已选定的连续全年结果和完整小时。室温是模拟状态，横轴采用区间末；出力是区间平均，横轴采用区间开始。全年最冷/最热周只按室外滚动平均选择，不自称峰值负荷周。未完成的基准和消融不放虚构曲线。",
        rating="可视化与分析15分、多尺度10分：正式全年图源已有；基准、敏感性和多目标样本待验收。",
        evidence=link(annual_plot_receipt_path) + "；" + link(annual_plot_spec) + "。逐图视觉验收记录见证据登记。")
    amend(20, body=table(["题面子项", "权重", "可证亮点", "当前完成边界"], [
        ["模型架构", 20, "实际冷热＋逐楼RC＋源网储", "年/季结果与审计；全矩阵独立重建未做"],
        ["动态仿真", 15, "同投资1h连续年/季", "8784h/2928h正式复核通过"],
        ["场景验证", 15, "S3舒适/设备/地源账本", "原型完整仿真通过；非当地实证"],
        ["多目标协同", 15, "统一碳边界、词典序/ε筛选", "公开数学算例5点复现；供热工程前沿未准入"],
        ["多尺度", 10, "逐楼状态、唯一归属/汇总", "全年聚合已有；H/C跨层受控对照待跑"],
        ["可视化与分析", 15, "连续求解图、模型成本碳边界", "程序验证年图已生成；真实案例/收益待依据"],
        ["代码与可移植性", 10, "配置模块、哈希、复算", "628全回归；精选包55项便携检查，范围分别说明"],
    ]))
    pages[19]["body"] += "\n\n" + table(["工程事实", "证据"], [
        ["共享核心11.12.0rc1", "CHANGELOG.md保留变更与边界"],
        ["最新整合全回归628项通过", "regression_004.xml，实际记录67.551s"],
        ["先前发布准入专项8项通过", "release_gate_003.xml，单列，不与全回归相加"],
        ["精选核心包便携检查55项通过", "8.02s；2项原整树身份测试因范围不符排除，全量628结论不变"],
        ["历史C4源码身份", "仍按运行manifest代码哈希，不改历史epoch"],
    ])
    pages[19]["evidence"] += "\n\n" + code("CHANGELOG.md", "outputs/competition_20261001/C0/regression_004.xml", "outputs/competition_20261001/C0/release_gate_003.xml", "outputs/competition_20261001/C0/core_portable_verification_01.json")
    amend(14, body=bullets([
        "供热代码统一冷热总运营排放表达式；成本/碳词典序端点与ε采样按容差去重、去支配，工程前沿尚未准入。",
        "已批准的公开数学补证：Mavrotas作者报告第4.1节，max f1＝x1，max f2＝3x1＋4x2；5x1＋4x2≤200，x1≤20，x2≤40。非负域依据原图解释。",
        "用项目LinearMILP数值层和独立两阶段词典序包装实际求解；不等同HeatPlanningModel端点实现或完整AUGMECON复现。",
    ]) + "\n\n" + table(["ε", "x1", "x2", "f1", "f2"], [[f"{float(row[k]):g}" for k in ["epsilon", "x1", "x2", "f1", "f2"]] for row in published_points]) + "\n\n" + bullets([
        "5点唯一且互不支配；23个LP阶段含22个最优阶段和1个预声明不可行负例。资源180、ε184时解析上f2≤172，求解拒绝。",
        "106文件身份核对通过，11个最终点经独立闭式核验；供热工程收益和完整前沿不由此得到验证。",
    ]), figures=link(published_base / "published_frontier.png", "公开算例实际LP点与独立解析线") + "。图源：" + link(published_base / "points.csv") + "；来源为[George Mavrotas GAMS作者报告](https://www.gams.com/modlib/adddocs/epscm.pdf)第6–9物理页。中间ε值按原文四等分规则推导；180/220边界是数学检验设置。",
        speech="这份补证能核对数值层的词典序与ε采样，包括边界改变和不可行拒绝。它是公开数学算例；两阶段包装与供热模型接口的实现范围分开，不能把小LP结果外推为真实供热的精度、效率或收益。",
        rating="多目标15分、工程10分：公开算例方法复现已准引用，供热工程多目标效果仍缺真实输入与结果。",
        evidence=refs("OPT04", "OPT05") + "\n\n" + code("src/heatplan/model.py", "src/heatplan/experiments.py", "outputs/competition_20261001/C0/published_method_citation_approval_01.json", "outputs/competition_20261001/C4_published_method/published_lp_01/manifest.json"))
    amend(23, body=bullets([
        "来源全量快照8051条记录中4935条缺依据；记录含重复引用，不是独立参数数。建筑H/C、面积、占用、价格和空间尚未准入真实工程。",
        "1R1C含单显热状态和时段末舒适，未含湿度潜热、多节点围护或小时内温控。",
        "网络仍为静态kW，未含真实GIS、动态水力/迟滞、串联提温或全系统火用优化。",
        "代表日恢复20℃造成动态冷量近似偏差，消融尚待；年度地源账本不证明多年地温稳定。",
        "同投资连续年/季已通过；同服务基准、正式多目标样本和受控敏感性仍待选定。"]),
        speech="正式连续仿真和复算说明给定模型的可行性及一致性。没有当地观测，不能说明工程精度；没有有效基准，不能声称一定节约。18项复核也没有独立重建全部设备和网络方程。",
        evidence=refs("PH03", "PH04", "PH05", "PH06", "PH07") + "\n\n" + annual_evidence_note)
    amend(24, body=bullets([
        "连续服务年和供暖季已完成；接下来补同服务基准与代表边界消融。",
        "再开展正式多目标端点、采样及H/C和边界条件受控对照，评估方案稳定性。",
        "具备数据后，用真实室温、热表、设备和GIS资料辨识，并进行留出验证与工程造价核对。",
        "长期方向：动态热网、多年地下状态和完整系统性能/火用边界。",
        "团队后续计划、负责人和完成日期：________。"]),
        speech="先形成可归因的基准和受控对照，再决定模型扩展。完整年/季已经有结果，未来计划不重复冒充既有工作；团队人员与日期安排仍留白。")
    # Final main-result admission: unsourced prototype engineering figures stay
    # in the byte-preserved history. Keep actual execution facts on page 13.
    amend(17, title="源温级与容量选择逻辑", body=bullets([
        "容量约束：0≤Qh,s,t≤Kexisting,s＋ΔKs，0≤ΔKs≤Knew,max,s；容量、出力、资源和共享电力共同限定候选。",
        "温级准入：只对允许的源—服务温级建立连接；不允许的直接供热需拒绝或经有电耗代价的转换。",
        "提温账本：W＝Qh/COP，Qenv＝Qh−W；冷热与电力口径需保持一致。",
        "专项反例：测试设定50℃源不能无功直供75℃服务；600m候选在599m距离上限下被拒绝。以上是约束反例，不是工程设备数据。",
        "真实候选设备、可用容量、报价与接入资料：________。"]),
        figures="建议画准入、容量和转换的逻辑图，再列两组反例的条件与判定。当前主体不展示未溯源原型的具体候选容量；旧容量图已归研究历史。",
        speech="选择逻辑的贡献在于可证伪：供热不足不能凭虚拟状态维持舒适，低温热不能无功代替高温服务，空间接入会改变可行域。工程设备选择需要真实条件和报价，此页先展示方程、代码和已登记反例。",
        rating="模型架构20分、场景验证15分、多尺度10分：容量/温级/空间机制与反例有证据；工程候选决策结果留空。",
        evidence=code("src/heatplan/model.py", "src/heatplan/building_thermal.py", "src/heatplan/spatial_quality.py", "docs/execution_20261001/C2_handoff.md", "docs/execution_20261001/C3_handoff.md"))
    amend(18, title="经济与碳评价口径", body=bullets([
        "Cannual＝CRF(r,n)·I＋固定运维＋逐时能源费＋情景碳成本＋已启用惩罚项；CRF＝r(1＋r)^n/[(1＋r)^n−1]。",
        "Egrid＝Σt Pgrid,t·Δt·fgrid,t，碳成本＝pCO₂·E。平均/边际、运营/生命周期和冷热共享电力边界分开。",
        "官方资料值：2023年湖北省电力平均排放因子0.4044kgCO₂/kWh。它不是逐时边际因子；采用时仍须核对地区与年份，不追改历史证书。",
        "年度资本只计一次；现金收支、年化资本与情景碳价分别列示。没有真实设备报价和收入依据，经济结果留空。"])+"\n\n"+table(["工程经济资料与结果", "已核依据/数值"], [
            ["设备建设报价与适用日期", ""], ["实际电价、收费和收入机制", ""],
            ["建设投资、年度现金费", ""], ["同服务方案成本/碳差额", ""],
            ["盈亏平衡收费、NPV/IRR/回收期", ""],
        ]),
        figures="建议放公式、核算边界图和官方因子来源；未具备依据的项目格保持空白。主体不展示未溯源原型费用、能耗或成本/减排百分比，旧结果留研究历史。",
        speech="评价要先有同服务边界、适用价格和设备报价。资本不能重复加，假设碳价不能称企业实际支付；有依据后再计算年度费、排放和盈亏平衡，明确收入与贴现情景后再做回报分析。",
        rating="结果分析15分：费用/排放公式、边界和官方资料已有；工程经济与收益结果未准入。",
        evidence=refs("D05")+"\n\n"+code("data/grid_emission_factors_2023_official.json", "src/heatplan/accounting.py", "src/heatplan/finance.py"))
    amend(19, title="可核验的数据与算法图", body=table(["证据对象", "当前可展示", "用途"], [
        ["NASA气象", "15年度文件、四字段526176值一致", "产品来源与单位核对"],
        ["N2外部实测序列", "16A三预测模式＋各自基线", "有限方法适用性诊断"],
        ["原作者两变量多目标算例", "5个非支配点；106文件身份核对", "数值层＋独立词典序包装的数学复现"],
        ["实际执行规模/耗时/数值审核", "13页程序证据", "输入未准入，不能当工程实证"],
    ]),
        figures="NASA字段/单位与核对流程；外部实测图见16A，保留作者、DOI、CC BY-SA 4.0与派生聚合说明。公开算例图：" + link(published_base / "published_frontier.png") + "，图源" + link(published_base / "points.csv") + "，来源[George Mavrotas GAMS作者报告](https://www.gams.com/modlib/adddocs/epscm.pdf)。旧武汉原型年度能耗/调度图不进入本版主成果。",
        speech="三类证据分开回答问题：气象核对证明来源一致；外部实测诊断揭示一阶模型一步与长程的差异；原作者算例核对多目标实现。任何一类都不自动证明真实规划项目的成本或减排优势。",
        rating="分析15分、多目标15分、工程10分：可核验数据与公开数学算例已准引用；工程收益留空。",
        evidence=code("outputs/competition_20261001/C0_weather_02/verification_report.json", "outputs/competition_20261001/C0/external_citation_approval_01.json", "outputs/competition_20261001/C6/external_measurement_review.md", "outputs/competition_20261001/C0/published_method_citation_approval_01.json", "outputs/competition_20261001/C4_published_method/published_lp_01/points.csv"))
    pages[19]["body"] = pages[19]["body"].replace("程序验证年图已生成；真实案例/收益待依据", "NASA全量核对、外部诊断已有；工程费用/收益留空")
    pages[12]["body"] = pages[12]["body"].replace("以下规模、耗时和gap仅来自假设原型的实际求解，不作为实测工程性能。", "以下规模、耗时和gap仅来自输入尚未准入的历史假设原型求解，作为程序运行证据；不作为本版真实工程成果。")
    amend(13, figures="建议以本页计时与规模表为主。若使用先前代表期计时图，图旁注明气象NASA POWER、建筑/价格尚未溯源、仅假设原型程序验证；不同问题的耗时不能当速度对照。")
    # Guard every result-page occurrence rather than relying on the data-page note.
    wording = {"已有真实图可选": "已保存求解图可选", "真实计算记录": "程序验证计算记录", "真实设备选择": "候选设备决策", "真实全年图表": "连续全年求解图", "全年真实图表": "连续全年求解图", "三项真实模型费用": "三项程序验证模型费用", "现有真实代表期图": "现有代表期求解图", "全年最冷/最热周": "该全年仿真最冷/最热周"}
    for p in pages:
        for field in ["title", "body", "figures", "speech", "rating", "evidence"]:
            for old, new in wording.items():
                p[field] = p[field].replace(old, new)
    external_modes = [("一步（上一实测区间均值）", "rc_one_step", "persistence_one_step"),
        ("连续开环（全序列初始化一次）", "rc_continuous_open_loop", "persistence_continuous_open_loop"),
        ("分段开环（各划分段初始化一次）", "rc_segment_open_loop", "persistence_segment_open_loop")]
    external_slot = page("16A", "外部实测序列：一步与长程表现", "采用Fraunhofer/IEA EBC Annex71德国N2主实验发布方处理后的实测序列，11988条连续10min记录；十个房间110cm温度按面积加权形成派生观测。参数仅用训练段拟合，同段记录的天气与供热作为已知条件输入。\n\n" + table(
        ["测试段模式", "首轮RC RMSE ℃", "持续基线RMSE ℃", "24h损失探索RC RMSE ℃"],
        [[label, f"{external_test[rc]['rmse_c']:.6f}", f"{external_test[baseline]['rmse_c']:.6f}", f"{followup_test[rc]['rmse_c']:.6f}"] for label, rc, baseline in external_modes]) + "\n\n" + bullets([
            "一步误差略低；连续与分段开环均高于各自基线，低一步误差不能推导长期准确。",
            "后续24h训练损失探索的留出表现更差，太阳代理参数达到声明上界；日块训练改善不能推导留出改善。这是看过首轮测试后的探索，不是新的独立确认。",
            "C6核对18项清单哈希并独立复算162指标通过；只获准有限方法适用性诊断，不是完整物理验证、武汉校准或参数转移。"]),
        link(external_manifest_path.parent / "external_validation.png", "首轮外部观测聚合、预测与三模式基线图") + "；后续探索以同页表另列。来源：Kersken与Strachan，Fraunhofer/Annex71，2020 v2，[DOI10.24406/fordatis/76.2](https://doi.org/10.24406/fordatis/76.2)，CC BY-SA 4.0。说明发布方补值、房间面积加权与条件预测；观测与模型预测采用不同线型。",
        "结果揭示长期开环不足，不能只报告单步改善。室内/功率十分钟聚合窗口和标签相位未完全确认，发布方插值/置零无逐行旗标，不能排除源处理跨划分信息。太阳得热是声明代理，不是认证全屋严格上界；内部分析计划不证明独立不可变预注册。C0按原起点协议完整重拟合两轮，参数与144项标量指标差均0，说明复现一致而非模型准确。",
        "模型20分、动态15分、分析15分：有限外部诊断及不利结果已准引用；不能替代真实工程规划输入。",
        code("outputs/competition_20261001/C0/external_citation_approval_01.json", "outputs/competition_20261001/C0/external_followup_citation_approval_01.json", "outputs/competition_20261001/C1_external/validation_report.json", "outputs/competition_20261001/C1_external_followup/validation_report.json", "outputs/competition_20261001/C0_external_reproduction_01/reproduction_report.json", "outputs/competition_20261001/C6/external_measurement_review.md"))
    pages.insert(16, external_slot)
    DEST.mkdir(parents=True, exist_ok=True)
    page_files = []
    for p in pages:
        safe = re.sub(r'[<>:"/\\|?*]', "_", p["title"])
        ordinal = f"{p['number']:02d}" if isinstance(p["number"], int) else str(p["number"])
        path = DEST / f"{ordinal}_{safe}.md"
        path.write_text(render(p), encoding="utf-8")
        page_files.append({"number": p["number"], "title": p["title"], "path": str(path.resolve()), "sha256": publisher.sha(path)})
    active_page_names = {Path(r["path"]).name for r in page_files}
    for obsolete in DEST.glob("*.md"):
        if not re.match(r"^[0-9]{2}(?:A)?_", obsolete.name) or obsolete.name.startswith("00_") or obsolete.name in active_page_names:
            continue
        archive = (DEST / "历史草稿").resolve()
        target = archive / obsolete.name
        if not obsolete.resolve().is_relative_to(DEST.resolve()) or not target.resolve().is_relative_to(DEST.resolve()):
            raise ValueError("Obsolete page archive path escapes assigned C5 folder")
        archive.mkdir(exist_ok=True)
        if target.exists():
            target = archive / (obsolete.stem + "_" + publisher.sha(obsolete)[:8] + obsolete.suffix)
        if not target.exists():
            obsolete.rename(target)
    prefix = (
        "# 答辩逐页Markdown总览\n\n更新：2026-10-02。24页主体与16A有限外部实测诊断页，保留主体编号。\n\n"
        "本版仅交付逐页Markdown，不继续生成或实现PPTX；原有PDF/PPTX草稿保留且不作为本版正式交付。\n\n"
        "按用户顺序：选题原因→九位真实成员及实际分工→7/8/10月实际进展→正式主要内容/模型算法/实验与评分亮点→企业联合精进→企业认可鉴定→不足展望。\n\n"
        "姓名、分工、阶段事实、企业名称、合作与鉴定信息尚未确认，正文对应位置均留白。7/8/10月仅为待填页签；团队可按真实经历改月份。每页分正文、图表建议、讲稿要点、评分与证据；图表建议不表示已制作或已完成实验。\n\n"
        "本版主成果不展示未溯源武汉原型的具体容量、费用、减排百分比或年度能耗/调度图。17–19页呈现选择逻辑、评价公式及可核验数据与算法证据；旧稿原字节保留在历史目录，不进入当前交付清单。历史C4执行规模/耗时只用于13页程序证据。NASA来源核对、N2有限外部诊断及不利探索、公开数学算例获准引用；工程数据真实性要求仍未全部满足。\n\n"
        + "## 页面目录\n\n"
        + table(["页", "标题"], [[r["number"], link(r["path"], r["title"])] for r in page_files])
        + "\n\n## 完整逐页内容\n\n"
    )
    overview = DEST / "00_逐页总览与完整稿.md"
    overview.write_text(prefix + "\n\n---\n\n".join(render(p) for p in pages), encoding="utf-8")
    used_metrics = [
        {"page": 13, "file": "model_profile.json", "pointer": "/" + k, "value": profile[k]}
        for k in ["variables", "constraints", "matrix_nonzeros", "solve_and_extract_seconds", "wall_seconds"]
    ] + [{"page": 13, "file": "summary.json", "pointer": "/mip_gap", "value": summary["mip_gap"]}]
    for m in used_metrics:
        m["source_path"] = str(base / m["file"])
        m["sha256"] = verified_files[m["file"]]
        m["scope"] = "representative; any per-year metrics are weighted annual equivalents"
    annual_metrics = [{"page": 18, "pointer": "/" + k, "value": annual_summary[k]}
        for k in ["objective_yuan_per_year", "total_service_emissions_kg_per_year", "building_heat_delivered_kwh_per_year", "building_cooling_delivered_kwh_per_year"]]
    annual_metrics += [{"page": 18, "pointer": "/components_yuan_per_year/" + k, "value": v} for k, v in annual_comp.items()]
    annual_metrics += [{"page": 13, "pointer": "/" + k, "value": annual_summary[k]} for k in ["solver_elapsed_seconds", "mip_gap"]]
    for m in annual_metrics:
        m.update({"source_path": str(annual["manifest_path"].parent / "summary.json"), "sha256": annual["manifest"]["files"]["summary.json"], "scope": "continuous 8784h service year, not representative equivalent"})
    used_metrics.extend(annual_metrics)
    used_metrics.extend([{"page": 13, "pointer": "/" + k, "value": annual_profile[k], "source_path": str(annual["manifest_path"].parent / "model_profile.json"),
        "sha256": annual["manifest"]["files"]["model_profile.json"], "scope": "continuous 8784h fixed-investment replay"}
        for k in ["variables", "constraints", "matrix_nonzeros", "solve_and_extract_seconds", "wall_seconds"]])
    used_metrics.append({"page": 18, "pointer": "/planning/carbon_price_yuan_per_kg", "value": carbon_price_assumption,
        "source_path": str(annual["manifest_path"].parent / "model_config.json"), "sha256": annual["manifest"]["files"]["model_config.json"],
        "scope": "design assumption, not a verified enterprise payment"})
    receipt = {
        "schema": "C5_markdown_pages.v1", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "requested_delivery": "page_by_page_markdown_only", "page_count": len(pages), "main_page_count": 24,
        "external_validation_slot": "16A; first diagnostic and adverse exploratory followup approved; all three modes and matched baselines shown",
        "edition_status": "current_admitted_data_and_mathematical_evidence_engineering_case_not_admitted",
        "all_data_authenticity_requirements_satisfied": False, "final_main_engineering_case_admitted": False,
        "markdown_links": "relative to page directory; current linked files are listed in C5/final_current_files.json; historical references remain repository locators only",
        "pptx_build_stopped": True, "pdf_deferred": True, "no_solver_invoked": True,
        "selected_by": "C0", "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": publisher.sha(manifest_path), "formal_scope": manifest["formal_scope"],
        "complete": manifest["complete"], "full_annual_run_accepted_by_C0": True,
        "manifest_external_certificate_field": manifest.get("independent_external_certificate"),
        "building_audit_passed": audit["passed"], "building_audit_annual_certification": audit["annual_certification"],
        "building_audit_scope": audit["scope"], "building_audit_limitations": audit["limitations"],
        "selected_replays": [{"name": r["name"], "manifest_path": str(r["manifest_path"]), "manifest_sha256": r["review"]["manifest_sha256"],
            "verified_result_files": r["manifest"]["files"], "review_path": str(r["review_path"]), "review_sha256": publisher.sha(r["review_path"]),
            "review_passed": r["review"]["passed"], "review_checks": r["review"]["checks"], "scope": scope,
            "hours": r["manifest"]["hours"], "parent_investment_sha256": r["manifest"]["parent_investment_sha256"]} for scope, r in replay_runs.items()],
        "engineering_test_records": engineering_records,
        "selected_core_portability": {"path": str(portable_path), "sha256": publisher.sha(portable_path), "passed": 55,
            "deselected": 2, "seconds": portable["tests"]["seconds"], "excluded": portable["tests"]["excluded"],
            "scope": portable["scope"], "full_original_regression_count": 628},
        "current_program_execution_extract": {"path": str(program_receipt_path), "sha256": publisher.sha(program_receipt_path),
            "verified_against_original_profiles_and_solver_fields": True, "scope": program_receipt["scope"], "engineering_data_approved": False},
        "final_source_audit": {"path": str(truth_manifest_path), "sha256": publisher.sha(truth_manifest_path),
            "baseline_records": 8051, "unsubstantiated_records": 4935, "records_are_unique_parameters": False,
            "complete_inventory_does_not_certify_all_data": True, "positive_project_input_field_records": 63,
            "main_engineering_case_admitted": False},
        "published_mathematical_method": {"approval_path": str(published_approval_path), "approval_sha256": publisher.sha(published_approval_path),
            "manifest_path": str(published_manifest_path), "manifest_sha256": publisher.sha(published_manifest_path),
            "file_hashes_checked": 106, "epsilon_points": published_points, "points_csv_sha256": publisher.sha(published_base / "points.csv"),
            "actual_lp_stages": 23, "optimal": 22, "expected_infeasible": 1, "closed_form_final_points_checked": 11,
            "figure_path": str(published_base / "published_frontier.png"), "figure_sha256": publisher.sha(published_base / "published_frontier.png"),
            "visual_review": "passed; labels, axes, point values and legend readable", "method_scope": published_approval["method_scope"],
            "source_url": "https://www.gams.com/modlib/adddocs/epscm.pdf", "engineering_or_empirical_validation": False},
        "weather_source_evidence": {"manifest_path": str(weather_manifest_path), "manifest_sha256": publisher.sha(weather_manifest_path),
            "report_path": str(weather_report_path), "report_sha256": publisher.sha(weather_report_path), "annual_files": len(weather_records),
            "hours": sum(r["rows"] for r in weather_records), "product_values": sum(r["rows"] * len(r["fields"]) for r in weather_records),
            "all_values_match": True, "original_csv_unchanged": True, "official_response_and_archive_hashes_checked": True,
            "product_fields": weather_report["audit_scope"]["fields"], "current_api_version": "v2.10.2", "old_api_version_backfilled": False,
            "addendum_path": str(weather_addendum_path), "addendum_sha256": publisher.sha(weather_addendum_path),
            "certifies_generated_demand_building_prices_or_station_accuracy": False},
        "current_code_version_claim": {"version": "11.12.0rc1", "source": "CHANGELOG.md", "sha256": publisher.sha(ROOT / "CHANGELOG.md"), "changes_historical_solver_epoch": False},
        "previous_edition_archive": {"path": str(history_dir), "manifest_sha256": publisher.sha(history_dir / "归档清单.json"), "old_C4_capacity_cost_and_energy_graphs_main_admitted": False},
        "external_measurement_citation": {"approval_path": str(external_approval_path), "approval_sha256": publisher.sha(external_approval_path),
            "manifest_path": str(external_manifest_path), "manifest_sha256": publisher.sha(external_manifest_path), "review_path": str(external_review_path),
            "review_sha256": publisher.sha(external_review_path), "licence": external_report["licence"], "scope": external_approval["required_scope"],
            "required_qualifications": external_approval["required_qualifications"], "full_physical_or_Wuhan_validation": False,
            "metrics": [{"pointer": f"/metrics/test/{mode}/rmse_c", "value": external_test[mode]["rmse_c"], "source_sha256": publisher.sha(external_report_path)} for _, rc, baseline in external_modes for mode in [rc, baseline]]},
        "external_exploratory_followup": {"approval_path": str(followup_approval_path), "approval_sha256": publisher.sha(followup_approval_path),
            "manifest_path": str(followup_manifest_path), "manifest_sha256": publisher.sha(followup_manifest_path), "fresh_independent_confirmation": False,
            "report_path": str(followup_report_path), "report_sha256": publisher.sha(followup_report_path), "all_adverse_results_retained": True,
            "same_protocol_refit_report_path": str(reproduction_path), "same_protocol_refit_report_sha256": publisher.sha(reproduction_path),
            "metrics": [{"pointer": f"/metrics/test/{mode}/rmse_c", "value": followup_test[mode]["rmse_c"]} for _, mode, _ in external_modes],
            "public_release_note": "Only consumed licensed N2 inputs may be packaged; unused unlicensed CTSM discovery files are not copied by this publisher"},
        "verified_result_files": verified_files, "spec_path": str(selected.resolve()),
        "spec_sha256": publisher.sha(selected), "page_files": page_files,
        "overview_path": str(overview.resolve()), "overview_sha256": publisher.sha(overview),
        "metrics": used_metrics, "investment_source_path": str(base / "investments.csv"),
        "investment_sha256": verified_files["investments.csv"],
        "display_rounding": "counts exact; capacities 2 decimals; annual costs/energies 4 decimals; MIP gap percent 4/5 decimals; raw evidence unchanged",
        "annual_cost_derivations": {"energy_operating_yuan": {"expression": "operating_cost + cooling_operating_cost + fixed_om_cost", "value": energy_om},
            "assumed_carbon_price_yuan": {"expression": "carbon_cost + cooling_carbon_cost", "value": carbon_price_cost},
            "objective_without_carbon_price_yuan": {"expression": "objective_yuan_per_year - carbon_cost - cooling_carbon_cost", "value": objective_without_carbon}},
        "unconfirmed_fields_blank": ["nine_member_names", "actual_personal_division", "July_progress", "August_progress", "October_progress", "enterprise_name", "enterprise_cooperation", "enterprise_appraisal"],
        "existing_representative_plots_visual_review": "pending; referenced suggestions only",
        "annual_plot_manifest_path": str(annual_plot_receipt_path), "annual_plots_visual_review": "passed" if all(f.get("visual_review") == "passed" for f in annual_plots["figures"]) else "pending",
        "markdown_review": "pending", "supersedes_delivery_scope_not_previous_raw_files": True,
    }
    receipt["metrics"] = [m for m in receipt["metrics"] if m["page"] == 13]
    for metric in receipt["metrics"]:
        original_path = Path(metric["source_path"])
        index = 0 if original_path.parent.name == "optimized_plan_01" else 1
        section = "profile" if original_path.name == "model_profile.json" else "numerical_summary"
        metric["original_source_path"] = metric["source_path"]
        metric["original_pointer"] = metric["pointer"]
        metric["source_path"] = str(program_receipt_path)
        metric["pointer"] = f"/records/{index}/{section}" + metric["pointer"]
        metric["sha256"] = publisher.sha(program_receipt_path)
    receipt.pop("annual_cost_derivations", None)
    receipt.pop("annual_plot_manifest_path", None)
    receipt.pop("annual_plots_visual_review", None)
    receipt["historical_plot_status"] = "archived research; not main-result admission"
    (DEST / "证据登记.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"page_count": len(pages), "result_files_verified": len(verified_files), "overview": str(overview), "pptx_build_stopped": True, "no_solver_invoked": True}, ensure_ascii=True))


if __name__ == "__main__":
    main()
