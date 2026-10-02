"""Check the selected Markdown publication and write its exact current file list.

This performs publication QA only. It never runs a model, fitter, or test suite.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/competition_20261001/C5"
DEST = OUT / "答辩逐页Markdown"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def main():
    receipt_path = DEST / "证据登记.json"
    receipt = read(receipt_path)
    pages = {str(row["number"]): Path(row["path"]) for row in receipt["page_files"]}
    expected_numbers = {str(number) for number in range(1, 25)} | {"16A"}
    checks = {}

    def check(name, passed):
        checks[name] = bool(passed)
        if not passed:
            raise ValueError(f"Publication check failed: {name}")

    check("24_main_pages_plus_16A", set(pages) == expected_numbers and len(pages) == 25)
    check("exactly_one_current_16A", len(list(DEST.glob("16A_*.md"))) == 1)
    overview = Path(receipt["overview_path"])
    active_md = set(pages.values()) | {overview}
    check("no_obsolete_current_page", set(DEST.glob("*.md")) == active_md)
    check("page_hashes_match_receipt", all(sha(Path(row["path"])) == row["sha256"] for row in receipt["page_files"]))
    check("overview_hash_matches_receipt", sha(overview) == receipt["overview_sha256"])
    sections = ["正文建议", "图表建议", "讲稿要点", "对应评分与证据"]
    text = {number: path.read_text(encoding="utf-8") for number, path in pages.items()}
    check("four_sections_each_page", all(all(content.count("## " + heading) == 1 for heading in sections) for content in text.values()))
    links = set()
    for path in active_md:
        content = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(<?([^\n>]+?)>?\)", content):
            if re.match(r"https?://", target):
                continue
            resolved = (path.parent / target).resolve()
            check("local_link_" + str(len(links)), resolved.is_relative_to(ROOT.resolve()) and resolved.is_file())
            links.add(resolved)
    check("no_historical_links", all("历史" not in relative(path) and "/C4/" not in relative(path) and "/C5/plots/" not in relative(path) for path in links))

    def empty_table(number, expected_rows):
        body = text[number].split("## 正文建议\n\n", 1)[1].split("\n\n## 图表建议", 1)[0]
        rows = [line for line in body.splitlines() if line.startswith("|")][2:]
        return len(rows) == expected_rows and all(all(not value.strip() for value in line.strip("|").split("|")[1:]) for line in rows)

    check("nine_real_members_blank", empty_table("2", 9))
    check("all_progress_and_enterprise_facts_blank", all(empty_table(number, rows) for number, rows in [("3", 4), ("4", 4), ("5", 4), ("21", 4), ("22", 5)]))
    external = receipt["external_measurement_citation"]
    followup = receipt["external_exploratory_followup"]
    check("all_approved_external_modes_and_baselines", all(f"{row['value']:.6f}" in text["16A"] for row in external["metrics"] + followup["metrics"]))
    check("adverse_external_results_and_licence_retained", all(phrase in text["16A"] for phrase in ["均高于各自基线", "留出表现更差", "不是新的独立确认", "CC BY-SA 4.0", "面积加权"]))
    published = receipt["published_mathematical_method"]
    check("approved_published_example_scope", published["file_hashes_checked"] == 106 and len(published["epsilon_points"]) == 5 and not published["engineering_or_empirical_validation"])
    check("published_example_not_full_heatmodel_or_augmecon", "不等同HeatPlanningModel" in text["14"] and "完整AUGMECON" in text["14"])
    check("latest_regression_and_portable_counts_separate", "628" in text["20"] and "67.551" in text["20"] and "55" in text["20"] and "8.02" in text["20"] and "590" not in text["20"])
    check("main_results_exclude_unsourced_engineering_numbers", all(value not in "\n".join(text[number] for number in ["17", "18", "19"]) for value in ["803336", "586978", "259799", "301782", "25396", "561582", "optimized_annual_duration", "markdown_annual_v3"]))
    check("source_audit_complete_but_engineering_not_admitted", receipt["final_source_audit"]["baseline_records"] == 8051 and receipt["final_source_audit"]["unsubstantiated_records"] == 4935 and not receipt["all_data_authenticity_requirements_satisfied"])
    archive = Path(receipt["previous_edition_archive"]["path"])
    archived = read(archive / "归档清单.json")
    check("28_original_archive_files_unchanged", len(archived["files"]) == 28 and all(sha(Path(row["archived_path"])) == row["sha256"] for row in archived["files"]))

    used_figure_sources = {
        ROOT / "outputs/competition_20261001/C1_external/predictions_and_residuals.csv",
        ROOT / "outputs/competition_20261001/C1_external/external_validation.png",
        ROOT / "outputs/competition_20261001/C1_external/validation_report.json",
        ROOT / "outputs/competition_20261001/C4_published_method/published_lp_01/published_frontier.png",
        ROOT / "outputs/competition_20261001/C4_published_method/published_lp_01/points.csv",
    }
    check("used_figure_sources_exist", all(path.is_file() for path in used_figure_sources))
    checks_path = DEST / "检查记录.json"
    final_path = OUT / "final_current_files.json"
    current = active_md | links | used_figure_sources | {
        receipt_path, checks_path, final_path,
        ROOT / "docs/execution_20261001/评分逐项验收与算法优势.md",
        ROOT / "docs/execution_20261001/C5_handoff.md",
    }
    check("current_list_excludes_history_and_old_deliverables", all("历史" not in relative(path) and "/C5/plots/" not in relative(path) and "/C4/" not in relative(path) and path.suffix.lower() not in [".pptx", ".pdf"] for path in current))
    receipt["markdown_review"] = "passed"
    receipt["current_file_list"] = relative(final_path)
    receipt["existing_representative_plots_visual_review"] = "historical only; excluded from current delivery"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    checks_path.write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(), "passed": True,
        "page_count": 25, "main_page_count": 24, "only_current_16A": True,
        "local_link_targets_checked": len(links), "checks": checks,
        "figure_visual_review": {"N2_first_diagnostic": "passed previously; unchanged approved image", "published_mathematical_example": published["visual_review"]},
        "no_solver_fitter_or_test_suite_executed": True, "engineering_case_admitted": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    final_path.write_text(json.dumps({
        "files": sorted(relative(path) for path in current),
        "schema": "C5.final_current_files.v1", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "file_count": len(current), "page_count": 25,
        "purpose": "Exact current publication files for C0 whitelist packaging; no historical engineering results or old PPT/PDF",
        "sha256": {relative(path): sha(path) for path in sorted(current) if path != final_path},
        "self_hash_omitted": True,
        "figure_paths": sorted(relative(path) for path in used_figure_sources),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "pages": 25, "unique_current_16A": True, "links_checked": len(links), "current_files": len(current), "file_list": relative(final_path)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
