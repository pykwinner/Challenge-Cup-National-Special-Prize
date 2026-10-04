        else:
            # 配置与时序共同定义算例身份；本段不自动启动碳帽扫描。
            config, frame = load_inputs(config_path, timeseries_path)
            # 仅在提供场景文件时展开不确定场景；无文件则沿用读入时序。
            if scenario_specs_path:
                specs = yaml.safe_load(scenario_specs_path.read_text(encoding="utf-8"))
                # 按节点配置收集需求列，保持场景扩展与需求接口一致。
                demand_columns = [
                    column for node in config["nodes"]
                    for column in node.get("demand_columns", {}).values()
                ]
                frame = build_correlated_northern_scenario_bundle(
                    frame, demand_columns, specs["scenarios"],
                    # period_weight是代表时段权重，不等同于额外的物理小时步长。
                    period_weight=float(specs.get("period_weight", config["planning"].get("representative_day_weight", 1.0))),
                )
                # 复制后修改风险设置，保留此前读入的配置对象。
                config = copy.deepcopy(config)
                config["planning"]["risk"] = dict(specs.get("risk", {}))
                # 场景展开后再次校验列名、参数和时序约定。
                validate_inputs(config, frame)
            # 构造公用模型；源、网、荷、储与配电模块由配置装配。
            model = HeatPlanningModel(config, frame)
            # mip_gap和time_limit限制求解预算，不是物理可行性容差。
            artifacts = model.solve(
                mip_gap=args.mip_gap,
                time_limit=args.time_limit,
            )
            # 导出所得解及其实际输入；无可行解会由上层模型抛出异常。
            export_results(profile_output, artifacts, config, model.ts)
            summary = {key: value for key, value in artifacts.summary.items() if not isinstance(value, dict)}
            # 场景统计另存，避免以全系统平均掩盖某个场景的失供。
            (profile_output / "scenario_results.json").write_text(
                json.dumps(artifacts.summary.get("scenario_results", {}), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
