from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from heatplan.chronology import (
    load_fixed_investment_plan_checkpoint,
    save_fixed_investment_plan_checkpoint,
)
from heatplan.io import load_config


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add validated manifests to existing fixed-plan checkpoints"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--checkpoint-root", required=True)
    parser.add_argument("--profiles", nargs="+")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    checkpoint_root = Path(args.checkpoint_root).resolve()
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    selected = set(args.profiles or [])
    for profile in payload.get("profiles", []):
        name = str(profile["name"])
        if selected and name not in selected:
            continue
        config = load_config(
            _resolve(manifest_path.parent, str(profile["config"]))
        )
        representative = pd.read_csv(
            _resolve(
                manifest_path.parent,
                str(profile["representative_timeseries"]),
            )
        )
        checkpoint = checkpoint_root / name
        fixed_plan = load_fixed_investment_plan_checkpoint(
            checkpoint,
            expected_config=config,
            expected_representative_timeseries=representative,
            require_manifest=False,
        )
        manifest = save_fixed_investment_plan_checkpoint(
            checkpoint,
            fixed_plan,
            source_config=config,
            representative_timeseries=representative,
        )
        print(f"{name}: {manifest}")


if __name__ == "__main__":
    main()
