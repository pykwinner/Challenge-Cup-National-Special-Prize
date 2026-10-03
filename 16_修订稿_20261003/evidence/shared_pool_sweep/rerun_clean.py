"""干净重跑（顺序：先快的 a55/a650，a40 最后）。避免并发抢写同一结果文件。"""
import sys, json, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_pool_sweep as R

PEAK = 1081.5311191794565
JOBS = [("a55", PEAK * 0.55), ("a650", 650.0), ("a40", PEAK * 0.40)]
for tag, a in JOBS:
    config = R.write_variant(tag, a)
    manifest = R.write_manifest(tag, config)
    out = R.HERE / f"run_{tag}"
    cmd = [sys.executable, "-m", "heatplan.annual_replay_batch_cli",
           "--manifest", str(manifest), "--output", str(out), "--wall-time-limit", "3600"]
    env = {"PYTHONPATH": str(R.ROOT / "src"), **dict(os.environ)}
    proc = subprocess.run(cmd, cwd=R.ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    (R.HERE / f"log_{tag}.txt").write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    rec = {"tag": tag, "available_heat_kw": a, "return_code": proc.returncode}
    pdir = out / f"urumqi_pool_{tag}"
    s = pdir / "annual_replay_summary.json"
    if s.exists():
        d = json.loads(s.read_text(encoding="utf-8"))
        rec.update({k: d.get(k) for k in (
            "planning_solver_status", "replay_solver_status", "replay_unserved_rate",
            "annual_replay_certified", "acceptance_failed_gates", "replay_solve_seconds")})
        for label, p in (("planning", pdir / "representative_planning" / "summary.json"),
                         ("replay", pdir / "fixed_investment_annual_replay" / "summary.json")):
            if p.exists():
                x = json.loads(p.read_text(encoding="utf-8"))
                rec[f"{label}_objective"] = x.get("objective_value")
                rec[f"{label}_dual_bound"] = x.get("mip_dual_bound")
                rec[f"{label}_gap"] = x.get("mip_gap")
                rec[f"{label}_emissions_kg"] = x.get("emissions_kg_per_year")
        rec["observed_peak_upstream_kw"] = R.peak_pool_use(pdir)
    (R.HERE / f"result_{tag}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(tag, rec.get("return_code"), rec.get("replay_objective"), rec.get("replay_emissions_kg"),
          rec.get("replay_gap"), rec.get("annual_replay_certified"), flush=True)
