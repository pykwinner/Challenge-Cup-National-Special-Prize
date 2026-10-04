        # Storage investments and operation. Short-term storage can be cyclic;
        # seasonal storage can use fixed initial/final boundary conditions.
        for st_name, storage in self.storages.items():
            existing = float(storage.get("existing_capacity_kwh", 0.0))
            max_new = float(storage.get("max_new_capacity_kwh", 0.0))
            cap_idx = None
            if max_new > 0:
                cap_idx = p.add_var(f"storage_capacity[{st_name}]", ub=max_new)
                self.variables["storage_capacity"][st_name] = cap_idx
                self._record_investment_cost(
                    cap_idx, "storage_capacity", st_name, float(storage.get("capex_yuan_per_kwh", 0.0))
                )

            power_ratio = float(storage.get("power_to_energy_ratio_per_hour", 0.25))
            max_power = power_ratio * (existing + max_new)
            strict_storage_mode = bool(storage.get("strict_charge_discharge_mode", True))
            throughput_cost = float(
                storage.get("throughput_cost_yuan_per_kwh", 0.0)
            )
            for t in self.times:
                ch = p.add_var(f"storage_charge[{st_name},{t}]")
                dis = p.add_var(f"storage_discharge[{st_name},{t}]")
                soc = p.add_var(f"storage_soc[{st_name},{t}]")
                self.variables["storage_charge"][(st_name, t)] = ch
                self.variables["storage_discharge"][(st_name, t)] = dis
                self.variables["storage_soc"][(st_name, t)] = soc
                if throughput_cost > 0.0:
                    self._record_time_component(
                        "operating_cost", ch, throughput_cost, t
                    )
                    self._record_time_component(
                        "operating_cost", dis, throughput_cost, t
                    )

                soc_row = {soc: 1.0}
                ch_row = {ch: 1.0}
                dis_row = {dis: 1.0}
                if cap_idx is not None:
                    soc_row[cap_idx] = -1.0
                    ch_row[cap_idx] = -power_ratio
                    dis_row[cap_idx] = -power_ratio
                p.add_constraint(soc_row, ub=existing)
                p.add_constraint(ch_row, ub=power_ratio * existing)
                p.add_constraint(dis_row, ub=power_ratio * existing)
                if strict_storage_mode:
                    mode = p.add_var(f"storage_mode[{st_name},{t}]", ub=1.0, integer=True)
                    self.variables["storage_mode"][(st_name, t)] = mode
                    p.add_constraint({ch: 1.0, mode: -max_power}, ub=0.0)
                    p.add_constraint({dis: 1.0, mode: max_power}, ub=max_power)

            boundary_mode = str(storage.get("boundary_mode", "cyclic"))
            loss_factor = (1.0 - float(storage.get("self_loss_per_hour", 0.0))) ** self.dt
            eta_ch = float(storage.get("charge_efficiency", 0.95))
            eta_dis = float(storage.get("discharge_efficiency", 0.95))
            if boundary_mode not in {"cyclic", "fixed"}:
                raise ValueError(f"储热 {st_name} 的 boundary_mode 仅支持 cyclic 或 fixed")
            for t in self.times:
                row = {
                    self.variables["storage_soc"][(st_name, t)]: 1.0,
                    self.variables["storage_charge"][(st_name, t)]: -eta_ch * self.dt,
                    self.variables["storage_discharge"][(st_name, t)]: self.dt / eta_dis,
                }
                group_times = self.boundary_groups[self.time_boundary_group[t]]
                is_group_start = t == group_times[0]
                if boundary_mode == "cyclic" or not is_group_start:
                    prev = self.time_predecessor[t]
                    self._add(row, self.variables["storage_soc"][(st_name, prev)], -loss_factor)
                    p.add_constraint(row, lb=0.0, ub=0.0)
                else:
                    initial_fraction = float(storage.get("initial_soc_fraction", 0.5))
                    if cap_idx is not None:
                        row[cap_idx] = -loss_factor * initial_fraction
                    initial_energy = loss_factor * initial_fraction * existing
                    p.add_constraint(row, lb=initial_energy, ub=initial_energy)
            if boundary_mode == "fixed":
                final_fraction = float(storage.get("final_soc_min_fraction", 0.0))
                for group_times in self.boundary_groups.values():
                    final_row = {self.variables["storage_soc"][(st_name, group_times[-1])]: 1.0}
                    if cap_idx is not None:
                        final_row[cap_idx] = -final_fraction
                    p.add_constraint(final_row, lb=final_fraction * existing)
