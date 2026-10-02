from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csc_array, csr_array


@dataclass
class LinearMILP:
    names: list[str] = field(default_factory=list)
    lower: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    integrality: list[int] = field(default_factory=list)
    objective: list[float] = field(default_factory=list)
    rows: list[dict[int, float]] = field(default_factory=list)
    row_lower: list[float] = field(default_factory=list)
    row_upper: list[float] = field(default_factory=list)
    _compiled_matrix: csc_array | None = field(
        default=None, init=False, repr=False
    )
    _compiled_constraints: LinearConstraint | None = field(
        default=None, init=False, repr=False
    )
    _compiled_bounds: Bounds | None = field(
        default=None, init=False, repr=False
    )
    _compiled_integrality: np.ndarray | None = field(
        default=None, init=False, repr=False
    )
    last_matrix_compile_seconds: float = field(
        default=0.0, init=False
    )
    last_matrix_cache_hit: bool = field(default=False, init=False)
    _last_solve_objective: np.ndarray | None = field(default=None, init=False, repr=False)
    _last_solve_fun: float | None = field(default=None, init=False, repr=False)

    def audit_solution(
        self, values: np.ndarray, *, absolute_tolerance: float = 1e-6,
        relative_tolerance: float = 1e-8,
    ) -> dict:
        """Recompute every row, bound and integer restriction from the full solution.

        Scaled violations divide by atol + rtol * row magnitude; <= 1 passes.
        The objective snapshot belongs to the last solve, including emissions
        objectives that a caller restores after the solver returns.
        """
        if not np.isfinite(absolute_tolerance) or not np.isfinite(relative_tolerance) or absolute_tolerance <= 0 or relative_tolerance < 0:
            raise ValueError("audit tolerances must be positive/nonnegative")
        x = np.asarray(values, dtype=float)
        report = {
            "numerical_audit_passed": False,
            "numerical_audit_tolerance": {
                "absolute": absolute_tolerance, "relative": relative_tolerance,
                "scaled_violation_limit": 1.0,
            },
            "numerical_solution_finite": bool(x.shape == (len(self.names),) and np.isfinite(x).all()),
        }
        if not report["numerical_solution_finite"]:
            return report
        compile_seconds, cache_hit = self.last_matrix_compile_seconds, self.last_matrix_cache_hit
        matrix = self.constraint_matrix()
        self.last_matrix_compile_seconds, self.last_matrix_cache_hit = compile_seconds, cache_hit
        activity = np.asarray(matrix @ x)
        magnitude = np.asarray(abs(matrix) @ np.abs(x))
        row_lo, row_hi = np.asarray(self.row_lower), np.asarray(self.row_upper)
        row_violation = np.maximum(0.0, np.maximum(row_lo - activity, activity - row_hi))
        row_scaled = np.zeros_like(activity)
        for bound, violation in ((row_lo, np.maximum(0.0, row_lo - activity)), (row_hi, np.maximum(0.0, activity - row_hi))):
            scale = np.maximum(np.maximum(1.0, magnitude), np.where(np.isfinite(bound), np.abs(bound), 0.0))
            row_scaled = np.maximum(row_scaled, violation / (absolute_tolerance + relative_tolerance * scale))
        lower, upper = np.asarray(self.lower), np.asarray(self.upper)
        bound_violation = np.maximum(0.0, np.maximum(lower - x, x - upper))
        bound_scaled = np.zeros_like(x)
        for bound, violation in ((lower, np.maximum(0.0, lower - x)), (upper, np.maximum(0.0, x - upper))):
            scale = np.maximum(np.maximum(1.0, np.abs(x)), np.where(np.isfinite(bound), np.abs(bound), 0.0))
            bound_scaled = np.maximum(bound_scaled, violation / (absolute_tolerance + relative_tolerance * scale))
        integer_values = x[np.asarray(self.integrality) != 0]
        integer_violation = float(np.max(np.abs(integer_values - np.rint(integer_values)), initial=0.0))
        objective_error = None
        objective_pass = True
        if self._last_solve_objective is not None and self._last_solve_fun is not None:
            recomputed = float(self._last_solve_objective @ x)
            objective_error = abs(recomputed - self._last_solve_fun)
            objective_scale = max(1.0, float(np.abs(self._last_solve_objective * x).sum()), abs(self._last_solve_fun))
            objective_pass = bool(objective_error <= absolute_tolerance + relative_tolerance * objective_scale)
        report.update({
            "numerical_constraint_max_violation": float(np.max(row_violation, initial=0.0)),
            "numerical_constraint_max_scaled_violation": float(np.max(row_scaled, initial=0.0)),
            "numerical_constraint_worst_row": int(np.argmax(row_scaled)) if len(row_scaled) else None,
            "numerical_bound_max_violation": float(np.max(bound_violation, initial=0.0)),
            "numerical_bound_max_scaled_violation": float(np.max(bound_scaled, initial=0.0)),
            "numerical_bound_worst_variable": self.names[int(np.argmax(bound_scaled))] if len(x) else None,
            "numerical_integrality_max_violation": integer_violation,
            "numerical_objective_recompute_error": objective_error,
            "numerical_objective_checked": objective_error is not None,
            "numerical_audit_passed": bool(
                np.isfinite(activity).all() and np.max(row_scaled, initial=0.0) <= 1.0
                and np.max(bound_scaled, initial=0.0) <= 1.0
                and integer_violation <= absolute_tolerance and objective_pass
            ),
        })
        return report

    def _invalidate_matrix(self) -> None:
        self._compiled_matrix = None
        self._compiled_constraints = None
        self._compiled_bounds = None
        self._compiled_integrality = None

    def add_var(self, name: str, lb: float = 0.0, ub: float = np.inf, integer: bool = False, obj: float = 0.0) -> int:
        self._invalidate_matrix()
        idx = len(self.names)
        self.names.append(name)
        self.lower.append(float(lb))
        self.upper.append(float(ub))
        self.integrality.append(1 if integer else 0)
        self.objective.append(float(obj))
        return idx

    def add_constraint(self, coefficients: dict[int, float], lb: float = -np.inf, ub: float = np.inf) -> None:
        self._invalidate_matrix()
        cleaned = {i: float(v) for i, v in coefficients.items() if abs(v) > 1e-12}
        self.rows.append(cleaned)
        self.row_lower.append(float(lb))
        self.row_upper.append(float(ub))

    def add_to_objective(self, index: int, coefficient: float) -> None:
        self.objective[index] += float(coefficient)

    def constraint_matrix(self) -> csc_array:
        """Compile row dictionaries directly to CSC and reuse the result.

        Each row is already a dictionary, so column indices are unique within
        that row.  Building CSR indptr/indices/data arrays directly avoids the
        three large Python coordinate lists and the COO-to-CSR conversion that
        previously dominated matrix preparation on 8760-hour replays.
        """

        started = perf_counter()
        if self._compiled_matrix is not None:
            self.last_matrix_compile_seconds = perf_counter() - started
            self.last_matrix_cache_hit = True
            return self._compiled_matrix

        row_count = len(self.rows)
        lengths = np.fromiter(
            (len(row) for row in self.rows),
            dtype=np.int64,
            count=row_count,
        )
        indptr = np.empty(row_count + 1, dtype=np.int64)
        indptr[0] = 0
        np.cumsum(lengths, out=indptr[1:])
        nonzeros = int(indptr[-1])
        indices = np.fromiter(
            (index for row in self.rows for index in row),
            dtype=np.int32,
            count=nonzeros,
        )
        values = np.fromiter(
            (value for row in self.rows for value in row.values()),
            dtype=np.float64,
            count=nonzeros,
        )
        matrix = csr_array(
            (values, indices, indptr),
            shape=(row_count, len(self.names)),
        ).tocsc()
        self._compiled_matrix = matrix
        self.last_matrix_compile_seconds = perf_counter() - started
        self.last_matrix_cache_hit = False
        return matrix

    def solve(self, mip_gap: float = 0.005, time_limit: float | None = 120.0):
        matrix = self.constraint_matrix()
        if self._compiled_constraints is None:
            self._compiled_constraints = LinearConstraint(
                matrix,
                np.asarray(self.row_lower),
                np.asarray(self.row_upper),
            )
        if self._compiled_bounds is None:
            self._compiled_bounds = Bounds(
                np.asarray(self.lower), np.asarray(self.upper)
            )
        if self._compiled_integrality is None:
            self._compiled_integrality = np.asarray(self.integrality)
        options: dict[str, float | bool] = {"mip_rel_gap": mip_gap, "disp": False}
        if time_limit is not None:
            options["time_limit"] = float(time_limit)
        self._last_solve_objective = np.asarray(self.objective, dtype=float).copy()
        self._last_solve_fun = None
        result = milp(
            c=self._last_solve_objective,
            integrality=self._compiled_integrality,
            bounds=self._compiled_bounds,
            constraints=self._compiled_constraints,
            options=options,
        )
        if result.fun is not None:
            self._last_solve_fun = float(result.fun)
        return result
