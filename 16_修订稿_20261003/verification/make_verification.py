"""生成修订稿的验证记录：编译、图件完整性、结构计数、禁词扫描。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess

import pypdf

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = [
    "main.tex",
    "sections/model-derivation.tex",
    "sections/algorithm-revision.tex",
    "sections/model-appendix.tex",
]
DATA_TABLE_SOURCES = ["数据表/仿真结果数据表.tex"]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pdf_stats(path: pathlib.Path) -> dict:
    reader = pypdf.PdfReader(path)
    text = "".join((page.extract_text() or "") for page in reader.pages)
    return {
        "path": str(path.relative_to(ROOT)),
        "pages": len(reader.pages),
        "cjk_chars": len(re.findall(r"[\u4e00-\u9fff]", text)),
        "literal_double_question": text.count("??"),
        "sha256": sha256(path),
    }


def log_stats(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {
        "overfull_boxes": len(re.findall(r"Overfull", text)),
        "overfull_max_pt": max(
            [float(v) for v in re.findall(r"Overfull \\hbox \(([0-9.]+)pt", text)] or [0.0]
        ),
        "errors": len(re.findall(r"^! ", text, re.M)),
        "undefined_references": len(re.findall(r"undefined", text)),
    }


INCLUDE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}")


def figure_audit() -> dict:
    refs: set[str] = set()
    for rel in SOURCES:
        refs.update(INCLUDE.findall((ROOT / rel).read_text(encoding="utf-8")))
    missing = [
        r for r in sorted(refs)
        if not (ROOT / r).exists() and not (ROOT / "media" / r).exists()
    ]
    return {"referenced": len(refs), "missing": missing, "passed": not missing}


FORBIDDEN = ["口径", "台账", "耦合"]
FORBIDDEN_EXEMPT = {"耦合": "技术名（热电联产/热电解耦）另行核对"}


def forbidden_scan() -> dict:
    result: dict[str, list[str]] = {}
    for word in FORBIDDEN:
        hits = []
        for rel in SOURCES + DATA_TABLE_SOURCES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for line_no, line in enumerate(text.split("\n"), 1):
                if word in line:
                    hits.append(f"{rel}:{line_no}")
        result[word] = hits
    return result


def structure_counts() -> dict:
    counts = {}
    for rel in SOURCES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        counts[rel] = {
            "sections": len(re.findall(r"\\section\{", text)),
            "subsections": len(re.findall(r"\\subsection\{", text)),
            "figures": len(INCLUDE.findall(text)),
            "tables": len(re.findall(r"\\begin\{table\}", text)),
            "equations": len(re.findall(r"\\begin\{(?:equation|align|multline)\}", text)),
            "cjk_chars": len(re.findall(r"[\u4e00-\u9fff]", text)),
        }
    return counts


def main() -> None:
    record = {
        "engine": subprocess.run(
            ["xelatex", "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.splitlines()[0],
        "compilation_passes": 2,
        "main_report": pdf_stats(ROOT / "build/main.pdf"),
        "main_report_log": log_stats(ROOT / "build/main.log"),
        "data_tables": pdf_stats(ROOT / "数据表/build/仿真结果数据表.pdf"),
        "data_tables_log": log_stats(ROOT / "数据表/build/仿真结果数据表.log"),
        "figure_audit": figure_audit(),
        "forbidden_words": forbidden_scan(),
        "structure_counts": structure_counts(),
        "dropped_experiments": [
            {
                "experiment": "工业余热共同预算额度扫描 · 峰值 55% 档（594.84 kW）",
                "reason": "同批重复运行并发写入同一结果文件，覆盖了该档的结果存档；该档首次完成时的数值（可行值 681023.12 元、验收未通过）此后无法复现，故不报告",
            },
            {
                "experiment": "工业余热共同预算额度扫描 · 峰值 40% 档（432.61 kW）",
                "reason": "触发外部墙钟上限（2280 s）被终止，未取得结果",
            },
            {
                "experiment": "图 24 重出",
                "reason": "全仓无该图生成脚本、底表不可得；本轮仅未处理，未凭图形估造底表",
            },
        ],
        "acceptance": {},
    }
    m, d = record["main_report_log"], record["data_tables_log"]
    record["acceptance"] = {
        "overfull_within_2pt": m["overfull_max_pt"] <= 2.0 and d["overfull_max_pt"] <= 2.0,
        "no_latex_error": m["errors"] == 0 and d["errors"] == 0,
        "no_undefined_reference": m["undefined_references"] == 0 and d["undefined_references"] == 0,
        "no_literal_double_question": record["main_report"]["literal_double_question"] == 0,
        "all_figures_present": record["figure_audit"]["passed"],
    }
    (ROOT / "verification/验证记录.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "main": record["main_report"], "data_tables": record["data_tables"],
        "acceptance": record["acceptance"],
        "forbidden_words": record["forbidden_words"],
        "figure_audit": record["figure_audit"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
