"""Register completion after the saved PNG/PDF renders have been visually checked.

This explicit flag confirms a human/agent visual step; it does not replace tests.
Only new files in this spatial-reference batch are written.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def write(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-visual-qa", action="store_true", required=True)
    parser.parse_args()
    now = datetime.now(timezone.utc).isoformat()
    verification = read("verification.json")
    assert verification["focused_tests"]["exit_code"] == 0
    integrity = read("input_integrity.json")
    assert all(sha(Path(p)) == r["sha256"] for p, r in integrity["before"].items())
    for r in verification["pdf_checks"]:
        assert sha(OUT / r["city"] / "map.pdf") == r["pdf_sha256"]
        assert sha(OUT / r["render_file"]) == r["render_sha256"]
    verification.update(state="complete_spatial_reference_only", finalized_utc=now)
    verification["pdf_visual_qa"] = {"passed": True, "checked_utc": now,
                                      "checked_via": "view_image of all saved PDF renders; Wuhan text crop additionally checked; coordinates, legends and all six IDs readable",
                                      "pdf_export": "Type 3 vector glyphs", "no_actual_heating_network_claim": True}
    write("verification.json", verification)
    results = read("results.json"); results.update(state="complete_spatial_reference_only", focused_tests="23 passed", maps_visual_qa_passed=True)
    write("results.json", results)
    status = read("status.json")
    status.update(stage="complete_spatial_reference_only", state="complete", updated_utc=now,
                  completed_stages=["protocol_registered", "selection_frozen", "bounded_native_intake", "topology_and_routes", "three_png_and_pdf_maps", "23_focused_tests_passed", "original_inputs_integrity", "png_pdf_visual_qa", "manifest_registered"],
                  next_stage=None, active_city=None, github_upload=False, milp_run=False,
                  optional_dependencies="Only process-local isolated pyproj/support packages; ignored, excluded from deliverable, no global installs")
    write("status.json", status)
    readme = (OUT / "README.md").read_text(encoding="utf-8")
    readme += "\n验收：23项专项测试通过；三份PNG与单页PDF已视觉核验，建筑编号与固原超距失败图例可读。运行版本及未运行项见verification.json；正式交付文件SHA256见manifest.json。当前主环境未装pyproj时该测试模块明确skip，主项目回归由统筹任务负责，不以skip冒充通过。\n"
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    (OUT / "qa/README.md").write_text("# 地图视觉核验\n\n三份`*_pdf.png`是最终PDF的110dpi渲染，已逐城检查。`wuhan_pdf_text_crop.png`为最终文字区核验。`*_pdfium.png`和`*_pdf_fresh.png`为早期字体/阅读器显示诊断参考，保留排查证据，不替代各城目录的最终map.png/map.pdf。\n", encoding="utf-8")
    sources = [ROOT / "src/heatplan/osm_reference.py", ROOT / "scripts/build_osm_reference_cases.py", ROOT / "tests/test_osm_reference.py"]
    sources += [p for p in sorted(OUT.rglob("*")) if p.is_file() and "_runtime_packages" not in p.parts and "__pycache__" not in p.parts and p.name != "manifest.json"]
    manifest = {"registered_utc": now, "status": "spatial_reference_experimental_not_formal_engineering_release",
                "github_upload": False, "excluded": {"_runtime_packages/": "isolated optional dependencies, not vendor deliverables", "__pycache__/": "bytecode"},
                "files": [{"file": str(p.relative_to(ROOT)).replace("\\", "/"), "bytes": p.stat().st_size, "sha256": sha(p)} for p in sources]}
    write("manifest.json", manifest)
    print(json.dumps({"state": status["state"], "delivery_files": len(manifest["files"]), "original_files_unchanged": len(integrity["before"]), "focused_tests": "23 passed"}, ensure_ascii=True))


if __name__ == "__main__": main()
