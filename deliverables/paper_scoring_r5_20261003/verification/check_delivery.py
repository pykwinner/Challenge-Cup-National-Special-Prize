"""Read-only paper delivery checks. Does not compile, run experiments, or modify Git.

Draft invocation:
    python final_candidate_readonly_check_draft.py DELIVERY_ROOT \
        --baseline-source PREVIOUS_DELIVERY/paper_source

All output is JSON. Credential findings contain file names, rule IDs, and counts
only. Suspicious credential-named files are rejected without reading their bytes.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

EXPECTED_CHAPTERS = [
    "sections/problem-data-scoring-20261003.tex",
    "sections/model-scoring-20261003.tex",
    "sections/algorithm-scoring-20261003.tex",
    "sections/experiments-scoring-20261003.tex",
    "sections/economics-advantage-20261003.tex",
    "sections/software-pseudocode-20261003.tex",
    "sections/conclusion-advantage-20261003.tex",
]
EXPECTED_ASSETS = [
    "cover.jpg", "body-background.jpg",
    "media/paper_r2/external_w1.pdf",
    "media/paper_r2/model_paper/source_network_load_storage_zh.pdf",
    "media/paper_r2/mechanism_paper/thermal_coupling.pdf",
    "media/paper_r2/tradeoff_paper/cost_carbon.pdf",
    "build-fonts/NotoSansSC-Regular.ttf",
    "build-fonts/NotoSerifSC-Regular.ttf",
    "build-fonts/NotoSerifSC-Bold.ttf",
]
TEXT_SUFFIXES = {".tex", ".md", ".txt", ".json", ".ps1", ".py", ".yml", ".yaml", ".csv", ".toml", ".ini", ".cfg"}
CREDENTIAL_RULES = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "api_key_shape": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
}
SENSITIVE_FILE_NAME = re.compile(r"^(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?|.*(?:private[_-]?key|credentials|access[_-]?token).*)$", re.I)
DISPLAY_MATH = re.compile(r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?)\}.*?\\end\{\1\}|\\\[.*?\\\]", re.S)
NUMBER = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def no_comments(source: str) -> str:
    return "\n".join(re.split(r"(?<!\\)%", line, maxsplit=1)[0] for line in source.splitlines())


def safe_relative(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    path.relative_to(root.resolve())
    return path


def tex_inputs(source: str) -> list[str]:
    return re.findall(r"\\(?:input|include)\{([^}]+)\}", no_comments(source))


def latex_path(root: Path, rel: str) -> Path:
    if not Path(rel).suffix:
        rel += ".tex"
    return safe_relative(root, rel)


def is_subsequence(old: list[str], new: list[str]) -> bool:
    iterator = iter(new)
    return all(any(item == expected for item in iterator) for expected in old)


def numeric_tables(source: str) -> dict[str, list[list[str]]]:
    """Compare numeric cells in labelled experiment tables, not prose/captions."""
    found = {}
    source = no_comments(source)
    for outer in re.finditer(r"\\begin\{table\*?\}.*?\\end\{table\*?\}", source, re.S):
        labels = re.findall(r"\\label\{(tab:[^}]+)\}", outer.group(0))
        if len(labels) != 1:
            continue
        inner = re.search(r"\\begin\{(tabularx|tabular\*?|longtable)\}(.*?)\\end\{\1\}", outer.group(0), re.S)
        if inner is None:
            continue
        body = inner.group(2)
        if "\\toprule" in body:
            body = body.split("\\toprule", 1)[1]
        rows = []
        for row in re.split(r"\\\\", body):
            if "&" not in row:
                continue
            values = NUMBER.findall(row)
            if values:
                rows.append(values)
        if rows:
            found[labels[0]] = rows
    return found


def digest_object(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery_root", type=Path)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--source-relative", default="paper_source")
    parser.add_argument("--pdf-relative", default="paper.pdf")
    parser.add_argument("--font-license-directory", type=Path)
    parser.add_argument("--expected-pages", type=int)
    parser.add_argument("--file-manifest", type=Path, help="Optional JSON file list, or object with files list; each entry needs path and sha256.")
    args = parser.parse_args()
    root = args.delivery_root.resolve()
    base = args.baseline_source.resolve()
    report = {
        "script_status": "readonly_draft",
        "checked_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
        "delivery_root": str(root), "baseline_source": str(base),
        "read_only": True, "compile_run": False, "experiments_run": False,
        "git_commands_run": False, "secret_values_output": False,
        "checks": [], "limitations": [
            "This script does not prove that paper.pdf was compiled from the supplied source; compile recorder and final-copy checks must bind them separately.",
            "Numeric table comparison covers existing labelled tables in experiment/economics sections; newly added design tables are reported elsewhere.",
            "Credential patterns cannot detect every possible secret. Suspicious credential-named files are not read.",
            "Display-math comparison preserves prior blocks in order; extra new blocks are reported but require substantive review.",
        ],
    }
    checks = report["checks"]
    stage = "paths"
    try:
        source = safe_relative(root, args.source_relative)
        pdf = safe_relative(root, args.pdf_relative)
        checks.append({"id": "ROOTS", "passed": root.is_dir() and source.is_dir() and base.is_dir() and pdf.is_file()})
        main_tex = text(safe_relative(source, "main.tex"))
        inputs = tex_inputs(main_tex)
        checks.append({"id": "SEVEN_MAIN_INPUTS", "passed": inputs == EXPECTED_CHAPTERS, "actual_count": len(inputs), "actual_paths": inputs})
        stage = "dependencies"
        dependency_files = ["main.tex"]
        missing = []
        violations = []
        queue = list(inputs)
        seen = set()
        while queue:
            rel = queue.pop(0)
            if rel in seen:
                continue
            seen.add(rel)
            try:
                path = latex_path(source, rel)
            except ValueError:
                violations.append({"rule": "dependency_outside_source", "reference_path": rel})
                continue
            if not path.is_file():
                missing.append(rel)
                continue
            dependency_files.append(path.relative_to(source).as_posix())
            queue.extend(tex_inputs(text(path)))
        checks.append({"id": "TEX_DEPENDENCIES", "passed": not missing and not violations and len(seen) == 7, "missing": missing, "path_rule_violations": violations, "recursive_input_count": len(seen)})
        graphics = []
        graphics_missing = []
        for rel in dependency_files:
            body = no_comments(text(source / rel))
            for graphic in re.findall(r"\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}", body):
                options = []
                for prefix in ["", "media/"]:
                    try:
                        item = safe_relative(source, prefix + graphic)
                    except ValueError:
                        continue
                    if item.is_file():
                        options.append(item)
                if options:
                    graphics.append(options[0].relative_to(source).as_posix())
                else:
                    graphics_missing.append({"tex": rel, "reference_path": graphic})
        checks.append({"id": "GRAPHICS_DEPENDENCIES", "passed": not graphics_missing and sorted(set(graphics)) == sorted(EXPECTED_ASSETS[:6]), "resolved_paths": sorted(set(graphics)), "missing": graphics_missing})
        asset_checks = []
        for rel in EXPECTED_ASSETS:
            current_file = source / rel
            baseline_file = base / rel
            exists = current_file.is_file() and baseline_file.is_file()
            current_hash = sha(current_file) if current_file.is_file() else None
            old_hash = sha(baseline_file) if baseline_file.is_file() else None
            asset_checks.append({"path": rel, "sha256": current_hash, "baseline_sha256": old_hash, "same": exists and current_hash == old_hash})
        checks.append({"id": "INHERITED_ASSETS", "passed": all(item["same"] for item in asset_checks), "files": asset_checks})
        license_dir = (args.font_license_directory or source / "build-fonts").resolve()
        license_checks = []
        for filename in ["OFL-NotoSansSC.txt", "OFL-NotoSerifSC.txt"]:
            item = license_dir / filename
            present = item.is_file()
            content = text(item) if present else ""
            license_checks.append({"file": filename, "exists": present, "has_ofl_header_and_conditions": "SIL OPEN FONT LICENSE Version 1.1" in content and "PERMISSION & CONDITIONS" in content, "sha256": sha(item) if present else None})
        notices = license_dir / "FONT_NOTICES.md"
        notice_content = text(notices) if notices.is_file() else ""
        checks.append({"id": "FONT_LICENSES", "passed": all(item["has_ofl_header_and_conditions"] for item in license_checks) and "2014-2021 Adobe" in notice_content and "2017-2024 Adobe" in notice_content, "license_directory": str(license_dir), "files": license_checks, "actual_copyright_notices_present": "2014-2021 Adobe" in notice_content and "2017-2024 Adobe" in notice_content})
        stage = "equations_and_tables"
        equation_checks = []
        for rel in EXPECTED_CHAPTERS:
            before = [item.group(0) for item in DISPLAY_MATH.finditer(text(base / rel))]
            after = [item.group(0) for item in DISPLAY_MATH.finditer(text(source / rel))]
            equation_checks.append({"path": rel, "old_blocks": len(before), "new_blocks": len(after), "prior_blocks_preserved_in_order": is_subsequence(before, after), "exact_block_list_equal": before == after, "added_blocks": len(after) - len(before)})
        checks.append({"id": "PRIOR_DISPLAY_MATH", "passed": all(item["prior_blocks_preserved_in_order"] for item in equation_checks), "sections": equation_checks})
        table_checks = []
        for rel in ["sections/experiments-scoring-20261003.tex", "sections/economics-advantage-20261003.tex"]:
            before = numeric_tables(text(base / rel))
            after = numeric_tables(text(source / rel))
            for label, old_rows in before.items():
                new_rows = after.get(label)
                table_checks.append({"path": rel, "label": label, "present": new_rows is not None, "numeric_row_count": len(old_rows), "same_numeric_rows": old_rows == new_rows, "baseline_numeric_sha256": digest_object(old_rows), "current_numeric_sha256": digest_object(new_rows) if new_rows is not None else None})
        expected_labels = {"tab:w1", "tab:heatpump", "tab:factorial", "tab:network", "tab:tradeoff"}
        actual_labels = {item["label"] for item in table_checks}
        checks.append({"id": "PRIOR_NUMERIC_TABLES", "passed": expected_labels.issubset(actual_labels) and all(item["same_numeric_rows"] for item in table_checks), "expected_scientific_labels": sorted(expected_labels), "found_labels": sorted(actual_labels), "tables": table_checks})
        stage = "pdf"
        import fitz
        with fitz.open(pdf) as document:
            page_text = [page.get_text("text") for page in document]
            all_text = "\n".join(page_text)
            metrics = {
                "pages": len(document), "sha256": sha(pdf), "bytes": pdf.stat().st_size,
                "non_whitespace_characters": len(re.sub(r"\s", "", all_text)),
                "han_characters": len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", all_text)),
                "latin_word_tokens": len(re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", all_text)),
                "numeric_tokens": len(NUMBER.findall(all_text)),
                "per_page_non_whitespace_characters": [len(re.sub(r"\s", "", item)) for item in page_text],
                "unresolved_reference_markers": all_text.count("??"),
                "raw_pdf_text_output": False,
            }
        checks.append({"id": "PDF_TEXT_METRICS", "passed": metrics["non_whitespace_characters"] > 1000 and metrics["han_characters"] > 500 and metrics["unresolved_reference_markers"] == 0 and (args.expected_pages is None or metrics["pages"] == args.expected_pages), "expected_pages": args.expected_pages, "metrics": metrics})
        stage = "credentials"
        findings = []
        sensitive_files = []
        skipped_text_files = []
        scanned = 0
        forbidden_parts = {".git", ".aws", ".codex", ".agents", "__pycache__"}
        for item in sorted(root.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(root)
            try:
                item.resolve().relative_to(root)
            except ValueError:
                sensitive_files.append({"file": rel.as_posix(), "rule": "resolved_file_outside_delivery_not_read"})
                continue
            if any(part in forbidden_parts for part in rel.parts):
                sensitive_files.append({"file": rel.as_posix(), "rule": "excluded_internal_directory_file"})
                continue
            if SENSITIVE_FILE_NAME.match(item.name):
                sensitive_files.append({"file": rel.as_posix(), "rule": "credential_filename_not_read"})
                continue
            if item.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if item.stat().st_size > 5_000_000:
                skipped_text_files.append({"file": rel.as_posix(), "rule": "text_file_exceeds_scan_limit", "bytes": item.stat().st_size})
                continue
            try:
                body = text(item)
            except UnicodeError:
                skipped_text_files.append({"file": rel.as_posix(), "rule": "text_encoding_unreadable"})
                continue
            scanned += 1
            for rule, pattern in CREDENTIAL_RULES.items():
                count = sum(1 for _ in pattern.finditer(body))
                if count:
                    findings.append({"file": rel.as_posix(), "rule": rule, "count": count})
        checks.append({"id": "CREDENTIAL_PATTERN_SCREEN", "passed": not findings and not sensitive_files and not skipped_text_files, "scanned_text_files": scanned, "findings_without_values": findings, "sensitive_filenames_without_content_read": sensitive_files, "unscanned_text_files": skipped_text_files})
        stage = "manifest"
        if args.file_manifest:
            manifest = json.loads(text(args.file_manifest))
            entries = manifest.get("files", []) if isinstance(manifest, dict) else manifest
            manifest_checks = []
            for entry in entries:
                if not isinstance(entry, dict) or "path" not in entry or "sha256" not in entry:
                    manifest_checks.append({"rule": "unsupported_manifest_entry", "passed": False})
                    continue
                if not isinstance(entry["sha256"], str) or re.fullmatch(r"[0-9a-fA-F]{64}", entry["sha256"]) is None:
                    manifest_checks.append({"file": entry.get("path"), "rule": "invalid_sha256_format", "passed": False})
                    continue
                try:
                    item = safe_relative(root, entry["path"])
                except (ValueError, TypeError):
                    manifest_checks.append({"file": entry.get("path"), "rule": "manifest_path_outside_delivery", "passed": False})
                    continue
                if SENSITIVE_FILE_NAME.match(item.name):
                    manifest_checks.append({"file": entry["path"], "rule": "credential_filename_not_read", "passed": False})
                    continue
                actual = sha(item) if item.is_file() else None
                manifest_checks.append({"file": entry["path"], "passed": actual == entry["sha256"], "expected_sha256": entry["sha256"], "actual_sha256": actual})
            checks.append({"id": "OPTIONAL_FILE_MANIFEST", "passed": bool(manifest_checks) and all(item["passed"] for item in manifest_checks), "files": manifest_checks})
        report["passed"] = bool(checks) and all(item["passed"] for item in checks)
    except Exception as exception:
        # Error text is deliberately omitted; it may contain confidential source text.
        report["error"] = {"stage": stage, "exception_type": type(exception).__name__}
        report["passed"] = False
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
