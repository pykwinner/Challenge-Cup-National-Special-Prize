"""Plot frozen feedback iterations and the non-improving soft-penalty control."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_evidence_case_experiments import save, sha
from verify_network_feedback_reference import audit_saved


def report(source, out):
    if out.exists():
        raise ValueError("preserve prior report")
    audit = audit_saved(source)
    if not audit["passed"]:
        raise ValueError("saved reference failed verification")
    out.mkdir(parents=True)
    history = json.loads((source / "results/soft_off/history.json").read_text(encoding="utf-8"))
    comparison = json.loads((source / "comparison.json").read_text(encoding="utf-8"))
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2), constrained_layout=True)
    rounds = [r["iteration"] + 1 for r in history]
    pressure = [r["audit"]["maximum_active_pressure_drop_pa_per_m"] for r in history]
    cost = [r["objective_yuan"] for r in history]
    colors = ["#c66b47" if r["status"] == "rejected" else "#19786c" for r in history]
    axes[0].bar(rounds, pressure, color=colors)
    axes[0].axhline(300, color="#263748", ls="--", label="Hard limit: 300 Pa/m")
    axes[0].set(title="Feedback removes hydraulic overload", xlabel="Solve iteration", ylabel="Maximum active on-state pressure loss (Pa/m)", xticks=rounds)
    axes[0].legend(fontsize=8)
    axes[1].bar(rounds, cost, color=colors)
    axes[1].set(title="Engineering feasibility has a cost", xlabel="Solve iteration", ylabel="72-hour reference electricity bill (yuan)", xticks=rounds)
    for x, y in zip(rounds, cost):
        axes[1].text(x, y + max(cost)*.015, f"{y:.2f}", ha="center", fontsize=9)
    axes[1].set_ylim(0, max(cost)*1.16)
    axes[2].bar(["Penalty off", "Penalty 0.2"], [r["delta_t_excess_degree_on_hours"] for r in comparison], color=["#617c99", "#19786c"])
    axes[2].set(title="Soft penalty: retained negative result", ylabel="Excess delta-T (degree x on-hours)")
    axes[2].text(.5, .82, "Same selected schedule\nat the frozen penalty", transform=axes[2].transAxes, ha="center", fontsize=10)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.15)
        ax.set_axisbelow(True)
    fig.suptitle("Designed two-branch network | Urumqi weather | common reference prices\nFinite steady modes; not a measured Bayi network", fontsize=12)
    for suffix in ("png", "svg"):
        fig.savefig(out / f"network_feedback.{suffix}", dpi=170, facecolor="white")
    plt.close(fig)
    save(out / "mechanisms.json", {"initial_pressure_pa_per_m": pressure[0], "final_pressure_pa_per_m": pressure[-1],
        "initial_bill_yuan": cost[0], "final_bill_yuan": cost[-1], "bill_increase_percent": 100*(cost[-1]/cost[0]-1),
        "final_minimum_far_supply_c": comparison[0]["minimum_active_far_supply_c"],
        "soft_penalty_changed_schedule": False, "field_validation": False})
    save(out / "manifest.json", {"source_manifest_sha256": sha(source / "manifest.json"), "export_audit_passed": audit["passed"],
        "files": {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}})


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    report(args.input.resolve(), args.out.resolve())
