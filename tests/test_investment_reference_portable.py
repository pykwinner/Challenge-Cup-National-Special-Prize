"""Missing originals must not prevent processed reproduction or fake source review."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/run_investment_reference.py'
spec = spec_from_file_location('investment_portable_checks', SCRIPT)
runner = module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def no_pdf_bundle(tmp_path):
    source = ROOT / 'docs/implementation_20261002_followup/parallel_stage_03/investment/run_02/input_bundle'
    destination = tmp_path / 'processed'
    destination.mkdir()
    for path in source.iterdir():
        if path.suffix.lower() != '.pdf':
            shutil.copyfile(path, destination / path.name)
    assert not list(destination.rglob('*.pdf'))
    return destination


def test_missing_old_manifest_pdf_is_optional_without_changing_old_lock(no_pdf_bundle):
    lock = (no_pdf_bundle / 'input_manifest.json').read_bytes()
    report = runner.inspect_bundle(no_pdf_bundle)
    assert report['passed']
    assert not report['original_source_hashes_verified_this_run']
    assert not report['original_content_review_repeated']
    originals = [r for r in report['checks'] if not r['required_for_numerical_reproduction']]
    assert len(originals) == 1
    assert originals[0]['status'] == 'missing_original_not_required'
    assert not originals[0]['checked_this_run']
    assert originals[0]['acquisition_url'].startswith('https://www.ccgp-neimenggu.gov.cn/')
    assert (no_pdf_bundle / 'input_manifest.json').read_bytes() == lock


def test_strict_mode_reports_exact_original_url_and_sha(no_pdf_bundle):
    report = runner.inspect_bundle(no_pdf_bundle, True)
    assert not report['passed']
    missing = [r for r in report['checks'] if not r['passed']]
    assert len(missing) == 1
    assert missing[0]['status'] == 'missing_original_required'
    assert missing[0]['expected_sha256'] == 'aa9f565530afe3e4f72b5df478b34f1e603ec1c7f883a90d6d329426b05acb8d'


def test_processed_numeric_tampering_is_not_optional(no_pdf_bundle):
    file = no_pdf_bundle / 'input.csv'
    file.write_bytes(file.read_bytes() + b'\n')
    report = runner.inspect_bundle(no_pdf_bundle)
    assert not report['passed']
    bad = next(r for r in report['checks'] if r['path'] == 'input.csv')
    assert bad['required_for_numerical_reproduction']
    assert bad['checked_this_run'] and bad['status'] == 'sha256_mismatch'


@pytest.mark.parametrize('strict', [False, True])
def test_real_cli_check_only_stops_before_solver_with_exact_source_status(no_pdf_bundle, tmp_path, strict):
    out = tmp_path / ('strict' if strict else 'numeric')
    command = [sys.executable, '-B', str(SCRIPT), '--input-bundle', str(no_pdf_bundle), '--out', str(out), '--check-only']
    if strict:
        command.append('--verify-original-sources')
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                             env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout=30)
    assert process.returncode == (2 if strict else 0), process.stdout + process.stderr
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    report = json.loads((out / 'source_check.json').read_text(encoding='utf-8'))
    assert manifest['solver_runs'] == 0
    assert manifest['passed'] == (not strict)
    assert not report['original_source_hashes_verified_this_run']
    assert not list(out.rglob('dispatch.csv'))
    assert not list(out.rglob('*.pdf'))
