        else:
            config, frame = load_inputs(config_path, timeseries_path)
            if scenario_specs_path:
                specs = yaml.safe_load(scenario_specs_path.read_text(encoding="utf-8"))
                demand_columns = [
                    column for node in config["nodes"]
                    for column in node.get("demand_columns", {}).values()
                ]
                frame = build_correlated_northern_scenario_bundle(
                    frame, demand_columns, specs["scenarios"],
                    period_weight=float(specs.get("period_weight", config["planning"].get("representative_day_weight", 1.0))),
                )
                config = copy.deepcopy(config)
                config["planning"]["risk"] = dict(specs.get("risk", {}))
                validate_inputs(config, frame)
            model = HeatPlanningModel(config, frame)
            artifacts = model.solve(
                mip_gap=args.mip_gap,
                time_limit=args.time_limit,
            )
            export_results(profile_output, artifacts, config, model.ts)
            summary = {key: value for key, value in artifacts.summary.items() if not isinstance(value, dict)}
            (profile_output / "scenario_results.json").write_text(
                json.dumps(artifacts.summary.get("scenario_results", {}), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
