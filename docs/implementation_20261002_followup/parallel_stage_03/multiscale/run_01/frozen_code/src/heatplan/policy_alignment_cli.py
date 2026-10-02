from __future__ import annotations

import argparse

from .policy_alignment import export_policy_alignment


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate one solved heating plan against an auditable policy profile"
    )
    parser.add_argument("--summary", required=True, help="model summary.json")
    parser.add_argument("--profile", required=True, help="policy profile YAML")
    parser.add_argument("--output", required=True, help="output directory")
    parser.add_argument(
        "--scenario-id",
        help="scenario1_existing_network / scenario2_distributed_clean / scenario3_high_quality_newbuild",
    )
    parser.add_argument(
        "--as-of-date",
        help="policy audit date in YYYY-MM-DD",
    )
    args = parser.parse_args()
    result = export_policy_alignment(
        args.summary,
        args.profile,
        args.output,
        scenario_id=args.scenario_id,
        as_of_date=args.as_of_date,
    )
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
