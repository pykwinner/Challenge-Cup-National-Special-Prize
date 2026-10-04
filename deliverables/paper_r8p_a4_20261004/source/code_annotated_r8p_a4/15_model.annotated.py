        # Local electricity connection adequacy for heat pumps and circulation
        # pumps. This prevents a heat-only optimum that cannot be connected to
        # the distribution grid without reinforcement.
        # 以下依赖build内既有变量，配置接入模块才装配。
        grid = self.config.get("grid_connection")
        if grid:
            # 既有和新增接入容量均为kW，新增量有投资上界。
            existing_grid = float(grid.get("existing_capacity_kw", 0.0))
            max_new_grid = float(grid.get("max_new_capacity_kw", 0.0))
            grid_idx = None
            # 不允许增容时仅使用既有容量，不建立新增变量。
            if max_new_grid > 0:
                grid_idx = p.add_var("grid_connection_capacity", ub=max_new_grid)
                self.variables["grid_connection_capacity"]["system"] = grid_idx
                # 增容造价按元每kW进入既有投资年化账本。
                self._record_investment_cost(
                    grid_idx, "grid_connection_capacity", "system", float(grid.get("capex_yuan_per_kw", 0.0))
                )
            # 裕度无量纲，以1加备用比例放大净电负荷。
            margin = 1.0 + float(grid.get("reserve_margin", 0.0))
            for t in self.times:
                grid_availability = 1.0
                # 若登记可用率时序，则逐时截取到0至1。
                if grid.get("availability_column"):
                    grid_availability = self._clip_scalar(
                        self._ts_value(t, grid["availability_column"]),
                        0.0,
                        1.0,
                    )
                # 读取统一电负荷行，热泵和泵电耗不另算一遍。
                row = self._electric_load_row(t, margin=margin)
                # 微电网分支仅在配置启用时修正接入净负荷。
                if microgrid:
                    for category, sign in (
                        # 储电充电增加负荷，本地发电与放电减少负荷。
                        ("microgrid_battery_charge", 1.0),
                        ("microgrid_renewable_use", -1.0),
                        ("microgrid_backup_generation", -1.0),
                        ("microgrid_battery_discharge", -1.0),
                    ):
                        self._add(row, self.variables[category][t], margin * sign)
                # 新增容量移到左端且乘可用率，保持线性上界。
                if grid_idx is not None:
                    self._add(row, grid_idx, -grid_availability)
                # 净负荷须小于可用率乘既有加新增接入容量。
                p.add_constraint(row, ub=existing_grid * grid_availability)

            # Ensure that electrically supplied firm heat capacity is actually
            # deliverable through the grid connection in a contingency.  The
            # legacy method assumes every installed electric heat source runs
            # simultaneously.  ``portfolio_dispatch`` instead selects a
            # feasible firm-heat portfolio for the peak (and each configured
            # source outage), avoiding double-counting mutually substitutable
            # heat pumps and backup boilers.
            # 以下准备静态可靠容量检查；同时率与裕度不同。
            simultaneity = float(grid.get("firm_simultaneity", 1.0))
            existing_firm_local = 0.0
            firm_local_row: dict[int, float] = {}
            if microgrid:
                # 本地可再生可靠容量按登记出力因子折算。
                renewable_firm = float(microgrid.get("renewable_firm_output_factor", 0.15))
                for asset, factor in (("renewable", renewable_firm), ("battery_power", 1.0), ("backup", 1.0)):
                    idx, existing, _ = microgrid_specs[asset]
                    existing_firm_local += factor * existing
                    if idx is not None:
                        firm_local_row[idx] = -factor

            # 选择可靠容量方法；本片段尚未装配该方法约束。
            firm_method = str(grid.get("firm_capacity_method", "all_installed_electric"))
            if firm_method not in {"all_installed_electric", "portfolio_dispatch"}:
                raise ValueError(
                    "grid_connection.firm_capacity_method 仅支持 "
                    "all_installed_electric 或 portfolio_dispatch"
                )
