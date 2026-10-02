"""Read-only evidence publisher for the 2026-10-01 competition work.

No model is imported or solved. Missing evidence is never converted to zero.
The default creates explicitly pending material. Binding numerical results requires
the C0-selected manifest and a declarative evidence spec with file hashes.
All writes stay in outputs/competition_20261001/C5 and the two assigned C5 docs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/competition_20261001/C5"
DOCS = ROOT / "docs/execution_20261001"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def pointer(value, path: str):
    for token in path.strip("/").split("/") if path else []:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def checked_file(entry: dict) -> Path:
    path = Path(entry["path"])
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file() or sha(path) != entry["sha256"]:
        raise ValueError(f"Evidence missing or hash changed: {path}")
    return path


def bind_evidence(manifest: Path | None, spec_path: Path | None) -> dict:
    """Explicit source selection, no recursive search for a convenient result.

    spec: {manifest_sha256, selected_by:'C0', scope_statement, metrics:[
      {label,unit,source:{path,sha256},pointer:'/summary/cost'} OR
      {label,unit,source:{path,sha256},column:'cost',row:0}],
      tables:[{title,source:{path,sha256},columns:[{field,label}]}],
      assertions:[{label,source:{path,sha256},pointer:'/passed',expected:true}]}
    Assertions are displayed verbatim as checks, never used to fabricate an
    overall certification. Formal interpretation remains a reviewed C0 decision.
    """
    if manifest is None:
        if spec_path:
            raise ValueError("An evidence spec requires --manifest")
        return {"status": "pending_manifest", "metrics": [], "tables": [], "checks": []}
    if spec_path is None:
        raise ValueError("--manifest requires --evidence-spec with explicit bindings")
    spec = read_json(spec_path)
    if spec.get("selected_by") != "C0":
        raise ValueError("Only the C0-selected result batch may enter this report")
    if sha(manifest) != spec["manifest_sha256"]:
        raise ValueError("Selected manifest hash differs")
    manifest_data = read_json(manifest)
    if manifest_data.get("complete") is not True:
        raise ValueError("Selected manifest has no complete:true publication marker")
    if manifest_data.get("status") == "data_prepared_not_solver_certified":
        raise ValueError("A data manifest cannot stand in for a solver result manifest")
    result = {"status": "evidence_bound", "manifest_path": str(manifest.resolve()),
              "manifest_sha256": sha(manifest), "manifest_status": manifest_data.get("status"),
              "scope_statement": spec["scope_statement"], "metrics": [], "tables": [], "checks": []}
    for item in spec.get("metrics", []):
        path = checked_file(item["source"])
        if "pointer" in item:
            value = pointer(read_json(path), item["pointer"])
        else:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            value = rows[item["row"]][item["column"]]
        if value is None or value == "":
            raise ValueError(f"Missing metric: {item['label']}")
        if item.get("type", "number") == "number":
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite metric: {item['label']}")
        result["metrics"].append({"label": item["label"], "value": value, "unit": item["unit"],
                                   "path": str(path), "sha256": sha(path), "locator": item.get("pointer", f"row {item.get('row')} / {item.get('column')}")})
    for item in spec.get("tables", []):
        path = checked_file(item["source"])
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        selected = [[row[col["field"]] for col in item["columns"]] for row in rows]
        if not selected:
            raise ValueError(f"Empty result table: {path}")
        result["tables"].append({"title": item["title"], "headers": [c["label"] for c in item["columns"]],
                                  "rows": selected, "path": str(path), "sha256": sha(path)})
    for item in spec.get("assertions", []):
        path = checked_file(item["source"])
        value = pointer(read_json(path), item["pointer"])
        result["checks"].append({"label": item["label"], "observed": value, "expected": item["expected"],
                                  "passed": value == item["expected"], "source": str(path), "pointer": item["pointer"]})
    return result


def build_result_visuals(spec_path: Path, revision: str) -> dict:
    """Publish plots only from C0's explicit immutable run selection.

    The evidence spec may contain plot_runs:[{role,label,manifest:{path,sha256}}],
    plot_frontier:{manifest:{path,sha256},csv:'nondominated.csv',cost_column,
    carbon_column}, and comparison_metrics:[{label,unit,pointer}].
    plot_runs with role 'optimized_annual' and 'baseline_annual' are compared only
    after service_contract_sha256 equality. No income or return is inferred.
    Every plotted data series is exported to CSV with a source receipt. Figure
    PNG/PDF files serve the report; chart_payloads.json retains full values for
    native editable PowerPoint charts. Nothing scans result folders implicitly.
    """
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.font_manager import FontProperties
    spec = read_json(spec_path)
    if spec.get("selected_by") != "C0":
        raise ValueError("Plots require C0 result selection")
    directory = OUT / "plots" / revision
    directory.mkdir(parents=True, exist_ok=False)
    cn_font = FontProperties(fname="C:/Windows/Fonts/msyh.ttc")
    plt.rcParams.update({"font.family": cn_font.get_name(), "axes.unicode_minus": False,
                         "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "savefig.dpi": 170, "axes.titleweight": "bold"})
    colors = ["#126E78", "#D78336", "#487DB0", "#A06082", "#6C8A4A", "#796DA8"]
    receipts, payloads, runs = [], [], {}

    def verified_run(item):
        path = checked_file(item["manifest"])
        data = read_json(path)
        if data.get("complete") is not True or not isinstance(data.get("files"), dict):
            raise ValueError("Plot run requires a complete C4 files manifest")
        for rel, expected in data["files"].items():
            file = (path.parent / rel).resolve()
            if not file.is_relative_to(path.parent.resolve()) or sha(file) != expected:
                raise ValueError(f"Run file identity mismatch: {rel}")
        if "summary.json" in data["files"]:
            summary = read_json(path.parent / "summary.json")
            if not summary.get("runner_audit") or not all(summary["runner_audit"].values()):
                raise ValueError("A plot run did not pass its recorded acceptance checks")
        def csvfile(name):
            if name not in data["files"]:
                raise ValueError(f"Required CSV absent from manifest: {name}")
            return pd.read_csv(path.parent / name)
        def jsonfile(name):
            if name not in data["files"]:
                raise ValueError(f"Required JSON absent from manifest: {name}")
            return read_json(path.parent / name)
        return {"manifest": data, "path": path, "csv": csvfile, "json": jsonfile,
                "role": item["role"], "label": item.get("label", item["role"])}

    def finite(frame, columns):
        for column in columns:
            values = pd.to_numeric(frame[column], errors="raise").to_numpy(float)
            if not np.isfinite(values).all():
                raise ValueError(f"Nonfinite plot field: {column}")

    def save_figure(fig, stem, title, sources, facts, data_frame):
        fig.suptitle(title, x=.08, ha="left", fontsize=14, color="#123543")
        footer_y = .014
        fig.text(.08, footer_y + 14 / (fig.get_figheight() * 72), "求解来源：" + ", ".join(sources) + "；气象：NASA POWER公开产品", fontsize=8)
        fig.text(.08, footer_y, "建筑/价格等尚未完成溯源；仅假设原型程序验证，非实测或企业效果", fontsize=8)
        fig.tight_layout(rect=(0, .09, 1, .94))
        png = directory / f"{stem}.png"
        fig.savefig(png)
        fig.savefig(directory / f"{stem}.pdf")
        plt.close(fig)
        data_frame.to_csv(directory / f"{stem}.csv", index=False, encoding="utf-8-sig")
        receipts.append({"id": stem, "title": title, "png": str(png), "sources": sources,
                         "facts": facts, "data_sha256": sha(directory / f"{stem}.csv"),
                         "png_sha256": sha(png), "visual_review": "pending"})

    def line_payload(stem, title, frame, columns, unit, x="local_time"):
        payloads.append({"id": stem, "title": title, "type": "line", "unit": unit,
                         "categories": frame[x].astype(str).tolist(),
                         "series": [{"name": label, "values": frame[field].astype(float).tolist(),
                                     "line": {"fill": colors[i % len(colors)], "width": 2}}
                                    for i, (field, label) in enumerate(columns)]})

    for item in spec.get("plot_runs", []):
        run = verified_run(item)
        if item["role"] in runs:
            raise ValueError("Duplicate plot role")
        runs[item["role"]] = run
        meta, role = run["manifest"], run["role"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", role):
            raise ValueError("Unsafe plot role")
        scope = meta.get("formal_scope")
        if scope == "representative":
            source_id = run["path"].parent.name
            investments = run["csv"]("investments.csv")
            finite(investments, ["value"])
            capacities = investments[(investments.category == "source_capacity") & (investments.unit == "kW")].copy()
            if capacities.empty:
                raise ValueError("Representative source capacity table is empty")
            fig, ax = plt.subplots(figsize=(10.5, 5.3))
            ax.barh(capacities.asset, capacities.value, color=colors[0])
            ax.set(xlabel="新建热源容量 kW")
            save_figure(fig, f"{role}_investment", "代表期规划的新建热源容量", [source_id], {"scope": "representative_investment_not_full_year_certificate", "hours": meta["hours"], "weighted_hours": meta["weighted_hours"]}, capacities)
            payloads.append({"id": f"{role}_investment", "title": "代表期新建热源容量", "type": "bar", "unit": "kW", "xTitle": "候选热源", "categories": capacities.asset.tolist(), "series": [{"name": "新建容量", "values": capacities.value.tolist(), "fill": colors[0]}]})
            summary = run["json"]("summary.json")
            parts = pd.DataFrame([{"component": k, "yuan_equivalent_year": v} for k, v in summary["components_yuan_per_year"].items()])
            finite(parts, ["yuan_equivalent_year"])
            labels = {"annualized_capex": "资本年值", "operating_cost": "制热运行", "carbon_cost": "制热碳费", "cooling_operating_cost": "制冷运行", "cooling_carbon_cost": "制冷碳费", "exergy_mismatch_cost": "品质偏好", "reliability_penalty": "可靠性惩罚"}
            parts["label"] = parts.component.map(lambda x: labels.get(x, x))
            fig, ax = plt.subplots(figsize=(9.7, 4.6))
            ax.bar(parts.label, parts.yuan_equivalent_year/10000, color=colors[0])
            ax.set(ylabel="万元/年等效（规划目标分量）")
            save_figure(fig, f"{role}_cost_components", "代表期规划目标成本构成", [source_id], {"annual_equivalent_not_actual_annual_run": True, "objective_contains_enabled_penalties": True}, parts)
            payloads.append({"id": f"{role}_cost_components", "title": "代表期规划目标分量", "type": "bar", "unit": "万元/年等效", "categories": parts.label.tolist(), "series": [{"name": "目标分量", "values": (parts.yuan_equivalent_year/10000).tolist(), "fill": colors[0]}]})
            profile = run["json"]("model_profile.json")
            stages = ["build_seconds", "solve_and_extract_seconds", "export_seconds", "audit_seconds"]
            timing = pd.DataFrame({"stage": ["建模", "求解与提取", "导出", "审计"], "seconds": [profile[k] for k in stages]})
            finite(timing, ["seconds"])
            fig, ax = plt.subplots(figsize=(8.5, 4.2))
            ax.barh(timing.stage, timing.seconds, color=colors[0])
            ax.set(xlabel="墙钟秒（本次代表期实测）")
            save_figure(fig, f"{role}_timing", "代表期运行分段耗时", [source_id], {"not_full_year_speed_prediction": True, "wall_seconds": profile["wall_seconds"], "variables": profile["variables"], "constraints": profile["constraints"], "matrix_nonzeros": profile["matrix_nonzeros"]}, timing)
            payloads.append({"id": f"{role}_timing", "title": "代表期运行分段耗时", "type": "bar", "unit": "秒", "categories": timing.stage.tolist(), "series": [{"name": "墙钟耗时", "values": timing.seconds.tolist(), "fill": colors[0]}]})
        if scope not in {"annual", "season"}:
            # Frontier representative points are handled separately; they cannot
            # be relabelled as continuous-year calendars or extreme weeks.
            continue
        traj, dispatch, times = run["csv"]("building_trajectory.csv"), run["csv"]("dispatch.csv"), run["csv"]("model_timeseries.csv")
        config, summary = run["json"]("model_config.json"), run["json"]("summary.json")
        finite(traj, ["time_index", "dt_hours", "period_weight", "heat_kw", "cooling_kw", "temperature_end_c", "min_temperature_c", "max_temperature_c", "outdoor_temperature_c"])
        finite(dispatch, ["time_index", "value"])
        times["local_time"] = pd.to_datetime(times["time"], utc=True).dt.tz_convert("Asia/Shanghai")
        count = len(times)
        if count != meta["hours"] or len(times.local_time.unique()) != count or not (times.local_time.diff().dropna() == pd.Timedelta(hours=1)).all():
            raise ValueError("Continuous 1h plot scope missing, duplicated or inconsistent")
        if not np.allclose(traj.dt_hours, 1) or not np.allclose(traj.period_weight, 1):
            raise ValueError("Full-period plots require dt=1h and unit period weights")
        names = [b["name"] for b in config["building_thermal"]["buildings"]]
        if set(traj.building) != set(names) or traj.duplicated(["building", "time_index"]).any():
            raise ValueError("Trajectory building coverage mismatch")
        for _, group in traj.groupby("building"):
            if sorted(group.time_index.tolist()) != list(range(count)):
                raise ValueError("Incomplete building time grid")
        if traj.groupby("time_index").outdoor_temperature_c.nunique().max() != 1:
            raise ValueError("Building outdoor inputs disagree")
        time_axis = times.local_time.reset_index(drop=True)
        aggregate = traj.groupby("time_index").agg(heat_kw=("heat_kw", "sum"), cooling_kw=("cooling_kw", "sum"), outdoor_c=("outdoor_temperature_c", "first")).reindex(range(count))
        aggregate["local_time"] = time_axis
        runs[role]["aggregate"] = aggregate
        if not role.startswith("optimized"):
            continue
        source_id = run["path"].parent.name
        # Exact sorted curves contain every original hourly observation.
        duration = pd.DataFrame({"rank_hour": np.arange(1, count+1), "heat_kw": np.sort(aggregate.heat_kw)[::-1], "cooling_kw": np.sort(aggregate.cooling_kw)[::-1]})
        fig, ax = plt.subplots(figsize=(9.4, 4.5))
        for col, label, color in [("heat_kw", "实际送达热量", colors[0]), ("cooling_kw", "实际送达冷量", colors[1])]:
            ax.plot(duration.rank_hour, duration[col], label=label, color=color)
        ax.set(xlabel="累计小时（独立排序）", ylabel="kW")
        ax.legend(frameon=False)
        save_figure(fig, f"{role}_duration", "实际冷热负荷持续曲线", [source_id], {"scope": scope, "hours": count, "sorted_independently": True}, duration)
        line_payload(f"{role}_duration", "实际冷热负荷持续曲线", duration, [("heat_kw", "实际供热"), ("cooling_kw", "实际制冷")], "kW", "rank_hour")
        # Missing date/hour cells stay masked, never silently filled with zeros.
        calendar = aggregate.copy()
        calendar["date"] = calendar.local_time.dt.strftime("%Y-%m-%d")
        calendar["hour"] = calendar.local_time.dt.hour
        fig, axes = plt.subplots(1, 2, figsize=(10.6, 5.5))
        for ax, column, title in zip(axes, ["heat_kw", "cooling_kw"], ["送达热量", "送达冷量"]):
            grid = calendar.pivot(index="date", columns="hour", values=column).reindex(columns=range(24))
            im = ax.imshow(np.ma.masked_invalid(grid.to_numpy()), aspect="auto", interpolation="nearest", cmap="YlOrRd" if column == "heat_kw" else "Blues", vmin=0)
            step = max(1, len(grid)//7)
            ticks = list(range(0, len(grid), step))
            ax.set_yticks(ticks, [grid.index[i] for i in ticks])
            ax.set_xticks([0, 6, 12, 18, 23])
            ax.set(xlabel="北京时间小时", title=title)
            fig.colorbar(im, ax=ax, label="kW", shrink=.8)
        save_figure(fig, f"{role}_calendar", "冷热服务日历", [source_id], {"scope": scope, "hours": count, "missing_cells_masked": True}, calendar)
        # Extreme week means the lowest/highest rolling mean outdoor temperature.
        if count < 168:
            raise ValueError("Continuous extreme-week plot needs at least 168 hours")
        rolling = aggregate.outdoor_c.rolling(168).mean()
        for tag, end in [("cold", int(rolling.idxmin())), ("hot", int(rolling.idxmax()))]:
            start = end-167
            week = aggregate.iloc[start:end+1].copy()
            selected = traj[traj.time_index.between(start, end)].sort_values(["building", "time_index"])
            selected = selected.merge(times[["local_time"]].reset_index(names="time_index"), on="time_index", validate="many_to_one")
            selected["state_time"] = selected.local_time + pd.Timedelta(hours=1)
            fig, axes = plt.subplots(len(names), 1, figsize=(10.5, 2.0*len(names)), sharex=True)
            for ax, name, color in zip(np.atleast_1d(axes), names, colors):
                b = selected[selected.building == name]
                ax.fill_between(b.state_time, b.min_temperature_c, b.max_temperature_c, color="#DCEAEA", label="时段末舒适范围")
                ax.plot(b.state_time, b.temperature_end_c, color=color, label="时段末室温")
                ax.set(ylabel="℃", title=name)
            np.atleast_1d(axes)[0].legend(frameon=False, ncol=2, loc="upper left", fontsize=9)
            np.atleast_1d(axes)[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=time_axis.dt.tz))
            label = "最冷" if tag == "cold" else "最热"
            facts = {"selection": f"{tag}_rolling_168h_mean_outdoor", "start": str(week.local_time.iloc[0]), "end_exclusive": str(week.local_time.iloc[-1] + pd.Timedelta(hours=1)), "hour_count": 168, "temperature_axis": "interval_end_state_time", "dispatch_axis": "interval_start_average_power"}
            save_figure(fig, f"{role}_{tag}_temperature", f"{label}连续周楼宇室温", [source_id], facts, selected)
            for name in names:
                b = selected[selected.building == name]
                line_payload(f"{role}_{tag}_{name}", f"{label}周 {name} 室温", b, [("temperature_end_c", "室温"), ("min_temperature_c", "下限"), ("max_temperature_c", "上限")], "℃", "state_time")
            window = dispatch[dispatch.time_index.between(start, end)]
            fig, axes = plt.subplots(3, 1, figsize=(10.5, 7.2), sharex=True)
            plot_data = week.copy()
            for category, ax, y_label in [("source_heat", axes[0], "热源出力 kW"), ("cooling_output", axes[1], "制冷出力 kW"), ("storage_soc", axes[2], "库存 kWh")]:
                part = window[window.category == category]
                if part.empty:
                    ax.text(.02, .45, "该运行未配置此类变量", transform=ax.transAxes)
                else:
                    pivot = part.pivot_table(index="time_index", columns="asset", values="value", aggfunc="sum").reindex(week.index)
                    if pivot.isna().any().any():
                        raise ValueError(f"Missing {category} trajectory")
                    for j, asset in enumerate(pivot):
                        ax.plot(week.local_time, pivot[asset], label=asset, color=colors[j % len(colors)])
                        plot_data[f"{category}:{asset}"] = pivot[asset]
                    ax.legend(frameon=False, ncol=3, fontsize=8)
                ax.set(ylabel=y_label)
            axes[0].plot(week.local_time, week.heat_kw, "--", color="#263A48", label="楼宇送达（不含网损）")
            axes[0].legend(frameon=False, ncol=3, fontsize=8)
            axes[1].plot(week.local_time, week.cooling_kw, "--", color="#263A48", label="楼宇实际冷量")
            axes[1].legend(frameon=False, ncol=3, fontsize=8)
            axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=time_axis.dt.tz))
            save_figure(fig, f"{role}_{tag}_dispatch", f"{label}连续周源储调度", [source_id], {**facts, "gross_source_vs_delivered_includes_network_loss": True}, plot_data)
            power_columns = [(c, c.split(":", 1)[-1]) for c in plot_data if c.startswith("source_heat:")]
            if power_columns:
                line_payload(f"{role}_{tag}_heat_dispatch", f"{label}周热源调度", plot_data, power_columns + [("heat_kw", "楼宇送达")], "kW")
        # Same delivered service appears at every hierarchy level. Compare it,
        # never add building+node+system energy as a new total.
        hierarchy = run["csv"]("delivered_heat_hierarchy.csv")
        finite(hierarchy, ["time_index", "actual_delivered_heat_kw"])
        layers = pd.DataFrame({"local_time": time_axis, "building_kw": aggregate.heat_kw})
        for level in ["node", "district", "system"]:
            layer = hierarchy[hierarchy.level == level].groupby("time_index").actual_delivered_heat_kw.sum().reindex(range(count))
            if layer.isna().any():
                raise ValueError(f"Missing {level} aggregation")
            layers[f"{level}_kw"] = layer
        residual_columns = []
        for level in ["node", "district", "system"]:
            col = f"{level}_minus_building_kw"
            layers[col] = layers[f"{level}_kw"] - layers.building_kw
            residual_columns.append(col)
        fig, axes = plt.subplots(2, 1, figsize=(10.5, 5.7), sharex=True)
        axes[0].plot(time_axis, layers.building_kw, color=colors[0], label="楼宇总送达")
        axes[0].plot(time_axis, layers.system_kw, "--", color=colors[1], label="系统聚合视图")
        for i, col in enumerate(residual_columns):
            axes[1].plot(time_axis, layers[col], label=col.replace("_minus_building_kw", ""), color=colors[i])
        axes[0].set(ylabel="kW")
        axes[1].set(ylabel="聚合差额 kW", xlabel="北京时间")
        for ax in axes:
            ax.legend(frameon=False, ncol=3)
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m", tz=time_axis.dt.tz))
        save_figure(fig, f"{role}_hierarchy", "楼宇、节点、片区与系统送达守恒", [source_id], {"max_absolute_difference_kw": float(layers[residual_columns].abs().to_numpy().max()), "not_source_network_balance_certificate": True}, layers)
        line_payload(f"{role}_hierarchy", "层级聚合差额", layers, [(c, c.replace("_minus_building_kw", "")) for c in residual_columns], "kW")

    if frontier := spec.get("plot_frontier"):
        selection = dict(frontier, role="frontier")
        run = verified_run(selection)
        points = run["csv"](frontier.get("csv", "nondominated.csv"))
        cost, carbon = frontier["cost_column"], frontier["carbon_column"]
        finite(points, [cost, carbon])
        if points.empty:
            raise ValueError("Cannot plot empty Pareto samples")
        ordered = points.sort_values(carbon)
        fig, ax = plt.subplots(figsize=(8.8, 4.8))
        ax.scatter(ordered[carbon]/1000, ordered[cost]/10000, color=colors[0], s=55)
        ax.set(xlabel="总服务运营排放 tCO2", ylabel="目标成本 万元/年")
        save_figure(fig, "pareto_samples", "采样可行解的非支配子集", [run["path"].parent.name], {"scope": run["manifest"].get("scope", "以前沿manifest为准"), "point_count": len(points), "continuous_frontier_not_claimed": True}, points)
        payloads.append({"id": "pareto_samples", "title": "成本碳采样非支配点", "type": "scatter", "unit": "万元/年", "categories": [], "series": [{"name": "非支配采样点", "xValues": (ordered[carbon]/1000).tolist(), "values": (ordered[cost]/10000).tolist(), "marker": {"symbol": "circle", "size": 8}, "fill": colors[0]}]})
    if "optimized_annual" in runs and "baseline_annual" in runs:
        opt, base = runs["optimized_annual"], runs["baseline_annual"]
        for key in ["service_contract_sha256", "source_sha256", "spatial_sha256", "code_sha256"]:
            if not opt["manifest"].get(key) or opt["manifest"][key] != base["manifest"].get(key):
                raise ValueError(f"Cost/carbon comparison has different {key}")
        metrics = spec.get("comparison_metrics", [])
        if not metrics:
            raise ValueError("Annual comparison needs explicit metric pointers and units")
        rows = []
        for run in [base, opt]:
            summary = run["json"]("summary.json")
            row = {"方案": run["label"]}
            for metric in metrics:
                value = pointer(summary, metric["pointer"])
                if value is None or not math.isfinite(float(value)):
                    raise ValueError("Comparison metric absent/nonfinite")
                row[metric["label"]] = float(value)
            rows.append(row)
        comparison = pd.DataFrame(rows)
        fig, axes = plt.subplots(1, len(metrics), figsize=(4.3*len(metrics), 4.6), squeeze=False)
        for ax, metric in zip(axes[0], metrics):
            ax.bar(comparison["方案"], comparison[metric["label"]], color=colors[:2])
            ax.set(title=metric["label"], ylabel=metric["unit"])
        save_figure(fig, "same_service_comparison", "同服务年度成本碳对比", [base["path"].parent.name, opt["path"].parent.name], {"service_contract_sha256": opt["manifest"]["service_contract_sha256"], "capital_not_added_twice": True}, comparison)
        for metric in metrics:
            payloads.append({"id": "comparison_" + metric["label"], "title": metric["label"], "type": "bar", "unit": metric["unit"], "categories": comparison["方案"].tolist(), "series": [{"name": metric["label"], "values": comparison[metric["label"]].tolist(), "fill": colors[0]}]})
    result = {"status": "generated_from_C0_selected_manifests", "spec_sha256": sha(spec_path), "figures": receipts,
              "sources": [{"role": role, "manifest": str(r["path"]), "sha256": sha(r["path"])} for role, r in runs.items()]}
    (directory / "plot_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "chart_payloads.json").write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


RUBRICS = [
    ("模型架构构建", 20, "独立完成涵盖“源-网-荷-储”全环节的数学模型构建，物理逻辑清晰，能够准确描述供热系统的能量流动与转换过程。", "4",
     "model.py::build；building_thermal.py::add_building_thermal_constraints；spatial_quality.py::validate_source_grades / validate_conversion_dispatch",
     "候选 YAML 继承链、四楼宇主表、S3 空间包和逐时气象。", "C2 解析降温、断供恢复、供热不足不可行及冷热互斥；C3 温级与转换反例。主模型已读到实际热冷映射接入，最终按冻结版本复核。",
     "模块已验收；全系统闭环待完整运行", "完整调度、楼宇温度、源网储账本和独立残差同时通过。"),
    ("动态仿真能力", 15, "模型需具备动态特性，能够实现供暖季逐时（供暖季时长）供需平衡计算。未能覆盖完整供暖季时间尺度或时间分辨率不足，至多扣除 15 分。", "4",
     "building_thermal.py；model.py 储热递推；chronology.py::explicit_annual_scope_audit / replay_prepared_fixed_investment_plan；heating_season.py",
     "2928h 供暖季、8784h 服务年，1h、UTC 内部时钟和北京时间服务期。", "C1 连续性、闰日、边缘与单位 QA；C2 全量输入校验。数据 complete 不含 solver 认证。",
     "时间数据已验收；连续季/年待完整运行", "固定一次投资、完整小时、所有状态连续和初末态通过。静态管网不含水力迟滞。"),
    ("场景验证完整度", 15, "需完整完成三大基础场景中至少一个场景的案例分析。模型缺少必要的约束条件（如室内温度约束、热源出力上下限等）或关键要素缺失，每项至多扣除 5 分。", "4-5",
     "S3 候选配置；model.py 出力/舒适/资源/电网/政策；heating_season.py 季节边界；chronology.py 年度验收",
     "武汉公开气象和三类合成建筑，保留 ASHP/GSHP/电锅炉/太阳热/PCM/管道/电网。", "C1/C2/C3 专项及短窗适配器仅支持输入和局部模块。S1/S2 原有配置不等于本轮完整案例。",
     "主案例待完整运行", "同一投资的冬季、全年地源账本和同服务基准；冻结代码 S1/S2 回归。"),
    ("多目标协同与收敛性", 15, "算法需具备多目标协同能力，在边界条件变化时能稳定输出收敛的最优解。算法无法收敛或计算效率过低，视严重程度扣分。", "5",
     "linear.py::constraint_matrix / audit_solution；model.py::total_service_emissions_expression / solve_lexicographic；experiments.py::epsilon_constraint_pareto / nondominated_cost_emission_points",
     "同一冷热服务、成本碳边界、技术候选、碳帽和求解预算。", "已读到两阶段词典序、统一排放表达式、ε采样和去重/支配筛选代码。本轮正式端点与耗时尚待指定结果。",
     "方法代码已有；本轮收敛与效率待运行", "有效端点、非支配点、边界变化、状态/gap/对偶界及端到端耗时。"),
    ("多尺度耦合特性", 10, "有效体现城市级、区域级、用户级的耦合调度特性，实现多尺度下的系统能效最优。", "5",
     "building_thermal.py 每楼状态；spatial_quality.py::build_hierarchy / aggregate_delivered_power；model.py 区域平衡及共享电力/投资",
     "唯一楼宇归属、独立 H/C、占用、舒适、节点/片区/系统层级。", "C2 异质响应，C3 唯一归属负例和逐层守恒已验收。",
     "模块已验收；完整跨层受控对照待运行", "改变楼宇 H/C 或舒适会改变片区调度、上层电力或投资；服务比较边界一致。"),
    ("结果可视化与分析深度", 15, "仿真结果应当具备直观的图表展示（如负荷持续曲线、源荷匹配热力图、系统能流图等），图表排版美观专业；能够基于数据对规划方案的经济性（投资回报）、环保性（碳减排）及可行性进行深入分析，鼓励进行多方案对比分析或敏感性分析，逻辑自洽，结论具有决策参考价值。", "5",
     "reporting.py；finance.py::evaluate_project_finance / break_even_tariff；scripts/build_competition_deliverables.py/.mjs",
     "C0 指定完成 manifest、真实汇总/投资/调度/温度/SOC/审计 JSON 与 CSV。", "本轮材料骨架可生成；未收到正式结果，不绘制虚构收益图。题面所列图型是举例。",
     "结果分析待完整运行与审核", "同批次投资成本碳表、困难时段温度与调度、持续曲线/前沿；收入无依据时不报 IRR。"),
    ("代码质量与可移植性", 10, "算法代码应满足交付标准：结构规范：具备清晰的目录结构与模块划分，主程序与函数库分离。注释充分：核心逻辑段落需有详尽注释，变量命名规范，可读性强。复用性强：代码封装良好，配置参数与核心逻辑解耦，便于后续集成至专业规划平台。", "5",
     "src/heatplan、CLI、data、tests；scripts/prepare_competition_case.py；专属模块和接口文档",
     "冻结环境、完整源码配置输入输出与文件哈希。", "C1 可离线重建并在失败时撤销完成标记；C2/C3 单模块和负例证据。17/63/46 不是修改后全项目回归总数。",
     "专项工程已有；发布复现待验收", "安装、入口、统一回归、短例复现、结果独立复算及最终包清单一致。"),
]

LIMITS = [
    "气象为 NASA POWER 格点/再分析输入，UTC/LST 小样本仅检验时钟与单位，未证明当地站点气象精度。",
    "建筑 H/C、占用、内得热、太阳有效孔口与空间坐标是透明合成假设，未做武汉室温或热表参数辨识。",
    "一阶 RC 仅含单显热状态和时段末舒适，不含湿度、潜热、多节点围护及小时内温控；初始20℃预调节置于比较边界外，末态须恢复。",
    "网络是静态 kW 输热，原型距离改变接入与投资，但没有真实 GIS、地下管线、逐户支线、水力或变流热输运迟滞。",
    "品质采用温级准入与热泵电耗账本，未实现跨节点串联提温或完整燃料/电/环境热输入边界的全系统㶲优化。",
    "电价为0.30-1.12元/kWh合成情景。2023湖北平均0.4044kgCO₂/kWh用于2023/24天气是规划情景，非逐时边际或完整生命周期碳。",
    "有限 ε 点仅形成采样可行解的非支配子集；距归一化理想点最近不等于严格曲率膝点。限时可行解须披露 gap。",
    "独立循环代表日不能认证夏充冬放。完整年地源恢复、连续冷段与固定投资回放必须单独通过。",
    "模块测试、数学残差和解析解比较属于不同证据层级，均不自动证明当地工程精度、一定节约或全年计算速度优势。",
]


def make_content(evidence: dict) -> dict:
    data = read_json(ROOT / "data/competition_20261001/manifest.json")
    if data.get("complete") is not True:
        raise ValueError("C1 data package is not complete")
    with (ROOT / "data/competition_20261001/building_archetypes.csv").open(encoding="utf-8-sig", newline="") as f:
        buildings = list(csv.DictReader(f))
    sources = read_json(ROOT / "docs/research_sources_20261001.json")
    scope = data["scope"]
    pending = evidence["status"] == "pending_manifest"
    status_text = "待 C0 指定真实结果 manifest；尚未完成季/年与收益验收" if pending else evidence["scope_statement"]
    areas = sum(float(b["area_m2"]) for b in buildings)
    total_h = sum(float(b["heat_loss_kw_per_c"]) for b in buildings)
    total_c = sum(float(b["thermal_capacity_kwh_per_c"]) for b in buildings)
    btable = {"headers": ["建筑原型", "区域", "面积 m²", "H kW/K", "C kWh/K"],
              "rows": [[b["name"], b["district_id"].replace("district_", "片区"), b["area_m2"], b["heat_loss_kw_per_c"], b["thermal_capacity_kwh_per_c"]] for b in buildings]}
    methods = [
        {"title": "研究任务与证据边界", "paragraphs": [
            "本研究面向 CP-202611 供热规划赛题，在既有源网荷储混合整数线性规划内核上，接入独立楼宇热状态、实际冷热供给、空间候选约束及统一成本碳核算。主案例选择夏热冬冷地区新建高品质供热场景，以武汉公开气象构造可复核原型。",
            "研究目标是把候选技术组合、运行服务和验证证据闭合。技术结构优势与经济/减排/速度优势分别举证，后者必须来自同服务基准、同版求解和独立审计。本文不预先承诺一定节省。",
            status_text,
            "本轮按2026-10-01启动后一天组织技术验收。题面2026-09-15提交日期已经过去，本轮时间盒不是官方延期或报名资格确认。团队名称、成员、学校和导师尚待提供，材料不虚构团队信息。"],
         "table": {"headers": ["证据级别", "可支持的结论"], "rows": [["数据与模块已验收", "限定输入、解析解、局部接口和反例"], ["待完整运行", "完整季/年、同服务比较与最终投资建议"], ["未实现", "真实 GIS、动态水力迟滞、全系统㶲、当地辨识"]]}},
        {"title": "主案例、时间轴与输入身份", "paragraphs": [
            f"主服务年为北京时间2023-07-01至2024-07-01半开区间，共{scope['annual_expected_hours']}个逐时段，覆盖闰日。项目假定供暖服务期为2023-11-15至2024-03-16半开区间，共{scope['expected_hours']}小时，包含3月15日全天。该服务期不是武汉法定供暖期。",
            "内部 time/timestamp 使用带时区 UTC，占用和电价先按 Asia/Shanghai 生成后映射。功率是时段平均kW，能量是kWh，室温和SOC是时段末状态。period_weight仅用于统计，不进入RC时间步或状态递推。",
            "NASA POWER武汉同点UTC/LST各48h缓存检验支持+8h映射，40h×4字段=160对一致。历史CSV原名solar_irradiance_kwh_m2_hour实际为Wh/m²，新包除1000得到kWh/m²，并另存平均W/m²。该检查不等于全年气象精度校准。",
            f"四栋聚合楼宇包含住宅、办公和零售三类，总面积{areas:g}m²，总H={total_h:g}kW/K、总C={total_c:g}kWh/K。逐楼物性独立，面积仅用于归属和强度指标，H/C已是总量，不能再次乘面积。",
            "占用舒适18-26℃、非占用16-28℃，工作日和营业安排为合成情景，未模拟节假日调休。所有楼宇初末温20℃。PCM初始空库，末态非负库存不计残值。可靠容量中未证实的建筑与储热信用保守关闭。[D01,D05,PH03]"], "table": btable},
        {"title": "内生负荷与一阶 RC 模型推导", "paragraphs": [
            "令H为建筑总传热系数(kW/K)，C为有效热容(kWh/K)，Qh/Qc为真实送达热/冷功率(kW)，G为内部和太阳显热得热(kW)。从显热守恒得到一阶常微分方程。每小时参数及输入保持常数时，对线性方程积分即可获得精确离散系数。这里的“精确”限定于所声明的一阶分段常值模型。[PH01,PH02]",
            "Tstart为本时段起点温度，Tend为时段末温度。a、b预先计算，状态式保持线性。实现用-expm1提高小指数情形的稳定性，并拒绝会被线性系数清理阈值丢弃的过小b。H、C、dt须正且有限。",
            "预测负荷以满足指定舒适的所需能量表达，不把旧典型日代理热量与RC负荷同时强制。稳态保守参考 H·max(0,Tmin-Tout)只用于筛查；设计峰值取逐楼最严格最低舒适温与统一设计外温，不抵扣未经证明的得热或蓄热容量。该参考不是优化后的实际耗热。",
            "正常工况采用硬舒适约束。供热不足必须使室温约束不可行，不能用一个与RC脱节的unserved量掩盖。冷热互斥默认exclusive；relaxed仅是诊断选项，任何真实同开都不能取得严格楼宇物理通过。"],
         "formulas": ["C·dT/dt = H·(Tout - T) + Qh - Qc + G", "a = exp(-H·dt/C),  b = (1-a)/H = -expm1(-H·dt/C)/H", "Tend = a·Tstart + b·(H·Tout + Qh - Qc + G)", "Tmin[t] <= Tend[t] <= Tmax[t]", "0 <= Qh[t] <= Mh[t]·z[t],  0 <= Qc[t] <= Mc[t]·(1-z[t])"]},
        {"title": "源网荷储及联合冷热能量边界", "paragraphs": [
            "投资变量描述新建源容量、储热容量和候选管径，运行变量描述源出力、输热、充放热、电力与冷热服务。每个节点及温级建立供给、网络输入、储热净输出与实际服务的平衡。楼宇供热按节点/温级汇总，制冷按节点汇总，父子层是同一能量的不同视图，不重复计量。",
            "RC覆盖的节点温级跳过旧固定需求、DR移时守恒、虚拟建筑热惯性和免费计量节热。未覆盖温级继续保留独立固定服务；同温级DHW也须显式另列。冷设备输出真实进入楼宇冷量。源出力上下限、容量、可用性、电网和适用启停/爬坡约束由核心统一控制。",
            "热泵供热Q由电功W与环境取热Qenv共同组成。COP采用供水绝对温度和温升的简化卡诺效率表达，低温除霜及湿度修正后截断于给定上下限。这是透明设备情景函数，不是厂家全工况校准曲线。",
            "储热每步先考虑自损，再加充热和减放热。fixed首时段读取明确初态，cyclic仅在同一boundary_group内闭合。代表日不能用权重代替跨期状态。新案例初库为空，所有可用储热来自窗内充热。[OPT01,OPT06]"],
         "formulas": ["ΣQsource + Σηpipe·Fin - ΣFout + Qdis - Qch = Qbuilding + Qfixed", "W = Qh/COP,  Qenv = Qh - W", "COP = clip(ηCarnot·Tsupply,K/max(Tsupply,K - Tsource,K,4)·fdefrost·fhumidity)", "SOCend = (1-loss)^dt·SOCstart + ηch·Qch·dt - Qdis·dt/ηdis"]},
        {"title": "空间候选、温级与多尺度耦合", "paragraphs": [
            "空间预处理使用local_cartesian_m原型坐标，节点必须唯一覆盖配置。路线长度由欧氏距离乘绕行系数获得，按距离上限、接入允许和跨系统规则筛选。返回配置直接删去不可行管道并改写管径总投资，因此空间改变数学可行域与目标，不能只保存一张地图后仍使用原配置。",
            "C3微型反例中600m上限可保留连接，599m上限删除连接和管道变量，同服务要求变为不可行；合成管价100+600×2=1300元进入1300×CRF年化目标。这是局部机制证据，不是武汉工程报价或正式收益。",
            "每栋楼保留独立H/C、面积、占用和温度，唯一映射到节点、片区与系统。空间包只做热量守恒视图，完整多尺度优势还需在同天气下改变楼宇条件，观察片区设备调度和上层共享电网/投资的变化。",
            "直接热源必须满足供热温级条件。低温资源通过热泵供高温服务时，W和Qenv均入账，禁止无功提温或环境取热双计。当前没有跨节点串联增温器，管道仍按同温级静态kW输送。热源输出热㶲指标不等于全系统㶲效率。[PH05-PH07]"],
         "formulas": ["Lroute = route_factor·sqrt((x1-x2)^2 + (y1-y2)^2)", "Ipipe(option) = fixed_yuan(option) + yuan_per_m(option)·Lroute", "Qnode,grade = Σ Qbuilding + Qindependent_fixed"]},
        {"title": "稀疏 MILP 与成本碳多目标算法", "paragraphs": [
            "模型把连续容量、逐时运行与离散技术/管径选择联立为MILP。可行技术组合由约束隐式搜索，无需逐个穷举；不能用二元数目推算后宣称所有组合可行。LinearMILP直接由行字典构造CSR再转CSC，并缓存未改变的矩阵。稀疏结构是实现特征，速度优势要由同矩阵、同预算实测。",
            "经济目标区分资本年值、固定运维、现金运行费、碳费和惩罚。年化资本由CRF换算，寿命、补贴和共享资产边界由配置确定。技术成本比较应排除人为惩罚影响；含碳价政策目标不能与现金成本混称。",
            "总服务排放表达式统一相加emissions_kg与cooling_emissions_kg，包含共享电力账本作用，供排放目标和碳帽共同调用。旧供热分量含共享电力抵扣，不能单独称为净供热碳足迹。运营排放不含未建模的设备制造等生命周期碳。",
            "成本端点与排放端点各做两阶段词典序：先求主目标，再在主目标容差帽下最小化另一目标。端点间预设ε碳帽求成本最小点，对有效可行点按容差去重并去除支配解。折中推荐采用归一化目标空间距理想点最近规则。[OPT04,OPT05,ENV01]",
            "所有点必须记录实际向量、status、gap、界、容差及分段耗时。status=1只有存在可行向量并通过审计时才是限时可行，status=0只说明该数学模型的求解终止质量。代表期最优不证明全年最优，gap不等于物理误差。[OPT07,OPT08,ENV02]"],
         "formulas": ["min cᵀx,  l <= A·x <= u,  xI integer", "CRF = r·(1+r)^n / ((1+r)^n-1),  r=0 时 CRF=1/n", "Etotal(x) = Eheating_ledger(x) + Ecooling_ledger(x)", "min C(x) subject to Etotal(x) <= ε, x in feasible set", "d(x) = sqrt(Cnorm(x)^2 + Enorm(x)^2)"]},
        {"title": "代表时段规划与固定投资连续回放", "paragraphs": [
            "代表时段用于控制规划规模，应保留天气、占用、得热和显式参考负荷特征，并检查能量、持续曲线、相关性及爬坡代表性。统计相似不能替代完整时序可行性。[OPT02]",
            "候选投资生成后冻结一次，规划年化资本随方案一同保存。冬季与全年回放采用同一容量、管道和技术选择，仅允许调度改变。独立冬季与年度的初态目前分别固定，并不宣称季初状态承接全年轨迹。",
            "回放应逐时检查室温、冷热守恒、SOC、出力、电网、网络、固定服务、适用政策和年度地源账本。冬季不能强套年度提取/回灌比，也不能据冬季运行宣称全年地源平衡。成本回放要加回规划资本年值，避免冻结投资后出现假便宜。",
            "若失败，按容量、舒适、库存、网络或年度资源定位原因，在预算内补充带状态上下文的关键时段或边界后重规划。每轮新投资都重新冻结并完整验证。有限反馈不具备未经证明的Benders或全局收敛保证。[OPT03]",
            "本稿等待C0指定runner正式manifest与独立审计后，填写实际代表集、轮次、候选、失败原因和最终状态；没有执行的反馈不能写成已经带来收益。"]},
        {"title": "验证设计与当前证据", "paragraphs": [
            "证据分为解析/合成程序测试、独立物理重算、外部实验基准和当地实测。现有工作主要覆盖前两类的模块与接口准备，未获得当地测量。参考论文支持方法，不把论文精度或节能率移植到本案例。[PH03,PH04,VAL01]",
            "C1交接记载17项数据测试通过；C2交接记载63项楼宇专项测试通过；C3交接记载46项空间品质专项测试通过。这些记录有各自代码/输入身份和限定范围，不相加为全项目回归，更不等于完整供暖季证书。",
            "C2真实冬季前24h适配器只是固定供能上限、最小冷热量目标，不含真实设备效率、投资或管网，故仅证明局部输入兼容与可求解。正例要求存在向量并重算约束，负例以预期不可行为成功。",
            "最终独立审计须从完整导出的轨迹重新计算，不以业务函数对自身的调用充当独立证明。哈希同时绑定代码、配置、天气、投资与结果。任何数值依赖变更都使受影响证书失效。"]},
        {"title": "寻优结果、经济碳评价与调度分析", "paragraphs": [
            status_text,
            "正式结果必须列出规划端点、采样非支配点、投资组合、求解质量、季/年回放和同服务基准。基准共用天气、建筑物性、占用舒适、初末态与冷热服务，只改变技术集合或控制规则。不能以零供暖、少供热或不同冷服务制造节省。",
            "成本差额及减排率分别以同边界基准为分母，分母为零时不报百分比。没有收入、税费或寿命依据时给成本和盈亏平衡，不编造IRR。传统LCOH若只计热，必须说明联合制冷及共享资产分摊，优先报告总系统成本与总服务排放。",
            "图表设计以评委决策为中心：源容量与投资表、实际热负荷持续曲线、最困难连续片段的热源/冷源/SOC/温度及边界、成本结构、总排放和ε权衡。每图携带run_id与来源文件身份。没有真实轨迹时不画模拟样例充当结果。",
            "敏感性应明确是重新规划还是固定投资运行对照。改变H/C、价格、碳因子或效率都会影响约束或最优调度，不能只重算报表数字。全流程耗时包括建模、求解、导出和审计，模块秒级耗时不能外推全年性能。"],
         "table": {"headers": ["正式结果字段", "当前值", "接受条件"], "rows": [["推荐投资/成本/总碳", "未绑定", "同版完成manifest及审计"], ["2928h/8784h连续回放", "未绑定", "同投资、完整时序与边界"], ["相对基准节省/减排", "未计算", "双方同服务且验证通过"], ["端到端速度优势", "未证明", "同矩阵/容差/预算比较"]]}},
        {"title": "评分逐项验收与可证明贡献", "paragraphs": [
            "评分依据为题面第4-5页。七个子项权重合计100分，本文逐条映射代码、输入、试验和结果，不预测得分，不把完成比例折算评分。完整原文与证据矩阵另见《评分逐项验收与算法优势》。",
            "贡献一是源网荷储与冷热共同决策的稀疏MILP。贡献二是异构楼宇精确离散RC接实际供能，消除温度与供能脱节的建模路径。贡献三是空间筛选真正改写可行连接及成本。贡献四是统一碳边界、词典序端点和非支配筛选。贡献五是同一投资连续回放和独立物理审核的证据闭合。",
            "前述贡献的结构与反例已有部分证据；正式经济、环境和速度优势仍须从真实比较得出。若验证失败，失败和缺口应进入结论，而非调整措辞隐去必要约束。"],
         "table": {"headers": ["评分项", "权重", "当前判定"], "rows": [[r[0], str(r[1]), r[7]] for r in RUBRICS]}},
        {"title": "真实局限与技术答辩要点", "paragraphs": LIMITS + [
            "答辩时先演示可证伪反例：断源后温度无法凭空维持、599m接入上限导致同服务不可行、低温直供高温服务被拒绝。再展示同投资完整轨迹与独立审计，最后讨论同服务成本碳差额。缺少后一环时只陈述模型能力和待验证假设。",
            "三类场景适配由共同配置与内核支持，但本轮主场景完整验收须独立完成。广义时间-空间-品质三维方向尚有真实GIS、动态水力迟滞及全系统㶲等缺口，不能仅以命名覆盖宣称全部达成。"]},
    ]
    methods[5]["pseudocode"] = [
        "算法1  词典序端点与 ε 抽样筛选",
        "输入：同一可行域F、成本C、总冷热排放E、容差τ、样本数m和求解预算B",
        "输出：端点、非支配采样集P、折中点、每阶段状态与gap",
        "01  对主目标f∈{C,E}：先最小化f，保存可行解值f*及求解界",
        "02  若无可行向量或数值/物理审计失败：保存失败并停止",
        "03  在f≤f*+τabs+τrel·|f*|下最小化另一目标，保存端点",
        "04  若低碳端点排放高于成本端点且超容差：拒绝生成ε区间",
        "05  在两端排放之间生成m个预设ε值",
        "06  对每个ε：最小化C，约束E≤ε；审核实际向量和碳帽",
        "07  对审核通过样本按容差去重并剔除支配点，得到P",
        "08  若P为空：保存失败；否则按归一化理想点距离选折中样本",
        "终止：全部预设点完成或预算耗尽。限时点披露gap，不称完整连续前沿。",
    ]
    methods[6]["pseudocode"] = [
        "算法2  代表时段、投资冻结与完整年季审核",
        "输入：冻结配置、逐时年/季输入、代表集R、同服务基准、轮次与时间上限",
        "输出：每轮方案身份、完整轨迹、失败原因及验收范围",
        "01  校验时钟、楼宇、空间、品质、单位与初末态；选择并审核R覆盖",
        "02  在R上规划技术与容量；若无可行向量：记录失败，交总控裁决",
        "03  对本轮候选只冻结一次投资和资本账本，登记投资/配置/数据/代码哈希",
        "04  用冻结投资运行完整服务年及独立固定初态冬季",
        "05  独立重算RC、冷热守恒、SOC、舒适、出力、网络与年度地源账本",
        "06  若全部通过：保留本轮可推荐范围，并与同服务基准比较",
        "07  若失败：按原因提出有上下文的时段/边界修正，保留失败证据",
        "08  由总控决定在预算内开始下一轮；新投资重新冻结并复验",
        "终止：通过、无可修正原因或预算耗尽。runner分阶段执行，外层失败重规划由总控触发。",
    ]
    methods[7]["paragraphs"].append("配置中cooling.maximum_capacity_oversize_ratio=1.10尚未进入当前模型约束，不列为已实现硬上限；冷源仍受共享热泵容量和每源新建上限约束。旧占用温差≤950指标属于原虚拟状态，RC舒适验收改用实际室温越界度时及C6独立审计。")
    china_path = ROOT / "docs/china_research_20261001/sources.json"
    if china_path.exists():
        china = read_json(china_path)
        selected_ids = {"P1", "P4", "P5", "P6", "C1", "C4", "C6"}
        for ref in china:
            if ref["id"] in selected_ids:
                sources.append({"id": "CN-" + ref["id"], "title": ref["title"],
                                "authors_or_org": ref.get("authors", ref.get("publisher", "原始来源见链接")),
                                "year": ref.get("year", ""), "url": ref["url"],
                                "read_depth": ref["read_depth"], "limits": ref["limitations"]})
        methods[2]["paragraphs"].append("国内热容辨识与热负荷弹性研究强调参数依据与模型误差。当前可对H/C设置有解释的低中高扰动，核对固定投资下舒适和方案排序，尚不能声称已完成实测辨识。没有观测或专家证据时不虚构概率或置信度。[CN-P1,CN-P4]")
        methods[3]["paragraphs"].append("机组COP与系统COP的边界不同。供热系统季节性能应以同期间实际供热总量除以压缩机、泵、辅机与备用电加热的相应用电总量，不能对逐时COP简单平均。国家能源局吐鲁番案例记录的测试点差异支持这一边界区分，不作为武汉设备参数。[CN-C6]")
        methods[6]["paragraphs"].append("年度地源提取/回灌能量比仅是年度账本，不等于多年地下温度场恢复。济宁复合地热案例和跨季地埋管研究提示需另有地下状态、功率/能量容量及多年影响模型；不能将水箱SOC改名为地下热平衡。[CN-C1,CN-P6]")
        methods[10]["paragraphs"].append("国内热网动态研究区分水体蓄热、输送迟滞与建筑热容。当前网络变量为kW，不能直接代入质量流量或速度公式，也不能用静态候选长度宣称已实现动态水力。[CN-P5]")
        methods[7]["paragraphs"].append("中国资料补充共9个工程案例和7篇论文，其中6个案例来自同一国家能源局汇编，不计为6个独立出版来源。汇编西吉案例的费用差额单位疑点及部分月份外推已登记；正式数据应保留原值/单位、变换与采用值，权威出处仍需算术和口径校核。[CN-C4]")
    if evidence["metrics"]:
        methods[8]["table"] = {"headers": ["指标", "真实值", "单位"], "rows": [[m["label"], f"{m['value']:.6g}" if isinstance(m["value"], float) else str(m["value"]), m["unit"]] for m in evidence["metrics"]]}
        methods[8]["paragraphs"].append("本节指标按evidence_bindings.json记录的JSON指针或CSV行列读取，未发现值时生成器拒绝填充；绑定结果并不自动表示所有验收项通过。")
    for t in evidence["tables"]:
        methods.append({"title": t["title"], "paragraphs": [f"来源：{Path(t['path']).name}；SHA256 {t['sha256']}。"], "table": t})
    checks = evidence.get("checks")
    if checks:
        methods.append({"title": "指定批次审核结果", "paragraphs": ["以下判定直接读取指定审计文件，不由图文生成器自行推断。"], "table": {"headers": ["审核项", "实际值", "是否符合绑定预期"], "rows": [[c["label"], str(c["observed"]), "是" if c["passed"] else "否"] for c in checks]}})
    slides = [
        {"kind": "cover", "title": "城镇供热源荷匹配\n与规划寻优", "paragraphs": ["CP-202611  ·  2026年10月1日"], "footnote": "团队、学校及成员信息待提供"},
        {"title": "任务边界与当前验收", "paragraphs": ["至少一个场景完成逐时完整供暖季仿真", "本轮主案例：S3 夏热冬冷地区高品质新建供热", "结构优势用代码与反例证明，收益结论等待真实同服务比较"], "table": {"headers": ["已验收", "待完整运行", "未实现"], "rows": [["数据和专项模块", "季/年回放与基准", "真实 GIS/水力迟滞"], ["解析与负例检查", "成本碳前沿/收益", "全系统㶲/当地辨识"]]}, "notes": "题面第3-5页；C1/C2/C3交接。"},
        {"title": "主案例与数据身份", "paragraphs": [f"服务年{scope['annual_expected_hours']}小时，供暖季{scope['expected_hours']}小时，统一1h", f"4栋、3类合成楼宇，总面积{areas:g}m²", "NASA公开气象，H/C、占用与电价为明确情景假设"], "footnote": "供暖期：北京时间2023-11-15至2024-03-16，半开区间，项目假定", "notes": "C1 manifest / building_archetypes.csv；D01,D05。"},
        {"title": "源网荷储与冷热共同决策", "paragraphs": ["投资层：源容量、储热容量与候选管径", "运行层：实际热冷量、输热、充放热与电力", "服务层：逐楼室温、占用舒适与明确初末态"], "formula": "供给 + 网络净输入 + 储热净输出 = 实际楼宇服务 + 独立固定服务", "notes": "model.py::build；building_thermal.py。网络仍为静态kW输热。"},
        {"title": "实际供能驱动楼宇温度", "paragraphs": ["每栋楼保留独立H/C、得热和舒适边界", "分段常值一阶模型解析离散，预计算系数后仍为线性约束"], "formula": ["a = exp(-H·dt/C),  b = (1-a)/H", "Tend = a·Tstart + b·(H·Tout + Qh - Qc + G)"], "footnote": "供热不足不能由脱节的缺供变量维持舒适", "notes": "PH01/PH02；C2解析降温、恢复、供热不足不可行反例。"},
        {"title": "异构楼宇与多尺度作用", "table": btable, "paragraphs": ["逐楼状态联到区域设备与网络，上层共享电网和投资约束继续作用"], "footnote": "全部物性为合成原型，未做当地实测辨识", "notes": "building_archetypes.csv；C3唯一归属、逐层守恒。最终需H/C受控对照。"},
        {"title": "空间条件改变可行域和投资", "paragraphs": ["长度、接入和跨系统规则先筛除候选边", "返回配置实际进入规划和固定投资回放", "局部反例：600m允许，599m上限下同服务不可行"], "formula": "Ipipe = fixed_yuan + yuan_per_m·Lroute", "footnote": "原型米制坐标，不是真实GIS或工程报价", "notes": "C3_handoff；spatial_quality.py::preprocess_spatial_quality。"},
        {"title": "品质转换保留电耗代价", "paragraphs": ["50℃直接热源不能无功满足75℃服务", "低温资源经热泵提温，电耗与环境取热同时入账"], "formula": ["W = Qh/COP", "Qenv = Qh - W"], "footnote": "温级准入与转换账本已有；全系统㶲优化未实现", "notes": "C3专项反例；PH07用于边界解释。COP为现有简化情景函数。"},
        {"title": "稀疏 MILP 的求解结构", "paragraphs": ["连续容量与离散技术组合联立求解", "行字典直接构造CSR后转CSC，未变矩阵可复用", "每个结果记录状态、可行向量、gap、界与分段耗时"], "formula": "min cᵀx    subject to l <= A·x <= u, xI integer", "footnote": "实现结构不等于已证明全年速度优势", "notes": "linear.py；ENV01/ENV02。"},
        {"title": "统一碳边界与多目标权衡", "paragraphs": ["成本与低碳端点采用两阶段词典序", "统一冷热总排放同时用于目标与ε碳帽", "去重和支配筛选后，按归一化理想点距离选择折中方案"], "formula": "min C(x)  subject to Etotal(x) <= ε", "footnote": "结果是采样可行解的非支配子集，非完整连续Pareto前沿", "notes": "model.py::solve_lexicographic；experiments.py；OPT04/OPT05。"},
        {"title": "同一投资的完整连续回放", "paragraphs": ["代表时段形成候选后，投资只冻结一次", "完整冬季和全年逐时保留室温与储热状态", "失败按原因补时段或约束，新投资重新验证"], "footnote": "年度地源账本不能由冬季替代；循环典型日不能证明夏充冬放", "notes": "chronology.py；heating_season.py；OPT01/OPT02/OPT03/OPT06。"},
        {"title": "物理验证的证据层级", "table": {"headers": ["证据", "当前作用"], "rows": [["C1 17项专项测试", "时钟、单位、完整输入"], ["C2 63项专项测试", "RC解析、供能和互斥"], ["C3 46项专项测试", "空间、品质、层级"], ["季/年完整轨迹与独立审计", "待C0指定真实结果"]]}, "footnote": "专项数量不能当作全年认证，也不能当作全项目回归总数", "notes": "C1/C2/C3_handoff；PH03/PH04。"},
        {"title": "正式结果与同服务比较", "paragraphs": [status_text, "基准和推荐共用天气、建筑、占用舒适、冷热服务与初末态", "成本、减排、投资回报和速度仅从真实完整证据得出"], "notes": "本页待真实manifest填充，不含假定优化收益。"},
        {"title": "逐项评分响应", "table": {"headers": ["评分项", "分值", "关键证据"], "rows": [["模型/动态/场景", "50", "守恒、完整季、必要约束"], ["多目标/多尺度", "25", "端点前沿、跨层受控对照"], ["可视化与分析", "15", "同服务成本碳及调度"], ["代码与可移植性", "10", "冻结环境、源码和复算"]]}, "footnote": "完成状态不换算比赛得分", "notes": "题面第4-5页；完整逐条原文见评分逐项验收与算法优势.md。"},
        {"title": "当前可以证明的技术贡献", "paragraphs": ["实际冷热供给与异构楼宇温度联立", "空间接入约束和造价真实改变规划", "总冷热碳口径统一，端点和筛选规则透明", "固定投资与独立审计为最终推荐提供可复核依据"], "footnote": "正式经济、环境和速度优势仍以有效对照结果为准", "notes": "证据链：输入身份、代码约束、反例、真实轨迹、独立审计。"},
        {"title": "局限与下一步证据", "paragraphs": ["当地实测与参数辨识尚缺，1R1C仅含显热和小时末舒适", "真实GIS、动态水力迟滞、串联提温和全系统㶲尚未实现", "完成同一投资的冬季、全年与同服务基准，再形成正式建议"], "footnote": "不预先宣称一定节省成本、碳或计算时间", "notes": "PH05/PH06/PH07、C1/C2/C3边界；本轮一天验收不是官方延期。"},
    ]
    if evidence["metrics"]:
        slides[12].pop("paragraphs", None)
        slides[12]["table"] = {"headers": ["真实指标", "数值", "单位"], "rows": methods[8]["table"]["rows"][:6]}
        slides[12]["footnote"] = status_text
        slides[12]["notes"] = json.dumps(evidence, ensure_ascii=False)
    if plot_info := evidence.get("plot_manifest"):
        for figure in plot_info["figures"]:
            methods.append({"title": figure["title"], "paragraphs": ["来源运行：" + ", ".join(figure["sources"]) + "。范围和选择规则：" + json.dumps(figure["facts"], ensure_ascii=False)], "figure": figure["png"]})
        chart_lookup = {chart["id"]: chart for chart in evidence["chart_payloads"]}
        for binding in evidence.get("slide_charts", []):
            target = slides[binding["slide"] - 1]
            chart = chart_lookup[binding["id"]]
            target["chart"] = chart
            target.pop("table", None)
            target.pop("formula", None)
            target["paragraphs"] = []
            target["title"] = chart["title"]
            target["notes"] = json.dumps({"chart_id": chart["id"], "spec_sha256": plot_info["spec_sha256"], "source_manifests": plot_info["sources"]}, ensure_ascii=False)
    return {"title": "城镇供热源荷匹配与规划寻优模型研究报告", "subtitle": "武汉公开气象与异构楼宇RC原型", "status": evidence["status"], "report_status": status_text,
            "manifest_path": evidence.get("manifest_path"), "date": "2026-10-01", "team": "团队、学校、成员及导师信息待提供", "sections": methods, "slides": slides,
            "sources": sources, "evidence": evidence,
            "input_manifest": {"path": "data/competition_20261001/manifest.json", "sha256": sha(ROOT / "data/competition_20261001/manifest.json"), "status": data["status"]}}


def write_rubric(content: dict):
    lines = ["# 评分逐项验收与算法优势", "", "日期：2026-10-01；题面 CP-202611 第4-5页。", "", content["report_status"], "",
             "本表严格区分模块已验收、待完整运行和未实现。权重合计100分，不预测比赛得分。所有代码符号均待最终冻结身份复核，行号不作为跨版本永久标识。", ""]
    for name, weight, quote, page, code, inputs, evidence, state, needed in RUBRICS:
        lines += [f"## {name}（{weight}分）", "", f"> 原文（PDF第{page}页）：{quote}", "", f"- 代码：`{code}`", f"- 输入：{inputs}", f"- 已有试验/结果：{evidence}", f"- 当前判定：**{state}**。", f"- 正式验收缺口：{needed}", ""]
    lines += ["## 七项评分的可证亮点", "",
              "| 子项 | 拟呈现的具体亮点 | 反例/对照及完成边界 |", "| --- | --- | --- |",
              "| 模型架构20 | 实际冷热量联立独立楼宇RC与源网储 | 无源维持舒适应不可行；低温无功直供高温应拒绝；完整闭环待结果 |",
              "| 动态仿真15 | 统一UTC和明确初末态下连续1h年/季 | 缺时、重复、跨组串接负例；同投资完整轨迹和SOC重算待结果 |",
              "| 场景验证15 | S3冷热服务与年度地源账本共用真实状态 | 必要政策保持的冷热极端短例与完整年季分别列证；短例不能代替主案例 |",
              "| 多目标15 | 统一碳边界、词典序端点、容差非支配筛选 | 不一致碳帽、重复/支配点反例；实际端点/gap与边界变化待结果 |",
              "| 多尺度10 | 每楼H/C保留，层级实际送达量守恒 | 重复归属拒绝、600/599m空间反例；H/C影响上层的受控对照待结果 |",
              "| 分析可视化15 | 同服务成本碳、冷热日历、极端周及前沿证据 | 只读指定完成manifest，缺列/改哈希拒绝；现金费/资本/收益情景分开 |",
              "| 工程质量10 | 独立模块、输入输出身份、复算与发布清单 | C0报告本轮529项回归通过；最终绑定日志与发布复现后验收 |", "",
              "冷源1.10倍超配参数尚未进入当前模型硬约束，不能称已实现上限；旧占用温差≤950虚拟指标不认证RC舒适，采用实际越界度时与C6。", "",
              "## 答题及交付硬要求", "", "至少选择三类场景之一完整仿真，时间分辨率1h。PPT须含团队、场景和技术路线；PDF须含负荷模型推导、匹配算法、寻优结果、经济碳评价及调度，原则上不超过1万字；另交真实结果数据表、可运行完整源码、配置、对应输入输出、预处理与字段说明。团队身份待提供。", "",
              "题面9月15日提交日期已经过去；本轮按启动后一天验收，不宣称官方延期。", "", "## 技术答辩的证明链", "",
              "| 可证明主张 | 已有机制证据 | 还需正式证据 |", "| --- | --- | --- |",
              "| 稀疏MILP共同决策 | 连续容量、离散技术和逐时运行同模 | 同矩阵资源/耗时与真实组合 |",
              "| 室温接实际供能 | RC解析、断源不可行、核心冷热映射 | 完整年/季独立物理审计 |",
              "| 空间改变决策 | 600/599m负例与造价进入CRF | 同版空间包与正式投资身份 |",
              "| 品质转换付出电耗 | 50℃禁直供75℃，提温电/环境热账本 | 正式转换调度账本 |",
              "| 多目标口径一致 | 总冷热排放表达式、词典序、支配筛选 | 同版有效端点与前沿 |",
              "| 连续回放可追溯 | 冻结投资与状态审计接口 | 同一投资冬季/全年和独立审计 |", "",
              "答辩顺序：先给出可证伪反例，再展示完整时序审核，最后讨论同服务基准的成本碳差额。若最后两环尚缺，明确停留在结构能力和局部验证，不能用漂亮收益数代替。", "", "## 未实现及真实局限", ""]
    lines += [f"{i+1}. {s}" for i, s in enumerate(LIMITS)]
    lines += ["", "## 结果绑定契约", "", "C5仅消费C0指定、complete=true且哈希匹配的正式结果manifest。数据包complete=true不代表求解完成。默认骨架不含任何成本/碳/速度优化结果；缺值拒绝填0。代码、数据、参数或投资变更必须重新绑定并重验。历史结果如引用，独立标版本，不能混为本轮RC推荐。", ""]
    (DOCS / "评分逐项验收与算法优势.md").write_text("\n".join(lines), encoding="utf-8")


def render_pdf(content: dict, path: Path):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, Image
    pdfmetrics.registerFont(TTFont("ReportCN", "C:/Windows/Fonts/simhei.ttf"))
    pdfmetrics.registerFont(TTFont("ReportLatin", "C:/Windows/Fonts/arial.ttf"))
    styles = {
        "body": ParagraphStyle("Body", fontName="ReportCN", fontSize=10.5, leading=17.8, spaceAfter=9, wordWrap="CJK", textColor=colors.HexColor("#20343F")),
        "title": ParagraphStyle("Title", fontName="ReportCN", fontSize=27, leading=39, spaceAfter=20, textColor=colors.HexColor("#123543")),
        "h1": ParagraphStyle("H1", fontName="ReportCN", fontSize=17, leading=25, spaceAfter=15, keepWithNext=True, textColor=colors.HexColor("#126E78")),
        "small": ParagraphStyle("Small", fontName="ReportCN", fontSize=8, leading=12, spaceAfter=5, wordWrap="CJK"),
        "formula": ParagraphStyle("Formula", fontName="ReportLatin", fontSize=10.5, leading=18, spaceAfter=6),
        "cell": ParagraphStyle("Cell", fontName="ReportCN", fontSize=9, leading=14, wordWrap="CJK"),
    }
    def pdf_text(s):
        return str(s).replace("²", "^2").replace("₂", "2").replace("ᵀ", "^T").replace("㶲", "火用")
    def p(s, style="body"):
        return Paragraph(escape(pdf_text(s)).replace("\n", "<br/>"), styles[style])
    story = [Spacer(1, 62), p("城镇供热源荷匹配\n与规划寻优模型研究报告", "title"), p(content["subtitle"], "h1"), Spacer(1, 20), p(content["report_status"]), Spacer(1, 20), p(content["team"]), p("工作日期：2026年10月1日"), p("输入为公开气象与合成楼宇原型；结论范围按实际证据声明。"), Spacer(1, 35), p("研究报告、可编辑答辩PPT及真实结果绑定共用一份内容清单。", "small"), PageBreak()]
    story += [p("报告结构", "h1")]
    for i, section in enumerate(content["sections"], 1):
        story += [p(f"{i:02d}  {section['title']}")]
    story += [p("附录  来源登记与使用范围"), PageBreak()]
    for i, section in enumerate(content["sections"], 1):
        story += [p(f"{i:02d}  {section['title']}", "h1")]
        for para in section.get("paragraphs", []):
            story.append(p(para))
        for formula in section.get("formulas", []):
            # Chinese trailing qualifications need the CJK font; formulas remain
            # real PDF text and editable source, never bitmap screenshots.
            story.append(p(formula, "body" if re.search(r"[\u4e00-\u9fff]", formula) else "formula"))
        if pseudo := section.get("pseudocode"):
            story += [Spacer(1, 12)]
            for line in pseudo:
                story.append(p(line, "small"))
        if figure := section.get("figure"):
            from PIL import Image as PILImage
            with PILImage.open(figure) as img:
                width, height = img.size
            scale = min((A4[0] - 92)/width, 565/height)
            story.append(Image(figure, width=width*scale, height=height*scale))
        if table := section.get("table"):
            rows = [[p(x, "cell") for x in row] for row in [table["headers"], *table["rows"]]]
            width = A4[0] - 92
            count = len(table["headers"])
            widths = [width/count]*count
            if count == 3:
                widths = [width*.27, width*.18, width*.55]
            if count == 5:
                widths = [width*.30, width*.18, width*.18, width*.16, width*.18]
            t = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E0ECEC")), ("LINEBELOW", (0,0), (-1,0), .8, colors.HexColor("#126E78")), ("LINEBELOW", (0,1), (-1,-1), .3, colors.HexColor("#CAD7D8")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("TOPPADDING",(0,0),(-1,-1),7), ("BOTTOMPADDING",(0,0),(-1,-1),7)]))
            story += [Spacer(1, 9), t]
        story.append(PageBreak())
    story += [p("附录  来源登记与使用范围", "h1"), p("以下含既有31条来源记录及择优补入的中国资料。阅读深度不同，记录数量不等于全文精读数量。方法资料支持模型设计，标准公告只支持已读元数据，不能自动提供当地参数或认证。完整阅读范围、缓存路径和SHA256见工程docs/research_sources_20261001.json及docs/china_research_20261001/sources.json。")]
    for ref in content["sources"]:
        story.append(p(f"[{ref['id']}] {ref['authors_or_org']}，{ref['title']}，{ref['year']}。", "small"))
        url = ref["url"]
        story.append(Paragraph(f'<link href="{escape(url)}" color="#126E78">原始资料链接</link>  '+escape(pdf_text(ref["read_depth"])), styles["small"]))
        story.append(p("使用边界：" + ref["limits"], "small"))
        story.append(Spacer(1, 5))
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("ReportCN", 8)
        canvas.setFillColor(colors.HexColor("#5D7078"))
        canvas.drawString(46, 26, "CP-202611 / S3公开气象原型 / " + ("待正式结果" if content["status"] == "pending_manifest" else "指定证据批次"))
        canvas.drawRightString(A4[0]-46, 26, str(doc.page))
        canvas.restoreState()
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=46, leftMargin=46, topMargin=44, bottomMargin=46, title=content["title"], author="north_heat_challenge")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--evidence-spec", type=Path)
    ap.add_argument("--skip-pdf", action="store_true")
    ap.add_argument("--plots", action="store_true", help="C0 manifest-bound plots; use project Python with matplotlib")
    ap.add_argument("--revision", default="pending_manifest")
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.revision):
        raise ValueError("revision must be a safe filename token")
    OUT.mkdir(parents=True, exist_ok=True)
    evidence = bind_evidence(args.manifest, args.evidence_spec)
    if args.plots:
        if args.manifest is None or args.evidence_spec is None:
            raise ValueError("--plots requires C0-selected --manifest and --evidence-spec")
        evidence["plot_manifest"] = build_result_visuals(args.evidence_spec, args.revision)
        evidence["chart_payloads"] = read_json(OUT / "plots" / args.revision / "chart_payloads.json")
        evidence["slide_charts"] = read_json(args.evidence_spec).get("slide_charts", [])
    content = make_content(evidence)
    (OUT / "content.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "evidence_bindings.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot = {}
    for rel in ["docs/execution_20261001/INTERFACES.md", "docs/execution_20261001/C1_handoff.md", "docs/execution_20261001/C2_handoff.md", "docs/execution_20261001/C3_handoff.md", "docs/execution_20261001/C4_handoff.md", "docs/research_sources_20261001.json", "docs/china_research_20261001/sources.json", "docs/china_research_20261001/verification.json", "src/heatplan/model.py", "src/heatplan/building_thermal.py", "src/heatplan/spatial_quality.py", "src/heatplan/linear.py", "src/heatplan/experiments.py", "src/heatplan/chronology.py", "src/heatplan/heating_season.py"]:
        path = ROOT / rel
        if path.is_file():
            snapshot[rel] = sha(path)
    (OUT / "source_snapshot.json").write_text(json.dumps({"snapshot_only_not_frozen_run": True, "files": snapshot}, indent=2), encoding="utf-8")
    write_rubric(content)
    pdf = OUT / f"模型算法研究报告_{args.revision}.pdf"
    if not args.skip_pdf:
        render_pdf(content, pdf)
    # Count the full report body and bibliographic appendix separately.
    body_text = "\n".join([content["title"], *[json.dumps(s, ensure_ascii=False) for s in content["sections"]]])
    cn_count = len(re.findall(r"[\u3400-\u9fff]", body_text))
    appendix_text = "\n".join(str(ref[field]) for ref in content["sources"] for field in ["title", "authors_or_org", "year", "read_depth", "limits"])
    appendix_count = len(re.findall(r"[\u3400-\u9fff]", appendix_text))
    if cn_count + appendix_count > 10000:
        raise ValueError("Report Chinese body plus references exceeds the 10000-character target")
    (OUT / "build_record.json").write_text(json.dumps({"status": evidence["status"], "body_cjk_characters": cn_count, "appendix_cjk_characters": appendix_count, "total_cjk_characters": cn_count+appendix_count, "slide_count": len(content["slides"]), "report_path": str(pdf), "report_created": not args.skip_pdf, "manifest_bound": args.manifest is not None, "no_solver_invoked": True, "visual_review": "pending"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "body_cjk_characters": cn_count, "slides": len(content["slides"]), "pdf": str(pdf)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
