from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from heatplan.io import load_inputs  # noqa: E402
from heatplan.model import HeatPlanningModel  # noqa: E402


PUBLIC_INPUTS = ROOT / "data" / "public_scenario_inputs"


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    config: Path
    timeseries: Path


BUILTIN_CASES = {
    "scenario1": BenchmarkCase(
        name="scenario1_urumqi",
        config=PUBLIC_INPUTS / "scenario1_urumqi_deterministic_public.yaml",
        timeseries=PUBLIC_INPUTS / "scenario1_urumqi_representative_days_2023.csv",
    ),
    "scenario2": BenchmarkCase(
        name="scenario2_guyuan",
        config=PUBLIC_INPUTS / "scenario2_guyuan_evidence_public.yaml",
        timeseries=PUBLIC_INPUTS / "scenario2_guyuan_representative_days_2023.csv",
    ),
    "scenario3": BenchmarkCase(
        name="scenario3_wuhan",
        config=PUBLIC_INPUTS / "scenario3_wuhan_joint_public.yaml",
        timeseries=PUBLIC_INPUTS / "scenario3_wuhan_representative_days_2023.csv",
    ),
}


OUTPUT_COLUMNS = [
    "case",
    "config",
    "time_steps",
    "variables",
    "integer_variables",
    "constraints",
    "matrix_nonzeros",
    "repeats",
    "mean_seconds",
    "median_seconds",
    "min_seconds",
    "max_seconds",
    "stdev_seconds",
]


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


def _build_once(config: dict, frame) -> tuple[float, tuple[int, int, int, int, int]]:
    # Model initialization is intentionally outside the timed section. It
    # copies and indexes the time series, while this benchmark targets build().
    model = HeatPlanningModel(config, frame)
    started = perf_counter()
    model.build()
    elapsed = perf_counter() - started
    structure = (
        len(model.times),
        len(model.problem.names),
        sum(model.problem.integrality),
        len(model.problem.rows),
        sum(len(row) for row in model.problem.rows),
    )
    return elapsed, structure


def benchmark_case(case: BenchmarkCase, repeats: int, warmups: int = 0) -> dict[str, object]:
    config, frame = load_inputs(case.config, case.timeseries)

    for _ in range(warmups):
        _build_once(config, frame)

    timings: list[float] = []
    expected_structure: tuple[int, int, int, int, int] | None = None
    for _ in range(repeats):
        elapsed, structure = _build_once(config, frame)
        if expected_structure is None:
            expected_structure = structure
        elif structure != expected_structure:
            raise RuntimeError(
                f"Model structure changed between repetitions for {case.name}: "
                f"{expected_structure} != {structure}"
            )
        timings.append(elapsed)

    assert expected_structure is not None
    time_steps, variables, integer_variables, constraints, nonzeros = expected_structure
    return {
        "case": case.name,
        "config": case.config.name,
        "time_steps": time_steps,
        "variables": variables,
        "integer_variables": integer_variables,
        "constraints": constraints,
        "matrix_nonzeros": nonzeros,
        "repeats": repeats,
        "mean_seconds": statistics.fmean(timings),
        "median_seconds": statistics.median(timings),
        "min_seconds": min(timings),
        "max_seconds": max(timings),
        "stdev_seconds": statistics.pstdev(timings),
    }


def _display_value(column: str, value: object) -> str:
    if column.endswith("_seconds"):
        return f"{float(value):.4f}"
    return str(value)


def print_table(rows: list[dict[str, object]]) -> None:
    display_columns = [
        ("case", "case"),
        ("time_steps", "T"),
        ("variables", "vars"),
        ("integer_variables", "int_vars"),
        ("constraints", "constraints"),
        ("matrix_nonzeros", "nonzeros"),
        ("repeats", "runs"),
        ("mean_seconds", "mean_s"),
        ("median_seconds", "median_s"),
        ("min_seconds", "min_s"),
        ("max_seconds", "max_s"),
    ]
    rendered = [
        [_display_value(column, row[column]) for column, _ in display_columns]
        for row in rows
    ]
    widths = [
        max(len(label), *(len(row[index]) for row in rendered))
        for index, (_, label) in enumerate(display_columns)
    ]
    header = "  ".join(
        label.ljust(widths[index])
        for index, (_, label) in enumerate(display_columns)
    )
    separator = "  ".join("-" * width for width in widths)
    print(header)
    print(separator)
    for row in rendered:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _parse_custom_cases(raw_cases: list[list[str]]) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for name, config, timeseries in raw_cases:
        cases.append(
            BenchmarkCase(
                name=name,
                config=Path(config).expanduser().resolve(),
                timeseries=Path(timeseries).expanduser().resolve(),
            )
        )
    return cases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark HeatPlanningModel.build() without solving the MILP. "
            "By default, all three public representative-day cases are used."
        )
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=tuple(BUILTIN_CASES),
        help="built-in public case to run; repeat the option to select multiple cases",
    )
    parser.add_argument(
        "--case",
        action="append",
        nargs=3,
        default=[],
        metavar=("NAME", "CONFIG", "TIMESERIES"),
        help="add a custom benchmark case; may be repeated",
    )
    parser.add_argument(
        "--repeat",
        type=_positive_int,
        default=1,
        help="number of measured builds per case (default: 1)",
    )
    parser.add_argument(
        "--warmup",
        type=_nonnegative_int,
        default=0,
        help="unmeasured builds per case before timing (default: 0)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="optional CSV output path; no file is written by default",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="list built-in profile paths and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_profiles:
        for key, case in BUILTIN_CASES.items():
            print(f"{key}: {case.config} | {case.timeseries}")
        return

    selected = args.profile or ([] if args.case else list(BUILTIN_CASES))
    cases = [BUILTIN_CASES[name] for name in dict.fromkeys(selected)]
    cases.extend(_parse_custom_cases(args.case))

    duplicate_names = {case.name for case in cases if sum(row.name == case.name for row in cases) > 1}
    if duplicate_names:
        raise SystemExit(f"Duplicate case name(s): {', '.join(sorted(duplicate_names))}")

    rows = [benchmark_case(case, args.repeat, args.warmup) for case in cases]
    print_table(rows)
    if args.csv:
        destination = args.csv.expanduser().resolve()
        write_csv(destination, rows)
        print(f"\nCSV written to: {destination}")


if __name__ == "__main__":
    main()
