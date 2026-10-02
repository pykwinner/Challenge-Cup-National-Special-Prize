from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from heatplan.annual_replay_batch_cli import (
    _profile_record,
    _publish_completion,
    _required_outputs,
    _run_fingerprint,
    run_batch_replay,
)


def _write_success_evidence(output: Path) -> None:
    for name in _required_outputs(False, False):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("{}" if path.suffix == ".json" else "value\n1\n", encoding="utf-8")
    (output / "annual_replay_acceptance_certificate.json").write_text(
        json.dumps({"certification_pass": True}), encoding="utf-8",
    )


class AnnualReplayBatchTest(unittest.TestCase):
    def test_profile_record_preserves_readiness_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            failure = {
                "status": "fixed_investment_readiness_failed",
                "planning_solver_status": 0,
                "firm_heat_capacity_gap_kw": 0.0,
                "firm_electric_capacity_gap_kw": -2.5,
            }
            (output / "annual_replay_failure_summary.json").write_text(
                json.dumps(failure), encoding="utf-8"
            )

            record = _profile_record(
                name="case",
                profile_output=output,
                return_code=2,
                elapsed_seconds=1.25,
                resumed=False,
                preflight_only=False,
            )

            self.assertEqual(
                record["status"], "fixed_investment_readiness_failed"
            )
            self.assertEqual(record["firm_electric_capacity_gap_kw"], -2.5)
            self.assertEqual(record["return_code"], 2)

    def test_matching_successful_run_is_resumed_without_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("config.yaml", "representative.csv", "annual.csv"):
                (root / name).write_text("{}" if name.endswith("yaml") else name, encoding="utf-8")
            manifest = {
                "profiles": [
                    {
                        "name": "case",
                        "config": "config.yaml",
                        "representative_timeseries": "representative.csv",
                        "annual_timeseries": "annual.csv",
                        "timestamp_column": "time",
                    }
                ]
            }
            manifest_path = root / "manifest.yaml"
            manifest_path.write_text(
                yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
            )
            profile_output = root / "outputs" / "case"
            profile_output.mkdir(parents=True)
            fingerprint = _run_fingerprint(
                config_path=(root / "config.yaml").resolve(),
                representative_path=(root / "representative.csv").resolve(),
                annual_path=(root / "annual.csv").resolve(),
                timestamp_column="time",
                planning_time_limit=180.0,
                replay_time_limit=1800.0,
                planning_mip_gap=None,
                replay_mip_gap=None,
                preflight_only=False,
            )
            (profile_output / "batch_run_configuration.json").write_text(
                json.dumps({"fingerprint": fingerprint}), encoding="utf-8"
            )
            (profile_output / "annual_replay_summary.json").write_text(
                json.dumps(
                    {
                        "planning_solver_status": 0,
                        "replay_solver_status": 0,
                        "replay_unserved_rate": 0.0,
                        "annual_replay_certified": True,
                    }
                ),
                encoding="utf-8",
            )
            _write_success_evidence(profile_output)
            self.assertTrue(_publish_completion(
                profile_output, fingerprint=fingerprint,
                preflight_only=False, estimate_model_size=False,
            ))

            with patch("heatplan.annual_replay_batch_cli.subprocess.run") as run:
                result = run_batch_replay(manifest_path, root / "outputs")

            run.assert_not_called()
            self.assertEqual(result.loc[0, "status"], "success")
            self.assertTrue(bool(result.loc[0, "resumed"]))

    def test_subprocess_success_is_aggregated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("config.yaml", "representative.csv", "annual.csv"):
                (root / name).write_text("{}" if name.endswith("yaml") else name, encoding="utf-8")
            manifest_path = root / "manifest.yaml"
            manifest_path.write_text(
                yaml.safe_dump(
                    {
                        "profiles": [
                            {
                                "name": "case",
                                "config": "config.yaml",
                                "representative_timeseries": "representative.csv",
                                "annual_timeseries": "annual.csv",
                                "timestamp_column": "time",
                            }
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            def fake_run(command, **kwargs):
                output = Path(command[command.index("--output") + 1])
                (output / "annual_replay_summary.json").write_text(
                    json.dumps(
                        {
                            "planning_solver_status": 0,
                            "replay_solver_status": 0,
                            "replay_unserved_rate": 0.0,
                            "replay_variables": 100,
                            "annual_replay_certified": True,
                        }
                    ),
                    encoding="utf-8",
                )
                _write_success_evidence(output)
                return subprocess.CompletedProcess(command, 0, "ok", "")

            with patch(
                "heatplan.annual_replay_batch_cli.subprocess.run",
                side_effect=fake_run,
            ):
                result = run_batch_replay(
                    manifest_path, root / "outputs", resume=False
                )

            self.assertEqual(result.loc[0, "status"], "success")
            self.assertEqual(int(result.loc[0, "replay_variables"]), 100)
            self.assertTrue(
                (root / "outputs" / "annual_replay_batch_summary.json").exists()
            )

    def test_wall_time_limit_is_preserved_as_structured_outcome(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("config.yaml", "representative.csv", "annual.csv"):
                (root / name).write_text("{}" if name.endswith("yaml") else name, encoding="utf-8")
            manifest_path = root / "manifest.yaml"
            manifest_path.write_text(
                yaml.safe_dump(
                    {
                        "profiles": [
                            {
                                "name": "case",
                                "config": "config.yaml",
                                "representative_timeseries": "representative.csv",
                                "annual_timeseries": "annual.csv",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            timeout = subprocess.TimeoutExpired(["python"], 1.0)

            with patch(
                "heatplan.annual_replay_batch_cli.subprocess.run",
                side_effect=timeout,
            ):
                result = run_batch_replay(
                    manifest_path,
                    root / "outputs",
                    resume=False,
                    wall_time_limit=1.0,
                )

            self.assertEqual(
                result.loc[0, "status"], "wall_time_limit_exceeded"
            )
            self.assertTrue(
                (
                    root
                    / "outputs"
                    / "case"
                    / "annual_replay_batch_timeout_summary.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
