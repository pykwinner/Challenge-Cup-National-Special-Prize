        # Local electricity connection adequacy for heat pumps and circulation
        # pumps. This prevents a heat-only optimum that cannot be connected to
        # the distribution grid without reinforcement.
        grid = self.config.get("grid_connection")
        if grid:
            existing_grid = float(grid.get("existing_capacity_kw", 0.0))
            max_new_grid = float(grid.get("max_new_capacity_kw", 0.0))
            grid_idx = None
            if max_new_grid > 0:
                grid_idx = p.add_var("grid_connection_capacity", ub=max_new_grid)
                self.variables["grid_connection_capacity"]["system"] = grid_idx
                self._record_investment_cost(
                    grid_idx, "grid_connection_capacity", "system", float(grid.get("capex_yuan_per_kw", 0.0))
                )
            margin = 1.0 + float(grid.get("reserve_margin", 0.0))
            for t in self.times:
                grid_availability = 1.0
                if grid.get("availability_column"):
                    grid_availability = self._clip_scalar(
                        self._ts_value(t, grid["availability_column"]),
                        0.0,
                        1.0,
                    )
                row = self._electric_load_row(t, margin=margin)
                if microgrid:
                    for category, sign in (
                        ("microgrid_battery_charge", 1.0),
                        ("microgrid_renewable_use", -1.0),
                        ("microgrid_backup_generation", -1.0),
                        ("microgrid_battery_discharge", -1.0),
                    ):
                        self._add(row, self.variables[category][t], margin * sign)
                if grid_idx is not None:
                    self._add(row, grid_idx, -grid_availability)
                p.add_constraint(row, ub=existing_grid * grid_availability)

            # Ensure that electrically supplied firm heat capacity is actually
            # deliverable through the grid connection in a contingency.  The
            # legacy method assumes every installed electric heat source runs
            # simultaneously.  ``portfolio_dispatch`` instead selects a
            # feasible firm-heat portfolio for the peak (and each configured
            # source outage), avoiding double-counting mutually substitutable
            # heat pumps and backup boilers.
            simultaneity = float(grid.get("firm_simultaneity", 1.0))
            existing_firm_local = 0.0
            firm_local_row: dict[int, float] = {}
            if microgrid:
                renewable_firm = float(microgrid.get("renewable_firm_output_factor", 0.15))
                for asset, factor in (("renewable", renewable_firm), ("battery_power", 1.0), ("backup", 1.0)):
                    idx, existing, _ = microgrid_specs[asset]
                    existing_firm_local += factor * existing
                    if idx is not None:
                        firm_local_row[idx] = -factor

            firm_method = str(grid.get("firm_capacity_method", "all_installed_electric"))
            if firm_method not in {"all_installed_electric", "portfolio_dispatch"}:
                raise ValueError(
                    "grid_connection.firm_capacity_method 仅支持 "
                    "all_installed_electric 或 portfolio_dispatch"
                )
